"""Tests for shared extraction core across pipeline and eval."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.config import AppConfig
from job_monitor.email.parser import ParsedEmailData
from job_monitor.eval.models import CachedEmail, EvalRunResult
from job_monitor.eval.runner import run_evaluation
from job_monitor.extraction.core import run_core_classification_and_extraction
from job_monitor.extraction.llm import LLMExtractionResult, LLMLinkConfirmResult
from job_monitor.extraction.pipeline import ScanSummary, _process_single_email, run_scan
from job_monitor.models import Application, Base, ProcessedEmail, ScanState


class _StubLLMProvider:
    def __init__(self, result: LLMExtractionResult) -> None:
        self._result = result
        self.extract_calls = 0

    def extract_fields(self, sender: str, subject: str, body: str) -> LLMExtractionResult:
        self.extract_calls += 1
        return self._result

    def confirm_same_application(self, *args, **kwargs) -> LLMLinkConfirmResult:
        return LLMLinkConfirmResult(is_same_application=False)


def _make_config(*, llm_enabled: bool) -> AppConfig:
    return AppConfig(
        imap_host="imap.example.com",
        email_username="candidate@example.com",
        email_password="secret",
        llm_enabled=llm_enabled,
        llm_timeout_sec=3,
    )


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _validate_title(title: str) -> str:
    from job_monitor.extraction.pipeline import _validate_job_title as _vt

    return _vt(title)


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
        confidence=0.95,
    )


def _make_job_application_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Acme",
        job_title="Data Engineer",
        base_title="Data Engineer",
        req_id="",
        title_with_req_id="Data Engineer",
        status="已申请",
        confidence=0.95,
    )


def test_core_llm_recruiter_outreach_is_trackable() -> None:
    prediction = run_core_classification_and_extraction(
        sender="recruiter@example.com",
        subject="Quick intro",
        body="I'd like to discuss a role.",
        llm_provider=_StubLLMProvider(_make_recruiter_result()),
        llm_timeout_sec=3,
        validate_job_title=_validate_title,
    )

    assert prediction.classification.is_trackable_job is True
    assert prediction.classification.predicted_email_category == "not_job_related"
    assert prediction.classification.non_job_reason is None
    assert prediction.extraction is not None
    assert prediction.extraction.status == "Recruiter Reach-out"


def test_core_linkedin_invite_hard_rule_skips_llm() -> None:
    provider = _StubLLMProvider(_make_job_application_result())
    prediction = run_core_classification_and_extraction(
        sender="Qinghuai Tan <invitations@linkedin.com>",
        subject="You have an invitation",
        body="I'd like to add you to my professional network on LinkedIn.",
        llm_provider=provider,
        llm_timeout_sec=3,
        validate_job_title=_validate_title,
    )

    assert provider.extract_calls == 0
    assert prediction.classification.is_trackable_job is False
    assert prediction.classification.predicted_email_category == "not_job_related"
    assert prediction.classification.non_job_reason == "social_invitation"
    assert prediction.classification.llm_used is False
    assert prediction.extraction is None


def test_core_zip_digest_hard_rule_sets_reason() -> None:
    provider = _StubLLMProvider(_make_job_application_result())
    prediction = run_core_classification_and_extraction(
        sender="alerts@ziprecruiter.com",
        subject="Jacky, I think this job might be right for you!",
        body="View all jobs recommended for you.",
        llm_provider=provider,
        llm_timeout_sec=3,
        validate_job_title=_validate_title,
    )

    assert provider.extract_calls == 0
    assert prediction.classification.is_trackable_job is False
    assert prediction.classification.predicted_email_category == "not_job_related"
    assert prediction.classification.non_job_reason == "job_recommendation_digest"
    assert prediction.extraction is None


def test_core_rule_fallback_extracts_fields() -> None:
    prediction = run_core_classification_and_extraction(
        sender="no-reply@acme.com",
        subject="Application for Data Engineer at Acme",
        body="Thank you for applying.",
        llm_provider=None,
        llm_timeout_sec=3,
        validate_job_title=_validate_title,
    )

    assert prediction.classification.is_trackable_job is True
    assert prediction.classification.predicted_email_category == "job_application"
    assert prediction.extraction is not None
    assert prediction.extraction.status == "已申请"
    assert prediction.extraction.company in {"Acme", "Unknown"}


def test_eval_runner_persists_non_job_reason_and_decision_log(monkeypatch) -> None:
    session = _new_session()
    try:
        session.add(
            CachedEmail(
                uid=1,
                email_account="candidate@example.com",
                email_folder="INBOX",
                gmail_message_id="cache-msg-1@example.com",
                gmail_thread_id="cache-thread-1",
                subject="Jacky, I think this job might be right for you!",
                sender="alerts@ziprecruiter.com",
                email_date=datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc),
                body_text="Recommended jobs for you.",
                raw_rfc822=b"",
            )
        )
        session.commit()

        monkeypatch.setattr(
            "job_monitor.eval.runner.create_llm_provider",
            lambda _cfg: _StubLLMProvider(_make_job_application_result()),
        )

        run = run_evaluation(_make_config(llm_enabled=True), session, run_name="shared-core-eval")
        result = (
            session.query(EvalRunResult)
            .filter(EvalRunResult.eval_run_id == run.id)
            .one()
        )

        assert result.predicted_is_job_related is False
        assert result.predicted_email_category == "not_job_related"
        assert result.predicted_non_job_reason == "job_recommendation_digest"
        assert result.predicted_status is None
        logs = json.loads(result.decision_log_json or "[]")
        assert any("non_job_reason='job_recommendation_digest'" in (item.get("message") or "") for item in logs)
    finally:
        session.close()


def test_pipeline_and_eval_share_non_job_classification(monkeypatch) -> None:
    session = _new_session()
    try:
        config = _make_config(llm_enabled=True)
        parsed = ParsedEmailData(
            subject="You have an invitation",
            sender="invitations@linkedin.com",
            date_raw="Fri, 27 Feb 2026 10:00:00 +0000",
            date_pt="2026-02-27 02:00:00 PST",
            date_dt=datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc),
            body_text="I'd like to add you to my professional network on LinkedIn.",
            message_id="prod-msg-1@example.com",
            gmail_thread_id="prod-thread-1",
        )

        _process_single_email(
            session=session,
            config=config,
            llm_provider=_StubLLMProvider(_make_job_application_result()),
            uid=1,
            parsed=parsed,
            summary=ScanSummary(),
        )
        session.commit()

        processed = session.query(ProcessedEmail).filter(ProcessedEmail.uid == 1).one()
        assert processed.is_job_related is False
        assert processed.application_id is None
        assert session.query(Application).count() == 0

        session.add(
            CachedEmail(
                uid=101,
                email_account="candidate@example.com",
                email_folder="INBOX",
                gmail_message_id="cache-msg-101@example.com",
                gmail_thread_id="cache-thread-101",
                subject=parsed.subject,
                sender=parsed.sender,
                email_date=parsed.date_dt,
                body_text=parsed.body_text,
                raw_rfc822=b"",
            )
        )
        session.commit()

        monkeypatch.setattr(
            "job_monitor.eval.runner.create_llm_provider",
            lambda _cfg: _StubLLMProvider(_make_job_application_result()),
        )

        run = run_evaluation(config, session, run_name="shared-core-consistency")
        eval_result = (
            session.query(EvalRunResult)
            .filter(EvalRunResult.eval_run_id == run.id)
            .one()
        )

        assert processed.is_job_related == eval_result.predicted_is_job_related
        assert eval_result.predicted_email_category == "not_job_related"
        assert eval_result.predicted_non_job_reason == "social_invitation"
        assert eval_result.predicted_status is None
        assert eval_result.predicted_application_group_id is None
    finally:
        session.close()


def test_run_scan_commits_each_email_before_later_sqlite_failure(monkeypatch) -> None:
    session = _new_session()

    class _FakeGmailClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def fetch_latest_message_ids(self, _count: int) -> tuple[list[str], int]:
            return ["msg-1", "msg-2"], 2

        def fetch_message(self, gmail_message_id: str):
            uid = 1 if gmail_message_id == "msg-1" else 2
            return uid, {"uid": uid}, f"thread-{uid}", None, uid

    def _fake_parse_email_message(msg: dict[str, int], *, gmail_thread_id: str | None = None) -> ParsedEmailData:
        uid = msg["uid"]
        return ParsedEmailData(
            subject=f"Opportunity #{uid}",
            sender="recruiter@example.com",
            date_raw="Fri, 27 Feb 2026 10:00:00 +0000",
            date_pt="2026-02-27 02:00:00 PST",
            date_dt=datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc),
            body_text="We'd like to discuss a role.",
            message_id=f"msg-{uid}@example.com",
            gmail_thread_id=gmail_thread_id or f"thread-{uid}",
        )

    def _fake_process_single_email(
        session: Session,
        config: AppConfig,
        llm_provider,
        owner_user_id: int,
        mailbox_email: str,
        mailbox_folder: str,
        uid: int,
        parsed: ParsedEmailData,
        summary: ScanSummary,
        gmail_message_id_override: str | None = None,
    ) -> None:
        del config, llm_provider, mailbox_email, mailbox_folder, parsed, gmail_message_id_override
        if uid == 2:
            raise OperationalError(
                "UPDATE applications",
                {"id": 2},
                sqlite3.OperationalError("database is locked"),
            )

        session.add(
            Application(
                owner_user_id=owner_user_id,
                company="Acme",
                normalized_company="acme",
                job_title="Data Engineer",
                status="已申请",
                source="email",
            )
        )
        summary.emails_matched += 1
        summary.applications_created += 1

    monkeypatch.setattr("job_monitor.extraction.pipeline.GmailClient", _FakeGmailClient)
    monkeypatch.setattr("job_monitor.extraction.pipeline.parse_email_message", _fake_parse_email_message)
    monkeypatch.setattr("job_monitor.extraction.pipeline._process_single_email", _fake_process_single_email)
    monkeypatch.setattr("job_monitor.extraction.pipeline.merge_owner_duplicate_applications", lambda *_args, **_kwargs: 0)

    try:
        summary = run_scan(
            _make_config(llm_enabled=False),
            session,
            owner_user_id=1,
            mailbox_email="candidate@example.com",
        )

        assert summary.applications_created == 1
        assert summary.emails_matched == 1
        assert len(summary.errors) == 1
        assert "database is locked" in summary.errors[0]
        assert session.query(Application).count() == 1

        assert session.query(ScanState).count() == 1
    finally:
        session.close()
