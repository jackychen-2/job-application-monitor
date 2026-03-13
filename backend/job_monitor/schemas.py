"""Pydantic schemas for API request/response validation."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── Application schemas ───────────────────────────────────


class ApplicationBase(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    job_title: Optional[str] = Field(None, max_length=300)
    req_id: Optional[str] = Field(None, max_length=80)
    status: str = Field("已申请", max_length=50)
    notes: Optional[str] = None


class ApplicationCreate(ApplicationBase):
    """Request body for manually creating an application."""

    source: str = Field("manual", max_length=50)


class ApplicationUpdate(BaseModel):
    """Request body for updating an application (all fields optional)."""

    company: Optional[str] = Field(None, min_length=1, max_length=200)
    job_title: Optional[str] = Field(None, max_length=300)
    req_id: Optional[str] = Field(None, max_length=80)
    status: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None


class StatusHistoryOut(BaseModel):
    id: int
    old_status: Optional[str]
    new_status: str
    change_source: Optional[str]
    changed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class LinkedEmailOut(BaseModel):
    """Email linked to this application (via thread or company)."""
    id: int
    uid: int
    subject: Optional[str] = None
    sender: Optional[str] = None
    email_date: Optional[datetime] = None
    gmail_thread_id: Optional[str] = None
    processed_at: Optional[datetime] = None
    link_method: Optional[str] = None
    needs_review: bool = False

    model_config = {"from_attributes": True}


class PendingReviewEmailOut(BaseModel):
    """Email that needs user review for linking."""
    id: int
    uid: int
    subject: Optional[str] = None
    sender: Optional[str] = None
    email_date: Optional[datetime] = None
    application_id: Optional[int] = None
    application_company: Optional[str] = None

    model_config = {"from_attributes": True}


class LinkEmailRequest(BaseModel):
    """Request to manually link an email to an application."""
    application_id: int


class MergeApplicationRequest(BaseModel):
    """Request to merge another application into this one."""
    source_application_id: int


class ApplicationMergeEventOut(BaseModel):
    id: int
    target_application_id: int
    source_application_id: int
    merge_source: str = "manual"
    source_company: Optional[str] = None
    source_job_title: Optional[str] = None
    source_req_id: Optional[str] = None
    source_status: Optional[str] = None
    moved_email_count: int = 0
    moved_history_count: int = 0
    merged_at: datetime
    undone_at: Optional[datetime] = None
    undone_source_application_id: Optional[int] = None

    model_config = {"from_attributes": True}


class UnmergeApplicationOut(BaseModel):
    merge_event_id: int
    target_application_id: int
    restored_source_application_id: int
    restored_email_count: int
    restored_history_count: int
    undone_at: datetime


class SplitApplicationRequest(BaseModel):
    """Request to split selected emails from one application into a new application."""
    email_ids: List[int] = Field(..., min_length=1)
    company: Optional[str] = Field(None, min_length=1, max_length=200)
    job_title: Optional[str] = Field(None, max_length=300)
    req_id: Optional[str] = Field(None, max_length=80)
    status: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None


class SplitApplicationOut(BaseModel):
    source_application_id: int
    new_application_id: int
    moved_email_count: int


class ApplicationOut(BaseModel):
    id: int
    company: str
    job_title: Optional[str]
    req_id: Optional[str]
    email_subject: Optional[str]
    email_sender: Optional[str]
    email_date: Optional[datetime]
    applied_at: Optional[datetime]
    status: str
    source: str
    notes: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    email_count: int = 0  # Number of linked emails (for expandable row)

    model_config = {"from_attributes": True}


class ApplicationDetailOut(ApplicationOut):
    """Application with full status history and linked emails."""

    status_history: List[StatusHistoryOut] = []
    linked_emails: List[LinkedEmailOut] = []
    email_count: int = 0


class ApplicationListOut(BaseModel):
    items: List[ApplicationOut]
    total: int
    page: int
    page_size: int


# ── Scan schemas ──────────────────────────────────────────


class ScanResultOut(BaseModel):
    emails_scanned: int
    emails_matched: int
    skipped_social_or_promotions: int = 0
    skipped_not_job_related: int = 0
    skipped_message_unavailable: int = 0
    non_job_reason_counts: Dict[str, int] = Field(default_factory=dict)
    applications_created: int
    applications_updated: int
    applications_deleted: int = 0
    created_application_ids: List[int] = Field(default_factory=list)
    updated_application_ids: List[int] = Field(default_factory=list)
    total_prompt_tokens: int
    total_completion_tokens: int
    total_estimated_cost: float
    errors: List[str]
    cancelled: bool = False


class ScanStateOut(BaseModel):
    email_account: str
    email_folder: str
    last_uid: int
    last_scan_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ScanJobOut(BaseModel):
    id: int
    status: str
    mode: str
    requested_max_emails: int
    since_date: Optional[str] = None
    before_date: Optional[str] = None
    history_fallback_used: bool = False
    total_messages: int
    processed_messages: int
    current_subject: Optional[str] = None
    emails_matched: int
    skipped_social_or_promotions: int = 0
    skipped_not_job_related: int = 0
    skipped_message_unavailable: int = 0
    applications_created: int
    applications_updated: int
    applications_deleted: int = 0
    total_prompt_tokens: int
    total_completion_tokens: int
    total_estimated_cost: float
    errors: List[str] = Field(default_factory=list)
    non_job_reason_counts: Dict[str, int] = Field(default_factory=dict)
    created_application_ids: List[int] = Field(default_factory=list)
    updated_application_ids: List[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class CreateScanJobOut(BaseModel):
    job: ScanJobOut
    reused: bool = False


class ScanJobStepOut(BaseModel):
    job: ScanJobOut
    processed_in_step: int
    done: bool


# ── Stats schemas ─────────────────────────────────────────


class StatusCount(BaseModel):
    status: str
    count: int


class DailyCost(BaseModel):
    date: str
    cost: float


class DailyCount(BaseModel):
    date: str
    count: int


class HourlyCount(BaseModel):
    timestamp: str
    count: int


class StatusTransition(BaseModel):
    """A single status transition (edge in the Sankey diagram)."""
    from_status: str
    to_status: str
    count: int


class FlowData(BaseModel):
    """Full application flow data for the Sankey diagram."""
    status_counts: List[StatusCount]
    transitions: List[StatusTransition]
    total: int


class StatsOut(BaseModel):
    total_applications: int
    status_breakdown: List[StatusCount]
    recent_applications: List[ApplicationOut]
    total_emails_scanned: int
    total_llm_cost: float
    daily_llm_costs: List[DailyCost] = []
    daily_applications: List[DailyCount] = []
    hourly_applications_24h: List[HourlyCount] = []


class DashboardDataOut(BaseModel):
    total_applications: int
    status_breakdown: List[StatusCount]
    recent_applications: List[ApplicationOut] = []
    total_emails_scanned: int
    total_llm_cost: float
    daily_llm_costs: List[DailyCost] = []
    daily_applications: List[DailyCount] = []
    hourly_applications_24h: List[HourlyCount] = []
    flow: FlowData


# ── Auth schemas ─────────────────────────────────────────


class AuthUserOut(BaseModel):
    id: int
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    active_journey_id: Optional[int] = None


class ProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=200)


class AccountOut(BaseModel):
    id: int
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: datetime
    active_journey_id: Optional[int] = None
    active_journey_name: Optional[str] = None
    google_account_email: Optional[str] = None
    google_account_connected: bool = False
    gmail_scope_granted: bool = False
    active_session_count: int = 0
    journey_count: int = 0


class DeleteAccountOut(BaseModel):
    status: str


# ── Journey schemas ───────────────────────────────────────


class JourneyCreate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)


class JourneyUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class JourneyOut(BaseModel):
    id: int
    name: str
    owner_user_id: int
    created_at: datetime
    updated_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class JourneyDeleteOut(BaseModel):
    deleted_journey_id: int
    active_journey_id: int
    replacement_created: bool
