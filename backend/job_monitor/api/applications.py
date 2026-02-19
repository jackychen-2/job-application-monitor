"""CRUD endpoints for job applications."""

from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from job_monitor.database import get_db
from job_monitor.models import Application, ProcessedEmail, StatusHistory
from job_monitor.schemas import (
    ApplicationCreate,
    ApplicationDetailOut,
    ApplicationListOut,
    ApplicationOut,
    ApplicationUpdate,
    LinkedEmailOut,
    MergeApplicationRequest,
    StatusHistoryOut,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("", response_model=ApplicationListOut)
def list_applications(
    status: Optional[str] = Query(None, description="Filter by status"),
    company: Optional[str] = Query(None, description="Search company name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("created_at", description="Sort field"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort order"),
    db: Session = Depends(get_db),
) -> ApplicationListOut:
    """List applications with optional filtering, sorting, and pagination."""
    query = db.query(Application)

    # Filters
    if status:
        query = query.filter(Application.status == status)
    if company:
        query = query.filter(Application.company.ilike(f"%{company}%"))

    # Total count before pagination
    total = query.count()

    # Sorting
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
) -> ApplicationOut:
    """Manually create a new application."""
    # Check for duplicates
    existing = (
        db.query(Application)
        .filter(Application.company == body.company, Application.job_title == body.job_title)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Application already exists for {body.company} - {body.job_title}",
        )

    app = Application(
        company=body.company,
        job_title=body.job_title,
        status=body.status,
        source=body.source,
        notes=body.notes,
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

    logger.info("application_created_manual", id=app.id, company=body.company)
    return ApplicationOut.model_validate(app)


@router.patch("/{application_id}", response_model=ApplicationOut)
def update_application(
    application_id: int,
    body: ApplicationUpdate,
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
) -> ApplicationOut:
    """Merge source application into target. Moves all emails and history, deletes source."""
    target = db.query(Application).filter(Application.id == application_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target application not found")

    source = db.query(Application).filter(Application.id == body.source_application_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source application not found")

    if target.id == source.id:
        raise HTTPException(status_code=400, detail="Cannot merge application with itself")

    # Move all processed emails from source to target
    moved_emails = (
        db.query(ProcessedEmail)
        .filter(ProcessedEmail.application_id == source.id)
        .update({ProcessedEmail.application_id: target.id})
    )

    # Move status history from source to target
    moved_history = (
        db.query(StatusHistory)
        .filter(StatusHistory.application_id == source.id)
        .update({StatusHistory.application_id: target.id})
    )

    # Delete the source application
    db.delete(source)
    db.commit()
    db.refresh(target)

    logger.info(
        "applications_merged",
        target_id=target.id,
        source_id=body.source_application_id,
        moved_emails=moved_emails,
        moved_history=moved_history,
    )

    # Recount emails
    email_count = (
        db.query(func.count(ProcessedEmail.id))
        .filter(
            ProcessedEmail.application_id == target.id,
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .scalar() or 0
    )

    app_dict = ApplicationOut.model_validate(target).model_dump()
    app_dict["email_count"] = email_count
    return ApplicationOut(**app_dict)
