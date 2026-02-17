"""IMAP email client with retry logic and proper resource management."""

from __future__ import annotations

import email as email_lib
import imaplib
import socket
from email.message import Message
from typing import List, Tuple

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from job_monitor.config import AppConfig

logger = structlog.get_logger(__name__)

# Transient errors worth retrying
_RETRYABLE = (
    imaplib.IMAP4.error,
    socket.timeout,
    ConnectionResetError,
    ConnectionRefusedError,
    OSError,
)


class IMAPClient:
    """IMAP connection wrapper with retry, timeout, and context-manager support.

    Usage::

        with IMAPClient(config) as client:
            uids = client.fetch_uids_after(last_uid=5000)
            for uid in uids:
                msg = client.fetch_message(uid)
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._mail: imaplib.IMAP4_SSL | None = None

    # ── Context manager ───────────────────────────────────
    def __enter__(self) -> "IMAPClient":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    # ── Connection ────────────────────────────────────────
    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    def connect(self) -> None:
        """Establish IMAP connection and select the configured folder."""
        cfg = self._config
        socket.setdefaulttimeout(cfg.imap_timeout_sec)

        logger.info("imap_connecting", host=cfg.imap_host, port=cfg.imap_port)
        self._mail = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)

        logger.info("imap_logging_in", username=cfg.email_username)
        self._mail.login(cfg.email_username, cfg.email_password.get_secret_value())

        status, _ = self._mail.select(cfg.email_folder)
        if status != "OK":
            raise RuntimeError(f"Cannot select folder: {cfg.email_folder}")
        logger.info("imap_folder_selected", folder=cfg.email_folder)

    def disconnect(self) -> None:
        """Safely close the IMAP connection."""
        if self._mail is not None:
            try:
                self._mail.logout()
                logger.debug("imap_disconnected")
            except Exception:
                pass
            finally:
                self._mail = None

    # ── Fetching ──────────────────────────────────────────
    def _ensure_connected(self) -> imaplib.IMAP4_SSL:
        if self._mail is None:
            raise RuntimeError("IMAP client not connected — call connect() first")
        return self._mail

    def fetch_uids_after(self, last_uid: int) -> List[int]:
        """Return UIDs newer than *last_uid*, capped by max_scan_emails."""
        mail = self._ensure_connected()
        cfg = self._config

        search_criteria = f"UID {last_uid + 1}:*"
        status, data = mail.uid("SEARCH", None, search_criteria)
        if status != "OK":
            raise RuntimeError("IMAP UID SEARCH failed")

        uid_tokens = (data[0] or b"").split()
        uids = sorted(int(t) for t in uid_tokens if int(t) > last_uid)

        if len(uids) > cfg.max_scan_emails:
            total_found = len(uids)
            uids = uids[:cfg.max_scan_emails]  # Take OLDEST first so none are skipped
            logger.info(
                "imap_uid_capped",
                total=total_found,
                kept=cfg.max_scan_emails,
                remaining=total_found - cfg.max_scan_emails,
            )

        logger.info("imap_uids_found", count=len(uids))
        return uids

    def fetch_latest_uids(self, count: int) -> List[int]:
        """Return the latest *count* email UIDs from the mailbox."""
        mail = self._ensure_connected()

        status, data = mail.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise RuntimeError("IMAP UID SEARCH failed")

        uid_tokens = (data[0] or b"").split()
        all_uids = sorted(int(t) for t in uid_tokens)

        # Take the most recent N
        uids = all_uids[-count:] if len(all_uids) > count else all_uids

        logger.info("imap_latest_uids", total_in_mailbox=len(all_uids), selected=len(uids))
        return uids

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    def fetch_message(self, uid: int) -> Tuple[int, Message | None]:
        """Fetch a single email by UID and return (uid, parsed Message or None)."""
        mail = self._ensure_connected()

        status, fetched = mail.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK" or not fetched or fetched[0] is None:
            logger.warning("imap_fetch_failed", uid=uid)
            return uid, None

        raw = fetched[0][1]
        if not raw:
            logger.warning("imap_empty_payload", uid=uid)
            return uid, None

        msg = email_lib.message_from_bytes(raw)
        return uid, msg
