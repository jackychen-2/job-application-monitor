"""Authentication API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from job_monitor.auth.deps import get_current_user
from job_monitor.auth.oauth_google import (
    build_google_authorize_url,
    generate_oauth_state,
    google_account_has_required_gmail_scope,
    upsert_user_from_google_oauth,
)
from job_monitor.auth.security import (
    clear_oauth_state_cookie,
    clear_session_cookie,
    generate_session_token,
    hash_token,
    session_expiry,
    set_oauth_state_cookie,
    set_session_cookie,
    utcnow,
)
from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db
from job_monitor.models import AuthSession, GoogleAccount, Journey, User
from job_monitor.schemas import AccountOut, AuthUserOut, DeleteAccountOut, ProfileUpdate

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _build_account_out(db: Session, current_user: User) -> AccountOut:
    active_journey = None
    if current_user.active_journey_id is not None:
        active_journey = db.query(Journey).filter(Journey.id == current_user.active_journey_id).first()

    google_account = (
        db.query(GoogleAccount)
        .filter(GoogleAccount.user_id == current_user.id)
        .order_by(GoogleAccount.id.asc())
        .first()
    )
    active_session_count = (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == current_user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
        .count()
    )
    journey_count = db.query(Journey).filter(Journey.owner_user_id == current_user.id).count()

    return AccountOut(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        avatar_url=current_user.avatar_url,
        created_at=current_user.created_at,
        active_journey_id=current_user.active_journey_id,
        active_journey_name=active_journey.name if active_journey is not None else None,
        google_account_email=google_account.email if google_account is not None else None,
        google_account_connected=google_account is not None,
        gmail_scope_granted=(
            google_account is not None
            and google_account_has_required_gmail_scope(google_account.scope)
        ),
        active_session_count=active_session_count,
        journey_count=journey_count,
    )


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
    state = generate_oauth_state()
    auth_url = build_google_authorize_url(config, state=state)
    response = RedirectResponse(url=auth_url, status_code=302)
    set_oauth_state_cookie(response, state, config)
    return response


@router.get("/google/callback")
def google_callback(
    code: str,
    state: str,
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    expected_state = request.cookies.get("job_monitor_oauth_state")
    try:
        user = upsert_user_from_google_oauth(
            code=code,
            state=state,
            expected_state=expected_state,
            session=db,
            config=config,
        )
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
    clear_oauth_state_cookie(response, config)
    set_session_cookie(response, raw_session_token, config)
    return response


@router.get("/me", response_model=AuthUserOut)
def auth_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "display_name": current_user.display_name,
        "avatar_url": current_user.avatar_url,
        "active_journey_id": current_user.active_journey_id,
    }


@router.get("/account", response_model=AccountOut)
def get_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountOut:
    return _build_account_out(db, current_user)


@router.patch("/profile", response_model=AccountOut)
def update_profile(
    body: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountOut:
    display_name = (body.display_name or "").strip() or None
    current_user.display_name = display_name
    db.flush()
    return _build_account_out(db, current_user)


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


@router.delete("/account", response_model=DeleteAccountOut)
def delete_account(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> DeleteAccountOut:
    db.delete(current_user)
    clear_session_cookie(response, config)
    return DeleteAccountOut(status="deleted")
