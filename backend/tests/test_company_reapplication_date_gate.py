"""Tests for re-application filtering date gate in company resolver."""

from __future__ import annotations

from datetime import datetime

from job_monitor.linking.resolver import CompanyLinkCandidate, resolve_by_company_candidates


class _ConfirmResult:
    def __init__(self, *, is_same_application: bool) -> None:
        self.is_same_application = is_same_application
        self.confidence = 0.95
        self.reason = "test"
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.estimated_cost_usd = 0.0


class _StubConfirmProvider:
    def __init__(self, *, is_same_application: bool = True) -> None:
        self._is_same_application = is_same_application
        self.calls = 0

    def confirm_same_application(self, **kwargs) -> _ConfirmResult:  # pragma: no cover - interface shim
        self.calls += 1
        return _ConfirmResult(is_same_application=self._is_same_application)


def _timeline(new_email_dt: datetime, app_last_dt: datetime) -> dict:
    return {
        "new_email_date": new_email_dt.isoformat(sep=" ", timespec="seconds"),
        "app_created_at": "",
        "app_last_email_date": app_last_dt.isoformat(sep=" ", timespec="seconds"),
        "days_since_last_email": abs((new_email_dt - app_last_dt).days),
        "recent_events": [],
    }


def _make_candidate() -> CompanyLinkCandidate:
    return CompanyLinkCandidate(
        id=142,
        company="Grafana Labs",
        normalized_company="grafana",
        job_title="Senior Data Engineer | USA | Remote",
        req_id="",
        status="拒绝",
        last_email_subject="Your application for Grafana Labs",
    )


def test_older_replay_does_not_trigger_reapplication_filter() -> None:
    candidate = _make_candidate()
    provider = _StubConfirmProvider(is_same_application=True)
    result = resolve_by_company_candidates(
        company="Grafana Labs",
        candidates=[candidate],
        extracted_status="已申请",
        job_title="Senior Data Engineer | USA | Remote",
        llm_provider=provider,
        email_subject="Thank you for applying to Grafana Labs",
        email_sender="no-reply@grafana.com",
        email_body="We have received your application.",
        timeline_provider=lambda _c: _timeline(
            datetime(2026, 2, 23, 21, 13, 7),
            datetime(2026, 2, 27, 19, 23, 59),
        ),
    )

    assert result.is_linked is True
    assert result.application_id == candidate.id
    # No re-application filter => regular company confirmation path.
    assert result.link_method == "company"
    assert provider.calls == 1


def test_newer_email_still_triggers_reapplication_filter() -> None:
    candidate = _make_candidate()
    provider = _StubConfirmProvider(is_same_application=True)
    result = resolve_by_company_candidates(
        company="Grafana Labs",
        candidates=[candidate],
        extracted_status="已申请",
        job_title="Senior Data Engineer | USA | Remote",
        llm_provider=provider,
        email_subject="Thank you for applying to Grafana Labs",
        email_sender="no-reply@grafana.com",
        email_body="We have received your application.",
        timeline_provider=lambda _c: _timeline(
            datetime(2026, 3, 1, 9, 0, 0),
            datetime(2026, 2, 27, 19, 23, 59),
        ),
    )

    assert result.is_linked is True
    assert result.application_id == candidate.id
    # Re-application filter removes exact candidate first, then fuzzy rescue confirms.
    assert result.link_method == "company_fuzzy"
    assert provider.calls == 1
