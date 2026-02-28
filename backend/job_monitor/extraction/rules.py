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
                r"\b(team|careers?|jobs?|hiring|recruiting)\b$", "", company, flags=re.IGNORECASE
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

_REQ_ID_TOKEN = r"(?:R-?\d{5,}|JR\d{5,}|\d{4}-\d{3,6}|\d{5,8})"
_REQ_ID_RE = re.compile(rf"\b{_REQ_ID_TOKEN}\b", re.IGNORECASE)


def normalize_req_id(value: str) -> str:
    """Normalize requisition IDs like r0612345 / jr123456 / 2025-4844 to uppercase."""
    compact = re.sub(r"\s+", "", value or "")
    if not compact:
        return ""
    matched = _REQ_ID_RE.search(compact)
    if not matched:
        return ""
    return matched.group(0).upper()


def split_title_and_req_id(title: str) -> tuple[str, str]:
    """Split a combined title into (base_title, req_id) when possible."""
    value = _normalize_space(title or "")
    if not value:
        return "", ""

    paren_tail = re.search(
        rf"^(?P<title>.*?)\s*\((?P<req>{_REQ_ID_TOKEN})\)\s*$",
        value,
        re.IGNORECASE,
    )
    if paren_tail:
        return _normalize_space(paren_tail.group("title")), normalize_req_id(paren_tail.group("req"))

    tail = re.search(
        rf"^(?P<title>.*?)(?:\s*[-,:]\s*|\s+)(?P<req>{_REQ_ID_TOKEN})\s*$",
        value,
        re.IGNORECASE,
    )
    if tail:
        return _normalize_space(tail.group("title")), normalize_req_id(tail.group("req"))

    head = re.search(
        rf"^(?P<req>{_REQ_ID_TOKEN})\s*[-,:]\s*(?P<title>.+)$",
        value,
        re.IGNORECASE,
    )
    if head:
        return _normalize_space(head.group("title")), normalize_req_id(head.group("req"))

    return value, ""


def compose_title_with_req_id(base_title: str, req_id: str) -> str:
    """Compose display title with requisition ID when both are present."""
    title = _normalize_space(base_title or "")
    rid = normalize_req_id(req_id)
    if not title:
        return ""
    return f"{title} - {rid}" if rid else title


def _extract_req_id_near_title(text: str, title: str, max_dist: int = 90) -> str:
    """Find a requisition ID near a known title span."""
    parts = [re.escape(p) for p in title.split() if p]
    if not parts:
        return ""
    title_re = re.compile(r"\s+".join(parts), re.IGNORECASE)
    candidates: list[tuple[int, int, int, str]] = []

    for tm in title_re.finditer(text):
        win_start = max(0, tm.start() - max_dist)
        win_end = min(len(text), tm.end() + max_dist)
        window = text[win_start:win_end]
        title_local_start = tm.start() - win_start

        for rm in _REQ_ID_RE.finditer(window):
            req = normalize_req_id(rm.group(0))
            if not req:
                continue
            is_after = 1 if rm.start() > title_local_start else 0
            dist = min(abs(rm.start() - title_local_start), abs(rm.end() - title_local_start))
            prefix_penalty = 1 if req[:1].isdigit() else 0
            candidates.append((is_after, dist, prefix_penalty, req))

    if not candidates:
        return ""
    candidates.sort()
    return candidates[0][3]


_REQ_CONTEXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        rf"\b(?:position(?:\(s\))?|role|opening|requisition|job requisition)\b[^\n\r]{{0,60}}?\b(?P<req>{_REQ_ID_TOKEN})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bsubject:\s*[^\n\r]{{0,120}}?\b(?P<req>{_REQ_ID_TOKEN})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:-|:|\|)\s*(?P<req>{_REQ_ID_TOKEN})\s*(?:-|:|\|)\s*[A-Za-z]",
        re.IGNORECASE,
    ),
]


def extract_job_req_id(subject: str, body: str, job_title: str = "") -> str:
    """Extract requisition ID associated with a job title from subject/body."""
    text = f"{subject}\n{body}"

    base_title, req_from_title = split_title_and_req_id(job_title)
    if req_from_title:
        return req_from_title

    if base_title:
        near = _extract_req_id_near_title(text, base_title)
        if near:
            return near

    for pattern in _REQ_CONTEXT_PATTERNS:
        matched = pattern.search(text)
        if matched:
            req = normalize_req_id(matched.group("req"))
            if req:
                return req
    return ""


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
    (
        [
            "background check",
            "background screening",
            "pre-employment screening",
            "drug test",
            "drug screen",
            "i-9",
            "i9",
            "e-verify",
            "onboarding",
            "on-boarding",
            "new hire paperwork",
            "new hire portal",
            "benefits enrollment",
            "benefits enrolment",
            "direct deposit",
            "payroll setup",
            "w-4",
            "w4",
            "orientation",
        ],
        "Onboarding",
    ),
    (["offer letter", "congratulations", "录用", "录取"], "Offer"),
    (
        [
            "online assessment",
            "online assessemnt",
            "oa invitation",
            "oa invite",
            "coding challenge",
            "assessment",
            "online test",
            "take-home",
            "hackerrank",
            "codesignal",
            "codility",
            "笔试",
        ],
        "OA",
    ),
    (
        [
            "interview",
            "phone screen",
            "onsite",
            "面试",
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
