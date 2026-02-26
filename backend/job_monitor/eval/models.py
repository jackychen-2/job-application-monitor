"""SQLAlchemy models for the evaluation framework."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_monitor.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Email Cache
# ---------------------------------------------------------------------------

class CachedEmail(Base):
    """Locally cached raw email for offline pipeline replay."""

    __tablename__ = "cached_emails"
    __table_args__ = (
        UniqueConstraint("gmail_message_id", name="uq_cached_gmail_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    email_account: Mapped[str] = mapped_column(String(300), nullable=False)
    email_folder: Mapped[str] = mapped_column(String(100), nullable=False, default="INBOX")
    gmail_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_rfc822: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    label: Mapped[EvalLabel | None] = relationship(
        back_populates="cached_email", uselist=False, cascade="all, delete-orphan"
    )
    run_results: Mapped[list[EvalRunResult]] = relationship(
        back_populates="cached_email", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<CachedEmail id={self.id} uid={self.uid} subj={self.subject!r:.40}>"


# ---------------------------------------------------------------------------
# Application Groups (for grouping evaluation)
# ---------------------------------------------------------------------------

class EvalApplicationGroup(Base):
    """A named group representing a single real-world job application (ground truth).

    Groups are scoped to an eval run via eval_run_id — each run has its own isolated
    set of application groups. Legacy groups (before run-scoping) have eval_run_id=NULL.
    """

    __tablename__ = "eval_application_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("eval_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    labels: Mapped[list[EvalLabel]] = relationship(back_populates="application_group")

    def __repr__(self) -> str:
        return f"<EvalApplicationGroup id={self.id} name={self.name!r}>"


class EvalPredictedGroup(Base):
    """A predicted group created by the runner based on company+job_title."""

    __tablename__ = "eval_predicted_groups"
    __table_args__ = (
        UniqueConstraint("eval_run_id", "company_norm", "job_title_norm", name="uq_predicted_group"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    company_norm: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    job_title_norm: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    eval_run: Mapped[EvalRun] = relationship(back_populates="predicted_groups")
    results: Mapped[list[EvalRunResult]] = relationship(back_populates="predicted_group")

    def __repr__(self) -> str:
        return f"<EvalPredictedGroup id={self.id} company={self.company!r} title={self.job_title!r}>"


# ---------------------------------------------------------------------------
# Ground Truth Labels
# ---------------------------------------------------------------------------

class EvalLabel(Base):
    """Human-annotated ground truth for a cached email.

    Run-scoped: one row per (cached_email_id, eval_run_id). Legacy rows have eval_run_id=NULL.
    """

    __tablename__ = "eval_labels"
    __table_args__ = (
        UniqueConstraint("cached_email_id", "eval_run_id", name="uq_eval_label_email_run"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cached_email_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cached_emails.id", ondelete="CASCADE"), nullable=False, index=True
    )
    eval_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("eval_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Classification
    is_job_related: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Three-way category: "job_application" | "recruiter_reach_out" | "not_job_related"
    email_category: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Field extraction ground truth
    correct_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    correct_job_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    correct_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    correct_recruiter_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    correct_date_applied: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Grouping
    correct_application_group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("eval_application_groups.id", ondelete="SET NULL"), nullable=True
    )

    # Review metadata
    labeler: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unlabeled"
    )  # unlabeled, labeled, skipped, uncertain

    # Correction audit log — JSON list of {"field", "predicted", "corrected", "at"}
    # Appended to on every save where a field differs from the pipeline prediction.
    corrections_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Grouping decision learning data — computed when correct_application_group_id is set.
    # Captures the predicted dedup key, correct dedup key, which key part differed,
    # and cluster co-membership so a future model can learn grouping decisions.
    grouping_analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    cached_email: Mapped[CachedEmail] = relationship(back_populates="label")
    application_group: Mapped[EvalApplicationGroup | None] = relationship(back_populates="labels")

    def __repr__(self) -> str:
        return f"<EvalLabel id={self.id} email={self.cached_email_id} status={self.review_status!r}>"


# ---------------------------------------------------------------------------
# Evaluation Runs
# ---------------------------------------------------------------------------

class EvalRun(Base):
    """A single evaluation run of the pipeline against labeled data."""

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_emails: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    labeled_emails: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Aggregate metrics
    classification_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_f1: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_extraction_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    status_detection_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    grouping_ari: Mapped[float | None] = mapped_column(Float, nullable=True)
    grouping_v_measure: Mapped[float | None] = mapped_column(Float, nullable=True)

    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    total_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_estimated_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Relationships
    results: Mapped[list[EvalRunResult]] = relationship(
        back_populates="eval_run", cascade="all, delete-orphan"
    )
    predicted_groups: Mapped[list[EvalPredictedGroup]] = relationship(
        back_populates="eval_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<EvalRun id={self.id} name={self.run_name!r}>"


class EvalRunResult(Base):
    """Per-email result from an evaluation run."""

    __tablename__ = "eval_run_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cached_email_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cached_emails.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Pipeline outputs
    predicted_is_job_related: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Three-way predicted category: "job_application" | "recruiter_reach_out" | "not_job_related"
    predicted_email_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    predicted_company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    predicted_job_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    predicted_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    predicted_application_group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("eval_predicted_groups.id", ondelete="SET NULL"), nullable=True
    )
    predicted_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Per-field correctness
    classification_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    company_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    company_partial: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    job_title_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    grouping_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # LLM usage
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Step-by-step decision log recorded during the actual eval run
    # JSON list of {"stage": str, "message": str, "level": str}
    decision_log_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    eval_run: Mapped[EvalRun] = relationship(back_populates="results")
    cached_email: Mapped[CachedEmail] = relationship(back_populates="run_results")
    predicted_group: Mapped[EvalPredictedGroup | None] = relationship(back_populates="results")

    def __repr__(self) -> str:
        return f"<EvalRunResult run={self.eval_run_id} email={self.cached_email_id}>"
