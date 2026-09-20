"""Re-runs every threshold check for a client and records status-change alerts.

Called after every import, manual entry and threshold edit. An alert row is only
written when a status differs from the last recorded one (an unalerted metric
counts as 'normal' / 'below'), so re-running with unchanged data is silent.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import Alert
from app.repositories import (
    alert_repo,
    client_repo,
    figures_repo,
    threshold_repo,
    voucher_repo,
)
from app.services.alert_engine import (
    AbsoluteLimitStatus,
    AlertEvent,
    BandStatus,
    evaluate_absolute_limit_alert,
    evaluate_band_alert,
    notify,
)
from app.services.comparison_service import build_comparison
from app.services.fy_utils import next_fy


def _record(db: Session, client_id: int, fy: str, event: AlertEvent, **extra) -> Alert:
    alert = alert_repo.create_alert(db, client_id, fy, event, **extra)
    notify(event)
    return alert


def _fys_with_data(db: Session, client_id: int) -> set[str]:
    figures = {f.fy for f in figures_repo.list_figures(db, client_id)}
    return figures | set(voucher_repo.fys_with_vouchers(db, client_id))


def evaluate_client(
    db: Session, client_id: int, fys: set[str] | list[str], as_of: date | None = None
) -> list[Alert]:
    """Evaluate the given FYs (and each following FY, whose 'previous year' may
    have changed) for one client. Returns the newly raised alerts."""
    client = client_repo.get_client(db, client_id)
    if client is None:
        return []
    settings = threshold_repo.get_settings(db)
    with_data = _fys_with_data(db, client_id)
    targets = sorted({f for fy in fys for f in (fy, next_fy(fy))} & with_data)

    raised: list[Alert] = []
    for fy in targets:
        comparison = build_comparison(db, client, fy, as_of)
        for row in comparison.rows:
            if row.band is None:
                continue
            previous = alert_repo.latest_status(db, client_id, fy, row.key)
            event = evaluate_band_alert(
                row.key,
                row.comparison.change_pct,
                previous or BandStatus.NORMAL.value,
                settings.moderate_pct,
                settings.significant_pct,
            )
            if event:
                raised.append(_record(db, client_id, fy, event))
        for limit in comparison.limits:
            metric_key = f"limit:{limit.limit_id}:{limit.metric}"
            previous = alert_repo.latest_status(db, client_id, fy, metric_key)
            event = evaluate_absolute_limit_alert(
                metric_key,
                limit.cumulative,
                limit.amount,
                limit.name,
                previous or AbsoluteLimitStatus.BELOW.value,
                limit.approaching_pct,
            )
            if event:
                raised.append(
                    _record(
                        db,
                        client_id,
                        fy,
                        event,
                        crossed_on=limit.crossed_on,
                        crossed_voucher_no=limit.crossed_voucher_no,
                    )
                )
    db.flush()
    return raised


def evaluate_all_clients(db: Session, as_of: date | None = None) -> list[Alert]:
    """Full re-check (e.g. after thresholds change)."""
    raised: list[Alert] = []
    for client in client_repo.list_clients(db):
        raised += evaluate_client(db, client.id, _fys_with_data(db, client.id), as_of)
    db.commit()
    return raised
