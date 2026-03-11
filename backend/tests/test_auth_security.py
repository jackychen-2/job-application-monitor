from __future__ import annotations

from fastapi import Response

from job_monitor.auth.security import clear_session_cookie, set_session_cookie
from job_monitor.config import AppConfig


def _config() -> AppConfig:
    return AppConfig(
        auth_cookie_name="job_monitor_session",
        auth_cookie_secure=False,
        auth_session_ttl_days=30,
    )


def test_set_session_cookie_sets_auth_and_hint_cookie() -> None:
    response = Response()

    set_session_cookie(response, "token-value", _config())

    cookies = response.headers.getlist("set-cookie")
    assert any(cookie.startswith("job_monitor_session=token-value;") for cookie in cookies)
    assert any(cookie.startswith("job_monitor_session_hint=1;") for cookie in cookies)


def test_clear_session_cookie_clears_auth_and_hint_cookie() -> None:
    response = Response()

    clear_session_cookie(response, _config())

    cookies = response.headers.getlist("set-cookie")
    assert any(cookie.startswith("job_monitor_session=") for cookie in cookies)
    assert any(cookie.startswith("job_monitor_session_hint=") for cookie in cookies)
