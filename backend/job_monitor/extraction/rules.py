"""Regex-based field extraction for company name, job title, and status."""

from __future__ import annotations

import re

import structlog

from job_monitor.email.parser import is_noise_text

logger = structlog.get_logger(__name__)

# ── Helpers ───────────────────────────────────────────────


def _clean_text(text: str, max_len: int = 90) -> str:
    """Collapse whitespace and trim surrounding punctuation."""
    value = re.sub(r"\s+", " ", text).strip(" \t\r\n-:;,.，。")
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    return value


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ── Company extraction ────────────────────────────────────

COMPANY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*([A-Za-z0-9&.'\- ]{2,50})\s*[-:|]", re.IGNORECASE),
    re.compile(
        r"[-:|]\s*([A-Za-z0-9&.'\- ]{2,50})\s*(?:application|job|position|role|careers?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:at|to)\s+([A-Za-z0-9&.,'\- ]{2,60})(?:\s+(?:has|for|about|on)\b|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfrom\s+([A-Za-z0-9&.,'\- ]{2,60})(?:\s+(?:has|for|about|on)\b|$)",
        re.IGNORECASE,
    ),
    re.compile(r"加入\s*([^\s,，。!！?？]{2,30})"),
    re.compile(r"来自\s*([^\s,，。!！?？]{2,30})"),
    re.compile(r"【([^】]{2,40})】"),
]

_JUNK_SUBJECT_MARKERS = [
    "and ",
    " more jobs",
    "new jobs",
    "job alert",
    "jobs you may be interested in",
    "推荐职位",
]

_GENERIC_COMPANY_NAMES = {"thank you", "application received", "application", "job"}


def extract_company_from_subject(subject: str) -> str:
    """Try to pull a company name from the email subject via regex."""
    lowered = subject.lower()
    if any(marker in lowered for marker in _JUNK_SUBJECT_MARKERS):
        return ""

    for pattern in COMPANY_PATTERNS:
        matched = pattern.search(subject)
        if matched:
            company = _clean_text(matched.group(1))
            company = re.sub(
                r"\b(team|careers?|jobs?)\b$", "", company, flags=re.IGNORECASE
            ).strip()
            company = re.sub(
                r"\b(application|applied|position|role)\b.*$",
                "",
                company,
                flags=re.IGNORECASE,
            ).strip()
            if company.lower() in _GENERIC_COMPANY_NAMES:
                continue
            if company:
                return company
    return ""


def infer_company_from_sender(sender: str) -> str:
    """Fall back to extracting company from the sender's email domain."""
    match = re.search(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", sender)
    if not match:
        return ""
    domain = match.group(1).lower()
    stripped = re.sub(r"^(mail|email|notifications|notify|jobs?|careers?)\.", "", domain)

    if stripped.endswith(".co.uk"):
        pieces = stripped.split(".")
        if len(pieces) >= 3:
            return pieces[-3].replace("-", " ").title()

    pieces = stripped.split(".")
    if len(pieces) >= 2:
        return pieces[-2].replace("-", " ").title()
    return stripped.replace("-", " ").title()


def extract_company(subject: str, sender: str) -> str:
    """Extract company name from subject first, fall back to sender domain."""
    company = extract_company_from_subject(subject)
    if not company:
        company = infer_company_from_sender(sender)
    return company or "Unknown"


# ── Job title extraction ──────────────────────────────────

JOB_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:(?<=^)|(?<=\s))(?:position|role|title)\s*[:：\-]\s*([^\n\r]{2,100})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfor\s+(?:the\s+)?([A-Za-z0-9 /&,+.#()\-]{2,90})\s+(?:position|role)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:applied|application)\s*(?:for|to)\s*([A-Za-z0-9 /&,+.#()\-]{2,90})\s+at\s+[A-Za-z0-9&.,'\- ]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:applied|application)\s*(?:for|to)\s*([A-Za-z0-9 /&,+.#()\-]{2,90})",
        re.IGNORECASE,
    ),
    re.compile(
        r"for the\s+([A-Za-z0-9 /&,+.#()\-]{2,90})\s+position",
        re.IGNORECASE,
    ),
    re.compile(
        r"for our\s+([A-Za-z0-9 /&,+.#()\-]{2,90})\s+role",
        re.IGNORECASE,
    ),
    re.compile(r"(?:职位|岗位)\s*[:：]\s*([^\n\r]{2,80})"),
    re.compile(r"申请(?:的)?\s*([^\n\r，。]{2,80})(?:职位|岗位)"),
]


def _clean_title(text: str) -> str:
    """Post-process a raw title match."""
    value = _clean_text(text)
    value = re.sub(r"\s*\|\s*.*$", "", value)
    value = re.sub(
        r"\s*-\s*(application|applied|confirmation|received).*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\s+(application|confirmation|received)$", "", value, flags=re.IGNORECASE
    )
    value = re.sub(
        r"\b(application|submitted|received|confirmation|thank you|thanks)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    return value


_ROLE_KEYWORDS = {"engineer", "developer", "manager", "analyst", "scientist", "designer", "intern"}


def extract_job_title(subject: str, body: str) -> str:
    """Extract a job title from the email subject and body via regex patterns."""
    combined = f"{subject}\n{body}"

    # Phase 1: structured patterns against combined text
    for pattern in JOB_TITLE_PATTERNS:
        matched = pattern.search(combined)
        if matched:
            title = _clean_title(matched.group(1))
            if title and not is_noise_text(title):
                return title

    # Phase 2: subject-specific fallbacks
    subject_fallback_patterns = [
        re.compile(r"application for\s+([A-Za-z0-9 /&,+.#()\-]{2,90})", re.IGNORECASE),
        re.compile(r"applied to\s+([A-Za-z0-9 /&,+.#()\-]{2,90})", re.IGNORECASE),
        re.compile(
            r"for\s+(?:the\s+)?([A-Za-z0-9 /&,+.#()\-]{2,90})\s+(?:position|role)",
            re.IGNORECASE,
        ),
    ]
    for pattern in subject_fallback_patterns:
        matched = pattern.search(subject)
        if matched:
            title = _clean_title(matched.group(1))
            if title and not is_noise_text(title):
                return title

    # Phase 3: line-by-line scan of body for role-keyword lines
    for line in body.splitlines():
        line = _normalize_space(line)
        if len(line) < 4 or len(line) > 120 or is_noise_text(line):
            continue
        for pattern in JOB_TITLE_PATTERNS:
            matched = pattern.search(line)
            if matched:
                title = _clean_title(matched.group(1))
                if title and not is_noise_text(title):
                    return title
        if re.match(r"^[A-Za-z][A-Za-z0-9 /&,+.#()\-]{3,80}$", line):
            if any(kw in line.lower() for kw in _ROLE_KEYWORDS):
                return _clean_title(line)

    # Phase 4: subject structure "Company - Role" or "Role at Company"
    structure_patterns = [
        re.compile(r"^[^\-|:]{2,60}\s*-\s*([^\-|:]{2,90})$", re.IGNORECASE),
        re.compile(r"^([^\-|:]{2,90})\s+at\s+[^\-|:]{2,60}$", re.IGNORECASE),
    ]
    for pattern in structure_patterns:
        matched = pattern.search(subject)
        if matched:
            title = _clean_title(matched.group(1))
            if title and not is_noise_text(title):
                return title

    return ""


# ── Status extraction ─────────────────────────────────────

_STATUS_MAP: list[tuple[list[str], str]] = [
    (["offer letter", "congratulations", "录用", "录取"], "Offer"),
    (
        [
            "interview",
            "phone screen",
            "onsite",
            "coding challenge",
            "assessment",
            "面试",
            "笔试",
        ],
        "面试",
    ),
    (
        [
            "regret",
            "unfortunately",
            "not moving forward",
            "will not be moving",
            "拒绝",
            "不合适",
            "遗憾",
        ],
        "拒绝",
    ),
    (
        [
            "application",
            "applied",
            "thank you for applying",
            "received your",
            "投递",
            "申请",
        ],
        "已申请",
    ),
]


def extract_status(subject: str, body: str) -> str:
    """Infer application status from subject + body keywords."""
    searchable = f"{subject}\n{body}".lower()
    for keywords, status in _STATUS_MAP:
        if any(kw in searchable for kw in keywords):
            return status
    return "已申请"
