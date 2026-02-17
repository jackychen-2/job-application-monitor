"""CSV export for application data."""

from __future__ import annotations

import csv
import io
from typing import List

from job_monitor.models import Application


def export_applications_csv(applications: List[Application]) -> str:
    """Export a list of Application ORM objects to a CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Company", "Job Title", "Status", "Email Subject", "Email Date", "Source", "Notes", "Created At", "Updated At"])

    for app in applications:
        writer.writerow([
            app.id,
            app.company,
            app.job_title or "",
            app.status,
            app.email_subject or "",
            app.email_date.isoformat() if app.email_date else "",
            app.source,
            app.notes or "",
            app.created_at.isoformat() if app.created_at else "",
            app.updated_at.isoformat() if app.updated_at else "",
        ])

    return output.getvalue()
