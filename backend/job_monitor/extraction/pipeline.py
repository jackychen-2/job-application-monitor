"""Extraction pipeline — orchestrates rule-based and LLM-based extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
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

from job_monitor.application_dates import assign_applied_at_if_missing
from job_monitor.config import AppConfig
from job_monitor.dedupe import merge_owner_duplicate_applications
from job_monitor.email.gmail_client import (
    GmailClient,
    GmailHistoryExpiredError,
    GmailMessageNotFoundError,
    is_inbox_message,
)
from job_monitor.email.parser import ParsedEmailData, parse_email_message
from job_monitor.extraction.core import run_core_classification_and_extraction
from job_monitor.extraction.llm import (
    LLMExtractionResult,
    LLMProvider,
    create_llm_provider,
)
from job_monitor.extraction.rules import (
    extract_job_req_id,
    extract_job_title,
    is_blank_or_artifact_job_title,
    normalize_job_title_candidate,
    normalize_req_id,
    split_title_and_req_id,
    validate_job_title_candidate,
)
from job_monitor.linking.resolver import (
    normalize_company,
    resolve_by_company,
)

def _is_manual_source(source: str | None) -> bool:
    return (source or "").strip().lower().startswith("manual")


def _validate_job_title(title: str) -> str:
    """Return the title if valid, or empty string for garbage values."""
    return validate_job_title_candidate(title)


def _normalize_stored_title_for_rescan(existing_title: str | None, incoming_title: str | None) -> str | None:
    """Decide whether a rescan should repair an already stored title.

    We only clear/replace legacy titles that are blank or clear OA/event
    artifacts. A merely "odd" title should not be erased by a later rescan.
    """
    current_raw = (existing_title or "").strip()
    if not current_raw:
        cleaned_incoming = _validate_job_title(incoming_title or "")
        return cleaned_incoming or None

    if not is_blank_or_artifact_job_title(current_raw):
        return None

    cleaned_incoming = _validate_job_title(incoming_title or "")
    return cleaned_incoming or ""


def _extract_title_and_req_id(
    subject: str,
    body: str,
    *,
    preferred_title: str = "",
    preferred_req_id: str = "",
) -> tuple[str, str]:
    """Extract normalized (job_title, req_id) with fallbacks from title/body."""
    req_id = normalize_req_id(preferred_req_id or "")
    candidates = [preferred_title, extract_job_title(subject, body)]
    seen: set[str] = set()

    for raw in candidates:
        title_raw = (raw or "").strip()
        if not title_raw or title_raw in seen:
            continue
        seen.add(title_raw)

        base_title, req_from_title = split_title_and_req_id(title_raw)
        title = _validate_job_title(base_title or title_raw)

        if not req_id:
            req_id = normalize_req_id(req_from_title)
        if not req_id:
            req_id = normalize_req_id(extract_job_req_id(subject, body, title_raw))

        if title:
            return title, req_id

    return "", req_id


from job_monitor.models import Application, ProcessedEmail, ScanState, StatusHistory

logger = structlog.get_logger(__name__)


@dataclass
class ScanSummary:
    """Result summary after a scan run."""

    emails_scanned: int = 0
    emails_matched: int = 0
    skipped_social_or_promotions: int = 0
    skipped_not_job_related: int = 0
    skipped_message_unavailable: int = 0
    non_job_reason_counts: dict[str, int] = field(default_factory=dict)
    applications_created: int = 0
    applications_updated: int = 0
    applications_deleted: int = 0
    created_application_ids: list[int] = field(default_factory=list)
    updated_application_ids: list[int] = field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_estimated_cost: float = 0.0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def _append_unique_application_id(bucket: list[int], application_id: int) -> None:
    if application_id not in bucket:
        bucket.append(application_id)


def _increment_count(bucket: dict[str, int], key: str) -> None:
    bucket[key] = bucket.get(key, 0) + 1


def _merge_scan_summary(target: ScanSummary, delta: ScanSummary) -> None:
    """Accumulate counters from a successfully committed per-email summary."""
    target.emails_matched += delta.emails_matched
    target.skipped_social_or_promotions += delta.skipped_social_or_promotions
    target.skipped_not_job_related += delta.skipped_not_job_related
    target.skipped_message_unavailable += delta.skipped_message_unavailable
    for reason, count in delta.non_job_reason_counts.items():
        target.non_job_reason_counts[reason] = target.non_job_reason_counts.get(reason, 0) + count
    target.applications_created += delta.applications_created
    target.applications_updated += delta.applications_updated
    target.applications_deleted += delta.applications_deleted
    for application_id in delta.created_application_ids:
        _append_unique_application_id(target.created_application_ids, application_id)
    for application_id in delta.updated_application_ids:
        _append_unique_application_id(target.updated_application_ids, application_id)
    target.total_prompt_tokens += delta.total_prompt_tokens
    target.total_completion_tokens += delta.total_completion_tokens
    target.total_estimated_cost += delta.total_estimated_cost
    target.errors.extend(delta.errors)


def build_title_req_filters(model_cls: type, job_title: str | None, req_id: str | None) -> list:
    """Build dedup filters for (job_title, req_id), with fallback when req_id is absent.

    Eval code imports this helper to keep grouping dedup logic aligned with production.
    Some models (e.g., current Application schema) may not expose ``req_id``; in that
    case we gracefully fall back to title-only matching.
    """
    jt = (job_title or "").strip()
    rq = (req_id or "").strip()
    has_req = hasattr(model_cls, "req_id")

    if not has_req:
        if jt:
            return [model_cls.job_title == jt]
        return [(model_cls.job_title == None) | (model_cls.job_title == "")]  # noqa: E711

    if jt and rq:
        return [model_cls.job_title == jt, model_cls.req_id == rq]
    if jt:
        return [
            model_cls.job_title == jt,
            (model_cls.req_id == None) | (model_cls.req_id == ""),  # noqa: E711
        ]
    if rq:
        return [
            (model_cls.job_title == None) | (model_cls.job_title == ""),  # noqa: E711
            model_cls.req_id == rq,
        ]
    return [
        (model_cls.job_title == None) | (model_cls.job_title == ""),  # noqa: E711
        (model_cls.req_id == None) | (model_cls.req_id == ""),  # noqa: E711
    ]


def _get_or_create_application(
    session: Session,
    owner_user_id: int,
    company: str,
    job_title: str,
    req_id: str,
    email_subject: str,
    email_sender: str,
    email_date: Optional[datetime],
    status: str,
    source: str = "email",
) -> tuple[Application, bool, bool]:
    """Find an existing application or create a new one.

    Returns (application, created, changed_existing) where created=True for new rows.
    Deduplicates by normalized_company + (job_title, req_id).
    Updates existing record if data has changed.
    """
    # Use normalized_company for matching to handle variations like "Qventus, Inc" vs "Qventus"
    normalized = normalize_company(company)
    req_id = normalize_req_id(req_id or "")

    base_query = session.query(Application).filter(
        Application.owner_user_id == owner_user_id,
        Application.normalized_company == normalized,
    )
    existing = base_query.filter(
        *build_title_req_filters(Application, job_title, req_id)
    ).first()
    # Backward-compat: legacy rows may have empty req_id for the same title.
    if existing is None and req_id:
        existing = base_query.filter(
            *build_title_req_filters(Application, job_title, None)
        ).first()

    if existing:
        # Update fields - merge old into most recent
        changed_existing = False
        if existing.company != company:
            existing.company = company
            existing.normalized_company = normalized
            changed_existing = True
        repaired_title = _normalize_stored_title_for_rescan(existing.job_title, job_title)
        if repaired_title is not None and (existing.job_title or "") != repaired_title:
            existing.job_title = repaired_title
            changed_existing = True
        if req_id and existing.req_id != req_id:
            existing.req_id = req_id
            changed_existing = True
        # Always update to most recent email info
        _ed = email_date.replace(tzinfo=None) if email_date and hasattr(email_date, 'tzinfo') and email_date.tzinfo else email_date
        _ad = existing.email_date.replace(tzinfo=None) if existing.email_date and hasattr(existing.email_date, 'tzinfo') and existing.email_date.tzinfo else existing.email_date
        if _ed and (_ad is None or _ed > _ad):
            existing.email_date = email_date
            existing.email_subject = email_subject
            existing.email_sender = email_sender
            changed_existing = True
        if changed_existing:
            existing.updated_at = datetime.utcnow()
        logger.info("application_merged", app_id=existing.id, company=company, job_title=job_title, req_id=req_id)
        return existing, False, changed_existing

    app = Application(
        owner_user_id=owner_user_id,
        company=company,
        normalized_company=normalize_company(company),
        job_title=job_title,
        req_id=req_id or None,
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
            owner_user_id=owner_user_id,
            application_id=app.id,
            old_status=None,
            new_status=status,
            change_source=f"email_scan",
        )
    )
    return app, True, False


def _update_status_if_changed(
    session: Session,
    app: Application,
    new_status: str,
    owner_user_id: int,
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
            owner_user_id=owner_user_id,
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
    owner_user_id: int,
    app_id: Optional[int],
    exclude_uid: int,
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
    other_refs = (
        session.query(ProcessedEmail)
        .filter(
            ProcessedEmail.owner_user_id == owner_user_id,
            ProcessedEmail.application_id == app_id,
            ProcessedEmail.uid != exclude_uid,
        )
        .count()
    )
    if other_refs == 0:
        app = session.query(Application).get(app_id)
        if app:
            if _is_manual_source(app.source):
                logger.info(
                    "application_orphan_cleanup_skipped_manual",
                    app_id=app_id,
                    uid=exclude_uid,
                    company=app.company,
                    job_title=app.job_title,
                    source=app.source,
                )
                return
            session.query(StatusHistory).filter(
                StatusHistory.application_id == app_id
            ).delete()
            session.delete(app)
            if summary is not None:
                summary.applications_deleted += 1
            logger.info("application_deleted_orphaned", app_id=app_id, uid=exclude_uid,
                        company=app.company, job_title=app.job_title)
    else:
        logger.info("application_kept_has_other_refs", app_id=app_id, uid=exclude_uid,
                     other_refs=other_refs)


def _get_previous_app_id(
    session: Session,
    owner_user_id: int,
    uid: int,
    account: str,
    folder: str,
) -> Optional[int]:
    """获取该邮件UID之前关联的application_id（用于重新扫描时的清理）。"""
    existing = (
        session.query(ProcessedEmail)
        .filter(
            ProcessedEmail.owner_user_id == owner_user_id,
            ProcessedEmail.uid == uid,
            ProcessedEmail.email_account == account,
            ProcessedEmail.email_folder == folder,
        )
        .first()
    )
    return existing.application_id if existing else None


def _is_already_processed(
    session: Session,
    owner_user_id: int,
    uid: int,
    account: str,
    folder: str,
) -> bool:
    """Check if this email UID has already been processed."""
    return (
        session.query(ProcessedEmail)
        .filter(
            ProcessedEmail.owner_user_id == owner_user_id,
            ProcessedEmail.uid == uid,
            ProcessedEmail.email_account == account,
            ProcessedEmail.email_folder == folder,
        )
        .first()
        is not None
    )


def _get_scan_state(session: Session, owner_user_id: int, account: str, folder: str) -> int:
    """Return last_uid for the given account+folder, or 0."""
    state = (
        session.query(ScanState)
        .filter(
            ScanState.owner_user_id == owner_user_id,
            ScanState.email_account == account,
            ScanState.email_folder == folder,
        )
        .first()
    )
    return state.last_uid if state else 0


def _update_scan_state(
    session: Session,
    owner_user_id: int,
    account: str,
    folder: str,
    last_uid: int,
) -> None:
    """Upsert the scan state for account+folder."""
    state = (
        session.query(ScanState)
        .filter(
            ScanState.owner_user_id == owner_user_id,
            ScanState.email_account == account,
            ScanState.email_folder == folder,
        )
        .first()
    )
    now = datetime.utcnow()
    if state:
        state.last_uid = last_uid
        state.last_scan_at = now
    else:
        session.add(
            ScanState(
                owner_user_id=owner_user_id,
                email_account=account,
                email_folder=folder,
                last_uid=last_uid,
                last_scan_at=now,
            )
        )


def _rollback_after_email_error(
    session: Session,
    *,
    gmail_message_id: str,
    exc: Exception,
) -> None:
    """Reset session state after a per-email failure so the scan can continue."""
    try:
        session.rollback()
    except Exception as rollback_exc:
        logger.error(
            "email_error_rollback_failed",
            gmail_message_id=gmail_message_id,
            original_error=str(exc),
            rollback_error=str(rollback_exc),
        )


def _rollback_after_step_error(
    session: Session,
    *,
    step: str,
    exc: Exception,
) -> None:
    """Reset session state after a non-email scan step failure."""
    try:
        session.rollback()
    except Exception as rollback_exc:
        logger.error(
            "scan_step_rollback_failed",
            step=step,
            original_error=str(exc),
            rollback_error=str(rollback_exc),
        )


def _process_single_email(
    session: Session,
    config: AppConfig,
    llm_provider: Optional[LLMProvider],
    owner_user_id: int,
    mailbox_email: str,
    mailbox_folder: str,
    uid: int,
    parsed: ParsedEmailData,
    summary: ScanSummary,
    gmail_message_id_override: Optional[str] = None,
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
    3. Determine if job-related (如果非求职，清理旧app并返回)
    4. Extract fields
    5. Persist application (更新所有字段)
    6. 清理孤立的旧Application（如果关联变了）
    7. Record processed email
    """
    subject = parsed.subject
    sender = parsed.sender
    body = parsed.body_text
    email_date = parsed.date_dt
    gmail_message_id = gmail_message_id_override or parsed.message_id
    gmail_thread_id = parsed.gmail_thread_id

    # ── Step 0: 记住之前的app关联 ─────────────────────────
    previous_app_id = _get_previous_app_id(
        session,
        owner_user_id=owner_user_id,
        uid=uid,
        account=mailbox_email,
        folder=mailbox_folder,
    )

    # ── Step 1: (Thread linking removed — unreliable for companies
    #    like Amazon that reuse threads for different positions) ────
    linked_app_id: Optional[int] = None
    link_method: str = "new"
    needs_review: bool = False

    # ── Step 2~4: Shared classification + extraction ─────
    logger.info("core_extracting", uid=uid)
    core_prediction = run_core_classification_and_extraction(
        sender=sender,
        subject=subject,
        body=body,
        llm_provider=llm_provider,
        llm_timeout_sec=config.llm_timeout_sec,
        validate_job_title=_validate_job_title,
    )
    classification = core_prediction.classification
    llm_result = classification.llm_result
    llm_used = classification.llm_used
    non_job_reason = classification.non_job_reason

    if llm_result is not None:
        summary.total_prompt_tokens += llm_result.prompt_tokens
        summary.total_completion_tokens += llm_result.completion_tokens
        summary.total_estimated_cost += llm_result.estimated_cost_usd

    if not classification.is_trackable_job:
        logger.info(
            "email_skipped_not_trackable",
            uid=uid,
            non_job_reason=non_job_reason,
            predicted_email_category=classification.predicted_email_category,
        )
        summary.skipped_not_job_related += 1
        if non_job_reason:
            _increment_count(summary.non_job_reason_counts, non_job_reason)
        _cleanup_orphaned_app(
            session,
            owner_user_id=owner_user_id,
            app_id=previous_app_id,
            exclude_uid=uid,
            summary=summary,
        )
        _record_processed(
            session,
            uid,
            mailbox_email,
            mailbox_folder,
            owner_user_id,
            parsed,
            is_job=False,
            app_id=None,
            llm_used=llm_used,
            llm_result=llm_result,
            gmail_message_id=gmail_message_id,
        )
        return

    extraction = core_prediction.extraction
    if extraction is None:
        logger.warning("trackable_email_missing_extraction", uid=uid)
        summary.skipped_not_job_related += 1
        _cleanup_orphaned_app(
            session,
            owner_user_id=owner_user_id,
            app_id=previous_app_id,
            exclude_uid=uid,
            summary=summary,
        )
        _record_processed(
            session,
            uid,
            mailbox_email,
            mailbox_folder,
            owner_user_id,
            parsed,
            is_job=False,
            app_id=None,
            llm_used=llm_used,
            llm_result=llm_result,
            gmail_message_id=gmail_message_id,
        )
        return

    company = extraction.company or "Unknown"
    job_title = extraction.job_title
    req_id = extraction.req_id
    status = extraction.status

    # ── Step 4.5: Company-based linking (fallback) ────────
    # If thread linking didn't find a match, try company name
    if linked_app_id is None and company != "Unknown":
        company_link = resolve_by_company(
            session, company,
            extracted_status=status,
            job_title=job_title,
            req_id=req_id,
            email_date=email_date,
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
            app, created, changed = _get_or_create_application(
                session,
                owner_user_id=owner_user_id,
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
                _append_unique_application_id(summary.created_application_ids, app.id)
                logger.info("created_new_application", uid=uid, company=company, title=job_title)
        else:
            created = False
            # 更新所有可能变化的字段（重新扫描时内容可能不同）
            changed = False
            if company and app.company != company:
                app.company = company
                app.normalized_company = normalize_company(company)
                changed = True
            repaired_title = _normalize_stored_title_for_rescan(app.job_title, job_title)
            if repaired_title is not None and (app.job_title or "") != repaired_title:
                app.job_title = repaired_title
                changed = True
            if req_id and app.req_id != req_id:
                app.req_id = req_id
                changed = True
            if email_date:
                # Normalize both datetimes to naive UTC for comparison
                cmp_email_date = email_date.replace(tzinfo=None) if email_date.tzinfo else email_date
                cmp_app_date = app.email_date.replace(tzinfo=None) if app.email_date and app.email_date.tzinfo else app.email_date
                if cmp_app_date is None or cmp_email_date > cmp_app_date:
                    app.email_date = email_date
                    app.email_subject = subject
                    app.email_sender = sender
                    changed = True
            if _update_status_if_changed(
                session,
                app,
                status,
                owner_user_id=owner_user_id,
                change_source=f"email_uid_{uid}",
                email_date=email_date,
            ):
                changed = True
            if changed:
                app.updated_at = datetime.utcnow()
                logger.info("application_updated_rescan", app_id=app.id, company=company, title=job_title)
            if changed:
                summary.applications_updated += 1
                _append_unique_application_id(summary.updated_application_ids, app.id)
    else:
        app, created, changed = _get_or_create_application(
            session,
            owner_user_id=owner_user_id,
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
            _append_unique_application_id(summary.created_application_ids, app.id)
            logger.info("created_new_application", uid=uid, company=company, title=job_title)
        else:
            changed = _update_status_if_changed(
                session,
                app,
                status,
                owner_user_id=owner_user_id,
                change_source=f"email_uid_{uid}",
                email_date=email_date,
            ) or changed
            if changed:
                summary.applications_updated += 1
                _append_unique_application_id(summary.updated_application_ids, app.id)

    assign_applied_at_if_missing(
        app,
        status=app.status,
        preferred_at=email_date,
        fallback_at=app.created_at,
    )

    # ── Step 6: 清理孤立的旧Application ───────────────────
    # 如果这封邮件之前关联到不同的app，清理旧的（如果没有其他邮件引用）
    if previous_app_id is not None and previous_app_id != app.id:
        _cleanup_orphaned_app(
            session,
            owner_user_id=owner_user_id,
            app_id=previous_app_id,
            exclude_uid=uid,
            summary=summary,
        )

    summary.emails_matched += 1

    # ── Step 7: Record processed email ────────────────────
    _record_processed(
        session, uid, mailbox_email, mailbox_folder, owner_user_id, parsed,
        is_job=True, app_id=app.id, llm_used=llm_used, llm_result=llm_result,
        link_method=link_method, needs_review=needs_review,
        gmail_message_id=gmail_message_id,
    )


def _record_processed(
    session: Session,
    uid: int,
    account: str,
    folder: str,
    owner_user_id: int,
    parsed: ParsedEmailData,
    *,
    is_job: bool,
    app_id: Optional[int],
    llm_used: bool,
    llm_result: Optional[LLMExtractionResult] = None,
    link_method: str = "new",
    needs_review: bool = False,
    gmail_message_id: Optional[str] = None,
) -> None:
    """Insert or update a row in processed_emails (supports re-scanning).
    
    Now also stores gmail_message_id, gmail_thread_id, link_method, and needs_review.
    """
    effective_gmail_message_id = gmail_message_id or parsed.message_id

    existing = None
    if effective_gmail_message_id:
        existing = (
            session.query(ProcessedEmail)
            .filter(
                ProcessedEmail.owner_user_id == owner_user_id,
                ProcessedEmail.gmail_message_id == effective_gmail_message_id,
            )
            .first()
        )
    if existing is None:
        existing = (
            session.query(ProcessedEmail)
            .filter(
                ProcessedEmail.owner_user_id == owner_user_id,
                ProcessedEmail.uid == uid,
                ProcessedEmail.email_account == account,
                ProcessedEmail.email_folder == folder,
            )
            .first()
        )
    if existing:
        # Update existing record
        existing.is_job_related = is_job
        existing.application_id = app_id
        existing.llm_used = llm_used
        existing.prompt_tokens = llm_result.prompt_tokens if llm_result else 0
        existing.completion_tokens = llm_result.completion_tokens if llm_result else 0
        existing.estimated_cost_usd = llm_result.estimated_cost_usd if llm_result else 0.0
        existing.link_method = link_method
        existing.needs_review = needs_review
        # Update gmail fields if not already set
        if effective_gmail_message_id and not existing.gmail_message_id:
            existing.gmail_message_id = effective_gmail_message_id
        if parsed.gmail_thread_id and not existing.gmail_thread_id:
            existing.gmail_thread_id = parsed.gmail_thread_id
    else:
        session.add(
            ProcessedEmail(
                owner_user_id=owner_user_id,
                uid=uid,
                email_account=account,
                email_folder=folder,
                gmail_message_id=effective_gmail_message_id,
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
        )


def run_scan(
    config: AppConfig,
    session: Session,
    owner_user_id: int,
    mailbox_email: str,
    oauth_access_token: str | None = None,
    mailbox_folder: str | None = None,
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

    email_folder = mailbox_folder or config.email_folder

    with GmailClient(config, oauth_access_token=oauth_access_token or "") as gmail:
        message_ids, latest_history_id = gmail.fetch_latest_message_ids(scan_count)
        summary.emails_scanned = len(message_ids)

        max_history_id = 0
        for idx, gmail_message_id in enumerate(message_ids, start=1):
            # Check for cancellation
            if should_cancel and should_cancel():
                logger.warning("scan_cancelled", processed=idx-1, total=len(message_ids))
                summary.cancelled = True
                summary.emails_scanned = idx - 1
                if progress_callback:
                    progress_callback({
                        "processed": idx - 1,
                        "total": len(message_ids),
                        "current_subject": "",
                        "status": "cancelled",
                    })
                break

            logger.info("processing_email", index=idx, total=len(message_ids), gmail_message_id=gmail_message_id)

            try:
                uid, msg, gmail_thread_id, _, history_id, label_ids = gmail.fetch_message(gmail_message_id)
                if not is_inbox_message(label_ids):
                    logger.info("email_skipped_social_or_promotions", gmail_message_id=gmail_message_id)
                    summary.skipped_social_or_promotions += 1
                    max_history_id = max(max_history_id, history_id)
                    continue
                if msg is None:
                    summary.skipped_message_unavailable += 1
                    continue
                parsed = parse_email_message(msg, gmail_thread_id=gmail_thread_id)
                
                # Send progress update before processing
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(message_ids),
                        "current_subject": parsed.subject[:100] if parsed.subject else "",
                        "status": "processing",
                    })

                email_summary = ScanSummary()
                _process_single_email(
                    session,
                    config,
                    llm_provider,
                    owner_user_id,
                    mailbox_email,
                    email_folder,
                    uid,
                    parsed,
                    email_summary,
                    gmail_message_id_override=gmail_message_id,
                )
                session.commit()
                _merge_scan_summary(summary, email_summary)
                max_history_id = max(max_history_id, history_id)
            except GmailMessageNotFoundError:
                logger.info("email_skipped_message_unavailable", gmail_message_id=gmail_message_id)
                summary.skipped_message_unavailable += 1
            except Exception as exc:
                _rollback_after_email_error(
                    session,
                    gmail_message_id=gmail_message_id,
                    exc=exc,
                )
                error_msg = f"gmail_message_id={gmail_message_id}: {exc}"
                logger.error("email_processing_error", gmail_message_id=gmail_message_id, error=str(exc))
                summary.errors.append(error_msg)
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(message_ids),
                        "current_subject": "",
                        "status": "error",
                    })

        # Update scan state with the latest history ID for incremental sync.
        cursor = max(max_history_id, latest_history_id)
        if cursor > 0:
            try:
                _update_scan_state(session, owner_user_id, mailbox_email, email_folder, cursor)
                session.commit()
            except Exception as exc:
                _rollback_after_step_error(session, step="scan_state_update", exc=exc)
                error_msg = f"scan_state_update: {exc}"
                logger.warning("scan_state_update_failed", owner_user_id=owner_user_id, error=str(exc))
                summary.errors.append(error_msg)

    try:
        merged = merge_owner_duplicate_applications(
            session,
            owner_user_id,
            session.info.get("journey_id"),
        )
        session.commit()
        if merged > 0:
            summary.applications_deleted += merged
            logger.info("scan_deduped_applications", owner_user_id=owner_user_id, merged=merged)
    except Exception as exc:
        _rollback_after_step_error(session, step="scan_dedupe", exc=exc)
        logger.warning("scan_dedupe_failed", owner_user_id=owner_user_id, error=str(exc))
        summary.errors.append(f"scan_dedupe: {exc}")

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
    owner_user_id: int,
    mailbox_email: str,
    oauth_access_token: str | None = None,
    mailbox_folder: str | None = None,
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

    email_folder = mailbox_folder or config.email_folder

    with GmailClient(config, oauth_access_token=oauth_access_token or "") as gmail:
        message_ids, _ = gmail.fetch_message_ids_by_date_range(since_date, before_date)
        summary.emails_scanned = len(message_ids)

        for idx, gmail_message_id in enumerate(message_ids, start=1):
            # Check for cancellation
            if should_cancel and should_cancel():
                logger.warning("scan_cancelled", processed=idx-1, total=len(message_ids))
                summary.cancelled = True
                summary.emails_scanned = idx - 1
                if progress_callback:
                    progress_callback({
                        "processed": idx - 1,
                        "total": len(message_ids),
                        "current_subject": "",
                        "status": "cancelled",
                    })
                break

            logger.info("processing_email", index=idx, total=len(message_ids), gmail_message_id=gmail_message_id)

            try:
                uid, msg, gmail_thread_id, _, _, label_ids = gmail.fetch_message(gmail_message_id)
                if not is_inbox_message(label_ids):
                    logger.info("email_skipped_social_or_promotions", gmail_message_id=gmail_message_id)
                    summary.skipped_social_or_promotions += 1
                    continue
                if msg is None:
                    summary.skipped_message_unavailable += 1
                    continue
                parsed = parse_email_message(msg, gmail_thread_id=gmail_thread_id)

                # Send progress update before processing
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(message_ids),
                        "current_subject": parsed.subject[:100] if parsed.subject else "",
                        "status": "processing",
                    })

                email_summary = ScanSummary()
                _process_single_email(
                    session,
                    config,
                    llm_provider,
                    owner_user_id,
                    mailbox_email,
                    email_folder,
                    uid,
                    parsed,
                    email_summary,
                    gmail_message_id_override=gmail_message_id,
                )
                session.commit()
                _merge_scan_summary(summary, email_summary)
            except GmailMessageNotFoundError:
                logger.info("email_skipped_message_unavailable", gmail_message_id=gmail_message_id)
                summary.skipped_message_unavailable += 1
            except Exception as exc:
                _rollback_after_email_error(
                    session,
                    gmail_message_id=gmail_message_id,
                    exc=exc,
                )
                error_msg = f"gmail_message_id={gmail_message_id}: {exc}"
                logger.error("email_processing_error", gmail_message_id=gmail_message_id, error=str(exc))
                summary.errors.append(error_msg)
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(message_ids),
                        "current_subject": "",
                        "status": "error",
                    })

        # NOTE: Date-range scans do NOT update last_uid (the incremental scan cursor).
        # This is intentional — scanning a historical date range (e.g. Aug 2025) should
        # not regress the cursor used by "Scan New" for incremental scanning.
        # Only run_scan() and run_incremental_scan() update the cursor.

    try:
        merged = merge_owner_duplicate_applications(
            session,
            owner_user_id,
            session.info.get("journey_id"),
        )
        session.commit()
        if merged > 0:
            summary.applications_deleted += merged
            logger.info("scan_deduped_applications", owner_user_id=owner_user_id, merged=merged)
    except Exception as exc:
        _rollback_after_step_error(session, step="scan_dedupe", exc=exc)
        logger.warning("scan_dedupe_failed", owner_user_id=owner_user_id, error=str(exc))
        summary.errors.append(f"scan_dedupe: {exc}")

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
    owner_user_id: int,
    mailbox_email: str,
    oauth_access_token: str | None = None,
    mailbox_folder: str | None = None,
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

    # Get the last Gmail history cursor
    email_folder = mailbox_folder or config.email_folder
    last_history_id = _get_scan_state(session, owner_user_id, mailbox_email, email_folder)
    logger.info(
        "incremental_scan_starting",
        last_history_id=last_history_id,
        max_scan_emails=config.max_scan_emails,
    )

    # Resolve LLM provider
    llm_provider: Optional[LLMProvider] = None
    if config.llm_enabled:
        try:
            llm_provider = create_llm_provider(config)
            logger.info("llm_provider_ready", provider=config.llm_provider, model=config.llm_model)
        except Exception as exc:
            logger.warning("llm_provider_init_failed", error=str(exc))

    with GmailClient(config, oauth_access_token=oauth_access_token or "") as gmail:
        try:
            message_ids, latest_history_id = gmail.fetch_message_ids_after_history(
                last_history_id,
                max_count=config.max_scan_emails,
            )
        except GmailHistoryExpiredError:
            logger.warning(
                "gmail_history_cursor_expired_fallback_full",
                last_history_id=last_history_id,
            )
            message_ids, latest_history_id = gmail.fetch_latest_message_ids(config.max_scan_emails)

        summary.emails_scanned = len(message_ids)
        
        if not message_ids:
            logger.info("incremental_scan_no_new_emails", last_history_id=last_history_id)
            if latest_history_id > last_history_id:
                try:
                    _update_scan_state(session, owner_user_id, mailbox_email, email_folder, latest_history_id)
                    session.commit()
                except Exception as exc:
                    _rollback_after_step_error(session, step="scan_state_update", exc=exc)
                    error_msg = f"scan_state_update: {exc}"
                    logger.warning("scan_state_update_failed", owner_user_id=owner_user_id, error=str(exc))
                    summary.errors.append(error_msg)
            if progress_callback:
                progress_callback({
                    "processed": 0,
                    "total": 0,
                    "current_subject": "",
                    "status": "completed",
                })
            return summary

        max_history_id = last_history_id
        for idx, gmail_message_id in enumerate(message_ids, start=1):
            if should_cancel and should_cancel():
                logger.warning("scan_cancelled", processed=idx-1, total=len(message_ids))
                summary.cancelled = True
                summary.emails_scanned = idx - 1
                if progress_callback:
                    progress_callback({
                        "processed": idx - 1,
                        "total": len(message_ids),
                        "current_subject": "",
                        "status": "cancelled",
                    })
                break

            logger.info("processing_email", index=idx, total=len(message_ids), gmail_message_id=gmail_message_id)

            try:
                uid, msg, gmail_thread_id, _, history_id, label_ids = gmail.fetch_message(gmail_message_id)
                if not is_inbox_message(label_ids):
                    logger.info("email_skipped_social_or_promotions", gmail_message_id=gmail_message_id)
                    summary.skipped_social_or_promotions += 1
                    max_history_id = max(max_history_id, history_id)
                    continue
                if msg is None:
                    summary.skipped_message_unavailable += 1
                    continue
                parsed = parse_email_message(msg, gmail_thread_id=gmail_thread_id)
                
                # Send progress update before processing
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(message_ids),
                        "current_subject": parsed.subject[:100] if parsed.subject else "",
                        "status": "processing",
                    })

                email_summary = ScanSummary()
                _process_single_email(
                    session,
                    config,
                    llm_provider,
                    owner_user_id,
                    mailbox_email,
                    email_folder,
                    uid,
                    parsed,
                    email_summary,
                    gmail_message_id_override=gmail_message_id,
                )
                session.commit()
                _merge_scan_summary(summary, email_summary)
                max_history_id = max(max_history_id, history_id)
            except GmailMessageNotFoundError:
                logger.info("email_skipped_message_unavailable", gmail_message_id=gmail_message_id)
                summary.skipped_message_unavailable += 1
            except Exception as exc:
                _rollback_after_email_error(
                    session,
                    gmail_message_id=gmail_message_id,
                    exc=exc,
                )
                error_msg = f"gmail_message_id={gmail_message_id}: {exc}"
                logger.error("email_processing_error", gmail_message_id=gmail_message_id, error=str(exc))
                summary.errors.append(error_msg)
                if progress_callback:
                    progress_callback({
                        "processed": idx,
                        "total": len(message_ids),
                        "current_subject": "",
                        "status": "error",
                    })

        # Update scan state with the latest Gmail history cursor.
        cursor = max(max_history_id, latest_history_id)
        if cursor > last_history_id:
            try:
                _update_scan_state(session, owner_user_id, mailbox_email, email_folder, cursor)
                session.commit()
            except Exception as exc:
                _rollback_after_step_error(session, step="scan_state_update", exc=exc)
                error_msg = f"scan_state_update: {exc}"
                logger.warning("scan_state_update_failed", owner_user_id=owner_user_id, error=str(exc))
                summary.errors.append(error_msg)

    try:
        merged = merge_owner_duplicate_applications(
            session,
            owner_user_id,
            session.info.get("journey_id"),
        )
        session.commit()
        if merged > 0:
            summary.applications_deleted += merged
            logger.info("scan_deduped_applications", owner_user_id=owner_user_id, merged=merged)
    except Exception as exc:
        _rollback_after_step_error(session, step="scan_dedupe", exc=exc)
        logger.warning("scan_dedupe_failed", owner_user_id=owner_user_id, error=str(exc))
        summary.errors.append(f"scan_dedupe: {exc}")

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
