"""Export endpoints for downloading application data as CSV or Excel."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from job_monitor.database import get_db
from job_monitor.export.csv_export import export_applications_csv
from job_monitor.export.excel_export import export_applications_excel
from job_monitor.models import Application

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("")
def export_data(
    format: str = Query("csv", pattern="^(csv|excel)$", description="Export format"),
    db: Session = Depends(get_db),
) -> Response:
    """Download all applications as CSV or Excel."""
    applications = db.query(Application).order_by(Application.created_at.desc()).all()

    if format == "excel":
        content = export_applications_excel(applications)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=applications.xlsx"},
        )

    # Default: CSV
    content = export_applications_csv(applications)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=applications.csv"},
    )
