"""Scan trigger and status endpoints."""

from __future__ import annotations

import threading
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db, get_session_factory
from job_monitor.extraction.pipeline import ScanSummary, run_scan
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


def _should_cancel() -> bool:
    """Check if cancellation has been requested."""
    return _cancel_requested


def _run_scan_background(config: AppConfig, max_emails: int) -> None:
    """Run scan in background thread."""
    global _last_result, _cancel_requested, _scan_running

    try:
        _scan_running = True
        _cancel_requested = False
        
        session_factory = get_session_factory()
        session = session_factory()
        
        try:
            config = config.model_copy(update={"max_scan_emails": max_emails})
            logger.info("scan_triggered_via_api", max_emails=max_emails)
            summary: ScanSummary = run_scan(config, session, should_cancel=_should_cancel)

            result = ScanResultOut(
                emails_scanned=summary.emails_scanned,
                emails_matched=summary.emails_matched,
                applications_created=summary.applications_created,
                applications_updated=summary.applications_updated,
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
    config: AppConfig = Depends(get_config),
) -> dict:
    """Trigger an email scan in the background. Always scans the latest N emails.

    Args:
        max_emails: Number of latest emails to scan (default: 100).
    
    Returns:
        A dict with message indicating scan started.
    """
    global _scan_thread

    if not _scan_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A scan is already in progress")

    # Start background thread
    _scan_thread = threading.Thread(
        target=_run_scan_background,
        args=(config, max_emails),
        daemon=True,
    )
    _scan_thread.start()
    
    return {"message": "Scan started", "max_emails": max_emails}


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
    """Check if a scan is currently running."""
    return {"running": _scan_running}


@router.post("/cancel", response_model=dict)
def cancel_scan() -> dict:
    """Request cancellation of the currently running scan."""
    global _cancel_requested
    
    if not _scan_running:
        raise HTTPException(status_code=400, detail="No scan is currently running")
    
    _cancel_requested = True
    logger.info("scan_cancellation_requested")
    
    return {"message": "Scan cancellation requested"}
