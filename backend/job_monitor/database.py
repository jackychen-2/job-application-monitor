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
    ApplicationMergeEvent,
    ApplicationMergeItem,
    AuthSession,
    Base,
    GoogleAccount,
    Journey,
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
    Journey,
    Application,
    ApplicationMergeEvent,
    ApplicationMergeItem,
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

_JOURNEY_SCOPED_MODELS = (
    Application,
    ApplicationMergeEvent,
    ApplicationMergeItem,
    ProcessedEmail,
    StatusHistory,
    ScanState,
)

_OWNER_SCOPED_TABLES = (
    "journeys",
    "applications",
    "application_merge_events",
    "application_merge_items",
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

_JOURNEY_SCOPED_TABLES = (
    "applications",
    "application_merge_events",
    "application_merge_items",
    "processed_emails",
    "status_history",
    "scan_state",
)


@event.listens_for(Session, "do_orm_execute")
def _inject_owner_scope(execute_state):
    """Automatically scope owner/journey-bound ORM selects to request context."""
    owner_user_id = execute_state.session.info.get("owner_user_id")
    journey_id = execute_state.session.info.get("journey_id")
    if not execute_state.is_select:
        return

    statement = execute_state.statement
    if owner_user_id:
        for model in _OWNER_SCOPED_MODELS:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda cls: cls.owner_user_id == owner_user_id,  # noqa: B023
                    include_aliases=True,
                )
            )
    if journey_id:
        for model in _JOURNEY_SCOPED_MODELS:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda cls: cls.journey_id == journey_id,  # noqa: B023
                    include_aliases=True,
                )
            )
    execute_state.statement = statement


@event.listens_for(Session, "before_flush")
def _assign_owner_before_flush(session: Session, flush_context, instances) -> None:  # type: ignore[no-untyped-def]
    """Default owner/journey ids on newly created scoped rows."""
    owner_user_id = session.info.get("owner_user_id")
    journey_id = session.info.get("journey_id")
    if not owner_user_id and not journey_id:
        return

    for obj in session.new:
        if owner_user_id and hasattr(obj, "owner_user_id") and getattr(obj, "owner_user_id", None) is None:
            setattr(obj, "owner_user_id", owner_user_id)
        if journey_id and hasattr(obj, "journey_id") and getattr(obj, "journey_id", None) is None:
            setattr(obj, "journey_id", journey_id)


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
    """Run idempotent schema upgrades for owner + journey scoping."""
    if _engine is None or _SessionLocal is None:
        return

    inspector = inspect(_engine)
    existing_tables = set(inspector.get_table_names())

    with _engine.begin() as conn:
        if "users" in existing_tables:
            user_columns = {col["name"] for col in inspector.get_columns("users")}
            if "active_journey_id" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN active_journey_id INTEGER"))
                logger.info("schema_upgrade_added_column", table="users", column="active_journey_id")
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_users_active_journey_id "
                    "ON users(active_journey_id)"
                )
            )

        if "applications" in existing_tables:
            app_columns = {col["name"] for col in inspector.get_columns("applications")}
            if "dedupe_locked" not in app_columns:
                conn.execute(
                    text(
                        "ALTER TABLE applications "
                        "ADD COLUMN dedupe_locked BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
                logger.info(
                    "schema_upgrade_added_column",
                    table="applications",
                    column="dedupe_locked",
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_applications_dedupe_locked "
                    "ON applications(dedupe_locked)"
                )
            )

        if "application_merge_events" in existing_tables:
            merge_cols = {col["name"] for col in inspector.get_columns("application_merge_events")}
            if "merge_source" not in merge_cols:
                conn.execute(
                    text(
                        "ALTER TABLE application_merge_events "
                        "ADD COLUMN merge_source VARCHAR(30) NOT NULL DEFAULT 'manual'"
                    )
                )
                logger.info(
                    "schema_upgrade_added_column",
                    table="application_merge_events",
                    column="merge_source",
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_application_merge_events_merge_source "
                    "ON application_merge_events(merge_source)"
                )
            )

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

        for table_name in _JOURNEY_SCOPED_TABLES:
            if table_name not in existing_tables:
                continue

            columns = {col["name"] for col in inspector.get_columns(table_name)}
            if "journey_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN journey_id INTEGER"))
                logger.info("schema_upgrade_added_column", table=table_name, column="journey_id")
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS idx_{table_name}_journey_id "
                    f"ON {table_name}(journey_id)"
                )
            )

    if config.database_url.startswith("sqlite"):
        _rebuild_sqlite_journey_scoped_tables_if_needed()

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
            logger.info("schema_owner_backfill_not_needed")
        else:
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
            logger.info("schema_owner_backfill_complete", owner_email=owner_email, rows=rows_needing_backfill)

        _ensure_default_journeys_and_backfill(session, existing_tables)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _sqlite_unique_index_exists(table_name: str, expected_columns: list[str]) -> bool:
    """Return True if SQLite table has a unique index exactly on expected columns."""
    if _engine is None:
        return False

    with _engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA index_list('{table_name}')")).mappings().all()
        for row in rows:
            if int(row.get("unique", 0) or 0) != 1:
                continue
            index_name = row["name"]
            col_rows = conn.execute(text(f"PRAGMA index_info('{index_name}')")).mappings().all()
            cols = [str(col["name"]) for col in col_rows]
            if cols == expected_columns:
                return True
    return False


def _rebuild_sqlite_journey_scoped_tables_if_needed() -> None:
    """Rebuild SQLite tables when unique/index constraints differ from current schema."""
    if _engine is None:
        return

    has_app_unique_journey_key = _sqlite_unique_index_exists(
        "applications",
        ["owner_user_id", "journey_id", "company", "job_title", "req_id"],
    )
    has_app_unique_legacy_key = _sqlite_unique_index_exists(
        "applications",
        ["owner_user_id", "company", "job_title", "req_id"],
    )
    # Current applications schema intentionally allows duplicate company/title/req combinations.
    # Rebuild only when an old unique key still exists.
    needs_applications = has_app_unique_journey_key or has_app_unique_legacy_key
    needs_processed_uid = not _sqlite_unique_index_exists(
        "processed_emails",
        ["owner_user_id", "journey_id", "uid", "email_account", "email_folder"],
    )
    needs_processed_gmail = not _sqlite_unique_index_exists(
        "processed_emails",
        ["owner_user_id", "journey_id", "gmail_message_id"],
    )
    needs_scan_state = not _sqlite_unique_index_exists(
        "scan_state",
        ["owner_user_id", "journey_id", "email_account", "email_folder"],
    )

    if not any([needs_applications, needs_processed_uid or needs_processed_gmail, needs_scan_state]):
        return

    raw_conn = _engine.raw_connection()
    cursor = raw_conn.cursor()
    try:
        logger.info(
            "schema_sqlite_rebuild_required",
            applications=needs_applications,
            processed_emails=needs_processed_uid or needs_processed_gmail,
            scan_state=needs_scan_state,
        )
        cursor.execute("PRAGMA foreign_keys=OFF")
        if needs_scan_state:
            _sqlite_rebuild_scan_state(cursor)
        if needs_processed_uid or needs_processed_gmail:
            _sqlite_rebuild_processed_emails(cursor)
        if needs_applications:
            _sqlite_rebuild_applications(cursor)
        cursor.execute("PRAGMA foreign_keys=ON")
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        cursor.close()
        raw_conn.close()


def _sqlite_rebuild_applications(cursor) -> None:  # type: ignore[no-untyped-def]
    logger.info("schema_sqlite_rebuild_table", table="applications")
    cursor.execute("PRAGMA table_info('applications')")
    app_columns = {str(row[1]) for row in cursor.fetchall()}
    has_dedupe_locked = "dedupe_locked" in app_columns

    cursor.execute(
        """
        CREATE TABLE applications__journey_new (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER,
            journey_id INTEGER,
            company VARCHAR(200) NOT NULL,
            normalized_company VARCHAR(200),
            job_title VARCHAR(300),
            req_id VARCHAR(80),
            email_subject TEXT,
            email_sender VARCHAR(300),
            email_date DATETIME,
            status VARCHAR(50) NOT NULL DEFAULT '已申请',
            source VARCHAR(50) NOT NULL DEFAULT 'email',
            notes TEXT,
            dedupe_locked BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY(journey_id) REFERENCES journeys (id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        f"""
        INSERT INTO applications__journey_new (
            id, owner_user_id, journey_id, company, normalized_company, job_title, req_id,
            email_subject, email_sender, email_date, status, source, notes, dedupe_locked, created_at, updated_at
        )
        SELECT
            id, owner_user_id, journey_id, company, normalized_company, job_title, req_id,
            email_subject, email_sender, email_date, status, source, notes,
            {"dedupe_locked" if has_dedupe_locked else "0"},
            created_at, updated_at
        FROM applications
        """
    )
    cursor.execute("DROP TABLE applications")
    cursor.execute("ALTER TABLE applications__journey_new RENAME TO applications")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_applications_owner_user_id ON applications(owner_user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_applications_journey_id ON applications(journey_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_applications_company ON applications(company)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_applications_normalized_company ON applications(normalized_company)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_applications_status ON applications(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_applications_dedupe_locked ON applications(dedupe_locked)")


def _sqlite_rebuild_processed_emails(cursor) -> None:  # type: ignore[no-untyped-def]
    logger.info("schema_sqlite_rebuild_table", table="processed_emails")
    cursor.execute(
        """
        CREATE TABLE processed_emails__journey_new (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER,
            journey_id INTEGER,
            uid INTEGER NOT NULL,
            email_account VARCHAR(300) NOT NULL,
            email_folder VARCHAR(100) NOT NULL DEFAULT 'INBOX',
            subject TEXT,
            sender VARCHAR(300),
            email_date DATETIME,
            is_job_related BOOLEAN NOT NULL DEFAULT 0,
            application_id INTEGER,
            llm_used BOOLEAN NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd FLOAT NOT NULL DEFAULT 0.0,
            processed_at DATETIME NOT NULL,
            gmail_message_id VARCHAR(200),
            gmail_thread_id VARCHAR(100),
            link_method VARCHAR(20),
            needs_review BOOLEAN NOT NULL DEFAULT 0,
            FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY(journey_id) REFERENCES journeys (id) ON DELETE CASCADE,
            FOREIGN KEY(application_id) REFERENCES applications (id) ON DELETE SET NULL,
            UNIQUE (owner_user_id, journey_id, uid, email_account, email_folder),
            UNIQUE (owner_user_id, journey_id, gmail_message_id)
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO processed_emails__journey_new (
            id, owner_user_id, journey_id, uid, email_account, email_folder, subject, sender, email_date,
            is_job_related, application_id, llm_used, prompt_tokens, completion_tokens, estimated_cost_usd,
            processed_at, gmail_message_id, gmail_thread_id, link_method, needs_review
        )
        SELECT
            id, owner_user_id, journey_id, uid, email_account, email_folder, subject, sender, email_date,
            is_job_related, application_id, llm_used, prompt_tokens, completion_tokens, estimated_cost_usd,
            processed_at, gmail_message_id, gmail_thread_id, link_method, needs_review
        FROM processed_emails
        """
    )
    cursor.execute("DROP TABLE processed_emails")
    cursor.execute("ALTER TABLE processed_emails__journey_new RENAME TO processed_emails")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_emails_owner_user_id ON processed_emails(owner_user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_emails_journey_id ON processed_emails(journey_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_processed_emails_uid ON processed_emails(uid)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_processed_emails_gmail_message_id ON processed_emails(gmail_message_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_emails_gmail_thread_id ON processed_emails(gmail_thread_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_emails_needs_review ON processed_emails(needs_review)")


def _sqlite_rebuild_scan_state(cursor) -> None:  # type: ignore[no-untyped-def]
    logger.info("schema_sqlite_rebuild_table", table="scan_state")
    cursor.execute(
        """
        CREATE TABLE scan_state__journey_new (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER,
            journey_id INTEGER,
            email_account VARCHAR(300) NOT NULL,
            email_folder VARCHAR(100) NOT NULL DEFAULT 'INBOX',
            last_uid INTEGER NOT NULL DEFAULT 0,
            last_scan_at DATETIME,
            FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY(journey_id) REFERENCES journeys (id) ON DELETE CASCADE,
            UNIQUE (owner_user_id, journey_id, email_account, email_folder)
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO scan_state__journey_new (
            id, owner_user_id, journey_id, email_account, email_folder, last_uid, last_scan_at
        )
        SELECT
            id, owner_user_id, journey_id, email_account, email_folder, last_uid, last_scan_at
        FROM scan_state
        """
    )
    cursor.execute("DROP TABLE scan_state")
    cursor.execute("ALTER TABLE scan_state__journey_new RENAME TO scan_state")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_state_owner_user_id ON scan_state(owner_user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_state_journey_id ON scan_state(journey_id)")


def _ensure_default_journeys_and_backfill(session: Session, existing_tables: set[str]) -> None:
    """Create per-user default journeys and backfill journey_id to legacy rows."""
    users = session.query(User).order_by(User.id.asc()).all()
    default_journey_by_user: dict[int, int] = {}

    for user in users:
        journeys = (
            session.query(Journey)
            .filter(Journey.owner_user_id == user.id)
            .order_by(Journey.id.asc())
            .all()
        )
        if not journeys:
            default = Journey(owner_user_id=user.id, name="Default Journey")
            session.add(default)
            session.flush()
            journeys = [default]
            logger.info("default_journey_created", user_id=user.id, journey_id=default.id)

        valid_ids = {j.id for j in journeys}
        if user.active_journey_id not in valid_ids:
            user.active_journey_id = journeys[0].id
        default_journey_by_user[user.id] = journeys[0].id

    updated_rows = 0
    for table_name in _JOURNEY_SCOPED_TABLES:
        if table_name not in existing_tables:
            continue
        for owner_user_id, journey_id in default_journey_by_user.items():
            result = session.execute(
                text(
                    f"UPDATE {table_name} "
                    "SET journey_id = :journey_id "
                    "WHERE owner_user_id = :owner_user_id AND journey_id IS NULL"
                ),
                {"journey_id": journey_id, "owner_user_id": owner_user_id},
            )
            if result.rowcount and result.rowcount > 0:
                updated_rows += int(result.rowcount)

    if updated_rows > 0:
        logger.info("journey_backfill_complete", rows=updated_rows)


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

        # Step 2: Merge duplicates (same owner + journey + normalized_company + title + req)
        from sqlalchemy import func

        duplicates_deleted = 0
        dup_groups = (
            session.query(
                Application.owner_user_id,
                Application.journey_id,
                Application.normalized_company,
                Application.job_title,
                Application.req_id,
                func.count(Application.id).label("cnt"),
            )
            .group_by(
                Application.owner_user_id,
                Application.journey_id,
                Application.normalized_company,
                Application.job_title,
                Application.req_id,
            )
            .having(func.count(Application.id) > 1)
            .all()
        )

        for owner_user_id, journey_id, norm_company, job_title, req_id, _ in dup_groups:
            group_apps = (
                session.query(Application)
                .filter(
                    Application.owner_user_id == owner_user_id,
                    Application.journey_id == journey_id,
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
                        journey_id=journey_id,
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
        previous_journey_scope = session.info.get("journey_id")
        for pe in company_emails:
            app = session.query(Application).get(pe.application_id)
            if not app:
                continue

            session.info["owner_user_id"] = pe.owner_user_id
            session.info["journey_id"] = pe.journey_id
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
                        journey_id=pe.journey_id,
                    )
                else:
                    logger.info(
                        "startup_relink_needs_rescan",
                        email_uid=pe.uid,
                        app_id=app.id,
                        company=app.company,
                        journey_id=pe.journey_id,
                    )

        session.info["owner_user_id"] = previous_owner_scope
        session.info["journey_id"] = previous_journey_scope
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
