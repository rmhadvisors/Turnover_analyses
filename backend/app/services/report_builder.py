"""Turns a ClientComparison into display-ready report data.

(Excel / PDF export and the month-wise chart data are added in the reports stage.)
"""

from __future__ import annotations

from app.schemas.reports import ComparisonRead, LimitResultRead, MetricRowRead, SummaryRow
from app.services.alert_engine import BAND_DISPLAY, AbsoluteLimitStatus
from app.services.comparison_service import ClientComparison, LimitResult, MetricRow

NO_COMPARISON_LABEL = "New / No comparison"
METRIC_NAMES = {
    "sales_turnover": "Sales turnover",
    "purchase_turnover": "Purchase turnover",
    "aggregate_turnover": "Aggregate turnover",
}


def _direction(row: MetricRow) -> str:
    difference = row.comparison.difference if row.comparison else None
    if difference is None:
        return "none"
    return "up" if difference > 0 else "down" if difference < 0 else "flat"


def _metric_row(row: MetricRow) -> MetricRowRead:
    comparison = row.comparison
    label = NO_COMPARISON_LABEL
    if row.band is not None:
        label = BAND_DISPLAY[row.band][1]
    return MetricRowRead(
        key=row.key,
        label=row.label,
        previous=row.previous,
        current=row.current,
        difference=comparison.difference if comparison else None,
        change_pct=comparison.change_pct if comparison else None,
        direction=_direction(row),
        no_comparison=True if comparison is None else comparison.no_comparison,
        sign_change=comparison.sign_change if comparison else None,
        band=row.band.value if row.band else None,
        status_label=label,
        annualised=row.annualised,
    )


def limit_message(limit: LimitResult) -> str:
    what = METRIC_NAMES.get(limit.metric, limit.metric)
    if limit.status == AbsoluteLimitStatus.CROSSED:
        when = f" on {limit.crossed_on:%d-%b-%Y}" if limit.crossed_on else ""
        voucher = f" (voucher {limit.crossed_voucher_no})" if limit.crossed_voucher_no else ""
        return f"{what} crossed {limit.name}{when}{voucher}"
    if limit.status == AbsoluteLimitStatus.APPROACHING:
        return f"{what} is approaching {limit.name} (at or above {limit.approaching_pct:g}% of the limit)"
    return f"{what} is within {limit.name}"


def _limit_row(limit: LimitResult) -> LimitResultRead:
    return LimitResultRead(
        limit_id=limit.limit_id,
        name=limit.name,
        metric=limit.metric,
        amount=limit.amount,
        status=limit.status.value,
        cumulative=limit.cumulative,
        crossed_on=limit.crossed_on,
        crossed_voucher_no=limit.crossed_voucher_no,
        message=limit_message(limit),
    )


def comparison_to_read(comparison: ClientComparison) -> ComparisonRead:
    return ComparisonRead(
        client_id=comparison.client_id,
        client_name=comparison.client_name,
        fy=comparison.fy,
        previous_fy=comparison.previous_fy,
        is_ytd=comparison.is_ytd,
        is_period_matched=comparison.is_period_matched,
        period_label=comparison.period_label,
        rows=[_metric_row(r) for r in comparison.rows],
        limits=[_limit_row(x) for x in comparison.limits],
        notes=comparison.notes,
    )


def summary_row(comparison: ClientComparison, open_alerts: int) -> SummaryRow:
    """One line of the all-clients summary, built from the client's comparison."""
    turnover = next(_metric_row(r) for r in comparison.rows if r.key == "turnover")
    net_profit = next(r for r in comparison.rows if r.key == "net_profit")
    statuses = [x.status for x in comparison.limits]
    return SummaryRow(
        client_id=comparison.client_id,
        client_name=comparison.client_name,
        fy=comparison.fy,
        is_ytd=comparison.is_ytd,
        previous_turnover=turnover.previous,
        current_turnover=turnover.current,
        change_pct=turnover.change_pct,
        band=turnover.band,
        status_label=turnover.status_label,
        net_profit_flag=net_profit.comparison.sign_change if net_profit.comparison else None,
        limits_crossed=statuses.count(AbsoluteLimitStatus.CROSSED),
        limits_approaching=statuses.count(AbsoluteLimitStatus.APPROACHING),
        open_alerts=open_alerts,
    )
