"""SQLAlchemy ORM models for all database tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Shared declarative base for all models."""


class User(Base):
    """Application user authenticated through Google OAuth."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    google_sub: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user", cascade="all, delete-orphan")
    google_accounts: Mapped[list[GoogleAccount]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


class AuthSession(Base):
    """Server-side auth session tracked by hashed opaque token."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")

    def __repr__(self) -> str:
        return f"<AuthSession id={self.id} user_id={self.user_id}>"


class GoogleAccount(Base):
    """Google OAuth tokens and identity for a user."""

    __tablename__ = "google_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "google_sub", name="uq_google_account_user_sub"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    google_sub: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="google_accounts")

    def __repr__(self) -> str:
        return f"<GoogleAccount id={self.id} user_id={self.user_id} email={self.email!r}>"


class Application(Base):
    """A tracked job application."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "company", "job_title", name="uq_owner_company_job_title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    company: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    normalized_company: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    job_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_sender: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="已申请", index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="email")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    status_history: Mapped[list[StatusHistory]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="StatusHistory.changed_at"
    )
    processed_emails: Mapped[list[ProcessedEmail]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Application id={self.id} company={self.company!r} title={self.job_title!r} status={self.status!r}>"


class StatusHistory(Base):
    """Audit trail of application status changes."""

    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    application_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    old_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str] = mapped_column(String(50), nullable=False)
    change_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationship
    application: Mapped[Application] = relationship(back_populates="status_history")

    def __repr__(self) -> str:
        return f"<StatusHistory app_id={self.application_id} {self.old_status!r}->{self.new_status!r}>"


class ProcessedEmail(Base):
    """Record of every email that was scanned."""

    __tablename__ = "processed_emails"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "uid", "email_account", "email_folder", name="uq_owner_uid_account_folder"),
        UniqueConstraint("owner_user_id", "gmail_message_id", name="uq_owner_gmail_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    email_account: Mapped[str] = mapped_column(String(300), nullable=False)
    email_folder: Mapped[str] = mapped_column(String(100), nullable=False, default="INBOX")

    # Gmail-specific identifiers for thread linking
    gmail_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender: Mapped[str | None] = mapped_column(String(300), nullable=True)
    email_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_job_related: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    application_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("applications.id", ondelete="SET NULL"), nullable=True
    )
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Linking metadata
    link_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationship
    application: Mapped[Application | None] = relationship(back_populates="processed_emails")

    def __repr__(self) -> str:
        return f"<ProcessedEmail uid={self.uid} thread={self.gmail_thread_id} app_id={self.application_id}>"


class ScanState(Base):
    """Tracks the last scanned UID per user+account+folder."""

    __tablename__ = "scan_state"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "email_account", "email_folder", name="uq_owner_account_folder"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    email_account: Mapped[str] = mapped_column(String(300), nullable=False)
    email_folder: Mapped[str] = mapped_column(String(100), nullable=False, default="INBOX")
    last_uid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ScanState owner={self.owner_user_id} account={self.email_account!r} "
            f"folder={self.email_folder!r} uid={self.last_uid}>"
        )
