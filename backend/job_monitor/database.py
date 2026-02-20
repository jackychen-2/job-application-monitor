"""Database engine, session management, and initialization."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Generator

import structlog
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.config import AppConfig
from job_monitor.models import Base

logger = structlog.get_logger(__name__)

# Module-level engine and session factory (initialized by init_db)
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _enable_sqlite_wal(dbapi_conn: object, _connection_record: object) -> None:
    """Enable WAL mode for SQLite for better concurrent read performance."""
    cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(config: AppConfig) -> Engine:
    """Create the database engine, tables, and return the engine."""
    global _engine, _SessionLocal

    connect_args = {}
    if config.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_engine(
        config.database_url,
        connect_args=connect_args,
        echo=False,
        pool_pre_ping=True,
    )

    # SQLite-specific optimizations
    if config.database_url.startswith("sqlite"):
        event.listen(_engine, "connect", _enable_sqlite_wal)

    # Create all tables
    Base.metadata.create_all(bind=_engine)
    logger.info("database_initialized", url=config.database_url)

    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    
    # Re-process data with latest non-LLM logic on startup
    _cleanup_on_startup()
    
    return _engine


def _cleanup_on_startup() -> None:
    """Re-process existing data with latest rules on startup (skip LLM)."""
    from job_monitor.linking.resolver import normalize_company
    from job_monitor.models import Application
    
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
        
        # Step 2: Merge duplicates (same normalized_company + job_title)
        # Keep the one with most recent email_date, delete others
        from sqlalchemy import func
        duplicates_deleted = 0
        
        # Find groups with duplicates
        dup_groups = (
            session.query(
                Application.normalized_company,
                Application.job_title,
                func.count(Application.id).label("cnt"),
            )
            .group_by(Application.normalized_company, Application.job_title)
            .having(func.count(Application.id) > 1)
            .all()
        )
        
        for norm_company, job_title, _ in dup_groups:
            # Get all apps in this group, ordered by email_date desc
            group_apps = (
                session.query(Application)
                .filter(
                    Application.normalized_company == norm_company,
                    Application.job_title == job_title if job_title else (
                        (Application.job_title == None) | (Application.job_title == "")
                    ),
                )
                .order_by(Application.email_date.desc().nullslast())
                .all()
            )
            
            if len(group_apps) > 1:
                # Find the record with most recent email_date
                most_recent_app = max(
                    group_apps,
                    key=lambda a: a.email_date if a.email_date else datetime.min
                )
                
                # Keep the first record but update with most recent email info
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
                    )
        
        # Step 3: Update each Application's email_date to most recent ProcessedEmail
        from job_monitor.models import ProcessedEmail
        email_dates_updated = 0
        
        for app in session.query(Application).all():
            most_recent_email = (
                session.query(ProcessedEmail)
                .filter(ProcessedEmail.application_id == app.id)
                .order_by(ProcessedEmail.email_date.desc().nullslast())
                .first()
            )
            
            if most_recent_email and most_recent_email.email_date:
                if app.email_date != most_recent_email.email_date:
                    app.email_date = most_recent_email.email_date
                    app.email_subject = most_recent_email.subject
                    app.email_sender = most_recent_email.sender
                    email_dates_updated += 1
        
        session.commit()
        
        if normalized_count > 0 or duplicates_deleted > 0 or email_dates_updated > 0:
            logger.info(
                "startup_cleanup_complete",
                normalized=normalized_count,
                duplicates_deleted=duplicates_deleted,
                email_dates_updated=email_dates_updated,
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
