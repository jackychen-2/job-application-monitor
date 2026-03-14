from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.api.stats import get_dashboard_data, get_stats
from job_monitor.models import Application, Base, StatusHistory


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_get_stats_includes_hourly_applications_24h() -> None:
    session = _new_session()
    try:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        session.add_all(
            [
                Application(
                    company="Stripe",
                    normalized_company="stripe",
                    status="已申请",
                    source="manual",
                    created_at=now - timedelta(hours=2),
                ),
                Application(
                    company="OpenAI",
                    normalized_company="openai",
                    status="面试",
                    source="email",
                    created_at=now - timedelta(days=3),
                    email_date=now - timedelta(hours=1),
                ),
                Application(
                    company="Older",
                    normalized_company="older",
                    status="拒绝",
                    source="manual",
                    created_at=now - timedelta(days=2),
                ),
            ]
        )
        session.commit()

        stats = get_stats(db=session)

        assert len(stats.hourly_applications_24h) == 24
        assert sum(item.count for item in stats.hourly_applications_24h) == 1
    finally:
        session.close()


def test_get_dashboard_data_reuses_status_snapshot_for_flow() -> None:
    session = _new_session()
    try:
        session.add_all(
            [
                Application(
                    company="Stripe",
                    normalized_company="stripe",
                    status="已申请",
                    source="manual",
                ),
                Application(
                    company="OpenAI",
                    normalized_company="openai",
                    status="面试",
                    source="email",
                ),
            ]
        )
        session.commit()

        dashboard = get_dashboard_data(db=session)

        assert dashboard.total_applications == 2
        assert dashboard.flow.total == 2
        assert sorted((item.status, item.count) for item in dashboard.status_breakdown) == [
            ("已申请", 1),
            ("面试", 1),
        ]
        assert sorted((item.status, item.count) for item in dashboard.flow.status_counts) == [
            ("已申请", 1),
            ("面试", 1),
        ]
        assert sorted(
            (item.from_status, item.to_status, item.count)
            for item in dashboard.flow.transitions
        ) == [
            ("Applications", "已申请", 1),
            ("Applications", "面试", 1),
        ]
        assert dashboard.recent_applications == []
    finally:
        session.close()


def test_flow_uses_first_status_not_current_status_for_root_edges() -> None:
    session = _new_session()
    try:
        app = Application(
            company="Anthropic",
            normalized_company="anthropic",
            status="已申请",
            source="email",
        )
        session.add(app)
        session.flush()
        session.add_all(
            [
                StatusHistory(
                    application_id=app.id,
                    old_status=None,
                    new_status="Recruiter Reach-out",
                    change_source="email",
                ),
                StatusHistory(
                    application_id=app.id,
                    old_status="Recruiter Reach-out",
                    new_status="已申请",
                    change_source="email",
                ),
            ]
        )
        session.commit()

        dashboard = get_dashboard_data(db=session)

        assert sorted(
            (item.from_status, item.to_status, item.count)
            for item in dashboard.flow.transitions
        ) == [
            ("Applications", "Recruiter Reach-out", 1),
            ("Recruiter Reach-out", "已申请", 1),
        ]
    finally:
        session.close()
