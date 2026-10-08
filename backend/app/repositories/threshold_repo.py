"""Database access for band settings and absolute limits."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AbsoluteLimit, ThresholdSetting
from app.repositories.seed_data import DEFAULT_LIMITS
from app.services.applicability import DEFAULT_RULES


def get_settings(db: Session) -> ThresholdSetting:
    """The single settings row, created with defaults on first use."""
    settings = db.get(ThresholdSetting, 1)
    if settings is None:
        settings = ThresholdSetting(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def list_limits(db: Session, enabled_only: bool = False) -> list[AbsoluteLimit]:
    query = select(AbsoluteLimit).order_by(AbsoluteLimit.id)
    if enabled_only:
        query = query.where(AbsoluteLimit.is_enabled.is_(True))
    return list(db.scalars(query))


def get_limit(db: Session, limit_id: int) -> AbsoluteLimit | None:
    return db.get(AbsoluteLimit, limit_id)


def seed_defaults(db: Session) -> None:
    """Create the settings row and, on an empty table, the default limits."""
    get_settings(db)
    if db.scalar(select(AbsoluteLimit.id).limit(1)) is None:
        db.add_all(
            AbsoluteLimit(is_default_seed=True, applies_when=DEFAULT_RULES.get(row["name"]), **row)
            for row in DEFAULT_LIMITS
        )
        db.commit()
