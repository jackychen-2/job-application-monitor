"""Gmail REST API client (read-only) used for scanning and cache download."""

from __future__ import annotations

import base64
import email as email_lib
import hashlib
from datetime import datetime, timedelta
from email.message import Message
from typing import Any, Optional

import httpx
import structlog

from job_monitor.config import AppConfig

logger = structlog.get_logger(__name__)


class GmailHistoryExpiredError(RuntimeError):
    """Raised when startHistoryId is too old and Gmail no longer has the history window."""


def _stable_uid_from_gmail_id(gmail_message_id: str) -> int:
    """Map Gmail message ID to stable signed 63-bit int for legacy uid column compatibility."""
    digest = hashlib.sha256(gmail_message_id.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF
    return value if value > 0 else 1


def _to_gmail_date(date_yyyy_mm_dd: str) -> str:
    dt = datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d")
    return dt.strftime("%Y/%m/%d")


def _to_gmail_before_date_inclusive(before_yyyy_mm_dd: str) -> str:
    dt = datetime.strptime(before_yyyy_mm_dd, "%Y-%m-%d") + timedelta(days=1)
    return dt.strftime("%Y/%m/%d")


class GmailClient:
    """Minimal Gmail API client for read-only message listing and retrieval."""

    BASE_URL = "https://gmail.googleapis.com/gmail/v1"

    def __init__(self, config: AppConfig, *, oauth_access_token: str) -> None:
        self._config = config
        self._oauth_access_token = oauth_access_token
        self._client: Optional[httpx.Client] = None

    def __enter__(self) -> "GmailClient":
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=max(20, self._config.imap_timeout_sec),
            headers={"Authorization": f"Bearer {self._oauth_access_token}"},
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            raise RuntimeError("Gmail client not initialized — use as context manager")
        return self._client

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        client = self._ensure_client()
        res = client.get(path, params=params)
        res.raise_for_status()
        return res.json()

    def get_latest_history_id(self) -> int:
        data = self._get("/users/me/profile")
        hid = data.get("historyId")
        return int(hid) if hid is not None else 0

    def _list_message_ids(
        self,
        *,
        query: Optional[str],
        max_count: Optional[int],
    ) -> list[str]:
        ids: list[str] = []
        page_token: Optional[str] = None

        while True:
            page_size = 500
            if max_count is not None:
                remaining = max_count - len(ids)
                if remaining <= 0:
                    break
                page_size = min(page_size, max(1, remaining))

            params: dict[str, Any] = {"maxResults": page_size}
            if query:
                params["q"] = query
            if page_token:
                params["pageToken"] = page_token

            data = self._get("/users/me/messages", params=params)
            for msg in data.get("messages", []) or []:
                mid = msg.get("id")
                if mid:
                    ids.append(str(mid))

            if max_count is not None and len(ids) >= max_count:
                ids = ids[:max_count]
                break

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return ids

    def fetch_latest_message_ids(self, count: int) -> tuple[list[str], int]:
        ids = self._list_message_ids(query=None, max_count=count)
        # Gmail list returns newest-first; process oldest->newest for stable progression.
        ids.reverse()
        return ids, self.get_latest_history_id()

    def fetch_message_ids_by_date_range(
        self,
        since_date: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> tuple[list[str], int]:
        parts: list[str] = []
        if since_date:
            parts.append(f"after:{_to_gmail_date(since_date)}")
        if before_date:
            parts.append(f"before:{_to_gmail_before_date_inclusive(before_date)}")

        query = " ".join(parts) if parts else None
        ids = self._list_message_ids(query=query, max_count=None)
        ids.reverse()
        return ids, self.get_latest_history_id()

    def fetch_message_ids_after_history(
        self,
        start_history_id: int,
        *,
        max_count: Optional[int],
    ) -> tuple[list[str], int]:
        if start_history_id <= 0:
            return self.fetch_latest_message_ids(max_count or self._config.max_scan_emails)

        ids: list[str] = []
        seen: set[str] = set()
        page_token: Optional[str] = None
        latest_history_id = start_history_id

        while True:
            params: dict[str, Any] = {
                "startHistoryId": str(start_history_id),
                "historyTypes": "messageAdded",
                "maxResults": 500,
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                data = self._get("/users/me/history", params=params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise GmailHistoryExpiredError(
                        f"startHistoryId {start_history_id} expired"
                    ) from exc
                raise

            if data.get("historyId"):
                latest_history_id = int(data["historyId"])

            for history_row in data.get("history", []) or []:
                for added in history_row.get("messagesAdded", []) or []:
                    msg = added.get("message") or {}
                    mid = msg.get("id")
                    if not mid:
                        continue
                    mid = str(mid)
                    if mid in seen:
                        continue
                    seen.add(mid)
                    ids.append(mid)
                    if max_count is not None and len(ids) >= max_count:
                        return ids, latest_history_id

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return ids, latest_history_id

    def fetch_message(self, gmail_message_id: str) -> tuple[int, Message | None, str | None, str, int]:
        data = self._get(f"/users/me/messages/{gmail_message_id}", params={"format": "raw"})

        raw = data.get("raw")
        if not raw:
            logger.warning("gmail_message_missing_raw", message_id=gmail_message_id)
            return _stable_uid_from_gmail_id(gmail_message_id), None, None, gmail_message_id, int(data.get("historyId") or 0)

        padded = raw + "=" * (-len(raw) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("utf-8"))
        msg = email_lib.message_from_bytes(payload)

        thread_id = data.get("threadId")
        history_id = int(data.get("historyId") or 0)
        uid = _stable_uid_from_gmail_id(gmail_message_id)
        return uid, msg, thread_id, gmail_message_id, history_id
