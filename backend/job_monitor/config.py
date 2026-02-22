"""Application configuration with Pydantic Settings validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """All application settings, loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── IMAP ──────────────────────────────────────────────
    imap_host: str
    imap_port: int = 993
    email_username: str
    email_password: SecretStr
    email_folder: str = "INBOX"

    # ── Database ──────────────────────────────────────────
    database_url: str = "sqlite:///job_monitor.db"

    # ── Scanning ──────────────────────────────────────────
    max_scan_emails: int = 20
    imap_timeout_sec: int = 30

    # ── LLM ───────────────────────────────────────────────
    llm_enabled: bool = True
    llm_provider: str = "openai"  # openai | anthropic | ollama (future)
    llm_model: str = "gpt-4o-mini"
    llm_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")  # backward compat
    llm_timeout_sec: int = 45
    cost_input_per_mtok: float = 0.15
    cost_output_per_mtok: float = 0.60

    # ── Server ────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ── Logging ───────────────────────────────────────────
    log_level: str = "INFO"
    log_file: Optional[str] = None

    # ── Validators ────────────────────────────────────────
    @field_validator("llm_enabled", mode="before")
    @classmethod
    def parse_bool(cls, v: object) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return bool(v)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def ensure_string(cls, v: object) -> str:
        return str(v).strip()

    @model_validator(mode="after")
    def _resolve_api_key(self) -> "AppConfig":
        """Fall back to OPENAI_API_KEY if LLM_API_KEY is empty."""
        if not self.llm_api_key.get_secret_value() and self.openai_api_key.get_secret_value():
            self.llm_api_key = self.openai_api_key
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_path(self) -> Optional[Path]:
        """Return the SQLite file path, or None for non-file databases."""
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.replace("sqlite:///", ""))
        return None


# Runtime override for LLM enabled — set via API without restarting the server.
# None means "use the value from .env / environment variables".
_llm_enabled_override: Optional[bool] = None


def get_config() -> AppConfig:
    """Load and return validated application config.

    If ``_llm_enabled_override`` has been set (via the eval settings API),
    that value takes precedence over the environment variable.
    """
    cfg = AppConfig()  # type: ignore[call-arg]
    if _llm_enabled_override is not None:
        # Pydantic models are normally immutable; use object.__setattr__ to bypass
        object.__setattr__(cfg, "llm_enabled", _llm_enabled_override)
    return cfg


def set_llm_enabled(value: bool) -> None:
    """Override the llm_enabled setting at runtime (persists until server restart)."""
    global _llm_enabled_override
    _llm_enabled_override = value
