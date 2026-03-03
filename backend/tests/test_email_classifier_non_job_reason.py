"""Tests for non-job reason detection in keyword classifier."""

from __future__ import annotations

from job_monitor.email.classifier import detect_non_job_reason, is_job_related


def test_detect_linkedin_social_invitation_exact_rule() -> None:
    reason = detect_non_job_reason(
        sender="Qinghuai Tan <invitations@linkedin.com>",
        subject="You have an invitation",
        body="I'd like to add you to my professional network on LinkedIn.",
    )
    assert reason == "social_invitation"


def test_detect_ziprecruiter_share_exact_rule() -> None:
    reason = detect_non_job_reason(
        sender="alerts@ziprecruiter.com",
        subject="Jacky, I think this job might be right for you!",
        body="View all jobs.",
    )
    assert reason == "job_recommendation_digest"


def test_detect_general_job_digest_semantics() -> None:
    reason = detect_non_job_reason(
        sender="alerts@indeed.com",
        subject="Your Job Alert: 18 new jobs for you",
        body="Jobs matching your profile are waiting.",
    )
    assert reason == "job_recommendation_digest"


def test_real_application_not_marked_as_non_job_reason() -> None:
    reason = detect_non_job_reason(
        sender="no-reply@greenhouse.io",
        subject="Thank you for applying to Data Engineer at Acme",
        body="We received your application.",
    )
    assert reason is None
    assert is_job_related(
        subject="Thank you for applying to Data Engineer at Acme",
        sender="no-reply@greenhouse.io",
        body="We received your application.",
    ) is True


def test_recruiter_outreach_subject_not_misdetected_as_digest() -> None:
    reason = detect_non_job_reason(
        sender="Matt Barna <matt.barna@insyncstaffing.com>",
        subject="Manufacturing Specialist in Hayward",
        body="I have a role that might be a fit for your background.",
    )
    assert reason is None
