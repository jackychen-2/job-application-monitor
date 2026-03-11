from __future__ import annotations

from pathlib import Path

from job_monitor.config import AppConfig
import job_monitor.database as database


def _reset_database_module() -> None:
    if database._engine is not None:
        database._engine.dispose()
    database._engine = None
    database._SessionLocal = None


def _config(db_path: Path, *, startup_cleanup_enabled: bool) -> AppConfig:
    return AppConfig(
        database_url=f"sqlite:///{db_path}",
        llm_enabled=False,
        startup_cleanup_enabled=startup_cleanup_enabled,
    )


def test_init_db_skips_startup_cleanup_by_default(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(database, "_cleanup_on_startup", lambda: calls.append("called"))
    _reset_database_module()

    try:
        database.init_db(_config(tmp_path / "default.db", startup_cleanup_enabled=False))
        assert calls == []
    finally:
        _reset_database_module()


def test_init_db_runs_startup_cleanup_when_enabled(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(database, "_cleanup_on_startup", lambda: calls.append("called"))
    _reset_database_module()

    try:
        database.init_db(_config(tmp_path / "enabled.db", startup_cleanup_enabled=True))
        assert calls == ["called"]
    finally:
        _reset_database_module()
