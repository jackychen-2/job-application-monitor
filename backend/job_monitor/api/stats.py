"""Dashboard statistics endpoint."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import cast, func, Date
from sqlalchemy.orm import Session

from job_monitor.database import get_db
from job_monitor.models import Application, ProcessedEmail, StatusHistory
from job_monitor.schemas import ApplicationOut, FlowData, StatsOut, StatusCount, StatusTransition

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
def get_stats(db: Session = Depends(get_db)) -> StatsOut:
    """Return dashboard statistics: totals, status breakdown, recent activity, daily costs."""
    total = db.query(func.count(Application.id)).scalar() or 0

    # Status breakdown
    status_rows = (
        db.query(Application.status, func.count(Application.id))
        .group_by(Application.status)
        .all()
    )
    status_breakdown = [StatusCount(status=s, count=c) for s, c in status_rows]

    # Recent applications (last 10)
    recent = (
        db.query(Application)
        .order_by(Application.created_at.desc())
        .limit(10)
        .all()
    )

    # Email scan totals
    total_emails = db.query(func.count(ProcessedEmail.id)).scalar() or 0
    total_cost = db.query(func.sum(ProcessedEmail.estimated_cost_usd)).scalar() or 0.0

    # Daily application counts (for heatmap)
    daily_apps_rows = (
        db.query(
            func.date(Application.email_date).label("date"),
            func.count(Application.id).label("count"),
        )
        .filter(Application.email_date != None)  # noqa: E711
        .group_by(func.date(Application.email_date))
        .order_by(func.date(Application.email_date))
        .all()
    )
    daily_applications = [{"date": str(row.date), "count": int(row.count)} for row in daily_apps_rows]

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

    return StatsOut(
        total_applications=total,
        status_breakdown=status_breakdown,
        recent_applications=[ApplicationOut.model_validate(a) for a in recent],
        total_emails_scanned=total_emails,
        total_llm_cost=round(total_cost, 6),
        daily_llm_costs=daily_costs,
        daily_applications=daily_applications,
    )


@router.get("/flow", response_model=FlowData)
def get_flow_data(db: Session = Depends(get_db)) -> FlowData:
    """Return application flow data: status counts + transition edges for Sankey diagram.

    Aggregates StatusHistory transitions (old_status → new_status) and also counts
    applications that are still in their initial status (no transitions yet).
    """
    total = db.query(func.count(Application.id)).scalar() or 0

    # Status breakdown (current snapshot)
    status_rows = (
        db.query(Application.status, func.count(Application.id))
        .group_by(Application.status)
        .all()
    )
    status_counts = [StatusCount(status=s, count=c) for s, c in status_rows]

    # Aggregate transitions from StatusHistory.
    # Initial creation entries have old_status = NULL; map them to a virtual root
    # node so the Sankey graph can be built from transition edges only.
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
    transitions = [
        StatusTransition(from_status=old, to_status=new, count=cnt)
        for old, new, cnt in transition_rows
        if old != new  # skip self-transitions
    ]

    return FlowData(
        status_counts=status_counts,
        transitions=transitions,
        total=total,
    )
