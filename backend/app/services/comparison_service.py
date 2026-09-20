"""Builds the current-vs-previous FY comparison for a client.

Shared by the alert re-check and the reports, so the figures a user sees on
screen are exactly the ones the alerts were raised on. For an FY still in
progress, turnover and purchases are compared year-to-date against the same
months of the previous FY (from vouchers), never part-year against full-year.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Client
from app.repositories import figures_repo, threshold_repo, voucher_repo
from app.services.alert_engine import (
    AbsoluteLimitStatus,
    BandStatus,
    check_limit_series,
    classify_absolute_limit,
    classify_band,
)
from app.services.calculations import MetricComparison, annualise, compare_metric
from app.services.fy_utils import (
    is_fy_in_progress,
    months_elapsed_in_fy,
    period_label,
    previous_fy,
    ytd_window_pair,
)
from app.services.turnover import (
    PURCHASE_TYPES,
    SALES_TYPES,
    VoucherAmount,
    limit_entries,
    net_purchases,
    net_sales,
)

METRICS = (
    ("turnover", "Turnover"),
    ("purchases", "Purchases"),
    ("gross_profit", "Gross Profit"),
    ("net_profit", "Net Profit"),
)


@dataclass
class MetricRow:
    key: str
    label: str
    previous: Decimal | None
    current: Decimal | None
    comparison: MetricComparison | None  # None when the current value is missing
    band: BandStatus | None
    annualised: Decimal | None = None


@dataclass
class LimitResult:
    limit_id: int
    name: str
    metric: str
    amount: Decimal
    approaching_pct: Decimal
    status: AbsoluteLimitStatus
    cumulative: Decimal
    crossed_on: date | None
    crossed_voucher_no: str | None


@dataclass
class ClientComparison:
    client_id: int
    client_name: str
    fy: str
    previous_fy: str
    is_ytd: bool
    period_label: str | None
    rows: list[MetricRow]
    limits: list[LimitResult]
    notes: list[str] = field(default_factory=list)


def _has_type(vouchers: list[VoucherAmount], types: dict) -> bool:
    return any(v.voucher_type in types for v in vouchers)


def _figure_values(figures) -> dict[str, Decimal | None]:
    return {key: getattr(figures, key) if figures else None for key, _ in METRICS}


def _apply_ytd_vouchers(
    db: Session, client_id: int, fy: str, as_of: date, include_gst: bool, cur, prev
) -> tuple[str, list[str]]:
    """Overwrite turnover/purchases in `cur`/`prev` with month-matched YTD voucher totals."""
    cur_window, prev_window = ytd_window_pair(fy, as_of)
    cur_v = voucher_repo.amounts_for_range(db, client_id, cur_window.start, cur_window.end)
    prev_v = voucher_repo.amounts_for_range(db, client_id, prev_window.start, prev_window.end)
    label = period_label(cur_window)
    notes = []
    for key, types, total in (
        ("turnover", SALES_TYPES, net_sales),
        ("purchases", PURCHASE_TYPES, net_purchases),
    ):
        if _has_type(cur_v, types) or _has_type(prev_v, types):
            cur[key], prev[key] = total(cur_v, include_gst), total(prev_v, include_gst)
    notes.append(
        f"FY {fy} is in progress: turnover and purchases are {label} vs {label} of the previous FY."
    )
    if cur["gross_profit"] is not None or cur["net_profit"] is not None:
        notes.append("Profit figures are compared as entered/imported and are not period-matched.")
    return label, notes


def _limit_results(
    db: Session, client_id: int, fy: str, as_of: date, include_gst: bool, cur: dict
) -> list[LimitResult]:
    vouchers = [
        v for v in voucher_repo.amounts_for_fy(db, client_id, fy) if v.voucher_date <= as_of
    ]
    results = []
    for limit in threshold_repo.list_limits(db, enabled_only=True):
        if limit.fy_scope not in (None, fy):
            continue
        metric = limit.metric.value
        entries = limit_entries(vouchers, metric, include_gst)
        if entries:
            check = check_limit_series(entries, limit.amount, limit.approaching_pct)
            status, cumulative = check.status, check.cumulative
            crossed_on, crossed_no = check.crossed_on, check.crossed_voucher_no
        else:  # no vouchers: fall back to manually entered / imported yearly figures
            figure = cur["purchases"] if metric == "purchase_turnover" else cur["turnover"]
            if figure is None:
                continue
            status = classify_absolute_limit(figure, limit.amount, limit.approaching_pct)
            cumulative, crossed_on, crossed_no = figure, None, None
        results.append(
            LimitResult(
                limit.id,
                limit.name,
                metric,
                limit.amount,
                limit.approaching_pct,
                status,
                cumulative,
                crossed_on,
                crossed_no,
            )
        )
    return results


def build_comparison(
    db: Session, client: Client, fy: str, as_of: date | None = None
) -> ClientComparison:
    """Compare `fy` with the previous FY for one client and check the absolute limits."""
    as_of = as_of or date.today()
    settings = threshold_repo.get_settings(db)
    include_gst = settings.include_gst_in_turnover
    prev_fy = previous_fy(fy)
    cur = _figure_values(figures_repo.get_figures(db, client.id, fy))
    prev = _figure_values(figures_repo.get_figures(db, client.id, prev_fy))

    is_ytd = is_fy_in_progress(fy, as_of)
    label, notes = None, []
    if is_ytd:
        label, notes = _apply_ytd_vouchers(db, client.id, fy, as_of, include_gst, cur, prev)
    months = months_elapsed_in_fy(fy, as_of)

    rows = []
    for key, name in METRICS:
        comparison = None if cur[key] is None else compare_metric(prev[key], cur[key])
        band = None
        if comparison is not None and comparison.change_pct is not None:
            band = classify_band(
                comparison.change_pct, settings.moderate_pct, settings.significant_pct
            )
        projected = None
        if is_ytd and key in ("turnover", "purchases") and cur[key] is not None:
            projected = annualise(cur[key], months)
        rows.append(MetricRow(key, name, prev[key], cur[key], comparison, band, projected))

    limits = _limit_results(db, client.id, fy, as_of, include_gst, cur)
    return ClientComparison(client.id, client.name, fy, prev_fy, is_ytd, label, rows, limits, notes)
