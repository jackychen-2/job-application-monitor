"""Pipeline tests for recruiter outreach status handling."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.config import AppConfig
from job_monitor.email.parser import ParsedEmailData
from job_monitor.extraction.llm import LLMExtractionResult
from job_monitor.extraction.pipeline import ScanSummary, _process_single_email
from job_monitor.extraction.rules import extract_status
from job_monitor.models import Application, Base, ProcessedEmail


class _StubLLMProvider:
    def __init__(self, result: LLMExtractionResult) -> None:
        self._result = result

    def extract_fields(self, sender: str, subject: str, body: str) -> LLMExtractionResult:
        return self._result


def _make_config() -> AppConfig:
    return AppConfig(
        imap_host="imap.example.com",
        email_username="candidate@example.com",
        email_password="secret",
        llm_enabled=True,
        llm_timeout_sec=3,
    )


def _make_parsed(uid: int) -> ParsedEmailData:
    return ParsedEmailData(
        subject=f"Opportunity #{uid}",
        sender="recruiter@example.com",
        date_raw="Fri, 27 Feb 2026 10:00:00 +0000",
        date_pt="2026-02-27 02:00:00 PST",
        date_dt=datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc),
        body_text="We'd like to discuss a Senior Backend Engineer role.",
        message_id=f"msg-{uid}@example.com",
        gmail_thread_id=f"thread-{uid}",
    )


def _make_recruiter_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=False,
        email_category="not_job_related",
        company="Meta",
        job_title="Senior Backend Engineer",
        base_title="Senior Backend Engineer",
        req_id="",
        title_with_req_id="Senior Backend Engineer",
        status="Recruiter Reach-out",
        confidence=0.98,
    )


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_job_application_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Senior Backend Engineer",
        base_title="Senior Backend Engineer",
        req_id="",
        title_with_req_id="Senior Backend Engineer",
        status="已申请",
        confidence=0.98,
    )


def _make_offer_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Senior Backend Engineer",
        base_title="Senior Backend Engineer",
        req_id="",
        title_with_req_id="Senior Backend Engineer",
        status="Offer",
        confidence=0.98,
    )


def _make_oa_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Senior Backend Engineer",
        base_title="Senior Backend Engineer",
        req_id="",
        title_with_req_id="Senior Backend Engineer",
        status="OA",
        confidence=0.98,
    )


def _make_onboarding_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Senior Backend Engineer",
        base_title="Senior Backend Engineer",
        req_id="",
        title_with_req_id="Senior Backend Engineer",
        status="Onboarding",
        confidence=0.98,
    )


def test_recruiter_reach_out_sets_status_and_preserves_title() -> None:
    session = _new_session()
    try:
        summary = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_recruiter_result()),
            uid=1,
            parsed=_make_parsed(1),
            summary=summary,
        )
        session.commit()

        app = session.query(Application).one()
        processed = session.query(ProcessedEmail).one()

        assert app.company == "Meta"
        assert app.source == "email"
        assert app.job_title == "Senior Backend Engineer"
        assert app.status == "Recruiter Reach-out"
        assert processed.application_id == app.id
        assert processed.is_job_related is True
        assert summary.applications_created == 1
        assert summary.emails_matched == 1
    finally:
        session.close()


def test_follow_up_application_reuses_group_and_advances_status() -> None:
    session = _new_session()
    try:
        summary_a = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_recruiter_result()),
            uid=2_001,
            parsed=_make_parsed(2_001),
            summary=summary_a,
        )
        session.commit()

        summary_b = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_result()),
            uid=2_002,
            parsed=_make_parsed(2_002),
            summary=summary_b,
        )
        session.commit()

        apps = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps) == 1
        assert apps[0].status == "已申请"
        assert apps[0].job_title == "Senior Backend Engineer"
        assert summary_a.applications_created == 1
        assert summary_b.applications_created == 0
        assert summary_b.applications_updated == 1
    finally:
        session.close()


def test_repeated_recruiter_reach_out_reuses_same_group() -> None:
    session = _new_session()
    try:
        provider = _StubLLMProvider(_make_recruiter_result())
        summary_a = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=provider,
            uid=10,
            parsed=_make_parsed(10),
            summary=summary_a,
        )
        session.commit()

        summary_b = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=provider,
            uid=11,
            parsed=_make_parsed(11),
            summary=summary_b,
        )
        session.commit()

        apps = session.query(Application).all()
        assert len(apps) == 1
        app_id = apps[0].id
        assert apps[0].status == "Recruiter Reach-out"

        emails = session.query(ProcessedEmail).order_by(ProcessedEmail.uid.asc()).all()
        assert len(emails) == 2
        assert all(e.application_id == app_id for e in emails)
        assert summary_a.applications_created == 1
        assert summary_b.applications_created == 0
    finally:
        session.close()


def test_offer_followed_by_onboarding_reuses_group_and_advances_status() -> None:
    session = _new_session()
    try:
        summary_offer = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_offer_result()),
            uid=30,
            parsed=_make_parsed(30),
            summary=summary_offer,
        )
        session.commit()

        summary_onboarding = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_onboarding_result()),
            uid=31,
            parsed=_make_parsed(31),
            summary=summary_onboarding,
        )
        session.commit()

        apps = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps) == 1
        assert apps[0].status == "Onboarding"
        assert apps[0].job_title == "Senior Backend Engineer"
        assert summary_offer.applications_created == 1
        assert summary_onboarding.applications_created == 0
        assert summary_onboarding.applications_updated == 1
    finally:
        session.close()


def test_application_followed_by_oa_reuses_group_and_advances_status() -> None:
    session = _new_session()
    try:
        summary_applied = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_result()),
            uid=40,
            parsed=_make_parsed(40),
            summary=summary_applied,
        )
        session.commit()

        summary_oa = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_oa_result()),
            uid=41,
            parsed=_make_parsed(41),
            summary=summary_oa,
        )
        session.commit()

        apps = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps) == 1
        assert apps[0].status == "OA"
        assert apps[0].job_title == "Senior Backend Engineer"
        assert summary_applied.applications_created == 1
        assert summary_oa.applications_created == 0
        assert summary_oa.applications_updated == 1
    finally:
        session.close()


def test_rule_status_detects_onboarding_and_prioritizes_it_over_offer() -> None:
    status = extract_status(
        subject="Offer accepted - next steps",
        body=(
            "Please complete your background check and I-9. "
            "You can also finish benefits enrollment in the onboarding portal."
        ),
    )
    assert status == "Onboarding"


def test_rule_status_detects_oa_online_assessment() -> None:
    status = extract_status(
        subject="Next step: Online Assessemnt",
        body="Please complete the coding challenge in CodeSignal.",
    )
    assert status == "OA"
