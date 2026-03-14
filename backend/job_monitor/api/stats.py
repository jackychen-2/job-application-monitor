"""Dashboard statistics endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from job_monitor.auth.deps import get_owner_scoped_db
from job_monitor.models import Application, ProcessedEmail, StatusHistory
from job_monitor.schemas import (
    ApplicationOut,
    DashboardDataOut,
    FlowData,
    StatsOut,
    StatusCount,
    StatusTransition,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/stats", tags=["stats"])


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _get_status_snapshot(db: Session) -> tuple[int, list[StatusCount]]:
    status_rows = (
        db.query(Application.status, func.count(Application.id))
        .group_by(Application.status)
        .all()
    )
    status_breakdown = [StatusCount(status=s, count=int(c)) for s, c in status_rows]
    total = sum(item.count for item in status_breakdown)
    return total, status_breakdown


def _build_stats_fields(
    db: Session,
    total: int,
    status_breakdown: list[StatusCount],
) -> dict[str, Any]:
    application_timeline_at = func.coalesce(Application.applied_at, Application.created_at)

    # Email scan totals
    total_emails = db.query(func.count(ProcessedEmail.id)).scalar() or 0
    total_cost = db.query(func.sum(ProcessedEmail.estimated_cost_usd)).scalar() or 0.0

    # Daily application counts (for heatmap)
    daily_apps_rows = (
        db.query(
            func.date(application_timeline_at).label("date"),
            func.count(Application.id).label("count"),
        )
        .filter(application_timeline_at != None)  # noqa: E711
        .group_by(func.date(application_timeline_at))
        .order_by(func.date(application_timeline_at))
        .all()
    )
    daily_applications = [{"date": str(row.date), "count": int(row.count)} for row in daily_apps_rows]

    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    hour_start = current_hour - timedelta(hours=23)
    hourly_buckets = {
        hour_start + timedelta(hours=offset): 0
        for offset in range(24)
    }
    recent_activity_rows = (
        db.query(Application.applied_at, Application.created_at)
        .filter(application_timeline_at >= hour_start)
        .all()
    )
    for applied_at, created_at in recent_activity_rows:
        activity_at = _as_utc(applied_at) or _as_utc(created_at)
        if activity_at is None or activity_at < hour_start:
            continue
        bucket = activity_at.replace(minute=0, second=0, microsecond=0)
        if bucket > current_hour:
            continue
        if bucket in hourly_buckets:
            hourly_buckets[bucket] += 1
    hourly_applications_24h = [
        {"timestamp": bucket.isoformat(), "count": count}
        for bucket, count in sorted(hourly_buckets.items())
    ]

    # Daily LLM cost history (for line chart)
    daily_costs_rows = (
        db.query(
            func.date(ProcessedEmail.processed_at).label("date"),
            func.sum(ProcessedEmail.estimated_cost_usd).label("cost"),
        )
        .filter(ProcessedEmail.llm_used == True)  # noqa: E712
        .group_by(func.date(ProcessedEmail.processed_at))
        .order_by(func.date(ProcessedEmail.processed_at))
        .all()
    )
    daily_costs = [{"date": str(row.date), "cost": round(float(row.cost or 0), 6)} for row in daily_costs_rows]

    return {
        "total_applications": total,
        "status_breakdown": status_breakdown,
        "total_emails_scanned": total_emails,
        "total_llm_cost": round(total_cost, 6),
        "daily_llm_costs": daily_costs,
        "daily_applications": daily_applications,
        "hourly_applications_24h": hourly_applications_24h,
    }


def _build_flow_data(
    db: Session,
    total: int,
    status_counts: list[StatusCount],
) -> FlowData:
    """Build Sankey flow data from current status counts and first/next status history."""
    from_status_expr = func.coalesce(StatusHistory.old_status, "Applications")
    transition_rows = (
        db.query(
            from_status_expr,
            StatusHistory.new_status,
            func.count(StatusHistory.id),
        )
        .group_by(from_status_expr, StatusHistory.new_status)
        .all()
    )
    transition_counts: dict[tuple[str, str], int] = {}

    for old, new, cnt in transition_rows:
        if old == new:
            continue
        key = (old, new)
        transition_counts[key] = transition_counts.get(key, 0) + int(cnt)

    # Some legacy/manual rows may exist without an initial status_history record.
    # Treat the current status as the entry edge so the Sankey still has a stable root.
    apps_without_history_rows = (
        db.query(
            Application.status,
            func.count(Application.id),
        )
        .outerjoin(StatusHistory, StatusHistory.application_id == Application.id)
        .filter(StatusHistory.id == None)  # noqa: E711
        .group_by(Application.status)
        .all()
    )
    for status, cnt in apps_without_history_rows:
        if not status or cnt <= 0:
            continue
        key = ("Applications", status)
        transition_counts[key] = transition_counts.get(key, 0) + int(cnt)

    transitions = [
        StatusTransition(from_status=old, to_status=new, count=count)
        for (old, new), count in sorted(transition_counts.items())
    ]

    return FlowData(
        status_counts=status_counts,
        transitions=transitions,
        total=total,
    )


@router.get("", response_model=StatsOut)
def get_stats(db: Session = Depends(get_owner_scoped_db)) -> StatsOut:
    """Return dashboard statistics: totals, status breakdown, recent activity, daily costs."""
    total, status_breakdown = _get_status_snapshot(db)

    recent = (
        db.query(Application)
        .order_by(func.coalesce(Application.applied_at, Application.created_at).desc())
        .limit(10)
        .all()
    )

    stats_fields = _build_stats_fields(db, total, status_breakdown)
    return StatsOut(
        recent_applications=[ApplicationOut.model_validate(a) for a in recent],
        **stats_fields,
    )


@router.get("/flow", response_model=FlowData)
def get_flow_data(db: Session = Depends(get_owner_scoped_db)) -> FlowData:
    """Return application flow data: status counts + transition edges for Sankey diagram.

    Aggregates StatusHistory transitions (old_status → new_status) and also counts
    applications that are still in their initial status (no transitions yet).
    """
    total, status_counts = _get_status_snapshot(db)
    return _build_flow_data(db, total, status_counts)


@router.get("/dashboard", response_model=DashboardDataOut)
def get_dashboard_data(db: Session = Depends(get_owner_scoped_db)) -> DashboardDataOut:
    """Return the dashboard payload in one request to avoid duplicate aggregates."""
    total, status_breakdown = _get_status_snapshot(db)
    stats_fields = _build_stats_fields(db, total, status_breakdown)
    flow = _build_flow_data(db, total, status_breakdown)
    return DashboardDataOut(
        recent_applications=[],
        flow=flow,
        **stats_fields,
    )
