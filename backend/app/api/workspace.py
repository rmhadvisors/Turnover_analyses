"""One lightweight call with everything the frontend sidebar needs."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.reports import all_financial_years
from app.database import get_db
from app.repositories import alert_repo, client_repo, figures_repo, voucher_repo
from app.schemas.client import ClientRead

router = APIRouter(tags=["workspace"])


class WorkspaceRead(BaseModel):
    clients: list[ClientRead]
    fys: list[str]  # newest first
    unacknowledged_alerts: int  # critical + warning, all clients and years


class CoverageRow(BaseModel):
    """What data one client has for one FY."""

    client_id: int
    fy: str
    sales_vouchers: int  # sales + credit notes stored (Tally / register imports)
    purchase_vouchers: int  # purchases + debit notes stored
    figures: str | None  # yearly figures: "manual", "imported" or None
    has_profit: bool  # gross or net profit present
    profit_source: str | None = None  # manual | pl_import | tally_json


@router.get("/coverage", response_model=list[CoverageRow])
def coverage(db: Session = Depends(get_db)):
    """Every client/FY pair that has any data. Years with nothing are simply absent."""
    rows: dict[tuple[int, str], dict] = {}

    def row(client_id: int, fy: str) -> dict:
        return rows.setdefault(
            (client_id, fy),
            {"client_id": client_id, "fy": fy, "sales_vouchers": 0, "purchase_vouchers": 0,
             "figures": None, "has_profit": False, "profit_source": None},
        )  # fmt: skip

    for client_id, fy, voucher_type, count in voucher_repo.counts_by_client_fy(db):
        side = "sales_vouchers" if voucher_type in ("sales", "credit_note") else "purchase_vouchers"
        row(client_id, fy)[side] += count
    for figures in figures_repo.list_all(db):
        values = (figures.turnover, figures.purchases, figures.gross_profit, figures.net_profit)
        if all(value is None for value in values):
            continue
        item = row(figures.client_id, figures.fy)
        item["figures"] = "manual" if figures.is_manual else "imported"
        item["has_profit"] = figures.gross_profit is not None or figures.net_profit is not None
        item["profit_source"] = figures.profit_source if item["has_profit"] else None
    return sorted(rows.values(), key=lambda r: (r["client_id"], r["fy"]))


@router.get("/workspace", response_model=WorkspaceRead)
def workspace(db: Session = Depends(get_db)):
    return WorkspaceRead(
        clients=client_repo.list_clients(db),
        fys=all_financial_years(db),
        unacknowledged_alerts=alert_repo.count_unacknowledged(db),
    )
