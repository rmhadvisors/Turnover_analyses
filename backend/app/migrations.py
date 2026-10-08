"""Small, idempotent schema upgrades for an existing database (run at startup).

`create_all` creates missing tables and the startup code adds missing indexes; this
adds columns introduced after a table was created, and fills them once.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import AbsoluteLimit, AbsoluteLimitMetric, Alert
from app.repositories.seed_data import PURCHASE_194Q_TEXT
from app.services.applicability import DEFAULT_RULES

_NEW_COLUMNS = {
    "yearly_figures": {"profit_source": "VARCHAR(20)"},
    "absolute_limits": {"applies_when": "JSON"},
    "threshold_settings": {
        "tds_approaching_pct": "NUMERIC(5, 2) DEFAULT 80",
        "tds_analysis_fy": "VARCHAR(7) DEFAULT '2025-26'",
        "tds_unidentified_min": "NUMERIC(18, 2) DEFAULT 30000",
    },
    "tds_ledger_map": {"requires_choice": "BOOLEAN DEFAULT 0"},
    "alerts": {
        "note": "VARCHAR(500)",
        "section": "VARCHAR(10)",
        "party": "VARCHAR(255)",
        "party_key": "VARCHAR(300)",
        "party_pan": "VARCHAR(10)",
        "threshold": "NUMERIC(18, 2)",
        "aggregate": "NUMERIC(18, 2)",
        "tds_computed": "NUMERIC(18, 2)",
        "tds_deducted": "NUMERIC(18, 2)",
        "shortfall": "NUMERIC(18, 2)",
        "money_at_stake": "NUMERIC(18, 2)",
    },
}
SYSTEM_PER_SELLER = "System - 194Q is now checked per seller"


def _add_columns(engine: Engine) -> set[str]:
    added = set()
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in _NEW_COLUMNS.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                    added.add(f"{table}.{name}")
    return added


def upgrade(engine: Engine) -> None:
    added = _add_columns(engine)
    with Session(engine) as db:
        if "yearly_figures.profit_source" in added:
            # existing profit figures came from manual entry or a P&L import
            db.execute(
                text(
                    "UPDATE yearly_figures SET profit_source = CASE WHEN is_manual "
                    "THEN 'manual' ELSE 'pl_import' END "
                    "WHERE gross_profit IS NOT NULL OR net_profit IS NOT NULL"
                )
            )
        if "absolute_limits.applies_when" in added:
            for limit in db.query(AbsoluteLimit):
                limit.applies_when = DEFAULT_RULES.get(limit.name)
                if (
                    limit.name == "TDS u/s 194Q - purchases of goods"
                    and limit.metric == AbsoluteLimitMetric.PURCHASE_TURNOVER
                ):
                    old_key = f"limit:{limit.id}:{AbsoluteLimitMetric.PURCHASE_TURNOVER.value}"
                    limit.metric = AbsoluteLimitMetric.PURCHASE_PER_SELLER
                    limit.description = (
                        "Verify current limit before relying on it. " + PURCHASE_194Q_TEXT
                    )
                    for alert in db.query(Alert).filter(
                        Alert.metric == old_key, Alert.acknowledged.is_(False)
                    ):
                        alert.acknowledged, alert.acknowledged_by = True, SYSTEM_PER_SELLER
        db.commit()
