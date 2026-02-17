"""Extraction pipeline — orchestrates rule-based and LLM-based extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import structlog
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig
from job_monitor.email.classifier import is_job_related
from job_monitor.email.client import IMAPClient
from job_monitor.email.parser import ParsedEmailData, parse_email_message
from job_monitor.extraction.llm import (
    LLMExtractionResult,
    LLMProvider,
    create_llm_provider,
    extract_with_timeout,
)
from job_monitor.extraction.rules import extract_company, extract_job_title, extract_status

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
    if cleaned.lower() in _INVALID_TITLES:
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
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_estimated_cost: float = 0.0
    errors: list[str] = None  # type: ignore[assignment]
    cancelled: bool = False

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _get_or_create_application(
    session: Session,
    company: str,
    job_title: str,
    email_subject: str,
    email_sender: str,
    email_date: Optional[datetime],
    status: str,
    source: str = "email",
) -> tuple[Application, bool]:
    """Find an existing application or create a new one.

    Returns (application, created) where created=True for new rows.
    Deduplicates by company + job_title (treats empty titles as equivalent).
    """
    # Try to find existing (company + job_title match)
    # Handle NULL/empty job_title: treat all empty titles for same company as one
    if job_title:
        existing = (
            session.query(Application)
            .filter(
                Application.company == company,
                Application.job_title == job_title,
            )
            .first()
        )
    else:
        existing = (
            session.query(Application)
            .filter(
                Application.company == company,
                (Application.job_title == None) | (Application.job_title == ""),  # noqa: E711
            )
            .first()
        )
    if existing:
        return existing, False

    app = Application(
        company=company,
        job_title=job_title,
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
) -> bool:
    """Update application status and record history. Returns True if changed."""
    if not new_status or new_status == app.status:
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
    """Process one parsed email: classify, extract, persist."""
    subject = parsed.subject
    sender = parsed.sender
    body = parsed.body_text
    email_date = parsed.date_dt

    llm_result: Optional[LLMExtractionResult] = None
    llm_used = False

    # ── Step 1: LLM classification + extraction ──────────
    if llm_provider is not None:
        llm_used = True
        try:
            logger.info("llm_extracting", uid=uid)
            llm_result = extract_with_timeout(
                llm_provider, sender, subject, body, timeout_sec=config.llm_timeout_sec
            )
            summary.total_prompt_tokens += llm_result.prompt_tokens
            summary.total_completion_tokens += llm_result.completion_tokens
            summary.total_estimated_cost += llm_result.estimated_cost_usd
        except Exception as exc:
            logger.warning("llm_fallback", uid=uid, error=str(exc))
            llm_result = None

    # ── Step 2: Determine if job-related ──────────────────
    if llm_result is not None:
        if not llm_result.is_job_application:
            logger.info("email_skipped_llm", uid=uid)
            _record_processed(
                session, uid, config, parsed, is_job=False, app_id=None, llm_used=True,
                llm_result=llm_result,
            )
            return
    else:
        if not is_job_related(subject, sender):
            if llm_used:
                logger.info("email_skipped_rules_fallback", uid=uid)
            else:
                logger.info("email_skipped_rules", uid=uid)
            _record_processed(
                session, uid, config, parsed, is_job=False, app_id=None, llm_used=False,
            )
            return

    # ── Step 3: Extract fields ────────────────────────────
    if llm_result is not None and llm_result.is_job_application:
        company = llm_result.company or extract_company(subject, sender)
        job_title = _validate_job_title(llm_result.job_title) or _validate_job_title(extract_job_title(subject, body))
        status = llm_result.status or extract_status(subject, body)
    else:
        company = extract_company(subject, sender)
        job_title = _validate_job_title(extract_job_title(subject, body))
        status = extract_status(subject, body)

    if not company:
        company = "Unknown"

    # ── Step 4: Persist application ───────────────────────
    app, created = _get_or_create_application(
        session,
        company=company,
        job_title=job_title,
        email_subject=subject,
        email_sender=sender,
        email_date=email_date,
        status=status,
    )
    if created:
        summary.applications_created += 1
        logger.info("application_created", uid=uid, company=company, title=job_title)
    else:
        updated = _update_status_if_changed(session, app, status, change_source=f"email_uid_{uid}")
        if updated:
            summary.applications_updated += 1

    summary.emails_matched += 1

    # ── Step 5: Record processed email ────────────────────
    _record_processed(
        session, uid, config, parsed,
        is_job=True, app_id=app.id, llm_used=llm_used, llm_result=llm_result,
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
) -> None:
    """Insert or update a row in processed_emails (supports re-scanning)."""
    existing = (
        session.query(ProcessedEmail)
        .filter(
            ProcessedEmail.uid == uid,
            ProcessedEmail.email_account == config.email_username,
            ProcessedEmail.email_folder == config.email_folder,
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
    else:
        session.add(
            ProcessedEmail(
                uid=uid,
                email_account=config.email_username,
                email_folder=config.email_folder,
                subject=parsed.subject,
                sender=parsed.sender,
                email_date=parsed.date_dt,
                is_job_related=is_job,
                application_id=app_id,
                llm_used=llm_used,
                prompt_tokens=llm_result.prompt_tokens if llm_result else 0,
                completion_tokens=llm_result.completion_tokens if llm_result else 0,
                estimated_cost_usd=llm_result.estimated_cost_usd if llm_result else 0.0,
            )
        )


def run_scan(
    config: AppConfig,
    session: Session,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ScanSummary:
    """Execute a full email scan: fetch the latest N emails, extract, persist.

    Always scans the most recent `max_scan_emails` emails from the inbox.
    Every email is re-analyzed even if previously scanned.
    
    Args:
        config: Application configuration
        session: Database session
        should_cancel: Optional callable that returns True if scan should be cancelled
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

        for idx, uid in enumerate(uids, start=1):
            # Check for cancellation
            if should_cancel and should_cancel():
                logger.warning("scan_cancelled", processed=idx-1, total=len(uids))
                summary.cancelled = True
                summary.emails_scanned = idx - 1
                break

            logger.info("processing_email", index=idx, total=len(uids), uid=uid)

            try:
                _, msg = imap.fetch_message(uid)
                if msg is None:
                    continue
                parsed = parse_email_message(msg)
                _process_single_email(session, config, llm_provider, uid, parsed, summary)
            except Exception as exc:
                error_msg = f"uid={uid}: {exc}"
                logger.error("email_processing_error", uid=uid, error=str(exc))
                summary.errors.append(error_msg)

    session.commit()

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
