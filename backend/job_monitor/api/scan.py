"""Scan trigger and status endpoints."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import AsyncGenerator, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from job_monitor.auth.deps import get_current_user
from job_monitor.auth.oauth_google import get_valid_google_access_token
from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db, get_session_factory
from job_monitor.extraction.pipeline import (
    ProgressInfo,
    ScanSummary,
    run_date_range_scan,
    run_incremental_scan,
    run_scan,
)
from job_monitor.models import ScanState, User
from job_monitor.schemas import ScanResultOut, ScanStateOut

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/scan", tags=["scan"])

# In-memory state keyed by user id
_user_scan_locks: dict[int, threading.Lock] = {}
_user_scan_running: dict[int, bool] = {}
_user_scan_cancel_requested: dict[int, bool] = {}
_user_scan_last_result: dict[int, ScanResultOut] = {}
_user_scan_progress: dict[int, dict] = {}

# SSE-specific in-memory state keyed by user id
_user_sse_scan_locks: dict[int, threading.Lock] = {}
_user_sse_scan_running: dict[int, bool] = {}
_user_sse_cancel_requested: dict[int, bool] = {}

_state_lock = threading.Lock()


def _get_scan_lock(user_id: int) -> threading.Lock:
    with _state_lock:
        return _user_scan_locks.setdefault(user_id, threading.Lock())


def _get_sse_scan_lock(user_id: int) -> threading.Lock:
    with _state_lock:
        return _user_sse_scan_locks.setdefault(user_id, threading.Lock())


def _set_user_progress(user_id: int, info: ProgressInfo) -> None:
    _user_scan_progress[user_id] = {"type": "progress", **info}


def _to_result(summary: ScanSummary) -> ScanResultOut:
    return ScanResultOut(
        emails_scanned=summary.emails_scanned,
        emails_matched=summary.emails_matched,
        applications_created=summary.applications_created,
        applications_updated=summary.applications_updated,
        applications_deleted=summary.applications_deleted,
        total_prompt_tokens=summary.total_prompt_tokens,
        total_completion_tokens=summary.total_completion_tokens,
        total_estimated_cost=summary.total_estimated_cost,
        errors=summary.errors,
        cancelled=summary.cancelled,
    )


def _run_scan_background(
    config: AppConfig,
    user_id: int,
    mailbox_email: str,
    oauth_access_token: str,
    max_emails: int,
    incremental: bool = False,
) -> None:
    """Run scan in background thread for one user."""
    lock = _get_scan_lock(user_id)

    def should_cancel() -> bool:
        return _user_scan_cancel_requested.get(user_id, False)

    def progress_callback(info: ProgressInfo) -> None:
        _set_user_progress(user_id, info)

    try:
        _user_scan_running[user_id] = True
        _user_scan_cancel_requested[user_id] = False

        session_factory = get_session_factory()
        session = session_factory()
        session.info["owner_user_id"] = user_id

        try:
            cfg = config.model_copy(update={"max_scan_emails": max_emails})
            if incremental:
                logger.info("incremental_scan_triggered_via_api", user_id=user_id)
                summary = run_incremental_scan(
                    cfg,
                    session,
                    owner_user_id=user_id,
                    mailbox_email=mailbox_email,
                    oauth_access_token=oauth_access_token,
                    should_cancel=should_cancel,
                    progress_callback=progress_callback,
                )
            else:
                logger.info("scan_triggered_via_api", user_id=user_id, max_emails=max_emails)
                summary = run_scan(
                    cfg,
                    session,
                    owner_user_id=user_id,
                    mailbox_email=mailbox_email,
                    oauth_access_token=oauth_access_token,
                    should_cancel=should_cancel,
                    progress_callback=progress_callback,
                )
            _user_scan_last_result[user_id] = _to_result(summary)
        finally:
            session.close()
    except Exception as exc:
        logger.error("background_scan_error", user_id=user_id, error=str(exc))
        _user_scan_last_result[user_id] = ScanResultOut(
            emails_scanned=0,
            emails_matched=0,
            applications_created=0,
            applications_updated=0,
            applications_deleted=0,
            total_prompt_tokens=0,
            total_completion_tokens=0,
            total_estimated_cost=0.0,
            errors=[str(exc)],
            cancelled=False,
        )
    finally:
        _user_scan_running[user_id] = False
        _user_scan_progress.pop(user_id, None)
        if lock.locked():
            lock.release()


@router.post("", response_model=dict)
def trigger_scan(
    max_emails: int = 100,
    incremental: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict:
    """Trigger a background scan for the current user mailbox."""
    try:
        oauth_access_token, mailbox_email = get_valid_google_access_token(db, current_user.id, config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google mailbox not connected: {exc}") from exc

    lock = _get_scan_lock(current_user.id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A scan is already in progress for this user")

    scan_thread = threading.Thread(
        target=_run_scan_background,
        args=(config, current_user.id, mailbox_email, oauth_access_token, max_emails, incremental),
        daemon=True,
    )
    scan_thread.start()

    mode = "incremental (new emails only)" if incremental else f"full (latest {max_emails})"
    return {"message": f"Scan started ({mode})", "max_emails": max_emails, "incremental": incremental}


@router.get("/status", response_model=ScanStateOut | None)
def get_scan_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScanStateOut | None:
    """Return the last scan state for the current user."""
    state = (
        db.query(ScanState)
        .filter(ScanState.owner_user_id == current_user.id)
        .order_by(ScanState.last_scan_at.desc().nullslast())
        .first()
    )
    if not state:
        return None
    return ScanStateOut.model_validate(state)


@router.get("/last-result", response_model=ScanResultOut | None)
def get_last_scan_result(current_user: User = Depends(get_current_user)) -> ScanResultOut | None:
    """Return the latest in-memory scan result for this user."""
    return _user_scan_last_result.get(current_user.id)


@router.get("/running", response_model=dict)
def get_scan_running(current_user: User = Depends(get_current_user)) -> dict:
    """Check whether the current user has any scan running."""
    running = _user_scan_running.get(current_user.id, False) or _user_sse_scan_running.get(current_user.id, False)
    return {"running": running}


@router.get("/progress", response_model=dict)
def get_scan_progress(current_user: User = Depends(get_current_user)) -> dict:
    """Return latest scan progress for current user."""
    progress = _user_scan_progress.get(current_user.id)
    if progress is not None:
        return progress

    running = _user_scan_running.get(current_user.id, False) or _user_sse_scan_running.get(current_user.id, False)
    if running:
        return {"type": "progress", "processed": 0, "total": 0, "current_subject": "", "status": "processing"}
    return {"type": "idle", "processed": 0, "total": 0, "current_subject": "", "status": "idle"}


@router.post("/cancel", response_model=dict)
def cancel_scan(current_user: User = Depends(get_current_user)) -> dict:
    """Request cancellation of current user's non-SSE scan."""
    if not _user_scan_running.get(current_user.id, False):
        raise HTTPException(status_code=400, detail="No scan is currently running")

    _user_scan_cancel_requested[current_user.id] = True
    logger.info("scan_cancellation_requested", user_id=current_user.id)
    return {"message": "Scan cancellation requested"}


def _run_scan_with_queue(
    config: AppConfig,
    user_id: int,
    mailbox_email: str,
    oauth_access_token: str,
    max_emails: int,
    incremental: bool,
    progress_queue: queue.Queue,
    since_date: Optional[str] = None,
    before_date: Optional[str] = None,
) -> None:
    """Run scan and push progress updates to queue for one user."""
    lock = _get_sse_scan_lock(user_id)

    def should_cancel() -> bool:
        return _user_sse_cancel_requested.get(user_id, False)

    def progress_callback(info: ProgressInfo) -> None:
        event = {"type": "progress", **info}
        _user_scan_progress[user_id] = event
        try:
            progress_queue.put(event, block=False)
        except queue.Full:
            pass

    try:
        _user_sse_scan_running[user_id] = True
        _user_sse_cancel_requested[user_id] = False

        session_factory = get_session_factory()
        session = session_factory()
        session.info["owner_user_id"] = user_id

        try:
            cfg = config.model_copy(update={"max_scan_emails": max_emails})
            if since_date or before_date:
                logger.info("sse_date_range_scan_starting", user_id=user_id, since=since_date, before=before_date)
                summary = run_date_range_scan(
                    cfg,
                    session,
                    owner_user_id=user_id,
                    mailbox_email=mailbox_email,
                    oauth_access_token=oauth_access_token,
                    since_date=since_date,
                    before_date=before_date,
                    should_cancel=should_cancel,
                    progress_callback=progress_callback,
                )
            elif incremental:
                logger.info("sse_incremental_scan_starting", user_id=user_id)
                summary = run_incremental_scan(
                    cfg,
                    session,
                    owner_user_id=user_id,
                    mailbox_email=mailbox_email,
                    oauth_access_token=oauth_access_token,
                    should_cancel=should_cancel,
                    progress_callback=progress_callback,
                )
            else:
                logger.info("sse_scan_starting", user_id=user_id, max_emails=max_emails)
                summary = run_scan(
                    cfg,
                    session,
                    owner_user_id=user_id,
                    mailbox_email=mailbox_email,
                    oauth_access_token=oauth_access_token,
                    should_cancel=should_cancel,
                    progress_callback=progress_callback,
                )

            _user_scan_last_result[user_id] = _to_result(summary)
            progress_queue.put({"type": "complete", "result": _user_scan_last_result[user_id].model_dump()})
        finally:
            session.close()
    except Exception as exc:
        logger.error("sse_scan_error", user_id=user_id, error=str(exc))
        progress_queue.put({"type": "error", "message": str(exc)})
    finally:
        _user_sse_scan_running[user_id] = False
        _user_scan_progress.pop(user_id, None)
        progress_queue.put(None)
        if lock.locked():
            lock.release()


async def _event_generator(
    progress_queue: queue.Queue,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Generate SSE events from the progress queue."""
    while True:
        if await request.is_disconnected():
            logger.info("sse_client_disconnected_scan_continues")
            break

        try:
            try:
                event = progress_queue.get_nowait()
            except queue.Empty:
                yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
                continue

            if event is None:
                break

            yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.error("sse_generator_error", error=str(exc))
            break


@router.get("/stream")
async def stream_scan(
    request: Request,
    max_emails: int = Query(100, ge=1, le=10000),
    incremental: bool = Query(False),
    since_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    before_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> StreamingResponse:
    """Stream scan progress via Server-Sent Events for current user."""
    try:
        oauth_access_token, mailbox_email = get_valid_google_access_token(db, current_user.id, config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google mailbox not connected: {exc}") from exc

    lock = _get_sse_scan_lock(current_user.id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An SSE scan is already in progress for this user")

    progress_queue: queue.Queue = queue.Queue(maxsize=100)

    scan_thread = threading.Thread(
        target=_run_scan_with_queue,
        args=(
            config,
            current_user.id,
            mailbox_email,
            oauth_access_token,
            max_emails,
            incremental,
            progress_queue,
            since_date,
            before_date,
        ),
        daemon=True,
    )
    scan_thread.start()

    logger.info(
        "sse_scan_stream_started",
        user_id=current_user.id,
        max_emails=max_emails,
        incremental=incremental,
        since_date=since_date,
        before_date=before_date,
    )

    return StreamingResponse(
        _event_generator(progress_queue, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stream/cancel", response_model=dict)
def cancel_sse_scan(current_user: User = Depends(get_current_user)) -> dict:
    """Request cancellation of current user's SSE scan."""
    _user_sse_cancel_requested[current_user.id] = True
    logger.info("sse_scan_cancellation_requested", user_id=current_user.id)
    return {"message": "SSE scan cancellation requested"}
