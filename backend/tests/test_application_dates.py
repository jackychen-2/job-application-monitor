from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.api.applications import create_application, split_application, update_application
from job_monitor.api.stats import get_stats
from job_monitor.dedupe import merge_owner_duplicate_applications
from job_monitor.models import Application, Base, ProcessedEmail
from job_monitor.schemas import ApplicationCreate, ApplicationUpdate, SplitApplicationRequest


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _naive_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def test_manual_application_creation_and_status_update_manage_applied_at() -> None:
    session = _new_session()
    try:
        applied = create_application(
            ApplicationCreate(company="OpenAI", status="已申请", source="manual"),
            session,
        )
        recruiter = create_application(
            ApplicationCreate(company="Anthropic", status="Recruiter Reach-out", source="manual"),
            session,
        )

        applied_row = session.get(Application, applied.id)
        recruiter_row = session.get(Application, recruiter.id)
        assert applied_row is not None
        assert recruiter_row is not None
        assert applied_row.applied_at is not None
        assert recruiter_row.applied_at is None

        before_update = datetime.now(timezone.utc)
        updated = update_application(
            recruiter.id,
            ApplicationUpdate(status="面试"),
            session,
        )
        after_update = datetime.now(timezone.utc)

        updated_row = session.get(Application, updated.id)
        assert updated_row is not None
        assert updated_row.applied_at is not None
        assert _naive_utc(before_update) <= updated_row.applied_at <= _naive_utc(after_update)

        first_applied_at = updated_row.applied_at
        update_application(
            recruiter.id,
            ApplicationUpdate(status="Offer"),
            session,
        )
        session.refresh(updated_row)
        assert updated_row.applied_at == first_applied_at
    finally:
        session.close()


def test_split_application_recomputes_applied_at_for_both_records() -> None:
    session = _new_session()
    try:
        first_email_at = datetime(2026, 1, 10, 18, 0, tzinfo=timezone.utc)
        second_email_at = datetime(2026, 1, 20, 18, 0, tzinfo=timezone.utc)

        source = Application(
            company="Stripe",
            normalized_company="stripe",
            job_title="Backend Engineer",
            status="Offer",
            source="email",
            applied_at=first_email_at,
        )
        session.add(source)
        session.flush()

        first_email = ProcessedEmail(
            uid=1001,
            email_account="candidate@example.com",
            email_folder="INBOX",
            gmail_message_id="stripe-1@example.com",
            subject="Stripe application received",
            sender="jobs@stripe.com",
            email_date=first_email_at,
            is_job_related=True,
            application_id=source.id,
        )
        second_email = ProcessedEmail(
            uid=1002,
            email_account="candidate@example.com",
            email_folder="INBOX",
            gmail_message_id="stripe-2@example.com",
            subject="Stripe interview",
            sender="jobs@stripe.com",
            email_date=second_email_at,
            is_job_related=True,
            application_id=source.id,
        )
        session.add_all([first_email, second_email])
        session.commit()

        result = split_application(
            source.id,
            SplitApplicationRequest(email_ids=[first_email.id]),
            session,
        )

        refreshed_source = session.get(Application, source.id)
        new_app = session.get(Application, result.new_application_id)
        assert refreshed_source is not None
        assert new_app is not None
        assert refreshed_source.applied_at == _naive_utc(second_email_at)
        assert new_app.applied_at == _naive_utc(first_email_at)
    finally:
        session.close()


def test_system_dedupe_merge_keeps_earliest_applied_at() -> None:
    session = _new_session()
    try:
        first_applied_at = datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc)
        later_applied_at = datetime(2026, 2, 5, 9, 0, tzinfo=timezone.utc)
        session.add_all(
            [
                Application(
                    owner_user_id=1,
                    journey_id=10,
                    company="Meta",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="REQ-1",
                    status="已申请",
                    source="email",
                    applied_at=later_applied_at,
                ),
                Application(
                    owner_user_id=1,
                    journey_id=10,
                    company="Meta",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="REQ-1",
                    status="OA",
                    source="email",
                    applied_at=first_applied_at,
                ),
            ]
        )
        session.commit()

        merged = merge_owner_duplicate_applications(session, owner_user_id=1, journey_id=10)
        session.commit()

        remaining = session.query(Application).filter(Application.owner_user_id == 1).all()
        assert merged == 1
        assert len(remaining) == 1
        assert remaining[0].applied_at == _naive_utc(first_applied_at)
    finally:
        session.close()


def test_stats_use_applied_at_over_created_at_for_timeline_buckets() -> None:
    session = _new_session()
    try:
        session.add(
            Application(
                company="OpenAI",
                normalized_company="openai",
                status="已申请",
                source="manual",
                created_at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
                applied_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()

        stats = get_stats(db=session)

        assert [(item.date, item.count) for item in stats.daily_applications] == [("2026-01-15", 1)]
        assert sum(item.count for item in stats.hourly_applications_24h) == 0
    finally:
        session.close()
