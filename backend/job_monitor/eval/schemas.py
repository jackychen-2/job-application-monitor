"""Pydantic schemas for the evaluation API."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Cache ─────────────────────────────────────────────────


class CacheDownloadRequest(BaseModel):
    since_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    before_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    max_count: int = Field(500, ge=1, le=5000)


class CacheDownloadResult(BaseModel):
    new_emails: int
    skipped_duplicates: int
    errors: int
    total_fetched: int


class CacheStatsOut(BaseModel):
    total_cached: int
    total_labeled: int
    total_unlabeled: int
    total_skipped: int
    date_range_start: Optional[datetime] = None
    date_range_end: Optional[datetime] = None


class CachedEmailOut(BaseModel):
    id: int
    uid: int
    email_account: str
    email_folder: str
    gmail_message_id: Optional[str] = None
    gmail_thread_id: Optional[str] = None
    subject: Optional[str] = None
    sender: Optional[str] = None
    email_date: Optional[datetime] = None
    body_text: Optional[str] = None
    fetched_at: datetime
    # Label status (joined)
    review_status: Optional[str] = None  # unlabeled if no label row exists

    model_config = {"from_attributes": True}


class CachedEmailDetailOut(CachedEmailOut):
    """Full detail including pipeline prediction from most recent eval run."""
    # Pipeline predictions (from latest eval run, if any)
    predicted_is_job_related: Optional[bool] = None
    predicted_email_category: Optional[str] = None
    predicted_company: Optional[str] = None
    predicted_job_title: Optional[str] = None
    predicted_req_id: Optional[str] = None
    predicted_status: Optional[str] = None
    predicted_application_group: Optional[int] = None
    predicted_application_group_display: Optional[str] = None  # "Company — Job Title (date)"
    predicted_confidence: Optional[float] = None
    decision_log_json: Optional[str] = None  # step-by-step log from the actual eval run


class CachedEmailListOut(BaseModel):
    items: List[CachedEmailOut]
    total: int
    page: int
    page_size: int


class EmailPredictionRunOut(BaseModel):
    """A historical eval run that contains predictions for a specific cached email."""
    run_id: int
    run_name: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None


# ── Labels ────────────────────────────────────────────────


# ── Correction taxonomy ──────────────────────────────────
# Each string is a machine-readable key; the UI maps these to human labels.

CORRECTION_ERROR_TYPES: dict[str, list[dict]] = {
    "company": [
        {"key": "sender_domain_fallback",  "label": "Sender-domain fallback",      "desc": "Pipeline used the email domain instead of the real company name"},
        {"key": "linkedin_inmail",          "label": "LinkedIn InMail",              "desc": "Sender is linkedin.com; actual hiring company is in subject/body"},
        {"key": "ats_platform_sender",      "label": "ATS platform sender",          "desc": "Greenhouse / Lever / Workday sent the email, not the company"},
        {"key": "recruiter_outreach",       "label": "Third-party recruiter",        "desc": "Recruiting agency sent the email; hiring company is their client"},
        {"key": "wrong_regex_match",        "label": "Wrong regex match",            "desc": "Subject regex latched onto the wrong token"},
        {"key": "company_alias",            "label": "Company alias / parent name",  "desc": "Pipeline used a different legal/brand name (e.g. Alphabet vs Google)"},
        {"key": "no_company_signal",        "label": "No company signal",            "desc": "Email has no extractable company name"},
    ],
    "job_title": [
        {"key": "title_too_generic",        "label": "Title too generic",            "desc": "Extracted title is too vague (e.g. just 'Engineer')"},
        {"key": "title_includes_junk",      "label": "Title includes extra tokens",  "desc": "Regex captured surrounding words along with the title"},
        {"key": "no_title_signal",          "label": "No explicit title",            "desc": "Email never states the job title explicitly"},
        {"key": "wrong_pattern_phase",      "label": "Wrong extraction phase",       "desc": "Title came from a phase/pattern that was not the best match"},
    ],
    "req_id": [
        {"key": "missing_req_id",           "label": "Missing requisition ID",       "desc": "Email has an ID but extraction missed it"},
        {"key": "wrong_req_id",             "label": "Wrong requisition ID",         "desc": "Extracted requisition ID does not match email evidence"},
        {"key": "no_req_id_signal",         "label": "No requisition ID signal",     "desc": "Email does not include a clear requisition ID"},
    ],
    "status": [
        {"key": "soft_rejection_missed",    "label": "Soft rejection not detected",  "desc": "Polite 'keep your resume on file' language was not caught"},
        {"key": "on_hold_not_rejection",    "label": "'On hold' = effective rejection","desc": "Position put on hold, pipeline did not treat it as a rejection"},
        {"key": "wrong_keyword_matched",    "label": "Wrong keyword fired",          "desc": "A keyword matched a status that does not apply"},
        {"key": "status_ambiguous",         "label": "Status genuinely ambiguous",   "desc": "Email could reasonably be interpreted as multiple statuses"},
    ],
    "classification": [
        {"key": "false_pos_newsletter",     "label": "Newsletter / job alert",       "desc": "Email is a digest or newsletter, not an application confirmation"},
        {"key": "false_pos_verification",   "label": "Security / verification email","desc": "OTP, password reset, or identity verification"},
        {"key": "false_pos_recruiter",      "label": "Recruiter cold outreach",      "desc": "Recruiter reach out — no application was submitted (should be 'not_job_related' with status 'Recruiter Reach-out')"},
        {"key": "false_neg_no_keywords",    "label": "Job email missing keywords",   "desc": "Genuine job email but lacked any signal keywords"},
        {"key": "recruiter_misclassified",  "label": "Recruiter reach out missed",   "desc": "Pipeline classified as job_application or not_job_related, but this is a recruiter reach out"},
    ],
    "application_group": [
        {"key": "same_app_split",           "label": "Same application split",       "desc": "Emails from one application were split into multiple predicted groups"},
        {"key": "different_apps_merged",    "label": "Different applications merged","desc": "Emails from distinct applications were merged into one predicted group"},
        {"key": "thread_mismatch",          "label": "Wrong thread merged",          "desc": "Reply to a different job was merged with this application"},
        {"key": "company_name_variant",     "label": "Company name variant",         "desc": "Predicted group used a different company name spelling/alias"},
    ],
    "other": [
        {"key": "other",                    "label": "Other (see reason field)",     "desc": "None of the above — fill in the reason text"},
    ],
}


class CorrectionEntryIn(BaseModel):
    """One human-annotated correction for a single predicted field."""
    field: str                          # "company" | "job_title" | "req_id" | "status" | "classification" | "application_group"
    predicted: Optional[str] = None     # raw predicted value (string representation)
    corrected: Optional[str] = None     # human-corrected value
    error_type: Optional[str] = None    # key from CORRECTION_ERROR_TYPES taxonomy
    evidence: Optional[str] = None      # text from subject/body that supports the correction
    reason: Optional[str] = None        # free-text explanation of why the prediction failed


class EvalLabelIn(BaseModel):
    is_job_related: Optional[bool] = None
    # Two-way category: "job_application" | "not_job_related"
    email_category: Optional[str] = None
    correct_company: Optional[str] = None
    correct_job_title: Optional[str] = None
    correct_req_id: Optional[str] = None
    correct_status: Optional[str] = None
    correct_recruiter_name: Optional[str] = None
    correct_date_applied: Optional[str] = None
    correct_application_group_id: Optional[int] = None
    notes: Optional[str] = None
    review_status: str = "labeled"
    # Human-provided structured corrections (optional; if absent, backend auto-detects)
    corrections: Optional[List[CorrectionEntryIn]] = None
    # Which eval run this save is associated with (for correction log scoping)
    run_id: Optional[int] = None


class EvalLabelOut(BaseModel):
    id: int
    cached_email_id: int
    is_job_related: Optional[bool] = None
    email_category: Optional[str] = None
    correct_company: Optional[str] = None
    correct_job_title: Optional[str] = None
    correct_req_id: Optional[str] = None
    correct_status: Optional[str] = None
    correct_recruiter_name: Optional[str] = None
    correct_date_applied: Optional[str] = None
    correct_application_group_id: Optional[int] = None
    labeler: str
    labeled_at: Optional[datetime] = None
    notes: Optional[str] = None
    review_status: str
    corrections_json: Optional[str] = None          # JSON list of {"field","predicted","corrected","at"}
    grouping_analysis_json: Optional[str] = None    # JSON grouping decision learning record

    model_config = {"from_attributes": True}


class BulkLabelUpdate(BaseModel):
    cached_email_ids: List[int]
    is_job_related: Optional[bool] = None
    review_status: Optional[str] = None


# ── Application Groups ────────────────────────────────────


class EvalGroupIn(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    notes: Optional[str] = None
    eval_run_id: Optional[int] = None  # scope this group to a specific eval run


class EvalGroupOut(BaseModel):
    id: int
    eval_run_id: Optional[int] = None
    name: str
    company: Optional[str] = None
    job_title: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    email_count: int = 0

    model_config = {"from_attributes": True}


class EvalPredictedGroupOut(BaseModel):
    id: int
    eval_run_id: int
    company: Optional[str] = None
    job_title: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Dropdown Data ─────────────────────────────────────────


class DropdownOptions(BaseModel):
    companies: List[str]
    job_titles: List[str]
    statuses: List[str]


# ── Eval Runs ─────────────────────────────────────────────


class EvalRunRequest(BaseModel):
    name: Optional[str] = None


class EvalRunOut(BaseModel):
    id: int
    run_name: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_emails: int
    labeled_emails: int
    classification_accuracy: Optional[float] = None
    classification_precision: Optional[float] = None
    classification_recall: Optional[float] = None
    classification_f1: Optional[float] = None
    field_extraction_accuracy: Optional[float] = None
    status_detection_accuracy: Optional[float] = None
    grouping_ari: Optional[float] = None
    grouping_v_measure: Optional[float] = None
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_estimated_cost: float = 0.0

    model_config = {"from_attributes": True}


class EvalRunDetailOut(EvalRunOut):
    report_json: Optional[str] = None
    config_snapshot: Optional[str] = None


class EvalRunResultOut(BaseModel):
    id: int
    cached_email_id: int
    predicted_is_job_related: bool
    predicted_email_category: Optional[str] = None
    predicted_company: Optional[str] = None
    predicted_job_title: Optional[str] = None
    predicted_req_id: Optional[str] = None
    predicted_status: Optional[str] = None
    predicted_application_group_id: Optional[int] = None
    predicted_group: Optional[EvalPredictedGroupOut] = None
    predicted_confidence: Optional[float] = None
    classification_correct: Optional[bool] = None
    company_correct: Optional[bool] = None
    company_partial: Optional[bool] = None
    job_title_correct: Optional[bool] = None
    req_id_correct: Optional[bool] = None
    status_correct: Optional[bool] = None
    grouping_correct: Optional[bool] = None
    llm_used: bool
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float

    # Joined fields for display
    email_subject: Optional[str] = None
    email_sender: Optional[str] = None

    # Human ground-truth label (joined from EvalLabel if exists)
    label_is_job_related: Optional[bool] = None
    label_company: Optional[str] = None
    label_job_title: Optional[str] = None
    label_req_id: Optional[str] = None
    label_status: Optional[str] = None
    label_review_status: Optional[str] = None  # unlabeled | labeled | skipped | uncertain
    decision_log_json: Optional[str] = None    # step-by-step log from the actual eval run

    model_config = {"from_attributes": True}


class EvalRunErrorsOut(BaseModel):
    classification_errors: List[EvalRunResultOut]
    field_errors: List[EvalRunResultOut]
    status_errors: List[EvalRunResultOut]
    grouping_errors: List[EvalRunResultOut]
