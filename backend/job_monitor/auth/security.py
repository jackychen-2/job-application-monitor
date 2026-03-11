"""Authentication security helpers."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Response

from job_monitor.config import AppConfig

_SESSION_HINT_COOKIE = "job_monitor_session_hint"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_session_token() -> str:
    """Generate an opaque session token."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Hash session token before storing in DB."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry(config: AppConfig) -> datetime:
    return utcnow() + timedelta(days=config.auth_session_ttl_days)


def _set_session_hint_cookie(response: Response, config: AppConfig) -> None:
    response.set_cookie(
        key=_SESSION_HINT_COOKIE,
        value="1",
        max_age=config.auth_session_ttl_days * 24 * 60 * 60,
        httponly=False,
        secure=config.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def set_session_cookie(response: Response, token: str, config: AppConfig) -> None:
    response.set_cookie(
        key=config.auth_cookie_name,
        value=token,
        max_age=config.auth_session_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=config.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    _set_session_hint_cookie(response, config)


def clear_session_cookie(response: Response, config: AppConfig) -> None:
    response.delete_cookie(
        key=config.auth_cookie_name,
        path="/",
        httponly=True,
        secure=config.auth_cookie_secure,
        samesite="lax",
    )
    response.delete_cookie(
        key=_SESSION_HINT_COOKIE,
        path="/",
        httponly=False,
        secure=config.auth_cookie_secure,
        samesite="lax",
    )
