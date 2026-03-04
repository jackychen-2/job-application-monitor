"""Email download and local caching logic."""

from __future__ import annotations

import email as email_lib
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig
from job_monitor.email.gmail_client import GmailClient
from job_monitor.email.parser import parse_email_message
from job_monitor.eval.models import CachedEmail

logger = structlog.get_logger(__name__)


def download_and_cache_emails(
    config: AppConfig,
    session: Session,
    owner_user_id: int,
    mailbox_email: str,
    oauth_access_token: str | None = None,
    *,
    since_date: Optional[str] = None,
    before_date: Optional[str] = None,
    max_count: int = 500,
) -> dict:
    """Fetch emails from IMAP and cache them locally.

    Returns summary dict with counts.
    """
    new_count = 0
    skipped_count = 0
    error_count = 0

    with GmailClient(config, oauth_access_token=oauth_access_token or "") as gmail:
        if since_date or before_date:
            message_ids, _ = gmail.fetch_message_ids_by_date_range(since_date, before_date)
        else:
            message_ids, _ = gmail.fetch_latest_message_ids(max_count)

        if len(message_ids) > max_count:
            message_ids = message_ids[:max_count]

        total = len(message_ids)
        logger.info("cache_download_start", total=total)

        for idx, gmail_message_id in enumerate(message_ids, 1):
            try:
                uid, msg, gmail_thread_id, _, _ = gmail.fetch_message(gmail_message_id)
                if msg is None:
                    error_count += 1
                    continue

                parsed = parse_email_message(msg, gmail_thread_id=gmail_thread_id)

                # Check if already cached by Gmail message ID
                existing = (
                    session.query(CachedEmail.id)
                    .filter(
                        CachedEmail.owner_user_id == owner_user_id,
                        CachedEmail.gmail_message_id == gmail_message_id,
                    )
                    .first()
                )
                if existing:
                    skipped_count += 1
                    continue

                # Also check by UID + account + folder
                existing_uid = (
                    session.query(CachedEmail.id)
                    .filter(
                        CachedEmail.uid == uid,
                        CachedEmail.owner_user_id == owner_user_id,
                        CachedEmail.email_account == mailbox_email,
                        CachedEmail.email_folder == config.email_folder,
                    )
                    .first()
                )
                if existing_uid:
                    skipped_count += 1
                    continue

                # Serialize raw bytes
                raw_bytes = msg.as_bytes()

                cached = CachedEmail(
                    owner_user_id=owner_user_id,
                    uid=uid,
                    email_account=mailbox_email,
                    email_folder=config.email_folder,
                    gmail_message_id=gmail_message_id,
                    gmail_thread_id=parsed.gmail_thread_id,
                    subject=parsed.subject,
                    sender=parsed.sender,
                    email_date=parsed.date_dt,
                    raw_rfc822=raw_bytes,
                    body_text=parsed.body_text,
                )
                session.add(cached)
                new_count += 1

                if new_count % 50 == 0:
                    session.flush()
                    logger.info("cache_download_progress", new=new_count, total=total, index=idx)

            except Exception as exc:
                logger.warning("cache_download_error", gmail_message_id=gmail_message_id, error=str(exc))
                error_count += 1

    session.commit()
    logger.info(
        "cache_download_complete",
        new=new_count,
        skipped=skipped_count,
        errors=error_count,
    )
    return {
        "new_emails": new_count,
        "skipped_duplicates": skipped_count,
        "errors": error_count,
        "total_fetched": total,
    }


def reparse_cached_email(cached: CachedEmail):
    """Re-parse a cached email's raw RFC822 bytes into a ParsedEmailData."""
    if not cached.raw_rfc822:
        return None
    msg = email_lib.message_from_bytes(cached.raw_rfc822)
    return parse_email_message(msg, gmail_thread_id=cached.gmail_thread_id)
