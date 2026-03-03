"""Shared extraction core used by production scan and eval runner.

This module contains the single source of truth for:
- job-related classification
- status normalization
- company/title/req-id extraction
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from job_monitor.email.classifier import detect_non_job_reason, is_job_related
from job_monitor.extraction.llm import (
    LLMExtractionResult,
    LLMProvider,
    extract_with_timeout,
)
from job_monitor.extraction.rules import (
    extract_company,
    extract_job_req_id,
    extract_job_title,
    extract_status,
    normalize_req_id,
    split_title_and_req_id,
)

DecisionLogger = Callable[[str, str, str], None]
TitleValidator = Callable[[str], str]


@dataclass(frozen=True)
class CoreDecisionLogEntry:
    """Structured decision log line for optional tracing."""

    stage: str
    message: str
    level: str = "info"


@dataclass(frozen=True)
class CoreClassificationResult:
    """Classification-stage outputs."""

    is_trackable_job: bool
    predicted_email_category: Optional[str]
    non_job_reason: Optional[str]
    llm_result: Optional[LLMExtractionResult]
    llm_used: bool
    is_recruiter_reach_out: bool = False
    is_onboarding: bool = False
    is_oa: bool = False


@dataclass(frozen=True)
class CoreExtractionResult:
    """Field extraction outputs."""

    company: str
    job_title: str
    req_id: str
    status: str
    confidence: Optional[float] = None


@dataclass(frozen=True)
class CorePrediction:
    """Combined classification + extraction outputs."""

    classification: CoreClassificationResult
    extraction: Optional[CoreExtractionResult]


def _emit(
    decision_logger: Optional[DecisionLogger],
    stage: str,
    message: str,
    level: str = "info",
) -> None:
    if decision_logger is None:
        return
    decision_logger(stage, message, level)


def derive_predicted_email_category(
    llm_result: Optional[LLMExtractionResult],
    is_trackable_job: bool,
) -> Optional[str]:
    """Derive category in one place for prod/eval consistency."""
    if llm_result and llm_result.email_category:
        return llm_result.email_category
    if is_trackable_job:
        return "job_application"
    return "not_job_related"


def _status_flags(status: str) -> tuple[bool, bool, bool]:
    normalized_status = (status or "").strip().lower().replace("_", " ")
    is_recruiter_reach_out = normalized_status in {
        "recruiter reach-out",
        "recruiter reach out",
    }
    is_onboarding = normalized_status in {
        "onboarding",
        "background check",
        "background screening",
    }
    is_oa = normalized_status in {
        "oa",
        "online assessment",
        "online assessemnt",
        "online test",
        "coding challenge",
        "assessment",
        "take-home",
        "take home",
        "hackerrank",
        "codesignal",
        "codility",
    }
    return is_recruiter_reach_out, is_onboarding, is_oa


def run_core_classification_and_extraction(
    *,
    sender: str,
    subject: str,
    body: str,
    llm_provider: Optional[LLMProvider],
    llm_timeout_sec: int,
    validate_job_title: TitleValidator,
    decision_logger: Optional[DecisionLogger] = None,
    llm_provider_label: Optional[str] = None,
) -> CorePrediction:
    """Run shared classification + extraction logic without persistence side effects."""
    llm_result: Optional[LLMExtractionResult] = None
    llm_used = llm_provider is not None
    non_job_reason = detect_non_job_reason(sender, subject, body)

    if non_job_reason:
        _emit(decision_logger, "classification", "═══ Stage 0: Non-job hard rules ═══")
        _emit(
            decision_logger,
            "classification",
            f"Hard rule matched: non_job_reason={non_job_reason!r} "
            f"(sender={sender[:120]!r}, subject={subject[:120]!r})",
            "warn",
        )
        _emit(decision_logger, "classification", "→ Not job-related — field extraction skipped", "warn")
        classification = CoreClassificationResult(
            is_trackable_job=False,
            predicted_email_category="not_job_related",
            non_job_reason=non_job_reason,
            llm_result=None,
            llm_used=False,
        )
        return CorePrediction(classification=classification, extraction=None)

    # Stage 1: LLM extraction
    if llm_provider is not None:
        if llm_provider_label:
            _emit(decision_logger, "llm", f"LLM enabled: {llm_provider_label}")
        else:
            _emit(decision_logger, "llm", "LLM enabled")
        try:
            llm_result = extract_with_timeout(
                llm_provider, sender, subject, body, timeout_sec=llm_timeout_sec
            )
            _emit(
                decision_logger,
                "llm",
                f"is_job={llm_result.is_job_application}  category={llm_result.email_category!r}  "
                f"company={llm_result.company!r}  title={llm_result.job_title!r}  "
                f"req_id={llm_result.req_id!r}  "
                f"status={llm_result.status!r}  confidence={llm_result.confidence}  "
                f"tokens={llm_result.prompt_tokens}+{llm_result.completion_tokens}",
            )
        except Exception as llm_exc:
            _emit(decision_logger, "llm", f"LLM failed: {llm_exc} — falling back to rules", "error")
            llm_result = None
    else:
        _emit(decision_logger, "llm", "LLM disabled — rule-based pipeline only", "info")

    # Stage 2: Classification
    _emit(decision_logger, "classification", "═══ Stage 2: Classification ═══")
    is_recruiter_reach_out = False
    is_onboarding = False
    is_oa = False
    if llm_result is not None:
        is_recruiter_reach_out, is_onboarding, is_oa = _status_flags(llm_result.status)
        has_role_signal = bool((llm_result.base_title or llm_result.job_title).strip())
        has_company_signal = bool((llm_result.company or "").strip())
        # Exception: genuine recruiter outreach is trackable when it carries at least
        # one concrete job signal (role/company). Hard non-job rules still run first.
        recruiter_outreach_trackable = is_recruiter_reach_out and (has_role_signal or has_company_signal)
        pred_is_job = llm_result.is_job_application or recruiter_outreach_trackable
        _emit(
            decision_logger,
            "classification",
            f"LLM result: is_job_application={llm_result.is_job_application} "
            f"email_category={llm_result.email_category!r} "
            f"recruiter_outreach_trackable={recruiter_outreach_trackable} "
            f"trackable={pred_is_job} "
            f"(confidence={llm_result.confidence:.2f})",
            "success" if pred_is_job else "warn",
        )
    else:
        pred_is_job = is_job_related(subject, sender, body)
        _emit(
            decision_logger,
            "classification",
            f"Rule-based: is_job_related={pred_is_job} (subject: {subject[:80]!r})",
            "success" if pred_is_job else "warn",
        )

    classification = CoreClassificationResult(
        is_trackable_job=pred_is_job,
        predicted_email_category=derive_predicted_email_category(llm_result, pred_is_job),
        non_job_reason=None,
        llm_result=llm_result,
        llm_used=llm_used,
        is_recruiter_reach_out=is_recruiter_reach_out,
        is_onboarding=is_onboarding,
        is_oa=is_oa,
    )

    if not pred_is_job:
        _emit(decision_logger, "classification", "→ Not job-related — field extraction skipped", "warn")
        return CorePrediction(classification=classification, extraction=None)

    # Stage 3: Field extraction
    _emit(decision_logger, "company", "═══ Stage 3: Company ═══")
    _emit(decision_logger, "title", "═══ Stage 3: Title ═══")
    _emit(decision_logger, "req_id", "═══ Stage 3: Req ID ═══")
    _emit(decision_logger, "status", "═══ Stage 3: Status ═══")

    if llm_result is not None:
        company = (llm_result.company or "").strip() or "Unknown"
        raw_title = validate_job_title(llm_result.base_title or llm_result.job_title)
        base_title, req_from_title = split_title_and_req_id(raw_title)
        req_id = normalize_req_id(llm_result.req_id) or normalize_req_id(req_from_title)
        if not base_title:
            llm_title_with_req = validate_job_title(llm_result.title_with_req_id)
            base_title, req_from_title_with_req = split_title_and_req_id(llm_title_with_req)
            if not req_id:
                req_id = normalize_req_id(req_from_title_with_req)
        job_title = base_title
        if is_recruiter_reach_out:
            status = "Recruiter Reach-out"
            _emit(decision_logger, "status", "LLM status recruiter reach-out -> Recruiter Reach-out", "success")
        elif is_oa:
            status = "OA"
            _emit(decision_logger, "status", "LLM status OA-like -> OA", "success")
        elif is_onboarding:
            status = "Onboarding"
            _emit(decision_logger, "status", "LLM status onboarding-like -> Onboarding", "success")
        else:
            llm_status = llm_result.status
            if llm_status and llm_status.lower() != "unknown":
                status = llm_status
                _emit(decision_logger, "status", f"LLM: {status!r}", "success")
            else:
                status = "Unknown"
                _emit(decision_logger, "status", "LLM returned unknown", "warn")
        confidence = llm_result.confidence
        _emit(decision_logger, "company", f"LLM: {company!r}", "success" if company else "warn")
        _emit(decision_logger, "title", f"LLM: {job_title!r}", "success" if job_title else "warn")
        _emit(decision_logger, "req_id", f"LLM: {req_id!r}", "success" if req_id else "warn")
    else:
        company = extract_company(subject, sender)
        raw_title = validate_job_title(extract_job_title(subject, body))
        base_title, req_from_title = split_title_and_req_id(raw_title)
        req_id = normalize_req_id(extract_job_req_id(subject, body, base_title) or req_from_title)
        job_title = base_title
        status = extract_status(subject, body)
        confidence = None
        _emit(
            decision_logger,
            "company",
            f"Rules: {company!r}",
            "success" if company and company != "Unknown" else "warn",
        )
        _emit(decision_logger, "title", f"Rules: {job_title!r}", "success" if job_title else "warn")
        _emit(decision_logger, "req_id", f"Rules: {req_id!r}", "success" if req_id else "warn")
        _emit(decision_logger, "status", f"Rules: {status!r}", "success")

    extraction = CoreExtractionResult(
        company=company or "Unknown",
        job_title=job_title,
        req_id=req_id,
        status=status,
        confidence=confidence,
    )
    return CorePrediction(classification=classification, extraction=extraction)
