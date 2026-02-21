"""FastAPI router for evaluation endpoints — /api/eval/*."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db
from job_monitor.eval.cache import download_and_cache_emails
from job_monitor.eval.models import (
    CachedEmail,
    EvalApplicationGroup,
    EvalLabel,
    EvalRun,
    EvalRunResult,
)
from job_monitor.eval.runner import run_evaluation
from job_monitor.eval.schemas import (
    BulkLabelUpdate,
    CacheDownloadRequest,
    CacheDownloadResult,
    CachedEmailDetailOut,
    CachedEmailListOut,
    CachedEmailOut,
    CacheStatsOut,
    DropdownOptions,
    EvalGroupIn,
    EvalGroupOut,
    EvalLabelIn,
    EvalLabelOut,
    EvalRunDetailOut,
    EvalRunErrorsOut,
    EvalRunOut,
    EvalRunRequest,
    EvalRunResultOut,
)
from job_monitor.models import Application

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


# ── Cache Management ──────────────────────────────────────


@router.post("/cache/download", response_model=CacheDownloadResult)
def cache_download(
    req: CacheDownloadRequest,
    session: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    """Fetch emails from IMAP and cache locally."""
    result = download_and_cache_emails(
        config, session,
        since_date=req.since_date,
        before_date=req.before_date,
        max_count=req.max_count,
    )
    return CacheDownloadResult(**result)


@router.get("/cache/stats", response_model=CacheStatsOut)
def cache_stats(session: Session = Depends(get_db)):
    """Get cache statistics."""
    total = session.query(func.count(CachedEmail.id)).scalar() or 0
    labeled = (
        session.query(func.count(EvalLabel.id))
        .filter(EvalLabel.review_status == "labeled")
        .scalar() or 0
    )
    skipped = (
        session.query(func.count(EvalLabel.id))
        .filter(EvalLabel.review_status == "skipped")
        .scalar() or 0
    )
    date_min = session.query(func.min(CachedEmail.email_date)).scalar()
    date_max = session.query(func.max(CachedEmail.email_date)).scalar()

    return CacheStatsOut(
        total_cached=total,
        total_labeled=labeled,
        total_unlabeled=total - labeled - skipped,
        total_skipped=skipped,
        date_range_start=date_min,
        date_range_end=date_max,
    )


@router.get("/cache/emails", response_model=CachedEmailListOut)
def cache_list_emails(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=10000),
    review_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    session: Session = Depends(get_db),
):
    """List cached emails with pagination and filters."""
    q = session.query(CachedEmail).outerjoin(EvalLabel)

    if review_status:
        if review_status == "unlabeled":
            q = q.filter(EvalLabel.id == None)  # noqa: E711
        else:
            q = q.filter(EvalLabel.review_status == review_status)

    if search:
        pattern = f"%{search}%"
        q = q.filter(
            CachedEmail.subject.ilike(pattern) | CachedEmail.sender.ilike(pattern)
        )

    total = q.count()
    items = (
        q.order_by(CachedEmail.email_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return CachedEmailListOut(
        items=[
            CachedEmailOut(
                id=ce.id,
                uid=ce.uid,
                email_account=ce.email_account,
                email_folder=ce.email_folder,
                gmail_message_id=ce.gmail_message_id,
                gmail_thread_id=ce.gmail_thread_id,
                subject=ce.subject,
                sender=ce.sender,
                email_date=ce.email_date,
                body_text=None,  # Omit body in list view
                fetched_at=ce.fetched_at,
                review_status=ce.label.review_status if ce.label else "unlabeled",
            )
            for ce in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/cache/emails/{email_id}", response_model=CachedEmailDetailOut)
def cache_get_email(email_id: int, session: Session = Depends(get_db)):
    """Get a single cached email with full body and latest pipeline predictions."""
    ce = session.query(CachedEmail).get(email_id)
    if not ce:
        raise HTTPException(404, "Cached email not found")

    # Get latest eval run result for this email
    latest_result = (
        session.query(EvalRunResult)
        .filter(EvalRunResult.cached_email_id == email_id)
        .order_by(EvalRunResult.eval_run_id.desc())
        .first()
    )

    # Get EvalPredictedGroup display info if predicted group exists
    app_display = None
    pred_group_id = None
    if latest_result:
        if latest_result.predicted_application_group_id:
            pred_group_id = latest_result.predicted_application_group_id
            pg = latest_result.predicted_group
            if pg:
                app_display = f"{pg.company or '?'} — {pg.job_title or 'Unknown'}"
        elif latest_result.predicted_company:
            # Fallback: show predicted company+title when no predicted group exists
            app_display = f"{latest_result.predicted_company} — {latest_result.predicted_job_title or 'Unknown'}"

    return CachedEmailDetailOut(
        id=ce.id,
        uid=ce.uid,
        email_account=ce.email_account,
        email_folder=ce.email_folder,
        gmail_message_id=ce.gmail_message_id,
        gmail_thread_id=ce.gmail_thread_id,
        subject=ce.subject,
        sender=ce.sender,
        email_date=ce.email_date,
        body_text=ce.body_text,
        fetched_at=ce.fetched_at,
        review_status=ce.label.review_status if ce.label else "unlabeled",
        predicted_is_job_related=latest_result.predicted_is_job_related if latest_result else None,
        predicted_company=latest_result.predicted_company if latest_result else None,
        predicted_job_title=latest_result.predicted_job_title if latest_result else None,
        predicted_status=latest_result.predicted_status if latest_result else None,
        predicted_application_group=pred_group_id,
        predicted_application_group_display=app_display,
        predicted_confidence=latest_result.predicted_confidence if latest_result else None,
    )


# ── Labels ────────────────────────────────────────────────


@router.get("/labels/{cached_email_id}", response_model=Optional[EvalLabelOut])
def get_label(cached_email_id: int, session: Session = Depends(get_db)):
    """Get the label for a specific cached email."""
    label = (
        session.query(EvalLabel)
        .filter(EvalLabel.cached_email_id == cached_email_id)
        .first()
    )
    if not label:
        return None
    return label


@router.put("/labels/{cached_email_id}", response_model=EvalLabelOut)
def upsert_label(
    cached_email_id: int,
    data: EvalLabelIn,
    session: Session = Depends(get_db),
):
    """Create or update a label for a cached email."""
    logger.info("upsert_label", cached_email_id=cached_email_id, data=data.model_dump())
    ce = session.query(CachedEmail).get(cached_email_id)
    if not ce:
        raise HTTPException(404, "Cached email not found")

    # Validate group ID exists if provided (treat 0 as None)
    group_id = data.correct_application_group_id
    if group_id == 0:
        group_id = None
    if group_id is not None:
        group = session.query(EvalApplicationGroup).get(group_id)
        if not group:
            raise HTTPException(400, f"Application group {group_id} does not exist")

    # Build data dict with corrected group_id
    data_dict = data.model_dump(exclude_unset=True)
    if "correct_application_group_id" in data_dict and data_dict["correct_application_group_id"] == 0:
        data_dict["correct_application_group_id"] = None

    label = (
        session.query(EvalLabel)
        .filter(EvalLabel.cached_email_id == cached_email_id)
        .first()
    )
    if label:
        for key, val in data_dict.items():
            setattr(label, key, val)
        label.labeled_at = datetime.now(timezone.utc)
    else:
        label = EvalLabel(
            cached_email_id=cached_email_id,
            labeled_at=datetime.now(timezone.utc),
            **data_dict,
        )
        session.add(label)

    session.commit()
    session.refresh(label)
    return label


@router.post("/labels/bulk")
def bulk_update_labels(
    data: BulkLabelUpdate,
    session: Session = Depends(get_db),
):
    """Bulk update labels for multiple emails."""
    updated = 0
    for eid in data.cached_email_ids:
        label = (
            session.query(EvalLabel)
            .filter(EvalLabel.cached_email_id == eid)
            .first()
        )
        if not label:
            label = EvalLabel(cached_email_id=eid)
            session.add(label)
        if data.is_job_related is not None:
            label.is_job_related = data.is_job_related
        if data.review_status is not None:
            label.review_status = data.review_status
        label.labeled_at = datetime.now(timezone.utc)
        updated += 1

    session.commit()
    return {"updated": updated}


# ── Application Groups (from main Applications table) ─────


@router.get("/applications", response_model=list[dict])
def list_applications_for_eval(session: Session = Depends(get_db)):
    """List all applications with their linked emails for group selection."""
    from job_monitor.models import ProcessedEmail
    
    apps = session.query(Application).order_by(Application.created_at.desc()).all()
    result = []
    for app in apps:
        # Get linked emails
        emails = session.query(ProcessedEmail).filter(ProcessedEmail.application_id == app.id).all()
        email_previews = []
        for e in emails[:5]:  # Max 5 previews
            email_previews.append({
                "subject": e.subject[:80] if e.subject else "",
                "sender": e.sender[:50] if e.sender else "",
                "date": e.email_date.strftime("%m/%d") if e.email_date else "?",
            })
        
        date_str = app.email_date.strftime("%Y-%m-%d") if app.email_date else "?"
        result.append({
            "id": app.id,
            "company": app.company,
            "job_title": app.job_title or "Unknown",
            "date": date_str,
            "status": app.status,
            "email_count": len(emails),
            "email_previews": email_previews,
            "display": f"{app.company} — {app.job_title or 'Unknown'} ({date_str})",
        })
    return result


# ── Legacy Application Groups (kept for compatibility) ────


@router.get("/groups", response_model=list[EvalGroupOut])
def list_groups(session: Session = Depends(get_db)):
    """List all application groups with email counts."""
    groups = session.query(EvalApplicationGroup).order_by(EvalApplicationGroup.created_at.desc()).all()
    result = []
    for g in groups:
        count = (
            session.query(func.count(EvalLabel.id))
            .filter(EvalLabel.correct_application_group_id == g.id)
            .scalar() or 0
        )
        result.append(EvalGroupOut(
            id=g.id,
            name=g.name,
            company=g.company,
            job_title=g.job_title,
            notes=g.notes,
            created_at=g.created_at,
            email_count=count,
        ))
    return result


@router.post("/groups", response_model=EvalGroupOut)
def create_group(data: EvalGroupIn, session: Session = Depends(get_db)):
    """Create a new application group."""
    name = data.name or f"{data.company or 'Unknown'} — {data.job_title or 'Unknown'}"
    group = EvalApplicationGroup(
        name=name,
        company=data.company,
        job_title=data.job_title,
        notes=data.notes,
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return EvalGroupOut(
        id=group.id, name=group.name, company=group.company,
        job_title=group.job_title, notes=group.notes,
        created_at=group.created_at, email_count=0,
    )


@router.put("/groups/{group_id}", response_model=EvalGroupOut)
def update_group(group_id: int, data: EvalGroupIn, session: Session = Depends(get_db)):
    """Update an application group."""
    group = session.query(EvalApplicationGroup).get(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    for key, val in data.model_dump(exclude_unset=True).items():
        if val is not None:
            setattr(group, key, val)
    session.commit()
    session.refresh(group)
    count = (
        session.query(func.count(EvalLabel.id))
        .filter(EvalLabel.correct_application_group_id == group.id)
        .scalar() or 0
    )
    return EvalGroupOut(
        id=group.id, name=group.name, company=group.company,
        job_title=group.job_title, notes=group.notes,
        created_at=group.created_at, email_count=count,
    )


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, session: Session = Depends(get_db)):
    """Delete a group and unlink its labels."""
    group = session.query(EvalApplicationGroup).get(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    # Unlink labels
    session.query(EvalLabel).filter(
        EvalLabel.correct_application_group_id == group_id
    ).update({"correct_application_group_id": None})
    session.delete(group)
    session.commit()
    return {"deleted": True}


# ── Dropdown Data ─────────────────────────────────────────


@router.get("/dropdown/options", response_model=DropdownOptions)
def dropdown_options(session: Session = Depends(get_db)):
    """Get dropdown options for the review UI."""
    # Companies from applications + labels
    app_companies = session.query(distinct(Application.company)).all()
    label_companies = session.query(distinct(EvalLabel.correct_company)).filter(
        EvalLabel.correct_company.isnot(None)
    ).all()
    companies = sorted(set(
        c[0] for c in app_companies if c[0]
    ) | set(
        c[0] for c in label_companies if c[0]
    ))

    # Job titles from applications + labels
    app_titles = session.query(distinct(Application.job_title)).filter(
        Application.job_title.isnot(None)
    ).all()
    label_titles = session.query(distinct(EvalLabel.correct_job_title)).filter(
        EvalLabel.correct_job_title.isnot(None)
    ).all()
    job_titles = sorted(set(
        t[0] for t in app_titles if t[0]
    ) | set(
        t[0] for t in label_titles if t[0]
    ))

    statuses = ["已申请", "面试", "拒绝", "Offer", "Unknown"]

    return DropdownOptions(companies=companies, job_titles=job_titles, statuses=statuses)


# ── Evaluation Runs ───────────────────────────────────────


@router.post("/runs", response_model=EvalRunOut)
def trigger_eval_run(
    req: EvalRunRequest,
    session: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    """Trigger a new evaluation run."""
    eval_run = run_evaluation(config, session, run_name=req.name)
    return eval_run


@router.get("/runs", response_model=list[EvalRunOut])
def list_runs(session: Session = Depends(get_db)):
    """List all evaluation runs."""
    runs = session.query(EvalRun).order_by(EvalRun.started_at.desc()).all()
    return runs


@router.get("/runs/{run_id}", response_model=EvalRunDetailOut)
def get_run(run_id: int, session: Session = Depends(get_db)):
    """Get a single evaluation run with full report."""
    run = session.query(EvalRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}/results", response_model=list[EvalRunResultOut])
def get_run_results(
    run_id: int,
    errors_only: bool = Query(False),
    session: Session = Depends(get_db),
):
    """Get per-email results for an evaluation run."""
    q = (
        session.query(EvalRunResult)
        .filter(EvalRunResult.eval_run_id == run_id)
    )
    if errors_only:
        q = q.filter(
            (EvalRunResult.classification_correct == False) |  # noqa: E712
            (EvalRunResult.company_correct == False) |
            (EvalRunResult.job_title_correct == False) |
            (EvalRunResult.status_correct == False)
        )

    results = q.all()
    out = []
    for r in results:
        ce = session.query(CachedEmail).get(r.cached_email_id)
        pg = r.predicted_group  # Lazy-loaded relationship
        out.append(EvalRunResultOut(
            id=r.id,
            cached_email_id=r.cached_email_id,
            predicted_is_job_related=r.predicted_is_job_related,
            predicted_company=r.predicted_company,
            predicted_job_title=r.predicted_job_title,
            predicted_status=r.predicted_status,
            predicted_application_group_id=r.predicted_application_group_id,
            predicted_group=pg,
            predicted_confidence=r.predicted_confidence,
            classification_correct=r.classification_correct,
            company_correct=r.company_correct,
            company_partial=r.company_partial,
            job_title_correct=r.job_title_correct,
            status_correct=r.status_correct,
            grouping_correct=r.grouping_correct,
            llm_used=r.llm_used,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            estimated_cost_usd=r.estimated_cost_usd,
            email_subject=ce.subject if ce else None,
            email_sender=ce.sender if ce else None,
        ))
    return out


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, session: Session = Depends(get_db)):
    """Delete an evaluation run and its results."""
    run = session.query(EvalRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    session.delete(run)
    session.commit()
    return {"deleted": True}
