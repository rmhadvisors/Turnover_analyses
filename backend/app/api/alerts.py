from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Alert
from app.repositories import alert_repo
from app.schemas.alerts import AcknowledgeIn, AlertCount, AlertRead, RecheckResult
from app.services.alert_engine import severity_for_status
from app.services.recheck_service import evaluate_all_clients

router = APIRouter(prefix="/alerts", tags=["alerts"])

METRIC_LABELS = {
    "turnover": "Turnover",
    "purchases": "Purchases",
    "gross_profit": "Gross Profit",
    "net_profit": "Net Profit",
}


def _metric_label(alert: Alert) -> str:
    if alert.metric.startswith("limit:"):
        name = (alert.threshold_description or "").split(" limit = ")[0]
        return f"Limit: {name}" if name else "Absolute limit"
    return METRIC_LABELS.get(alert.metric, alert.metric)


def to_read(alert: Alert) -> AlertRead:
    return AlertRead(
        id=alert.id,
        client_id=alert.client_id,
        client_name=alert.client.name,
        fy=alert.fy,
        metric=alert.metric,
        metric_label=_metric_label(alert),
        old_status=alert.old_status,
        new_status=alert.new_status,
        severity=severity_for_status(alert.new_status),
        value=alert.value,
        threshold_description=alert.threshold_description,
        crossed_on=alert.crossed_on,
        crossed_voucher_no=alert.crossed_voucher_no,
        triggered_at=alert.triggered_at,
        acknowledged=alert.acknowledged,
        acknowledged_by=alert.acknowledged_by,
    )


@router.get("", response_model=list[AlertRead])
def list_alerts(
    client_id: int | None = None,
    fy: str | None = None,
    severity: str | None = Query(None, pattern="^(critical|warning|info)$"),
    unacknowledged_only: bool = False,
    db: Session = Depends(get_db),
):
    alerts = alert_repo.list_alerts(db, client_id, fy, severity, unacknowledged_only)
    return [to_read(a) for a in alerts]


@router.get("/count", response_model=AlertCount)
def count_alerts(db: Session = Depends(get_db)):
    return AlertCount(unacknowledged=alert_repo.count_unacknowledged(db))


@router.post("/recheck", response_model=RecheckResult)
def recheck(db: Session = Depends(get_db)):
    return RecheckResult(alerts_raised=len(evaluate_all_clients(db)))


@router.post("/{alert_id}/acknowledge", response_model=AlertRead)
def acknowledge(alert_id: int, body: AcknowledgeIn, db: Session = Depends(get_db)):
    alert = alert_repo.get_alert(db, alert_id)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    return to_read(alert_repo.acknowledge(db, alert, body.acknowledged_by))
