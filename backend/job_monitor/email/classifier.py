"""Keyword-based classifier to decide if an email is job-application related."""

from __future__ import annotations

import re
import structlog

logger = structlog.get_logger(__name__)

# ── Signal keywords (case-insensitive) ────────────────────
JOB_SIGNAL_KEYWORDS: list[str] = [
    # English
    "application",
    "applied",
    "thank you for applying",
    "interview",
    "recruiter",
    "hiring",
    "position",
    "job",
    "career",
    "offer letter",
    "regret to inform",
    "unfortunately",
    "moved forward",
    "next steps",
    "phone screen",
    "onsite",
    "coding challenge",
    "take-home",
    "assessment",
    # Chinese
    "投递",
    "申请",
    "应聘",
    "职位",
    "岗位",
    "面试",
    "录用",
    "offer",
]

# Negative keywords — emails containing these are NOT job applications
NEGATIVE_KEYWORDS: list[str] = [
    "verification",
    "verify your",
    "identity verification",
    "password reset",
    "two-factor",
    "2fa",
    "otp",
    "security code",
    "unsubscribe",
    "newsletter",
    "job alert",
    "jobs for you",
    "recommended jobs",
    "career journey",
    "career tips",
    "opportunities for you",
    "grow your career",
    "empower your",
    "we found",
    "wotc",
    "推荐职位",
    "验证码",
    "密码重置",
]

SOCIAL_INVITATION_SUBJECT_HINTS: tuple[str, ...] = (
    "you have an invitation",
    "wants to connect",
    "would like to connect",
    "invitation to connect",
    "connection request",
    "join my network",
    "invited you to connect",
    "sent you an invitation",
)

SOCIAL_INVITATION_BODY_HINTS: tuple[str, ...] = (
    "i'd like to add you to my professional network",
    "let's connect on linkedin",
    "invited you to connect",
)

JOB_RECOMMENDATION_SUBJECT_HINTS: tuple[str, ...] = (
    "i think this job might be right for you",
    "your job alert",
    "jobs for you",
    "recommended jobs",
    "jobs matching your profile",
    "new jobs for you",
    "we found jobs",
    "recommended for you",
)

JOB_RECOMMENDATION_BODY_HINTS: tuple[str, ...] = (
    "jobs matching your profile",
    "recommended jobs",
    "job alert",
    "view all jobs",
    "new jobs for you",
)

JOB_BOARD_SENDER_HINTS: tuple[str, ...] = (
    "ziprecruiter.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "wellfound.com",
    "monster.com",
    "dice.com",
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def detect_non_job_reason(sender: str, subject: str, body: str = "") -> str | None:
    """Return a specific non-job reason when a known pattern is detected."""
    normalized_sender = _normalize_text(sender)
    normalized_subject = _normalize_text(subject)
    normalized_body = _normalize_text(body)
    combined_text = f"{normalized_subject}\n{normalized_body}"

    # High-confidence LinkedIn invite rule from user examples.
    if "invitations@linkedin.com" in normalized_sender and "you have an invitation" in normalized_subject:
        return "social_invitation"

    if "linkedin.com" in normalized_sender and (
        _contains_any(normalized_subject, SOCIAL_INVITATION_SUBJECT_HINTS)
        or _contains_any(combined_text, SOCIAL_INVITATION_BODY_HINTS)
    ):
        return "social_invitation"

    # High-confidence ZipRecruiter sharing rule from user examples.
    if "ziprecruiter.com" in normalized_sender and "i think this job might be right for you" in normalized_subject:
        return "job_recommendation_digest"

    subject_is_digest = _contains_any(normalized_subject, JOB_RECOMMENDATION_SUBJECT_HINTS)
    body_is_digest = _contains_any(combined_text, JOB_RECOMMENDATION_BODY_HINTS)
    sender_is_job_board = _contains_any(normalized_sender, JOB_BOARD_SENDER_HINTS)
    if subject_is_digest and (sender_is_job_board or body_is_digest):
        return "job_recommendation_digest"

    return None


def is_job_related(subject: str, sender: str = "", body: str = "") -> bool:
    """Return True if the email subject contains job-application signal words
    AND does not contain negative keywords.

    Args:
        subject: Decoded email subject line.
        sender: Decoded sender address.
        body: Email body text.

    Returns:
        True when signal keywords found and no negative keywords present.
    """
    non_job_reason = detect_non_job_reason(sender, subject, body)
    if non_job_reason:
        logger.debug("classifier_non_job_reason_match", reason=non_job_reason, subject=subject[:80], sender=sender[:80])
        return False

    searchable = subject.lower()

    # Check negative keywords first
    if any(neg in searchable for neg in NEGATIVE_KEYWORDS):
        logger.debug("classifier_negative_match", subject=subject[:80])
        return False

    matched = any(kw in searchable for kw in JOB_SIGNAL_KEYWORDS)
    if matched:
        logger.debug("classifier_match", subject=subject[:80])
    return matched
