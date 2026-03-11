"""Helpers for database-backed scan jobs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig
from job_monitor.dedupe import merge_owner_duplicate_applications
from job_monitor.email.gmail_client import (
    GmailClient,
    GmailHistoryExpiredError,
    GmailMessageNotFoundError,
    is_inbox_message,
)
from job_monitor.email.parser import parse_email_message
from job_monitor.extraction.llm import LLMProvider, create_llm_provider
from job_monitor.extraction.pipeline import (
    ScanSummary,
    _get_scan_state,
    _process_single_email,
    _rollback_after_email_error,
    _rollback_after_step_error,
    _update_scan_state,
)
from job_monitor.models import ScanJob, ScanJobMessage

logger = structlog.get_logger(__name__)

ACTIVE_SCAN_JOB_STATUSES = ("queued", "running", "cancel_requested")
TERMINAL_SCAN_JOB_STATUSES = ("cancelled", "completed", "failed")
SCAN_JOB_MODES = ("incremental", "full", "date_range")
DEFAULT_REQUESTED_MAX_EMAILS = 50
MAX_REQUESTED_MAX_EMAILS = 500
DEFAULT_BATCH_SIZE = 5
MAX_BATCH_SIZE = 10
STALE_PROCESSING_TIMEOUT = timedelta(minutes=10)
BACKGROUND_DISPATCH_TIMEOUT = httpx.Timeout(connect=5.0, read=0.2, write=5.0, pool=5.0)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mark_claimed_message_skipped(
    session: Session,
    job: ScanJob,
    claimed_message_id: int,
    summary: ScanSummary,
) -> None:
    claimed_row = session.get(ScanJobMessage, claimed_message_id)
    if claimed_row is None:
        raise RuntimeError("Claimed scan job message disappeared")
    claimed_row.status = "skipped"
    claimed_row.processed_at = _utcnow()
    claimed_row.error_message = None
    _apply_summary_to_job(job, summary, current_subject="")
    session.commit()


def clamp_requested_max_emails(requested_max_emails: int | None) -> int:
    value = requested_max_emails or DEFAULT_REQUESTED_MAX_EMAILS
    return max(1, min(int(value), MAX_REQUESTED_MAX_EMAILS))


def get_effective_batch_size(config: AppConfig) -> int:
    value = int(getattr(config, "scan_job_batch_size", DEFAULT_BATCH_SIZE) or DEFAULT_BATCH_SIZE)
    return max(1, min(value, MAX_BATCH_SIZE))


def normalize_requested_max_emails_for_mode(
    mode: str,
    requested_max_emails: int | None,
) -> int:
    if requested_max_emails is None:
        return DEFAULT_REQUESTED_MAX_EMAILS if mode == "incremental" else 0
    if requested_max_emails <= 0:
        return 0 if mode in {"full", "date_range"} else DEFAULT_REQUESTED_MAX_EMAILS
    return clamp_requested_max_emails(requested_max_emails)


def _query_limit_for_job(job: ScanJob) -> int | None:
    return None if job.requested_max_emails <= 0 else int(job.requested_max_emails)


def _backend_base_url_from_config(config: AppConfig) -> str | None:
    redirect_uri = (config.google_redirect_uri or "").strip()
    if not redirect_uri:
        return None
    parsed = urlsplit(redirect_uri)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _scan_worker_secret_from_config(config: AppConfig) -> str:
    explicit = config.scan_job_continue_secret.get_secret_value().strip()
    if explicit:
        return explicit
    return config.token_encryption_key.get_secret_value().strip()


def dispatch_scan_job_continuation(config: AppConfig, job_id: int) -> bool:
    if not config.scan_job_continue_enabled:
        return False

    base_url = _backend_base_url_from_config(config)
    worker_secret = _scan_worker_secret_from_config(config)
    if not base_url or not worker_secret:
        logger.warning("scan_job_background_dispatch_skipped", job_id=job_id)
        return False

    url = f"{base_url}/api/scan/jobs/{job_id}/process"
    try:
        with httpx.Client(timeout=BACKGROUND_DISPATCH_TIMEOUT) as client:
            client.post(
                url,
                headers={
                    "Authorization": f"Bearer {worker_secret}",
                    "X-Scan-Worker": "1",
                },
            )
        logger.info("scan_job_background_dispatch_completed", job_id=job_id)
        return True
    except httpx.ReadTimeout:
        logger.info("scan_job_background_dispatch_accepted", job_id=job_id)
        return True
    except Exception as exc:
        logger.warning(
            "scan_job_background_dispatch_failed",
            job_id=job_id,
            error=str(exc),
        )
        return False


def _json_loads_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return decoded if isinstance(decoded, list) else []


def _json_loads_dict(value: str | None) -> dict[str, int]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    cleaned: dict[str, int] = {}
    for key, raw_value in decoded.items():
        try:
            cleaned[str(key)] = int(raw_value)
        except (TypeError, ValueError):
            continue
    return cleaned


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _append_unique_int(bucket: list[int], value: int) -> None:
    if value not in bucket:
        bucket.append(value)


def _ensure_scope(session: Session, owner_user_id: int, journey_id: int) -> None:
    session.info["owner_user_id"] = owner_user_id
    session.info["journey_id"] = journey_id


def serialize_scan_job(job: ScanJob) -> dict[str, Any]:
    created_application_ids = [
        int(value) for value in _json_loads_list(job.created_application_ids_json)
    ]
    updated_application_ids = [
        int(value) for value in _json_loads_list(job.updated_application_ids_json)
    ]
    return {
        "id": job.id,
        "status": job.status,
        "mode": job.mode,
        "requested_max_emails": job.requested_max_emails,
        "since_date": job.since_date,
        "before_date": job.before_date,
        "history_fallback_used": job.history_fallback_used,
        "total_messages": job.total_messages,
        "processed_messages": job.processed_messages,
        "current_subject": job.current_subject,
        "emails_matched": job.emails_matched,
        "skipped_social_or_promotions": job.skipped_social_or_promotions,
        "skipped_not_job_related": job.skipped_not_job_related,
        "skipped_message_unavailable": job.skipped_message_unavailable,
        "applications_created": job.applications_created,
        "applications_updated": job.applications_updated,
        "applications_deleted": job.applications_deleted,
        "total_prompt_tokens": job.total_prompt_tokens,
        "total_completion_tokens": job.total_completion_tokens,
        "total_estimated_cost": job.total_estimated_cost,
        "errors": _json_loads_list(job.errors_json),
        "non_job_reason_counts": _json_loads_dict(job.non_job_reason_counts_json),
        "created_application_ids": created_application_ids,
        "updated_application_ids": updated_application_ids,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }


def scan_job_to_scan_result(job: ScanJob) -> dict[str, Any]:
    payload = serialize_scan_job(job)
    return {
        "emails_scanned": job.processed_messages,
        "emails_matched": job.emails_matched,
        "skipped_social_or_promotions": job.skipped_social_or_promotions,
        "skipped_not_job_related": job.skipped_not_job_related,
        "skipped_message_unavailable": job.skipped_message_unavailable,
        "non_job_reason_counts": payload["non_job_reason_counts"],
        "applications_created": job.applications_created,
        "applications_updated": job.applications_updated,
        "applications_deleted": job.applications_deleted,
        "created_application_ids": payload["created_application_ids"],
        "updated_application_ids": payload["updated_application_ids"],
        "total_prompt_tokens": job.total_prompt_tokens,
        "total_completion_tokens": job.total_completion_tokens,
        "total_estimated_cost": job.total_estimated_cost,
        "errors": payload["errors"],
        "cancelled": job.status == "cancelled",
    }


def get_active_scan_job(session: Session, owner_user_id: int, journey_id: int) -> ScanJob | None:
    _ensure_scope(session, owner_user_id, journey_id)
    return (
        session.query(ScanJob)
        .filter(
            ScanJob.owner_user_id == owner_user_id,
            ScanJob.journey_id == journey_id,
            ScanJob.status.in_(ACTIVE_SCAN_JOB_STATUSES),
        )
        .order_by(ScanJob.created_at.desc())
        .first()
    )


def get_latest_terminal_scan_job(
    session: Session,
    owner_user_id: int,
    journey_id: int,
) -> ScanJob | None:
    _ensure_scope(session, owner_user_id, journey_id)
    return (
        session.query(ScanJob)
        .filter(
            ScanJob.owner_user_id == owner_user_id,
            ScanJob.journey_id == journey_id,
            ScanJob.status.in_(TERMINAL_SCAN_JOB_STATUSES),
        )
        .order_by(ScanJob.completed_at.desc(), ScanJob.created_at.desc())
        .first()
    )


def get_scan_job(
    session: Session,
    owner_user_id: int,
    journey_id: int,
    job_id: int,
) -> ScanJob | None:
    _ensure_scope(session, owner_user_id, journey_id)
    return (
        session.query(ScanJob)
        .filter(
            ScanJob.id == job_id,
            ScanJob.owner_user_id == owner_user_id,
            ScanJob.journey_id == journey_id,
        )
        .first()
    )


def create_scan_job(
    session: Session,
    config: AppConfig,
    owner_user_id: int,
    journey_id: int,
    mailbox_email: str,
    oauth_access_token: str,
    mode: str = "incremental",
    requested_max_emails: int | None = None,
    since_date: str | None = None,
    before_date: str | None = None,
) -> tuple[ScanJob, bool]:
    _ensure_scope(session, owner_user_id, journey_id)

    existing = get_active_scan_job(session, owner_user_id, journey_id)
    if existing is not None:
        return existing, True

    if mode not in SCAN_JOB_MODES:
        raise ValueError(f"Unsupported scan mode: {mode}")

    max_emails = normalize_requested_max_emails_for_mode(mode, requested_max_emails)
    max_count = None if max_emails <= 0 else max_emails
    batch_size = get_effective_batch_size(config)
    email_folder = config.email_folder
    last_history_id = _get_scan_state(session, owner_user_id, mailbox_email, email_folder)
    history_fallback_used = False

    with GmailClient(config, oauth_access_token=oauth_access_token) as gmail:
        if mode == "incremental":
            try:
                message_ids, latest_history_id = gmail.fetch_message_ids_after_history(
                    last_history_id,
                    max_count=max_count,
                )
            except GmailHistoryExpiredError:
                logger.warning(
                    "scan_job_history_cursor_expired_fallback_full",
                    owner_user_id=owner_user_id,
                    journey_id=journey_id,
                    last_history_id=last_history_id,
                )
                history_fallback_used = True
                message_ids, latest_history_id = gmail.fetch_latest_message_ids(
                    max_count or DEFAULT_REQUESTED_MAX_EMAILS
                )
        elif mode == "full":
            message_ids, latest_history_id = gmail.fetch_trackable_message_ids(max_count=max_count)
        else:
            message_ids, latest_history_id = gmail.fetch_message_ids_by_date_range(
                since_date=since_date,
                before_date=before_date,
                max_count=max_count,
            )

    now = _utcnow()
    job = ScanJob(
        owner_user_id=owner_user_id,
        journey_id=journey_id,
        status="queued",
        mode=mode,
        mailbox_email=mailbox_email,
        requested_max_emails=max_emails,
        since_date=since_date,
        before_date=before_date,
        batch_size=batch_size,
        history_start_id=last_history_id,
        history_latest_id=latest_history_id,
        history_fallback_used=history_fallback_used,
        total_messages=len(message_ids),
        processed_messages=0,
        current_subject=None,
        errors_json="[]",
        non_job_reason_counts_json="{}",
        created_application_ids_json="[]",
        updated_application_ids_json="[]",
    )
    session.add(job)
    session.flush()

    for position, gmail_message_id in enumerate(message_ids, start=1):
        session.add(
            ScanJobMessage(
                scan_job_id=job.id,
                position=position,
                gmail_message_id=gmail_message_id,
                status="pending",
            )
        )

    if not message_ids:
        job.status = "completed"
        job.started_at = now
        job.completed_at = now
        if latest_history_id > last_history_id:
            if mode in {"incremental", "full"}:
                _update_scan_state(
                    session,
                    owner_user_id,
                    mailbox_email,
                    email_folder,
                    latest_history_id,
                )

    session.commit()
    session.refresh(job)
    return job, False


def create_incremental_scan_job(
    session: Session,
    config: AppConfig,
    owner_user_id: int,
    journey_id: int,
    mailbox_email: str,
    oauth_access_token: str,
    requested_max_emails: int | None = None,
) -> tuple[ScanJob, bool]:
    return create_scan_job(
        session,
        config,
        owner_user_id,
        journey_id,
        mailbox_email,
        oauth_access_token,
        mode="incremental",
        requested_max_emails=requested_max_emails,
    )


def request_scan_job_cancel(
    session: Session,
    owner_user_id: int,
    journey_id: int,
    job_id: int,
) -> ScanJob | None:
    job = get_scan_job(session, owner_user_id, journey_id, job_id)
    if job is None:
        return None
    if job.status in TERMINAL_SCAN_JOB_STATUSES:
        return job
    if job.status != "cancel_requested":
        job.status = "cancel_requested"
        job.cancel_requested_at = _utcnow()
        session.commit()
        session.refresh(job)
    return job


def _append_job_error(job: ScanJob, message: str) -> None:
    errors = _json_loads_list(job.errors_json)
    errors.append(message)
    job.errors_json = _json_dumps(errors)
    job.last_error = message


def _merge_reason_counts(job: ScanJob, summary: ScanSummary) -> None:
    if not summary.non_job_reason_counts:
        return
    current = _json_loads_dict(job.non_job_reason_counts_json)
    for key, value in summary.non_job_reason_counts.items():
        current[key] = current.get(key, 0) + value
    job.non_job_reason_counts_json = _json_dumps(current)


def _merge_application_ids(job: ScanJob, summary: ScanSummary) -> None:
    created_ids = [int(v) for v in _json_loads_list(job.created_application_ids_json)]
    updated_ids = [int(v) for v in _json_loads_list(job.updated_application_ids_json)]
    for application_id in summary.created_application_ids:
        _append_unique_int(created_ids, application_id)
    for application_id in summary.updated_application_ids:
        _append_unique_int(updated_ids, application_id)
    job.created_application_ids_json = _json_dumps(created_ids)
    job.updated_application_ids_json = _json_dumps(updated_ids)


def _apply_summary_to_job(
    job: ScanJob,
    summary: ScanSummary,
    *,
    current_subject: str,
) -> None:
    job.processed_messages += 1
    job.current_subject = current_subject
    job.emails_matched += summary.emails_matched
    job.skipped_social_or_promotions += summary.skipped_social_or_promotions
    job.skipped_not_job_related += summary.skipped_not_job_related
    job.skipped_message_unavailable += summary.skipped_message_unavailable
    job.applications_created += summary.applications_created
    job.applications_updated += summary.applications_updated
    job.applications_deleted += summary.applications_deleted
    job.total_prompt_tokens += summary.total_prompt_tokens
    job.total_completion_tokens += summary.total_completion_tokens
    job.total_estimated_cost += summary.total_estimated_cost
    _merge_reason_counts(job, summary)
    _merge_application_ids(job, summary)
    if summary.errors:
        for message in summary.errors:
            _append_job_error(job, message)


def _release_stale_processing_messages(session: Session, job_id: int) -> None:
    stale_before = _utcnow() - STALE_PROCESSING_TIMEOUT
    (
        session.query(ScanJobMessage)
        .filter(
            ScanJobMessage.scan_job_id == job_id,
            ScanJobMessage.status == "processing",
            ScanJobMessage.claimed_at.is_not(None),
            ScanJobMessage.claimed_at < stale_before,
        )
        .update(
            {
                ScanJobMessage.status: "pending",
                ScanJobMessage.claimed_at: None,
            },
            synchronize_session=False,
        )
    )


def _claim_job_processing(session: Session, job_id: int) -> bool:
    claim_time = _utcnow()
    stale_before = claim_time - STALE_PROCESSING_TIMEOUT
    updated = (
        session.query(ScanJob)
        .filter(
            ScanJob.id == job_id,
            (
                ScanJob.processing_started_at.is_(None)
                | (ScanJob.processing_started_at < stale_before)
            ),
        )
        .update(
            {ScanJob.processing_started_at: claim_time},
            synchronize_session=False,
        )
    )
    session.commit()
    return bool(updated)


def _release_job_processing(session: Session, job_id: int) -> None:
    (
        session.query(ScanJob)
        .filter(ScanJob.id == job_id)
        .update({ScanJob.processing_started_at: None}, synchronize_session=False)
    )
    session.commit()


def _reset_messages_to_pending(session: Session, message_ids: list[int]) -> None:
    if not message_ids:
        return
    (
        session.query(ScanJobMessage)
        .filter(ScanJobMessage.id.in_(message_ids))
        .update(
            {
                ScanJobMessage.status: "pending",
                ScanJobMessage.claimed_at: None,
            },
            synchronize_session=False,
        )
    )


def _claim_pending_messages(session: Session, job_id: int, batch_size: int) -> list[ScanJobMessage]:
    candidate_ids = [
        row[0]
        for row in (
            session.query(ScanJobMessage.id)
            .filter(
                ScanJobMessage.scan_job_id == job_id,
                ScanJobMessage.status == "pending",
            )
            .order_by(ScanJobMessage.position.asc())
            .limit(batch_size * 4)
            .all()
        )
    ]

    if not candidate_ids:
        return []

    claimed_ids: list[int] = []
    claim_time = _utcnow()
    for message_id in candidate_ids:
        updated = (
            session.query(ScanJobMessage)
            .filter(
                ScanJobMessage.id == message_id,
                ScanJobMessage.status == "pending",
            )
            .update(
                {
                    ScanJobMessage.status: "processing",
                    ScanJobMessage.claimed_at: claim_time,
                    ScanJobMessage.error_message: None,
                },
                synchronize_session=False,
            )
        )
        if updated:
            claimed_ids.append(message_id)
            if len(claimed_ids) >= batch_size:
                break

    session.commit()

    if not claimed_ids:
        return []

    return (
        session.query(ScanJobMessage)
        .filter(ScanJobMessage.id.in_(claimed_ids))
        .order_by(ScanJobMessage.position.asc())
        .all()
    )


def _has_unfinished_messages(session: Session, job_id: int) -> bool:
    return (
        session.query(ScanJobMessage)
        .filter(
            ScanJobMessage.scan_job_id == job_id,
            ScanJobMessage.status.in_(("pending", "processing")),
        )
        .first()
        is not None
    )


def _build_llm_provider(config: AppConfig) -> LLMProvider | None:
    if not config.llm_enabled:
        return None
    try:
        provider = create_llm_provider(config)
        logger.info(
            "scan_job_llm_provider_ready",
            provider=config.llm_provider,
            model=config.llm_model,
        )
        return provider
    except Exception as exc:
        logger.warning("scan_job_llm_provider_init_failed", error=str(exc))
        return None


def _finalize_scan_job(session: Session, config: AppConfig, job_id: int) -> ScanJob:
    job = session.get(ScanJob, job_id)
    if job is None:
        raise RuntimeError(f"Scan job {job_id} not found")

    try:
        merged = merge_owner_duplicate_applications(session, job.owner_user_id, job.journey_id)
        if merged > 0:
            job.applications_deleted += merged

        if job.mode in {"incremental", "full"} and job.history_latest_id > job.history_start_id:
            _update_scan_state(
                session,
                job.owner_user_id,
                job.mailbox_email,
                config.email_folder,
                job.history_latest_id,
            )

        job.status = "completed"
        job.current_subject = None
        job.processing_started_at = None
        job.completed_at = _utcnow()
        session.commit()
    except Exception as exc:
        _rollback_after_step_error(session, step="scan_job_finalize", exc=exc)
        job = session.get(ScanJob, job_id)
        if job is None:
            raise
        job.status = "failed"
        job.processing_started_at = None
        job.completed_at = _utcnow()
        _append_job_error(job, f"scan_job_finalize: {exc}")
        session.commit()

    refreshed = session.get(ScanJob, job_id)
    if refreshed is None:
        raise RuntimeError(f"Scan job {job_id} disappeared")
    return refreshed


def run_scan_job_step(
    session: Session,
    config: AppConfig,
    owner_user_id: int,
    journey_id: int,
    job_id: int,
    oauth_access_token: str,
) -> tuple[ScanJob, int, bool]:
    _ensure_scope(session, owner_user_id, journey_id)
    job = get_scan_job(session, owner_user_id, journey_id, job_id)
    if job is None:
        raise RuntimeError("Scan job not found")

    if job.status in TERMINAL_SCAN_JOB_STATUSES:
        return job, 0, True

    if job.status == "cancel_requested":
        job.status = "cancelled"
        job.current_subject = None
        job.processing_started_at = None
        job.completed_at = _utcnow()
        session.commit()
        session.refresh(job)
        return job, 0, True

    if not _claim_job_processing(session, job.id):
        job = session.get(ScanJob, job.id)
        if job is None:
            raise RuntimeError("Scan job disappeared before processing")
        return job, 0, False

    if job.status == "queued":
        job.status = "running"
        if job.started_at is None:
            job.started_at = _utcnow()
        session.commit()
        session.refresh(job)

    try:
        _release_stale_processing_messages(session, job.id)
        session.commit()

        claimed_messages = _claim_pending_messages(session, job.id, job.batch_size)
        if not claimed_messages:
            if _has_unfinished_messages(session, job.id):
                job = session.get(ScanJob, job.id)
                if job is None:
                    raise RuntimeError("Scan job disappeared during step")
                return job, 0, False
            job = _finalize_scan_job(session, config, job.id)
            return job, 0, True

        llm_provider = _build_llm_provider(config)
        processed_in_step = 0
        remaining_message_ids = [message.id for message in claimed_messages]

        with GmailClient(config, oauth_access_token=oauth_access_token) as gmail:
            for message in claimed_messages:
                job = session.get(ScanJob, job.id)
                if job is None:
                    raise RuntimeError("Scan job disappeared during processing")

                if job.status == "cancel_requested":
                    _reset_messages_to_pending(session, remaining_message_ids)
                    session.commit()
                    break

                remaining_message_ids.pop(0)
                history_id = 0
                try:
                    uid, raw_message, gmail_thread_id, _, history_id, label_ids = gmail.fetch_message(
                        message.gmail_message_id
                    )
                    if history_id > job.history_latest_id:
                        job.history_latest_id = history_id

                    if not is_inbox_message(label_ids):
                        _mark_claimed_message_skipped(
                            session,
                            job,
                            message.id,
                            ScanSummary(skipped_social_or_promotions=1),
                        )
                        processed_in_step += 1
                        continue

                    if raw_message is None:
                        _mark_claimed_message_skipped(
                            session,
                            job,
                            message.id,
                            ScanSummary(skipped_message_unavailable=1),
                        )
                        processed_in_step += 1
                        continue

                    parsed = parse_email_message(raw_message, gmail_thread_id=gmail_thread_id)
                    summary = ScanSummary()
                    _process_single_email(
                        session,
                        config,
                        llm_provider,
                        owner_user_id,
                        job.mailbox_email,
                        config.email_folder,
                        uid,
                        parsed,
                        summary,
                        gmail_message_id_override=message.gmail_message_id,
                    )

                    claimed_row = session.get(ScanJobMessage, message.id)
                    if claimed_row is None:
                        raise RuntimeError("Claimed scan job message disappeared")
                    claimed_row.status = "done" if summary.emails_matched > 0 else "skipped"
                    claimed_row.processed_at = _utcnow()
                    claimed_row.error_message = None
                    _apply_summary_to_job(
                        job,
                        summary,
                        current_subject=(parsed.subject[:100] if parsed.subject else ""),
                    )
                    session.commit()
                    processed_in_step += 1
                except GmailMessageNotFoundError:
                    logger.info(
                        "scan_job_message_unavailable",
                        scan_job_id=job.id,
                        gmail_message_id=message.gmail_message_id,
                    )
                    _mark_claimed_message_skipped(
                        session,
                        job,
                        message.id,
                        ScanSummary(skipped_message_unavailable=1),
                    )
                    processed_in_step += 1
                except Exception as exc:
                    _rollback_after_email_error(
                        session,
                        gmail_message_id=message.gmail_message_id,
                        exc=exc,
                    )
                    claimed_row = session.get(ScanJobMessage, message.id)
                    job = session.get(ScanJob, job.id)
                    if claimed_row is None or job is None:
                        raise RuntimeError("Scan job state disappeared after rollback") from exc
                    claimed_row.status = "error"
                    claimed_row.processed_at = _utcnow()
                    claimed_row.error_message = str(exc)
                    if history_id > job.history_latest_id:
                        job.history_latest_id = history_id
                    _apply_summary_to_job(
                        job,
                        ScanSummary(errors=[f"gmail_message_id={message.gmail_message_id}: {exc}"]),
                        current_subject="",
                    )
                    session.commit()
                    processed_in_step += 1

        job = session.get(ScanJob, job.id)
        if job is None:
            raise RuntimeError("Scan job disappeared after processing")

        if job.status == "cancel_requested":
            if not _has_unfinished_messages(session, job.id):
                job.status = "cancelled"
                job.current_subject = None
                job.processing_started_at = None
                job.completed_at = _utcnow()
                session.commit()
                session.refresh(job)
                return job, processed_in_step, True
            session.refresh(job)
            return job, processed_in_step, False

        if _has_unfinished_messages(session, job.id):
            session.refresh(job)
            return job, processed_in_step, False

        job = _finalize_scan_job(session, config, job.id)
        return job, processed_in_step, True
    finally:
        job_after = session.get(ScanJob, job.id)
        if job_after is not None and job_after.status not in TERMINAL_SCAN_JOB_STATUSES:
            _release_job_processing(session, job.id)


def process_scan_job(
    session: Session,
    config: AppConfig,
    job_id: int,
    oauth_access_token: str,
) -> tuple[ScanJob, int, bool]:
    job = session.get(ScanJob, job_id)
    if job is None:
        raise RuntimeError("Scan job not found")

    processed_job, processed_in_step, done = run_scan_job_step(
        session,
        config,
        owner_user_id=job.owner_user_id,
        journey_id=job.journey_id,
        job_id=job.id,
        oauth_access_token=oauth_access_token,
    )
    if (
        not done
        and processed_in_step > 0
        and processed_job.status in ACTIVE_SCAN_JOB_STATUSES
    ):
        dispatch_scan_job_continuation(config, processed_job.id)
    return processed_job, processed_in_step, done
