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
from datetime import datetime, timedelta
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
        ", inc.", " inc.", ", inc", " inc",
        ", llc", " llc",
        ", corp.", " corp.", ", corp", " corp",
        ", ltd.", " ltd.", ", ltd", " ltd",
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
# Tier 2: Company name linking (status-aware + title-aware)
# ---------------------------------------------------------------------------

# Statuses that indicate an application has progressed beyond initial submission.
# If an existing app is in one of these AND the new email is a fresh application
# confirmation (已申请), treat it as a re-application → skip that candidate.
_PROGRESSED_STATUSES = {"拒绝", "面试"}

# Title normalization synonyms
_TITLE_SYNONYMS = {
    "sr.": "senior", "sr": "senior",
    "jr.": "junior", "jr": "junior",
    "mgr": "manager", "eng": "engineer", "dev": "developer",
    "swe": "software engineer", "sde": "software development engineer",
    "mts": "member of technical staff",
    "iii": "3", "ii": "2", "i": "1", "iv": "4", "v": "5",
}


def _normalize_title(title: str) -> str:
    """Normalize a job title for comparison."""
    t = title.lower().strip()
    t = re.sub(r"[,\-–—/|()[\]{}]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    words = t.split()
    words = [_TITLE_SYNONYMS.get(w, w) for w in words]
    return " ".join(words)


def titles_similar(title_a: str | None, title_b: str | None, threshold: float = 0.9) -> bool:
    """Check if two job titles are essentially the same after normalization.

    Uses Jaccard word similarity with a strict threshold (default 0.9).
    Only near-identical titles are considered similar.

    Returns True if:
    - Either title is empty/None (can't judge, assume similar)
    - Normalized titles are identical
    - Jaccard similarity >= threshold

    Examples:
        titles_similar("Senior Data Engineer", "Sr. Data Engineer") → True (1.0)
        titles_similar("Data Engineer", "Product Manager") → False (0.0)
        titles_similar("Senior Data Engineer", "Senior Data Engineer - New Solutions") → False (0.6)
    """
    if not title_a or not title_b:
        return True  # Can't judge without both titles

    a = _normalize_title(title_a)
    b = _normalize_title(title_b)

    if a == b:
        return True

    words_a = set(a.split())
    words_b = set(b.split())

    if not words_a or not words_b:
        return True

    intersection = words_a & words_b
    union = words_a | words_b
    jaccard = len(intersection) / len(union)

    return jaccard >= threshold


def resolve_by_company(
    session: Session,
    company: str | None,
    extracted_status: str | None = None,
    job_title: str | None = None,
    email_date: Optional["datetime"] = None,
    llm_provider: Optional[object] = None,
    email_subject: str = "",
    email_sender: str = "",
    email_body: str = "",
) -> LinkResult:
    """Attempt to link a new email to an existing Application by company name.

    This is the fallback when thread ID linking fails. Uses three filtering
    rules to avoid incorrect merges:

    Rule A (Title mismatch): If both have job titles and they're clearly
        different (Jaccard < 0.9), skip that candidate.
    Rule B (Time gap): If new email is 已申请 and titles match but the time
        gap exceeds 3 days from the app's last email, skip (different cycle).
    Rule C (Re-application): If existing app is in a progressed status
        (拒绝/面试) and the new email is 已申请, skip that candidate.

    Args:
        session: Database session.
        company: Company name extracted from the email.
        extracted_status: Status extracted from the new email (已申请/面试/Offer/拒绝).
        job_title: Job title extracted from the new email.
        email_date: Date of the new email (from email header).

    Returns:
        LinkResult with:
        - Single match: application_id set, needs_review=False
        - Multiple matches: application_id=None, needs_review=True
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
    all_apps = (
        session.query(Application)
        .filter(Application.normalized_company == normalized)
        .order_by(Application.created_at.desc())
        .all()
    )

    if not all_apps:
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

    # ── Filter candidates ─────────────────────────────────
    candidates = list(all_apps)

    # Rule A: Title mismatch (strict — only near-identical titles match)
    if job_title:
        before = len(candidates)
        candidates = [
            app for app in candidates
            if titles_similar(job_title, app.job_title)
        ]
        filtered = before - len(candidates)
        if filtered > 0:
            logger.info(
                "company_link_title_filtered",
                company=company,
                new_title=job_title,
                filtered_count=filtered,
                remaining=len(candidates),
            )

    # Rule B: Time gap (only for 已申请 — two application confirmations
    # to the same company > 3 days apart = different application cycle)
    _MAX_SAME_CYCLE_DAYS = 3
    if extracted_status == "已申请" and email_date and candidates:
        def _within_time_window(app: Application) -> bool:
            if not app.email_date:
                return True  # No date to compare, keep candidate
            # Normalize both to naive for comparison
            ed = email_date.replace(tzinfo=None) if hasattr(email_date, 'tzinfo') and email_date.tzinfo else email_date
            ad = app.email_date.replace(tzinfo=None) if hasattr(app.email_date, 'tzinfo') and app.email_date.tzinfo else app.email_date
            gap = abs((ed - ad).days)
            return gap <= _MAX_SAME_CYCLE_DAYS

        before = len(candidates)
        candidates = [app for app in candidates if _within_time_window(app)]
        filtered = before - len(candidates)
        if filtered > 0:
            logger.info(
                "company_link_time_filtered",
                company=company,
                max_days=_MAX_SAME_CYCLE_DAYS,
                filtered_count=filtered,
                remaining=len(candidates),
            )

    # Rule C: Re-application after rejection/interview
    # If existing app has progressed (拒绝/面试) and new email is a fresh
    # application confirmation (已申请), treat as re-application.
    if extracted_status == "已申请" and candidates:
        before = len(candidates)
        candidates = [
            app for app in candidates
            if app.status not in _PROGRESSED_STATUSES
        ]
        filtered = before - len(candidates)
        if filtered > 0:
            logger.info(
                "company_link_reapplication_filtered",
                company=company,
                filtered_count=filtered,
                remaining=len(candidates),
            )

    # ── Decision: Rules A/B/C all passed → LLM confirms ──
    if not candidates:
        logger.info(
            "company_link_all_filtered",
            company=company,
            total_apps=len(all_apps),
            extracted_status=extracted_status,
            job_title=job_title,
        )
        return LinkResult(
            application_id=None,
            confidence=0.0,
            link_method="new",
        )

    # LLM confirmation: ask whether the email is about the same application
    # If LLM is unavailable, default to creating a new application (conservative)
    if llm_provider is not None and hasattr(llm_provider, "confirm_same_application"):
        for candidate in candidates:
            try:
                confirm_result = llm_provider.confirm_same_application(
                    email_subject=email_subject,
                    email_sender=email_sender,
                    email_body=email_body,
                    app_company=candidate.company,
                    app_job_title=candidate.job_title or "",
                    app_status=candidate.status,
                    app_last_email_subject=candidate.email_subject or "",
                )
                if confirm_result.is_same_application:
                    logger.info(
                        "linked_by_company_llm_confirmed",
                        company=company,
                        application_id=candidate.id,
                        prompt_tokens=confirm_result.prompt_tokens,
                    )
                    return LinkResult(
                        application_id=candidate.id,
                        confidence=COMPANY_LINK_CONFIDENCE,
                        link_method="company",
                    )
                else:
                    logger.info(
                        "company_link_llm_rejected",
                        company=company,
                        application_id=candidate.id,
                    )
            except Exception as exc:
                logger.warning(
                    "company_link_llm_error",
                    company=company,
                    application_id=candidate.id,
                    error=str(exc),
                )
                # LLM failed — conservative: don't link
                continue

        # LLM rejected all candidates (or all errored)
        logger.info(
            "company_link_llm_rejected_all",
            company=company,
            candidate_count=len(candidates),
        )
        return LinkResult(
            application_id=None,
            confidence=0.0,
            link_method="new",
        )

    # No LLM available — conservative: create new application
    logger.info(
        "company_link_no_llm_conservative",
        company=company,
        candidate_count=len(candidates),
    )
    return LinkResult(
        application_id=None,
        confidence=0.0,
        link_method="new",
    )
