"""Database engine, session management, and initialization."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Generator

import structlog
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from job_monitor.config import AppConfig
from job_monitor.models import (
    Application,
    AuthSession,
    Base,
    GoogleAccount,
    ProcessedEmail,
    ScanState,
    StatusHistory,
    User,
)
from job_monitor.eval.models import (
    CachedEmail,
    EvalApplicationGroup,
    EvalLabel,
    EvalPredictedGroup,
    EvalRun,
    EvalRunResult,
)
import job_monitor.eval.models as _eval_models  # noqa: F401 — register eval tables

logger = structlog.get_logger(__name__)

# Module-level engine and session factory (initialized by init_db)
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None

_OWNER_SCOPED_MODELS = (
    Application,
    ProcessedEmail,
    StatusHistory,
    ScanState,
    CachedEmail,
    EvalApplicationGroup,
    EvalLabel,
    EvalRun,
    EvalRunResult,
    EvalPredictedGroup,
)

_OWNER_SCOPED_TABLES = (
    "applications",
    "processed_emails",
    "status_history",
    "scan_state",
    "cached_emails",
    "eval_application_groups",
    "eval_labels",
    "eval_runs",
    "eval_run_results",
    "eval_predicted_groups",
)


@event.listens_for(Session, "do_orm_execute")
def _inject_owner_scope(execute_state):
    """Automatically scope owner-bound ORM selects to request owner_user_id."""
    owner_user_id = execute_state.session.info.get("owner_user_id")
    if not owner_user_id or not execute_state.is_select:
        return

    statement = execute_state.statement
    for model in _OWNER_SCOPED_MODELS:
        statement = statement.options(
            with_loader_criteria(
                model,
                lambda cls: cls.owner_user_id == owner_user_id,  # noqa: B023
                include_aliases=True,
            )
        )
    execute_state.statement = statement


@event.listens_for(Session, "before_flush")
def _assign_owner_before_flush(session: Session, flush_context, instances) -> None:  # type: ignore[no-untyped-def]
    """Default owner_user_id on newly created owner-scoped rows."""
    owner_user_id = session.info.get("owner_user_id")
    if not owner_user_id:
        return

    for obj in session.new:
        if hasattr(obj, "owner_user_id") and getattr(obj, "owner_user_id", None) is None:
            setattr(obj, "owner_user_id", owner_user_id)


def _enable_sqlite_wal(dbapi_conn: object, _connection_record: object) -> None:
    """Enable WAL mode for SQLite for better concurrent read performance."""
    cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def init_db(config: AppConfig) -> Engine:
    """Create the database engine, tables, and return the engine."""
    global _engine, _SessionLocal

    connect_args = {}
    if config.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30

    _engine = create_engine(
        config.database_url,
        connect_args=connect_args,
        echo=False,
        pool_pre_ping=True,
    )

    # SQLite-specific optimizations
    if config.database_url.startswith("sqlite"):
        event.listen(_engine, "connect", _enable_sqlite_wal)

    # Create all tables for new databases
    Base.metadata.create_all(bind=_engine)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    # Upgrade existing DB schema and owner backfill when needed
    _run_schema_upgrades(config)

    logger.info("database_initialized", url=config.database_url)

    # Re-process data with latest non-LLM logic on startup
    _cleanup_on_startup()

    return _engine


def _run_schema_upgrades(config: AppConfig) -> None:
    """Run idempotent additive schema upgrades for auth and owner scoping."""
    if _engine is None or _SessionLocal is None:
        return

    inspector = inspect(_engine)
    existing_tables = set(inspector.get_table_names())

    with _engine.begin() as conn:
        for table_name in _OWNER_SCOPED_TABLES:
            if table_name not in existing_tables:
                continue

            columns = {col["name"] for col in inspector.get_columns(table_name)}
            if "owner_user_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN owner_user_id INTEGER"))
                logger.info("schema_upgrade_added_column", table=table_name, column="owner_user_id")
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS idx_{table_name}_owner_user_id "
                    f"ON {table_name}(owner_user_id)"
                )
            )

    session = _SessionLocal()
    try:
        rows_needing_backfill = 0
        for table_name in _OWNER_SCOPED_TABLES:
            if table_name not in existing_tables:
                continue
            count = session.execute(
                text(f"SELECT COUNT(*) FROM {table_name} WHERE owner_user_id IS NULL")
            ).scalar_one()
            rows_needing_backfill += int(count or 0)

        if rows_needing_backfill == 0:
            return

        owner_email = config.legacy_owner_email.strip().lower()
        if not owner_email:
            raise RuntimeError(
                "LEGACY_OWNER_EMAIL is required because existing rows need owner backfill"
            )

        owner = session.query(User).filter(User.email == owner_email).first()
        if owner is None:
            owner = User(email=owner_email, display_name="Legacy Owner", is_active=True)
            session.add(owner)
            session.flush()

        for table_name in _OWNER_SCOPED_TABLES:
            if table_name not in existing_tables:
                continue
            session.execute(
                text(f"UPDATE {table_name} SET owner_user_id = :owner_id WHERE owner_user_id IS NULL"),
                {"owner_id": owner.id},
            )

        session.commit()
        logger.info("schema_owner_backfill_complete", owner_email=owner_email, rows=rows_needing_backfill)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _cleanup_on_startup() -> None:
    """Re-process existing data with latest rules on startup (skip LLM)."""
    from job_monitor.linking.resolver import normalize_company

    if _SessionLocal is None:
        return

    session = _SessionLocal()
    try:
        apps = session.query(Application).all()

        # Step 1: Re-normalize all company names
        normalized_count = 0
        for app in apps:
            new_normalized = normalize_company(app.company)
            if app.normalized_company != new_normalized:
                app.normalized_company = new_normalized
                normalized_count += 1

        # Step 2: Merge duplicates (same owner + normalized_company + job_title)
        from sqlalchemy import func

        duplicates_deleted = 0
        dup_groups = (
            session.query(
                Application.owner_user_id,
                Application.normalized_company,
                Application.job_title,
                Application.req_id,
                func.count(Application.id).label("cnt"),
            )
            .group_by(
                Application.owner_user_id,
                Application.normalized_company,
                Application.job_title,
                Application.req_id,
            )
            .having(func.count(Application.id) > 1)
            .all()
        )

        for owner_user_id, norm_company, job_title, req_id, _ in dup_groups:
            group_apps = (
                session.query(Application)
                .filter(
                    Application.owner_user_id == owner_user_id,
                    Application.normalized_company == norm_company,
                    Application.job_title == job_title
                    if job_title
                    else ((Application.job_title == None) | (Application.job_title == "")),  # noqa: E711
                    Application.req_id == req_id
                    if req_id
                    else ((Application.req_id == None) | (Application.req_id == "")),  # noqa: E711
                )
                .order_by(Application.email_date.desc().nullslast())
                .all()
            )

            if len(group_apps) > 1:
                most_recent_app = max(
                    group_apps,
                    key=lambda a: a.email_date if a.email_date else datetime.min,
                )
                keep = group_apps[0]
                if most_recent_app.email_date and keep.email_date != most_recent_app.email_date:
                    keep.email_date = most_recent_app.email_date
                    keep.email_subject = most_recent_app.email_subject
                    keep.email_sender = most_recent_app.email_sender

                for app_to_delete in group_apps[1:]:
                    session.delete(app_to_delete)
                    duplicates_deleted += 1
                    logger.info(
                        "duplicate_merged",
                        kept_id=keep.id,
                        deleted_id=app_to_delete.id,
                        company=norm_company,
                        owner_user_id=owner_user_id,
                    )

        # Step 3: Update each Application's email_date to most recent ProcessedEmail
        email_dates_updated = 0
        for app in session.query(Application).all():
            most_recent_email = (
                session.query(ProcessedEmail)
                .filter(ProcessedEmail.application_id == app.id)
                .order_by(ProcessedEmail.email_date.desc().nullslast())
                .first()
            )
            if most_recent_email and most_recent_email.email_date and app.email_date != most_recent_email.email_date:
                app.email_date = most_recent_email.email_date
                app.email_subject = most_recent_email.subject
                app.email_sender = most_recent_email.sender
                email_dates_updated += 1

        # Step 4: Re-evaluate company-linked emails with new linking rules.
        from job_monitor.extraction.rules import extract_status
        from job_monitor.linking.resolver import resolve_by_company

        relinked_count = 0
        company_emails = (
            session.query(ProcessedEmail)
            .filter(
                ProcessedEmail.link_method == "company",
                ProcessedEmail.application_id.isnot(None),
                ProcessedEmail.is_job_related == True,  # noqa: E712
            )
            .order_by(ProcessedEmail.email_date.asc())
            .all()
        )

        previous_owner_scope = session.info.get("owner_user_id")
        for pe in company_emails:
            app = session.query(Application).get(pe.application_id)
            if not app:
                continue

            session.info["owner_user_id"] = pe.owner_user_id
            email_status = extract_status(pe.subject or "", "")
            result = resolve_by_company(
                session,
                app.company,
                extracted_status=email_status,
                job_title=app.job_title,
                req_id=app.req_id,
                email_date=pe.email_date,
            )

            if not result.is_linked or result.application_id != app.id:
                if result.is_linked and result.application_id is not None:
                    pe.application_id = result.application_id
                    pe.link_method = "company_relinked"
                    relinked_count += 1
                    logger.info(
                        "startup_relinked_email",
                        email_uid=pe.uid,
                        old_app_id=app.id,
                        new_app_id=result.application_id,
                        company=app.company,
                    )
                else:
                    logger.info(
                        "startup_relink_needs_rescan",
                        email_uid=pe.uid,
                        app_id=app.id,
                        company=app.company,
                    )

        session.info["owner_user_id"] = previous_owner_scope
        session.commit()

        if normalized_count > 0 or duplicates_deleted > 0 or email_dates_updated > 0 or relinked_count > 0:
            logger.info(
                "startup_cleanup_complete",
                normalized=normalized_count,
                duplicates_deleted=duplicates_deleted,
                email_dates_updated=email_dates_updated,
                relinked=relinked_count,
            )
    except Exception as e:
        session.rollback()
        logger.warning("startup_cleanup_failed", error=str(e))
    finally:
        session.close()


def get_engine() -> Engine:
    """Return the current engine. Raises if init_db() has not been called."""
    if _engine is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the session factory. Raises if init_db() has not been called."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _SessionLocal


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context manager yielding a database session with auto-commit/rollback."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
