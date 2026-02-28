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
from sqlalchemy import distinct, func, or_
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig, get_config, set_llm_enabled
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
    EmailPredictionRunOut,
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

# ── Runtime settings ──────────────────────────────────────


@router.get("/settings")
def get_settings(config: AppConfig = Depends(get_config)):
    """Return current runtime eval settings."""
    return {
        "llm_enabled": config.llm_enabled,
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "llm_confidence_threshold": config.llm_confidence_threshold,
    }


@router.post("/settings")
def update_settings(
    llm_enabled: Optional[bool] = None,
    config: AppConfig = Depends(get_config),
):
    """Toggle LLM enabled at runtime (no server restart required)."""
    if llm_enabled is not None:
        set_llm_enabled(llm_enabled)
    return {
        "llm_enabled": llm_enabled if llm_enabled is not None else config.llm_enabled,
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "llm_confidence_threshold": config.llm_confidence_threshold,
    }


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
    run_id: Optional[int] = Query(None, description="Filter to only emails evaluated in this run"),
    session: Session = Depends(get_db),
):
    """List cached emails with pagination and filters."""
    # Join to the run-scoped label when run_id is given so review_status is correct
    if run_id:
        q = (
            session.query(CachedEmail)
            .outerjoin(
                EvalLabel,
                (EvalLabel.cached_email_id == CachedEmail.id) &
                (EvalLabel.eval_run_id == run_id),
            )
        )
        # Restrict to emails that have an EvalRunResult for this run
        subq = (
            session.query(EvalRunResult.cached_email_id)
            .filter(EvalRunResult.eval_run_id == run_id)
            .subquery()
        )
        q = q.filter(CachedEmail.id.in_(subq))
    else:
        # No run_id: join each email to its MOST RECENT label (highest id).
        # Using eval_run_id=NULL (legacy filter) missed all run-scoped labels.
        latest_label_id_subq = (
            session.query(
                EvalLabel.cached_email_id,
                func.max(EvalLabel.id).label("max_id"),
            )
            .group_by(EvalLabel.cached_email_id)
            .subquery()
        )
        q = (
            session.query(CachedEmail)
            .outerjoin(
                latest_label_id_subq,
                CachedEmail.id == latest_label_id_subq.c.cached_email_id,
            )
            .outerjoin(
                EvalLabel,
                EvalLabel.id == latest_label_id_subq.c.max_id,
            )
        )

    if review_status:
        if review_status == "unlabeled":
            # "unlabeled" can be represented either by missing label row (legacy)
            # or an explicit run-scoped label row with review_status="unlabeled".
            q = q.filter(
                or_(
                    EvalLabel.id.is_(None),
                    EvalLabel.review_status.is_(None),
                    EvalLabel.review_status == "unlabeled",
                )
            )
        else:
            q = q.filter(EvalLabel.review_status == review_status)

    if search:
        pattern = f"%{search}%"
        q = q.filter(
            CachedEmail.subject.ilike(pattern) | CachedEmail.sender.ilike(pattern)
        )

    total = q.count()
    # add_entity(EvalLabel) returns (CachedEmail, EvalLabel | None) rows so we
    # can use the *join-scoped* label directly without triggering an unfiltered
    # lazy-load via ce.label (which would ignore the eval_run_id constraint).
    rows = (
        q.add_entity(EvalLabel)
        .order_by(CachedEmail.email_date.desc())
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
                review_status=lbl.review_status if lbl else "unlabeled",
            )
            for ce, lbl in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/cache/emails/{email_id}", response_model=CachedEmailDetailOut)
def cache_get_email(
    email_id: int,
    run_id: Optional[int] = Query(None, description="When provided, return predictions from this eval run"),
    session: Session = Depends(get_db),
):
    """Get a single cached email with full body and pipeline predictions.

    If ``run_id`` is provided, predictions are read from that run. Otherwise,
    the latest run result for the email is returned.
    """
    ce = session.query(CachedEmail).get(email_id)
    if not ce:
        raise HTTPException(404, "Cached email not found")

    # Get eval run result for this email (run-scoped when run_id is provided)
    result_q = session.query(EvalRunResult).filter(EvalRunResult.cached_email_id == email_id)
    if run_id is not None:
        result_q = result_q.filter(EvalRunResult.eval_run_id == run_id)
    latest_result = result_q.order_by(EvalRunResult.eval_run_id.desc()).first()

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
        predicted_email_category=latest_result.predicted_email_category if latest_result else None,
        predicted_company=latest_result.predicted_company if latest_result else None,
        predicted_job_title=latest_result.predicted_job_title if latest_result else None,
        predicted_req_id=latest_result.predicted_req_id if latest_result else None,
        predicted_status=latest_result.predicted_status if latest_result else None,
        predicted_application_group=pred_group_id,
        predicted_application_group_display=app_display,
        predicted_confidence=latest_result.predicted_confidence if latest_result else None,
        decision_log_json=latest_result.decision_log_json if latest_result else None,
    )


@router.get("/cache/emails/{email_id}/prediction-runs", response_model=list[EmailPredictionRunOut])
def cache_get_email_prediction_runs(
    email_id: int,
    session: Session = Depends(get_db),
):
    """List historical eval runs that contain predictions for this cached email."""
    ce = session.query(CachedEmail).get(email_id)
    if not ce:
        raise HTTPException(404, "Cached email not found")

    rows = (
        session.query(EvalRun)
        .join(EvalRunResult, EvalRunResult.eval_run_id == EvalRun.id)
        .filter(EvalRunResult.cached_email_id == email_id)
        .order_by(EvalRun.started_at.desc())
        .all()
    )

    return [
        EmailPredictionRunOut(
            run_id=r.id,
            run_name=r.run_name,
            started_at=r.started_at,
            completed_at=r.completed_at,
        )
        for r in rows
    ]


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
        log("llm", f"  req_id         = {latest.predicted_req_id!r}", "info")
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
def get_label(
    cached_email_id: int,
    run_id: Optional[int] = Query(None),
    session: Session = Depends(get_db),
):
    """Get the label for a cached email, scoped to a specific eval run when run_id is provided."""
    q = session.query(EvalLabel).filter(EvalLabel.cached_email_id == cached_email_id)
    if run_id is not None:
        q = q.filter(EvalLabel.eval_run_id == run_id)
    else:
        # No run_id: return the most recent run's label
        q = q.order_by(EvalLabel.eval_run_id.desc().nullslast())
    label = q.first()
    if not label:
        return None
    return label


@router.put("/labels/{cached_email_id}", response_model=EvalLabelOut)
def upsert_label(
    cached_email_id: int,
    data: EvalLabelIn,
    session: Session = Depends(get_db),
):
    """Create or update a label for a cached email.

    Automatically appends a correction entry to ``corrections_json`` for every
    field whose submitted value differs from the latest pipeline prediction.
    """
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
    # Strip non-DB fields (run_id, corrections) before passing to SQLAlchemy
    _NON_DB = {"run_id", "corrections"}
    data_dict = {k: v for k, v in data.model_dump(exclude_unset=True).items() if k not in _NON_DB}
    if "correct_application_group_id" in data_dict and data_dict["correct_application_group_id"] == 0:
        data_dict["correct_application_group_id"] = None

    # ── Correction audit log ──────────────────────────────
    # Load the latest pipeline prediction to diff against
    latest_pred = (
        session.query(EvalRunResult)
        .filter(EvalRunResult.cached_email_id == cached_email_id)
        .order_by(EvalRunResult.eval_run_id.desc())
        .first()
    )

    now_str = datetime.now(timezone.utc).isoformat()
    new_corrections: list[dict] = []

    # Run ID to tag corrections with (frontend passes ?run_id or we fall back to latest run)
    save_run_id: Optional[int] = data.run_id or (latest_pred.eval_run_id if latest_pred else None)

    # Load existing label early — scoped to (cached_email_id, eval_run_id)
    _run_id_for_label = save_run_id  # use the run_id from the request
    _prev_label = (
        session.query(EvalLabel)
        .filter(
            EvalLabel.cached_email_id == cached_email_id,
            EvalLabel.eval_run_id == _run_id_for_label,
        )
        .first()
    )

    if data.corrections:
        # ── Human provided structured corrections (rich annotations) ──
        for c in data.corrections:
            new_corrections.append({
                "run_id": save_run_id,
                "field": c.field,
                "predicted": c.predicted,
                "corrected": c.corrected,
                "error_type": c.error_type,
                "evidence": c.evidence,
                "reason": c.reason,
                "at": now_str,
            })
    else:
        # ── Auto-detect corrections — always diff current GT against the ORIGINAL PREDICTION.
        # corrections_json is replaced on every save so it reflects the current state of
        # disagreement, not a cumulative history.
        def _diff(field: str, pred_val, gt_val) -> None:
            p = str(pred_val).strip().lower() if pred_val is not None else ""
            g = str(gt_val).strip().lower() if gt_val is not None else ""
            if p != g:
                new_corrections.append({
                    "run_id": save_run_id,
                    "field": field,
                    "predicted": str(pred_val) if pred_val is not None else None,
                    "corrected": str(gt_val) if gt_val is not None else None,
                    "error_type": None, "evidence": None, "reason": None,
                    "at": now_str,
                })

        if latest_pred:
            # Classification
            if "is_job_related" in data_dict:
                _diff("classification", latest_pred.predicted_is_job_related, data_dict["is_job_related"])

            gt_is_job = data_dict.get("is_job_related")
            if gt_is_job is False:
                # GT says not job-related — any non-null field prediction is now wrong
                for fld, pred_val in [
                    ("company",   latest_pred.predicted_company),
                    ("job_title", latest_pred.predicted_job_title),
                    ("req_id",    latest_pred.predicted_req_id),
                    ("status",    latest_pred.predicted_status),
                ]:
                    if pred_val:  # only record if prediction actually had a value
                        _diff(fld, pred_val, None)
            else:
                # Job-related: diff each labeled field against prediction
                if "correct_company" in data_dict:
                    _diff("company", latest_pred.predicted_company, data_dict["correct_company"])
                if "correct_job_title" in data_dict:
                    _diff("job_title", latest_pred.predicted_job_title, data_dict["correct_job_title"])
                if "correct_req_id" in data_dict:
                    _diff("req_id", latest_pred.predicted_req_id, data_dict["correct_req_id"])
                if "correct_status" in data_dict:
                    _diff("status", latest_pred.predicted_status, data_dict["correct_status"])

    # ── Grouping decision learning analysis v2 ───────────
    grouping_analysis: Optional[dict] = None
    correct_group_id = data_dict.get("correct_application_group_id")
    is_not_job = data_dict.get("is_job_related") is False

    if correct_group_id is not None or is_not_job:
        from job_monitor.eval.models import EvalPredictedGroup
        from difflib import SequenceMatcher as _SM
        from job_monitor.linking.resolver import normalize_company as _norm_co, titles_similar as _titles_sim

        # ── Predicted group info ──────────────────────────
        pred_group_id = latest_pred.predicted_application_group_id if latest_pred else None
        pred_group = None
        if pred_group_id:
            pred_group = session.query(EvalPredictedGroup).get(pred_group_id)

        pred_company = (latest_pred.predicted_company or "") if latest_pred else ""
        pred_title   = (latest_pred.predicted_job_title or "") if latest_pred else ""
        pred_company_norm = pred_group.company_norm if pred_group else (_norm_co(pred_company) or pred_company.strip().lower())
        pred_title_norm   = pred_group.job_title_norm if pred_group else pred_title.strip().lower()

        # ── Correct dedup key ─────────────────────────────
        # Use normalize_company() — same function as the production pipeline — so
        # "Qventus, Inc" → "qventus" instead of the naive "qventus, inc", preventing
        # false company-key mismatches in the grouping analysis report.
        correct_company = data_dict.get("correct_company") or ""
        correct_title   = data_dict.get("correct_job_title") or ""
        correct_company_norm = _norm_co(correct_company) or correct_company.strip().lower()
        correct_title_norm   = correct_title.strip().lower()

        # ── Key match analysis ────────────────────────────
        company_matches = pred_company_norm == correct_company_norm
        # Use titles_similar() for title comparison so abbreviations like
        # "Sr. Data Engineer" vs "Senior Data Engineer" are correctly treated as matching.
        title_matches   = _titles_sim(pred_title_norm, correct_title_norm) if (pred_title_norm and correct_title_norm) else (pred_title_norm == correct_title_norm)
        if not company_matches and not title_matches:
            dedup_failure: Optional[str] = "both"
        elif not company_matches:
            dedup_failure = "company"
        elif not title_matches:
            dedup_failure = "title"
        else:
            dedup_failure = None

        # ── Group sizes ───────────────────────────────────
        pred_group_size = 0
        if pred_group_id and latest_pred:
            pred_group_size = (
                session.query(func.count(EvalRunResult.id))
                .filter(EvalRunResult.predicted_application_group_id == pred_group_id)
                .scalar() or 0
            )

        correct_group_size = 0
        if correct_group_id:
            correct_group_size = (
                session.query(func.count(EvalLabel.id))
                .filter(EvalLabel.correct_application_group_id == correct_group_id)
                .scalar() or 0
            )

        # ── Co-member analysis ────────────────────────────
        co_member_ids: list[int] = []
        co_member_subjects: list[Optional[str]] = []
        co_member_email_dates: list[Optional[str]] = []
        co_member_predicted_group_ids: list[Optional[int]] = []
        co_member_predicted_group_names: list[Optional[str]] = []
        if correct_group_id:
            co_labels = (
                session.query(EvalLabel)
                .filter(
                    EvalLabel.correct_application_group_id == correct_group_id,
                    EvalLabel.cached_email_id != cached_email_id,
                )
                .all()
            )
            co_member_ids = [lbl.cached_email_id for lbl in co_labels]

            # Fetch subjects and dates for display
            for eid in co_member_ids:
                ce_co = session.query(CachedEmail).get(eid)
                co_member_subjects.append(ce_co.subject if ce_co else None)
                co_member_email_dates.append(
                    ce_co.email_date.isoformat() if ce_co and ce_co.email_date else None
                )

            if co_member_ids and latest_pred:
                co_results = (
                    session.query(EvalRunResult)
                    .filter(
                        EvalRunResult.eval_run_id == latest_pred.eval_run_id,
                        EvalRunResult.cached_email_id.in_(co_member_ids),
                    )
                    .all()
                )
                # Build email_id → result map to preserve ordering
                co_result_map = {r.cached_email_id: r for r in co_results}
                for eid in co_member_ids:
                    r = co_result_map.get(eid)
                    if r and r.predicted_application_group_id:
                        co_member_predicted_group_ids.append(r.predicted_application_group_id)
                        pg_co = session.query(EvalPredictedGroup).get(r.predicted_application_group_id)
                        if pg_co:
                            co_member_predicted_group_names.append(
                                f"#{pg_co.id} {pg_co.company or '?'} — {pg_co.job_title or 'Unknown'}"
                            )
                        else:
                            co_member_predicted_group_names.append(f"#{r.predicted_application_group_id}")
                    else:
                        co_member_predicted_group_ids.append(None)
                        co_member_predicted_group_names.append(None)

        # ── Group ID match ────────────────────────────────
        if dedup_failure is not None:
            group_id_match = False
        elif co_member_ids:
            # All co-members must be in the same predicted group
            group_id_match = all(
                pgid == pred_group_id
                for pgid in co_member_predicted_group_ids
                if pgid is not None
            )
        else:
            group_id_match = True  # no co-members yet; key match is sufficient

        # ── Group decision type ───────────────────────────
        if is_not_job:
            group_decision_type: Optional[str] = "MARKED_NOT_JOB"
        elif correct_group_id is None:
            group_decision_type = None
        elif pred_group_id is None:
            group_decision_type = "NEW_GROUP_CREATED"
        elif dedup_failure is not None:
            group_decision_type = "SPLIT_FROM_EXISTING"
        elif not co_member_ids:
            group_decision_type = "NEW_GROUP_CREATED"
        elif group_id_match:
            group_decision_type = "CONFIRMED"
        else:
            # Some co-members landed in different predicted groups → over-split
            group_decision_type = "MERGED_INTO_EXISTING"

        # ── Grouping failure category ─────────────────────
        if group_decision_type in ("CONFIRMED", "MARKED_NOT_JOB", "NEW_GROUP_CREATED", None):
            grouping_failure_category: Optional[str] = None
        elif group_decision_type == "MERGED_INTO_EXISTING":
            grouping_failure_category = "OVER_SPLIT"
        elif group_decision_type == "SPLIT_FROM_EXISTING":
            # What kind of key error?
            is_extraction_err = pred_company.strip().lower() in ("unknown", "", "none", "—")
            if is_extraction_err:
                grouping_failure_category = "EXTRACTION_ERROR"
            else:
                # Measure similarity of the failing dimension
                fail_dim = dedup_failure or "company"
                p_norm = pred_company_norm if fail_dim != "title" else pred_title_norm
                c_norm = correct_company_norm if fail_dim != "title" else correct_title_norm
                sim = _SM(None, p_norm, c_norm).ratio()
                grouping_failure_category = "NORMALIZATION_WEAKNESS" if sim >= 0.75 else "KEY_MISMATCH"
        else:
            grouping_failure_category = None

        grouping_analysis = {
            # Section 1: Dedup key
            "predicted_company":      pred_company or None,
            "predicted_title":        pred_title or None,
            "predicted_company_norm": pred_company_norm,
            "predicted_title_norm":   pred_title_norm,
            "predicted_dedup_key":    [pred_company_norm, pred_title_norm],
            "correct_company":        correct_company or None,
            "correct_title":          correct_title or None,
            "correct_company_norm":   correct_company_norm,
            "correct_title_norm":     correct_title_norm,
            "correct_dedup_key":      [correct_company_norm, correct_title_norm],
            "dedup_key_failure":      dedup_failure,
            "company_key_matches":    company_matches,
            "title_key_matches":      title_matches,
            # Section 2: Group-ID level
            "predicted_group_id":     pred_group_id,
            "correct_group_id":       correct_group_id,
            "group_id_match":         group_id_match,
            "predicted_group_size":   pred_group_size,
            "correct_group_size":     correct_group_size,
            # Section 3: Co-membership
            "co_member_email_ids":                  co_member_ids,
            "co_member_subjects":                   co_member_subjects,
            "co_member_email_dates":                co_member_email_dates,
            "co_member_count":                      len(co_member_ids),
            "co_member_predicted_group_ids":        co_member_predicted_group_ids,
            "co_member_predicted_group_names":      co_member_predicted_group_names,
            # Section 4: Decision
            "group_decision_type":        group_decision_type,
            "grouping_failure_category":  grouping_failure_category,
            # Metadata
            "at": now_str,
        }

    # ── Always record group assignment changes (old → new, regardless of prediction) ──
    # Uses field="group_assignment" to distinguish from prediction-comparison entries.
    # Reuse _prev_label loaded above — no second query needed.
    if "correct_application_group_id" in data_dict:
        old_grp_id = _prev_label.correct_application_group_id if _prev_label else None
        new_grp_id = data_dict.get("correct_application_group_id")
        if old_grp_id != new_grp_id:
            from_name: Optional[str] = None
            to_name: Optional[str] = None
            if old_grp_id:
                fgrp = session.query(EvalApplicationGroup).get(old_grp_id)
                from_name = fgrp.name if fgrp else f"Group #{old_grp_id}"
            if new_grp_id:
                tgrp = session.query(EvalApplicationGroup).get(new_grp_id)
                to_name = tgrp.name if tgrp else f"Group #{new_grp_id}"
            new_corrections.append({
                "run_id": save_run_id,
                "field": "group_assignment",
                "from_group_id": old_grp_id,
                "from_group_name": from_name,
                "to_group_id": new_grp_id,
                "to_group_name": to_name,
                "at": now_str,
            })

    label = _prev_label
    if label:
        for key, val in data_dict.items():
            setattr(label, key, val)
        label.labeled_at = datetime.now(timezone.utc)

        # ── Sync EvalApplicationGroup canonical name with human corrections ──
        # When the reviewer corrects "Zoom Communications" → "Zoom":
        #   1. Update the EvalApplicationGroup record (future auto-fill)
        #   2. Cascade to all other EvalLabel records in the same group
        #      so opening any co-member email shows the corrected name.
        _grp_id_to_sync = data_dict.get("correct_application_group_id") or label.correct_application_group_id
        if _grp_id_to_sync:
            _grp = session.query(EvalApplicationGroup).get(_grp_id_to_sync)
            if _grp:
                _grp_changed = False
                if "correct_company" in data_dict and data_dict["correct_company"]:
                    _grp.company = data_dict["correct_company"]
                    _grp_changed = True
                if "correct_job_title" in data_dict and data_dict["correct_job_title"]:
                    _grp.job_title = data_dict["correct_job_title"]
                    _grp_changed = True
                if _grp_changed:
                    _grp.name = f"{_grp.company or '?'} — {_grp.job_title or 'Unknown'}"
                    # Cascade: update correct_company/title on every label in the group
                    # EXCEPT the one we just saved. Filter by label.id (not cached_email_id)
                    # so that same-email labels from other runs are also updated.
                    _co_labels = (
                        session.query(EvalLabel)
                        .filter(
                            EvalLabel.correct_application_group_id == _grp_id_to_sync,
                            EvalLabel.id != label.id,
                        )
                        .all()
                    )
                    for _co in _co_labels:
                        if "correct_company" in data_dict and data_dict["correct_company"]:
                            _co.correct_company = data_dict["correct_company"]
                        if "correct_job_title" in data_dict and data_dict["correct_job_title"]:
                            _co.correct_job_title = data_dict["correct_job_title"]

        # Replace corrections_json with the current save's diff (not cumulative history).
        # This ensures the log always reflects the gap between the CURRENT ground truth and
        # the original prediction, discarding stale entries from earlier edits.
        label.corrections_json = json.dumps(new_corrections, ensure_ascii=False) if new_corrections else None
        # Always overwrite grouping analysis with latest (re-computed on each save).
        # Clear it when not job-related (stale analysis from a previous job-related save).
        if grouping_analysis is not None:
            label.grouping_analysis_json = json.dumps(grouping_analysis, ensure_ascii=False)
        elif data_dict.get("is_job_related") is False:
            label.grouping_analysis_json = None
    else:
        label = EvalLabel(
            cached_email_id=cached_email_id,
            eval_run_id=_run_id_for_label,  # run-scoped
            labeled_at=datetime.now(timezone.utc),
            corrections_json=json.dumps(new_corrections, ensure_ascii=False) if new_corrections else None,
            grouping_analysis_json=json.dumps(grouping_analysis, ensure_ascii=False) if grouping_analysis else None,
            **data_dict,
        )
        session.add(label)

        # Sync group for new labels too
        _new_grp_id = data_dict.get("correct_application_group_id")
        if _new_grp_id:
            _new_grp = session.query(EvalApplicationGroup).get(_new_grp_id)
            if _new_grp:
                _nc = False
                if data_dict.get("correct_company"):
                    _new_grp.company = data_dict["correct_company"]; _nc = True
                if data_dict.get("correct_job_title"):
                    _new_grp.job_title = data_dict["correct_job_title"]; _nc = True
                if _nc:
                    _new_grp.name = f"{_new_grp.company or '?'} — {_new_grp.job_title or 'Unknown'}"

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
def list_groups(
    run_id: Optional[int] = Query(None, description="Filter to groups created/matched in this eval run"),
    session: Session = Depends(get_db),
):
    """List application groups, optionally filtered to a specific eval run.

    When ``run_id`` is provided, only groups whose (company_norm, title_norm) appear
    in ``EvalPredictedGroup`` for that run are returned.
    """
    from job_monitor.eval.models import EvalPredictedGroup as _EPN

    if run_id:
        # Return only groups scoped to this specific run
        groups = (
            session.query(EvalApplicationGroup)
            .filter(EvalApplicationGroup.eval_run_id == run_id)
            .order_by(EvalApplicationGroup.created_at.desc())
            .all()
        )
    else:
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
            eval_run_id=g.eval_run_id,
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
        eval_run_id=data.eval_run_id,
        name=name,
        company=data.company,
        job_title=data.job_title,
        notes=data.notes,
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return EvalGroupOut(
        id=group.id, eval_run_id=group.eval_run_id, name=group.name, company=group.company,
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


@router.get("/groups/{group_id}/members")
def get_group_members(group_id: int, session: Session = Depends(get_db)):
    """Return all emails assigned to this application group (via EvalLabel.correct_application_group_id).

    Returns a list of ``{"cached_email_id", "subject", "sender", "email_date", "review_status"}``.
    """
    labels = (
        session.query(EvalLabel)
        .filter(EvalLabel.correct_application_group_id == group_id)
        .all()
    )
    result = []
    for label in labels:
        ce = session.query(CachedEmail).get(label.cached_email_id)
        if ce:
            result.append({
                "cached_email_id": ce.id,
                "subject": ce.subject or "(no subject)",
                "sender": ce.sender or "(unknown sender)",
                "email_date": ce.email_date.isoformat() if ce.email_date else None,
                "review_status": label.review_status,
            })
    return result


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


# ── Bootstrap groups from predictions ────────────────────


@router.post("/bootstrap-groups")
def bootstrap_groups_from_predictions(session: Session = Depends(get_db)):
    """Create EvalApplicationGroup records from the latest eval run's predicted groups,
    and link the emails predicted to be in each group to their corresponding
    EvalApplicationGroup via EvalLabel.correct_application_group_id.

    Rules:
    - If a matching EvalApplicationGroup already exists → use it (matched)
    - Otherwise → create a new EvalApplicationGroup (created)
    - For each EvalRunResult in the predicted group → create/update the EvalLabel
      setting correct_application_group_id ONLY if it is not already manually set.

    Returns ``{"created": int, "matched": int, "labels_linked": int, "run_id": int | None}``
    """
    from job_monitor.eval.models import EvalPredictedGroup

    latest_run = (
        session.query(EvalRun)
        .order_by(EvalRun.started_at.desc())
        .first()
    )
    if not latest_run:
        return {"created": 0, "matched": 0, "labels_linked": 0, "run_id": None}

    predicted_groups = (
        session.query(EvalPredictedGroup)
        .filter(EvalPredictedGroup.eval_run_id == latest_run.id)
        .all()
    )

    # Pre-load all existing EvalApplicationGroup records once
    all_existing: list[EvalApplicationGroup] = session.query(EvalApplicationGroup).all()

    created = 0
    matched = 0
    labels_linked = 0
    now = datetime.now(timezone.utc)

    for pg in predicted_groups:
        if not pg.company:
            continue
        company_norm = (pg.company or "").strip().lower()
        title_norm = (pg.job_title or "").strip().lower()

        # Find or create the EvalApplicationGroup
        app_group = next(
            (
                g for g in all_existing
                if (g.company or "").strip().lower() == company_norm
                and (g.job_title or "").strip().lower() == title_norm
            ),
            None,
        )

        if app_group:
            matched += 1
        else:
            name = f"{pg.company} — {pg.job_title or 'Unknown'}"
            app_group = EvalApplicationGroup(
                name=name,
                company=pg.company,
                job_title=pg.job_title,
                notes=f"Auto-created from eval run #{latest_run.id} predictions",
            )
            session.add(app_group)
            session.flush()  # get the new ID
            all_existing.append(app_group)
            created += 1

        # Link all emails predicted to be in this group → EvalLabel
        run_results = (
            session.query(EvalRunResult)
            .filter(EvalRunResult.predicted_application_group_id == pg.id)
            .all()
        )
        for result in run_results:
            label = (
                session.query(EvalLabel)
                .filter(EvalLabel.cached_email_id == result.cached_email_id)
                .first()
            )
            if label is None:
                # First bootstrap — create a minimal label from new run's predictions
                label = EvalLabel(
                    cached_email_id=result.cached_email_id,
                    labeled_at=now,
                    correct_application_group_id=app_group.id,
                    correct_company=result.predicted_company,
                    correct_job_title=result.predicted_job_title,
                    correct_req_id=result.predicted_req_id,
                    correct_status=result.predicted_status,
                    is_job_related=result.predicted_is_job_related,
                    review_status="unlabeled",
                )
                session.add(label)
                labels_linked += 1
            else:
                # Re-bootstrap for a new run: reset all prediction-derived fields to new
                # run's predictions. This clears the "defaults" for the new run so the
                # reviewer sees the new predictions in the form. The corrections_json
                # history is preserved — it retains all previous runs' corrections.
                label.correct_company = result.predicted_company
                label.correct_job_title = result.predicted_job_title
                label.correct_req_id = result.predicted_req_id
                label.correct_status = result.predicted_status
                label.is_job_related = result.predicted_is_job_related
                label.correct_application_group_id = app_group.id
                label.review_status = "unlabeled"  # reset to unlabeled for new review
                labels_linked += 1

    session.commit()
    return {"created": created, "matched": matched, "labels_linked": labels_linked, "run_id": latest_run.id}


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

    statuses = ["Recruiter Reach-out", "已申请", "OA", "面试", "Offer", "Onboarding", "拒绝", "Unknown"]

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
    max_emails: Optional[int] = Query(None, ge=1, description="Limit evaluation to the first N emails"),
    email_ids: Optional[str] = Query(None, description="Comma-separated list of CachedEmail IDs to evaluate"),
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

    # Parse comma-separated email_ids into a list of ints (if provided)
    parsed_email_ids: Optional[list[int]] = None
    if email_ids:
        try:
            parsed_email_ids = [int(x.strip()) for x in email_ids.split(",") if x.strip()]
        except ValueError:
            parsed_email_ids = None

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
                max_emails=max_emails,
                email_ids=parsed_email_ids,
            )
            db.commit()
            _was_cancelled = _eval_cancel_event.is_set()
            if _was_cancelled:
                _progress_cb(f"Cancelled — saving partial results for run #{result.id}…", 0, 0)
            # Auto-bootstrap: reset EvalLabel fields to new run's predictions
            # so Review Queue shows "unlabeled" with fresh defaults.
            # Corrections history (corrections_json) is preserved.
            # Runs even on cancel so partial results are usable.
            _progress_cb(f"Auto-bootstrapping labels from run #{result.id}…", 0, 0)
            try:
                    from job_monitor.eval.models import EvalPredictedGroup as _EPG
                    _all_groups: list[EvalApplicationGroup] = db.query(EvalApplicationGroup).all()
                    _now = datetime.now(timezone.utc)

                    import re as _re_boot
                    from difflib import SequenceMatcher as _SM_bootstrap

                    # Common legal / descriptive suffixes to strip before comparing
                    _COMPANY_STRIP = _re_boot.compile(
                        r"\b(inc\.?|llc\.?|ltd\.?|corp\.?|co\.?|company|"
                        r"communications?|group|technologies?|solutions?|"
                        r"systems?|services?|international|global|your)\b",
                        _re_boot.IGNORECASE,
                    )

                    def _normalize_co(name: str) -> str:
                        stripped = _COMPANY_STRIP.sub("", name)
                        return _re_boot.sub(r"\s+", " ", stripped).strip()

                    def _company_fuzzy(a: str, b: str, threshold: float = 0.80) -> bool:
                        """True when two company norms represent the same company.

                        Strategy:
                        1. Exact match on raw norms (fast path)
                        2. Exact match after stripping common legal suffixes
                           → "zoom communications" → "zoom", "zoom" → "zoom" = SAME
                        3. SequenceMatcher on stripped names >= threshold
                        """
                        if a == b:
                            return True
                        na, nb = _normalize_co(a), _normalize_co(b)
                        if na == nb and na:
                            return True
                        if not na or not nb:
                            return False
                        return _SM_bootstrap(None, na, nb).ratio() >= threshold

                    for _pg in db.query(_EPG).filter(_EPG.eval_run_id == result.id).all():
                        if not _pg.company:
                            continue
                        _cn = (_pg.company or "").strip().lower()
                        _tn = (_pg.job_title or "").strip().lower()
                        # Runs are isolated; use fuzzy company matching so "Zoom" and
                        # "Zoom Communications" dedup to the same group instead of
                        # creating separate near-duplicate groups.
                        _app_group = next(
                            (g for g in _all_groups
                             if g.eval_run_id == result.id
                             and (g.job_title or "").strip().lower() == _tn
                             and _company_fuzzy((g.company or "").strip().lower(), _cn)),
                            None,
                        )
                        if _app_group is None:
                            _app_group = EvalApplicationGroup(
                                eval_run_id=result.id,  # run-scoped
                                name=f"{_pg.company} — {_pg.job_title or 'Unknown'}",
                                company=_pg.company, job_title=_pg.job_title,
                                notes=f"Auto-created from eval run #{result.id}",
                            )
                            db.add(_app_group)
                            db.flush()
                            _all_groups.append(_app_group)

                        for _r in db.query(EvalRunResult).filter(
                            EvalRunResult.predicted_application_group_id == _pg.id
                        ).all():
                            # Run-scoped: look up by (email, run_id)
                            _lbl = db.query(EvalLabel).filter(
                                EvalLabel.cached_email_id == _r.cached_email_id,
                                EvalLabel.eval_run_id == result.id,
                            ).first()
                            if _lbl is None:
                                _lbl = EvalLabel(
                                    cached_email_id=_r.cached_email_id,
                                    eval_run_id=result.id,  # run-scoped
                                    labeled_at=_now,
                                    correct_application_group_id=_app_group.id,
                                    correct_company=_r.predicted_company,
                                    correct_job_title=_r.predicted_job_title,
                                    correct_req_id=_r.predicted_req_id,
                                    correct_status=_r.predicted_status,
                                    is_job_related=_r.predicted_is_job_related,
                                    review_status="unlabeled",
                                )
                                db.add(_lbl)
                            else:
                                # Reset to new predictions for this run
                                _lbl.correct_company = _r.predicted_company
                                _lbl.correct_job_title = _r.predicted_job_title
                                _lbl.correct_req_id = _r.predicted_req_id
                                _lbl.correct_status = _r.predicted_status
                                _lbl.is_job_related = _r.predicted_is_job_related
                                _lbl.correct_application_group_id = _app_group.id
                                _lbl.review_status = "unlabeled"

                    # Second pass: create/reset run-scoped labels for non-job-related emails
                    for _r in db.query(EvalRunResult).filter(
                        EvalRunResult.eval_run_id == result.id,
                        EvalRunResult.predicted_is_job_related == False,  # noqa: E712
                    ).all():
                        _lbl = db.query(EvalLabel).filter(
                            EvalLabel.cached_email_id == _r.cached_email_id,
                            EvalLabel.eval_run_id == result.id,
                        ).first()
                        if _lbl is None:
                            _lbl = EvalLabel(
                                cached_email_id=_r.cached_email_id,
                                eval_run_id=result.id,  # run-scoped
                                labeled_at=_now,
                                is_job_related=False,
                                review_status="unlabeled",
                            )
                            db.add(_lbl)
                        else:
                            _lbl.is_job_related = False
                            _lbl.correct_company = None
                            _lbl.correct_job_title = None
                            _lbl.correct_req_id = None
                            _lbl.correct_status = None
                            _lbl.correct_application_group_id = None
                            _lbl.review_status = "unlabeled"

                    db.commit()
                    _progress_cb(f"✓ Labels reset to Run #{result.id} predictions.", 0, 0)

                    # Refresh report_json and per-result flags now that labels reflect
                    # actual predictions — otherwise field_error_examples stays stale.
                    try:
                        from job_monitor.eval.runner import refresh_eval_run_report
                        refresh_eval_run_report(db, result.id)
                        db.commit()
                        _progress_cb("✓ Report refreshed with bootstrap labels.", 0, 0)
                    except Exception as _rbe:
                        logger.warning("report_refresh_failed", error=str(_rbe))
            except Exception as _be:
                logger.warning("auto_bootstrap_failed", error=str(_be))

            if _was_cancelled:
                msg_q.put({"type": "cancelled", "run_id": result.id})
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
    """List batch evaluation runs (single-email ad-hoc runs are hidden)."""
    runs = (
        session.query(EvalRun)
        .filter(~func.coalesce(EvalRun.run_name, "").like("review-email-%"))
        .order_by(EvalRun.started_at.desc())
        .all()
    )
    return runs


@router.get("/runs/{run_id}", response_model=EvalRunDetailOut)
def get_run(run_id: int, session: Session = Depends(get_db)):
    """Get a single evaluation run with full report."""
    run = session.query(EvalRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.post("/runs/{run_id}/refresh-report", response_model=EvalRunDetailOut)
def refresh_run_report(run_id: int, session: Session = Depends(get_db)):
    """Recompute report_json and per-result correctness flags from current labels."""
    run = session.query(EvalRun).get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    from job_monitor.eval.runner import refresh_eval_run_report
    refresh_eval_run_report(session, run_id)
    session.commit()
    return run


@router.get("/runs/{run_id}/results", response_model=list[EvalRunResultOut])
def get_run_results(
    run_id: int,
    errors_only: bool = Query(False),
    session: Session = Depends(get_db),
):
    """Get per-email results for an evaluation run.

    Correctness flags are recomputed against run-scoped labels so results stay
    consistent even after labels are edited post-run.
    """
    from difflib import SequenceMatcher
    from job_monitor.extraction.rules import normalize_req_id as _norm_req
    from job_monitor.linking.resolver import normalize_company as _norm_co, titles_similar as _titles_sim

    results = (
        session.query(EvalRunResult)
        .filter(EvalRunResult.eval_run_id == run_id)
        .all()
    )
    out = []
    for r in results:
        ce = session.query(CachedEmail).get(r.cached_email_id)
        pg = r.predicted_group  # Lazy-loaded relationship
        lbl = (
            session.query(EvalLabel)
            .filter(
                EvalLabel.cached_email_id == r.cached_email_id,
                EvalLabel.eval_run_id == run_id,
            )
            .first()
        )

        # Recompute correctness against current run-scoped labels.
        cls_correct = r.classification_correct
        company_correct = r.company_correct
        company_partial = r.company_partial
        title_correct = r.job_title_correct
        req_id_correct = r.req_id_correct
        status_correct = r.status_correct

        if lbl and lbl.is_job_related is not None:
            cls_correct = (r.predicted_is_job_related == lbl.is_job_related)
        if lbl and lbl.correct_company is not None and r.predicted_is_job_related:
            pn = _norm_co(r.predicted_company or "") or (r.predicted_company or "").strip().lower()
            ln = _norm_co(lbl.correct_company) or lbl.correct_company.strip().lower()
            company_correct = (pn == ln)
            company_partial = SequenceMatcher(None, pn, ln).ratio() >= 0.8
        if lbl and lbl.correct_job_title is not None and r.predicted_is_job_related:
            pt = (r.predicted_job_title or "").strip()
            lt = lbl.correct_job_title.strip()
            title_correct = (
                pt.lower() == lt.lower() or
                (bool(pt) and bool(lt) and _titles_sim(pt, lt))
            )
        if lbl and lbl.correct_status is not None and r.predicted_is_job_related:
            status_correct = (
                (r.predicted_status or "").strip().lower() ==
                lbl.correct_status.strip().lower()
            )
        if lbl and lbl.correct_req_id is not None and r.predicted_is_job_related:
            req_id_correct = _norm_req(r.predicted_req_id or "") == _norm_req(lbl.correct_req_id)

        if errors_only and not (
            cls_correct is False or
            company_correct is False or
            title_correct is False or
            req_id_correct is False or
            status_correct is False or
            r.grouping_correct is False
        ):
            continue

        out.append(EvalRunResultOut(
            id=r.id,
            cached_email_id=r.cached_email_id,
            predicted_is_job_related=r.predicted_is_job_related,
            predicted_email_category=r.predicted_email_category,
            predicted_company=r.predicted_company,
            predicted_job_title=r.predicted_job_title,
            predicted_req_id=r.predicted_req_id,
            predicted_status=r.predicted_status,
            predicted_application_group_id=r.predicted_application_group_id,
            predicted_group=pg,
            predicted_confidence=r.predicted_confidence,
            classification_correct=cls_correct,
            company_correct=company_correct,
            company_partial=company_partial,
            job_title_correct=title_correct,
            req_id_correct=req_id_correct,
            status_correct=status_correct,
            grouping_correct=r.grouping_correct,
            llm_used=r.llm_used,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            estimated_cost_usd=r.estimated_cost_usd,
            email_subject=ce.subject if ce else None,
            email_sender=ce.sender if ce else None,
            # Human ground-truth labels
            label_is_job_related=lbl.is_job_related if lbl else None,
            label_company=lbl.correct_company if lbl else None,
            label_job_title=lbl.correct_job_title if lbl else None,
            label_req_id=lbl.correct_req_id if lbl else None,
            label_status=lbl.correct_status if lbl else None,
            label_review_status=lbl.review_status if lbl else "unlabeled",
            # Eval run decision log
            decision_log_json=r.decision_log_json,
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
