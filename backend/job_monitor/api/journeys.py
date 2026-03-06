"""Journey management endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from job_monitor.auth.deps import get_current_user, get_owner_scoped_db
from job_monitor.models import Journey, User
from job_monitor.schemas import JourneyCreate, JourneyOut, JourneyUpdate

router = APIRouter(prefix="/api/journeys", tags=["journeys"])


def _default_journey_name() -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    return f"Journey {today}"


def _to_journey_out(journey: Journey, active_journey_id: int | None) -> JourneyOut:
    return JourneyOut(
        id=journey.id,
        name=journey.name,
        owner_user_id=journey.owner_user_id,
        created_at=journey.created_at,
        updated_at=journey.updated_at,
        is_active=(journey.id == active_journey_id),
    )


@router.get("", response_model=list[JourneyOut])
def list_journeys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_owner_scoped_db),
) -> list[JourneyOut]:
    journeys = db.query(Journey).order_by(Journey.created_at.asc(), Journey.id.asc()).all()
    return [_to_journey_out(j, current_user.active_journey_id) for j in journeys]


@router.post("", response_model=JourneyOut, status_code=201)
def create_journey(
    body: JourneyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_owner_scoped_db),
) -> JourneyOut:
    name = (body.name or "").strip() or _default_journey_name()
    journey = Journey(owner_user_id=current_user.id, name=name)
    db.add(journey)
    db.flush()

    current_user.active_journey_id = journey.id
    db.info["journey_id"] = journey.id
    db.commit()
    db.refresh(journey)
    db.refresh(current_user)
    return _to_journey_out(journey, current_user.active_journey_id)


@router.post("/{journey_id}/activate", response_model=JourneyOut)
def activate_journey(
    journey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_owner_scoped_db),
) -> JourneyOut:
    journey = db.query(Journey).filter(Journey.id == journey_id).first()
    if journey is None:
        raise HTTPException(status_code=404, detail="Journey not found")

    current_user.active_journey_id = journey.id
    db.info["journey_id"] = journey.id
    db.commit()
    db.refresh(current_user)
    return _to_journey_out(journey, current_user.active_journey_id)


@router.patch("/{journey_id}", response_model=JourneyOut)
def rename_journey(
    journey_id: int,
    body: JourneyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_owner_scoped_db),
) -> JourneyOut:
    journey = db.query(Journey).filter(Journey.id == journey_id).first()
    if journey is None:
        raise HTTPException(status_code=404, detail="Journey not found")

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Journey name cannot be empty")

    journey.name = name
    db.commit()
    db.refresh(journey)
    return _to_journey_out(journey, current_user.active_journey_id)
