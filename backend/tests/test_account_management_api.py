from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

import job_monitor.main as main_module
from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db_session
from job_monitor.main import create_app
from job_monitor.models import Application, AuthSession, GoogleAccount, Journey, User
from job_monitor.auth.security import hash_token, utcnow


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        database_url=f"sqlite:///{tmp_path / 'account-test.db'}",
        auth_cookie_name="job_monitor_session",
        auth_cookie_secure=False,
        cors_origins="http://testserver",
        frontend_url="http://testserver",
    )


@contextmanager
def _client(tmp_path: Path) -> Iterator[tuple[TestClient, AppConfig]]:
    config = _config(tmp_path)
    main_module._config = config
    app = create_app()
    app.dependency_overrides[get_config] = lambda: config

    with TestClient(app) as client:
        yield client, config

    main_module._config = None


def _login_user(
    client: TestClient,
    config: AppConfig,
    *,
    email: str = "candidate@example.com",
    display_name: str | None = "Candidate",
    avatar_url: str | None = "https://example.com/avatar.png",
    session_count: int = 1,
    include_google_account: bool = False,
) -> tuple[User, list[Journey]]:
    raw_tokens = [f"session-token-{idx}" for idx in range(session_count)]

    with get_db_session() as session:
        user = User(
            email=email,
            display_name=display_name,
            avatar_url=avatar_url,
            is_active=True,
        )
        session.add(user)
        session.flush()

        journeys = [
            Journey(owner_user_id=user.id, name="Primary Journey"),
            Journey(owner_user_id=user.id, name="Second Journey"),
        ]
        session.add_all(journeys)
        session.flush()
        user.active_journey_id = journeys[0].id

        for raw_token in raw_tokens:
            session.add(
                AuthSession(
                    user_id=user.id,
                    session_token_hash=hash_token(raw_token),
                    expires_at=utcnow() + timedelta(days=7),
                    last_seen_at=utcnow(),
                )
            )

        if include_google_account:
            session.add(
                GoogleAccount(
                    user_id=user.id,
                    google_sub="google-sub-1",
                    email=email,
                    refresh_token_encrypted="refresh-token",
                    access_token_encrypted="access-token",
                    scope=(
                        "openid https://www.googleapis.com/auth/userinfo.email "
                        "https://www.googleapis.com/auth/gmail.readonly"
                    ),
                )
            )

        session.flush()
        user_id = user.id
        journey_snapshots = [(journey.id, journey.name) for journey in journeys]

    client.cookies.set(config.auth_cookie_name, raw_tokens[0])

    user_out = User(
        id=user_id,
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
        active_journey_id=journey_snapshots[0][0],
        is_active=True,
    )
    journeys_out = [
        Journey(id=journey_id, owner_user_id=user_id, name=name)
        for journey_id, name in journey_snapshots
    ]
    return user_out, journeys_out


def test_account_profile_endpoints_return_account_details_and_support_updates(tmp_path: Path) -> None:
    with _client(tmp_path) as (client, config):
        user, journeys = _login_user(
            client,
            config,
            session_count=2,
            include_google_account=True,
        )

        response = client.get("/api/auth/account")

        assert response.status_code == 200
        payload = response.json()
        assert payload["email"] == user.email
        assert payload["avatar_url"] == user.avatar_url
        assert payload["active_journey_id"] == journeys[0].id
        assert payload["active_journey_name"] == journeys[0].name
        assert payload["google_account_connected"] is True
        assert payload["gmail_scope_granted"] is True
        assert payload["active_session_count"] == 2
        assert payload["journey_count"] == 2

        update_response = client.patch("/api/auth/profile", json={"display_name": "New Candidate"})

        assert update_response.status_code == 200
        assert update_response.json()["display_name"] == "New Candidate"

        me_response = client.get("/api/auth/me")
        assert me_response.status_code == 200
        assert me_response.json()["display_name"] == "New Candidate"
        assert me_response.json()["avatar_url"] == user.avatar_url


def test_delete_active_journey_creates_replacement_when_last_journey(tmp_path: Path) -> None:
    with _client(tmp_path) as (client, config):
        user, journeys = _login_user(client, config, include_google_account=False)

        with get_db_session() as session:
            second_journey = session.query(Journey).filter(Journey.id == journeys[1].id).first()
            assert second_journey is not None
            session.delete(second_journey)

            session.add(
                Application(
                    owner_user_id=user.id,
                    journey_id=journeys[0].id,
                    company="OpenAI",
                    normalized_company="openai",
                    job_title="Engineer",
                    req_id="REQ-1",
                    status="已申请",
                    source="manual",
                )
            )

        response = client.delete(f"/api/journeys/{journeys[0].id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted_journey_id"] == journeys[0].id
        assert payload["replacement_created"] is True
        assert payload["active_journey_id"] != journeys[0].id

        with get_db_session() as session:
            remaining_journeys = session.query(Journey).filter(Journey.owner_user_id == user.id).all()
            assert len(remaining_journeys) == 1
            assert remaining_journeys[0].id == payload["active_journey_id"]
            assert session.query(Application).filter(Application.owner_user_id == user.id).count() == 0
            refreshed_user = session.query(User).filter(User.id == user.id).first()
            assert refreshed_user is not None
            assert refreshed_user.active_journey_id == payload["active_journey_id"]


def test_delete_account_cascades_data_and_clears_session_cookie(tmp_path: Path) -> None:
    with _client(tmp_path) as (client, config):
        user, journeys = _login_user(client, config, include_google_account=True)

        with get_db_session() as session:
            session.add(
                Application(
                    owner_user_id=user.id,
                    journey_id=journeys[0].id,
                    company="Anthropic",
                    normalized_company="anthropic",
                    job_title="Research Engineer",
                    req_id="REQ-2",
                    status="面试",
                    source="email",
                )
            )

        response = client.delete("/api/auth/account")

        assert response.status_code == 200
        assert response.json() == {"status": "deleted"}
        cookies = response.headers.get_list("set-cookie")
        assert any(cookie.startswith(f"{config.auth_cookie_name}=") for cookie in cookies)
        assert any(cookie.startswith("job_monitor_session_hint=") for cookie in cookies)

        with get_db_session() as session:
            assert session.query(User).count() == 0
            assert session.query(Journey).count() == 0
            assert session.query(Application).count() == 0
            assert session.query(GoogleAccount).count() == 0
            assert session.query(AuthSession).count() == 0
