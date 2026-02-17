"""CRUD endpoints for job applications."""

from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from job_monitor.database import get_db
from job_monitor.models import Application, StatusHistory
from job_monitor.schemas import (
    ApplicationCreate,
    ApplicationDetailOut,
    ApplicationListOut,
    ApplicationOut,
    ApplicationUpdate,
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

    return ApplicationListOut(
        items=[ApplicationOut.model_validate(app) for app in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{application_id}", response_model=ApplicationDetailOut)
def get_application(
    application_id: int,
    db: Session = Depends(get_db),
) -> ApplicationDetailOut:
    """Get a single application with its full status history."""
    app = db.query(Application).filter(Application.id == application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    history = (
        db.query(StatusHistory)
        .filter(StatusHistory.application_id == application_id)
        .order_by(StatusHistory.changed_at.desc())
        .all()
    )

    return ApplicationDetailOut(
        **ApplicationOut.model_validate(app).model_dump(),
        status_history=[StatusHistoryOut.model_validate(h) for h in history],
    )


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
