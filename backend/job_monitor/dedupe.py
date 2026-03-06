"""Helpers for merging duplicate applications belonging to the same owner."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime

import structlog
from sqlalchemy import func
from sqlalchemy.orm import Session

from job_monitor.extraction.rules import normalize_req_id, split_title_and_req_id
from job_monitor.linking.resolver import normalize_company
from job_monitor.models import (
    Application,
    ApplicationMergeEvent,
    ApplicationMergeItem,
    ProcessedEmail,
    StatusHistory,
)

logger = structlog.get_logger(__name__)

_INVISIBLE_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_DASH_TRANSLATION = str.maketrans({
    "–": "-",
    "—": "-",
    "−": "-",
    "‐": "-",
})


def _to_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


def _clean_text(value: str | None) -> str:
    """Normalize text so invisible chars and dash variants don't split dedup keys."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = _INVISIBLE_RE.sub("", text)
    text = text.translate(_DASH_TRANSLATION)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _canonical_company(company: str | None, normalized_company: str | None) -> str:
    for raw in (normalized_company, company):
        cleaned = _clean_text(raw)
        if not cleaned:
            continue
        norm = normalize_company(cleaned)
        if norm:
            return norm
    return ""


def _canonical_title_and_req(job_title: str | None, req_id: str | None) -> tuple[str, str]:
    cleaned_title = _clean_text(job_title)
    base_title, req_from_title = split_title_and_req_id(cleaned_title)
    title_key = _clean_text(base_title).lower()
    req_key = normalize_req_id((req_id or req_from_title or "").strip())
    return title_key, req_key


def _is_newer_email(candidate: Application, current: Application) -> bool:
    candidate_dt = _to_naive(candidate.email_date)
    current_dt = _to_naive(current.email_date)
    if candidate_dt and current_dt:
        return candidate_dt > current_dt
    if candidate_dt and not current_dt:
        return True
    if not candidate_dt and current_dt:
        return False
    return candidate.id > current.id


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _serialize_application_snapshot(app: Application) -> dict[str, str | None]:
    return {
        "company": app.company,
        "normalized_company": app.normalized_company,
        "job_title": app.job_title,
        "req_id": app.req_id,
        "email_subject": app.email_subject,
        "email_sender": app.email_sender,
        "email_date": _serialize_datetime(app.email_date),
        "status": app.status,
        "source": app.source,
        "notes": app.notes,
        "created_at": _serialize_datetime(app.created_at),
        "updated_at": _serialize_datetime(app.updated_at),
    }


def merge_owner_duplicate_applications(
    session: Session,
    owner_user_id: int,
    journey_id: int | None = None,
) -> int:
    """Merge duplicate Application rows for one owner (optionally within one journey)."""
    app_query = session.query(Application).filter(Application.owner_user_id == owner_user_id)
    if journey_id is not None:
        app_query = app_query.filter(Application.journey_id == journey_id)
    apps = app_query.all()

    # Backfill req_id from job_title for legacy rows (e.g. "Role - R123456 -").
    backfilled = 0
    for app in apps:
        base_title, req_from_title = split_title_and_req_id(app.job_title or "")
        normalized_req = normalize_req_id((app.req_id or req_from_title or "").strip())
        if normalized_req and app.req_id != normalized_req:
            app.req_id = normalized_req
            backfilled += 1
        if req_from_title:
            cleaned_base = (base_title or "").strip()
            if cleaned_base and app.job_title != cleaned_base:
                app.job_title = cleaned_base
                backfilled += 1

    if backfilled > 0:
        logger.info(
            "application_req_id_backfilled",
            owner_user_id=owner_user_id,
            updated_rows=backfilled,
        )

    if len(apps) < 2:
        if backfilled > 0:
            session.flush()
        return 0

    unlocked_apps = [app for app in apps if not app.dedupe_locked]
    if len(unlocked_apps) < 2:
        if backfilled > 0:
            session.flush()
        return 0

    counts = (
        session.query(ProcessedEmail.application_id, func.count(ProcessedEmail.id))
        .filter(
            ProcessedEmail.owner_user_id == owner_user_id,
            ProcessedEmail.application_id.isnot(None),
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
    )
    if journey_id is not None:
        counts = counts.filter(ProcessedEmail.journey_id == journey_id)
    counts = counts.group_by(ProcessedEmail.application_id).all()
    email_counts = {int(app_id): int(cnt) for app_id, cnt in counts if app_id is not None}

    grouped: dict[tuple[str, str, str], list[Application]] = defaultdict(list)
    for app in unlocked_apps:
        company_key = _canonical_company(app.company, app.normalized_company)
        if not company_key:
            continue
        title_key, req_key = _canonical_title_and_req(app.job_title, app.req_id)
        grouped[(company_key, title_key, req_key)].append(app)

    merged = 0
    for key, candidates in grouped.items():
        if len(candidates) < 2:
            continue

        keep = max(
            candidates,
            key=lambda app: (
                email_counts.get(app.id, 0),
                _to_naive(app.email_date) or datetime.min,
                _to_naive(app.updated_at) or datetime.min,
                app.id,
            ),
        )

        for duplicate in candidates:
            if duplicate.id == keep.id:
                continue

            source_email_query = session.query(ProcessedEmail.id).filter(
                ProcessedEmail.application_id == duplicate.id,
            )
            if journey_id is not None:
                source_email_query = source_email_query.filter(ProcessedEmail.journey_id == journey_id)
            source_email_ids = [row_id for row_id, in source_email_query.all()]

            source_history_query = session.query(StatusHistory.id).filter(
                StatusHistory.application_id == duplicate.id,
            )
            if journey_id is not None:
                source_history_query = source_history_query.filter(StatusHistory.journey_id == journey_id)
            source_history_ids = [row_id for row_id, in source_history_query.all()]

            merge_event = ApplicationMergeEvent(
                owner_user_id=owner_user_id,
                journey_id=journey_id,
                target_application_id=keep.id,
                source_application_id=duplicate.id,
                source_company=duplicate.company,
                source_job_title=duplicate.job_title,
                source_req_id=duplicate.req_id,
                source_status=duplicate.status,
                source_snapshot_json=json.dumps(_serialize_application_snapshot(duplicate), ensure_ascii=False),
                merge_source="system_dedupe",
                moved_email_count=len(source_email_ids),
                moved_history_count=len(source_history_ids),
            )
            session.add(merge_event)
            session.flush()

            if source_email_ids:
                session.add_all(
                    [
                        ApplicationMergeItem(
                            owner_user_id=owner_user_id,
                            journey_id=journey_id,
                            merge_event_id=merge_event.id,
                            item_type="processed_email",
                            item_id=item_id,
                        )
                        for item_id in source_email_ids
                    ]
                )
            if source_history_ids:
                session.add_all(
                    [
                        ApplicationMergeItem(
                            owner_user_id=owner_user_id,
                            journey_id=journey_id,
                            merge_event_id=merge_event.id,
                            item_type="status_history",
                            item_id=item_id,
                        )
                        for item_id in source_history_ids
                    ]
                )

            if _is_newer_email(duplicate, keep):
                keep.email_date = duplicate.email_date
                keep.email_subject = duplicate.email_subject or keep.email_subject
                keep.email_sender = duplicate.email_sender or keep.email_sender
                keep.status = duplicate.status or keep.status

            if (not keep.job_title) and duplicate.job_title:
                keep.job_title = duplicate.job_title
            if (not keep.req_id) and duplicate.req_id:
                keep.req_id = duplicate.req_id
            if (not keep.normalized_company) and duplicate.normalized_company:
                keep.normalized_company = duplicate.normalized_company
            if (not keep.notes) and duplicate.notes:
                keep.notes = duplicate.notes

            pe_query = session.query(ProcessedEmail).filter(
                ProcessedEmail.application_id == duplicate.id,
            )
            if journey_id is not None:
                pe_query = pe_query.filter(ProcessedEmail.journey_id == journey_id)
            pe_query.update({ProcessedEmail.application_id: keep.id}, synchronize_session=False)

            sh_query = session.query(StatusHistory).filter(
                StatusHistory.application_id == duplicate.id,
            )
            if journey_id is not None:
                sh_query = sh_query.filter(StatusHistory.journey_id == journey_id)
            sh_query.update({StatusHistory.application_id: keep.id}, synchronize_session=False)

            session.delete(duplicate)
            merged += 1

            logger.info(
                "application_duplicate_merged",
                owner_user_id=owner_user_id,
                journey_id=journey_id,
                kept_id=keep.id,
                deleted_id=duplicate.id,
                dedup_key=f"{key[0]}|{key[1]}|{key[2]}",
            )

    if merged or backfilled > 0:
        session.flush()
    return merged
