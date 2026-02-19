"""Email review and manual linking endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from job_monitor.database import get_db
from job_monitor.models import Application, ProcessedEmail, StatusHistory
from job_monitor.schemas import (
    LinkEmailRequest,
    LinkedEmailOut,
    MergeApplicationRequest,
    PendingReviewEmailOut,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/emails", tags=["emails"])


@router.get("/pending-review", response_model=list[PendingReviewEmailOut])
def get_pending_review_emails(
    db: Session = Depends(get_db),
) -> list[PendingReviewEmailOut]:
    """Get all emails that need user review for linking."""
    emails = (
        db.query(ProcessedEmail)
        .filter(
            ProcessedEmail.needs_review == True,  # noqa: E712
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .order_by(ProcessedEmail.email_date.desc())
        .all()
    )

    result = []
    for e in emails:
        app_company = None
        if e.application_id:
            app = db.query(Application).get(e.application_id)
            app_company = app.company if app else None

        result.append(
            PendingReviewEmailOut(
                id=e.id,
                uid=e.uid,
                subject=e.subject,
                sender=e.sender,
                email_date=e.email_date,
                application_id=e.application_id,
                application_company=app_company,
            )
        )

    return result


@router.patch("/{email_id}/link", response_model=LinkedEmailOut)
def link_email_to_application(
    email_id: int,
    body: LinkEmailRequest,
    db: Session = Depends(get_db),
) -> LinkedEmailOut:
    """Manually link an email to a specific application."""
    email = db.query(ProcessedEmail).filter(ProcessedEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    app = db.query(Application).filter(Application.id == body.application_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    old_app_id = email.application_id
    email.application_id = body.application_id
    email.link_method = "manual"
    email.needs_review = False

    db.commit()
    db.refresh(email)

    logger.info(
        "email_manually_linked",
        email_id=email_id,
        old_application_id=old_app_id,
        new_application_id=body.application_id,
    )

    return LinkedEmailOut.model_validate(email)


@router.delete("/{email_id}/link", response_model=LinkedEmailOut)
def unlink_email(
    email_id: int,
    db: Session = Depends(get_db),
) -> LinkedEmailOut:
    """Remove an email's link to its application."""
    email = db.query(ProcessedEmail).filter(ProcessedEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    old_app_id = email.application_id
    email.application_id = None
    email.link_method = None
    email.needs_review = False

    db.commit()
    db.refresh(email)

    logger.info("email_unlinked", email_id=email_id, old_application_id=old_app_id)
    return LinkedEmailOut.model_validate(email)


@router.post("/{email_id}/dismiss-review", response_model=dict)
def dismiss_review(
    email_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Dismiss the review flag on an email without changing its link."""
    email = db.query(ProcessedEmail).filter(ProcessedEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    email.needs_review = False
    db.commit()

    return {"status": "ok", "email_id": email_id}
