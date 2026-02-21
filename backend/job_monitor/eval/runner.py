"""Evaluation runner — replays cached emails through the pipeline and scores results."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

import structlog
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig
from job_monitor.email.classifier import is_job_related
from job_monitor.eval.cache import reparse_cached_email
from job_monitor.eval.metrics import (
    FullReport,
    compute_classification_metrics,
    compute_field_metrics,
    compute_grouping_metrics,
    compute_status_metrics,
)
from job_monitor.eval.models import (
    EvalPredictedGroup,
    CachedEmail,
    EvalLabel,
    EvalRun,
    EvalRunResult,
)
from job_monitor.extraction.llm import (
    LLMExtractionResult,
    LLMProvider,
    create_llm_provider,
    extract_with_timeout,
)
from job_monitor.extraction.rules import extract_company, extract_job_title, extract_status

logger = structlog.get_logger(__name__)


def _validate_job_title(title: str) -> str:
    """Re-use pipeline's title validation."""
    from job_monitor.extraction.pipeline import _validate_job_title as _vt
    return _vt(title)


def run_evaluation(
    config: AppConfig,
    session: Session,
    run_name: Optional[str] = None,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    cancel_token: Optional[threading.Event] = None,
) -> EvalRun:
    """Run the full pipeline on all cached emails and compute metrics against labels.

    Args:
        config: Application configuration.
        session: Database session.
        run_name: Optional name for this evaluation run.
        progress_cb: Optional callback called as ``(message, current, total)`` for each email processed.
        cancel_token: Optional threading.Event — when set, the run is aborted after the current email.
    """

    def _log(msg: str, current: int = 0, total: int = 0) -> None:
        logger.info("eval_progress", msg=msg, current=current, total=total)
        if progress_cb:
            try:
                progress_cb(msg, current, total)
            except Exception:
                pass

    # Create run record
    _log("Creating evaluation run record…")
    eval_run = EvalRun(
        run_name=run_name or f"Run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
        started_at=datetime.now(timezone.utc),
        config_snapshot=json.dumps({
            "llm_enabled": config.llm_enabled,
            "llm_model": config.llm_model if config.llm_enabled else None,
            "llm_provider": config.llm_provider if config.llm_enabled else None,
        }),
    )
    session.add(eval_run)
    session.flush()

    # Load all cached emails
    _log("Loading cached emails from database…")
    cached_emails = session.query(CachedEmail).order_by(CachedEmail.email_date).all()
    eval_run.total_emails = len(cached_emails)

    # Load labels indexed by cached_email_id
    _log(f"Found {len(cached_emails)} cached emails. Loading labels…")
    labels_map: dict[int, EvalLabel] = {}
    for label in session.query(EvalLabel).all():
        labels_map[label.cached_email_id] = label
    eval_run.labeled_emails = len(labels_map)
    _log(f"Loaded {len(labels_map)} labels.")

    # Init LLM provider
    llm_provider: Optional[LLMProvider] = None
    if config.llm_enabled:
        _log(f"Initialising LLM provider ({config.llm_provider} / {config.llm_model})…")
        try:
            llm_provider = create_llm_provider(config)
            _log("LLM provider ready.")
        except Exception as exc:
            logger.warning("eval_llm_init_failed", error=str(exc))
            _log(f"LLM init failed: {exc}. Falling back to rule-based pipeline.")
    else:
        _log("LLM disabled — using rule-based pipeline.")

    # Process each email
    results: list[EvalRunResult] = []
    # Track application grouping: map (normalized_company, job_title) -> group_id
    app_group_map: dict[tuple[str, str], int] = {}
    total = len(cached_emails)

    for idx, cached in enumerate(cached_emails):
        # Check cancellation before each email
        if cancel_token is not None and cancel_token.is_set():
            _log(f"Cancellation requested — stopping after {idx} emails.", idx, total)
            break

        subject_preview = (cached.subject or "No subject")[:60]
        _log(f"[{idx + 1}/{total}] {subject_preview}", idx + 1, total)
        parsed = reparse_cached_email(cached)
        if parsed is None:
            # Use cached metadata as fallback
            subject = cached.subject or ""
            sender = cached.sender or ""
            body = cached.body_text or ""
        else:
            subject = parsed.subject
            sender = parsed.sender
            body = parsed.body_text

        # Run pipeline stages
        llm_result: Optional[LLMExtractionResult] = None
        llm_used = False

        # Stage 1: LLM extraction (if available)
        if llm_provider is not None:
            llm_used = True
            try:
                llm_result = extract_with_timeout(
                    llm_provider, sender, subject, body, timeout_sec=config.llm_timeout_sec
                )
                eval_run.total_prompt_tokens += llm_result.prompt_tokens
                eval_run.total_completion_tokens += llm_result.completion_tokens
                eval_run.total_estimated_cost += llm_result.estimated_cost_usd
            except Exception:
                llm_result = None

        # Stage 2: Classification
        if llm_result is not None:
            pred_is_job = llm_result.is_job_application
        else:
            pred_is_job = is_job_related(subject, sender)

        # Stage 3: Field extraction
        if llm_result is not None and llm_result.is_job_application:
            pred_company = llm_result.company or extract_company(subject, sender)
            pred_title = _validate_job_title(llm_result.job_title) or _validate_job_title(extract_job_title(subject, body))
            llm_status = llm_result.status
            if llm_status and llm_status.lower() != "unknown":
                pred_status = llm_status
            else:
                pred_status = extract_status(subject, body)
            pred_confidence = llm_result.confidence
        else:
            pred_company = extract_company(subject, sender)
            pred_title = _validate_job_title(extract_job_title(subject, body))
            pred_status = extract_status(subject, body)
            pred_confidence = None

        # Stage 4: Grouping (create EvalPredictedGroup by company+title dedup)
        pred_group_id = None
        if pred_is_job and pred_company:
            company_norm = pred_company.strip().lower()
            title_norm = (pred_title or "").strip().lower()
            key = (company_norm, title_norm)
            if key not in app_group_map:
                pred_group = EvalPredictedGroup(
                    eval_run_id=eval_run.id,
                    company=pred_company,
                    job_title=pred_title,
                    company_norm=company_norm,
                    job_title_norm=title_norm,
                )
                session.add(pred_group)
                session.flush()  # Get ID
                app_group_map[key] = pred_group.id
            pred_group_id = app_group_map[key]

        result = EvalRunResult(
            eval_run_id=eval_run.id,
            cached_email_id=cached.id,
            predicted_is_job_related=pred_is_job,
            predicted_company=pred_company if pred_is_job else None,
            predicted_job_title=pred_title if pred_is_job else None,
            predicted_status=pred_status if pred_is_job else None,
            predicted_application_group_id=pred_group_id,
            predicted_confidence=pred_confidence,
            llm_used=llm_used,
            prompt_tokens=llm_result.prompt_tokens if llm_result else 0,
            completion_tokens=llm_result.completion_tokens if llm_result else 0,
            estimated_cost_usd=llm_result.estimated_cost_usd if llm_result else 0.0,
        )
        results.append(result)
        session.add(result)

    session.flush()

    # Compute metrics against labels
    _log(f"Pipeline complete — processed {len(results)} emails. Computing metrics…", total, total)
    report = _compute_report(results, labels_map, cached_emails)

    # Update run with metrics
    eval_run.classification_accuracy = report.classification.accuracy
    eval_run.classification_precision = report.classification.precision
    eval_run.classification_recall = report.classification.recall
    eval_run.classification_f1 = report.classification.f1
    eval_run.field_extraction_accuracy = report.overall_field_accuracy
    eval_run.status_detection_accuracy = report.field_status.overall_accuracy
    eval_run.grouping_ari = report.grouping.ari
    eval_run.grouping_v_measure = report.grouping.v_measure
    eval_run.report_json = report.to_json()
    eval_run.completed_at = datetime.now(timezone.utc)

    # Update per-result correctness flags
    from collections import defaultdict
    from difflib import SequenceMatcher

    # Build grouping lookup tables for grouping_correct computation:
    #   true_group_id → set of predicted_group_ids used (split detection)
    #   pred_group_id → set of true_group_ids covered (merge detection)
    _true_to_pred: dict[int, set[int]] = defaultdict(set)
    _pred_to_true: dict[int, set[int]] = defaultdict(set)
    _grouping_results: list[EvalRunResult] = []

    for result in results:
        label = labels_map.get(result.cached_email_id)
        if (label
                and label.correct_application_group_id is not None
                and result.predicted_application_group_id is not None):
            _true_to_pred[label.correct_application_group_id].add(result.predicted_application_group_id)
            _pred_to_true[result.predicted_application_group_id].add(label.correct_application_group_id)
            _grouping_results.append(result)

    # Set grouping_correct = True iff the predicted cluster is pure and complete
    # (no split: all true-group emails map to one predicted group;
    #  no merge: the predicted group contains only emails from one true group)
    for result in _grouping_results:
        label = labels_map.get(result.cached_email_id)
        no_split = len(_true_to_pred[label.correct_application_group_id]) == 1
        no_merge = len(_pred_to_true[result.predicted_application_group_id]) == 1
        result.grouping_correct = no_split and no_merge

    # Classification / field correctness
    for result in results:
        label = labels_map.get(result.cached_email_id)
        if label and label.is_job_related is not None:
            result.classification_correct = (result.predicted_is_job_related == label.is_job_related)
        if label and label.correct_company is not None and result.predicted_is_job_related:
            pn = (result.predicted_company or "").strip().lower()
            ln = label.correct_company.strip().lower()
            result.company_correct = (pn == ln)
            result.company_partial = SequenceMatcher(None, pn, ln).ratio() >= 0.8
        if label and label.correct_job_title is not None and result.predicted_is_job_related:
            result.job_title_correct = (
                (result.predicted_job_title or "").strip().lower() ==
                label.correct_job_title.strip().lower()
            )
        if label and label.correct_status is not None and result.predicted_is_job_related:
            result.status_correct = (
                (result.predicted_status or "").strip().lower() ==
                label.correct_status.strip().lower()
            )

    session.commit()
    f1_str = f"{(eval_run.classification_f1 or 0) * 100:.1f}%" if eval_run.classification_f1 is not None else "n/a"
    acc_str = f"{(eval_run.field_extraction_accuracy or 0) * 100:.1f}%" if eval_run.field_extraction_accuracy is not None else "n/a"
    ari_str = f"{(eval_run.grouping_ari or 0):.3f}" if eval_run.grouping_ari is not None else "n/a"
    grouping_scored = len(_grouping_results)
    grouping_correct_count = sum(1 for r in _grouping_results if r.grouping_correct)
    _log(
        f"✓ Evaluation complete — F1: {f1_str} | Field Acc: {acc_str} | Grouping ARI: {ari_str}"
        f" ({grouping_correct_count}/{grouping_scored} groups correct)",
        total,
        total,
    )
    logger.info(
        "eval_run_complete",
        run_id=eval_run.id,
        total=eval_run.total_emails,
        labeled=eval_run.labeled_emails,
        accuracy=eval_run.classification_accuracy,
    )
    return eval_run


def _compute_report(
    results: list[EvalRunResult],
    labels_map: dict[int, EvalLabel],
    cached_emails: list[CachedEmail],
) -> FullReport:
    """Compute full metrics report from run results and labels."""
    report = FullReport()

    # Build email lookup
    email_map = {ce.id: ce for ce in cached_emails}

    # Classification
    cls_preds, cls_labels = [], []
    for r in results:
        label = labels_map.get(r.cached_email_id)
        if label and label.is_job_related is not None:
            cls_preds.append(r.predicted_is_job_related)
            cls_labels.append(label.is_job_related)
            # Collect error examples
            ce = email_map.get(r.cached_email_id)
            subj = ce.subject if ce else ""
            if r.predicted_is_job_related and not label.is_job_related:
                report.classification_fp_examples.append({
                    "email_id": r.cached_email_id, "subject": subj,
                })
            elif not r.predicted_is_job_related and label.is_job_related:
                report.classification_fn_examples.append({
                    "email_id": r.cached_email_id, "subject": subj,
                })

    report.classification = compute_classification_metrics(cls_preds, cls_labels)

    # Field extraction (only for emails labeled as job-related)
    company_preds, company_labels = [], []
    title_preds, title_labels = [], []
    status_preds, status_labels = [], []

    for r in results:
        label = labels_map.get(r.cached_email_id)
        if not label or not label.is_job_related:
            continue
        company_preds.append(r.predicted_company)
        company_labels.append(label.correct_company)
        title_preds.append(r.predicted_job_title)
        title_labels.append(label.correct_job_title)
        status_preds.append(r.predicted_status)
        status_labels.append(label.correct_status)

        # Collect field error examples
        ce = email_map.get(r.cached_email_id)
        subj = ce.subject if ce else ""
        errors = []
        if label.correct_company and (r.predicted_company or "").strip().lower() != label.correct_company.strip().lower():
            errors.append({"field": "company", "predicted": r.predicted_company, "expected": label.correct_company})
        if label.correct_job_title and (r.predicted_job_title or "").strip().lower() != label.correct_job_title.strip().lower():
            errors.append({"field": "job_title", "predicted": r.predicted_job_title, "expected": label.correct_job_title})
        if label.correct_status and (r.predicted_status or "").strip().lower() != label.correct_status.strip().lower():
            errors.append({"field": "status", "predicted": r.predicted_status, "expected": label.correct_status})
        if errors:
            report.field_error_examples.append({
                "email_id": r.cached_email_id, "subject": subj, "errors": errors,
            })

    report.field_company = compute_field_metrics(company_preds, company_labels)
    report.field_job_title = compute_field_metrics(title_preds, title_labels)
    report.field_status = compute_status_metrics(status_preds, status_labels)

    # Grouping
    pred_groups, true_groups, eids, subjs = [], [], [], []
    for r in results:
        label = labels_map.get(r.cached_email_id)
        if not label or label.correct_application_group_id is None:
            continue
        pred_groups.append(r.predicted_application_group_id)
        true_groups.append(label.correct_application_group_id)
        eids.append(r.cached_email_id)
        ce = email_map.get(r.cached_email_id)
        subjs.append(ce.subject if ce else "")

    report.grouping = compute_grouping_metrics(pred_groups, true_groups, eids, subjs)

    return report
