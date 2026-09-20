"""Database access for yearly figures."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import YearlyFigures

FIGURE_FIELDS = ("turnover", "purchases", "gross_profit", "net_profit")


def get_figures(db: Session, client_id: int, fy: str) -> YearlyFigures | None:
    return db.scalar(
        select(YearlyFigures).where(YearlyFigures.client_id == client_id, YearlyFigures.fy == fy)
    )


def list_figures(db: Session, client_id: int) -> list[YearlyFigures]:
    query = (
        select(YearlyFigures).where(YearlyFigures.client_id == client_id).order_by(YearlyFigures.fy)
    )
    return list(db.scalars(query))


def upsert_figures(
    db: Session, client_id: int, fy: str, values: dict, *, is_manual: bool | None = None
) -> YearlyFigures:
    """Set the given figure fields (leaving others untouched) for a client + FY.
    `is_manual=None` leaves the existing flag alone (new rows count as imported)."""
    row = get_figures(db, client_id, fy)
    if row is None:
        row = YearlyFigures(client_id=client_id, fy=fy, is_manual=False)
        db.add(row)
    for key, value in values.items():
        if key in FIGURE_FIELDS:
            setattr(row, key, value)
    if is_manual is not None:
        row.is_manual = is_manual
    db.flush()
    return row
