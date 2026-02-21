"""Scan trigger and status endpoints."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any, AsyncGenerator, Generator, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db, get_session_factory
from job_monitor.extraction.pipeline import (
    ProgressInfo,
    ScanSummary,
    run_scan,
    run_incremental_scan,
    run_date_range_scan,
)
from job_monitor.models import ScanState
from job_monitor.schemas import ScanResultOut, ScanStateOut

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/scan", tags=["scan"])

# Simple in-memory lock to prevent concurrent scans
_scan_lock = threading.Lock()
_last_result: ScanResultOut | None = None
_cancel_requested = False
_scan_thread: Optional[threading.Thread] = None
_scan_running = False
_current_progress: Optional[dict] = None  # Latest progress for polling


def _should_cancel() -> bool:
    """Check if cancellation has been requested."""
    return _cancel_requested


def _run_scan_background(config: AppConfig, max_emails: int, incremental: bool = False) -> None:
    """Run scan in background thread."""
    global _last_result, _cancel_requested, _scan_running

    try:
        _scan_running = True
        _cancel_requested = False
        
        session_factory = get_session_factory()
        session = session_factory()
        
        try:
            config = config.model_copy(update={"max_scan_emails": max_emails})
            if incremental:
                logger.info("incremental_scan_triggered_via_api")
                summary: ScanSummary = run_incremental_scan(config, session, should_cancel=_should_cancel)
            else:
                logger.info("scan_triggered_via_api", max_emails=max_emails)
                summary: ScanSummary = run_scan(config, session, should_cancel=_should_cancel)

            result = ScanResultOut(
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
            _last_result = result
        finally:
            session.close()
    except Exception as exc:
        logger.error("background_scan_error", error=str(exc))
        _last_result = ScanResultOut(
            emails_scanned=0,
            emails_matched=0,
            applications_created=0,
            applications_updated=0,
            total_prompt_tokens=0,
            total_completion_tokens=0,
            total_estimated_cost=0.0,
            errors=[str(exc)],
            cancelled=False,
        )
    finally:
        _scan_running = False
        _scan_lock.release()


@router.post("", response_model=dict)
def trigger_scan(
    max_emails: int = 100,
    incremental: bool = False,
    config: AppConfig = Depends(get_config),
) -> dict:
    """Trigger an email scan in the background.

    Args:
        max_emails: Number of latest emails to scan (default: 100).
        incremental: If True, only scan emails after the last scanned UID.
    
    Returns:
        A dict with message indicating scan started.
    """
    global _scan_thread

    if not _scan_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A scan is already in progress")

    # Start background thread
    _scan_thread = threading.Thread(
        target=_run_scan_background,
        args=(config, max_emails, incremental),
        daemon=True,
    )
    _scan_thread.start()
    
    mode = "incremental (new emails only)" if incremental else f"full (latest {max_emails})"
    return {"message": f"Scan started ({mode})", "max_emails": max_emails, "incremental": incremental}


@router.get("/status", response_model=ScanStateOut | None)
def get_scan_status(
    config: AppConfig = Depends(get_config),
    db: Session = Depends(get_db),
) -> ScanStateOut | None:
    """Return the last scan state for the configured account."""
    state = (
        db.query(ScanState)
        .filter(
            ScanState.email_account == config.email_username,
            ScanState.email_folder == config.email_folder,
        )
        .first()
    )
    if not state:
        return None
    return ScanStateOut.model_validate(state)


@router.get("/last-result", response_model=ScanResultOut | None)
def get_last_scan_result() -> ScanResultOut | None:
    """Return the result of the most recent scan (in-memory, resets on server restart)."""
    return _last_result


@router.get("/running", response_model=dict)
def get_scan_running() -> dict:
    """Check if a scan is currently running (either background or SSE)."""
    return {"running": _scan_running or _sse_scan_running}


@router.get("/progress", response_model=dict)
def get_scan_progress() -> dict:
    """Return the latest scan progress (for polling after page refresh).
    
    Returns the last progress update from an active SSE scan,
    or empty progress if no scan is running.
    """
    if _current_progress is not None:
        return _current_progress
    if _scan_running or _sse_scan_running:
        return {"type": "progress", "processed": 0, "total": 0, "current_subject": "", "status": "processing"}
    return {"type": "idle", "processed": 0, "total": 0, "current_subject": "", "status": "idle"}


@router.post("/cancel", response_model=dict)
def cancel_scan() -> dict:
    """Request cancellation of the currently running scan."""
    global _cancel_requested
    
    if not _scan_running:
        raise HTTPException(status_code=400, detail="No scan is currently running")
    
    _cancel_requested = True
    logger.info("scan_cancellation_requested")
    
    return {"message": "Scan cancellation requested"}


# SSE-specific state for streaming scan
_sse_cancel_requested = False
_sse_scan_lock = threading.Lock()
_sse_scan_running = False


def _sse_should_cancel() -> bool:
    """Check if SSE scan cancellation has been requested."""
    return _sse_cancel_requested


def _run_scan_with_queue(
    config: AppConfig,
    max_emails: int,
    incremental: bool,
    progress_queue: queue.Queue,
    since_date: Optional[str] = None,
    before_date: Optional[str] = None,
) -> None:
    """Run scan and push progress updates to queue."""
    global _sse_cancel_requested, _sse_scan_running, _current_progress, _last_result
    
    def progress_callback(info: ProgressInfo) -> None:
        """Push progress info to the queue and store for polling."""
        global _current_progress
        event = {"type": "progress", **info}
        _current_progress = event  # Store latest progress for polling
        try:
            progress_queue.put(event, block=False)
        except queue.Full:
            pass  # Drop update if queue is full
    
    try:
        _sse_scan_running = True
        _sse_cancel_requested = False
        session_factory = get_session_factory()
        session = session_factory()
        
        try:
            config = config.model_copy(update={"max_scan_emails": max_emails})
            if since_date or before_date:
                logger.info("sse_date_range_scan_starting", since=since_date, before=before_date)
                summary: ScanSummary = run_date_range_scan(
                    config, session,
                    since_date=since_date,
                    before_date=before_date,
                    should_cancel=_sse_should_cancel,
                    progress_callback=progress_callback,
                )
            elif incremental:
                logger.info("sse_incremental_scan_starting")
                summary: ScanSummary = run_incremental_scan(
                    config, session,
                    should_cancel=_sse_should_cancel,
                    progress_callback=progress_callback,
                )
            else:
                logger.info("sse_scan_starting", max_emails=max_emails)
                summary: ScanSummary = run_scan(
                    config, session,
                    should_cancel=_sse_should_cancel,
                    progress_callback=progress_callback,
                )
            
            # Send complete event with full result
            scan_result = ScanResultOut(
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
            _last_result = scan_result
            result = {
                "type": "complete",
                "result": {
                    "emails_scanned": summary.emails_scanned,
                    "emails_matched": summary.emails_matched,
                    "applications_created": summary.applications_created,
                    "applications_updated": summary.applications_updated,
                    "applications_deleted": summary.applications_deleted,
                    "total_prompt_tokens": summary.total_prompt_tokens,
                    "total_completion_tokens": summary.total_completion_tokens,
                    "total_estimated_cost": summary.total_estimated_cost,
                    "errors": summary.errors,
                    "cancelled": summary.cancelled,
                },
            }
            progress_queue.put(result)
        finally:
            session.close()
    except Exception as exc:
        logger.error("sse_scan_error", error=str(exc))
        progress_queue.put({
            "type": "error",
            "message": str(exc),
        })
    finally:
        # Signal end of stream and release lock
        _sse_scan_running = False
        _current_progress = None  # Clear progress when scan completes
        progress_queue.put(None)
        _sse_scan_lock.release()


async def _event_generator(
    progress_queue: queue.Queue,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Generate SSE events from the progress queue.
    
    Note: When the client disconnects, we stop sending events but do NOT cancel
    the scan. The scan continues running in the background. Only an explicit call
    to /api/scan/stream/cancel should cancel the scan.
    """
    while True:
        # Check if client disconnected - stop streaming but don't cancel scan
        if await request.is_disconnected():
            logger.info("sse_client_disconnected_scan_continues")
            break
        
        try:
            # Non-blocking check with asyncio sleep for cooperative multitasking
            try:
                event = progress_queue.get_nowait()
            except queue.Empty:
                # Send keepalive and yield control to event loop
                yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
                continue
            
            if event is None:
                # End of stream
                break
            
            # Format as SSE
            yield f"data: {json.dumps(event)}\n\n"
            
        except Exception as e:
            logger.error("sse_generator_error", error=str(e))
            break


@router.get("/stream")
async def stream_scan(
    request: Request,
    max_emails: int = Query(100, ge=1, le=10000),
    incremental: bool = Query(False),
    since_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    before_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    config: AppConfig = Depends(get_config),
) -> StreamingResponse:
    """Stream scan progress via Server-Sent Events.
    
    Starts a scan and streams progress events in real-time.
    
    Args:
        max_emails: Number of latest emails to scan (default: 100).
        incremental: If True, only scan emails after the last scanned UID.
    
    Returns:
        SSE stream with progress events:
        - data: {"type": "progress", "processed": 1, "total": 50, "current_subject": "...", "status": "processing"}
        - data: {"type": "complete", "result": {...scan_result...}}
        - data: {"type": "error", "error": "..."}
    """
    global _sse_cancel_requested
    
    # Acquire lock to prevent concurrent SSE scans
    if not _sse_scan_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An SSE scan is already in progress")
    
    progress_queue: queue.Queue = queue.Queue(maxsize=100)
    
    # Start scan in background thread (lock will be released in _run_scan_with_queue)
    scan_thread = threading.Thread(
        target=_run_scan_with_queue,
        args=(config, max_emails, incremental, progress_queue, since_date, before_date),
        daemon=True,
    )
    scan_thread.start()
    
    logger.info("sse_scan_stream_started", max_emails=max_emails, incremental=incremental, since_date=since_date, before_date=before_date)
    
    return StreamingResponse(
        _event_generator(progress_queue, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post("/stream/cancel", response_model=dict)
def cancel_sse_scan() -> dict:
    """Request cancellation of the currently running SSE scan."""
    global _sse_cancel_requested
    
    _sse_cancel_requested = True
    logger.info("sse_scan_cancellation_requested")
    
    return {"message": "SSE scan cancellation requested"}
