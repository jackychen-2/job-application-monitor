"""Extraction pipeline — orchestrates rule-based and LLM-based extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, TypedDict

import structlog


class ProgressInfo(TypedDict):
    """Progress information passed to the progress callback."""
    processed: int
    total: int
    current_subject: str
    status: str  # "processing", "completed", "cancelled", "error"


# Type alias for progress callback
ProgressCallback = Callable[[ProgressInfo], None]

from sqlalchemy.orm import Session

from job_monitor.config import AppConfig
from job_monitor.email.client import IMAPClient
from job_monitor.email.parser import ParsedEmailData, parse_email_message
from job_monitor.extraction.core import run_core_classification_and_extraction
from job_monitor.extraction.llm import (
    LLMExtractionResult,
    LLMProvider,
    create_llm_provider,
)
from job_monitor.linking.resolver import (
    is_message_already_processed,
    normalize_company,
    resolve_by_company,
    LinkResult,
)

# Garbage titles that should be replaced with empty string
_INVALID_TITLES = {
    "the", "a", "an", "to", "for", "at", "in", "on", "of", "and", "or",
    "your", "our", "this", "that", "it", "is", "are", "was", "were",
    "application", "job", "position", "role", "unknown", "n/a", "none",
}


def _validate_job_title(title: str) -> str:
    """Return the title if valid, or empty string for garbage values."""
    cleaned = title.strip()
    if not cleaned:
        return ""
    if len(cleaned) < 3:
        return ""
    # Max length: allow up to 200 chars to accommodate titles with team qualifiers and job IDs
    if len(cleaned) > 200:
        return ""
    if cleaned.lower() in _INVALID_TITLES:
        return ""
    # Reject values that start with lowercase (likely a sentence fragment, not a title)
    if cleaned[0].islower():
        return ""
    return cleaned


from job_monitor.models import Application, ProcessedEmail, ScanState, StatusHistory

logger = structlog.get_logger(__name__)


@dataclass
class ScanSummary:
    """Result summary after a scan run."""

    emails_scanned: int = 0
    emails_matched: int = 0
    applications_created: int = 0
    applications_updated: int = 0
    applications_deleted: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_estimated_cost: float = 0.0
    errors: list[str] = None  # type: ignore[assignment]
    cancelled: bool = False

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def build_title_req_filters(model_cls: type, job_title: str | None, req_id: str | None) -> list:
    """Build the 4-case (job_title, req_id) dedup filter conditions for a SQLAlchemy query.

    This is the **single source of truth** for the application dedup key logic.
    Both ``_get_or_create_application`` (production) and the eval runner's
    ``EvalPredictedGroup`` fallback dedup call this function so that any future
    change to the dedup rules is automatically reflected in both paths.

    Both ``Application`` and ``EvalPredictedGroup`` expose ``.job_title`` and
    ``.req_id`` columns, so the same filter builder works for either model.

    Args:
        model_cls: SQLAlchemy model class (``Application`` or ``EvalPredictedGroup``).
        job_title: Raw job title string (``None`` / ``""`` treated as absent).
        req_id: Normalised req-ID string (``None`` / ``""`` treated as absent).

    Returns:
        List of SQLAlchemy column-expression filters to pass to ``.filter(*filters)``.
    """
    jt = job_title or None   # normalise "" → None
    rq = req_id or None      # normalise "" → None
    if jt and rq:
        return [model_cls.job_title == jt, model_cls.req_id == rq]
    elif jt:
        return [
            model_cls.job_title == jt,
            (model_cls.req_id == None) | (model_cls.req_id == ""),  # noqa: E711
        ]
    elif rq:
        return [
            (model_cls.job_title == None) | (model_cls.job_title == ""),  # noqa: E711
            model_cls.req_id == rq,
        ]
    else:
        return [
            (model_cls.job_title == None) | (model_cls.job_title == ""),  # noqa: E711
            (model_cls.req_id == None) | (model_cls.req_id == ""),  # noqa: E711
        ]


def _get_or_create_application(
    session: Session,
    company: str,
    job_title: str,
    req_id: str,
    email_subject: str,
    email_sender: str,
    email_date: Optional[datetime],
    status: str,
    source: str = "email",
) -> tuple[Application, bool]:
    """Find an existing application or create a new one.

    Returns (application, created) where created=True for new rows.
    Deduplicates by normalized_company + job_title + req_id via
    :func:`build_title_req_filters` (shared with the eval runner).
    Updates existing record if data has changed.
    """
    # Use normalized_company for matching to handle variations like "Qventus, Inc" vs "Qventus"
    normalized = normalize_company(company)

    def _base_q():
        return session.query(Application).filter(Application.normalized_company == normalized)

    # Delegate the 4-case filter logic to the shared helper so prod and eval stay in sync.
    existing = _base_q().filter(*build_title_req_filters(Application, job_title, req_id)).first()
    if existing:
        # Update fields - merge old into most recent
        if existing.company != company:
            existing.company = company
            existing.normalized_company = normalized
        if job_title and existing.job_title != job_title:
            existing.job_title = job_title
        if req_id and existing.req_id != req_id:
            existing.req_id = req_id
        # Always update to most recent email info
        _ed = email_date.replace(tzinfo=None) if email_date and hasattr(email_date, 'tzinfo') and email_date.tzinfo else email_date
        _ad = existing.email_date.replace(tzinfo=None) if existing.email_date and hasattr(existing.email_date, 'tzinfo') and existing.email_date.tzinfo else existing.email_date
        if _ed and (_ad is None or _ed > _ad):
            existing.email_date = email_date
            existing.email_subject = email_subject
            existing.email_sender = email_sender
        existing.updated_at = datetime.utcnow()
        logger.info(
            "application_merged",
            app_id=existing.id,
            company=company,
            job_title=job_title,
            req_id=req_id,
        )
        return existing, False

    app = Application(
        company=company,
        normalized_company=normalize_company(company),
        job_title=job_title,
        req_id=req_id,
        email_subject=email_subject,
        email_sender=email_sender,
        email_date=email_date,
        status=status,
        source=source,
    )
    session.add(app)
    session.flush()

    # Initial status history entry
    session.add(
        StatusHistory(
            application_id=app.id,
            old_status=None,
            new_status=status,
            change_source=f"email_scan",
        )
    )
    return app, True


def _update_status_if_changed(
    session: Session,
    app: Application,
    new_status: str,
    change_source: str = "email_scan",
    email_date: Optional[datetime] = None,
) -> bool:
    """Update application status and record history. Returns True if changed.

    If email_date is provided, only update if the email is at least as recent
    as the application's current email_date. This prevents older emails
    (e.g., moved from spam with higher UID) from overriding a newer status.
    """
    if not new_status or new_status == app.status:
        return False

    # Protect against backward status from older emails
    if email_date and app.email_date:
        cmp_new = email_date.replace(tzinfo=None) if email_date.tzinfo else email_date
        cmp_cur = app.email_date.replace(tzinfo=None) if app.email_date.tzinfo else app.email_date
        if cmp_new < cmp_cur:
            logger.info(
                "status_update_skipped_older_email",
                app_id=app.id,
                old_status=app.status,
                attempted_status=new_status,
                email_date=str(email_date),
                app_email_date=str(app.email_date),
                source=change_source,
            )
            return False

    old = app.status
    app.status = new_status
    session.add(
        StatusHistory(
            application_id=app.id,
            old_status=old,
            new_status=new_status,
            change_source=change_source,
        )
    )
    logger.info("status_updated", app_id=app.id, old=old, new=new_status)
    return True


def _cleanup_orphaned_app(
    session: Session,
    app_id: Optional[int],
    *,
    exclude_processed_email_id: Optional[int] = None,
    summary: Optional[ScanSummary] = None,
) -> None:
    """删除孤立的Application（没有其他邮件引用时）。

    当邮件被重新分类（非求职）或关联到不同Application时调用。
    - 检查是否有其他 processed_email 记录引用该 application_id
    - 如果没有其他引用，删除 Application 和关联的 StatusHistory
    - 如果有其他引用，保留（其他邮件还需要它）
    """
    if app_id is None:
        return
    refs_q = session.query(ProcessedEmail).filter(ProcessedEmail.application_id == app_id)
    if exclude_processed_email_id is not None:
        refs_q = refs_q.filter(ProcessedEmail.id != exclude_processed_email_id)
    other_refs = refs_q.count()
    if other_refs == 0:
        app = session.query(Application).get(app_id)
        if app:
            session.query(StatusHistory).filter(
                StatusHistory.application_id == app_id
            ).delete()
            session.delete(app)
            if summary is not None:
                summary.applications_deleted += 1
            logger.info(
                "application_deleted_orphaned",
                app_id=app_id,
                excluded_email_id=exclude_processed_email_id,
                company=app.company,
                job_title=app.job_title,
            )
    else:
        logger.info(
            "application_kept_has_other_refs",
            app_id=app_id,
            excluded_email_id=exclude_processed_email_id,
            other_refs=other_refs,
        )


def _find_existing_processed_email(
    session: Session,
    config: AppConfig,
    uid: int,
    gmail_message_id: Optional[str],
) -> Optional[ProcessedEmail]:
    """Find existing processed email row by message-id first, then UID/account/folder."""
    if gmail_message_id:
        by_message = (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.gmail_message_id == gmail_message_id)
            .first()
        )
        if by_message is not None:
            return by_message

    return (
        session.query(ProcessedEmail)
        .filter(
            ProcessedEmail.uid == uid,
            ProcessedEmail.email_account == config.email_username,
            ProcessedEmail.email_folder == config.email_folder,
        )
        .first()
    )


def _get_previous_app_id(
    session: Session,
    uid: int,
    config: AppConfig,
    gmail_message_id: Optional[str] = None,
) -> Optional[int]:
    """获取该邮件UID之前关联的application_id（用于重新扫描时的清理）。"""
    existing = _find_existing_processed_email(session, config, uid, gmail_message_id)
    return existing.application_id if existing else None


def _is_already_processed(session: Session, uid: int, account: str, folder: str) -> bool:
    """Check if this email UID has already been processed."""
    return (
        session.query(ProcessedEmail)
        .filter(
            ProcessedEmail.uid == uid,
            ProcessedEmail.email_account == account,
            ProcessedEmail.email_folder == folder,
        )
        .first()
        is not None
    )


def _get_scan_state(session: Session, account: str, folder: str) -> int:
    """Return last_uid for the given account+folder, or 0."""
    state = (
        session.query(ScanState)
        .filter(ScanState.email_account == account, ScanState.email_folder == folder)
        .first()
    )
    return state.last_uid if state else 0


def _update_scan_state(
    session: Session, account: str, folder: str, last_uid: int
) -> None:
    """Upsert the scan state for account+folder."""
    state = (
        session.query(ScanState)
        .filter(ScanState.email_account == account, ScanState.email_folder == folder)
        .first()
    )
    now = datetime.utcnow()
    if state:
        state.last_uid = last_uid
        state.last_scan_at = now
    else:
        session.add(
            ScanState(
                email_account=account,
                email_folder=folder,
                last_uid=last_uid,
                last_scan_at=now,
            )
        )


def _process_single_email(
    session: Session,
    config: AppConfig,
    llm_provider: Optional[LLMProvider],
    uid: int,
    parsed: ParsedEmailData,
    summary: ScanSummary,
) -> None:
    """Process one parsed email: classify, extract, persist.

    重新扫描时会更新数据库中的所有相关数据：
    - 如果邮件从"求职相关"变为"非求职相关"，删除孤立的旧Application
    - 如果邮件仍是求职相关但提取内容变了（公司/职位/状态），更新Application
    - 如果邮件关联到不同的Application，清理旧的孤立Application

    Pipeline order:
    0. 记住之前的app关联（用于清理）
    1. Thread linking (attempt to link via gmail_thread_id BEFORE LLM)
    2. LLM classification + extraction
    3. Determine if job-related (如果非求职，先覆盖邮件记录，再清理旧app并返回)
    4. Extract fields
    5. Persist application (更新所有字段)
    6. Record processed email（同一封邮件按 message-id 覆盖）
    7. 清理孤立的旧Application（如果关联变了）
    """
    subject = parsed.subject
    sender = parsed.sender
    body = parsed.body_text
    email_date = parsed.date_dt
    gmail_message_id = parsed.message_id
    # ── Step 0: 记住之前的app关联 ─────────────────────────
    previous_app_id = _get_previous_app_id(session, uid, config, gmail_message_id)

    # ── Step 1: (Thread linking removed — unreliable for companies
    #    like Amazon that reuse threads for different positions) ────
    linked_app_id: Optional[int] = None
    link_method: str = "new"
    needs_review: bool = False

    if llm_provider is not None:
        logger.info("llm_extracting", uid=uid)

    # ── Step 2~4: Shared core (classification + extraction) ──────────────
    core_prediction = run_core_classification_and_extraction(
        sender=sender,
        subject=subject,
        body=body,
        llm_provider=llm_provider,
        llm_timeout_sec=config.llm_timeout_sec,
        validate_job_title=_validate_job_title,
    )
    llm_result = core_prediction.classification.llm_result
    llm_used = core_prediction.classification.llm_used
    is_trackable_job = core_prediction.classification.is_trackable_job
    non_job_reason = core_prediction.classification.non_job_reason

    if llm_result is not None:
        summary.total_prompt_tokens += llm_result.prompt_tokens
        summary.total_completion_tokens += llm_result.completion_tokens
        summary.total_estimated_cost += llm_result.estimated_cost_usd

    if not is_trackable_job:
        if llm_result is not None:
            logger.info(
                "email_skipped_llm",
                uid=uid,
                email_category=llm_result.email_category,
                non_job_reason=non_job_reason,
            )
        elif llm_used:
            logger.info("email_skipped_rules_fallback", uid=uid, non_job_reason=non_job_reason)
        else:
            logger.info("email_skipped_rules", uid=uid, non_job_reason=non_job_reason)
        recorded_email = _record_processed(
            session, uid, config, parsed, is_job=False, app_id=None, llm_used=llm_used,
            llm_result=llm_result,
        )
        _cleanup_orphaned_app(
            session,
            previous_app_id,
            exclude_processed_email_id=recorded_email.id,
            summary=summary,
        )
        return

    extraction = core_prediction.extraction
    if extraction is None:
        # Defensive guard: trackable emails should always include extraction output.
        logger.warning("core_extraction_missing_for_trackable", uid=uid, subject=subject[:120])
        return

    company = extraction.company or "Unknown"
    job_title = extraction.job_title
    req_id = extraction.req_id
    status = extraction.status

    # ── Step 4.5: Company-based linking (fallback) ────────
    # If thread linking didn't find a match, try company name.
    if linked_app_id is None and company != "Unknown":
        company_link = resolve_by_company(
            session, company,
            extracted_status=status,
            job_title=job_title,
            req_id=req_id,
            email_date=email_date,
            exclude_application_id=previous_app_id,
            llm_provider=llm_provider,
            email_subject=subject,
            email_sender=sender,
            email_body=body,
        )
        if company_link.is_linked:
            linked_app_id = company_link.application_id
            link_method = company_link.link_method
        elif company_link.needs_review:
            needs_review = True

    # ── Step 5: Persist application (更新所有字段) ─────────
    if linked_app_id is not None:
        app = session.query(Application).get(linked_app_id)
        if app is None:
            # Fallback: linked app was deleted, create new
            logger.warning("linked_app_not_found", application_id=linked_app_id)
            app, created = _get_or_create_application(
                session,
                company=company,
                job_title=job_title,
                req_id=req_id,
                email_subject=subject,
                email_sender=sender,
                email_date=email_date,
                status=status,
            )
            if created:
                summary.applications_created += 1
                logger.info("created_new_application", uid=uid, company=company, title=job_title)
        else:
            created = False
            # 更新所有可能变化的字段（重新扫描时内容可能不同）
            changed = False
            # For req-id direct links, keep canonical app key fields stable.
            # This avoids unique-key collisions when title extraction varies across emails.
            # Also keep key fields stable when this email is being re-linked from one app
            # to another in the same transaction (old app row is removed later).
            relinking_from_different_app = (
                previous_app_id is not None and previous_app_id != app.id
            )
            if link_method != "company_req_id" and not relinking_from_different_app:
                if company and app.company != company:
                    app.company = company
                    app.normalized_company = normalize_company(company)
                    changed = True
                if job_title and app.job_title != job_title:
                    app.job_title = job_title
                    changed = True
                if req_id and app.req_id != req_id:
                    app.req_id = req_id
                    changed = True
            elif relinking_from_different_app:
                logger.info(
                    "application_key_fields_kept_on_relink",
                    app_id=app.id,
                    previous_app_id=previous_app_id,
                    link_method=link_method,
                )
            if email_date:
                # Normalize both datetimes to naive UTC for comparison
                cmp_email_date = email_date.replace(tzinfo=None) if email_date.tzinfo else email_date
                cmp_app_date = app.email_date.replace(tzinfo=None) if app.email_date and app.email_date.tzinfo else app.email_date
                if cmp_app_date is None or cmp_email_date > cmp_app_date:
                    app.email_date = email_date
                    app.email_subject = subject
                    app.email_sender = sender
                    changed = True
            if _update_status_if_changed(session, app, status, change_source=f"email_uid_{uid}", email_date=email_date):
                summary.applications_updated += 1
                changed = True
            if changed:
                app.updated_at = datetime.utcnow()
                logger.info("application_updated_rescan", app_id=app.id, company=company, title=job_title)
    else:
        app, created = _get_or_create_application(
            session,
            company=company,
            job_title=job_title,
            req_id=req_id,
            email_subject=subject,
            email_sender=sender,
            email_date=email_date,
            status=status,
        )
        if created:
            summary.applications_created += 1
            logger.info("created_new_application", uid=uid, company=company, title=job_title)
        else:
            updated = _update_status_if_changed(session, app, status, change_source=f"email_uid_{uid}", email_date=email_date)
            if updated:
                summary.applications_updated += 1

    summary.emails_matched += 1

    # ── Step 6: Record processed email ────────────────────
    recorded_email = _record_processed(
        session, uid, config, parsed,
        is_job=is_trackable_job, app_id=app.id, llm_used=llm_used, llm_result=llm_result,
        link_method=link_method, needs_review=needs_review,
    )

    # ── Step 7: 清理孤立的旧Application ───────────────────
    # 如果这封邮件之前关联到不同的app，清理旧的（如果没有其他邮件引用）
    if previous_app_id is not None and previous_app_id != app.id:
        _cleanup_orphaned_app(
            session,
            previous_app_id,
            exclude_processed_email_id=recorded_email.id,
            summary=summary,
        )


def _record_processed(
    session: Session,
    uid: int,
    config: AppConfig,
    parsed: ParsedEmailData,
    *,
    is_job: bool,
    app_id: Optional[int],
    llm_used: bool,
    llm_result: Optional[LLMExtractionResult] = None,
    link_method: str = "new",
    needs_review: bool = False,
) -> ProcessedEmail:
    """Insert or update a row in processed_emails (supports re-scanning).
    
    Now also stores gmail_message_id, gmail_thread_id, link_method, and needs_review.
    """
    existing = _find_existing_processed_email(session, config, uid, parsed.message_id)
    if existing:
        # Overwrite existing row for the same email (message-id first, UID fallback).
        existing.uid = uid
        existing.email_account = config.email_username
        existing.email_folder = config.email_folder
        existing.subject = parsed.subject
        existing.sender = parsed.sender
        existing.email_date = parsed.date_dt
        existing.is_job_related = is_job
        existing.application_id = app_id
        existing.llm_used = llm_used
        existing.prompt_tokens = llm_result.prompt_tokens if llm_result else 0
        existing.completion_tokens = llm_result.completion_tokens if llm_result else 0
        existing.estimated_cost_usd = llm_result.estimated_cost_usd if llm_result else 0.0
        existing.link_method = link_method
        existing.needs_review = needs_review
        # Keep message-id stable unless this row was missing it.
        if parsed.message_id and (
            not existing.gmail_message_id or existing.gmail_message_id == parsed.message_id
        ):
            existing.gmail_message_id = parsed.message_id
        if parsed.gmail_thread_id:
            existing.gmail_thread_id = parsed.gmail_thread_id
        return existing
    else:
        processed = ProcessedEmail(
            uid=uid,
            email_account=config.email_username,
            email_folder=config.email_folder,
            gmail_message_id=parsed.message_id,
            gmail_thread_id=parsed.gmail_thread_id,
            subject=parsed.subject,
            sender=parsed.sender,
            email_date=parsed.date_dt,
            is_job_related=is_job,
            application_id=app_id,
            llm_used=llm_used,
            link_method=link_method,
            needs_review=needs_review,
            prompt_tokens=llm_result.prompt_tokens if llm_result else 0,
            completion_tokens=llm_result.completion_tokens if llm_result else 0,
            estimated_cost_usd=llm_result.estimated_cost_usd if llm_result else 0.0,
        )
        session.add(processed)
        session.flush()
        return processed


def run_scan(
    config: AppConfig,
    session: Session,
    should_cancel: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> ScanSummary:
    """Execute a full email scan: fetch the latest N emails, extract, persist.

    Always scans the most recent `max_scan_emails` emails from the inbox.
    Every email is re-analyzed even if previously scanned.
    
    Args:
        config: Application configuration
        session: Database session
        should_cancel: Optional callable that returns True if scan should be cancelled
        progress_callback: Optional callback for progress updates (for SSE streaming)
    """
    summary = ScanSummary()

    # Resolve LLM provider
    llm_provider: Optional[LLMProvider] = None
    if config.llm_enabled:
        try:
            llm_provider = create_llm_provider(config)
            logger.info("llm_provider_ready", provider=config.llm_provider, model=config.llm_model)
        except Exception as exc:
            logger.warning("llm_provider_init_failed", error=str(exc))

    scan_count = config.max_scan_emails
    logger.info("scan_starting", count=scan_count)

    with IMAPClient(config) as imap:
        uids = imap.fetch_latest_uids(scan_count)
        summary.emails_scanned = len(uids)

        max_uid = 0
        for idx, uid in enumerate(uids, start=1):
            # Check for cancellation
            if should_cancel and should_cancel():
                logger.warning("scan_cancelled", processed=idx-1, total=len(uids))
                summary.cancelled = True
                summary.emails_scanned = idx - 1
                if progress_callback:
                    progress_callback({
                        "processed": idx - 1,
                        "total": len(uids),
                        "current_subject": "",
                        "status": "cancelled",
                    })
                break

            logger.info("processing_email", index=idx, total=len(uids), uid=uid)

            try:
                _, msg, gmail_thread_id = imap.fetch_message(uid)
                if msg is None:
                    continue
                parsed = parse_email_message(msg, gmail_thread_id=gmail_thread_id)
                
                # Send progress update before processing
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(uids),
                        "current_subject": parsed.subject[:100] if parsed.subject else "",
                        "status": "processing",
                    })
                
                _process_single_email(session, config, llm_provider, uid, parsed, summary)
                max_uid = max(max_uid, uid)
            except Exception as exc:
                error_msg = f"uid={uid}: {exc}"
                logger.error("email_processing_error", uid=uid, error=str(exc))
                session.rollback()
                summary.errors.append(error_msg)
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(uids),
                        "current_subject": "",
                        "status": "error",
                    })

        # Update scan state with the highest UID processed
        if max_uid > 0:
            _update_scan_state(session, config.email_username, config.email_folder, max_uid)

    session.commit()

    # Send completion progress
    if progress_callback and not summary.cancelled:
        progress_callback({
            "processed": summary.emails_scanned,
            "total": summary.emails_scanned,
            "current_subject": "",
            "status": "completed",
        })

    if summary.cancelled:
        logger.info(
            "scan_cancelled_summary",
            scanned=summary.emails_scanned,
            matched=summary.emails_matched,
            created=summary.applications_created,
            updated=summary.applications_updated,
            cost=f"${summary.total_estimated_cost:.6f}",
            errors=len(summary.errors),
        )
    else:
        logger.info(
            "scan_complete",
            scanned=summary.emails_scanned,
            matched=summary.emails_matched,
            created=summary.applications_created,
            updated=summary.applications_updated,
            cost=f"${summary.total_estimated_cost:.6f}",
            errors=len(summary.errors),
        )
    return summary


def run_date_range_scan(
    config: AppConfig,
    session: Session,
    since_date: Optional[str] = None,
    before_date: Optional[str] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> ScanSummary:
    """Execute an email scan filtering by date range.

    Args:
        config: Application configuration
        session: Database session
        since_date: Start date in 'YYYY-MM-DD' format (inclusive)
        before_date: End date in 'YYYY-MM-DD' format (exclusive)
        should_cancel: Optional callable that returns True if scan should be cancelled
        progress_callback: Optional callback for progress updates (for SSE streaming)
    """
    summary = ScanSummary()

    # Resolve LLM provider
    llm_provider: Optional[LLMProvider] = None
    if config.llm_enabled:
        try:
            llm_provider = create_llm_provider(config)
            logger.info("llm_provider_ready", provider=config.llm_provider, model=config.llm_model)
        except Exception as exc:
            logger.warning("llm_provider_init_failed", error=str(exc))

    logger.info("date_range_scan_start", since=since_date, before=before_date)

    with IMAPClient(config) as imap:
        uids = imap.fetch_uids_by_date_range(since_date, before_date)
        summary.emails_scanned = len(uids)

        max_uid = 0
        for idx, uid in enumerate(uids, start=1):
            # Check for cancellation
            if should_cancel and should_cancel():
                logger.warning("scan_cancelled", processed=idx-1, total=len(uids))
                summary.cancelled = True
                summary.emails_scanned = idx - 1
                if progress_callback:
                    progress_callback({
                        "processed": idx - 1,
                        "total": len(uids),
                        "current_subject": "",
                        "status": "cancelled",
                    })
                break

            logger.info("processing_email", index=idx, total=len(uids), uid=uid)

            try:
                _, msg, gmail_thread_id = imap.fetch_message(uid)
                if msg is None:
                    continue
                parsed = parse_email_message(msg, gmail_thread_id=gmail_thread_id)

                # Send progress update before processing
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(uids),
                        "current_subject": parsed.subject[:100] if parsed.subject else "",
                        "status": "processing",
                    })

                _process_single_email(session, config, llm_provider, uid, parsed, summary)
                max_uid = max(max_uid, uid)
            except Exception as exc:
                error_msg = f"uid={uid}: {exc}"
                logger.error("email_processing_error", uid=uid, error=str(exc))
                session.rollback()
                summary.errors.append(error_msg)
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(uids),
                        "current_subject": "",
                        "status": "error",
                    })

        # NOTE: Date-range scans do NOT update last_uid (the incremental scan cursor).
        # This is intentional — scanning a historical date range (e.g. Aug 2025) should
        # not regress the cursor used by "Scan New" for incremental scanning.
        # Only run_scan() and run_incremental_scan() update the cursor.

    session.commit()

    # Send completion progress
    if progress_callback and not summary.cancelled:
        progress_callback({
            "processed": summary.emails_scanned,
            "total": summary.emails_scanned,
            "current_subject": "",
            "status": "completed",
        })

    logger.info(
        "date_range_scan_complete",
        since=since_date,
        before=before_date,
        scanned=summary.emails_scanned,
        matched=summary.emails_matched,
        created=summary.applications_created,
        updated=summary.applications_updated,
        cost=f"${summary.total_estimated_cost:.6f}",
        errors=len(summary.errors),
    )
    return summary


def run_incremental_scan(
    config: AppConfig,
    session: Session,
    should_cancel: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> ScanSummary:
    """Execute an incremental scan: only process emails after the last scanned UID.
    
    This is more efficient than run_scan as it only processes new emails.
    
    Args:
        config: Application configuration
        session: Database session
        should_cancel: Optional callable that returns True if scan should be cancelled
        progress_callback: Optional callback for progress updates (for SSE streaming)
    """
    summary = ScanSummary()

    # Get the last scanned UID
    last_uid = _get_scan_state(session, config.email_username, config.email_folder)
    logger.info("incremental_scan_starting", last_uid=last_uid, max_scan_emails=config.max_scan_emails)

    # Resolve LLM provider
    llm_provider: Optional[LLMProvider] = None
    if config.llm_enabled:
        try:
            llm_provider = create_llm_provider(config)
            logger.info("llm_provider_ready", provider=config.llm_provider, model=config.llm_model)
        except Exception as exc:
            logger.warning("llm_provider_init_failed", error=str(exc))

    with IMAPClient(config) as imap:
        uids = imap.fetch_uids_after(last_uid)
        summary.emails_scanned = len(uids)
        
        if not uids:
            logger.info("incremental_scan_no_new_emails", last_uid=last_uid)
            if progress_callback:
                progress_callback({
                    "processed": 0,
                    "total": 0,
                    "current_subject": "",
                    "status": "completed",
                })
            return summary

        max_uid = last_uid
        for idx, uid in enumerate(uids, start=1):
            if should_cancel and should_cancel():
                logger.warning("scan_cancelled", processed=idx-1, total=len(uids))
                summary.cancelled = True
                summary.emails_scanned = idx - 1
                if progress_callback:
                    progress_callback({
                        "processed": idx - 1,
                        "total": len(uids),
                        "current_subject": "",
                        "status": "cancelled",
                    })
                break

            logger.info("processing_email", index=idx, total=len(uids), uid=uid)

            try:
                _, msg, gmail_thread_id = imap.fetch_message(uid)
                if msg is None:
                    continue
                parsed = parse_email_message(msg, gmail_thread_id=gmail_thread_id)
                
                # Send progress update before processing
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(uids),
                        "current_subject": parsed.subject[:100] if parsed.subject else "",
                        "status": "processing",
                    })
                
                _process_single_email(session, config, llm_provider, uid, parsed, summary)
                max_uid = max(max_uid, uid)
            except Exception as exc:
                error_msg = f"uid={uid}: {exc}"
                logger.error("email_processing_error", uid=uid, error=str(exc))
                session.rollback()
                summary.errors.append(error_msg)
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(uids),
                        "current_subject": "",
                        "status": "error",
                    })

        # Update scan state with the highest UID processed
        if max_uid > last_uid:
            _update_scan_state(session, config.email_username, config.email_folder, max_uid)

    session.commit()

    # Send completion progress
    if progress_callback and not summary.cancelled:
        progress_callback({
            "processed": summary.emails_scanned,
            "total": summary.emails_scanned,
            "current_subject": "",
            "status": "completed",
        })

    logger.info(
        "incremental_scan_complete",
        scanned=summary.emails_scanned,
        matched=summary.emails_matched,
        created=summary.applications_created,
        updated=summary.applications_updated,
        cost=f"${summary.total_estimated_cost:.6f}",
        errors=len(summary.errors),
    )
    return summary
