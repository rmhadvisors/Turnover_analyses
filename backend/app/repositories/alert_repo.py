"""Database access for alerts."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Alert
from app.services.alert_engine import AlertEvent, statuses_for_severity


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
) -> list[Alert]:
    query = select(Alert).order_by(Alert.triggered_at.desc(), Alert.id.desc())
    if client_id is not None:
        query = query.where(Alert.client_id == client_id)
    if fy:
        query = query.where(Alert.fy == fy)
    if severity:
        query = query.where(Alert.new_status.in_(statuses_for_severity(severity)))
    if unacknowledged_only:
        query = query.where(Alert.acknowledged.is_(False))
    return list(db.scalars(query))


def count_unacknowledged(db: Session) -> int:
    """Badge count of unacknowledged critical/warning alerts ('back to normal'
    info alerts are recorded but are not actionable)."""
    actionable = statuses_for_severity("critical") + statuses_for_severity("warning")
    query = (
        select(func.count())
        .select_from(Alert)
        .where(Alert.acknowledged.is_(False), Alert.new_status.in_(actionable))
    )
    return db.scalar(query) or 0


def acknowledge(db: Session, alert: Alert, by: str) -> Alert:
    alert.acknowledged = True
    alert.acknowledged_by = by
    db.commit()
    db.refresh(alert)
    return alert
