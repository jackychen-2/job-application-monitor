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
    max_emails: Optional[int] = None,
    email_ids: Optional[list[int]] = None,
) -> EvalRun:
    """Run the full pipeline on all cached emails and compute metrics against labels.

    Args:
        config: Application configuration.
        session: Database session.
        run_name: Optional name for this evaluation run.
        progress_cb: Optional callback called as ``(message, current, total)`` for each email processed.
        cancel_token: Optional threading.Event — when set, the run is aborted after the current email.
        max_emails: Optional limit — evaluate only the first N emails (ignored when email_ids is set).
        email_ids: Optional explicit list of CachedEmail IDs to evaluate. When set, only those
            emails are run through the pipeline (max_emails is ignored).
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

    # Load cached emails — filtered by explicit IDs, limited by max_emails, or all
    _log("Loading cached emails from database…")
    q = session.query(CachedEmail).order_by(CachedEmail.email_date)
    if email_ids:
        q = q.filter(CachedEmail.id.in_(email_ids))
        _log(f"Filtering to {len(email_ids)} selected email IDs…")
    cached_emails = q.all()
    if not email_ids and max_emails and max_emails > 0:
        cached_emails = cached_emails[:max_emails]
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

    # ── Grouping state — mirrors production Application table ─────────────
    # app_group_info: group_id → {company_norm, job_title, status, latest_email_date}
    # Replaces the old (company_stripped, title_norm) dedup dict so Stage 4
    # uses the exact same Rules A/B/C as resolve_by_company() in production.
    from job_monitor.linking.resolver import (
        normalize_company as _prod_normalize_company,
        titles_similar as _prod_titles_similar,
        _PROGRESSED_STATUSES as _PROD_PROGRESSED,
    )
    _MAX_SAME_CYCLE_DAYS = 3

    app_group_info: dict[int, dict] = {}  # group_id → metadata
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
            subject = cached.subject or ""
            sender = cached.sender or ""
            body = cached.body_text or ""
        else:
            subject = parsed.subject
            sender = parsed.sender
            body = parsed.body_text

        # ── Per-email decision log ────────────────────────────
        dlog: list[dict] = []

        def dstep(stage: str, msg: str, level: str = "info") -> None:
            dlog.append({"stage": stage, "message": msg, "level": level})

        dstep("input", f"Subject : {subject[:120]!r}")
        dstep("input", f"Sender  : {sender[:120]!r}")
        dstep("input", f"Body    : {body[:200]!r}{'…' if len(body) > 200 else ''}")

        # Run pipeline stages
        llm_result: Optional[LLMExtractionResult] = None
        llm_used = False

        # Stage 1: LLM extraction (if available)
        if llm_provider is not None:
            llm_used = True
            dstep("llm", f"LLM enabled: {config.llm_provider} / {config.llm_model}")
            try:
                llm_result = extract_with_timeout(
                    llm_provider, sender, subject, body, timeout_sec=config.llm_timeout_sec
                )
                eval_run.total_prompt_tokens += llm_result.prompt_tokens
                eval_run.total_completion_tokens += llm_result.completion_tokens
                eval_run.total_estimated_cost += llm_result.estimated_cost_usd
                dstep("llm", f"is_job={llm_result.is_job_application}  company={llm_result.company!r}  "
                      f"title={llm_result.job_title!r}  status={llm_result.status!r}  "
                      f"confidence={llm_result.confidence}  tokens={llm_result.prompt_tokens}+{llm_result.completion_tokens}")
            except Exception as llm_exc:
                dstep("llm", f"LLM failed: {llm_exc} — falling back to rules", "error")
                llm_result = None
        else:
            dstep("llm", "LLM disabled — rule-based pipeline only", "info")

        # Stage 2: Classification
        dstep("classification", "═══ Stage 2: Classification ═══")
        if llm_result is not None:
            pred_is_job = llm_result.is_job_application
            dstep("classification", f"LLM result: is_job_application={pred_is_job}",
                  "success" if pred_is_job else "warn")
        else:
            pred_is_job = is_job_related(subject, sender)
            dstep("classification",
                  f"Rule-based: is_job_related={pred_is_job} (subject: {subject[:80]!r})",
                  "success" if pred_is_job else "warn")

        if not pred_is_job:
            dstep("classification", "→ Not job-related — field extraction skipped", "warn")
            pred_company = None
            pred_title = None
            pred_status = None
            pred_confidence = None
        else:
            # Stage 3: Field extraction (only for job-related emails)
            dstep("company", "═══ Stage 3: Company ═══")
            dstep("title",   "═══ Stage 3: Title ═══")
            dstep("status",  "═══ Stage 3: Status ═══")
            if llm_result is not None and llm_result.is_job_application:
                pred_company = llm_result.company or extract_company(subject, sender)
                pred_title = _validate_job_title(llm_result.job_title) or _validate_job_title(extract_job_title(subject, body))
                llm_status = llm_result.status
                if llm_status and llm_status.lower() != "unknown":
                    pred_status = llm_status
                    dstep("status", f"LLM: {pred_status!r}", "success")
                else:
                    pred_status = extract_status(subject, body)
                    dstep("status", f"LLM returned unknown → rule fallback: {pred_status!r}", "warn")
                pred_confidence = llm_result.confidence
                dstep("company", f"LLM: {pred_company!r} (raw={llm_result.company!r})",
                      "success" if pred_company else "warn")
                dstep("title",   f"LLM: {pred_title!r} (raw={llm_result.job_title!r})",
                      "success" if pred_title else "warn")
            else:
                pred_company = extract_company(subject, sender)
                pred_title = _validate_job_title(extract_job_title(subject, body))
                pred_status = extract_status(subject, body)
                pred_confidence = None
                dstep("company", f"Rules: {pred_company!r}", "success" if pred_company and pred_company != "Unknown" else "warn")
                dstep("title",   f"Rules: {pred_title!r}", "success" if pred_title else "warn")
                dstep("status",  f"Rules: {pred_status!r}", "success")

        # Stage 4: Grouping
        # Logic:
        #  • "Applied" emails → create/join group by (company_norm_stripped, title_norm)
        #  • Follow-up emails (interview/offer/rejection) → first try to find an
        #    existing upstream group; only create a new one if nothing matches.
        #  • Company normalization strips legal/descriptive suffixes so that
        #    "Zoom", "Zoom Communications", "Your Zoom" all map to the same key.
        dstep("grouping", "═══ Stage 4: Grouping ═══")
        pred_group_id = None
        if pred_is_job and pred_company:
            company_norm_prod = _prod_normalize_company(pred_company)
            dstep("grouping", f"Normalized company: {company_norm_prod!r}  (raw: {pred_company!r})")

            if company_norm_prod:
                # ── Candidate search (same as production resolve_by_company) ──
                candidates: list[int] = [
                    gid for gid, info in app_group_info.items()
                    if info["company_norm"] == company_norm_prod
                ]
                dstep("grouping", f"{len(candidates)} candidate group(s) with same company norm")

                # Rule A: Title similarity (Jaccard ≥ 0.9, production threshold)
                if pred_title and candidates:
                    before = len(candidates)
                    candidates = [
                        gid for gid in candidates
                        if _prod_titles_similar(pred_title, app_group_info[gid]["job_title"])
                    ]
                    if before != len(candidates):
                        dstep("grouping", f"Rule A (title filter): {before - len(candidates)} removed, {len(candidates)} remain", "info")

                # Rule B: Time gap — two "已申请" emails > 3 days apart = new cycle
                email_dt = cached.email_date
                if pred_status == "已申请" and email_dt and candidates:
                    before = len(candidates)
                    def _within_window(gid: int) -> bool:
                        prev = app_group_info[gid].get("latest_email_date")
                        if not prev:
                            return True
                        ed = email_dt.replace(tzinfo=None) if email_dt.tzinfo else email_dt
                        pd_ = prev.replace(tzinfo=None) if prev.tzinfo else prev
                        return abs((ed - pd_).days) <= _MAX_SAME_CYCLE_DAYS
                    candidates = [gid for gid in candidates if _within_window(gid)]
                    if before != len(candidates):
                        dstep("grouping", f"Rule B (time gap): {before - len(candidates)} removed, {len(candidates)} remain", "info")

                # Rule C: Re-application after rejection/interview
                if pred_status == "已申请" and candidates:
                    before = len(candidates)
                    candidates = [
                        gid for gid in candidates
                        if app_group_info[gid].get("status") not in _PROD_PROGRESSED
                    ]
                    if before != len(candidates):
                        dstep("grouping", f"Rule C (re-application): {before - len(candidates)} removed, {len(candidates)} remain", "info")

                if candidates:
                    pred_group_id = candidates[0]
                    dstep("grouping", f"Linked to existing group #{pred_group_id}", "success")
                    # Update group state so subsequent emails see the latest status/date
                    if pred_status:
                        app_group_info[pred_group_id]["status"] = pred_status
                    if cached.email_date:
                        app_group_info[pred_group_id]["latest_email_date"] = cached.email_date
                else:
                    # No surviving candidate — create new group
                    title_norm_for_key = (pred_title or "").strip().lower()
                    pred_group = EvalPredictedGroup(
                        eval_run_id=eval_run.id,
                        company=pred_company,
                        job_title=pred_title,
                        company_norm=company_norm_prod,
                        job_title_norm=title_norm_for_key,
                    )
                    session.add(pred_group)
                    session.flush()
                    pred_group_id = pred_group.id
                    app_group_info[pred_group_id] = {
                        "company_norm": company_norm_prod,
                        "job_title": pred_title,
                        "status": pred_status,
                        "latest_email_date": cached.email_date,
                    }
                    dstep("grouping", f"New predicted group #{pred_group_id}", "info")
            else:
                dstep("grouping", "Skipped (company normalization returned empty)", "warn")
        else:
            dstep("grouping", "Skipped (not job-related or no company extracted)", "warn")

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
            decision_log_json=json.dumps(dlog, ensure_ascii=False),
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
