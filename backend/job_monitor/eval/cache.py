"""Email download and local caching logic."""

from __future__ import annotations

import email as email_lib
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig
from job_monitor.email.client import IMAPClient
from job_monitor.email.parser import parse_email_message
from job_monitor.eval.models import CachedEmail

logger = structlog.get_logger(__name__)


def download_and_cache_emails(
    config: AppConfig,
    session: Session,
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

    with IMAPClient(config) as imap:
        if since_date or before_date:
            uids = imap.fetch_uids_by_date_range(since_date, before_date)
        else:
            uids = imap.fetch_latest_uids(max_count)

        if len(uids) > max_count:
            uids = uids[:max_count]

        total = len(uids)
        logger.info("cache_download_start", total=total)

        for idx, uid in enumerate(uids, 1):
            try:
                _, msg, gmail_thread_id = imap.fetch_message(uid)
                if msg is None:
                    error_count += 1
                    continue

                parsed = parse_email_message(msg, gmail_thread_id=gmail_thread_id)

                # Check if already cached by message_id
                if parsed.message_id:
                    existing = (
                        session.query(CachedEmail.id)
                        .filter(CachedEmail.gmail_message_id == parsed.message_id)
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
                        CachedEmail.email_account == config.email_username,
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
                    uid=uid,
                    email_account=config.email_username,
                    email_folder=config.email_folder,
                    gmail_message_id=parsed.message_id,
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
                logger.warning("cache_download_error", uid=uid, error=str(exc))
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
