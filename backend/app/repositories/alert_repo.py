"""Database access for alerts."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Alert
from app.services.alert_engine import (
    ACTIONABLE,
    AlertEvent,
    severity_for_status,
    statuses_for_severity,
)


def latest_status(db: Session, client_id: int, fy: str, metric: str) -> str | None:
    """The status recorded by the most recent alert for this client/FY/metric."""
    query = (
        select(Alert.new_status)
        .where(Alert.client_id == client_id, Alert.fy == fy, Alert.metric == metric)
        .order_by(Alert.id.desc())
        .limit(1)
    )
    return db.scalar(query)


def create_alert(
    db: Session,
    client_id: int,
    fy: str,
    event: AlertEvent,
    crossed_on=None,
    crossed_voucher_no=None,
) -> Alert:
    alert = Alert(
        client_id=client_id,
        fy=fy,
        metric=event.metric,
        old_status=event.old_status,
        new_status=event.new_status,
        value=event.value,
        threshold_description=event.threshold_description,
        crossed_on=crossed_on,
        crossed_voucher_no=crossed_voucher_no,
    )
    db.add(alert)
    db.flush()
    return alert


def get_alert(db: Session, alert_id: int) -> Alert | None:
    return db.get(Alert, alert_id)


def list_alerts(
    db: Session,
    client_id: int | None = None,
    fy: str | None = None,
    severity: str | None = None,
    unacknowledged_only: bool = False,
    kind: str | None = None,
    section: str | None = None,
    sort: str = "recent",
) -> list[Alert]:
    """`sort`: 'recent' (newest first) or 'at_stake' (TDS money at stake, largest first)."""
    query = (
        select(Alert)
        .options(joinedload(Alert.client))  # client name for every row, in the same query
        .order_by(Alert.triggered_at.desc(), Alert.id.desc())
    )
    if client_id is not None:
        query = query.where(Alert.client_id == client_id)
    if fy:
        query = query.where(Alert.fy == fy)
    if severity:
        query = query.where(Alert.new_status.in_(statuses_for_severity(severity)))
    if unacknowledged_only:
        query = query.where(Alert.acknowledged.is_(False))
    if kind == "tds":
        query = query.where(Alert.metric.startswith("tds:"))
    elif kind == "turnover":
        query = query.where(~Alert.metric.startswith("tds:"))
    if section:
        query = query.where(Alert.section == section)
    if sort == "at_stake":
        query = query.order_by(None).order_by(
            Alert.money_at_stake.desc().nulls_last(), Alert.triggered_at.desc(), Alert.id.desc()
        )
    return list(db.scalars(query))


def latest_alert(db: Session, client_id: int, fy: str, metric: str) -> Alert | None:
    query = (
        select(Alert)
        .where(Alert.client_id == client_id, Alert.fy == fy, Alert.metric == metric)
        .order_by(Alert.id.desc())
        .limit(1)
    )
    return db.scalar(query)


def count_unacknowledged(db: Session) -> int:
    """Badge count of unacknowledged critical/high/warning alerts ('back to normal'
    info alerts are recorded but are not actionable)."""
    actionable = [s for level in ACTIONABLE for s in statuses_for_severity(level)]
    query = (
        select(func.count())
        .select_from(Alert)
        .where(Alert.acknowledged.is_(False), Alert.new_status.in_(actionable))
    )
    return db.scalar(query) or 0


def open_counts_by_client(db: Session, fy: str | None = None) -> dict[int, dict[str, int]]:
    """Unacknowledged alerts per client and severity: {client_id: {"critical": n, ...}}."""
    query = (
        select(Alert.client_id, Alert.new_status, func.count())
        .where(Alert.acknowledged.is_(False))
        .group_by(Alert.client_id, Alert.new_status)
    )
    if fy:
        query = query.where(Alert.fy == fy)
    counts: dict[int, dict[str, int]] = {}
    for client_id, status, count in db.execute(query):
        row = counts.setdefault(client_id, {"critical": 0, "high": 0, "warning": 0, "info": 0})
        row[severity_for_status(status)] += count
    return counts


def count_open_tds(db: Session, fy: str | None = None) -> dict[str, int]:
    """Unacknowledged TDS alerts by severity (for the dashboard)."""
    query = (
        select(Alert.new_status, func.count())
        .where(Alert.acknowledged.is_(False), Alert.metric.startswith("tds:"))
        .group_by(Alert.new_status)
    )
    if fy:
        query = query.where(Alert.fy == fy)
    counts = dict.fromkeys(("critical", "high", "warning", "info"), 0)
    for status, count in db.execute(query):
        counts[severity_for_status(status)] += count
    return counts


def acknowledge(db: Session, alert: Alert, by: str, note: str | None = None) -> Alert:
    alert.acknowledged = True
    alert.acknowledged_by = by
    if note:
        alert.note = note[:500]
    db.commit()
    db.refresh(alert)
    return alert
