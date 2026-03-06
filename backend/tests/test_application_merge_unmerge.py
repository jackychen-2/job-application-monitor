"""Tests for manual merge/unmerge lifecycle."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.api.applications import (
    list_application_merge_events,
    merge_applications,
    split_application,
    unmerge_application,
)
from job_monitor.dedupe import merge_owner_duplicate_applications
from job_monitor.models import Application, Base, ProcessedEmail, StatusHistory
from job_monitor.schemas import MergeApplicationRequest, SplitApplicationRequest


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_merge_then_unmerge_restores_source_and_locks_dedupe() -> None:
    session = _new_session()
    try:
        target = Application(
            company="Stripe",
            normalized_company="stripe",
            job_title="Backend Engineer",
            req_id="REQ-1",
            status="已申请",
            source="manual",
        )
        source = Application(
            company="Stripe",
            normalized_company="stripe",
            job_title="Backend Engineer",
            req_id="REQ-2",
            status="OA",
            source="manual",
        )
        session.add_all([target, source])
        session.flush()

        session.add_all(
            [
                ProcessedEmail(
                    uid=1001,
                    email_account="candidate@example.com",
                    email_folder="INBOX",
                    gmail_message_id="msg-1@example.com",
                    subject="Stripe OA",
                    sender="jobs@stripe.com",
                    is_job_related=True,
                    application_id=source.id,
                ),
                ProcessedEmail(
                    uid=1002,
                    email_account="candidate@example.com",
                    email_folder="INBOX",
                    gmail_message_id="msg-2@example.com",
                    subject="Stripe Interview",
                    sender="jobs@stripe.com",
                    is_job_related=True,
                    application_id=source.id,
                ),
                StatusHistory(
                    application_id=source.id,
                    old_status=None,
                    new_status="已申请",
                    change_source="manual",
                ),
                StatusHistory(
                    application_id=source.id,
                    old_status="已申请",
                    new_status="OA",
                    change_source="email_scan",
                ),
            ]
        )
        session.commit()

        merge_applications(
            target.id,
            MergeApplicationRequest(source_application_id=source.id),
            session,
        )

        assert session.query(Application).filter(Application.id == source.id).first() is None
        assert (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.application_id == target.id)
            .count()
            == 2
        )
        assert (
            session.query(StatusHistory)
            .filter(StatusHistory.application_id == target.id)
            .count()
            == 2
        )

        events = list_application_merge_events(target.id, session)
        assert len(events) == 1
        event = events[0]
        assert event.moved_email_count == 2
        assert event.moved_history_count == 2
        assert event.undone_at is None

        restored = unmerge_application(target.id, event.id, session)
        restored_source_id = restored.restored_source_application_id

        assert restored.restored_email_count == 2
        assert restored.restored_history_count == 2
        assert restored_source_id != target.id

        restored_source = session.get(Application, restored_source_id)
        assert restored_source is not None
        assert restored_source.company == "Stripe"
        assert restored_source.req_id == "REQ-2"
        assert restored_source.dedupe_locked is True

        reloaded_target = session.get(Application, target.id)
        assert reloaded_target is not None
        assert reloaded_target.dedupe_locked is True

        assert (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.application_id == restored_source_id)
            .count()
            == 2
        )
        assert (
            session.query(StatusHistory)
            .filter(StatusHistory.application_id == restored_source_id)
            .count()
            == 2
        )
    finally:
        session.close()


def test_system_dedupe_merge_can_be_unmerged() -> None:
    session = _new_session()
    try:
        app_keep = Application(
            owner_user_id=1,
            journey_id=10,
            company="Meta",
            normalized_company="meta",
            job_title="SWE",
            req_id="R1",
            status="已申请",
            source="email",
        )
        app_dup = Application(
            owner_user_id=1,
            journey_id=10,
            company="Meta\u200b",
            normalized_company="meta",
            job_title="SWE",
            req_id="R1",
            status="OA",
            source="email",
        )
        session.add_all([app_keep, app_dup])
        session.flush()

        session.add(
            ProcessedEmail(
                owner_user_id=1,
                journey_id=10,
                uid=1999,
                email_account="candidate@example.com",
                email_folder="INBOX",
                gmail_message_id="sys-msg-0@example.com",
                subject="Meta Applied",
                sender="jobs@meta.com",
                is_job_related=True,
                application_id=app_keep.id,
            )
        )
        session.add(
            ProcessedEmail(
                owner_user_id=1,
                journey_id=10,
                uid=2000,
                email_account="candidate@example.com",
                email_folder="INBOX",
                gmail_message_id="sys-msg-0b@example.com",
                subject="Meta Follow Up",
                sender="jobs@meta.com",
                is_job_related=True,
                application_id=app_keep.id,
            )
        )
        session.add(
            ProcessedEmail(
                owner_user_id=1,
                journey_id=10,
                uid=2001,
                email_account="candidate@example.com",
                email_folder="INBOX",
                gmail_message_id="sys-msg-1@example.com",
                subject="Meta OA",
                sender="jobs@meta.com",
                is_job_related=True,
                application_id=app_dup.id,
            )
        )
        session.add(
            StatusHistory(
                owner_user_id=1,
                journey_id=10,
                application_id=app_dup.id,
                old_status="已申请",
                new_status="OA",
                change_source="email_scan",
            )
        )
        session.commit()

        merged = merge_owner_duplicate_applications(session, owner_user_id=1, journey_id=10)
        session.commit()
        assert merged == 1

        remaining_apps = session.query(Application).filter(Application.owner_user_id == 1).all()
        assert len(remaining_apps) == 1
        target_id = remaining_apps[0].id

        events = list_application_merge_events(target_id, session)
        assert len(events) == 1
        event = events[0]
        assert event.merge_source == "system_dedupe"
        assert event.undone_at is None

        restored = unmerge_application(target_id, event.id, session)
        restored_source = session.get(Application, restored.restored_source_application_id)
        assert restored_source is not None
        assert restored_source.company == "Meta\u200b"
        assert restored_source.dedupe_locked is True
        assert restored.restored_email_count == 1
        assert restored.restored_history_count == 1
    finally:
        session.close()


def test_split_application_moves_selected_emails_to_new_record() -> None:
    session = _new_session()
    try:
        app = Application(
            company="Amazon",
            normalized_company="amazon",
            job_title="Data Engineer",
            req_id="3194491",
            status="已申请",
            source="email",
        )
        session.add(app)
        session.flush()

        session.add_all(
            [
                ProcessedEmail(
                    uid=3001,
                    email_account="candidate@example.com",
                    email_folder="INBOX",
                    gmail_message_id="amz-msg-1@example.com",
                    subject="Thank you for Applying to Amazon!",
                    sender="noreply@mail.amazon.jobs",
                    is_job_related=True,
                    application_id=app.id,
                ),
                ProcessedEmail(
                    uid=3002,
                    email_account="candidate@example.com",
                    email_folder="INBOX",
                    gmail_message_id="amz-msg-2@example.com",
                    subject="Keep track of your application",
                    sender="noreply@mail.amazon.jobs",
                    is_job_related=True,
                    application_id=app.id,
                ),
            ]
        )
        session.commit()

        second_email = (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.gmail_message_id == "amz-msg-2@example.com")
            .first()
        )
        assert second_email is not None

        result = split_application(
            app.id,
            SplitApplicationRequest(
                email_ids=[second_email.id],
                company="Amazon",
                job_title="Data Engineer",
                req_id="3194491-SPLIT",
            ),
            session,
        )

        assert result.moved_email_count == 1
        assert result.source_application_id == app.id
        assert result.new_application_id != app.id

        moved = session.query(ProcessedEmail).filter(ProcessedEmail.id == second_email.id).first()
        assert moved is not None
        assert moved.application_id == result.new_application_id

        source_app = session.get(Application, app.id)
        new_app = session.get(Application, result.new_application_id)
        assert source_app is not None
        assert new_app is not None
        assert source_app.dedupe_locked is True
        assert new_app.dedupe_locked is True

        assert (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.application_id == app.id)
            .count()
            == 1
        )
        assert (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.application_id == result.new_application_id)
            .count()
            == 1
        )
    finally:
        session.close()


def test_split_application_allows_same_key_as_source() -> None:
    session = _new_session()
    try:
        app = Application(
            company="Amazon",
            normalized_company="amazon",
            job_title="Data Engineer",
            req_id="3194491",
            status="已申请",
            source="email",
        )
        session.add(app)
        session.flush()

        email = ProcessedEmail(
            uid=4001,
            email_account="candidate@example.com",
            email_folder="INBOX",
            gmail_message_id="amz-msg-same-key@example.com",
            subject="Keep track of your application",
            sender="noreply@mail.amazon.jobs",
            is_job_related=True,
            application_id=app.id,
        )
        session.add(email)
        session.add(
            ProcessedEmail(
                uid=4002,
                email_account="candidate@example.com",
                email_folder="INBOX",
                gmail_message_id="amz-msg-same-key-2@example.com",
                subject="Thank you for Applying to Amazon!",
                sender="noreply@mail.amazon.jobs",
                is_job_related=True,
                application_id=app.id,
            )
        )
        session.commit()

        result = split_application(
            app.id,
            SplitApplicationRequest(
                email_ids=[email.id],
                company="Amazon",
                job_title="Data Engineer",
                req_id="3194491",
            ),
            session,
        )

        assert result.moved_email_count == 1
        new_app = session.get(Application, result.new_application_id)
        assert new_app is not None
        assert new_app.company == "Amazon"
        assert new_app.job_title == "Data Engineer"
        assert new_app.req_id == "3194491"
    finally:
        session.close()
