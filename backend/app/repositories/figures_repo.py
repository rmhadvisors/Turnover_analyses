"""Database access for yearly figures."""

from __future__ import annotations

from decimal import Decimal

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


PROFIT_SOURCES_KEPT = ("manual", "pl_import")  # never overwritten by derived figures


def store_derived_profit(
    db: Session, client_id: int, fy: str, gross: Decimal | None, net: Decimal | None
) -> bool:
    """Save GP / NP worked out from a Tally export, unless the year already has profit
    entered manually or from a P&L import. Returns True when stored."""
    row = get_figures(db, client_id, fy)
    if row is not None and row.profit_source in PROFIT_SOURCES_KEPT:
        if row.gross_profit is not None or row.net_profit is not None:
            return False
    if row is None:
        row = YearlyFigures(client_id=client_id, fy=fy, is_manual=False)
        db.add(row)
    row.gross_profit, row.net_profit, row.profit_source = gross, net, "tally_json"
    db.flush()
    return True


def list_all(db: Session) -> list[YearlyFigures]:
    return list(db.scalars(select(YearlyFigures)))


def all_fys(db: Session) -> set[str]:
    """Every FY with yearly figures for any client."""
    return set(db.scalars(select(YearlyFigures.fy).distinct()))


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
