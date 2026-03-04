"""Pipeline tests for recruiter outreach status handling."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.config import AppConfig
from job_monitor.email.parser import ParsedEmailData
from job_monitor.extraction.llm import LLMExtractionResult, LLMLinkConfirmResult
from job_monitor.extraction.pipeline import ScanSummary, _process_single_email
from job_monitor.extraction.rules import extract_status, split_title_and_req_id
from job_monitor.models import Application, Base, ProcessedEmail


class _StubLLMProvider:
    def __init__(self, result: LLMExtractionResult) -> None:
        self._result = result

    def extract_fields(self, sender: str, subject: str, body: str) -> LLMExtractionResult:
        return self._result


class _StubLLMProviderNoConfirm(_StubLLMProvider):
    def confirm_same_application(self, *args, **kwargs):  # pragma: no cover - guard rail
        raise AssertionError("confirm_same_application should not be called")


class _StubLLMProviderWithConfirm(_StubLLMProvider):
    def __init__(self, result: LLMExtractionResult, *, is_same_application: bool) -> None:
        super().__init__(result)
        self._is_same_application = is_same_application
        self.confirm_calls = 0

    def confirm_same_application(self, *args, **kwargs) -> LLMLinkConfirmResult:
        self.confirm_calls += 1
        return LLMLinkConfirmResult(is_same_application=self._is_same_application)


class _StubLLMProviderSelectiveConfirm(_StubLLMProvider):
    def __init__(
        self,
        result: LLMExtractionResult,
        *,
        same_title: str | None = None,
        same_req_id: str | None = None,
        same_company: str | None = None,
    ) -> None:
        super().__init__(result)
        self._same_title = same_title
        self._same_req_id = same_req_id
        self._same_company = same_company
        self.confirm_calls = 0
        self.seen_titles: list[str] = []
        self.seen_req_ids: list[str] = []
        self.seen_companies: list[str] = []

    def confirm_same_application(self, *args, **kwargs) -> LLMLinkConfirmResult:
        self.confirm_calls += 1
        app_title = kwargs.get("app_job_title", "") or ""
        req_id = kwargs.get("candidate_req_id", "") or ""
        app_company = kwargs.get("app_company", "") or ""
        self.seen_titles.append(app_title)
        self.seen_req_ids.append(req_id)
        self.seen_companies.append(app_company)

        if (
            self._same_title is None
            and self._same_req_id is None
            and self._same_company is None
        ):
            return LLMLinkConfirmResult(is_same_application=False)

        is_same = True
        if self._same_title is not None:
            is_same = is_same and (app_title == self._same_title)
        if self._same_req_id is not None:
            is_same = is_same and (req_id == self._same_req_id)
        if self._same_company is not None:
            is_same = is_same and (app_company == self._same_company)
        return LLMLinkConfirmResult(is_same_application=is_same)


def _make_config() -> AppConfig:
    return AppConfig(
        imap_host="imap.example.com",
        email_username="candidate@example.com",
        email_password="secret",
        llm_enabled=True,
        llm_timeout_sec=3,
    )


def _make_parsed(
    uid: int,
    *,
    message_id: str | None = None,
    gmail_thread_id: str | None = None,
) -> ParsedEmailData:
    return ParsedEmailData(
        subject=f"Opportunity #{uid}",
        sender="recruiter@example.com",
        date_raw="Fri, 27 Feb 2026 10:00:00 +0000",
        date_pt="2026-02-27 02:00:00 PST",
        date_dt=datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc),
        body_text="We'd like to discuss a Senior Backend Engineer role.",
        message_id=message_id or f"msg-{uid}@example.com",
        gmail_thread_id=gmail_thread_id or f"thread-{uid}",
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


def _make_job_application_with_req_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Senior Backend Engineer - R0615432",
        base_title="Senior Backend Engineer",
        req_id="R0615432",
        title_with_req_id="Senior Backend Engineer - R0615432",
        status="已申请",
        confidence=0.98,
    )


def _make_job_application_with_parenthesized_req_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Casey's",
        job_title="Data Integration Developer (2025-4844)",
        base_title="",
        req_id="",
        title_with_req_id="Data Integration Developer (2025-4844)",
        status="已申请",
        confidence=0.98,
    )


def _make_follow_up_with_same_req_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Senior Backend Engineer - R0615432",
        base_title="Senior Backend Engineer",
        req_id="R0615432",
        title_with_req_id="Senior Backend Engineer - R0615432",
        status="OA",
        confidence=0.98,
    )


def _make_follow_up_with_same_req_different_title_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Machine Learning Engineer - R0615432",
        base_title="Machine Learning Engineer",
        req_id="R0615432",
        title_with_req_id="Machine Learning Engineer - R0615432",
        status="OA",
        confidence=0.98,
    )


def _make_rejected_different_title_no_req_result() -> LLMExtractionResult:
    return LLMExtractionResult(
        is_job_application=True,
        email_category="job_application",
        company="Meta",
        job_title="Senior Backend Engineer | USA | Remote",
        base_title="Senior Backend Engineer | USA | Remote",
        req_id="",
        title_with_req_id="Senior Backend Engineer | USA | Remote",
        status="拒绝",
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
        assert summary_b.emails_matched == 1
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
        assert summary_a.emails_matched == 1
        assert summary_b.emails_matched == 1
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


def test_job_title_and_req_id_are_stored_separately() -> None:
    session = _new_session()
    try:
        summary = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_with_req_result()),
            uid=50,
            parsed=_make_parsed(50),
            summary=summary,
        )
        session.commit()

        app = session.query(Application).one()
        assert app.job_title == "Senior Backend Engineer"
        assert app.req_id == "R0615432"
        assert summary.applications_created == 1
    finally:
        session.close()


def test_split_title_and_req_id_handles_parenthesized_hyphen_id() -> None:
    base_title, req_id = split_title_and_req_id("Data Integration Developer (2025-4844)")
    assert base_title == "Data Integration Developer"
    assert req_id == "2025-4844"


def test_parenthesized_hyphen_req_id_is_stored_separately() -> None:
    session = _new_session()
    try:
        summary = ScanSummary()
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_with_parenthesized_req_result()),
            uid=51,
            parsed=_make_parsed(51),
            summary=summary,
        )
        session.commit()

        app = session.query(Application).one()
        assert app.job_title == "Data Integration Developer"
        assert app.req_id == "2025-4844"
        assert summary.applications_created == 1
    finally:
        session.close()


def test_exact_req_id_links_without_llm_confirmation() -> None:
    session = _new_session()
    try:
        # Seed the application with the same req_id.
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_with_req_result()),
            uid=60,
            parsed=_make_parsed(60),
            summary=ScanSummary(),
        )
        session.commit()

        # Follow-up email should link by req_id directly; confirm_same_application must not run.
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProviderNoConfirm(_make_follow_up_with_same_req_result()),
            uid=61,
            parsed=_make_parsed(61),
            summary=ScanSummary(),
        )
        session.commit()

        apps = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps) == 1
        assert apps[0].req_id == "R0615432"
        assert apps[0].status == "OA"

        second = session.query(ProcessedEmail).filter(ProcessedEmail.uid == 61).one()
        assert second.link_method == "company_req_id"
    finally:
        session.close()


def test_req_id_match_but_title_mismatch_direct_links_without_llm() -> None:
    session = _new_session()
    try:
        # Seed with req_id + title.
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_with_req_result()),
            uid=62,
            parsed=_make_parsed(62),
            summary=ScanSummary(),
        )
        session.commit()

        # Same req_id but different title: should still direct-link when req_id uniquely matches.
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProviderNoConfirm(_make_follow_up_with_same_req_different_title_result()),
            uid=63,
            parsed=_make_parsed(63),
            summary=ScanSummary(),
        )
        session.commit()

        apps = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps) == 1
        assert apps[0].status == "OA"

        second = session.query(ProcessedEmail).filter(ProcessedEmail.uid == 63).one()
        assert second.link_method == "company_req_id"
    finally:
        session.close()


def test_req_id_multi_match_defers_to_llm_confirmation() -> None:
    session = _new_session()
    try:
        session.add_all(
            [
                Application(
                    company="Meta",
                    normalized_company="meta",
                    job_title="Senior Backend Engineer",
                    req_id="R0615432",
                    status="已申请",
                    source="email",
                ),
                Application(
                    company="Meta",
                    normalized_company="meta",
                    job_title="Machine Learning Engineer",
                    req_id="R0615432",
                    status="已申请",
                    source="email",
                ),
            ]
        )
        session.commit()

        provider = _StubLLMProviderWithConfirm(
            _make_follow_up_with_same_req_different_title_result(),
            is_same_application=True,
        )
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=provider,
            uid=64,
            parsed=_make_parsed(64),
            summary=ScanSummary(),
        )
        session.commit()

        linked = session.query(ProcessedEmail).filter(ProcessedEmail.uid == 64).one()
        assert linked.link_method == "company"
        assert provider.confirm_calls > 0
    finally:
        session.close()


def test_same_message_id_overwrites_existing_processed_email_row() -> None:
    session = _new_session()
    try:
        msg_id = "same-message@example.com"
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_with_req_result()),
            uid=70,
            parsed=_make_parsed(70, message_id=msg_id),
            summary=ScanSummary(),
        )
        session.commit()

        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_with_req_result()),
            uid=71,
            parsed=_make_parsed(71, message_id=msg_id),
            summary=ScanSummary(),
        )
        session.commit()

        emails = session.query(ProcessedEmail).all()
        assert len(emails) == 1
        assert emails[0].uid == 71
        assert emails[0].gmail_message_id == msg_id
        assert emails[0].link_method == "company_req_id"
    finally:
        session.close()


def test_same_message_id_relink_cleans_up_old_orphan_application() -> None:
    session = _new_session()
    try:
        msg_id = "relink-message@example.com"
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(_make_job_application_result()),
            uid=80,
            parsed=_make_parsed(80, message_id=msg_id),
            summary=ScanSummary(),
        )
        session.commit()

        relink_result = LLMExtractionResult(
            is_job_application=True,
            email_category="job_application",
            company="Stripe",
            job_title="Software Engineer",
            base_title="Software Engineer",
            req_id="",
            title_with_req_id="Software Engineer",
            status="已申请",
            confidence=0.98,
        )
        _process_single_email(
            session=session,
            config=_make_config(),
            llm_provider=_StubLLMProvider(relink_result),
            uid=81,
            parsed=_make_parsed(81, message_id=msg_id),
            summary=ScanSummary(),
        )
        session.commit()

        emails = session.query(ProcessedEmail).all()
        apps = session.query(Application).all()
        assert len(emails) == 1
        assert len(apps) == 1
        assert apps[0].company == "Stripe"
        assert emails[0].application_id == apps[0].id
    finally:
        session.close()


def test_rescan_same_message_excludes_self_candidate_from_llm_confirm() -> None:
    session = _new_session()
    try:
        config = _make_config()
        msg_id = "group-b@example.com"

        target_app = Application(
            company="Meta",
            normalized_company="meta",
            job_title="Senior Backend Engineer | USA | Remote",
            req_id="R0615432",
            status="已申请",
            source="email",
        )
        self_app = Application(
            company="Meta",
            normalized_company="meta",
            job_title="Senior Backend Engineer | USA | Remote",
            req_id="",
            status="拒绝",
            source="email",
        )
        session.add_all([target_app, self_app])
        session.flush()

        session.add(
            ProcessedEmail(
                uid=91,
                email_account=config.email_username,
                email_folder=config.email_folder,
                gmail_message_id=msg_id,
                gmail_thread_id="thread-91",
                subject="Your application for Meta",
                sender="no-reply@meta.com",
                email_date=datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc),
                is_job_related=True,
                application_id=self_app.id,
                llm_used=True,
                link_method="new",
                needs_review=False,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
            )
        )
        session.commit()

        apps_before = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps_before) == 2

        provider = _StubLLMProviderSelectiveConfirm(
            _make_rejected_different_title_no_req_result(),
            same_req_id="R0615432",
        )
        _process_single_email(
            session=session,
            config=config,
            llm_provider=provider,
            uid=92,
            parsed=_make_parsed(92, message_id=msg_id),
            summary=ScanSummary(),
        )
        session.commit()

        updated_email = (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.gmail_message_id == msg_id)
            .one()
        )
        assert updated_email.application_id == target_app.id
        assert provider.confirm_calls == 1
        assert "" not in provider.seen_req_ids

        apps_after = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps_after) == 1
        assert apps_after[0].id == target_app.id
    finally:
        session.close()


def test_rescan_relink_keeps_target_key_fields_to_avoid_unique_conflict() -> None:
    session = _new_session()
    try:
        config = _make_config()
        msg_id = "conflict-relink@example.com"

        target_app = Application(
            company="Meta Labs",
            normalized_company="meta",
            job_title="Senior Backend Engineer",
            req_id="",
            status="已申请",
            source="email",
        )
        self_app = Application(
            company="Meta",
            normalized_company="meta",
            job_title="Senior Backend Engineer | USA | Remote",
            req_id="",
            status="拒绝",
            source="email",
        )
        session.add_all([target_app, self_app])
        session.flush()

        session.add(
            ProcessedEmail(
                uid=93,
                email_account=config.email_username,
                email_folder=config.email_folder,
                gmail_message_id=msg_id,
                gmail_thread_id="thread-93",
                subject="Your application for Meta",
                sender="no-reply@meta.com",
                email_date=datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc),
                is_job_related=True,
                application_id=self_app.id,
                llm_used=True,
                link_method="new",
                needs_review=False,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=0.0,
            )
        )
        session.commit()

        provider = _StubLLMProviderSelectiveConfirm(
            _make_rejected_different_title_no_req_result(),
            same_company="Meta Labs",
        )
        _process_single_email(
            session=session,
            config=config,
            llm_provider=provider,
            uid=94,
            parsed=_make_parsed(94, message_id=msg_id),
            summary=ScanSummary(),
        )
        session.commit()

        email_row = (
            session.query(ProcessedEmail)
            .filter(ProcessedEmail.gmail_message_id == msg_id)
            .one()
        )
        assert email_row.application_id == target_app.id

        apps = session.query(Application).order_by(Application.id.asc()).all()
        assert len(apps) == 1
        assert apps[0].id == target_app.id
        assert apps[0].company == "Meta Labs"
        assert apps[0].job_title == "Senior Backend Engineer"
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
