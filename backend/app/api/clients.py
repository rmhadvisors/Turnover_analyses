from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_client_or_404
from app.database import get_db
from app.models import Client
from app.repositories import client_repo, profile_repo
from app.schemas.client import ClientIn, ClientRead
from app.schemas.profile import ProfileIn, ProfileRead
from app.services.applicability import STATES, parse_gstin
from app.services.recheck_service import evaluate_client, fys_with_data

router = APIRouter(prefix="/clients", tags=["clients"])


def _ensure_name_free(db: Session, name: str, own_id: int | None = None) -> None:
    existing = client_repo.get_by_name(db, name)
    if existing is not None and existing.id != own_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "A client with this name already exists")


@router.get("", response_model=list[ClientRead])
def list_clients(db: Session = Depends(get_db)):
    return client_repo.list_clients(db)


@router.post("", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
def create_client(body: ClientIn, db: Session = Depends(get_db)):
    _ensure_name_free(db, body.name)
    return client_repo.create_client(db, body.name)


@router.get("/{client_id}", response_model=ClientRead)
def read_client(client: Client = Depends(get_client_or_404)):
    return client


@router.put("/{client_id}", response_model=ClientRead)
def rename_client(
    body: ClientIn,
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    _ensure_name_free(db, body.name, client.id)
    return client_repo.rename_client(db, client, body.name)


_CHOICES = ("gst_registered", "special_category", "entity_type", "nature", "supplies",
            "presumptive", "cash_within_5pct")  # fmt: skip


def _profile_read(client_id: int, profile) -> ProfileRead:
    values = {name: getattr(profile, name, None) for name in ("gstin", "pan", "state_code", *_CHOICES)}
    facts = parse_gstin(values["gstin"])
    return ProfileRead(
        client_id=client_id,
        **values,
        state_name=STATES.get(values["state_code"] or ""),
        entity_hint=facts.hint if facts and values["entity_type"] is None else None,
        unknown_fields=[name for name in _CHOICES if values[name] is None],
    )


@router.get("/{client_id}/profile", response_model=ProfileRead)
def read_profile(client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    return _profile_read(client.id, profile_repo.get_profile(db, client.id))


@router.put("/{client_id}/profile", response_model=ProfileRead)
def save_profile(
    body: ProfileIn, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    """Save the profile, then re-check every year: limits that no longer apply have their
    open alerts acknowledged by the system; limits that now apply are evaluated."""
    values = body.model_dump()
    facts = parse_gstin(body.gstin)
    values["state_code"] = facts.state_code if facts else None
    values["pan"] = body.pan or (facts.gstin[2:12] if facts else None)
    profile = profile_repo.save_profile(db, client.id, values)
    evaluate_client(db, client.id, fys_with_data(db, client.id))
    db.commit()
    return _profile_read(client.id, profile)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    client_repo.delete_client(db, client)
