"""Helpers for merging duplicate applications belonging to the same owner."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import datetime

import structlog
from sqlalchemy import func
from sqlalchemy.orm import Session

from job_monitor.extraction.rules import normalize_req_id, split_title_and_req_id
from job_monitor.linking.resolver import normalize_company
from job_monitor.models import Application, ProcessedEmail, StatusHistory

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


def merge_owner_duplicate_applications(session: Session, owner_user_id: int) -> int:
    """Merge duplicate Application rows for one owner and move related records."""
    apps = (
        session.query(Application)
        .filter(Application.owner_user_id == owner_user_id)
        .all()
    )

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

    counts = (
        session.query(ProcessedEmail.application_id, func.count(ProcessedEmail.id))
        .filter(
            ProcessedEmail.owner_user_id == owner_user_id,
            ProcessedEmail.application_id.isnot(None),
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .group_by(ProcessedEmail.application_id)
        .all()
    )
    email_counts = {int(app_id): int(cnt) for app_id, cnt in counts if app_id is not None}

    grouped: dict[tuple[str, str, str], list[Application]] = defaultdict(list)
    for app in apps:
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

            session.query(ProcessedEmail).filter(
                ProcessedEmail.application_id == duplicate.id,
            ).update({ProcessedEmail.application_id: keep.id}, synchronize_session=False)

            session.query(StatusHistory).filter(
                StatusHistory.application_id == duplicate.id,
            ).update({StatusHistory.application_id: keep.id}, synchronize_session=False)

            session.delete(duplicate)
            merged += 1

            logger.info(
                "application_duplicate_merged",
                owner_user_id=owner_user_id,
                kept_id=keep.id,
                deleted_id=duplicate.id,
                dedup_key=f"{key[0]}|{key[1]}|{key[2]}",
            )

    if merged or backfilled > 0:
        session.flush()
    return merged
