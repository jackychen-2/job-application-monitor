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

from job_monitor.extraction.rules import (
    is_blank_or_artifact_job_title,
    normalize_req_id,
    split_title_and_req_id,
    validate_job_title_candidate,
)
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


def _is_manual_source(source: str | None) -> bool:
    return (source or "").strip().lower().startswith("manual")


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


def _email_identity_fingerprints(email: ProcessedEmail) -> set[str]:
    """Return stable fingerprints that identify the same underlying email."""
    fingerprints: set[str] = set()

    gmail_message_id = _clean_text(email.gmail_message_id)
    if gmail_message_id:
        fingerprints.add(f"gmail:{gmail_message_id}")

    subject = _clean_text(email.subject).lower()
    sender = _clean_text(email.sender).lower()
    email_dt = _to_naive(email.email_date)
    if subject and sender and email_dt is not None:
        fingerprints.add(
            f"ssd:{subject}|{sender}|{email_dt.replace(microsecond=0).isoformat(sep=' ')}"
        )

    return fingerprints


def _should_merge_shared_identity_group(
    candidates: list[Application],
    email_counts: dict[int, int],
) -> bool:
    """Allow shared-email dedupe only for clear split-app repair cases.

    This path is intentionally narrow: it should repair one blank/artifact title
    that split away from a richer application, not merge two independently valid
    applications that merely share a templated confirmation email.
    """
    req_ids = {
        normalize_req_id(app.req_id or "")
        for app in candidates
        if normalize_req_id(app.req_id or "")
    }
    if len(req_ids) > 1:
        return False

    valid_titles: dict[int, str] = {}
    artifact_or_blank_ids: set[int] = set()
    for app in candidates:
        validated_title = validate_job_title_candidate(app.job_title or "")
        if validated_title:
            valid_titles[app.id] = validated_title
        if is_blank_or_artifact_job_title(app.job_title):
            artifact_or_blank_ids.add(app.id)

    if len(valid_titles) != 1 or not artifact_or_blank_ids:
        return False

    valid_app_id = next(iter(valid_titles))
    if valid_app_id in artifact_or_blank_ids:
        return False

    # Require corroborating evidence on the richer application so a single
    # templated confirmation email cannot collapse two different roles.
    return email_counts.get(valid_app_id, 0) >= 2 or bool(req_ids)


def _choose_keep_application(candidates: list[Application], email_counts: dict[int, int]) -> Application:
    manual_candidates = [app for app in candidates if _is_manual_source(app.source)]
    if manual_candidates:
        return manual_candidates[0]
    return max(
        candidates,
        key=lambda app: (
            email_counts.get(app.id, 0),
            _to_naive(app.email_date) or datetime.min,
            _to_naive(app.updated_at) or datetime.min,
            app.id,
        ),
    )


def _merge_duplicate_group(
    session: Session,
    *,
    owner_user_id: int,
    journey_id: int | None,
    candidates: list[Application],
    email_counts: dict[int, int],
    dedup_key: str,
) -> int:
    if len(candidates) < 2:
        return 0

    manual_candidates = [app for app in candidates if _is_manual_source(app.source)]
    if len(manual_candidates) > 1:
        logger.info(
            "application_duplicate_group_skipped_multiple_manual",
            owner_user_id=owner_user_id,
            journey_id=journey_id,
            dedup_key=dedup_key,
            application_ids=[app.id for app in manual_candidates],
        )
        return 0

    keep = _choose_keep_application(candidates, email_counts)
    merged = 0

    for duplicate in candidates:
        if duplicate.id == keep.id:
            continue
        if _is_manual_source(duplicate.source):
            logger.info(
                "application_duplicate_skipped_manual",
                owner_user_id=owner_user_id,
                journey_id=journey_id,
                kept_id=keep.id,
                skipped_manual_id=duplicate.id,
                dedup_key=dedup_key,
            )
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

        keep_title = validate_job_title_candidate(keep.job_title or "")
        duplicate_title = validate_job_title_candidate(duplicate.job_title or "")
        if duplicate_title and is_blank_or_artifact_job_title(keep.job_title):
            keep.job_title = duplicate_title
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

        _refresh_application_email_summary(session, keep)

        session.delete(duplicate)
        merged += 1

        logger.info(
            "application_duplicate_merged",
            owner_user_id=owner_user_id,
            journey_id=journey_id,
            kept_id=keep.id,
            deleted_id=duplicate.id,
            dedup_key=dedup_key,
        )

    return merged


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


def _refresh_application_email_summary(session: Session, app: Application) -> None:
    latest = (
        session.query(ProcessedEmail)
        .filter(
            ProcessedEmail.application_id == app.id,
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .order_by(ProcessedEmail.email_date.desc())
        .first()
    )
    if latest is None:
        return
    app.email_date = latest.email_date
    app.email_subject = latest.subject
    app.email_sender = latest.sender


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

    merged = 0
    app_by_id = {app.id: app for app in unlocked_apps}
    app_ids = tuple(app_by_id)

    if app_ids:
        email_query = session.query(ProcessedEmail).filter(
            ProcessedEmail.owner_user_id == owner_user_id,
            ProcessedEmail.application_id.in_(app_ids),
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        if journey_id is not None:
            email_query = email_query.filter(ProcessedEmail.journey_id == journey_id)

        fingerprint_map: dict[str, set[int]] = defaultdict(set)
        for email in email_query.all():
            for fingerprint in _email_identity_fingerprints(email):
                fingerprint_map[fingerprint].add(int(email.application_id))

        parent = {app_id: app_id for app_id in app_ids}

        def _find(app_id: int) -> int:
            while parent[app_id] != app_id:
                parent[app_id] = parent[parent[app_id]]
                app_id = parent[app_id]
            return app_id

        def _union(a: int, b: int) -> None:
            ra = _find(a)
            rb = _find(b)
            if ra != rb:
                parent[rb] = ra

        for fingerprint, application_ids in fingerprint_map.items():
            if len(application_ids) < 2:
                continue
            by_company: dict[str, list[int]] = defaultdict(list)
            for app_id in sorted(application_ids):
                app = app_by_id.get(app_id)
                if app is None:
                    continue
                company_key = _canonical_company(app.company, app.normalized_company)
                if company_key:
                    by_company[company_key].append(app_id)
            for company_key, ids in by_company.items():
                if len(ids) < 2:
                    continue
                first = ids[0]
                for app_id in ids[1:]:
                    _union(first, app_id)

        grouped_by_overlap: dict[tuple[str, int], list[Application]] = {}
        for app_id in app_ids:
            app = app_by_id.get(app_id)
            if app is None:
                continue
            company_key = _canonical_company(app.company, app.normalized_company)
            if not company_key:
                continue
            root = _find(app_id)
            group_key = (company_key, root)
            grouped_by_overlap.setdefault(group_key, []).append(app)

        for (company_key, _root), candidates in grouped_by_overlap.items():
            if len(candidates) < 2:
                continue
            if not _should_merge_shared_identity_group(candidates, email_counts):
                continue
            ids = tuple(sorted(app.id for app in candidates))
            merged += _merge_duplicate_group(
                session,
                owner_user_id=owner_user_id,
                journey_id=journey_id,
                candidates=candidates,
                email_counts=email_counts,
                dedup_key=f"{company_key}|shared_email_identity|{','.join(str(app_id) for app_id in ids)}",
            )

    if merged > 0:
        session.flush()
        app_query = session.query(Application).filter(Application.owner_user_id == owner_user_id)
        if journey_id is not None:
            app_query = app_query.filter(Application.journey_id == journey_id)
        apps = app_query.all()
        unlocked_apps = [app for app in apps if not app.dedupe_locked]
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

    for key, candidates in grouped.items():
        merged += _merge_duplicate_group(
            session,
            owner_user_id=owner_user_id,
            journey_id=journey_id,
            candidates=candidates,
            email_counts=email_counts,
            dedup_key=f"{key[0]}|{key[1]}|{key[2]}",
        )

    if merged or backfilled > 0:
        session.flush()
    return merged
