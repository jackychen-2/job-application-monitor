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
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Callable, Optional, Sequence

import structlog
from sqlalchemy.orm import Session

from job_monitor.extraction.rules import normalize_req_id
from job_monitor.models import ProcessedEmail, Application, StatusHistory

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


@dataclass(frozen=True)
class CompanyLinkCandidate:
    """Minimal candidate shape used by shared company-linking logic."""

    id: int
    company: str
    normalized_company: Optional[str] = None
    job_title: Optional[str] = None
    req_id: Optional[str] = None
    status: Optional[str] = None
    last_email_subject: Optional[str] = None


# ---------------------------------------------------------------------------
# Company name normalization
# ---------------------------------------------------------------------------

def normalize_company(name: str | None) -> str | None:
    """Normalize company name for matching.

    Strips legal entity suffixes AND common descriptive company-type words
    so that "Zoom", "Zoom Communications", "Calico Labs" all normalise to
    the same root name.

    Examples:
        "Tesla, Inc."            -> "tesla"
        "Zoom Communications"    -> "zoom"
        "Calico Labs"            -> "calico"
        "Expedia Group"          -> "expedia"
        "Meta Platforms"         -> "meta platforms"  (no strip — 'platforms' is brand)
        "WPROMOTE"               -> "wpromote"
        "Snap Inc"               -> "snap"
    """
    if not name:
        return None

    # Normalise all Unicode whitespace (non-breaking space, thin space, etc.)
    # to plain ASCII space BEFORE suffix matching, otherwise suffixes like
    # " inc." won't be detected when the space is U+00A0.
    name = re.sub(r"\s+", " ", name).lower().strip()

    # ── Step 1: Strip legal entity suffixes (end of string) ──────────────
    legal_suffixes = [
        ", inc.", " inc.", ", inc", " inc",
        ", llc", " llc",
        ", corp.", " corp.", ", corp", " corp",
        ", ltd.", " ltd.", ", ltd", " ltd",
        ", gmbh", " gmbh",
        " limited", ", limited",
        " co.", ", co.",
        " company", ", company",
    ]
    for suffix in legal_suffixes:
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break

    # ── Step 2: Strip generic company-type words (suffix only) ───────────
    # These words are commonly appended to brand names in email subjects/senders
    # and cause the same company to appear under multiple slightly different names.
    # Guard: only strip if the remaining prefix is ≥ 3 chars so we don't reduce
    # short names to empty strings, and skip multi-word branded suffixes like
    # "web services" where the preceding word is also descriptive.
    type_suffixes = [
        " communications", " communication",
        " technologies", " technology",
        " solutions", " solution",
        " systems", " system",
        " group",
        " labs", " lab",
        " global",
        " international",
        " enterprises", " enterprise",
        " holdings", " holding",
        # NOTE: " services" and " service" intentionally omitted — they appear
        # in branded product names ("Amazon Web Services") where stripping would
        # create a wrong token.  Legal-entity " services" is rare in job emails.
    ]
    for suffix in type_suffixes:
        if name.endswith(suffix) and len(name) > len(suffix) + 2:
            name = name[: -len(suffix)].strip()
            break  # strip at most one type suffix

    # ── Step 3: Strip email-artifact prefixes (e.g. "your zoom" → "zoom") ─
    artifact_prefixes = ["your "]
    for prefix in artifact_prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break

    return name.strip() if name.strip() else None


# ---------------------------------------------------------------------------
# Fuzzy company candidate search
# ---------------------------------------------------------------------------

def _candidate_normalized_company(candidate: CompanyLinkCandidate) -> str:
    return candidate.normalized_company or normalize_company(candidate.company) or ""


def _find_fuzzy_candidates(
    candidates: Sequence[CompanyLinkCandidate],
    normalized: str,
    threshold: float = 0.75,
) -> list[CompanyLinkCandidate]:
    """Return candidates with similar normalized company names, sorted by similarity."""
    if not normalized:
        return []

    scored: list[tuple[float, CompanyLinkCandidate]] = []
    for candidate in candidates:
        existing_norm = _candidate_normalized_company(candidate)
        if not existing_norm:
            continue
        sim = SequenceMatcher(None, normalized, existing_norm).ratio()
        if sim >= threshold:
            scored.append((sim, candidate))

    scored.sort(key=lambda x: (-x[0], -(x[1].id)))
    return [candidate for _, candidate in scored]


def _to_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.isoformat(sep=" ", timespec="seconds")


def _build_timeline_summary(
    session: Session,
    app: Application,
    new_email_date: datetime | None,
    max_events: int = 5,
) -> dict:
    """Build compact timeline context for LLM link confirmation."""
    new_dt = _to_naive(new_email_date)
    app_created = _to_naive(app.created_at)
    app_last_email = _to_naive(app.email_date)
    days_since_last_email: int | None = None
    if new_dt and app_last_email:
        days_since_last_email = abs((new_dt - app_last_email).days)

    # Pull recent email and status events; merge by timestamp desc.
    email_rows = (
        session.query(ProcessedEmail.email_date, ProcessedEmail.subject)
        .filter(ProcessedEmail.application_id == app.id)
        .order_by(ProcessedEmail.email_date.desc().nullslast(), ProcessedEmail.processed_at.desc())
        .limit(max_events)
        .all()
    )
    status_rows = (
        session.query(StatusHistory.changed_at, StatusHistory.new_status)
        .filter(StatusHistory.application_id == app.id)
        .order_by(StatusHistory.changed_at.desc())
        .limit(max_events)
        .all()
    )

    merged: list[tuple[datetime, dict[str, str]]] = []
    for email_dt, subject in email_rows:
        sort_dt = _to_naive(email_dt) or datetime.min
        merged.append(
            (
                sort_dt,
                {
                    "date": _fmt_dt(_to_naive(email_dt)),
                    "status": "",
                    "subject": (subject or "")[:180],
                },
            )
        )
    for changed_at, new_status in status_rows:
        sort_dt = _to_naive(changed_at) or datetime.min
        merged.append(
            (
                sort_dt,
                {
                    "date": _fmt_dt(_to_naive(changed_at)),
                    "status": (new_status or "")[:80],
                    "subject": "",
                },
            )
        )

    merged.sort(key=lambda x: x[0], reverse=True)
    recent_events = [event for _, event in merged[:max_events]]

    return {
        "new_email_date": _fmt_dt(new_dt),
        "app_created_at": _fmt_dt(app_created),
        "app_last_email_date": _fmt_dt(app_last_email),
        "days_since_last_email": days_since_last_email,
        "recent_events": recent_events,
    }


def _confirm_same_application_with_timeline(
    llm_provider: object,
    *,
    email_subject: str,
    email_sender: str,
    email_body: str,
    app_company: str,
    app_job_title: str,
    app_status: str,
    app_last_email_subject: str,
    timeline: dict,
):
    """Call provider.confirm_same_application with timeline fields (compat fallback)."""
    try:
        return llm_provider.confirm_same_application(  # type: ignore[attr-defined]
            email_subject=email_subject,
            email_sender=email_sender,
            email_body=email_body,
            app_company=app_company,
            app_job_title=app_job_title,
            app_status=app_status,
            app_last_email_subject=app_last_email_subject,
            new_email_date=timeline["new_email_date"],
            app_created_at=timeline["app_created_at"],
            app_last_email_date=timeline["app_last_email_date"],
            days_since_last_email=timeline["days_since_last_email"],
            recent_events=timeline["recent_events"],
        )
    except TypeError:
        # Backward-compat with providers using the old signature.
        return llm_provider.confirm_same_application(  # type: ignore[attr-defined]
            email_subject=email_subject,
            email_sender=email_sender,
            email_body=email_body,
            app_company=app_company,
            app_job_title=app_job_title,
            app_status=app_status,
            app_last_email_subject=app_last_email_subject,
        )


def _default_timeline() -> dict:
    return {
        "new_email_date": "",
        "app_created_at": "",
        "app_last_email_date": "",
        "days_since_last_email": None,
        "recent_events": [],
    }


def _confirm_candidate(
    llm_provider: object,
    *,
    candidate: CompanyLinkCandidate,
    email_subject: str,
    email_sender: str,
    email_body: str,
    timeline_provider: Optional[Callable[[CompanyLinkCandidate], dict]] = None,
):
    timeline = _default_timeline()
    if timeline_provider is not None:
        try:
            provided = timeline_provider(candidate) or {}
            timeline.update({
                "new_email_date": provided.get("new_email_date", ""),
                "app_created_at": provided.get("app_created_at", ""),
                "app_last_email_date": provided.get("app_last_email_date", ""),
                "days_since_last_email": provided.get("days_since_last_email"),
                "recent_events": provided.get("recent_events", []),
            })
        except Exception:
            timeline = _default_timeline()

    return _confirm_same_application_with_timeline(
        llm_provider,
        email_subject=email_subject,
        email_sender=email_sender,
        email_body=email_body,
        app_company=candidate.company,
        app_job_title=candidate.job_title or "",
        app_status=candidate.status or "",
        app_last_email_subject=candidate.last_email_subject or "",
        timeline=timeline,
    )


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
_PROGRESSED_STATUSES = {"OA", "面试", "Offer", "Onboarding", "拒绝"}

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
    # Remove requisition-id-like tokens so title matching is stable
    # even when one side includes IDs and the other does not.
    t = re.sub(r"\b(?:r-?\d{5,}|jr\d{5,}|\d{4}-\d{3,6}|\d{5,8})\b", " ", t, flags=re.IGNORECASE)
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


def titles_equal_strict(title_a: str | None, title_b: str | None) -> bool:
    """Strict title equality for direct-link gating.

    Requires both titles to be present and normalized forms to match exactly.
    """
    if not title_a or not title_b:
        return False
    return _normalize_title(title_a) == _normalize_title(title_b)


def resolve_by_company_candidates(
    *,
    company: str | None,
    candidates: Sequence[CompanyLinkCandidate],
    extracted_status: str | None = None,
    job_title: str | None = None,
    req_id: str | None = None,
    llm_provider: Optional[object] = None,
    email_subject: str = "",
    email_sender: str = "",
    email_body: str = "",
    timeline_provider: Optional[Callable[[CompanyLinkCandidate], dict]] = None,
) -> LinkResult:
    """Shared company-linking decision core used by prod and eval pipelines."""
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

    same_company_candidates = [
        candidate for candidate in candidates
        if _candidate_normalized_company(candidate) == normalized
    ]
    if not same_company_candidates:
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

    filtered_candidates = list(same_company_candidates)

    # Rule 0: Req ID exact match when available.
    # Direct link is allowed only when BOTH req_id and title match (company is already pre-filtered).
    # If req_id matches but title is missing/mismatched, defer to LLM confirmation instead of direct linking.
    # If no exact req_id match exists, fall back to legacy rows with missing req_id.
    incoming_req = normalize_req_id(req_id or "")
    if incoming_req:
        exact_req = [c for c in filtered_candidates if normalize_req_id(c.req_id or "") == incoming_req]
        if exact_req:
            if job_title:
                title_matched_exact = [
                    c for c in exact_req
                    if titles_equal_strict(job_title, c.job_title)
                ]
                if title_matched_exact:
                    chosen = title_matched_exact[0]
                    logger.info(
                        "linked_by_company_req_id_title_strict",
                        company=company,
                        req_id=incoming_req,
                        job_title=job_title,
                        application_id=chosen.id,
                    )
                    return LinkResult(
                        application_id=chosen.id,
                        confidence=0.98,
                        link_method="company_req_id",
                    )
                logger.info(
                    "company_link_req_id_match_but_title_mismatch_defer_llm",
                    company=company,
                    req_id=incoming_req,
                    job_title=job_title,
                    candidate_count=len(exact_req),
                )
                filtered_candidates = exact_req
            else:
                logger.info(
                    "company_link_req_id_match_but_title_missing_defer_llm",
                    company=company,
                    req_id=incoming_req,
                    candidate_count=len(exact_req),
                )
                filtered_candidates = exact_req
        else:
            legacy_no_req = [c for c in filtered_candidates if not normalize_req_id(c.req_id or "")]
            if job_title:
                title_matched = [
                    c for c in legacy_no_req
                    if titles_similar(job_title, c.job_title)
                ]
                if title_matched:
                    filtered_candidates = title_matched
                    logger.info(
                        "company_link_req_fallback_title_matched",
                        company=company,
                        job_title=job_title,
                        remaining=len(filtered_candidates),
                    )
                else:
                    filtered_candidates = legacy_no_req
            else:
                filtered_candidates = legacy_no_req
    elif job_title:
        # No req_id in incoming email: prioritize same-title candidates for LLM confirmation.
        title_matched = [c for c in filtered_candidates if titles_similar(job_title, c.job_title)]
        if title_matched:
            filtered_candidates = title_matched
            logger.info(
                "company_link_no_req_title_matched",
                company=company,
                job_title=job_title,
                remaining=len(filtered_candidates),
            )

    # Rule 1: Re-application after rejection/interview
    # If existing app has progressed (OA/面试/Offer/Onboarding/拒绝) and new email is a fresh
    # application confirmation (已申请), treat as re-application.
    if extracted_status == "已申请" and filtered_candidates:
        before = len(filtered_candidates)
        filtered_candidates = [
            c for c in filtered_candidates
            if (c.status or "") not in _PROGRESSED_STATUSES
        ]
        filtered = before - len(filtered_candidates)
        if filtered > 0:
            logger.info(
                "company_link_reapplication_filtered",
                company=company,
                filtered_count=filtered,
                remaining=len(filtered_candidates),
            )

    # ── Decision: rule-filtered candidates → LLM confirms ──
    if not filtered_candidates:
        logger.info(
            "company_link_all_filtered",
            company=company,
            total_apps=len(same_company_candidates),
            extracted_status=extracted_status,
            job_title=job_title,
        )

        # ── Rescue pass: fuzzy company match + LLM confirmation ──────────
        # Includes exact company-norm matches too (sim=1.0): they may have been
        # filtered out by title/status rules and still deserve an LLM check.
        if llm_provider is not None and hasattr(llm_provider, "confirm_same_application"):
            fuzzy_candidates = _find_fuzzy_candidates(candidates, normalized, threshold=0.75)
            if fuzzy_candidates:
                logger.info(
                    "company_link_fuzzy_rescue_attempt",
                    company=company,
                    normalized=normalized,
                    fuzzy_candidate_count=len(fuzzy_candidates),
                )
                for candidate in fuzzy_candidates[:3]:
                    try:
                        confirm_result = _confirm_candidate(
                            llm_provider,
                            candidate=candidate,
                            email_subject=email_subject,
                            email_sender=email_sender,
                            email_body=email_body,
                            timeline_provider=timeline_provider,
                        )
                        if confirm_result.is_same_application:
                            logger.info(
                                "linked_by_fuzzy_llm_rescue",
                                company=company,
                                matched_company=candidate.company,
                                normalized_incoming=normalized,
                                normalized_matched=_candidate_normalized_company(candidate),
                                application_id=candidate.id,
                                prompt_tokens=confirm_result.prompt_tokens,
                            )
                            return LinkResult(
                                application_id=candidate.id,
                                confidence=0.75,
                                link_method="company_fuzzy",
                            )
                        else:
                            logger.info(
                                "fuzzy_rescue_llm_rejected",
                                company=company,
                                matched_company=candidate.company,
                                application_id=candidate.id,
                            )
                    except Exception as exc:
                        logger.warning(
                            "fuzzy_rescue_llm_error",
                            company=company,
                            application_id=candidate.id,
                            error=str(exc),
                        )
                        continue

        return LinkResult(
            application_id=None,
            confidence=0.0,
            link_method="new",
        )

    # LLM confirmation: ask whether the email is about the same application
    # If LLM is unavailable, default to creating a new application (conservative)
    if llm_provider is not None and hasattr(llm_provider, "confirm_same_application"):
        for candidate in filtered_candidates:
            try:
                confirm_result = _confirm_candidate(
                    llm_provider,
                    candidate=candidate,
                    email_subject=email_subject,
                    email_sender=email_sender,
                    email_body=email_body,
                    timeline_provider=timeline_provider,
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
            candidate_count=len(filtered_candidates),
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
        candidate_count=len(filtered_candidates),
    )
    return LinkResult(
        application_id=None,
        confidence=0.0,
        link_method="new",
    )


def resolve_by_company(
    session: Session,
    company: str | None,
    extracted_status: str | None = None,
    job_title: str | None = None,
    req_id: str | None = None,
    email_date: Optional["datetime"] = None,
    llm_provider: Optional[object] = None,
    email_subject: str = "",
    email_sender: str = "",
    email_body: str = "",
) -> LinkResult:
    """Attempt to link a new email to an existing Application by company name.

    This is the fallback when thread ID linking fails. Uses deterministic
    pre-filters plus LLM confirmation to avoid incorrect merges:

    Rule 0 (Req ID): Prefer exact req_id match when available.
    Rule 1 (Re-application): If existing app is in a progressed status
        (OA/面试/Offer/Onboarding/拒绝) and the new email is 已申请, skip that candidate.

    After rule filtering, LLM confirmation is preferred when LLM is available:
    every remaining exact-company candidate is checked with confirm_same_application().

    Args:
        session: Database session.
        company: Company name extracted from the email.
        extracted_status: Status extracted from the new email (已申请/OA/面试/Offer/Onboarding/拒绝).
        job_title: Job title extracted from the new email.
        req_id: Requisition ID extracted from the new email.
        email_date: Date of the new email (from email header).

    Returns:
        LinkResult with:
        - Single match: application_id set, needs_review=False
        - Multiple matches: application_id=None, needs_review=True
        - No match: application_id=None, needs_review=False
    """
    all_apps = (
        session.query(Application)
        .order_by(Application.created_at.desc())
        .all()
    )
    app_candidates = [
        CompanyLinkCandidate(
            id=app.id,
            company=app.company,
            normalized_company=app.normalized_company,
            job_title=app.job_title,
            req_id=app.req_id,
            status=app.status,
            last_email_subject=app.email_subject,
        )
        for app in all_apps
    ]
    app_map = {app.id: app for app in all_apps}

    def _timeline_provider(candidate: CompanyLinkCandidate) -> dict:
        app = app_map.get(candidate.id)
        if app is None:
            return _default_timeline()
        return _build_timeline_summary(session, app, email_date)

    return resolve_by_company_candidates(
        company=company,
        candidates=app_candidates,
        extracted_status=extracted_status,
        job_title=job_title,
        req_id=req_id,
        llm_provider=llm_provider,
        email_subject=email_subject,
        email_sender=email_sender,
        email_body=email_body,
        timeline_provider=_timeline_provider,
    )
