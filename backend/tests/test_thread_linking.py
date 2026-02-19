"""Tests for Gmail thread-based email linking.

Demonstrates:
1. Emails with same gmail_thread_id are linked to the same application_id
2. Duplicate gmail_message_id is skipped (idempotency)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.linking.resolver import (
    is_message_already_processed,
    resolve_by_thread_id,
    ThreadLinkResult,
    THREAD_LINK_CONFIDENCE,
)
from job_monitor.models import Application, Base, ProcessedEmail


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


class TestThreadLinking:
    """Test thread-based linking of emails to applications."""

    def test_resolve_by_thread_id_no_match(self, db_session: Session):
        """When no previous email with thread_id exists, return None."""
        result = resolve_by_thread_id(db_session, "thread_123")
        
        assert result.application_id is None
        assert result.confidence == 0.0
        assert result.linked_via == "new"
        assert not result.is_linked

    def test_resolve_by_thread_id_with_match(self, db_session: Session):
        """When previous email with same thread_id exists, return its application_id."""
        # Create an application
        app = Application(
            company="Google",
            job_title="Software Engineer",
            status="已申请",
        )
        db_session.add(app)
        db_session.flush()

        # Create a processed email linked to that application
        email1 = ProcessedEmail(
            uid=1001,
            email_account="test@example.com",
            email_folder="INBOX",
            gmail_message_id="msg_001@google.com",
            gmail_thread_id="thread_abc123",
            subject="Application received",
            sender="noreply@google.com",
            is_job_related=True,
            application_id=app.id,
        )
        db_session.add(email1)
        db_session.commit()

        # Now resolve with same thread_id
        result = resolve_by_thread_id(db_session, "thread_abc123")
        
        assert result.application_id == app.id
        assert result.confidence == THREAD_LINK_CONFIDENCE
        assert result.linked_via == "thread_id"
        assert result.is_linked

    def test_resolve_by_thread_id_ignores_non_job_emails(self, db_session: Session):
        """Thread linking should ignore emails that weren't job-related."""
        # Create a processed email NOT linked to any application
        email1 = ProcessedEmail(
            uid=1001,
            email_account="test@example.com",
            email_folder="INBOX",
            gmail_message_id="msg_001@google.com",
            gmail_thread_id="thread_newsletter",
            subject="Newsletter",
            sender="newsletter@company.com",
            is_job_related=False,
            application_id=None,
        )
        db_session.add(email1)
        db_session.commit()

        # Should not match because is_job_related=False
        result = resolve_by_thread_id(db_session, "thread_newsletter")
        
        assert result.application_id is None
        assert result.linked_via == "new"

    def test_resolve_by_thread_id_none_thread(self, db_session: Session):
        """When gmail_thread_id is None, return 'unknown' result."""
        result = resolve_by_thread_id(db_session, None)
        
        assert result.application_id is None
        assert result.linked_via == "unknown"

    def test_two_emails_same_thread_linked_to_same_application(self, db_session: Session):
        """
        SCENARIO: Email A and Email B share the same gmail_thread_id.
        
        Email A creates application #1.
        Email B should be linked to application #1 (not create a new one).
        """
        THREAD_ID = "thread_google_swe_2024"
        
        # Create application from Email A
        app = Application(
            company="Google",
            job_title="Software Engineer",
            status="已申请",
        )
        db_session.add(app)
        db_session.flush()

        # Record Email A as processed
        email_a = ProcessedEmail(
            uid=2001,
            email_account="candidate@gmail.com",
            email_folder="INBOX",
            gmail_message_id="email_a@google.com",
            gmail_thread_id=THREAD_ID,
            subject="Thanks for applying to Google",
            sender="jobs@google.com",
            is_job_related=True,
            application_id=app.id,
        )
        db_session.add(email_a)
        db_session.commit()

        # Email B arrives (interview invite, same thread)
        # Before processing Email B, we check thread linking
        link_result = resolve_by_thread_id(db_session, THREAD_ID)
        
        # Should link to same application
        assert link_result.is_linked
        assert link_result.application_id == app.id
        assert link_result.confidence == 0.95
        
        # Record Email B with same application_id
        email_b = ProcessedEmail(
            uid=2002,
            email_account="candidate@gmail.com",
            email_folder="INBOX",
            gmail_message_id="email_b@google.com",
            gmail_thread_id=THREAD_ID,
            subject="Interview invitation - Google",
            sender="recruiter@google.com",
            is_job_related=True,
            application_id=link_result.application_id,  # Same as app.id
        )
        db_session.add(email_b)
        db_session.commit()

        # Verify both emails are linked to the same application
        emails_in_thread = (
            db_session.query(ProcessedEmail)
            .filter(ProcessedEmail.gmail_thread_id == THREAD_ID)
            .all()
        )
        assert len(emails_in_thread) == 2
        assert all(e.application_id == app.id for e in emails_in_thread)


class TestDuplicateMessageIdempotency:
    """Test that duplicate gmail_message_id is skipped."""

    def test_is_message_already_processed_not_found(self, db_session: Session):
        """When message_id doesn't exist, return False."""
        result = is_message_already_processed(db_session, "new_message@example.com")
        assert result is False

    def test_is_message_already_processed_found(self, db_session: Session):
        """When message_id already exists, return True (skip processing)."""
        # Add an existing processed email
        email = ProcessedEmail(
            uid=3001,
            email_account="test@example.com",
            email_folder="INBOX",
            gmail_message_id="existing_msg@company.com",
            gmail_thread_id="thread_xyz",
            subject="Test",
            sender="sender@company.com",
            is_job_related=True,
        )
        db_session.add(email)
        db_session.commit()

        # Check idempotency
        result = is_message_already_processed(db_session, "existing_msg@company.com")
        assert result is True

    def test_is_message_already_processed_none(self, db_session: Session):
        """When message_id is None, return False (can't check)."""
        result = is_message_already_processed(db_session, None)
        assert result is False

    def test_duplicate_message_skipped_in_workflow(self, db_session: Session):
        """
        SCENARIO: Same email is fetched twice (e.g., re-scan).
        
        First processing: should proceed normally.
        Second processing: should be skipped due to duplicate gmail_message_id.
        """
        MESSAGE_ID = "unique_msg_12345@gmail.com"
        
        # First time: not processed yet
        assert is_message_already_processed(db_session, MESSAGE_ID) is False
        
        # Process and record the email
        email = ProcessedEmail(
            uid=4001,
            email_account="user@gmail.com",
            email_folder="INBOX",
            gmail_message_id=MESSAGE_ID,
            gmail_thread_id="thread_first",
            subject="Your application",
            sender="hr@company.com",
            is_job_related=True,
        )
        db_session.add(email)
        db_session.commit()
        
        # Second time: should be skipped
        assert is_message_already_processed(db_session, MESSAGE_ID) is True


class TestThreadLinkResult:
    """Test ThreadLinkResult dataclass."""

    def test_is_linked_true(self):
        result = ThreadLinkResult(application_id=123, confidence=0.95, linked_via="thread_id")
        assert result.is_linked is True

    def test_is_linked_false(self):
        result = ThreadLinkResult(application_id=None, confidence=0.0, linked_via="new")
        assert result.is_linked is False
