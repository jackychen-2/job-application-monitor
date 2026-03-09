from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.api.stats import get_stats
from job_monitor.models import Application, Base


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
        assert sum(item.count for item in stats.hourly_applications_24h) == 2
    finally:
        session.close()
