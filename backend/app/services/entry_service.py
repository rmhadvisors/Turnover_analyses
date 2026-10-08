"""Manual entry of yearly figures, followed by an automatic re-check."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Alert
from app.repositories import figures_repo
from app.services.recheck_service import evaluate_client

FIGURE_KEYS = ("turnover", "purchases", "gross_profit", "net_profit")


def save_manual_entry(
    db: Session,
    client_id: int,
    previous_fy: str,
    current_fy: str,
    previous: dict,
    current: dict,
) -> list[Alert]:
    """Store both years' figures (None leaves a field untouched), then re-evaluate."""
    for fy, values in ((previous_fy, previous), (current_fy, current)):
        given = {k: v for k, v in values.items() if k in FIGURE_KEYS and v is not None}
        if given:  # a year with nothing typed keeps its row (and its source) unchanged
            row = figures_repo.upsert_figures(db, client_id, fy, given, is_manual=True)
            if "gross_profit" in given or "net_profit" in given:
                row.profit_source = "manual"
    raised = evaluate_client(db, client_id, [previous_fy])
    db.commit()
    return raised
