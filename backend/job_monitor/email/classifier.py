"""Keyword-based classifier to decide if an email is job-application related."""

from __future__ import annotations

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


def is_job_related(subject: str, sender: str = "") -> bool:
    """Return True if the email subject contains job-application signal words
    AND does not contain negative keywords.

    Args:
        subject: Decoded email subject line.
        sender: Decoded sender address (currently unused, reserved for future rules).

    Returns:
        True when signal keywords found and no negative keywords present.
    """
    searchable = subject.lower()

    # Check negative keywords first
    if any(neg in searchable for neg in NEGATIVE_KEYWORDS):
        logger.debug("classifier_negative_match", subject=subject[:80])
        return False

    matched = any(kw in searchable for kw in JOB_SIGNAL_KEYWORDS)
    if matched:
        logger.debug("classifier_match", subject=subject[:80])
    return matched
