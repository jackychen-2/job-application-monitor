"""Email MIME parsing — subject decoding, body extraction, noise detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import List, Optional

from bs4 import BeautifulSoup

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

_PT = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class ParsedEmailData:
    """Structured output from parsing a raw email.Message."""

    subject: str
    sender: str
    date_raw: str
    date_pt: str
    date_dt: Optional[datetime]
    body_text: str


# ── Noise detection tokens ────────────────────────────────
_NOISE_TOKENS = [
    "color:",
    "font-",
    "px",
    "{",
    "}",
    "margin",
    "padding",
    "z-index",
    "mso-",
    ".job-title",
    "a:visited",
    "http://",
    "https://",
]


def is_noise_text(text: str, threshold: int = 2) -> bool:
    """Return True if *text* looks like CSS / HTML junk rather than real content."""
    lowered = text.lower()
    hits = sum(1 for tok in _NOISE_TOKENS if tok in lowered)
    return hits >= threshold


# ── MIME helpers ──────────────────────────────────────────


def decode_mime_text(value: Optional[str]) -> str:
    """Decode a MIME-encoded header value to a plain string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def normalize_date_to_pt(date_raw: str) -> str:
    """Convert a raw email date string to ``America/Los_Angeles`` timestamp."""
    if not date_raw:
        return ""
    try:
        dt = parsedate_to_datetime(date_raw)
        pt = dt.astimezone(_PT)
        return pt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return date_raw


def parse_date(date_raw: str) -> Optional[datetime]:
    """Parse a raw email date into a timezone-aware datetime, or None."""
    if not date_raw:
        return None
    try:
        dt = parsedate_to_datetime(date_raw)
        return dt.astimezone(_PT)
    except Exception:
        return None


# ── Body extraction ───────────────────────────────────────


def _html_to_text(html: str) -> str:
    """Strip HTML tags and return readable text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def extract_body_text(msg: Message) -> str:
    """Extract the best plain-text representation of the email body."""
    plain_parts: List[str] = []
    html_parts: List[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdisp = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in cdisp:
                continue
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode(errors="replace")
            if ctype == "text/html":
                html_parts.append(_html_to_text(text))
            else:
                plain_parts.append(text)
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except Exception:
            text = payload.decode(errors="replace")
        if msg.get_content_type() == "text/html":
            html_parts.append(_html_to_text(text))
        else:
            plain_parts.append(text)

    plain_text = "\n".join(plain_parts)
    html_text = "\n".join(html_parts)

    # Prefer plain-text unless it's mostly noise (CSS leftovers)
    if plain_text and not is_noise_text(plain_text):
        return plain_text
    return html_text if html_text else plain_text


# ── Top-level parser ─────────────────────────────────────


def parse_email_message(msg: Message) -> ParsedEmailData:
    """Parse a stdlib ``email.Message`` into a structured ``ParsedEmailData``."""
    subject = decode_mime_text(msg.get("Subject", ""))
    sender = decode_mime_text(msg.get("From", ""))
    date_raw = decode_mime_text(msg.get("Date", ""))
    date_pt = normalize_date_to_pt(date_raw)
    date_dt = parse_date(date_raw)
    body_text = extract_body_text(msg)

    return ParsedEmailData(
        subject=subject,
        sender=sender,
        date_raw=date_raw,
        date_pt=date_pt,
        date_dt=date_dt,
        body_text=body_text,
    )
