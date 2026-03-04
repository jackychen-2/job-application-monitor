"""Authentication API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_monitor.auth.deps import get_current_user
from job_monitor.auth.oauth_google import build_google_authorize_url, upsert_user_from_google_oauth
from job_monitor.auth.security import clear_session_cookie, generate_session_token, hash_token, session_expiry, set_session_cookie, utcnow
from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db
from job_monitor.models import AuthSession, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/google/start")
def google_start(config: AppConfig = Depends(get_config)):
    missing: list[str] = []
    if not config.google_client_id.strip():
        missing.append("GOOGLE_CLIENT_ID")
    if not config.google_client_secret.get_secret_value().strip():
        missing.append("GOOGLE_CLIENT_SECRET")
    if not config.google_redirect_uri.strip():
        missing.append("GOOGLE_REDIRECT_URI")
    if not config.token_encryption_key.get_secret_value().strip():
        missing.append("TOKEN_ENCRYPTION_KEY")
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Google OAuth is not configured. Missing: {', '.join(missing)}",
        )
    auth_url = build_google_authorize_url(config)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/google/callback")
def google_callback(
    code: str,
    state: str,
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    try:
        user = upsert_user_from_google_oauth(code=code, state=state, session=db, config=config)
    except Exception as exc:  # pragma: no cover - external provider interactions
        raise HTTPException(status_code=400, detail=f"Google OAuth failed: {exc}") from exc

    raw_session_token = generate_session_token()
    db.add(
        AuthSession(
            user_id=user.id,
            session_token_hash=hash_token(raw_session_token),
            expires_at=session_expiry(config),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            last_seen_at=utcnow(),
        )
    )
    db.commit()

    response = RedirectResponse(url=config.frontend_url, status_code=302)
    set_session_cookie(response, raw_session_token, config)
    return response


@router.get("/me")
def auth_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "display_name": current_user.display_name,
    }


@router.post("/logout")
def auth_logout(
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    token = request.cookies.get(config.auth_cookie_name)
    if token:
        token_hash = hash_token(token)
        session_row = (
            db.query(AuthSession)
            .filter(AuthSession.session_token_hash == token_hash, AuthSession.revoked_at.is_(None))
            .first()
        )
        if session_row is not None:
            session_row.revoked_at = utcnow()
            db.commit()

    response = JSONResponse({"status": "ok"})
    clear_session_cookie(response, config)
    return response
