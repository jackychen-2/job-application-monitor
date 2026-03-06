"""CRUD endpoints for job applications."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from job_monitor.auth.deps import get_owner_scoped_db
from job_monitor.dedupe import merge_owner_duplicate_applications
from job_monitor.extraction.rules import normalize_req_id
from job_monitor.linking.resolver import normalize_company
from job_monitor.models import (
    Application,
    ApplicationMergeEvent,
    ApplicationMergeItem,
    ProcessedEmail,
    StatusHistory,
)
from job_monitor.schemas import (
    ApplicationCreate,
    ApplicationDetailOut,
    ApplicationListOut,
    ApplicationMergeEventOut,
    ApplicationOut,
    ApplicationUpdate,
    LinkedEmailOut,
    MergeApplicationRequest,
    SplitApplicationOut,
    SplitApplicationRequest,
    StatusHistoryOut,
    UnmergeApplicationOut,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/applications", tags=["applications"])


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_snapshot_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _serialize_application_snapshot(app: Application) -> dict[str, Any]:
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


def _count_job_related_emails(db: Session, application_id: int) -> int:
    return (
        db.query(func.count(ProcessedEmail.id))
        .filter(
            ProcessedEmail.application_id == application_id,
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .scalar()
        or 0
    )


def _refresh_application_email_summary(db: Session, app: Application) -> None:
    """Refresh app email_date/subject/sender from most recent linked job email."""
    latest = (
        db.query(ProcessedEmail)
        .filter(
            ProcessedEmail.application_id == app.id,
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .order_by(ProcessedEmail.email_date.desc())
        .first()
    )
    if latest is None:
        app.email_date = None
        app.email_subject = None
        app.email_sender = None
        return
    app.email_date = latest.email_date
    app.email_subject = latest.subject
    app.email_sender = latest.sender


@router.get("", response_model=ApplicationListOut)
def list_applications(
    status: Optional[str] = Query(None, description="Filter by status"),
    company: Optional[str] = Query(None, description="Search company name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("created_at", description="Sort field"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort order"),
    db: Session = Depends(get_owner_scoped_db),
) -> ApplicationListOut:
    """List applications with optional filtering, sorting, and pagination."""
    owner_user_id = db.info.get("owner_user_id")
    journey_id = db.info.get("journey_id")
    if isinstance(owner_user_id, int):
        try:
            with db.begin_nested():
                merged = merge_owner_duplicate_applications(
                    db,
                    owner_user_id,
                    journey_id if isinstance(journey_id, int) else None,
                )
            if merged > 0:
                logger.info(
                    "applications_list_deduped",
                    owner_user_id=owner_user_id,
                    merged=merged,
                )
        except Exception as exc:
            logger.warning(
                "applications_list_dedupe_failed",
                owner_user_id=owner_user_id,
                error=str(exc),
            )

    query = db.query(Application)

    # Filters
    if status:
        query = query.filter(Application.status == status)
    if company:
        query = query.filter(Application.company.ilike(f"%{company}%"))

    # Total count before pagination
    total = query.count()

    # Sorting
    if sort_by == "email_date":
        # Manual apps can have NULL email_date; use created_at fallback so newly created rows
        # still surface near the top when sorting by recent activity.
        sort_column = func.coalesce(Application.email_date, Application.created_at)
    else:
        sort_column = getattr(Application, sort_by, Application.created_at)
    if sort_order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    # Pagination
    offset = (page - 1) * page_size
    items = query.offset(offset).limit(page_size).all()

    # Get email counts for each application (for expandable rows)
    app_ids = [app.id for app in items]
    email_counts = {}
    if app_ids:
        counts = (
            db.query(ProcessedEmail.application_id, func.count(ProcessedEmail.id))
            .filter(
                ProcessedEmail.application_id.in_(app_ids),
                ProcessedEmail.is_job_related == True,  # noqa: E712
            )
            .group_by(ProcessedEmail.application_id)
            .all()
        )
        email_counts = {app_id: count for app_id, count in counts}

    # Build response with email_count
    items_out = []
    for app in items:
        app_dict = ApplicationOut.model_validate(app).model_dump()
        app_dict["email_count"] = email_counts.get(app.id, 0)
        items_out.append(ApplicationOut(**app_dict))

    return ApplicationListOut(
        items=items_out,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{application_id}", response_model=ApplicationDetailOut)
def get_application(
    application_id: int,
    db: Session = Depends(get_owner_scoped_db),
) -> ApplicationDetailOut:
    """Get a single application with its full status history and linked emails."""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    history = (
        db.query(StatusHistory)
        .filter(StatusHistory.application_id == application_id)
        .order_by(StatusHistory.changed_at.desc())
        .all()
    )

    # Get all emails linked to this application (via thread linking or direct)
    linked_emails = (
        db.query(ProcessedEmail)
        .filter(
            ProcessedEmail.application_id == application_id,
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .order_by(ProcessedEmail.email_date.desc())
        .all()
    )

    app_dict = ApplicationOut.model_validate(app).model_dump()
    app_dict["email_count"] = len(linked_emails)
    return ApplicationDetailOut(
        **app_dict,
        status_history=[StatusHistoryOut.model_validate(h) for h in history],
        linked_emails=[LinkedEmailOut.model_validate(e) for e in linked_emails],
    )


@router.get("/{application_id}/emails", response_model=list[LinkedEmailOut])
def get_application_emails(
    application_id: int,
    db: Session = Depends(get_owner_scoped_db),
) -> list[LinkedEmailOut]:
    """Get all linked emails for an application (for expandable row in table)."""
    # Verify application exists
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    linked_emails = (
        db.query(ProcessedEmail)
        .filter(
            ProcessedEmail.application_id == application_id,
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .order_by(ProcessedEmail.email_date.asc())  # Chronological order for timeline
        .all()
    )

    return [LinkedEmailOut.model_validate(e) for e in linked_emails]


@router.post("", response_model=ApplicationOut, status_code=201)
def create_application(
    body: ApplicationCreate,
    db: Session = Depends(get_owner_scoped_db),
) -> ApplicationOut:
    """Manually create a new application."""
    req_id_raw = (body.req_id or "").strip()
    req_id = normalize_req_id(req_id_raw) or (req_id_raw or None)
    company = body.company.strip()
    job_title = (body.job_title or None)

    app = Application(
        company=company,
        normalized_company=normalize_company(company),
        job_title=job_title,
        req_id=req_id,
        status=body.status,
        source=body.source,
        notes=body.notes,
        dedupe_locked=True,
    )
    db.add(app)
    db.flush()

    # Record initial status
    db.add(
        StatusHistory(
            application_id=app.id,
            old_status=None,
            new_status=body.status,
            change_source="manual",
        )
    )
    db.commit()
    db.refresh(app)

    logger.info("application_created_manual", id=app.id, company=company)
    return ApplicationOut.model_validate(app)


@router.patch("/{application_id}", response_model=ApplicationOut)
def update_application(
    application_id: int,
    body: ApplicationUpdate,
    db: Session = Depends(get_owner_scoped_db),
) -> ApplicationOut:
    """Update an application's fields (status, notes, etc.)."""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    update_data = body.model_dump(exclude_unset=True)

    # Track status change
    if "status" in update_data and update_data["status"] != app.status:
        db.add(
            StatusHistory(
                application_id=app.id,
                old_status=app.status,
                new_status=update_data["status"],
                change_source="manual",
            )
        )

    for field, value in update_data.items():
        setattr(app, field, value)

    db.commit()
    db.refresh(app)

    logger.info("application_updated", id=app.id, fields=list(update_data.keys()))
    return ApplicationOut.model_validate(app)


@router.delete("/{application_id}", status_code=204)
def delete_application(
    application_id: int,
    db: Session = Depends(get_owner_scoped_db),
) -> None:
    """Delete an application and its history."""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    db.delete(app)
    db.commit()
    logger.info("application_deleted", id=application_id)


@router.post("/{application_id}/merge", response_model=ApplicationOut)
def merge_applications(
    application_id: int,
    body: MergeApplicationRequest,
    db: Session = Depends(get_owner_scoped_db),
) -> ApplicationOut:
    """Merge source application into target and persist audit details for possible unmerge."""
    target = db.query(Application).filter(Application.id == application_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target application not found")

    source = db.query(Application).filter(Application.id == body.source_application_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source application not found")

    if target.id == source.id:
        raise HTTPException(status_code=400, detail="Cannot merge application with itself")

    source_email_ids = [
        row_id
        for row_id, in db.query(ProcessedEmail.id)
        .filter(ProcessedEmail.application_id == source.id)
        .all()
    ]
    source_history_ids = [
        row_id
        for row_id, in db.query(StatusHistory.id)
        .filter(StatusHistory.application_id == source.id)
        .all()
    ]

    merge_event = ApplicationMergeEvent(
        target_application_id=target.id,
        source_application_id=source.id,
        merge_source="manual",
        source_company=source.company,
        source_job_title=source.job_title,
        source_req_id=source.req_id,
        source_status=source.status,
        source_snapshot_json=json.dumps(_serialize_application_snapshot(source), ensure_ascii=False),
        moved_email_count=len(source_email_ids),
        moved_history_count=len(source_history_ids),
    )
    db.add(merge_event)
    db.flush()

    if source_email_ids:
        db.add_all(
            [
                ApplicationMergeItem(
                    merge_event_id=merge_event.id,
                    item_type="processed_email",
                    item_id=item_id,
                )
                for item_id in source_email_ids
            ]
        )
    if source_history_ids:
        db.add_all(
            [
                ApplicationMergeItem(
                    merge_event_id=merge_event.id,
                    item_type="status_history",
                    item_id=item_id,
                )
                for item_id in source_history_ids
            ]
        )

    # Move all processed emails from source to target
    moved_emails = (
        db.query(ProcessedEmail)
        .filter(ProcessedEmail.application_id == source.id)
        .update({ProcessedEmail.application_id: target.id}, synchronize_session=False)
    )

    # Move status history from source to target
    moved_history = (
        db.query(StatusHistory)
        .filter(StatusHistory.application_id == source.id)
        .update({StatusHistory.application_id: target.id}, synchronize_session=False)
    )

    # Delete the source application
    target.dedupe_locked = False
    db.delete(source)
    db.commit()
    db.refresh(target)

    logger.info(
        "applications_merged",
        merge_event_id=merge_event.id,
        target_id=target.id,
        source_id=body.source_application_id,
        moved_emails=moved_emails,
        moved_history=moved_history,
    )

    app_dict = ApplicationOut.model_validate(target).model_dump()
    app_dict["email_count"] = _count_job_related_emails(db, target.id)
    return ApplicationOut(**app_dict)


@router.get("/{application_id}/merge-events", response_model=list[ApplicationMergeEventOut])
def list_application_merge_events(
    application_id: int,
    db: Session = Depends(get_owner_scoped_db),
) -> list[ApplicationMergeEventOut]:
    """List merge history where this application was the merge target."""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    events = (
        db.query(ApplicationMergeEvent)
        .filter(ApplicationMergeEvent.target_application_id == application_id)
        .order_by(ApplicationMergeEvent.merged_at.desc())
        .all()
    )
    return [ApplicationMergeEventOut.model_validate(e) for e in events]


@router.post("/{application_id}/unmerge/{merge_event_id}", response_model=UnmergeApplicationOut)
def unmerge_application(
    application_id: int,
    merge_event_id: int,
    db: Session = Depends(get_owner_scoped_db),
) -> UnmergeApplicationOut:
    """Restore one historical merge by recreating the source application and moving tracked rows back."""
    target = db.query(Application).filter(Application.id == application_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target application not found")

    merge_event = (
        db.query(ApplicationMergeEvent)
        .filter(
            ApplicationMergeEvent.id == merge_event_id,
            ApplicationMergeEvent.target_application_id == application_id,
        )
        .first()
    )
    if not merge_event:
        raise HTTPException(status_code=404, detail="Merge event not found")
    if merge_event.undone_at is not None:
        raise HTTPException(status_code=409, detail="This merge event was already unmerged")

    snapshot: dict[str, Any] = {}
    if merge_event.source_snapshot_json:
        try:
            snapshot = json.loads(merge_event.source_snapshot_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=409, detail="Merge snapshot is corrupted") from exc

    source_company = (snapshot.get("company") or merge_event.source_company or "").strip()
    source_job_title = snapshot.get("job_title") or merge_event.source_job_title
    source_req_id_raw = (snapshot.get("req_id") or merge_event.source_req_id or "").strip()
    source_req_id = normalize_req_id(source_req_id_raw) or (source_req_id_raw or None)
    source_status = snapshot.get("status") or merge_event.source_status or target.status

    if not source_company:
        raise HTTPException(status_code=409, detail="Cannot unmerge because source company is missing")

    restored_source = Application(
        company=source_company,
        normalized_company=snapshot.get("normalized_company") or normalize_company(source_company),
        job_title=source_job_title,
        req_id=source_req_id,
        email_subject=snapshot.get("email_subject"),
        email_sender=snapshot.get("email_sender"),
        email_date=_parse_snapshot_datetime(snapshot.get("email_date")),
        status=source_status,
        source=snapshot.get("source") or "manual",
        notes=snapshot.get("notes"),
        created_at=_parse_snapshot_datetime(snapshot.get("created_at")) or datetime.now(timezone.utc),
        updated_at=_parse_snapshot_datetime(snapshot.get("updated_at")) or datetime.now(timezone.utc),
        dedupe_locked=True,
    )
    db.add(restored_source)
    db.flush()

    items = (
        db.query(ApplicationMergeItem)
        .filter(ApplicationMergeItem.merge_event_id == merge_event.id)
        .all()
    )
    email_ids = [item.item_id for item in items if item.item_type == "processed_email"]
    history_ids = [item.item_id for item in items if item.item_type == "status_history"]

    restored_emails = 0
    restored_history = 0
    if email_ids:
        restored_emails = (
            db.query(ProcessedEmail)
            .filter(
                ProcessedEmail.id.in_(email_ids),
                ProcessedEmail.application_id == target.id,
            )
            .update({ProcessedEmail.application_id: restored_source.id}, synchronize_session=False)
        )
    if history_ids:
        restored_history = (
            db.query(StatusHistory)
            .filter(
                StatusHistory.id.in_(history_ids),
                StatusHistory.application_id == target.id,
            )
            .update({StatusHistory.application_id: restored_source.id}, synchronize_session=False)
        )

    target.dedupe_locked = True
    merge_event.undone_at = datetime.now(timezone.utc)
    merge_event.undone_source_application_id = restored_source.id
    db.commit()

    logger.info(
        "application_unmerged",
        merge_event_id=merge_event.id,
        target_application_id=target.id,
        restored_source_application_id=restored_source.id,
        restored_emails=restored_emails,
        restored_history=restored_history,
    )

    return UnmergeApplicationOut(
        merge_event_id=merge_event.id,
        target_application_id=target.id,
        restored_source_application_id=restored_source.id,
        restored_email_count=restored_emails,
        restored_history_count=restored_history,
        undone_at=merge_event.undone_at,
    )


@router.post("/{application_id}/split", response_model=SplitApplicationOut)
def split_application(
    application_id: int,
    body: SplitApplicationRequest,
    db: Session = Depends(get_owner_scoped_db),
) -> SplitApplicationOut:
    """Split selected emails from one application into a newly created application."""
    source_app = db.query(Application).filter(Application.id == application_id).first()
    if not source_app:
        raise HTTPException(status_code=404, detail="Application not found")

    email_ids = sorted(set(body.email_ids))
    source_email_ids = [
        row_id
        for row_id, in db.query(ProcessedEmail.id)
        .filter(ProcessedEmail.application_id == source_app.id)
        .all()
    ]
    if not source_email_ids:
        raise HTTPException(status_code=400, detail="No linked emails to split")

    selected = (
        db.query(ProcessedEmail)
        .filter(
            ProcessedEmail.application_id == source_app.id,
            ProcessedEmail.id.in_(email_ids),
        )
        .all()
    )
    if len(selected) != len(email_ids):
        raise HTTPException(status_code=400, detail="Some selected emails are not linked to this application")

    if len(email_ids) >= len(source_email_ids):
        raise HTTPException(status_code=400, detail="Cannot split all linked emails from one application")

    company = (body.company or source_app.company or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="Company is required")
    job_title = body.job_title if body.job_title is not None else source_app.job_title
    req_id_raw = (
        body.req_id
        if body.req_id is not None
        else (source_app.req_id or "")
    )
    req_id = normalize_req_id(req_id_raw or "") or ((req_id_raw or "").strip() or None)
    status = body.status or source_app.status
    notes = body.notes if body.notes is not None else source_app.notes

    new_app = Application(
        company=company,
        normalized_company=normalize_company(company),
        job_title=job_title,
        req_id=req_id,
        status=status,
        source="manual_split",
        notes=notes,
        dedupe_locked=True,
    )
    db.add(new_app)
    db.flush()

    moved_count = (
        db.query(ProcessedEmail)
        .filter(
            ProcessedEmail.application_id == source_app.id,
            ProcessedEmail.id.in_(email_ids),
        )
        .update({ProcessedEmail.application_id: new_app.id}, synchronize_session=False)
    )

    source_app.dedupe_locked = True
    _refresh_application_email_summary(db, source_app)
    _refresh_application_email_summary(db, new_app)

    db.add(
        StatusHistory(
            application_id=new_app.id,
            old_status=None,
            new_status=status,
            change_source="manual_split",
        )
    )
    db.commit()

    logger.info(
        "application_split",
        source_application_id=source_app.id,
        new_application_id=new_app.id,
        moved_email_count=moved_count,
    )

    return SplitApplicationOut(
        source_application_id=source_app.id,
        new_application_id=new_app.id,
        moved_email_count=moved_count,
    )
