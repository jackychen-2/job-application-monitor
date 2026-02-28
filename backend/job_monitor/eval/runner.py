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
from job_monitor.extraction.rules import (
    extract_company,
    extract_job_req_id,
    extract_job_title,
    extract_status,
    normalize_req_id,
    split_title_and_req_id,
)

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
    # app_group_info: group_id → {company_norm, company_orig, job_title, req_id, status, latest_email_date}
    # Stage 4 calls the same shared company-linking core used by production resolver.
    from job_monitor.linking.resolver import (
        CompanyLinkCandidate as _CompanyLinkCandidate,
        normalize_company as _prod_normalize_company,
        resolve_by_company_candidates as _resolve_company_link_shared,
    )

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

        email_date_str = cached.email_date.strftime("%Y-%m-%d %H:%M UTC") if cached.email_date else "unknown"
        dstep("input", f"Date    : {email_date_str}")
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
                dstep("llm", f"is_job={llm_result.is_job_application}  category={llm_result.email_category!r}  "
                      f"company={llm_result.company!r}  title={llm_result.job_title!r}  "
                      f"req_id={llm_result.req_id!r}  "
                      f"status={llm_result.status!r}  confidence={llm_result.confidence}  "
                      f"tokens={llm_result.prompt_tokens}+{llm_result.completion_tokens}")
            except Exception as llm_exc:
                dstep("llm", f"LLM failed: {llm_exc} — falling back to rules", "error")
                llm_result = None
        else:
            dstep("llm", "LLM disabled — rule-based pipeline only", "info")

        # Stage 2: Classification
        dstep("classification", "═══ Stage 2: Classification ═══")
        is_recruiter_reach_out = False
        is_onboarding = False
        is_oa = False
        if llm_result is not None:
            normalized_status = (llm_result.status or "").strip().lower().replace("_", " ")
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
            pred_is_job = llm_result.is_job_application or is_recruiter_reach_out or is_onboarding or is_oa
            dstep(
                "classification",
                f"LLM result: is_job_application={llm_result.is_job_application} "
                f"email_category={llm_result.email_category!r} "
                f"trackable={pred_is_job} "
                f"(confidence={llm_result.confidence:.2f})",
                "success" if pred_is_job else "warn",
            )
        else:
            pred_is_job = is_job_related(subject, sender)
            dstep("classification",
                  f"Rule-based: is_job_related={pred_is_job} (subject: {subject[:80]!r})",
                  "success" if pred_is_job else "warn")

        if not pred_is_job:
            dstep("classification", "→ Not job-related — field extraction skipped", "warn")
            pred_company = None
            pred_title = None
            pred_req_id = None
            pred_status = None
            pred_confidence = None
        else:
            # Stage 3: Field extraction (only for job-related emails)
            dstep("company", "═══ Stage 3: Company ═══")
            dstep("title",   "═══ Stage 3: Title ═══")
            dstep("req_id",  "═══ Stage 3: Req ID ═══")
            dstep("status",  "═══ Stage 3: Status ═══")
            if llm_result is not None:
                pred_company = (llm_result.company or "").strip() or "Unknown"
                raw_title = _validate_job_title(llm_result.base_title or llm_result.job_title)
                base_title, req_from_title = split_title_and_req_id(raw_title)
                pred_req_id = normalize_req_id(llm_result.req_id) or normalize_req_id(req_from_title)
                if not base_title:
                    llm_title_with_req = _validate_job_title(llm_result.title_with_req_id)
                    base_title, req_from_title_with_req = split_title_and_req_id(llm_title_with_req)
                    if not pred_req_id:
                        pred_req_id = normalize_req_id(req_from_title_with_req)
                pred_title = base_title
                if is_recruiter_reach_out:
                    pred_status = "Recruiter Reach-out"
                    dstep("status", "LLM status recruiter reach-out -> Recruiter Reach-out", "success")
                elif is_oa:
                    pred_status = "OA"
                    dstep("status", "LLM status OA-like -> OA", "success")
                elif is_onboarding:
                    pred_status = "Onboarding"
                    dstep("status", "LLM status onboarding-like -> Onboarding", "success")
                else:
                    llm_status = llm_result.status
                    if llm_status and llm_status.lower() != "unknown":
                        pred_status = llm_status
                        dstep("status", f"LLM: {pred_status!r}", "success")
                    else:
                        pred_status = "Unknown"
                        dstep("status", "LLM returned unknown", "warn")
                pred_confidence = llm_result.confidence
                dstep("company", f"LLM: {pred_company!r}",
                      "success" if pred_company else "warn")
                dstep("title",   f"LLM: {pred_title!r}",
                      "success" if pred_title else "warn")
                dstep("req_id",  f"LLM: {pred_req_id!r}",
                      "success" if pred_req_id else "warn")
            else:
                pred_company = extract_company(subject, sender)
                raw_title = _validate_job_title(extract_job_title(subject, body))
                base_title, req_from_title = split_title_and_req_id(raw_title)
                pred_req_id = normalize_req_id(extract_job_req_id(subject, body, base_title) or req_from_title)
                pred_title = base_title
                pred_status = extract_status(subject, body)
                pred_confidence = None
                dstep("company", f"Rules: {pred_company!r}", "success" if pred_company and pred_company != "Unknown" else "warn")
                dstep("title",   f"Rules: {pred_title!r}", "success" if pred_title else "warn")
                dstep("req_id",  f"Rules: {pred_req_id!r}", "success" if pred_req_id else "warn")
                dstep("status",  f"Rules: {pred_status!r}", "success")

        # Stage 4: Grouping
        # Uses the shared production company-link resolver core.
        dstep("grouping", "═══ Stage 4: Grouping ═══")
        pred_group_id = None
        if pred_is_job and pred_company:
            company_norm_prod = _prod_normalize_company(pred_company)
            dstep("grouping", f"Normalized company: {company_norm_prod!r}  (raw: {pred_company!r})")

            if company_norm_prod:
                same_company_count = sum(
                    1 for info in app_group_info.values()
                    if info.get("company_norm") == company_norm_prod
                )
                dstep("grouping", f"{same_company_count} candidate group(s) with same company norm")

                group_candidates = [
                    _CompanyLinkCandidate(
                        id=gid,
                        company=info.get("company_orig") or "",
                        normalized_company=info.get("company_norm"),
                        job_title=info.get("job_title"),
                        req_id=info.get("req_id"),
                        status=info.get("status"),
                        last_email_subject=info.get("latest_email_subject"),
                    )
                    for gid, info in app_group_info.items()
                ]

                def _fmt_dt(dt: datetime | None) -> str:
                    if not dt:
                        return ""
                    return dt.isoformat(sep=" ", timespec="seconds")

                def _timeline_provider(candidate: _CompanyLinkCandidate) -> dict:
                    info = app_group_info.get(candidate.id, {})
                    candidate_last_dt = info.get("latest_email_date")
                    if cached.email_date and candidate_last_dt:
                        cur_dt = cached.email_date.replace(tzinfo=None) if cached.email_date.tzinfo else cached.email_date
                        prev_dt = candidate_last_dt.replace(tzinfo=None) if candidate_last_dt.tzinfo else candidate_last_dt
                        days_since_last = abs((cur_dt - prev_dt).days)
                    else:
                        days_since_last = None
                    return {
                        "new_email_date": _fmt_dt(cached.email_date),
                        "app_created_at": "",
                        "app_last_email_date": _fmt_dt(candidate_last_dt),
                        "days_since_last_email": days_since_last,
                        "recent_events": [
                            {
                                "date": _fmt_dt(candidate_last_dt),
                                "status": info.get("status", "") or "",
                                "subject": info.get("latest_email_subject", "") or "",
                            }
                        ],
                    }

                shared_result = _resolve_company_link_shared(
                    company=pred_company,
                    candidates=group_candidates,
                    extracted_status=pred_status,
                    job_title=pred_title,
                    req_id=pred_req_id,
                    llm_provider=llm_provider,
                    email_subject=subject,
                    email_sender=sender,
                    email_body=body,
                    timeline_provider=_timeline_provider,
                )

                if shared_result.is_linked and shared_result.application_id in app_group_info:
                    pred_group_id = shared_result.application_id
                    pred_company = app_group_info[pred_group_id].get("company_orig", pred_company)
                    pred_title = app_group_info[pred_group_id].get("job_title", pred_title)
                    dstep(
                        "grouping",
                        f"Linked to existing group #{pred_group_id} via {shared_result.link_method} "
                        f"(canonical: {pred_company!r} / {pred_title!r})",
                        "success",
                    )

                    # Keep eval state aligned with production row update behavior.
                    if pred_company:
                        app_group_info[pred_group_id]["company_orig"] = pred_company
                    if pred_title:
                        app_group_info[pred_group_id]["job_title"] = pred_title
                    if pred_req_id:
                        app_group_info[pred_group_id]["req_id"] = pred_req_id
                    if pred_status:
                        app_group_info[pred_group_id]["status"] = pred_status
                    if cached.email_date:
                        app_group_info[pred_group_id]["latest_email_date"] = cached.email_date
                    app_group_info[pred_group_id]["latest_email_subject"] = subject
                else:
                    # Shared resolver declined linking — create/reuse an eval predicted group.
                    title_norm_for_key = (pred_title or "").strip().lower()
                    existing_group = session.query(EvalPredictedGroup).filter(
                        EvalPredictedGroup.eval_run_id == eval_run.id,
                        EvalPredictedGroup.company_norm == company_norm_prod,
                        EvalPredictedGroup.job_title_norm == title_norm_for_key,
                    ).first()
                    if existing_group:
                        pred_group_id = existing_group.id
                        dstep("grouping", f"Reused existing group #{pred_group_id} (same norm key)", "info")
                    else:
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
                        dstep("grouping", f"New predicted group #{pred_group_id}", "info")
                    app_group_info[pred_group_id] = {
                        "company_norm": company_norm_prod,
                        "company_orig": pred_company,
                        "job_title": pred_title,
                        "req_id": pred_req_id,
                        "status": pred_status,
                        "latest_email_date": cached.email_date,
                        "latest_email_subject": subject,
                    }
            else:
                dstep("grouping", "Skipped (company normalization returned empty)", "warn")
        else:
            dstep("grouping", "Skipped (not job-related or no company extracted)", "warn")

        # Derive predicted_email_category from LLM result or boolean fallback
        if llm_result and llm_result.email_category:
            pred_email_category: str | None = llm_result.email_category
        elif pred_is_job:
            pred_email_category = "job_application"
        else:
            pred_email_category = None  # rule-based: can't distinguish recruiter vs. not_job_related

        result = EvalRunResult(
            eval_run_id=eval_run.id,
            cached_email_id=cached.id,
            predicted_is_job_related=pred_is_job,
            predicted_email_category=pred_email_category,
            predicted_company=pred_company if pred_is_job else None,
            predicted_job_title=pred_title if pred_is_job else None,
            predicted_req_id=pred_req_id if pred_is_job else None,
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
        # Commit after each email so progress is preserved if the run fails mid-way.
        # This avoids losing LLM calls already made for earlier emails.
        try:
            session.commit()
        except Exception as commit_err:
            logger.warning("eval_email_commit_failed", email_id=cached.id, error=str(commit_err))
            session.rollback()

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
    # Use normalize_company() for company comparison so that "Microsoft Corporation"
    # and "Microsoft" are treated as the same company (both normalize to "microsoft").
    # Use titles_similar() for title comparison so abbreviation variants match.
    from job_monitor.linking.resolver import (
        normalize_company as _norm_co_runner,
        titles_similar as _titles_sim_runner,
    )

    for result in results:
        label = labels_map.get(result.cached_email_id)
        if label and label.is_job_related is not None:
            result.classification_correct = (result.predicted_is_job_related == label.is_job_related)
        if label and label.correct_company is not None and result.predicted_is_job_related:
            pn = _norm_co_runner(result.predicted_company or "") or (result.predicted_company or "").strip().lower()
            ln = _norm_co_runner(label.correct_company) or label.correct_company.strip().lower()
            result.company_correct = (pn == ln)
            result.company_partial = SequenceMatcher(None, pn, ln).ratio() >= 0.8
        if label and label.correct_job_title is not None and result.predicted_is_job_related:
            pred_t = (result.predicted_job_title or "").strip()
            true_t = label.correct_job_title.strip()
            # Exact match first; fall back to titles_similar for abbreviation variants
            result.job_title_correct = (
                pred_t.lower() == true_t.lower() or
                (bool(pred_t) and bool(true_t) and _titles_sim_runner(pred_t, true_t))
            )
        if label and label.correct_status is not None and result.predicted_is_job_related:
            result.status_correct = (
                (result.predicted_status or "").strip().lower() ==
                label.correct_status.strip().lower()
            )
        if label and label.correct_req_id is not None and result.predicted_is_job_related:
            result.req_id_correct = (
                normalize_req_id(result.predicted_req_id or "") ==
                normalize_req_id(label.correct_req_id)
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


def refresh_eval_run_report(session: Session, run_id: int) -> None:
    """Recompute report_json and per-result correctness flags for an existing run.

    Called after the auto-bootstrap updates run-scoped labels so that
    field_error_examples and aggregate metrics reflect the current labels,
    not the stale snapshot taken before bootstrap.
    """
    from collections import defaultdict
    from difflib import SequenceMatcher
    from job_monitor.linking.resolver import (
        normalize_company as _norm_co,
        titles_similar as _titles_sim,
    )

    eval_run = session.query(EvalRun).get(run_id)
    if eval_run is None:
        return

    results = (
        session.query(EvalRunResult)
        .filter(EvalRunResult.eval_run_id == run_id)
        .all()
    )
    if not results:
        return

    # Build labels_map from run-scoped labels (the bootstrap just committed these)
    labels_map: dict[int, EvalLabel] = {}
    for lbl in session.query(EvalLabel).filter(EvalLabel.eval_run_id == run_id).all():
        labels_map[lbl.cached_email_id] = lbl

    # Load cached emails for the results in this run
    email_ids = [r.cached_email_id for r in results]
    cached_emails = (
        session.query(CachedEmail).filter(CachedEmail.id.in_(email_ids)).all()
    )

    # Recompute aggregate report
    report = _compute_report(results, labels_map, cached_emails)
    eval_run.classification_accuracy = report.classification.accuracy
    eval_run.classification_precision = report.classification.precision
    eval_run.classification_recall = report.classification.recall
    eval_run.classification_f1 = report.classification.f1
    eval_run.field_extraction_accuracy = report.overall_field_accuracy
    eval_run.status_detection_accuracy = report.field_status.overall_accuracy
    eval_run.grouping_ari = report.grouping.ari
    eval_run.grouping_v_measure = report.grouping.v_measure
    eval_run.report_json = report.to_json()
    eval_run.labeled_emails = len(labels_map)

    # Recompute per-result grouping correctness
    _true_to_pred: dict[int, set[int]] = defaultdict(set)
    _pred_to_true: dict[int, set[int]] = defaultdict(set)
    _grouping_results = []
    for r in results:
        lbl = labels_map.get(r.cached_email_id)
        if (lbl
                and lbl.correct_application_group_id is not None
                and r.predicted_application_group_id is not None):
            _true_to_pred[lbl.correct_application_group_id].add(r.predicted_application_group_id)
            _pred_to_true[r.predicted_application_group_id].add(lbl.correct_application_group_id)
            _grouping_results.append(r)
    for r in _grouping_results:
        lbl = labels_map.get(r.cached_email_id)
        no_split = len(_true_to_pred[lbl.correct_application_group_id]) == 1
        no_merge = len(_pred_to_true[r.predicted_application_group_id]) == 1
        r.grouping_correct = no_split and no_merge

    # Recompute per-result classification / field correctness
    for r in results:
        lbl = labels_map.get(r.cached_email_id)
        if lbl and lbl.is_job_related is not None:
            r.classification_correct = (r.predicted_is_job_related == lbl.is_job_related)
        if lbl and lbl.correct_company is not None and r.predicted_is_job_related:
            pn = _norm_co(r.predicted_company or "") or (r.predicted_company or "").strip().lower()
            ln = _norm_co(lbl.correct_company) or lbl.correct_company.strip().lower()
            r.company_correct = (pn == ln)
            r.company_partial = SequenceMatcher(None, pn, ln).ratio() >= 0.8
        if lbl and lbl.correct_job_title is not None and r.predicted_is_job_related:
            pred_t = (r.predicted_job_title or "").strip()
            true_t = lbl.correct_job_title.strip()
            r.job_title_correct = (
                pred_t.lower() == true_t.lower() or
                (bool(pred_t) and bool(true_t) and _titles_sim(pred_t, true_t))
            )
        if lbl and lbl.correct_status is not None and r.predicted_is_job_related:
            r.status_correct = (
                (r.predicted_status or "").strip().lower() ==
                lbl.correct_status.strip().lower()
            )
        if lbl and lbl.correct_req_id is not None and r.predicted_is_job_related:
            r.req_id_correct = (
                normalize_req_id(r.predicted_req_id or "") ==
                normalize_req_id(lbl.correct_req_id)
            )

    session.flush()
    logger.info("eval_report_refreshed", run_id=run_id, labeled=len(labels_map))


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
    req_preds, req_labels = [], []
    status_preds, status_labels = [], []

    for r in results:
        label = labels_map.get(r.cached_email_id)
        if not label or not label.is_job_related:
            continue
        company_preds.append(r.predicted_company)
        company_labels.append(label.correct_company)
        title_preds.append(r.predicted_job_title)
        title_labels.append(label.correct_job_title)
        req_preds.append(r.predicted_req_id)
        req_labels.append(label.correct_req_id)
        status_preds.append(r.predicted_status)
        status_labels.append(label.correct_status)

        # Collect field error examples — use normalize_company() and titles_similar()
        # so "Microsoft Corporation" vs "Microsoft" and "Sr. Engineer" vs "Senior Engineer"
        # are not reported as errors (matches how company_correct is computed above).
        from job_monitor.linking.resolver import (
            normalize_company as _nc_report,
            titles_similar as _ts_report,
        )
        ce = email_map.get(r.cached_email_id)
        subj = ce.subject if ce else ""
        errors = []
        if label.correct_company:
            pco = _nc_report(r.predicted_company or "") or (r.predicted_company or "").strip().lower()
            lco = _nc_report(label.correct_company) or label.correct_company.strip().lower()
            if pco != lco:
                errors.append({"field": "company", "predicted": r.predicted_company, "expected": label.correct_company})
        if label.correct_job_title:
            pt = (r.predicted_job_title or "").strip()
            lt = label.correct_job_title.strip()
            if pt.lower() != lt.lower() and not (pt and lt and _ts_report(pt, lt)):
                errors.append({"field": "job_title", "predicted": r.predicted_job_title, "expected": label.correct_job_title})
        if label.correct_status and (r.predicted_status or "").strip().lower() != label.correct_status.strip().lower():
            errors.append({"field": "status", "predicted": r.predicted_status, "expected": label.correct_status})
        if label.correct_req_id:
            pr = normalize_req_id(r.predicted_req_id or "")
            lr = normalize_req_id(label.correct_req_id)
            if pr != lr:
                errors.append({
                    "field": "req_id",
                    "predicted": r.predicted_req_id,
                    "expected": label.correct_req_id,
                })
        if errors:
            report.field_error_examples.append({
                "email_id": r.cached_email_id, "subject": subj, "errors": errors,
            })

    report.field_company = compute_field_metrics(company_preds, company_labels)
    report.field_job_title = compute_field_metrics(title_preds, title_labels)
    report.field_req_id = compute_field_metrics(req_preds, req_labels)
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
