"""LLM-based field extraction with provider abstraction."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from job_monitor.config import AppConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LLMExtractionResult:
    """Structured output from an LLM extraction call."""

    is_job_application: bool = False
    company: str = ""
    job_title: str = ""
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
        "You determine if an email is about a REAL job application that the user actually submitted, "
        "and extract structured fields. "
        "Return strict JSON only with keys: is_job_application, company, job_title, status, confidence. "
        "\n\n"
        "IMPORTANT: is_job_application=true ONLY if the user actually applied for a job and this email "
        "is a confirmation, acknowledgment, status update, interview invite, or offer/rejection. "
        "\n"
        "is_job_application=false for: "
        "account verification emails, password resets, marketing newsletters, career tips, "
        "job alert digests ('we found jobs for you', 'new jobs posted', 'jobs matching your profile'), "
        "talent community notifications, job recommendation emails listing multiple open positions, "
        "application summary digests (emails summarizing multiple application statuses), "
        "promotional emails, unsubscribe confirmations, "
        "general company newsletters even if from a careers/talent team. "
        "If the email lists multiple job openings or says 'Your Job Alert matched the following jobs', "
        "it is a job alert digest, NOT an application — return is_job_application=false. "
        "\n\n"
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
        "Do NOT use sentences or phrases from email body. Return empty string if not found.\n"
        "- status: infer from BOTH email subject AND body. Must be one of:\n"
        "  * 'Offer' - offer letter, congratulations\n"
        "  * '面试' - interview, assessment, coding challenge\n"
        "  * '拒绝' - rejection ('unfortunately', 'regret', 'not moving forward')\n"
        "  * '已申请' - application received/confirmed\n"
        "  * 'Unknown' - only if truly unclear\n"
        "- confidence: <= 0.5 if uncertain."
    )

    def extract_fields(
        self, sender: str, subject: str, body: str
    ) -> LLMExtractionResult:
        cfg = self._config
        body_snippet = body[:8000]

        user_prompt = (
            f"Sender: {sender}\nSubject: {subject}\nBody:\n{body_snippet}\nReturn JSON."
        )

        resp = self._client.chat.completions.create(
            model=cfg.llm_model,
            timeout=cfg.llm_timeout_sec,
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

        is_job_raw = str(parsed.get("is_job_application", "")).strip().lower()
        is_job = is_job_raw in {"true", "1", "yes"}

        confidence_raw = parsed.get("confidence", 0)
        try:
            confidence = float(confidence_raw)
        except (ValueError, TypeError):
            confidence = 0.0

        return LLMExtractionResult(
            is_job_application=is_job,
            company=str(parsed.get("company", "")).strip(),
            job_title=str(parsed.get("job_title", "")).strip(),
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
    ) -> LLMLinkConfirmResult:
        """Ask LLM whether a new email belongs to an existing application."""
        cfg = self._config
        body_snippet = (email_body or "")[:2000]

        user_prompt = (
            f"Existing Application:\n"
            f"- Company: {app_company}\n"
            f"- Job Title: {app_job_title or '(unknown)'}\n"
            f"- Current Status: {app_status}\n"
            f"- Last Email Subject: \"{app_last_email_subject or '(none)'}\"\n\n"
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
