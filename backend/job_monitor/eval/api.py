"""FastAPI router for evaluation endpoints — /api/eval/*."""

from __future__ import annotations

import asyncio
import json
import queue as _queue
import threading
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db, get_session_factory
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

# ── Eval run state (module-level, single-process) ────────
_eval_cancel_event: threading.Event = threading.Event()
_eval_lock: threading.Lock = threading.Lock()
_eval_running: bool = False


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


# ── Pipeline Replay (decision trace) ─────────────────────


@router.get("/cache/emails/{email_id}/replay")
def replay_email_pipeline(
    email_id: int,
    session: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    """Replay the rule-based pipeline on a single cached email with detailed
    step-by-step decision logging.  LLM is intentionally skipped to avoid cost;
    stored LLM predictions are surfaced from the latest EvalRunResult.

    Returns ``{"logs": [{"stage": str, "message": str, "level": str}, ...]}``
    """
    import re as _re

    from job_monitor.email.classifier import JOB_SIGNAL_KEYWORDS, NEGATIVE_KEYWORDS
    from job_monitor.eval.cache import reparse_cached_email
    from job_monitor.extraction.rules import (
        COMPANY_PATTERNS,  # type: ignore[attr-defined]
        JOB_TITLE_PATTERNS,  # type: ignore[attr-defined]
        _GENERIC_COMPANY_NAMES,  # type: ignore[attr-defined]
        _JUNK_SUBJECT_MARKERS,  # type: ignore[attr-defined]
        _ROLE_KEYWORDS,  # type: ignore[attr-defined]
        _STATUS_MAP,  # type: ignore[attr-defined]
        _clean_text,  # type: ignore[attr-defined]
        _clean_title,  # type: ignore[attr-defined]
        _normalize_space,  # type: ignore[attr-defined]
    )
    from job_monitor.email.parser import is_noise_text

    ce = session.query(CachedEmail).get(email_id)
    if not ce:
        raise HTTPException(404, "Cached email not found")

    logs: list[dict] = []

    def log(stage: str, msg: str, level: str = "info") -> None:
        logs.append({"stage": stage, "message": msg, "level": level})

    # ── Parse ────────────────────────────────────────────
    parsed = reparse_cached_email(ce)
    if parsed:
        subject = parsed.subject or ""
        sender = parsed.sender or ""
        body = parsed.body_text or ""
        log("input", "Parsed from raw RFC-822 message.")
    else:
        subject = ce.subject or ""
        sender = ce.sender or ""
        body = ce.body_text or ""
        log("input", "Using cached text fields (no raw RFC-822 stored).", "warn")

    log("input", f"Subject : {subject[:120]!r}")
    log("input", f"Sender  : {sender[:120]!r}")
    log("input", f"Body    : {body[:300]!r}{'…' if len(body) > 300 else ''}")

    # ── Stored LLM result ────────────────────────────────
    latest = (
        session.query(EvalRunResult)
        .filter(EvalRunResult.cached_email_id == email_id)
        .order_by(EvalRunResult.eval_run_id.desc())
        .first()
    )
    if latest and latest.llm_used:
        log("llm", f"Latest eval run used LLM:", "info")
        log("llm", f"  is_job_related = {latest.predicted_is_job_related}", "info")
        log("llm", f"  company        = {latest.predicted_company!r}", "info")
        log("llm", f"  job_title      = {latest.predicted_job_title!r}", "info")
        log("llm", f"  status         = {latest.predicted_status!r}", "info")
        log("llm", f"  confidence     = {latest.predicted_confidence}", "info")
    else:
        log("llm", "Latest run used rule-based pipeline (LLM disabled or not yet run).", "info")

    # ══════════════════════════════════════════════════════
    # Stage 1: Classification
    # ══════════════════════════════════════════════════════
    log("classification", "══ Stage 1: Classification (keyword scan on subject) ══")
    searchable_subj = subject.lower()

    log("classification", f"Scanning subject (lowercased): {searchable_subj!r}")

    neg_hits = [kw for kw in NEGATIVE_KEYWORDS if kw in searchable_subj]
    if neg_hits:
        log("classification",
            f"NEGATIVE keywords found → {neg_hits}  — these override all positive signals.",
            "error")
        log("classification", "→ Result: NOT job-related", "error")
        pred_is_job = False
    else:
        log("classification", "No negative keywords found in subject.", "info")
        pos_hits = [kw for kw in JOB_SIGNAL_KEYWORDS if kw in searchable_subj]
        miss_hits = [kw for kw in JOB_SIGNAL_KEYWORDS if kw not in searchable_subj]
        if pos_hits:
            log("classification",
                f"Positive keywords matched → {pos_hits}", "success")
            log("classification",
                f"Keywords not matched      → {miss_hits[:10]}{'…' if len(miss_hits) > 10 else ''}", "info")
            log("classification", "→ Result: JOB-RELATED ✓", "success")
            pred_is_job = True
        else:
            log("classification",
                f"No positive keywords matched in subject.  (Checked {len(JOB_SIGNAL_KEYWORDS)} keywords.)",
                "warn")
            log("classification", "→ Result: NOT job-related", "warn")
            pred_is_job = False

    if not pred_is_job:
        log("classification", "Field extraction skipped (email not job-related).")
        return {"logs": logs}

    # ══════════════════════════════════════════════════════
    # Stage 2: Company extraction
    # ══════════════════════════════════════════════════════
    log("company", "══ Stage 2: Company Extraction ══")

    # 2a — Check junk markers
    lowered_subj = subject.lower()
    junk_hit = next((m for m in _JUNK_SUBJECT_MARKERS if m in lowered_subj), None)
    pred_company = ""

    if junk_hit:
        log("company",
            f"Subject contains junk marker {junk_hit!r} → skipping subject regex entirely.",
            "warn")
    else:
        log("company", "No junk markers found in subject — trying regex patterns.")
        # 2b — Try each COMPANY_PATTERNS regex
        for i, pattern in enumerate(COMPANY_PATTERNS, 1):
            m = pattern.search(subject)
            if m:
                raw = m.group(1)
                company = _clean_text(raw)
                # post-process: strip trailing role words
                company = _re.sub(
                    r"\b(team|careers?|jobs?|hiring|recruiting)\b$", "", company,
                    flags=_re.IGNORECASE,
                ).strip()
                company = _re.sub(
                    r"\b(application|applied|position|role)\b.*$", "", company,
                    flags=_re.IGNORECASE,
                ).strip()
                log("company",
                    f"  Pattern #{i} ({pattern.pattern[:60]!r}) → raw match={raw!r}, cleaned={company!r}",
                    "info")
                if company.lower() in _GENERIC_COMPANY_NAMES:
                    log("company",
                        f"  Rejected: {company!r} is a generic name ({_GENERIC_COMPANY_NAMES}).",
                        "warn")
                    continue
                if company:
                    log("company", f"  ✓ Accepted: {company!r}", "success")
                    pred_company = company
                    break
                else:
                    log("company", "  Cleaned result is empty — trying next pattern.", "warn")
            else:
                log("company", f"  Pattern #{i} → no match.", "info")

    if not pred_company:
        log("company", "Subject regex exhausted with no valid match — falling back to sender domain.", "warn")
        # 2c — Sender domain fallback with step-by-step trace
        log("company", f"  Sender string: {sender!r}")
        domain_m = _re.search(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", sender)
        if domain_m:
            raw_domain = domain_m.group(1).lower()
            log("company", f"  Extracted domain: {raw_domain!r}")
            stripped = _re.sub(
                r"^(mail|email|notifications|notify|jobs?|careers?)\.", "", raw_domain
            )
            if stripped != raw_domain:
                log("company", f"  Stripped common prefix → {stripped!r}")
            if stripped.endswith(".co.uk"):
                pieces = stripped.split(".")
                name = pieces[-3].replace("-", " ").title() if len(pieces) >= 3 else stripped
            else:
                pieces = stripped.split(".")
                name = pieces[-2].replace("-", " ").title() if len(pieces) >= 2 else stripped
            log("company",
                f"  Domain segments: {pieces}  → using segment[-2] = {pieces[-2] if len(pieces) >= 2 else '?'!r}",
                "info")
            log("company", f"  Titlecased result: {name!r}", "warn")
            pred_company = name
        else:
            log("company", "  No @domain found in sender — company unknown.", "error")
            pred_company = "Unknown"

    log("company", f"→ Final company: {pred_company!r}", "success" if pred_company and pred_company != "Unknown" else "warn")

    # ══════════════════════════════════════════════════════
    # Stage 3: Job Title extraction
    # ══════════════════════════════════════════════════════
    log("title", "══ Stage 3: Job Title Extraction ══")
    combined = f"{subject}\n{body}"
    pred_title = ""

    # Phase 1 — structured patterns on combined text
    log("title", "Phase 1: structured regex patterns on subject+body combined.")
    for i, pattern in enumerate(JOB_TITLE_PATTERNS, 1):
        m = pattern.search(combined)
        if m:
            raw = m.group(1)
            title = _clean_title(raw)
            log("title",
                f"  Pattern #{i} ({pattern.pattern[:60]!r})\n    raw={raw!r}, cleaned={title!r}",
                "info")
            if title and not is_noise_text(title):
                log("title", f"  ✓ Accepted: {title!r}", "success")
                pred_title = title
                break
            else:
                log("title",
                    f"  Rejected: {'empty after cleaning' if not title else 'noise text'} — trying next.",
                    "warn")
        else:
            log("title", f"  Pattern #{i} → no match.", "info")

    if not pred_title:
        # Phase 2 — subject-specific fallbacks
        log("title", "Phase 2: subject-specific fallback patterns.")
        subject_fallback_patterns = [
            (_re.compile(r"application for\s+([A-Za-z0-9 /&,+.#()\-]{2,90})", _re.IGNORECASE), "application for …"),
            (_re.compile(r"applied to\s+([A-Za-z0-9 /&,+.#()\-]{2,90})", _re.IGNORECASE), "applied to …"),
            (_re.compile(r"for\s+(?:the\s+)?([A-Za-z0-9 /&,+.#()\-]{2,90})\s+(?:position|role)", _re.IGNORECASE), "for … position/role"),
        ]
        for pat, desc in subject_fallback_patterns:
            m = pat.search(subject)
            if m:
                raw = m.group(1)
                title = _clean_title(raw)
                log("title", f"  Fallback '{desc}' → raw={raw!r}, cleaned={title!r}", "info")
                if title and not is_noise_text(title):
                    log("title", f"  ✓ Accepted: {title!r}", "success")
                    pred_title = title
                    break
                else:
                    log("title", f"  Rejected — noise or empty.", "warn")
            else:
                log("title", f"  Fallback '{desc}' → no match.", "info")

    if not pred_title:
        # Phase 3 — line-by-line body scan
        log("title", "Phase 3: line-by-line body scan for role-keyword lines.")
        body_lines = body.splitlines()
        log("title", f"  Scanning {len(body_lines)} body lines.")
        for line_no, line in enumerate(body_lines[:80], 1):
            line = _normalize_space(line)
            if len(line) < 4 or len(line) > 120 or is_noise_text(line):
                continue
            for pattern in JOB_TITLE_PATTERNS:
                m = pattern.search(line)
                if m:
                    title = _clean_title(m.group(1))
                    if title and not is_noise_text(title):
                        log("title",
                            f"  Line {line_no}: pattern matched → {title!r}", "success")
                        pred_title = title
                        break
            if pred_title:
                break
            if _re.match(r"^[A-Za-z][A-Za-z0-9 /&,+.#()\-]{3,80}$", line):
                if any(kw in line.lower() for kw in _ROLE_KEYWORDS):
                    pred_title = _clean_title(line)
                    log("title",
                        f"  Line {line_no}: role-keyword line → {pred_title!r}", "success")
                    break

    if not pred_title:
        # Phase 4 — subject structure patterns
        log("title", "Phase 4: subject structure 'Company - Role' or 'Role at Company'.")
        structure_patterns = [
            (_re.compile(r"^[^\-|:]{2,60}\s*-\s*([^\-|:]{2,90})$", _re.IGNORECASE), "Company - Role"),
            (_re.compile(r"^([^\-|:]{2,90})\s+at\s+[^\-|:]{2,60}$", _re.IGNORECASE), "Role at Company"),
        ]
        for pat, desc in structure_patterns:
            m = pat.search(subject)
            if m:
                title = _clean_title(m.group(1))
                log("title", f"  Structure '{desc}' → raw={m.group(1)!r}, cleaned={title!r}", "info")
                if title and not is_noise_text(title):
                    log("title", f"  ✓ Accepted: {title!r}", "success")
                    pred_title = title
                    break
            else:
                log("title", f"  Structure '{desc}' → no match.", "info")

    if not pred_title:
        log("title", "All phases exhausted — no title extracted.", "warn")
    log("title", f"→ Final title: {pred_title!r}", "success" if pred_title else "warn")

    # ══════════════════════════════════════════════════════
    # Stage 4: Status extraction
    # ══════════════════════════════════════════════════════
    log("status", "══ Stage 4: Status Extraction ══")
    searchable_full = f"{subject}\n{body}".lower()
    log("status", f"Search text (first 200 chars): {searchable_full[:200]!r}…")
    log("status", "Checking status categories in priority order (first match wins):")

    status_hit: Optional[str] = None
    for kws, label_val in _STATUS_MAP:
        hit_kws = [kw for kw in kws if kw in searchable_full]
        miss_kws = [kw for kw in kws if kw not in searchable_full]
        if hit_kws:
            log("status",
                f"  [{label_val}]  ✓ matched: {hit_kws}  (not matched: {miss_kws})",
                "success")
            status_hit = label_val
            break
        else:
            log("status",
                f"  [{label_val}]  ✗ none of {kws} found — skipping.",
                "info")

    if not status_hit:
        log("status", "No category matched — defaulting to '已申请' (applied).", "warn")
        status_hit = "已申请"
    log("status", f"→ Final status: {status_hit!r}", "success")

    # ══════════════════════════════════════════════════════
    # Stage 5: Application Grouping
    # ══════════════════════════════════════════════════════
    log("grouping", "══ Stage 5: Application Grouping (dedup) ══")
    log("grouping", f"  Input company : {pred_company!r}")
    log("grouping", f"  Input title   : {pred_title!r}")
    company_norm = pred_company.strip().lower()
    title_norm = (pred_title or "").strip().lower()
    log("grouping", f"  Normalised    : company={company_norm!r}  title={title_norm!r}")
    log("grouping", f"  Dedup key     : ({company_norm!r}, {title_norm!r})")
    log("grouping",
        "During a full eval run, all emails sharing this dedup key are assigned the same "
        "EvalPredictedGroup ID.  (Single-email replay cannot show the final group ID.)", "info")

    return {"logs": logs}


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
    """Trigger a new evaluation run (blocking, no streaming)."""
    eval_run = run_evaluation(
        config, session,
        run_name=req.name,
        cancel_token=_eval_cancel_event,
    )
    return eval_run


@router.get("/runs/stream")
async def stream_eval_run(
    name: Optional[str] = Query(None),
    config: AppConfig = Depends(get_config),
):
    """Trigger a new evaluation run and stream SSE progress events.

    SSE message shapes
    ------------------
    ``{"type": "log",      "message": "...", "current": int, "total": int}``
    ``{"type": "progress", "current": int, "total": int}``
    ``{"type": "done",     "run_id": int}``
    ``{"type": "error",    "message": "..."}``
    ``{"type": "cancelled"}``
    """
    global _eval_running

    with _eval_lock:
        if _eval_running:
            # Return a single-event SSE that immediately reports busy
            async def _busy():
                yield f"data: {json.dumps({'type': 'error', 'message': 'An evaluation is already running.'})}\n\n"
            return StreamingResponse(_busy(), media_type="text/event-stream")
        _eval_running = True
        _eval_cancel_event.clear()

    msg_q: _queue.Queue = _queue.Queue()

    def _progress_cb(message: str, current: int, total: int) -> None:
        msg_q.put({"type": "log", "message": message, "current": current, "total": total})

    def _run_thread() -> None:
        global _eval_running
        session_factory = get_session_factory()
        db = session_factory()
        try:
            result = run_evaluation(
                config, db,
                run_name=name,
                progress_cb=_progress_cb,
                cancel_token=_eval_cancel_event,
            )
            db.commit()
            if _eval_cancel_event.is_set():
                msg_q.put({"type": "cancelled"})
            else:
                msg_q.put({"type": "done", "run_id": result.id})
        except Exception as exc:
            logger.exception("eval_stream_error", error=str(exc))
            try:
                db.rollback()
            except Exception:
                pass
            msg_q.put({"type": "error", "message": str(exc)})
        finally:
            try:
                db.close()
            except Exception:
                pass
            with _eval_lock:
                _eval_running = False

    thread = threading.Thread(target=_run_thread, daemon=True)
    thread.start()

    async def _generate():
        while True:
            # Drain all queued messages before yielding keep-alive
            drained = False
            while True:
                try:
                    msg = msg_q.get_nowait()
                    drained = True
                    yield f"data: {json.dumps(msg)}\n\n"
                    if msg.get("type") in ("done", "error", "cancelled"):
                        return
                except _queue.Empty:
                    break

            if not thread.is_alive() and not drained:
                # Thread finished but nothing left in queue
                return

            # Keep-alive comment so the connection stays open
            yield ": keepalive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/runs/cancel")
def cancel_eval_run():
    """Request cancellation of the currently running evaluation."""
    _eval_cancel_event.set()
    return {"cancelled": True, "running": _eval_running}


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
            (EvalRunResult.status_correct == False) |
            (EvalRunResult.grouping_correct == False)  # noqa: E712
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
