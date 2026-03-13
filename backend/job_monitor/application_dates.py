"""Helpers for tracking the first known application submission time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from job_monitor.models import Application, ProcessedEmail, StatusHistory

_PRE_APPLICATION_STATUSES = {"Recruiter Reach-out", "Unknown"}


def status_implies_application(status: str | None) -> bool:
    normalized = (status or "").strip()
    return bool(normalized) and normalized not in _PRE_APPLICATION_STATUSES


def earliest_known_datetime(*values: datetime | None) -> datetime | None:
    candidates = [value for value in values if value is not None]
    if not candidates:
        return None
    return min(candidates)


def assign_applied_at_if_missing(
    app: Application,
    *,
    status: str | None,
    preferred_at: datetime | None = None,
    fallback_at: datetime | None = None,
) -> bool:
    if app.applied_at is not None or not status_implies_application(status):
        return False

    candidate = preferred_at or fallback_at
    if candidate is None:
        return False

    app.applied_at = candidate
    return True


def merge_applied_at(target: Application, source: Application) -> bool:
    merged_value = earliest_known_datetime(target.applied_at, source.applied_at)
    if merged_value == target.applied_at:
        return False
    target.applied_at = merged_value
    return True


def infer_applied_at(session: Session, app: Application) -> datetime | None:
    status_rows = (
        session.query(StatusHistory.new_status, StatusHistory.changed_at)
        .filter(StatusHistory.application_id == app.id)
        .order_by(StatusHistory.changed_at.asc(), StatusHistory.id.asc())
        .all()
    )
    has_application_signal = status_implies_application(app.status) or any(
        status_implies_application(status) for status, _ in status_rows
    )
    if not has_application_signal:
        return None

    first_applied_change = next(
        (changed_at for status, changed_at in status_rows if status_implies_application(status)),
        None,
    )
    if first_applied_change is not None:
        threshold = first_applied_change - timedelta(seconds=1)
        triggered_email = (
            session.query(ProcessedEmail.email_date)
            .filter(
                ProcessedEmail.application_id == app.id,
                ProcessedEmail.is_job_related == True,  # noqa: E712
                ProcessedEmail.email_date.isnot(None),
                ProcessedEmail.processed_at >= threshold,
            )
            .order_by(ProcessedEmail.processed_at.asc(), ProcessedEmail.email_date.asc(), ProcessedEmail.id.asc())
            .first()
        )
        if triggered_email and triggered_email[0] is not None:
            return triggered_email[0]

    earliest_job_email = (
        session.query(ProcessedEmail.email_date)
        .filter(
            ProcessedEmail.application_id == app.id,
            ProcessedEmail.is_job_related == True,  # noqa: E712
            ProcessedEmail.email_date.isnot(None),
        )
        .order_by(ProcessedEmail.email_date.asc(), ProcessedEmail.id.asc())
        .first()
    )
    if earliest_job_email and earliest_job_email[0] is not None:
        return earliest_job_email[0]

    if app.email_date is not None and status_implies_application(app.status):
        return app.email_date

    return app.created_at or datetime.now(timezone.utc)


def refresh_applied_at(
    session: Session,
    app: Application,
    *,
    preserve_existing: bool = True,
) -> bool:
    if preserve_existing and app.applied_at is not None:
        return False

    inferred = infer_applied_at(session, app)
    if inferred == app.applied_at:
        return False

    app.applied_at = inferred
    return True
