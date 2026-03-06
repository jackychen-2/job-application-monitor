"""Tests for journey-scoped data behavior."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_monitor.database import _ensure_default_journeys_and_backfill
from job_monitor.dedupe import merge_owner_duplicate_applications
from job_monitor.models import Application, Base, Journey, User


def _new_session() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_backfill_creates_default_journey_and_is_idempotent() -> None:
    session = _new_session()
    try:
        user = User(email="candidate@example.com", is_active=True)
        session.add(user)
        session.flush()

        app = Application(
            owner_user_id=user.id,
            journey_id=None,
            company="Meta",
            normalized_company="meta",
            job_title="SWE",
            req_id="R1",
            status="已申请",
            source="manual",
        )
        session.add(app)
        session.commit()

        existing_tables = {"applications", "processed_emails", "status_history", "scan_state"}
        _ensure_default_journeys_and_backfill(session, existing_tables)
        session.commit()

        journeys = session.query(Journey).filter(Journey.owner_user_id == user.id).all()
        assert len(journeys) == 1
        session.refresh(user)
        session.refresh(app)
        assert user.active_journey_id == journeys[0].id
        assert app.journey_id == journeys[0].id

        _ensure_default_journeys_and_backfill(session, existing_tables)
        session.commit()
        assert session.query(Journey).filter(Journey.owner_user_id == user.id).count() == 1
    finally:
        session.close()


def test_same_application_key_allowed_across_journeys() -> None:
    session = _new_session()
    try:
        user = User(email="candidate@example.com", is_active=True)
        session.add(user)
        session.flush()

        j1 = Journey(owner_user_id=user.id, name="Journey A")
        j2 = Journey(owner_user_id=user.id, name="Journey B")
        session.add_all([j1, j2])
        session.flush()

        session.add_all(
            [
                Application(
                    owner_user_id=user.id,
                    journey_id=j1.id,
                    company="Meta",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="R1",
                    status="已申请",
                    source="manual",
                ),
                Application(
                    owner_user_id=user.id,
                    journey_id=j2.id,
                    company="Meta",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="R1",
                    status="已申请",
                    source="manual",
                ),
            ]
        )
        session.commit()

        assert (
            session.query(Application)
            .filter(
                Application.owner_user_id == user.id,
                Application.company == "Meta",
                Application.job_title == "SWE",
                Application.req_id == "R1",
            )
            .count()
            == 2
        )
    finally:
        session.close()


def test_dedupe_only_within_selected_journey() -> None:
    session = _new_session()
    try:
        user = User(email="candidate@example.com", is_active=True)
        session.add(user)
        session.flush()

        j1 = Journey(owner_user_id=user.id, name="Journey A")
        j2 = Journey(owner_user_id=user.id, name="Journey B")
        session.add_all([j1, j2])
        session.flush()

        session.add_all(
            [
                Application(
                    owner_user_id=user.id,
                    journey_id=j1.id,
                    company="Meta",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="R1",
                    status="已申请",
                    source="manual",
                ),
                Application(
                    owner_user_id=user.id,
                    journey_id=j1.id,
                    company="Meta\u200b",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="R1",
                    status="OA",
                    source="manual",
                ),
                Application(
                    owner_user_id=user.id,
                    journey_id=j2.id,
                    company="Meta",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="R1",
                    status="已申请",
                    source="manual",
                ),
            ]
        )
        session.commit()

        merged = merge_owner_duplicate_applications(session, user.id, journey_id=j1.id)
        session.commit()

        assert merged == 1
        assert session.query(Application).filter(Application.owner_user_id == user.id, Application.journey_id == j1.id).count() == 1
        assert session.query(Application).filter(Application.owner_user_id == user.id, Application.journey_id == j2.id).count() == 1
    finally:
        session.close()


def test_session_scope_switches_with_journey_context() -> None:
    session = _new_session()
    try:
        user = User(email="candidate@example.com", is_active=True)
        session.add(user)
        session.flush()

        j1 = Journey(owner_user_id=user.id, name="Journey A")
        j2 = Journey(owner_user_id=user.id, name="Journey B")
        session.add_all([j1, j2])
        session.flush()

        session.add_all(
            [
                Application(
                    owner_user_id=user.id,
                    journey_id=j1.id,
                    company="A",
                    normalized_company="a",
                    job_title="SWE",
                    req_id="R1",
                    status="已申请",
                    source="manual",
                ),
                Application(
                    owner_user_id=user.id,
                    journey_id=j2.id,
                    company="B",
                    normalized_company="b",
                    job_title="SWE",
                    req_id="R2",
                    status="已申请",
                    source="manual",
                ),
            ]
        )
        session.commit()

        session.info["owner_user_id"] = user.id
        session.info["journey_id"] = j1.id
        apps_j1 = session.query(Application).all()
        assert [a.company for a in apps_j1] == ["A"]

        session.info["journey_id"] = j2.id
        session.expire_all()
        apps_j2 = session.query(Application).all()
        assert [a.company for a in apps_j2] == ["B"]
    finally:
        session.close()


def test_dedupe_skips_locked_applications() -> None:
    session = _new_session()
    try:
        user = User(email="candidate@example.com", is_active=True)
        session.add(user)
        session.flush()

        journey = Journey(owner_user_id=user.id, name="Journey A")
        session.add(journey)
        session.flush()

        session.add_all(
            [
                Application(
                    owner_user_id=user.id,
                    journey_id=journey.id,
                    company="Meta",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="R1",
                    status="已申请",
                    source="manual",
                    dedupe_locked=True,
                ),
                Application(
                    owner_user_id=user.id,
                    journey_id=journey.id,
                    company="Meta\u200b",
                    normalized_company="meta",
                    job_title="SWE",
                    req_id="R1",
                    status="OA",
                    source="manual",
                ),
            ]
        )
        session.commit()

        merged = merge_owner_duplicate_applications(session, user.id, journey_id=journey.id)
        session.commit()

        assert merged == 0
        assert (
            session.query(Application)
            .filter(
                Application.owner_user_id == user.id,
                Application.journey_id == journey.id,
            )
            .count()
            == 2
        )
    finally:
        session.close()
