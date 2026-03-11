"""Scan job endpoints, SSE streaming, and background job continuation."""

# ruff: noqa: B008

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from job_monitor.auth.deps import get_current_user
from job_monitor.auth.oauth_google import get_valid_google_access_token
from job_monitor.config import AppConfig, get_config
from job_monitor.database import get_db, get_session_factory
from job_monitor.models import ScanJob, ScanState, User
from job_monitor.scan_jobs import (
    ACTIVE_SCAN_JOB_STATUSES,
    MAX_REQUESTED_MAX_EMAILS,
    SCAN_JOB_MODES,
    TERMINAL_SCAN_JOB_STATUSES,
    create_scan_job as create_scan_job_record,
    dispatch_scan_job_continuation,
    get_active_scan_job,
    get_latest_terminal_scan_job,
    get_scan_job,
    process_scan_job,
    request_scan_job_cancel,
    run_scan_job_step,
    scan_job_to_scan_result,
    serialize_scan_job,
)
from job_monitor.schemas import (
    CreateScanJobOut,
    ScanJobOut,
    ScanJobStepOut,
    ScanResultOut,
    ScanStateOut,
)

router = APIRouter(prefix="/api/scan", tags=["scan"])

SSE_POLL_INTERVAL_SECONDS = 1.0


def _require_active_journey(current_user: User) -> int:
    if current_user.active_journey_id is None:
        raise HTTPException(status_code=400, detail="No active journey")
    return int(current_user.active_journey_id)


def _parse_date_input(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be YYYY-MM-DD",
        ) from exc
    return normalized


def _resolve_scan_mode(
    *,
    mode: str | None,
    incremental: bool | None = None,
    scan_all: bool = False,
    since_date: str | None = None,
    before_date: str | None = None,
) -> str:
    normalized_mode = (mode or "").strip().lower() or None
    if normalized_mode is None:
        if since_date or before_date:
            normalized_mode = "date_range"
        elif scan_all or incremental is False:
            normalized_mode = "full"
        else:
            normalized_mode = "incremental"

    if normalized_mode not in SCAN_JOB_MODES:
        allowed = ", ".join(SCAN_JOB_MODES)
        raise HTTPException(status_code=400, detail=f"mode must be one of: {allowed}")

    if normalized_mode == "date_range":
        if not since_date and not before_date:
            raise HTTPException(
                status_code=400,
                detail="date_range scans require since_date and/or before_date",
            )
        if since_date and before_date and since_date > before_date:
            raise HTTPException(
                status_code=400,
                detail="since_date must be before or equal to before_date",
            )
    elif since_date or before_date:
        raise HTTPException(
            status_code=400,
            detail="since_date/before_date are only supported for date_range scans",
        )

    return normalized_mode


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _streaming_response(generator: object) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_job_events(
    request: Request,
    owner_user_id: int,
    journey_id: int,
    job_id: int,
):
    session_factory = get_session_factory()
    last_payload: str | None = None

    while True:
        if await request.is_disconnected():
            break

        session = session_factory()
        try:
            job = get_scan_job(session, owner_user_id, journey_id, job_id)
            if job is None:
                payload = json.dumps({"detail": "Scan job not found"}, separators=(",", ":"))
                yield f"event: error\ndata: {payload}\n\n"
                break
            payload = json.dumps(serialize_scan_job(job), default=_json_default, separators=(",", ":"))
            terminal = job.status in TERMINAL_SCAN_JOB_STATUSES
        finally:
            session.close()

        if payload != last_payload:
            yield f"event: job\ndata: {payload}\n\n"
            last_payload = payload
        else:
            yield ": keepalive\n\n"

        if terminal:
            yield f"event: done\ndata: {payload}\n\n"
            break

        await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)


def _maybe_dispatch_background(job_payload: dict, config: AppConfig) -> None:
    if job_payload["status"] in TERMINAL_SCAN_JOB_STATUSES:
        return
    dispatch_scan_job_continuation(config, int(job_payload["id"]))


def _create_job(
    current_user: User,
    db: Session,
    config: AppConfig,
    *,
    mode: str,
    max_emails: int | None,
    since_date: str | None,
    before_date: str | None,
) -> tuple[dict, bool]:
    journey_id = _require_active_journey(current_user)
    try:
        oauth_access_token, mailbox_email = get_valid_google_access_token(
            db,
            current_user.id,
            config,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google mailbox not connected: {exc}") from exc

    job, reused = create_scan_job_record(
        db,
        config,
        owner_user_id=current_user.id,
        journey_id=journey_id,
        mailbox_email=mailbox_email,
        oauth_access_token=oauth_access_token,
        mode=mode,
        requested_max_emails=max_emails,
        since_date=since_date,
        before_date=before_date,
    )
    payload = serialize_scan_job(job)
    _maybe_dispatch_background(payload, config)
    return payload, reused


def _active_job_or_404(db: Session, current_user: User, job_id: int) -> dict:
    journey_id = _require_active_journey(current_user)
    job = get_scan_job(db, current_user.id, journey_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")
    return serialize_scan_job(job)


def _require_worker_secret(
    authorization: str | None,
    config: AppConfig,
) -> None:
    expected = config.scan_job_continue_secret.get_secret_value().strip()
    if not expected:
        expected = config.token_encryption_key.get_secret_value().strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Background scan worker secret is not configured")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("", response_model=dict)
def trigger_scan(
    max_emails: int | None = Query(50, ge=0, le=MAX_REQUESTED_MAX_EMAILS),
    incremental: bool = Query(True),
    scan_all: bool = Query(False),
    mode: str | None = Query(None),
    since_date: str | None = Query(None),
    before_date: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict:
    normalized_since = _parse_date_input(since_date, "since_date")
    normalized_before = _parse_date_input(before_date, "before_date")
    resolved_mode = _resolve_scan_mode(
        mode=mode,
        incremental=incremental,
        scan_all=scan_all,
        since_date=normalized_since,
        before_date=normalized_before,
    )
    job, reused = _create_job(
        current_user,
        db,
        config,
        mode=resolved_mode,
        max_emails=max_emails,
        since_date=normalized_since,
        before_date=normalized_before,
    )
    return {
        "message": "Scan job ready" if reused else "Scan job created",
        "max_emails": max_emails or 0,
        "incremental": resolved_mode == "incremental",
        "mode": resolved_mode,
        "job_id": job["id"],
        "reused": reused,
    }


@router.post("/jobs", response_model=CreateScanJobOut)
def create_scan_job(
    max_emails: int | None = Query(None, ge=0, le=MAX_REQUESTED_MAX_EMAILS),
    mode: str | None = Query(None),
    scan_all: bool = Query(False),
    since_date: str | None = Query(None),
    before_date: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> CreateScanJobOut:
    normalized_since = _parse_date_input(since_date, "since_date")
    normalized_before = _parse_date_input(before_date, "before_date")
    resolved_mode = _resolve_scan_mode(
        mode=mode,
        scan_all=scan_all,
        since_date=normalized_since,
        before_date=normalized_before,
    )
    job, reused = _create_job(
        current_user,
        db,
        config,
        mode=resolved_mode,
        max_emails=max_emails,
        since_date=normalized_since,
        before_date=normalized_before,
    )
    return CreateScanJobOut(job=job, reused=reused)


@router.get("/jobs/active", response_model=ScanJobOut | None)
def get_active_scan_job_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScanJobOut | None:
    journey_id = _require_active_journey(current_user)
    job = get_active_scan_job(db, current_user.id, journey_id)
    return None if job is None else ScanJobOut.model_validate(serialize_scan_job(job))


@router.get("/jobs/{job_id}", response_model=ScanJobOut)
def get_scan_job_endpoint(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScanJobOut:
    return ScanJobOut.model_validate(_active_job_or_404(db, current_user, job_id))


@router.get("/jobs/{job_id}/stream")
async def stream_scan_job(
    job_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    journey_id = _require_active_journey(current_user)
    job = get_scan_job(db, current_user.id, journey_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")
    return _streaming_response(
        _stream_job_events(request, current_user.id, journey_id, job.id)
    )


@router.post("/jobs/{job_id}/step", response_model=ScanJobStepOut)
def step_scan_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> ScanJobStepOut:
    journey_id = _require_active_journey(current_user)
    job = get_scan_job(db, current_user.id, journey_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

    try:
        oauth_access_token, _ = get_valid_google_access_token(db, current_user.id, config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google mailbox not connected: {exc}") from exc

    try:
        job, processed_in_step, done = run_scan_job_step(
            db,
            config,
            owner_user_id=current_user.id,
            journey_id=journey_id,
            job_id=job_id,
            oauth_access_token=oauth_access_token,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = serialize_scan_job(job)
    if not done and processed_in_step > 0 and payload["status"] in ACTIVE_SCAN_JOB_STATUSES:
        dispatch_scan_job_continuation(config, job.id)

    return ScanJobStepOut(
        job=payload,
        processed_in_step=processed_in_step,
        done=done,
    )


@router.post("/jobs/{job_id}/process", response_model=ScanJobStepOut)
def continue_scan_job_in_background(
    job_id: int,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> ScanJobStepOut:
    _require_worker_secret(authorization, config)

    job = db.get(ScanJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

    try:
        oauth_access_token, _ = get_valid_google_access_token(db, job.owner_user_id, config)
    except Exception as exc:
        job.status = "failed"
        job.completed_at = datetime.now(timezone.utc)
        job.last_error = f"Google mailbox not connected: {exc}"
        db.commit()
        raise HTTPException(status_code=400, detail=f"Google mailbox not connected: {exc}") from exc

    try:
        updated_job, processed_in_step, done = process_scan_job(
            db,
            config,
            job_id=job_id,
            oauth_access_token=oauth_access_token,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ScanJobStepOut(
        job=serialize_scan_job(updated_job),
        processed_in_step=processed_in_step,
        done=done,
    )


@router.post("/jobs/{job_id}/cancel", response_model=ScanJobOut)
def cancel_scan_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScanJobOut:
    journey_id = _require_active_journey(current_user)
    job = request_scan_job_cancel(db, current_user.id, journey_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")
    return ScanJobOut.model_validate(serialize_scan_job(job))


@router.get("/status", response_model=ScanStateOut | None)
def get_scan_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScanStateOut | None:
    if current_user.active_journey_id is None:
        return None

    state = (
        db.query(ScanState)
        .filter(
            ScanState.owner_user_id == current_user.id,
            ScanState.journey_id == current_user.active_journey_id,
        )
        .order_by(ScanState.last_scan_at.desc().nullslast())
        .first()
    )
    if not state:
        return None
    return ScanStateOut.model_validate(state)


@router.get("/last-result", response_model=ScanResultOut | None)
def get_last_scan_result(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScanResultOut | None:
    journey_id = _require_active_journey(current_user)
    job = get_latest_terminal_scan_job(db, current_user.id, journey_id)
    if job is None:
        return None
    return ScanResultOut.model_validate(scan_job_to_scan_result(job))


@router.get("/running", response_model=dict)
def get_scan_running(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    journey_id = _require_active_journey(current_user)
    return {"running": get_active_scan_job(db, current_user.id, journey_id) is not None}


@router.get("/progress", response_model=dict)
def get_scan_progress(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    journey_id = _require_active_journey(current_user)
    job = get_active_scan_job(db, current_user.id, journey_id)
    if job is None:
        return {"type": "idle", "processed": 0, "total": 0, "current_subject": "", "status": "idle"}
    return {
        "type": "progress",
        "processed": job.processed_messages,
        "total": job.total_messages,
        "current_subject": job.current_subject or "",
        "status": job.status,
        "mode": job.mode,
    }


@router.post("/cancel", response_model=dict)
def cancel_scan(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    journey_id = _require_active_journey(current_user)
    job = get_active_scan_job(db, current_user.id, journey_id)
    if job is None:
        raise HTTPException(status_code=400, detail="No scan is currently running")
    request_scan_job_cancel(db, current_user.id, journey_id, job.id)
    return {"message": "Scan cancellation requested"}


@router.get("/stream")
async def stream_scan(
    request: Request,
    max_emails: int | None = Query(None, ge=0, le=MAX_REQUESTED_MAX_EMAILS),
    incremental: bool | None = Query(None),
    scan_all: bool = Query(False),
    mode: str | None = Query(None),
    since_date: str | None = Query(None),
    before_date: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> StreamingResponse:
    normalized_since = _parse_date_input(since_date, "since_date")
    normalized_before = _parse_date_input(before_date, "before_date")
    resolved_mode = _resolve_scan_mode(
        mode=mode,
        incremental=incremental,
        scan_all=scan_all,
        since_date=normalized_since,
        before_date=normalized_before,
    )
    job, _ = _create_job(
        current_user,
        db,
        config,
        mode=resolved_mode,
        max_emails=max_emails,
        since_date=normalized_since,
        before_date=normalized_before,
    )
    journey_id = _require_active_journey(current_user)
    return _streaming_response(
        _stream_job_events(request, current_user.id, journey_id, int(job["id"]))
    )


@router.post("/stream/cancel", response_model=dict)
def cancel_sse_scan(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    journey_id = _require_active_journey(current_user)
    job = get_active_scan_job(db, current_user.id, journey_id)
    if job is None:
        raise HTTPException(status_code=400, detail="No scan is currently running")
    request_scan_job_cancel(db, current_user.id, journey_id, job.id)
    return {"message": "Scan cancellation requested"}
