from __future__ import annotations

from job_monitor.config import AppConfig
from job_monitor.email.gmail_client import GmailClient, is_inbox_message


def _config() -> AppConfig:
    return AppConfig()


def test_inbox_message_filters_social_and_promotions_only() -> None:
    assert is_inbox_message(["INBOX"]) is True
    assert is_inbox_message(["INBOX", "UNREAD"]) is True
    assert is_inbox_message(["INBOX", "CATEGORY_PROMOTIONS"]) is False
    assert is_inbox_message(["INBOX", "CATEGORY_SOCIAL"]) is False
    assert is_inbox_message(["INBOX", "CATEGORY_UPDATES"]) is True
    assert is_inbox_message(["CATEGORY_SOCIAL"]) is False
    assert is_inbox_message([]) is False


def test_fetch_latest_message_ids_queries_inbox_without_social_or_promotions(monkeypatch) -> None:
    client = GmailClient(_config(), oauth_access_token="token")
    seen_queries: list[str | None] = []

    def _fake_get(path: str, params=None):
        if path == "/users/me/messages":
            seen_queries.append(params.get("q"))
            return {"messages": [{"id": "newer"}, {"id": "older"}]}
        if path == "/users/me/profile":
            return {"historyId": "123"}
        raise AssertionError(path)

    client._get = _fake_get  # type: ignore[method-assign]

    ids, history_id = client.fetch_latest_message_ids(2)

    assert seen_queries == ["in:inbox -category:social -category:promotions"]
    assert ids == ["older", "newer"]
    assert history_id == 123


def test_fetch_message_ids_by_date_range_keeps_social_promotion_filter(monkeypatch) -> None:
    client = GmailClient(_config(), oauth_access_token="token")
    seen_queries: list[str | None] = []

    def _fake_get(path: str, params=None):
        if path == "/users/me/messages":
            seen_queries.append(params.get("q"))
            return {"messages": [{"id": "one"}]}
        if path == "/users/me/profile":
            return {"historyId": "321"}
        raise AssertionError(path)

    client._get = _fake_get  # type: ignore[method-assign]

    ids, history_id = client.fetch_message_ids_by_date_range("2026-03-01", "2026-03-05")

    assert seen_queries == ["in:inbox -category:social -category:promotions after:2026/03/01 before:2026/03/06"]
    assert ids == ["one"]
    assert history_id == 321
