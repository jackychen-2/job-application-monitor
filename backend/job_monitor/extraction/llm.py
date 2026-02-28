"""LLM-based field extraction with provider abstraction."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from job_monitor.config import AppConfig
from job_monitor.extraction.rules import (
    compose_title_with_req_id,
    normalize_req_id,
    split_title_and_req_id,
)

logger = structlog.get_logger(__name__)


def _normalize_llm_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u200b", " ")).strip()


def _pick_more_specific_title(a: str, b: str) -> str:
    """Prefer the title that carries more concrete qualifiers."""
    ta = _normalize_llm_text(a)
    tb = _normalize_llm_text(b)
    if not ta:
        return tb
    if not tb:
        return ta
    la = ta.lower()
    lb = tb.lower()
    if la in lb and len(tb) > len(ta):
        return tb
    if lb in la and len(ta) > len(tb):
        return ta

    def _score(v: str) -> tuple[int, int]:
        has_qualifier = 1 if any(tok in v for tok in (" - ", ",", "/", "(")) else 0
        return (has_qualifier, len(v))

    return ta if _score(ta) >= _score(tb) else tb


@dataclass(frozen=True)
class LLMExtractionResult:
    """Structured output from an LLM extraction call."""

    is_job_application: bool = False
    # Two-way classification: "job_application" | "not_job_related" | ""
    email_category: str = ""
    company: str = ""
    job_title: str = ""
    base_title: str = ""
    req_id: str = ""
    title_with_req_id: str = ""
    status: str = ""
    confidence: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass(frozen=True)
class LLMLinkConfirmResult:
    """Result from an LLM link-confirmation call."""

    is_same_application: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0


class LLMProvider(Protocol):
    """Protocol that any LLM provider must implement."""

    def extract_fields(
        self, sender: str, subject: str, body: str
    ) -> LLMExtractionResult: ...

    def confirm_same_application(
        self,
        email_subject: str,
        email_sender: str,
        email_body: str,
        app_company: str,
        app_job_title: str,
        app_status: str,
        app_last_email_subject: str,
        new_email_date: str = "",
        app_created_at: str = "",
        app_last_email_date: str = "",
        days_since_last_email: int | None = None,
        recent_events: list[dict[str, str]] | None = None,
    ) -> LLMLinkConfirmResult: ...


# ── OpenAI Provider ───────────────────────────────────────


class OpenAIProvider:
    """OpenAI-backed LLM extraction (GPT-4o-mini, GPT-4o, etc.)."""

    def __init__(self, config: AppConfig) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required when LLM is enabled — pip install openai"
            ) from exc

        self._config = config
        self._client = OpenAI(
            api_key=config.llm_api_key.get_secret_value(),
            timeout=config.llm_timeout_sec,
            max_retries=0,  # We handle retries at a higher level
        )

    _SYSTEM_PROMPT = (
        "You classify an email for a job-tracking system and extract structured fields. "
        "Return strict JSON only with keys: is_job_application, email_category, company, "
        "job_title, base_title, req_id, title_with_req_id, status, confidence. "
        "\n\n"
        "IMPORTANT: is_job_application=true ONLY if the user actually applied for a job and this email "
        "is a confirmation, acknowledgment, status update, OA/assessment invite, interview invite, offer/rejection, or post-offer onboarding communication. "
        "\n"
        "is_job_application=false for: "
        "account verification emails, password resets, marketing newsletters, career tips, "
        "job alert digests ('we found jobs for you', 'new jobs posted', 'jobs matching your profile'), "
        "talent community notifications, job recommendation emails listing multiple open positions, "
        "application summary digests (emails summarizing multiple application statuses), "
        "promotional emails, unsubscribe confirmations, "
        "general company newsletters even if from a careers/talent team, "
        "recruiter or TA proactively reaching out about a role (the user did NOT apply — set status='Recruiter Reach-out'). "
        "If the email lists multiple job openings or says 'Your Job Alert matched the following jobs', "
        "it is a job alert digest, NOT an application — return is_job_application=false. "
        "\n\n"
        "- email_category: REQUIRED — classify into exactly one of:\n"
        "  * 'job_application'     — user submitted an application; email is a confirmation, "
        "acknowledgment, status update, OA/assessment invite, interview invite, offer, rejection, or onboarding/background-check communication\n"
        "  * 'not_job_related'     — not about a specific job application at all "
        "(newsletter, verification code, job alert digest, marketing, etc.)\n"
        "  email_category='job_application' if and only if is_job_application=true.\n"
        "  recruiter outreach should still use email_category='not_job_related' and set "
        "status='Recruiter Reach-out'.\n"
        "\n"
        "Rules:\n"
        "- company: the real hiring company name, not ATS vendor.\n"
        "  COMPANY NAME RULES (important for consistent grouping):\n"
        "  * Use the well-known brand name, not the legal entity name.\n"
        "    'Adobe' not 'Adobe Systems Incorporated', 'Google' not 'Google LLC', "
        "'Zoom' not 'Zoom Communications'.\n"
        "  * Do NOT use ATS platform names (Greenhouse, Workday, Lever, iCIMS) as the company.\n"
        "  * Strip personal address prefixes only: 'Your Zoom' → 'Zoom', "
        "'Welcome to Google' → 'Google'.\n"
        "- job_title: a specific role name (e.g., 'Software Engineer', 'Product Manager'). "
        "Extract from the email body first, then subject as fallback. "
        "Look for patterns like 'application for the ... position', 'interest in the ... position', 'applying for ... role'. "
        "Include team/department qualifiers and job IDs when present to distinguish roles at the same company "
        "(e.g. 'Software Engineer, Payments Infrastructure (ID: 12345)'). "
        "Do NOT use sentences or phrases from email body. Return empty string only if truly not found anywhere.\n"
        "  TITLE COMPLETENESS RULES (critical):\n"
        "  * If body has explicit labels like 'Position:', 'Job Title:', or 'Role:', copy the value exactly.\n"
        "  * Keep full specialization suffixes; do NOT shorten 'A - B' to 'A'.\n"
        "  * If subject is generic but body title is specific, always choose body title.\n"
        "- base_title: title without requisition ID (e.g. 'Data Engineer').\n"
        "- req_id: requisition ID if present (e.g. 'R0615432', 'JR299365', '1841261', '2025-4844').\n"
        "- title_with_req_id: combine base_title + req_id when req_id exists "
        "(e.g. 'Data Engineer - R0615432'). If no req_id, this equals base_title.\n"
        "- status: infer from BOTH email subject AND body. Must be one of:\n"
        "  * 'Recruiter Reach-out' - recruiter/TA proactively reached out about a role and the user has not applied yet\n"
        "  * 'OA' - online assessment, coding challenge, take-home test, HackerRank/CodeSignal/Codility\n"
        "  * 'Onboarding' - after offer acceptance: background check, I-9/E-Verify, payroll/benefits setup, onboarding tasks\n"
        "  * 'Offer' - offer letter, congratulations\n"
        "  * '面试' - interview, phone screen, onsite\n"
        "  * '拒绝' - rejection ('unfortunately', 'regret', 'not moving forward')\n"
        "  * '已申请' - application received/confirmed\n"
        "  * 'Unknown' - only if truly unclear\n"
        "- confidence: <= 0.5 if uncertain."
    )

    def extract_fields(
        self, sender: str, subject: str, body: str
    ) -> LLMExtractionResult:
        cfg = self._config
        body_clean = _normalize_llm_text(body)
        body_snippet = body_clean[:8000]

        user_prompt = (
            f"Sender: {sender}\nSubject: {subject}\nBody:\n{body_snippet}\nReturn JSON."
        )

        resp = self._client.chat.completions.create(
            model=cfg.llm_model,
            timeout=cfg.llm_timeout_sec,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self._SYSTEM_PROMPT},
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

        # Parse email_category first; derive is_job_application from it when present.
        _VALID_CATEGORIES = {"job_application", "not_job_related"}
        email_category = str(parsed.get("email_category", "")).strip().lower()
        if email_category not in _VALID_CATEGORIES:
            email_category = ""

        if email_category == "job_application":
            is_job = True
        elif email_category == "not_job_related":
            is_job = False
        else:
            # Fallback: use explicit is_job_application field
            is_job_raw = str(parsed.get("is_job_application", "")).strip().lower()
            is_job = is_job_raw in {"true", "1", "yes"}
            email_category = "job_application" if is_job else "not_job_related"

        confidence_raw = parsed.get("confidence", 0)
        try:
            confidence = float(confidence_raw)
        except (ValueError, TypeError):
            confidence = 0.0

        raw_job_title = _normalize_llm_text(str(parsed.get("job_title", "")))
        raw_base_title = _normalize_llm_text(str(parsed.get("base_title", "")))
        raw_req_id = normalize_req_id(str(parsed.get("req_id", "")).strip())
        raw_title_with_req = _normalize_llm_text(str(parsed.get("title_with_req_id", "")))

        tw_base, tw_req = split_title_and_req_id(raw_title_with_req)
        jt_base, jt_req = split_title_and_req_id(raw_job_title)

        # Prefer the most specific non-req title, and avoid losing qualifiers.
        base_title = tw_base or _pick_more_specific_title(raw_base_title, jt_base or raw_job_title)
        req_id = raw_req_id or tw_req or jt_req
        if req_id:
            title_with_req_id = raw_title_with_req or compose_title_with_req_id(base_title, req_id)
            canonical_job_title = title_with_req_id or base_title
        else:
            canonical_job_title = _pick_more_specific_title(raw_job_title, base_title)
            title_with_req_id = canonical_job_title
            base_title = canonical_job_title

        return LLMExtractionResult(
            is_job_application=is_job,
            email_category=email_category,
            company=str(parsed.get("company", "")).strip(),
            job_title=canonical_job_title,
            base_title=base_title,
            req_id=req_id,
            title_with_req_id=title_with_req_id,
            status=str(parsed.get("status", "")).strip(),
            confidence=confidence,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost,
        )

    _LINK_CONFIRM_PROMPT = (
        "You are matching job application emails. Determine if a new email "
        "is about the SAME job application as an existing record, or a DIFFERENT one.\n\n"
        "SAME means: both refer to the same position at the same company "
        "(e.g., application confirmation followed by interview invite for the same role).\n"
        "DIFFERENT means: different position, different application cycle, "
        "or the email is unrelated to this specific application.\n\n"
        "Use timeline signals to judge application cycle continuity. "
        "If timeline suggests a new cycle (e.g., rejection followed by a fresh application later), "
        "prefer DIFFERENT.\n"
        "If requisition IDs are explicitly provided and equal, treat that as the strongest SAME signal.\n\n"
        "Answer ONLY with the word \"same\" or \"different\"."
    )

    def confirm_same_application(
        self,
        email_subject: str,
        email_sender: str,
        email_body: str,
        app_company: str,
        app_job_title: str,
        app_status: str,
        app_last_email_subject: str,
        new_email_date: str = "",
        app_created_at: str = "",
        app_last_email_date: str = "",
        days_since_last_email: int | None = None,
        recent_events: list[dict[str, str]] | None = None,
    ) -> LLMLinkConfirmResult:
        """Ask LLM whether a new email belongs to an existing application."""
        cfg = self._config
        body_snippet = (email_body or "")[:2000]
        _recent = recent_events or []
        recent_lines: list[str] = []
        for idx, event in enumerate(_recent[:5], start=1):
            edate = _normalize_llm_text(event.get("date", "")) or "(unknown)"
            estat = _normalize_llm_text(event.get("status", "")) or "(none)"
            esubj = _normalize_llm_text(event.get("subject", "")) or "(none)"
            recent_lines.append(f"{idx}. {edate} | status={estat} | subject=\"{esubj[:140]}\"")
        recent_events_block = "\n".join(recent_lines) if recent_lines else "(none)"
        days_label = str(days_since_last_email) if days_since_last_email is not None else "(unknown)"

        user_prompt = (
            f"Existing Application:\n"
            f"- Company: {app_company}\n"
            f"- Job Title: {app_job_title or '(unknown)'}\n"
            f"- Current Status: {app_status}\n"
            f"- Last Email Subject: \"{app_last_email_subject or '(none)'}\"\n\n"
            f"Timeline Summary:\n"
            f"- New Email Date: {new_email_date or '(unknown)'}\n"
            f"- Application Created At: {app_created_at or '(unknown)'}\n"
            f"- Application Last Email Date: {app_last_email_date or '(unknown)'}\n"
            f"- Days Since Last Email: {days_label}\n"
            f"- Recent Events (latest first):\n{recent_events_block}\n\n"
            f"New Email:\n"
            f"- Subject: \"{email_subject}\"\n"
            f"- From: {email_sender}\n"
            f"- Body:\n{body_snippet}\n\n"
            f"Is this new email about the SAME or a DIFFERENT job application?"
        )

        resp = self._client.chat.completions.create(
            model=cfg.llm_model,
            timeout=cfg.llm_timeout_sec,
            messages=[
                {"role": "system", "content": self._LINK_CONFIRM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )

        content = (resp.choices[0].message.content or "").strip().lower()
        is_same = "same" in content and "different" not in content

        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        estimated_cost = (
            (prompt_tokens / 1_000_000.0) * cfg.cost_input_per_mtok
            + (completion_tokens / 1_000_000.0) * cfg.cost_output_per_mtok
        )

        logger.info(
            "llm_link_confirm",
            is_same=is_same,
            raw_answer=content[:50],
            company=app_company,
            prompt_tokens=prompt_tokens,
        )

        return LLMLinkConfirmResult(
            is_same_application=is_same,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost,
        )


# ── Factory ───────────────────────────────────────────────

_PROVIDERS: dict[str, type] = {
    "openai": OpenAIProvider,
}


def create_llm_provider(config: AppConfig) -> LLMProvider:
    """Instantiate the configured LLM provider."""
    provider_cls = _PROVIDERS.get(config.llm_provider.lower())
    if provider_cls is None:
        raise ValueError(
            f"Unknown LLM provider: {config.llm_provider!r}. "
            f"Available: {', '.join(_PROVIDERS)}"
        )
    return provider_cls(config)


# ── Hard-timeout wrapper ──────────────────────────────────


def extract_with_timeout(
    provider: LLMProvider,
    sender: str,
    subject: str,
    body: str,
    timeout_sec: int = 45,
) -> LLMExtractionResult:
    """Call the LLM provider with a hard thread-based timeout.

    This guards against the SDK's own timeout being unreliable.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(provider.extract_fields, sender, subject, body)
    try:
        return future.result(timeout=timeout_sec)
    except FuturesTimeoutError:
        future.cancel()
        raise RuntimeError(f"LLM hard-timeout after {timeout_sec}s")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
