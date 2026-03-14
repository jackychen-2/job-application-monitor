from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.config import AppConfig
from job_monitor.email.parser import ParsedEmailData
from job_monitor.extraction.llm import LLMExtractionResult, LLMLinkConfirmResult
from job_monitor.extraction.pipeline import ScanSummary, _process_single_email
from job_monitor.models import Application, Base, ProcessedEmail, StatusHistory


class _StubLLMProvider:
    def __init__(self, result: LLMExtractionResult) -> None:
        self._result = result

    def extract_fields(self, sender: str, subject: str, body: str) -> LLMExtractionResult:
        return self._result

    def confirm_same_application(self, *args, **kwargs) -> LLMLinkConfirmResult:
        return LLMLinkConfirmResult(is_same_application=False)


def _make_config() -> AppConfig:
    return AppConfig(
        imap_host="imap.example.com",
        email_username="candidate@example.com",
        email_password="secret",
        llm_enabled=True,
        llm_timeout_sec=3,
    )


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _make_parsed(
    *,
    subject: str,
    sender: str,
    body: str,
    message_id: str,
    gmail_thread_id: str,
    email_date: datetime,
) -> ParsedEmailData:
    return ParsedEmailData(
        subject=subject,
        sender=sender,
        date_raw=email_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
        date_pt=email_date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        date_dt=email_date,
        body_text=body,
        message_id=message_id,
        gmail_thread_id=gmail_thread_id,
    )


def _job_result(*, status: str = "已申请", req_id: str = "") -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Senior Backend Engineer",
        base_title="Senior Backend Engineer",
        req_id=req_id,
        title_with_req_id="Senior Backend Engineer",
        status=status,
        confidence=0.98,
    )


def _recruiter_result(*, req_id: str = "") -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=False,
        email_category="not_job_related",
        company="Meta",
        job_title="Senior Backend Engineer",
        base_title="Senior Backend Engineer",
        req_id=req_id,
        title_with_req_id="Senior Backend Engineer",
        status="Recruiter Reach-out",
        confidence=0.98,
    )


def _non_job_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=False,
        email_category="not_job_related",
        company="",
        job_title="",
        base_title="",
        req_id="",
        title_with_req_id="",
        status="",
        confidence=0.98,
    )


def _run_email(
    session: Session,
    *,
    uid: int,
    parsed: ParsedEmailData,
    result: LLMExtractionResult,
) -> None:
    config = _make_config()
    _process_single_email(
        session=session,
        config=config,
        llm_provider=_StubLLMProvider(result),
        owner_user_id=1,
        mailbox_email=config.email_username,
        mailbox_folder=config.email_folder,
        uid=uid,
        parsed=parsed,
        summary=ScanSummary(),
        gmail_message_id_override=parsed.message_id,
    )


def test_initial_email_status_history_uses_email_date_and_uid_source() -> None:
    session = _new_session()
    try:
        email_date = _dt(2026, 2, 28, 9, 7)
        _run_email(
            session,
            uid=101,
            parsed=_make_parsed(
                subject="Quick intro about Senior Backend Engineer",
                sender="recruiter@example.com",
                body="I'd like to discuss a specific Senior Backend Engineer role.",
                message_id="msg-101",
                gmail_thread_id="thread-101",
                email_date=email_date,
            ),
            result=_recruiter_result(),
        )
        session.commit()

        history = session.query(StatusHistory).one()
        assert history.change_source == "email_uid_101"
        assert history.changed_at == email_date.replace(tzinfo=None)
    finally:
        session.close()


def test_rescan_same_message_non_job_deletes_orphan_app_even_if_uid_changes() -> None:
    session = _new_session()
    try:
        first_email_date = _dt(2026, 2, 28, 9, 7)
        parsed = _make_parsed(
            subject="Quick intro about Senior Backend Engineer",
            sender="recruiter@example.com",
            body="I'd like to discuss a specific Senior Backend Engineer role.",
            message_id="same-message",
            gmail_thread_id="thread-same-message",
            email_date=first_email_date,
        )
        _run_email(session, uid=201, parsed=parsed, result=_recruiter_result())
        session.commit()

        _run_email(session, uid=202, parsed=parsed, result=_non_job_result())
        session.commit()

        assert session.query(Application).count() == 0
        assert session.query(StatusHistory).count() == 0

        email_row = session.query(ProcessedEmail).one()
        assert email_row.uid == 202
        assert email_row.is_job_related is False
        assert email_row.application_id is None
    finally:
        session.close()


def test_rescan_non_job_removes_stale_status_history_and_reverts_app_status() -> None:
    session = _new_session()
    try:
        application_email = _make_parsed(
            subject="Your application for Senior Backend Engineer",
            sender="no-reply@meta.com",
            body="Thanks for applying to Senior Backend Engineer.",
            message_id="app-email",
            gmail_thread_id="thread-app-email",
            email_date=_dt(2026, 2, 16, 18, 8),
        )
        recruiter_email = _make_parsed(
            subject="Quick intro about Senior Backend Engineer",
            sender="recruiter@example.com",
            body="I'd like to discuss a specific Senior Backend Engineer role.",
            message_id="recruiter-email",
            gmail_thread_id="thread-recruiter-email",
            email_date=_dt(2026, 2, 28, 9, 7),
        )

        _run_email(session, uid=301, parsed=application_email, result=_job_result())
        session.commit()
        _run_email(session, uid=302, parsed=recruiter_email, result=_recruiter_result())
        session.commit()

        app = session.query(Application).one()
        assert app.status == "Recruiter Reach-out"
        assert session.query(StatusHistory).count() == 2

        _run_email(session, uid=303, parsed=recruiter_email, result=_non_job_result())
        session.commit()

        refreshed_app = session.query(Application).one()
        assert refreshed_app.status == "已申请"
        assert session.query(StatusHistory).count() == 1
        assert session.query(StatusHistory).one().new_status == "已申请"

        job_emails = session.query(ProcessedEmail).filter(ProcessedEmail.is_job_related == True).all()  # noqa: E712
        assert len(job_emails) == 1
        assert job_emails[0].gmail_message_id == "app-email"
    finally:
        session.close()


def test_relink_moves_email_derived_history_to_target_application() -> None:
    session = _new_session()
    try:
        target_app = Application(
            owner_user_id=1,
            company="Meta",
            normalized_company="meta",
            job_title="Senior Backend Engineer",
            req_id="R0615432",
            status="Recruiter Reach-out",
            source="email",
        )
        old_app = Application(
            owner_user_id=1,
            company="Meta",
            normalized_company="meta",
            job_title="Senior Backend Engineer",
            req_id="",
            status="Recruiter Reach-out",
            source="email",
        )
        session.add_all([target_app, old_app])
        session.flush()

        session.add(
            StatusHistory(
                owner_user_id=1,
                application_id=old_app.id,
                old_status=None,
                new_status="Recruiter Reach-out",
                change_source="email_uid_401",
                changed_at=_dt(2026, 2, 28, 9, 7),
            )
        )
        session.add(
            ProcessedEmail(
                owner_user_id=1,
                uid=401,
                email_account=_make_config().email_username,
                email_folder=_make_config().email_folder,
                gmail_message_id="relink-message",
                gmail_thread_id="thread-relink-message",
                subject="Quick intro about Senior Backend Engineer",
                sender="recruiter@example.com",
                email_date=_dt(2026, 2, 28, 9, 7),
                is_job_related=True,
                application_id=old_app.id,
                llm_used=True,
                link_method="new",
                needs_review=False,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
            )
        )
        session.commit()

        _run_email(
            session,
            uid=402,
            parsed=_make_parsed(
                subject="Quick intro about Senior Backend Engineer",
                sender="recruiter@example.com",
                body="I'd like to discuss a specific Senior Backend Engineer role.",
                message_id="relink-message",
                gmail_thread_id="thread-relink-message",
                email_date=_dt(2026, 2, 28, 9, 7),
            ),
            result=_recruiter_result(req_id="R0615432"),
        )
        session.commit()

        apps = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps) == 1
        assert apps[0].id == target_app.id

        email_row = session.query(ProcessedEmail).filter(ProcessedEmail.gmail_message_id == "relink-message").one()
        assert email_row.application_id == target_app.id

        history_rows = session.query(StatusHistory).all()
        assert len(history_rows) == 1
        assert history_rows[0].application_id == target_app.id
        assert history_rows[0].change_source == "email_uid_401"
    finally:
        session.close()
