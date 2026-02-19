"""Multi-tier resolver to link emails to job applications.

Linking strategies (in priority order):
1. Thread ID match - Same Gmail conversation (highest confidence)
2. Company name match - Same company, single application
3. Flag for review - Same company, multiple applications (user decides)
4. Create new - No match found

This module runs BEFORE expensive LLM/extraction logic to link related emails
(e.g., "submission -> OA -> interview -> offer") to the same Application record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, List

import structlog
from sqlalchemy.orm import Session

from job_monitor.models import ProcessedEmail, Application

logger = structlog.get_logger(__name__)

# Confidence scores for different linking methods
THREAD_LINK_CONFIDENCE = 0.95
COMPANY_LINK_CONFIDENCE = 0.80


@dataclass(frozen=True)
class LinkResult:
    """Result of attempting to link an email to an application."""

    application_id: Optional[int] = None
    confidence: float = 0.0
    link_method: str = "new"  # "thread", "company", "manual", "new"
    needs_review: bool = False
    candidate_app_ids: tuple[int, ...] = ()

    @property
    def is_linked(self) -> bool:
        """Return True if email was linked to an existing application."""
        return self.application_id is not None


# Keep alias for backwards compatibility
ThreadLinkResult = LinkResult


# ---------------------------------------------------------------------------
# Company name normalization
# ---------------------------------------------------------------------------

def normalize_company(name: str | None) -> str | None:
    """Normalize company name for matching.

    Examples:
        "Tesla, Inc." -> "tesla"
        "Meta Platforms" -> "meta platforms"
        "WPROMOTE" -> "wpromote"
        "Snap Inc" -> "snap"
    """
    if not name:
        return None

    name = name.lower().strip()

    # Remove legal suffixes
    suffixes = [
        ", inc.", " inc.", " inc",
        ", llc", " llc",
        ", corp.", " corp.", " corp",
        ", ltd.", " ltd.", " ltd",
        ", gmbh", " gmbh",
        " limited", ", limited",
        " co.", ", co.",
        " company", ", company",
    ]
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break

    # Remove extra whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name if name else None


# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------

def is_message_already_processed(
    session: Session,
    gmail_message_id: Optional[str],
) -> bool:
    """Check if a message with this gmail_message_id has already been processed.

    Args:
        session: Database session.
        gmail_message_id: The unique Gmail message ID (Message-ID header).

    Returns:
        True if this message_id already exists in the database.
    """
    if not gmail_message_id:
        return False

    existing = (
        session.query(ProcessedEmail.id)
        .filter(ProcessedEmail.gmail_message_id == gmail_message_id)
        .first()
    )

    if existing:
        logger.info(
            "skipped_duplicate_message",
            gmail_message_id=gmail_message_id[:50] if gmail_message_id else None,
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Tier 1: Thread ID linking
# ---------------------------------------------------------------------------

def resolve_by_thread_id(
    session: Session,
    gmail_thread_id: Optional[str],
) -> LinkResult:
    """Attempt to link a new email to an existing Application via Gmail thread ID.

    This should be called BEFORE extraction/LLM logic. If a previous email
    in the same thread was already linked to an Application, we reuse that
    Application with high confidence.

    Args:
        session: Database session.
        gmail_thread_id: Gmail's X-GM-THRID value for this email.

    Returns:
        LinkResult with application_id if found, None otherwise.
    """
    if not gmail_thread_id:
        logger.debug("thread_link_skipped_no_thread_id")
        return LinkResult(
            application_id=None,
            confidence=0.0,
            link_method="new",
        )

    # Find any previously processed email with the same thread_id
    # that was linked to a job application
    existing_email = (
        session.query(ProcessedEmail)
        .filter(
            ProcessedEmail.gmail_thread_id == gmail_thread_id,
            ProcessedEmail.application_id.isnot(None),
            ProcessedEmail.is_job_related == True,  # noqa: E712
        )
        .order_by(ProcessedEmail.processed_at.desc())  # Most recent first
        .first()
    )

    if existing_email and existing_email.application_id:
        logger.info(
            "linked_by_thread_id",
            gmail_thread_id=gmail_thread_id,
            application_id=existing_email.application_id,
            previous_email_uid=existing_email.uid,
        )
        return LinkResult(
            application_id=existing_email.application_id,
            confidence=THREAD_LINK_CONFIDENCE,
            link_method="thread",
        )

    # No existing link found
    logger.debug(
        "thread_link_no_match",
        gmail_thread_id=gmail_thread_id,
    )
    return LinkResult(
        application_id=None,
        confidence=0.0,
        link_method="new",
    )


# ---------------------------------------------------------------------------
# Tier 2: Company name linking
# ---------------------------------------------------------------------------

def resolve_by_company(
    session: Session,
    company: str | None,
) -> LinkResult:
    """Attempt to link a new email to an existing Application by company name.

    This is the fallback when thread ID linking fails.

    Args:
        session: Database session.
        company: Company name extracted from the email.

    Returns:
        LinkResult with:
        - Single match: application_id set, needs_review=False
        - Multiple matches: application_id=None, needs_review=True, candidate_app_ids set
        - No match: application_id=None, needs_review=False
    """
    if not company:
        logger.debug("company_link_skipped_no_company")
        return LinkResult(
            application_id=None,
            confidence=0.0,
            link_method="new",
        )

    normalized = normalize_company(company)
    if not normalized:
        return LinkResult(
            application_id=None,
            confidence=0.0,
            link_method="new",
        )

    # Find all applications with matching normalized company
    apps = (
        session.query(Application)
        .filter(Application.normalized_company == normalized)
        .order_by(Application.created_at.desc())  # Most recent first
        .all()
    )

    if not apps:
        logger.debug(
            "company_link_no_match",
            company=company,
            normalized_company=normalized,
        )
        return LinkResult(
            application_id=None,
            confidence=0.0,
            link_method="new",
        )

    if len(apps) == 1:
        # Single match - auto-link with high confidence
        logger.info(
            "linked_by_company",
            company=company,
            normalized_company=normalized,
            application_id=apps[0].id,
        )
        return LinkResult(
            application_id=apps[0].id,
            confidence=COMPANY_LINK_CONFIDENCE,
            link_method="company",
        )

    # Multiple matches - flag for user review
    app_ids = tuple(a.id for a in apps)
    logger.info(
        "company_link_ambiguous",
        company=company,
        normalized_company=normalized,
        candidate_count=len(apps),
        candidate_app_ids=app_ids,
    )
    return LinkResult(
        application_id=None,
        confidence=0.0,
        link_method="new",
        needs_review=True,
        candidate_app_ids=app_ids,
    )
