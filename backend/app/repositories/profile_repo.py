"""Database access for client profiles."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ClientProfile
from app.services.applicability import parse_gstin

PROFILE_FIELDS = (
    "gstin",
    "pan",
    "state_code",
    "gst_registered",
    "special_category",
    "entity_type",
    "nature",
    "supplies",
    "presumptive",
    "cash_within_5pct",
)


def get_profile(db: Session, client_id: int) -> ClientProfile | None:
    return db.get(ClientProfile, client_id)


def save_profile(db: Session, client_id: int, values: dict) -> ClientProfile:
    profile = get_profile(db, client_id) or ClientProfile(client_id=client_id)
    db.add(profile)
    for name in PROFILE_FIELDS:
        if name in values:
            setattr(profile, name, values[name])
    db.flush()
    return profile


def fill_from_gstin(db: Session, client_id: int, gstin: str) -> bool:
    """Fill the GSTIN-derived fields that are still empty (never overwrites). True if changed."""
    facts = parse_gstin(gstin)
    if facts is None:
        return False
    profile = get_profile(db, client_id) or ClientProfile(client_id=client_id)
    db.add(profile)
    derived = {
        "gstin": facts.gstin,
        "pan": facts.gstin[2:12],
        "state_code": facts.state_code,
        "gst_registered": True,
        "special_category": facts.special_category,
        "entity_type": facts.entity_type,
    }
    changed = False
    for name, value in derived.items():
        if getattr(profile, name) is None and value is not None:
            setattr(profile, name, value)
            changed = True
    db.flush()
    return changed
