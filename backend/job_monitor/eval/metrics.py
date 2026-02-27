"""Accuracy calculation, confusion matrices, and clustering metrics."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from difflib import SequenceMatcher


def _fuzzy_match(a: str, b: str, threshold: float = 0.8) -> bool:
    """Return True if normalized fuzzy similarity >= threshold."""
    if not a or not b:
        return False
    a_n = a.strip().lower()
    b_n = b.strip().lower()
    if a_n == b_n:
        return True
    return SequenceMatcher(None, a_n, b_n).ratio() >= threshold


def _normalize(s: Optional[str]) -> str:
    return (s or "").strip().lower()


# ---------------------------------------------------------------------------
# Classification metrics
# ---------------------------------------------------------------------------

@dataclass
class ClassificationMetrics:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "tn": self.tn, "fn": self.fn,
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "total": self.total,
        }


def compute_classification_metrics(
    predictions: list[bool], labels: list[bool]
) -> ClassificationMetrics:
    m = ClassificationMetrics()
    for pred, label in zip(predictions, labels):
        if label and pred:
            m.tp += 1
        elif not label and pred:
            m.fp += 1
        elif not label and not pred:
            m.tn += 1
        else:
            m.fn += 1
    return m


# ---------------------------------------------------------------------------
# Field extraction metrics
# ---------------------------------------------------------------------------

@dataclass
class FieldMetrics:
    exact_match: int = 0
    partial_match: int = 0
    wrong: int = 0
    missing_pred: int = 0  # ground truth has value, pred is empty
    missing_label: int = 0  # not labeled (excluded from scoring)

    @property
    def total_scored(self) -> int:
        return self.exact_match + self.partial_match + self.wrong + self.missing_pred

    @property
    def exact_accuracy(self) -> float:
        return self.exact_match / self.total_scored if self.total_scored else 0.0

    @property
    def partial_accuracy(self) -> float:
        return (self.exact_match + self.partial_match) / self.total_scored if self.total_scored else 0.0

    def to_dict(self) -> dict:
        return {
            "exact_match": self.exact_match,
            "partial_match": self.partial_match,
            "wrong": self.wrong,
            "missing_pred": self.missing_pred,
            "missing_label": self.missing_label,
            "total_scored": self.total_scored,
            "exact_accuracy": round(self.exact_accuracy, 4),
            "partial_accuracy": round(self.partial_accuracy, 4),
        }


def compute_field_metrics(
    predictions: list[Optional[str]], labels: list[Optional[str]]
) -> FieldMetrics:
    m = FieldMetrics()
    for pred, label in zip(predictions, labels):
        if label is None:
            m.missing_label += 1
            continue
        pred_n = _normalize(pred)
        label_n = _normalize(label)
        if not pred_n and label_n:
            m.missing_pred += 1
        elif pred_n == label_n:
            m.exact_match += 1
        elif _fuzzy_match(pred_n, label_n):
            m.partial_match += 1
        else:
            m.wrong += 1
    return m


# ---------------------------------------------------------------------------
# Status confusion matrix
# ---------------------------------------------------------------------------

KNOWN_STATUSES = ["Recruiter Reach-out", "已申请", "OA", "面试", "Offer", "Onboarding", "拒绝", "Unknown"]


@dataclass
class StatusMetrics:
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    per_class: dict[str, dict] = field(default_factory=dict)
    overall_accuracy: float = 0.0

    def to_dict(self) -> dict:
        return {
            "confusion_matrix": self.confusion,
            "per_class": self.per_class,
            "overall_accuracy": round(self.overall_accuracy, 4),
        }


def compute_status_metrics(
    predictions: list[Optional[str]], labels: list[Optional[str]]
) -> StatusMetrics:
    # Build confusion matrix
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total = 0
    correct = 0

    for pred, label in zip(predictions, labels):
        if label is None:
            continue
        p = _normalize(pred) or "unknown"
        l = _normalize(label) or "unknown"
        confusion[l][p] += 1
        total += 1
        if p == l:
            correct += 1

    # Per-class precision/recall
    all_classes = sorted(set(list(confusion.keys()) + [p for row in confusion.values() for p in row]))
    per_class = {}
    for cls in all_classes:
        tp = confusion.get(cls, {}).get(cls, 0)
        fp = sum(confusion.get(other, {}).get(cls, 0) for other in all_classes if other != cls)
        fn = sum(v for k, v in confusion.get(cls, {}).items() if k != cls)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_class[cls] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": sum(confusion.get(cls, {}).values()),
        }

    sm = StatusMetrics()
    sm.confusion = {k: dict(v) for k, v in confusion.items()}
    sm.per_class = per_class
    sm.overall_accuracy = correct / total if total else 0.0
    return sm


# ---------------------------------------------------------------------------
# Grouping / clustering metrics
# ---------------------------------------------------------------------------

@dataclass
class GroupingMetrics:
    ari: float = 0.0
    homogeneity: float = 0.0
    completeness: float = 0.0
    v_measure: float = 0.0
    split_errors: list[dict] = field(default_factory=list)
    merge_errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ari": round(self.ari, 4),
            "homogeneity": round(self.homogeneity, 4),
            "completeness": round(self.completeness, 4),
            "v_measure": round(self.v_measure, 4),
            "split_error_count": len(self.split_errors),
            "merge_error_count": len(self.merge_errors),
            "split_errors": self.split_errors[:20],
            "merge_errors": self.merge_errors[:20],
        }


def compute_grouping_metrics(
    pred_groups: list[Optional[int]],
    true_groups: list[Optional[int]],
    email_ids: list[int],
    email_subjects: list[str],
) -> GroupingMetrics:
    """Compute clustering metrics between predicted and true application groups.

    Only includes emails where both pred and true group are not None.
    """
    # Filter to emails with both labels
    filtered = [
        (p, t, eid, subj)
        for p, t, eid, subj in zip(pred_groups, true_groups, email_ids, email_subjects)
        if p is not None and t is not None
    ]
    if len(filtered) < 2:
        return GroupingMetrics()

    preds = [f[0] for f in filtered]
    trues = [f[1] for f in filtered]
    eids = [f[2] for f in filtered]
    subjs = [f[3] for f in filtered]

    gm = GroupingMetrics()

    # Try sklearn metrics if available
    try:
        from sklearn.metrics import adjusted_rand_score, homogeneity_completeness_v_measure
        gm.ari = adjusted_rand_score(trues, preds)
        gm.homogeneity, gm.completeness, gm.v_measure = homogeneity_completeness_v_measure(trues, preds)
    except ImportError:
        # Fallback: compute ARI manually (simplified)
        gm.ari = _simple_ari(trues, preds)

    # Detect split errors: one true group mapped to multiple pred groups
    true_to_preds: dict[int, set[int]] = defaultdict(set)
    true_to_emails: dict[int, list[dict]] = defaultdict(list)
    for p, t, eid, subj in filtered:
        true_to_preds[t].add(p)
        true_to_emails[t].append({"email_id": eid, "subject": subj, "pred_group": p})

    for tg, pg_set in true_to_preds.items():
        if len(pg_set) > 1:
            gm.split_errors.append({
                "true_group": tg,
                "predicted_groups": sorted(pg_set),
                "emails": true_to_emails[tg],
            })

    # Detect merge errors: one pred group contains multiple true groups
    pred_to_trues: dict[int, set[int]] = defaultdict(set)
    pred_to_emails: dict[int, list[dict]] = defaultdict(list)
    for p, t, eid, subj in filtered:
        pred_to_trues[p].add(t)
        pred_to_emails[p].append({"email_id": eid, "subject": subj, "true_group": t})

    for pg, tg_set in pred_to_trues.items():
        if len(tg_set) > 1:
            gm.merge_errors.append({
                "predicted_group": pg,
                "true_groups": sorted(tg_set),
                "emails": pred_to_emails[pg],
            })

    return gm


def _simple_ari(true_labels: list, pred_labels: list) -> float:
    """Simplified Adjusted Rand Index without sklearn."""
    from math import comb
    n = len(true_labels)
    if n < 2:
        return 0.0

    # Build contingency table
    true_clusters: dict = defaultdict(set)
    pred_clusters: dict = defaultdict(set)
    for i, (t, p) in enumerate(zip(true_labels, pred_labels)):
        true_clusters[t].add(i)
        pred_clusters[p].add(i)

    # Compute index
    sum_comb_nij = sum(
        comb(len(tc & pc), 2)
        for tc in true_clusters.values()
        for pc in pred_clusters.values()
    )
    sum_comb_ai = sum(comb(len(tc), 2) for tc in true_clusters.values())
    sum_comb_bj = sum(comb(len(pc), 2) for pc in pred_clusters.values())
    comb_n = comb(n, 2)

    if comb_n == 0:
        return 0.0

    expected = sum_comb_ai * sum_comb_bj / comb_n
    max_index = 0.5 * (sum_comb_ai + sum_comb_bj)
    denominator = max_index - expected

    if denominator == 0:
        return 1.0 if sum_comb_nij == expected else 0.0

    return (sum_comb_nij - expected) / denominator


# ---------------------------------------------------------------------------
# Full report generation
# ---------------------------------------------------------------------------

@dataclass
class FullReport:
    classification: ClassificationMetrics = field(default_factory=ClassificationMetrics)
    field_company: FieldMetrics = field(default_factory=FieldMetrics)
    field_job_title: FieldMetrics = field(default_factory=FieldMetrics)
    field_req_id: FieldMetrics = field(default_factory=FieldMetrics)
    field_status: StatusMetrics = field(default_factory=StatusMetrics)
    grouping: GroupingMetrics = field(default_factory=GroupingMetrics)

    # Error examples
    classification_fp_examples: list[dict] = field(default_factory=list)
    classification_fn_examples: list[dict] = field(default_factory=list)
    field_error_examples: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "classification": self.classification.to_dict(),
            "field_company": self.field_company.to_dict(),
            "field_job_title": self.field_job_title.to_dict(),
            "field_req_id": self.field_req_id.to_dict(),
            "field_status": self.field_status.to_dict(),
            "grouping": self.grouping.to_dict(),
            "classification_fp_examples": self.classification_fp_examples[:20],
            "classification_fn_examples": self.classification_fn_examples[:20],
            "field_error_examples": self.field_error_examples[:50],
        }, ensure_ascii=False, indent=2)

    @property
    def overall_field_accuracy(self) -> float:
        """Average exact accuracy across company, job_title, and req_id fields."""
        fields = [self.field_company, self.field_job_title, self.field_req_id]
        scored = [f for f in fields if f.total_scored > 0]
        if not scored:
            return 0.0
        return sum(f.exact_accuracy for f in scored) / len(scored)
