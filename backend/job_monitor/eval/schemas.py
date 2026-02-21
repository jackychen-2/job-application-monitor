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
    predicted_company: Optional[str] = None
    predicted_job_title: Optional[str] = None
    predicted_status: Optional[str] = None
    predicted_application_group: Optional[int] = None
    predicted_application_group_display: Optional[str] = None  # "Company — Job Title (date)"
    predicted_confidence: Optional[float] = None


class CachedEmailListOut(BaseModel):
    items: List[CachedEmailOut]
    total: int
    page: int
    page_size: int


# ── Labels ────────────────────────────────────────────────


class EvalLabelIn(BaseModel):
    is_job_related: Optional[bool] = None
    correct_company: Optional[str] = None
    correct_job_title: Optional[str] = None
    correct_status: Optional[str] = None
    correct_recruiter_name: Optional[str] = None
    correct_date_applied: Optional[str] = None
    correct_application_group_id: Optional[int] = None
    notes: Optional[str] = None
    review_status: str = "labeled"


class EvalLabelOut(BaseModel):
    id: int
    cached_email_id: int
    is_job_related: Optional[bool] = None
    correct_company: Optional[str] = None
    correct_job_title: Optional[str] = None
    correct_status: Optional[str] = None
    correct_recruiter_name: Optional[str] = None
    correct_date_applied: Optional[str] = None
    correct_application_group_id: Optional[int] = None
    labeler: str
    labeled_at: Optional[datetime] = None
    notes: Optional[str] = None
    review_status: str

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


class EvalGroupOut(BaseModel):
    id: int
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
    predicted_company: Optional[str] = None
    predicted_job_title: Optional[str] = None
    predicted_status: Optional[str] = None
    predicted_application_group_id: Optional[int] = None
    predicted_group: Optional[EvalPredictedGroupOut] = None
    predicted_confidence: Optional[float] = None
    classification_correct: Optional[bool] = None
    company_correct: Optional[bool] = None
    company_partial: Optional[bool] = None
    job_title_correct: Optional[bool] = None
    status_correct: Optional[bool] = None
    grouping_correct: Optional[bool] = None
    llm_used: bool
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float

    # Joined fields for display
    email_subject: Optional[str] = None
    email_sender: Optional[str] = None

    model_config = {"from_attributes": True}


class EvalRunErrorsOut(BaseModel):
    classification_errors: List[EvalRunResultOut]
    field_errors: List[EvalRunResultOut]
    status_errors: List[EvalRunResultOut]
    grouping_errors: List[EvalRunResultOut]
