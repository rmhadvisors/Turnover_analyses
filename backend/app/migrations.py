"""Small, idempotent schema upgrades for an existing database (run at startup).

`create_all` creates missing tables and the startup code adds missing indexes; this
adds columns introduced after a table was created, and fills them once.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import AbsoluteLimit, Alert, Client
from app.models.tds import TdsLedger
from app.repositories.alert_repo import close_limit_alerts
from app.repositories.seed_data import REMOVED_TDS_LIMITS
from app.services.applicability import DEFAULT_RULES, OLD_DEFAULT_RULES
from app.services.tally_json import company_id

_NEW_COLUMNS = {
    "clients": {"tally_company_id": "VARCHAR(36)"},
    "client_profiles": {"pan": "VARCHAR(10)"},
    "yearly_figures": {"profit_source": "VARCHAR(20)"},
    "absolute_limits": {"applies_when": "JSON"},
    "threshold_settings": {
        "tds_approaching_pct": "NUMERIC(5, 2) DEFAULT 80",
        "tds_analysis_fy": "VARCHAR(7) DEFAULT '2025-26'",
        "tds_unidentified_min": "NUMERIC(18, 2) DEFAULT 30000",
        "max_unmatched_ledger_pct": "NUMERIC(5, 2) DEFAULT 2",
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
SYSTEM_TDS_MOVED = "System - TDS thresholds are checked per party in the TDS module"
SYSTEM_LIMIT_DELETED = "System - limit deleted"


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


def _remove_tds_limits(db: Session) -> bool:
    """TDS section thresholds are per-party limits, not turnover limits: delete the rows
    seeded under those names and close their open alerts. True if anything changed."""
    rows = db.query(AbsoluteLimit).filter(AbsoluteLimit.name.in_(REMOVED_TDS_LIMITS)).all()
    close_limit_alerts(db, {row.id for row in rows}, SYSTEM_TDS_MOVED)
    for row in rows:
        db.delete(row)
    db.flush()
    existing = {limit_id for (limit_id,) in db.query(AbsoluteLimit.id)}
    orphans = {
        int(metric.split(":")[1])
        for (metric,) in db.query(Alert.metric).filter(
            Alert.acknowledged.is_(False), Alert.metric.like("limit:%")
        )
    } - existing
    close_limit_alerts(db, orphans, SYSTEM_LIMIT_DELETED)
    return bool(rows or orphans)


def _upgrade_rules(db: Session) -> bool:
    """Limits still on a rule that was seeded before get the current default rule."""
    changed = False
    for limit in db.query(AbsoluteLimit).filter(AbsoluteLimit.name.in_(OLD_DEFAULT_RULES)):
        if limit.applies_when == OLD_DEFAULT_RULES[limit.name]:
            limit.applies_when = DEFAULT_RULES[limit.name]
            changed = True
    return changed


def _backfill_company_ids(db: Session) -> None:
    """The Tally company GUID of clients imported before it was stored, from their ledgers."""
    for client in db.query(Client).filter(Client.tally_company_id.is_(None)):
        ids = Counter(
            company_id(guid)
            for (guid,) in db.query(TdsLedger.guid).filter(TdsLedger.client_id == client.id)
        )
        ids.pop(None, None)
        if len(ids) == 1:
            client.tally_company_id = next(iter(ids))


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
        if "clients.tally_company_id" in added:
            _backfill_company_ids(db)
        changed = _remove_tds_limits(db)
        changed = _upgrade_rules(db) or changed
        db.commit()
        if changed:
            # closes the open alerts of limits that no longer apply to a client's profile
            from app.services.recheck_service import evaluate_all_clients

            evaluate_all_clients(db)
