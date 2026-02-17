"""Database engine, session management, and initialization."""

from __future__ import annotations

from contextlib import contextmanager
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
    return _engine


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
