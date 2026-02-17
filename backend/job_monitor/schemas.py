"""Pydantic schemas for API request/response validation."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Application schemas ───────────────────────────────────


class ApplicationBase(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    job_title: Optional[str] = Field(None, max_length=300)
    status: str = Field("已申请", max_length=50)
    notes: Optional[str] = None


class ApplicationCreate(ApplicationBase):
    """Request body for manually creating an application."""

    source: str = Field("manual", max_length=50)


class ApplicationUpdate(BaseModel):
    """Request body for updating an application (all fields optional)."""

    company: Optional[str] = Field(None, min_length=1, max_length=200)
    job_title: Optional[str] = Field(None, max_length=300)
    status: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None


class StatusHistoryOut(BaseModel):
    id: int
    old_status: Optional[str]
    new_status: str
    change_source: Optional[str]
    changed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApplicationOut(BaseModel):
    id: int
    company: str
    job_title: Optional[str]
    email_subject: Optional[str]
    email_sender: Optional[str]
    email_date: Optional[datetime]
    status: str
    source: str
    notes: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApplicationDetailOut(ApplicationOut):
    """Application with full status history."""

    status_history: List[StatusHistoryOut] = []


class ApplicationListOut(BaseModel):
    items: List[ApplicationOut]
    total: int
    page: int
    page_size: int


# ── Scan schemas ──────────────────────────────────────────


class ScanResultOut(BaseModel):
    emails_scanned: int
    emails_matched: int
    applications_created: int
    applications_updated: int
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


class StatsOut(BaseModel):
    total_applications: int
    status_breakdown: List[StatusCount]
    recent_applications: List[ApplicationOut]
    total_emails_scanned: int
    total_llm_cost: float
    daily_llm_costs: List[DailyCost] = []
    daily_applications: List[DailyCount] = []
