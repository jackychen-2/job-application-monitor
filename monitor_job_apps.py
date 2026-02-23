#!/usr/bin/env python3
"""
Monitor job-application related emails and update a macOS Numbers (.numbers) tracker.

Expected env vars:
  IMAP_HOST
  IMAP_PORT (optional, defaults to 993)
  EMAIL_USERNAME
  EMAIL_PASSWORD
  EMAIL_FOLDER (optional, defaults to "INBOX")
  NUMBERS_PATH (optional, defaults to "job_application_tracker.numbers")
  STATE_PATH (optional, defaults to ".job_monitor_state.json")
  MAX_SCAN_EMAILS (optional, defaults to 20)
  IMAP_TIMEOUT_SEC (optional, defaults to 30)
  LLM_ENABLED (optional, defaults to true)
  OPENAI_API_KEY (required when LLM_ENABLED=true)
  LLM_MODEL (optional, defaults to "gpt-5-mini")
  LLM_TIMEOUT_SEC (optional, defaults to 45)
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
import socket
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from bs4 import BeautifulSoup
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from dotenv import load_dotenv
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


HEADERS = [
    "Company",
    "Email Title",
    "Job Title",
    "Date (PT)",
    "Status",
]


@dataclass
class Config:
    imap_host: str
    imap_port: int
    username: str
    password: str
    folder: str
    numbers_path: Path
    state_path: Path
    max_scan_emails: int
    imap_timeout_sec: int
    llm_enabled: bool
    llm_model: str
    llm_timeout_sec: int
    openai_api_key: str
    cost_input_per_mtok: float
    cost_output_per_mtok: float


@dataclass
class ParsedEmail:
    uid: int
    company: str
    subject: str
    job_title: str
    email_date: str
    status: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float


JOB_SIGNAL_KEYWORDS = [
    "application",
    "applied",
    "thank you for applying",
    "interview",
    "recruiter",
    "hiring",
    "position",
    "job",
    "career",
    "投递",
    "申请",
    "应聘",
    "职位",
    "岗位",
]


COMPANY_PATTERNS = [
    r"^\s*([A-Za-z0-9&.'\- ]{2,50})\s*[-:|]",
    r"[-:|]\s*([A-Za-z0-9&.'\- ]{2,50})\s*(?:application|job|position|role|careers?)\b",
    r"\b(?:at|to)\s+([A-Za-z0-9&.,'\- ]{2,60})(?:\s+(?:has|for|about|on)\b|$)",
    r"\bfrom\s+([A-Za-z0-9&.,'\- ]{2,60})(?:\s+(?:has|for|about|on)\b|$)",
    r"加入\s*([^\s,，。!！?？]{2,30})",
    r"来自\s*([^\s,，。!！?？]{2,30})",
    r"【([^】]{2,40})】",
]


JOB_TITLE_PATTERNS = [
    r"(?:(?<=^)|(?<=\s))(?:position|role|title)\s*[:：\-]\s*([^\n\r]{2,100})",
    r"\bfor\s+(?:the\s+)?([A-Za-z0-9 /&,+.#()\-]{2,90})\s+(?:position|role)\b",
    r"(?:applied|application)\s*(?:for|to)\s*([A-Za-z0-9 /&,+.#()\-]{2,90})\s+at\s+[A-Za-z0-9&.,'\- ]+",
    r"(?:applied|application)\s*(?:for|to)\s*([A-Za-z0-9 /&,+.#()\-]{2,90})",
    r"for the\s+([A-Za-z0-9 /&,+.#()\-]{2,90})\s+position",
    r"for our\s+([A-Za-z0-9 /&,+.#()\-]{2,90})\s+role",
    r"(?:职位|岗位)\s*[:：]\s*([^\n\r]{2,80})",
    r"申请(?:的)?\s*([^\n\r，。]{2,80})(?:职位|岗位)",
]


def decode_mime_text(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def clean_extracted_text(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip(" \t\r\n-:;,.，。")
    if len(value) > 90:
        value = value[:90].rstrip()
    return value


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_date_to_pt(date_raw: str) -> str:
    if not date_raw:
        return ""
    try:
        dt = parsedate_to_datetime(date_raw)
        # America/Los_Angeles covers PST/PDT automatically.
        pt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
        return pt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return date_raw


def extract_company_from_subject(subject: str) -> str:
    lowered = subject.lower()
    junk_subject_markers = [
        "and ",
        " more jobs",
        "new jobs",
        "job alert",
        "jobs you may be interested in",
        "推荐职位",
    ]
    if any(marker in lowered for marker in junk_subject_markers):
        return ""
    for pattern in COMPANY_PATTERNS:
        matched = re.search(pattern, subject, flags=re.IGNORECASE)
        if matched:
            company = clean_extracted_text(matched.group(1))
            company = re.sub(r"\b(team|careers?|jobs?)\b$", "", company, flags=re.IGNORECASE).strip()
            company = re.sub(r"\b(application|applied|position|role)\b.*$", "", company, flags=re.IGNORECASE).strip()
            if company.lower() in {"thank you", "application received", "application", "job"}:
                continue
            return company
    return ""


def extract_body_text(msg: Message) -> str:
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
                soup = BeautifulSoup(text, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                text = soup.get_text("\n", strip=True)
                html_parts.append(text)
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
            soup = BeautifulSoup(text, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            html_parts.append(soup.get_text("\n", strip=True))
        else:
            plain_parts.append(text)

    plain_text = "\n".join(plain_parts)
    html_text = "\n".join(html_parts)
    if plain_text and not is_noise_text(plain_text):
        return plain_text
    return html_text if html_text else plain_text


def is_noise_text(text: str) -> bool:
    lowered = text.lower()
    noise_tokens = [
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
    hit_count = sum(1 for tok in noise_tokens if tok in lowered)
    return hit_count >= 2


def clean_title_candidate(text: str) -> str:
    value = clean_extracted_text(text)
    value = re.sub(r"\s*\|\s*.*$", "", value)
    value = re.sub(r"\s*-\s*(application|applied|confirmation|received).*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(application|confirmation|received)$", "", value, flags=re.IGNORECASE)
    return value.strip()


def extract_job_title(subject: str, body: str) -> str:
    source_subject_first = f"{subject}\n{body}"
    for pattern in JOB_TITLE_PATTERNS:
        matched = re.search(pattern, source_subject_first, flags=re.IGNORECASE)
        if matched:
            title = clean_title_candidate(matched.group(1))
            title = re.sub(
                r"\b(application|submitted|received|confirmation|thank you|thanks)\b.*$",
                "",
                title,
                flags=re.IGNORECASE,
            ).strip()
            if title and not is_noise_text(title):
                return title

    subject_patterns = [
        r"application for\s+([A-Za-z0-9 /&,+.#()\-]{2,90})",
        r"applied to\s+([A-Za-z0-9 /&,+.#()\-]{2,90})",
        r"for\s+(?:the\s+)?([A-Za-z0-9 /&,+.#()\-]{2,90})\s+(?:position|role)",
    ]
    for pattern in subject_patterns:
        matched = re.search(pattern, subject, flags=re.IGNORECASE)
        if matched:
            title = clean_title_candidate(matched.group(1))
            if title and not is_noise_text(title):
                return title

    for line in body.splitlines():
        line = normalize_space(line)
        if len(line) < 4 or len(line) > 120 or is_noise_text(line):
            continue
        for pattern in JOB_TITLE_PATTERNS:
            matched = re.search(pattern, line, flags=re.IGNORECASE)
            if matched:
                title = clean_title_candidate(matched.group(1))
                if title and not is_noise_text(title):
                    return title
        if re.match(r"^[A-Za-z][A-Za-z0-9 /&,+.#()\-]{3,80}$", line):
            if any(k in line.lower() for k in ["engineer", "developer", "manager", "analyst", "scientist", "designer"]):
                return clean_title_candidate(line)

    # Subject fallback: often "Company - Role" or "Role at Company"
    subject_role_patterns = [
        r"^[^\-|:]{2,60}\s*-\s*([^\-|:]{2,90})$",
        r"^([^\-|:]{2,90})\s+at\s+[^\-|:]{2,60}$",
    ]
    for pattern in subject_role_patterns:
        matched = re.search(pattern, subject, flags=re.IGNORECASE)
        if matched:
            title = clean_title_candidate(matched.group(1))
            if title and not is_noise_text(title):
                return title
    return ""


def infer_company(sender: str) -> str:
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
        base = pieces[-2]
        return base.replace("-", " ").title()
    return stripped.replace("-", " ").title()


def looks_job_related(subject: str) -> bool:
    searchable = subject.lower()
    return any(token in searchable for token in JOB_SIGNAL_KEYWORDS)


def load_config() -> Config:
    load_dotenv()
    required = {
        "IMAP_HOST": os.getenv("IMAP_HOST", "").strip(),
        "EMAIL_USERNAME": os.getenv("EMAIL_USERNAME", "").strip(),
        "EMAIL_PASSWORD": os.getenv("EMAIL_PASSWORD", "").strip(),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    llm_enabled = os.getenv("LLM_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if llm_enabled and not openai_api_key:
        print("Warning: LLM_ENABLED=true but OPENAI_API_KEY is missing; fallback to rules only.")
        llm_enabled = False
    return Config(
        imap_host=required["IMAP_HOST"],
        imap_port=int(os.getenv("IMAP_PORT", "993")),
        username=required["EMAIL_USERNAME"],
        password=required["EMAIL_PASSWORD"],
        folder=os.getenv("EMAIL_FOLDER", "INBOX"),
        numbers_path=Path(os.getenv("NUMBERS_PATH", "job_application_tracker.numbers")),
        state_path=Path(os.getenv("STATE_PATH", ".job_monitor_state.json")),
        max_scan_emails=int(os.getenv("MAX_SCAN_EMAILS", "20")),
        imap_timeout_sec=int(os.getenv("IMAP_TIMEOUT_SEC", "30")),
        llm_enabled=llm_enabled,
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        llm_timeout_sec=int(os.getenv("LLM_TIMEOUT_SEC", "45")),
        openai_api_key=openai_api_key,
        cost_input_per_mtok=float(os.getenv("COST_INPUT_PER_MTOK", "0.25")),
        cost_output_per_mtok=float(os.getenv("COST_OUTPUT_PER_MTOK", "2.0")),
    )


def llm_extract_fields(cfg: Config, sender: str, subject: str, body: str) -> Dict[str, str]:
    if not cfg.llm_enabled:
        return {}
    if OpenAI is None:
        raise RuntimeError("LLM enabled but openai package is not installed")

    # Fail fast on flaky network: no SDK retries, strict request timeout.
    client = OpenAI(
        api_key=cfg.openai_api_key,
        timeout=cfg.llm_timeout_sec,
        max_retries=0,
    )
    body_snippet = body[:8000]
    system_prompt = (
        "You extract job-application email fields. "
        "Return strict JSON only with keys: is_job_application, company, job_title, status, confidence. "
        "Rules: company should be the real hiring company, not ATS vendor if possible. "
        "status must be one of: 已申请, 面试, 拒绝, Offer, Unknown. "
        "If uncertain, use empty string for company/job_title and confidence <= 0.5."
    )
    user_prompt = (
        f"Sender: {sender}\n"
        f"Subject: {subject}\n"
        f"Body:\n{body_snippet}\n"
        "Return JSON."
    )

    resp = client.chat.completions.create(
        model=cfg.llm_model,
        timeout=cfg.llm_timeout_sec,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = (resp.choices[0].message.content or "").strip()
    parsed = json.loads(content) if content else {}
    usage = getattr(resp, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    estimated_cost = (
        (prompt_tokens / 1_000_000.0) * cfg.cost_input_per_mtok
        + (completion_tokens / 1_000_000.0) * cfg.cost_output_per_mtok
    )
    return {
        "is_job_application": str(parsed.get("is_job_application", "")).strip(),
        "company": str(parsed.get("company", "")).strip(),
        "job_title": str(parsed.get("job_title", "")).strip(),
        "status": str(parsed.get("status", "")).strip(),
        "confidence": str(parsed.get("confidence", "")).strip(),
        "prompt_tokens": str(prompt_tokens),
        "completion_tokens": str(completion_tokens),
        "estimated_cost_usd": f"{estimated_cost:.8f}",
    }


def llm_extract_fields_with_hard_timeout(
    cfg: Config, sender: str, subject: str, body: str
) -> Dict[str, str]:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(llm_extract_fields, cfg, sender, subject, body)
    try:
        return future.result(timeout=cfg.llm_timeout_sec)
    except FuturesTimeoutError:
        future.cancel()
        raise RuntimeError(f"LLM request hard-timeout after {cfg.llm_timeout_sec}s")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def read_state(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {"last_uid": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"last_uid": int(data.get("last_uid", 0))}
    except Exception:
        return {"last_uid": 0}


def write_state(path: Path, last_uid: int) -> None:
    payload = {"last_uid": last_uid, "updated_at": datetime.utcnow().isoformat()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_osascript(script: str, args: List[str]) -> None:
    proc = subprocess.run(
        ["osascript", "-", *args],
        input=script,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip() or "unknown osascript error"
        raise RuntimeError(f"Numbers write failed: {msg}")


def ensure_numbers_document(path: Path) -> None:
    script = """
on run argv
    set filePath to item 1 of argv
    set fileExists to do shell script "test -f " & quoted form of filePath & "; echo $?"
    if fileExists is "0" then
        return
    end if
    tell application "Numbers"
        set d to make new document
        tell table 1 of sheet 1 of d
            set value of cell 1 of row 1 to "Company"
            set value of cell 2 of row 1 to "Email Title"
            set value of cell 3 of row 1 to "Job Title"
            set value of cell 4 of row 1 to "Date (PT)"
            set value of cell 5 of row 1 to "Status"
        end tell
        save d in POSIX file filePath
        close d saving yes
    end tell
end run
"""
    run_osascript(script, [str(path.expanduser().resolve())])


def append_rows_to_numbers(path: Path, items: List[ParsedEmail]) -> None:
    ensure_numbers_document(path)
    if not items:
        return
    args: List[str] = [str(path.expanduser().resolve()), str(len(items))]
    for item in items:
        args.extend(
            [
                (item.company or "").replace("\t", " ").replace("\n", " "),
                (item.subject or "").replace("\t", " ").replace("\n", " "),
                (item.job_title or "").replace("\t", " ").replace("\n", " "),
                (item.email_date or "").replace("\t", " ").replace("\n", " "),
                (item.status or "").replace("\t", " ").replace("\n", " "),
            ]
        )
    script = """
on run argv
    set filePath to item 1 of argv
    set rowCount to (item 2 of argv) as integer

    tell application "Numbers"
        open POSIX file filePath
        delay 0.2
        set d to front document
        tell table 1 of sheet 1 of d
            repeat with i from 1 to rowCount
                set baseIndex to 2 + ((i - 1) * 5)
                set companyValue to item (baseIndex + 1) of argv
                set subjectValue to item (baseIndex + 2) of argv
                set titleValue to item (baseIndex + 3) of argv
                set dateValue to item (baseIndex + 4) of argv
                set statusValue to item (baseIndex + 5) of argv
                set targetRow to 2
                repeat
                    if targetRow > row count then
                        add row below last row
                    end if
                    if value of cell 1 of row targetRow is missing value then
                        exit repeat
                    end if
                    set targetRow to targetRow + 1
                end repeat

                set value of cell 1 of row targetRow to companyValue
                set value of cell 2 of row targetRow to subjectValue
                set value of cell 3 of row targetRow to titleValue
                set value of cell 4 of row targetRow to dateValue
                set value of cell 5 of row targetRow to statusValue
            end repeat
        end tell
        save d
        close d saving yes
    end tell
end run
"""
    run_osascript(script, args)


def parse_email(cfg: Config, uid: int, msg: Message) -> Optional[ParsedEmail]:
    sender = decode_mime_text(msg.get("From", ""))
    subject = decode_mime_text(msg.get("Subject", ""))
    date_raw = decode_mime_text(msg.get("Date", ""))
    body_text = extract_body_text(msg)
    email_date = normalize_date_to_pt(date_raw)
    llm_fields: Dict[str, str] = {}
    used_llm = False
    prompt_tokens = 0
    completion_tokens = 0
    estimated_cost_usd = 0.0

    if not cfg.llm_enabled and not looks_job_related(subject=subject):
        print(f"  uid={uid}: skipped by keyword rules")
        return None

    try:
        if cfg.llm_enabled:
            used_llm = True
            print(f"  uid={uid}: calling LLM ...")
        llm_fields = llm_extract_fields_with_hard_timeout(
            cfg, sender=sender, subject=subject, body=body_text
        )
    except Exception as exc:
        print(f"LLM fallback to rules for uid={uid}: {exc}")
        llm_fields = {}

    if llm_fields:
        is_job = llm_fields.get("is_job_application", "").lower() in {"true", "1", "yes"}
        if not is_job:
            print(f"  uid={uid}: LLM judged non-job email")
            return None
        company = clean_extracted_text(llm_fields.get("company", "")) or extract_company_from_subject(subject) or "Unknown"
        job_title = clean_title_candidate(llm_fields.get("job_title", "")) or extract_job_title(subject, body_text)
        status = llm_fields.get("status", "").strip() or "已申请"
        prompt_tokens = int(llm_fields.get("prompt_tokens", "0") or 0)
        completion_tokens = int(llm_fields.get("completion_tokens", "0") or 0)
        estimated_cost_usd = float(llm_fields.get("estimated_cost_usd", "0") or 0)
    else:
        if not looks_job_related(subject=subject):
            if used_llm:
                print(f"  uid={uid}: fallback rules -> non-job email")
            return None
        company = extract_company_from_subject(subject) or "Unknown"
        job_title = extract_job_title(subject, body_text)
        status = "已申请"

    return ParsedEmail(
        uid=uid,
        company=company,
        subject=subject,
        job_title=job_title,
        email_date=email_date,
        status=status,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )


def fetch_new_emails(cfg: Config, after_uid: int) -> Tuple[List[ParsedEmail], int]:
    socket.setdefaulttimeout(cfg.imap_timeout_sec)
    print(f"Connecting to {cfg.imap_host}:{cfg.imap_port} ...")
    mail = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        print("Logging in ...")
        mail.login(cfg.username, cfg.password)
        status, _ = mail.select(cfg.folder)
        if status != "OK":
            raise RuntimeError(f"Cannot select folder: {cfg.folder}")
        print(f"Selected folder: {cfg.folder}")

        status, data = mail.uid("SEARCH", None, f"UID {after_uid + 1}:*")
        if status != "OK":
            raise RuntimeError("Failed to search mailbox")
        uid_tokens = (data[0] or b"").split()
        if len(uid_tokens) > cfg.max_scan_emails:
            uid_tokens = uid_tokens[-cfg.max_scan_emails :]
            print(f"Limiting scan to latest {cfg.max_scan_emails} messages.")
        print(f"Candidate email count: {len(uid_tokens)}")

        results: List[ParsedEmail] = []
        max_uid = after_uid

        for idx, uid_bytes in enumerate(uid_tokens, start=1):
            uid = int(uid_bytes)
            print(f"[{idx}/{len(uid_tokens)}] processing uid={uid}")
            max_uid = max(max_uid, uid)
            f_status, fetched = mail.uid("FETCH", str(uid), "(RFC822)")
            if f_status != "OK" or not fetched or fetched[0] is None:
                print(f"  uid={uid}: fetch failed")
                continue
            raw = fetched[0][1]
            if not raw:
                print(f"  uid={uid}: empty payload")
                continue
            msg = email.message_from_bytes(raw)
            parsed = parse_email(cfg, uid, msg)
            if parsed:
                results.append(parsed)
                print(f"  uid={uid}: matched, company={parsed.company}, title={parsed.job_title or '(empty)'}")
                if cfg.llm_enabled:
                    print(
                        f"  uid={uid}: tokens in/out={parsed.prompt_tokens}/{parsed.completion_tokens}, "
                        f"cost=${parsed.estimated_cost_usd:.8f}"
                    )
            else:
                print(f"  uid={uid}: not matched")

        return results, max_uid
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def main() -> int:
    try:
        cfg = load_config()
        state = read_state(cfg.state_path)
        current_uid = int(state.get("last_uid", 0))
        found, max_uid = fetch_new_emails(cfg, after_uid=current_uid)

        append_rows_to_numbers(cfg.numbers_path, found)

        write_state(cfg.state_path, max_uid)
        total_cost = sum(item.estimated_cost_usd for item in found)
        total_prompt = sum(item.prompt_tokens for item in found)
        total_completion = sum(item.completion_tokens for item in found)
        if not found:
            print("No job-related emails matched current rules. Numbers file kept with header only.")
        if cfg.llm_enabled:
            print(
                f"LLM usage total: prompt={total_prompt}, completion={total_completion}, "
                f"estimated_cost=${total_cost:.8f}"
            )
        print(
            f"Processed {len(found)} job-related email(s). "
            f"last_uid: {current_uid} -> {max_uid}. "
            f"Numbers: {cfg.numbers_path}"
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
