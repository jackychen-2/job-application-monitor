"""Authentication dependencies for FastAPI routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from job_monitor.auth.security import hash_token
from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db
from job_monitor.models import AuthSession, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> User:
    token = request.cookies.get(config.auth_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    token_hash = hash_token(token)
    session_row = (
        db.query(AuthSession)
        .filter(
            AuthSession.session_token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > _utcnow(),
        )
        .first()
    )
    if session_row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    user = db.query(User).filter(User.id == session_row.user_id, User.is_active == True).first()  # noqa: E712
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    session_row.last_seen_at = _utcnow()
    db.info["owner_user_id"] = user.id
    return user


def get_owner_scoped_db(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Session:
    """Return DB session scoped to current user ownership."""
    db.info["owner_user_id"] = current_user.id
    return db
