from __future__ import annotations

from datetime import timedelta, timezone, datetime
from email.message import EmailMessage

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.config import AppConfig
from job_monitor.email.gmail_client import GmailHistoryExpiredError
from job_monitor.models import Base, Journey, ScanJobMessage, ScanState, User
from job_monitor.scan_jobs import (
    create_scan_job,
    create_incremental_scan_job,
    get_active_scan_job,
    request_scan_job_cancel,
    run_scan_job_step,
)
import job_monitor.scan_jobs as scan_jobs


class FakeGmailClient:
    message_ids_after_history: list[str] = []
    latest_message_ids: list[str] = []
    trackable_message_ids: list[str] = []
    date_range_message_ids: list[str] = []
    latest_history_id: int = 0
    raise_history_expired: bool = False
    message_payloads: dict[str, tuple[int, EmailMessage | None, str | None, str, int, list[str]]] = {}

    def __init__(self, config: AppConfig, *, oauth_access_token: str) -> None:
        del config, oauth_access_token

    def __enter__(self) -> "FakeGmailClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def fetch_message_ids_after_history(self, start_history_id: int, *, max_count: int | None) -> tuple[list[str], int]:
        del start_history_id
        if self.raise_history_expired:
            raise GmailHistoryExpiredError("expired")
        ids = self.message_ids_after_history[: max_count or len(self.message_ids_after_history)]
        return ids, self.latest_history_id

    def fetch_latest_message_ids(self, count: int) -> tuple[list[str], int]:
        return self.latest_message_ids[:count], self.latest_history_id

    def fetch_trackable_message_ids(self, max_count: int | None = None) -> tuple[list[str], int]:
        ids = self.trackable_message_ids
        if max_count is not None:
            ids = ids[:max_count]
        return ids, self.latest_history_id

    def fetch_message_ids_by_date_range(
        self,
        since_date: str | None = None,
        before_date: str | None = None,
        max_count: int | None = None,
    ) -> tuple[list[str], int]:
        del since_date, before_date
        ids = self.date_range_message_ids
        if max_count is not None:
            ids = ids[:max_count]
        return ids, self.latest_history_id

    def fetch_message(self, gmail_message_id: str) -> tuple[int, EmailMessage | None, str | None, str, int, list[str]]:
        return self.message_payloads[gmail_message_id]


def _message(subject: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "jobs@example.com"
    message["Date"] = "Mon, 10 Mar 2025 10:00:00 +0000"
    message.set_content("Thank you for applying.")
    return message


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _config(*, batch_size: int = 1) -> AppConfig:
    return AppConfig(
        database_url="sqlite:///:memory:",
        llm_enabled=False,
        scan_job_batch_size=batch_size,
        scan_job_continue_enabled=False,
        max_scan_emails=50,
    )


def _bootstrap_scope(session: Session) -> tuple[User, Journey]:
    user = User(email="user@example.com", google_sub="sub-1", is_active=True)
    session.add(user)
    session.flush()
    journey = Journey(owner_user_id=user.id, name="Default Journey")
    session.add(journey)
    session.flush()
    user.active_journey_id = journey.id
    session.commit()
    return user, journey


def _fake_process_single_email(
    session: Session,
    config: AppConfig,
    llm_provider,
    owner_user_id: int,
    mailbox_email: str,
    mailbox_folder: str,
    uid: int,
    parsed,
    summary,
    gmail_message_id_override: str | None = None,
) -> None:
    del session, config, llm_provider, owner_user_id, mailbox_email, mailbox_folder, parsed, gmail_message_id_override
    summary.emails_matched += 1
    summary.applications_created += 1
    summary.created_application_ids.append(uid)


@pytest.fixture(autouse=True)
def _patch_scan_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeGmailClient.message_ids_after_history = []
    FakeGmailClient.latest_message_ids = []
    FakeGmailClient.trackable_message_ids = []
    FakeGmailClient.date_range_message_ids = []
    FakeGmailClient.latest_history_id = 0
    FakeGmailClient.raise_history_expired = False
    FakeGmailClient.message_payloads = {}
    monkeypatch.setattr(scan_jobs, "GmailClient", FakeGmailClient)
    monkeypatch.setattr(scan_jobs, "_process_single_email", _fake_process_single_email)


def test_create_scan_job_reuses_existing_active_job() -> None:
    session = _new_session()
    try:
        user, journey = _bootstrap_scope(session)
        FakeGmailClient.message_ids_after_history = ["m1"]
        FakeGmailClient.latest_history_id = 10

        job, reused = create_incremental_scan_job(
            session,
            _config(),
            user.id,
            journey.id,
            "user@example.com",
            "token",
        )
        same_job, reused_again = create_incremental_scan_job(
            session,
            _config(),
            user.id,
            journey.id,
            "user@example.com",
            "token",
        )

        assert reused is False
        assert reused_again is True
        assert same_job.id == job.id
        assert session.query(ScanJobMessage).count() == 1
    finally:
        session.close()


def test_create_scan_job_falls_back_when_history_cursor_expired() -> None:
    session = _new_session()
    try:
        user, journey = _bootstrap_scope(session)
        session.add(
            ScanState(
                owner_user_id=user.id,
                journey_id=journey.id,
                email_account="user@example.com",
                email_folder="INBOX",
                last_uid=42,
            )
        )
        session.commit()

        FakeGmailClient.raise_history_expired = True
        FakeGmailClient.latest_message_ids = ["m1", "m2"]
        FakeGmailClient.latest_history_id = 99

        job, reused = create_incremental_scan_job(
            session,
            _config(),
            user.id,
            journey.id,
            "user@example.com",
            "token",
        )

        assert reused is False
        assert job.history_fallback_used is True
        assert job.total_messages == 2
        assert job.history_latest_id == 99
    finally:
        session.close()


def test_step_processes_fixed_batch_and_updates_scan_state_only_on_completion() -> None:
    session = _new_session()
    try:
        user, journey = _bootstrap_scope(session)
        session.add(
            ScanState(
                owner_user_id=user.id,
                journey_id=journey.id,
                email_account="user@example.com",
                email_folder="INBOX",
                last_uid=5,
            )
        )
        session.commit()

        FakeGmailClient.message_ids_after_history = ["m1", "m2"]
        FakeGmailClient.latest_history_id = 20
        FakeGmailClient.message_payloads = {
            "m1": (101, _message("First"), "thread-1", "m1", 11, ["INBOX"]),
            "m2": (102, _message("Second"), "thread-2", "m2", 20, ["INBOX"]),
        }

        job, _ = create_incremental_scan_job(
            session,
            _config(batch_size=1),
            user.id,
            journey.id,
            "user@example.com",
            "token",
        )

        job, processed_in_step, done = run_scan_job_step(
            session,
            _config(batch_size=1),
            user.id,
            journey.id,
            job.id,
            "token",
        )

        state_after_first = session.query(ScanState).filter(ScanState.owner_user_id == user.id).first()
        assert processed_in_step == 1
        assert done is False
        assert job.status == "running"
        assert state_after_first is not None
        assert state_after_first.last_uid == 5

        job, processed_in_step, done = run_scan_job_step(
            session,
            _config(batch_size=1),
            user.id,
            journey.id,
            job.id,
            "token",
        )

        state_after_second = session.query(ScanState).filter(ScanState.owner_user_id == user.id).first()
        assert processed_in_step == 1
        assert done is True
        assert job.status == "completed"
        assert state_after_second is not None
        assert state_after_second.last_uid == 20
    finally:
        session.close()


def test_cancel_requested_job_becomes_cancelled_without_processing() -> None:
    session = _new_session()
    try:
        user, journey = _bootstrap_scope(session)
        FakeGmailClient.message_ids_after_history = ["m1"]
        FakeGmailClient.latest_history_id = 10

        job, _ = create_incremental_scan_job(
            session,
            _config(),
            user.id,
            journey.id,
            "user@example.com",
            "token",
        )
        cancelled_job = request_scan_job_cancel(session, user.id, journey.id, job.id)
        assert cancelled_job is not None
        assert cancelled_job.status == "cancel_requested"

        job, processed_in_step, done = run_scan_job_step(
            session,
            _config(),
            user.id,
            journey.id,
            job.id,
            "token",
        )

        assert processed_in_step == 0
        assert done is True
        assert job.status == "cancelled"
    finally:
        session.close()


def test_stale_processing_messages_are_reclaimed() -> None:
    session = _new_session()
    try:
        user, journey = _bootstrap_scope(session)
        FakeGmailClient.message_ids_after_history = ["m1"]
        FakeGmailClient.latest_history_id = 15
        FakeGmailClient.message_payloads = {
            "m1": (201, _message("Recovered"), "thread-recovered", "m1", 15, ["INBOX"]),
        }

        job, _ = create_incremental_scan_job(
            session,
            _config(),
            user.id,
            journey.id,
            "user@example.com",
            "token",
        )
        queued_message = session.query(ScanJobMessage).filter(ScanJobMessage.scan_job_id == job.id).one()
        queued_message.status = "processing"
        queued_message.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        job.status = "running"
        session.commit()

        job, processed_in_step, done = run_scan_job_step(
            session,
            _config(),
            user.id,
            journey.id,
            job.id,
            "token",
        )

        recovered = session.get(ScanJobMessage, queued_message.id)
        assert processed_in_step == 1
        assert done is True
        assert job.status == "completed"
        assert recovered is not None
        assert recovered.status == "done"
    finally:
        session.close()


def test_active_job_lookup_is_scoped_by_owner_and_journey() -> None:
    session = _new_session()
    try:
        user_a, journey_a = _bootstrap_scope(session)
        user_b = User(email="other@example.com", google_sub="sub-2", is_active=True)
        session.add(user_b)
        session.flush()
        journey_b = Journey(owner_user_id=user_b.id, name="Other Journey")
        session.add(journey_b)
        session.commit()

        FakeGmailClient.message_ids_after_history = ["m1"]
        FakeGmailClient.latest_history_id = 10

        job_a, _ = create_incremental_scan_job(
            session,
            _config(),
            user_a.id,
            journey_a.id,
            "user@example.com",
            "token",
        )

        FakeGmailClient.message_ids_after_history = ["m2"]
        FakeGmailClient.latest_history_id = 11
        job_b, _ = create_incremental_scan_job(
            session,
            _config(),
            user_b.id,
            journey_b.id,
            "other@example.com",
            "token",
        )

        assert get_active_scan_job(session, user_a.id, journey_a.id).id == job_a.id
        assert get_active_scan_job(session, user_b.id, journey_b.id).id == job_b.id
    finally:
        session.close()


def test_create_full_scan_job_uses_full_mode_listing() -> None:
    session = _new_session()
    try:
        user, journey = _bootstrap_scope(session)
        FakeGmailClient.trackable_message_ids = ["m1", "m2", "m3"]
        FakeGmailClient.latest_history_id = 25

        job, reused = create_scan_job(
            session,
            _config(),
            user.id,
            journey.id,
            "user@example.com",
            "token",
            mode="full",
            requested_max_emails=0,
        )

        assert reused is False
        assert job.mode == "full"
        assert job.requested_max_emails == 0
        assert job.total_messages == 3
        assert session.query(ScanJobMessage).count() == 3
    finally:
        session.close()


def test_date_range_scan_does_not_advance_scan_state_cursor() -> None:
    session = _new_session()
    try:
        user, journey = _bootstrap_scope(session)
        session.add(
            ScanState(
                owner_user_id=user.id,
                journey_id=journey.id,
                email_account="user@example.com",
                email_folder="INBOX",
                last_uid=7,
            )
        )
        session.commit()

        FakeGmailClient.date_range_message_ids = ["m1"]
        FakeGmailClient.latest_history_id = 99
        FakeGmailClient.message_payloads = {
            "m1": (301, _message("Range message"), "thread-range", "m1", 99, ["INBOX"]),
        }

        job, reused = create_scan_job(
            session,
            _config(),
            user.id,
            journey.id,
            "user@example.com",
            "token",
            mode="date_range",
            requested_max_emails=0,
            since_date="2026-01-01",
            before_date="2026-01-31",
        )

        assert reused is False
        assert job.mode == "date_range"
        assert job.since_date == "2026-01-01"
        assert job.before_date == "2026-01-31"

        job, processed_in_step, done = run_scan_job_step(
            session,
            _config(),
            user.id,
            journey.id,
            job.id,
            "token",
        )

        state_after = session.query(ScanState).filter(ScanState.owner_user_id == user.id).first()
        assert processed_in_step == 1
        assert done is True
        assert job.status == "completed"
        assert state_after is not None
        assert state_after.last_uid == 7
    finally:
        session.close()
