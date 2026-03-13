from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.auth.oauth_google import (
    GoogleMailboxPermissionsError,
    get_valid_google_access_token,
)
from job_monitor.auth.tokens import encrypt_token
from job_monitor.config import AppConfig
from job_monitor.models import Base, GoogleAccount, User


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _config(token_encryption_key: str) -> AppConfig:
    return AppConfig(
        database_url="sqlite:///:memory:",
        google_client_id="client-id",
        google_client_secret="client-secret",
        token_encryption_key=token_encryption_key,
    )


def test_get_valid_google_access_token_requires_gmail_read_scope_when_scope_is_known() -> None:
    session = _new_session()
    try:
        key = Fernet.generate_key().decode("utf-8")
        config = _config(key)
        user = User(email="user@example.com", google_sub="sub-1", is_active=True)
        session.add(user)
        session.flush()
        session.add(
            GoogleAccount(
                user_id=user.id,
                google_sub="sub-1",
                email="user@example.com",
                refresh_token_encrypted=encrypt_token("refresh-token", key),
                access_token_encrypted=encrypt_token("access-token", key),
                access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                scope="openid https://www.googleapis.com/auth/userinfo.email",
            )
        )
        session.commit()

        with pytest.raises(GoogleMailboxPermissionsError):
            get_valid_google_access_token(session, user.id, config)
    finally:
        session.close()


def test_get_valid_google_access_token_returns_cached_token_when_gmail_read_scope_present() -> None:
    session = _new_session()
    try:
        key = Fernet.generate_key().decode("utf-8")
        config = _config(key)
        user = User(email="user@example.com", google_sub="sub-1", is_active=True)
        session.add(user)
        session.flush()
        session.add(
            GoogleAccount(
                user_id=user.id,
                google_sub="sub-1",
                email="user@example.com",
                refresh_token_encrypted=encrypt_token("refresh-token", key),
                access_token_encrypted=encrypt_token("access-token", key),
                access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                scope=(
                    "openid https://www.googleapis.com/auth/userinfo.email "
                    "https://www.googleapis.com/auth/gmail.readonly"
                ),
            )
        )
        session.commit()

        token, email = get_valid_google_access_token(session, user.id, config)

        assert token == "access-token"
        assert email == "user@example.com"
    finally:
        session.close()
