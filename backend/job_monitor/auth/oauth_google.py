"""Google OAuth helpers and token lifecycle management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
import secrets

import httpx
import structlog
from sqlalchemy.orm import Session

from job_monitor.auth.tokens import decrypt_token, encrypt_token
from job_monitor.config import AppConfig
from job_monitor.models import GoogleAccount, User

logger = structlog.get_logger(__name__)

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# state -> expiry (UTC)
_oauth_state_store: dict[str, datetime] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_google_authorize_url(config: AppConfig) -> str:
    state = secrets.token_urlsafe(24)
    _oauth_state_store[state] = _utcnow() + timedelta(minutes=10)

    params = {
        "client_id": config.google_client_id,
        "redirect_uri": config.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.google_oauth_scopes_list),
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"


def validate_oauth_state(state: str) -> bool:
    expiry = _oauth_state_store.pop(state, None)
    if expiry is None:
        return False
    return _utcnow() <= expiry


def _exchange_code_for_tokens(code: str, config: AppConfig) -> dict[str, Any]:
    if not config.google_client_id or not config.google_client_secret.get_secret_value():
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required")

    response = httpx.post(
        _GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret.get_secret_value(),
            "redirect_uri": config.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if "access_token" not in data:
        raise RuntimeError("Google token response missing access_token")
    return data


def _fetch_google_userinfo(access_token: str) -> dict[str, Any]:
    response = httpx.get(
        _GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if "sub" not in data or "email" not in data:
        raise RuntimeError("Google userinfo response missing required fields")
    return data


def upsert_user_from_google_oauth(code: str, state: str, session: Session, config: AppConfig) -> User:
    """Validate OAuth callback and upsert user + Google account."""
    if not validate_oauth_state(state):
        raise RuntimeError("Invalid or expired OAuth state")

    token_payload = _exchange_code_for_tokens(code, config)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    expires_in = int(token_payload.get("expires_in", 3600))
    scope = token_payload.get("scope")

    userinfo = _fetch_google_userinfo(access_token)
    google_sub = str(userinfo["sub"]).strip()
    email = str(userinfo["email"]).strip().lower()
    display_name = str(userinfo.get("name") or "").strip() or None

    user = session.query(User).filter(User.email == email).first()
    if user is None:
        user = session.query(User).filter(User.google_sub == google_sub).first()

    if user is None:
        user = User(email=email, google_sub=google_sub, display_name=display_name, is_active=True)
        session.add(user)
        session.flush()
    else:
        if not user.google_sub:
            user.google_sub = google_sub
        if display_name:
            user.display_name = display_name

    account = session.query(GoogleAccount).filter(GoogleAccount.user_id == user.id).first()
    if account is None:
        if not refresh_token:
            raise RuntimeError("Google did not return refresh_token on first authorization")
        account = GoogleAccount(
            user_id=user.id,
            google_sub=google_sub,
            email=email,
            refresh_token_encrypted=encrypt_token(refresh_token, config.token_encryption_key.get_secret_value()),
            access_token_encrypted=encrypt_token(access_token, config.token_encryption_key.get_secret_value()),
            access_token_expires_at=_utcnow() + timedelta(seconds=expires_in),
            scope=scope,
        )
        session.add(account)
    else:
        account.google_sub = google_sub
        account.email = email
        account.scope = scope
        account.access_token_encrypted = encrypt_token(
            access_token,
            config.token_encryption_key.get_secret_value(),
        )
        account.access_token_expires_at = _utcnow() + timedelta(seconds=expires_in)
        if refresh_token:
            account.refresh_token_encrypted = encrypt_token(
                refresh_token,
                config.token_encryption_key.get_secret_value(),
            )

    logger.info("google_oauth_user_upserted", user_id=user.id, email=email)
    return user


def get_valid_google_access_token(
    session: Session,
    user_id: int,
    config: AppConfig,
) -> tuple[str, str]:
    """Return a usable Google access token and connected mailbox email."""
    account = session.query(GoogleAccount).filter(GoogleAccount.user_id == user_id).first()
    if account is None:
        raise RuntimeError("Google account not linked")

    now = _utcnow()
    if (
        account.access_token_encrypted
        and account.access_token_expires_at
        and account.access_token_expires_at > now + timedelta(seconds=60)
    ):
        return decrypt_token(account.access_token_encrypted, config.token_encryption_key.get_secret_value()), account.email

    refresh_token = decrypt_token(account.refresh_token_encrypted, config.token_encryption_key.get_secret_value())

    response = httpx.post(
        _GOOGLE_TOKEN_URL,
        data={
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret.get_secret_value(),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    response.raise_for_status()
    token_payload = response.json()
    new_access_token = token_payload.get("access_token")
    expires_in = int(token_payload.get("expires_in", 3600))
    if not new_access_token:
        raise RuntimeError("Google refresh token response missing access_token")

    account.access_token_encrypted = encrypt_token(
        new_access_token,
        config.token_encryption_key.get_secret_value(),
    )
    account.access_token_expires_at = now + timedelta(seconds=expires_in)
    session.flush()

    return new_access_token, account.email
