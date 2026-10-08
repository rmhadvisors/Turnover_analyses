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
from app.repositories import (
    figures_repo,
    import_repo,
    profile_repo,
    threshold_repo,
    voucher_repo,
)
from app.services import applicability
from app.services.alert_engine import (
    AbsoluteLimitStatus,
    BandStatus,
    check_limit_series,
    classify_absolute_limit,
    classify_band,
)
from app.services.calculations import MetricComparison, annualise, compare_metric
from app.services.fy_utils import (
    Period,
    fy_bounds,
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
    monthly_series,
    net_purchases,
    net_sales,
    purchase_entries_by_seller,
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
    is_period_matched: bool
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


def _same_day_in_fy(value: date, fy: str) -> date:
    """Put a calendar month/day into another financial year.

    The source ranges are dates from consecutive financial years, so matching by
    month/day (rather than a fixed number of days) also behaves correctly around
    a leap February.
    """
    start_year = int(fy[:4]) if value.month >= 4 else int(fy[:4]) + 1
    return date(start_year, value.month, value.day)


def _matched_available_period(db: Session, client_id: int, fy: str) -> tuple[Period, Period] | None:
    """Return the common observed window for two completed FY exports, if partial.

    A historical file can be cut off even though its FY is no longer in progress.
    In that case annual figures must not be compared with a part-year export.
    """
    previous = previous_fy(fy)
    # Date ranges alone are not proof of an incomplete export: a perfectly
    # valid register may simply have no voucher on the FY boundary.  Only
    # activate historical period matching when the import audit recorded a
    # cut-off JSON source for either compared FY.
    years = (fy[:4], previous[:4])
    cut_off = any(
        "[cut off]" in log.file_name and log.period and any(year in log.period for year in years)
        for log in import_repo.list_logs(db, client_id)
    )
    if not cut_off:
        return None
    current_rows = voucher_repo.amounts_for_fy(db, client_id, fy)
    previous_rows = voucher_repo.amounts_for_fy(db, client_id, previous)
    if not current_rows or not previous_rows:
        return None
    cur_start, cur_end = min(v.voucher_date for v in current_rows), max(
        v.voucher_date for v in current_rows
    )
    prev_start, prev_end = min(v.voucher_date for v in previous_rows), max(
        v.voucher_date for v in previous_rows
    )
    fy_start, fy_end = fy_bounds(fy)
    start = max(_same_day_in_fy(cur_start, fy), _same_day_in_fy(prev_start, fy))
    end = min(_same_day_in_fy(cur_end, fy), _same_day_in_fy(prev_end, fy))
    if start > end or (start <= fy_start and end >= fy_end):
        return None
    prev_period = Period(_same_day_in_fy(start, previous), _same_day_in_fy(end, previous))
    return Period(start, end), prev_period


def _exact_period_label(period: Period) -> str:
    return f"{period.start:%d-%b} to {period.end:%d-%b} only"


def _per_seller_check(vouchers, limit, include_gst: bool):
    """The worst seller for a per-seller limit: the first to cross it, else the largest."""
    checks = [
        check_limit_series(entries, limit.amount, limit.approaching_pct)
        for entries in purchase_entries_by_seller(vouchers, include_gst).values()
    ]
    crossed = [c for c in checks if c.crossed_on is not None]
    if crossed:
        return min(crossed, key=lambda c: c.crossed_on)
    return max(checks, key=lambda c: c.cumulative, default=None)


def applicable_limits(db: Session, client_id: int, fy: str) -> list:
    """Enabled limits for this FY whose `applies_when` rule matches the client's profile."""
    previous = figures_repo.get_figures(db, client_id, previous_fy(fy))
    facts = applicability.context(
        profile_repo.get_profile(db, client_id), previous.turnover if previous else None
    )
    return [
        limit
        for limit in threshold_repo.list_limits(db, enabled_only=True)
        if limit.fy_scope in (None, fy) and applicability.applies(limit.applies_when, facts)
    ]


def _limit_results(
    db: Session, client_id: int, fy: str, as_of: date, include_gst: bool, cur: dict
) -> list[LimitResult]:
    vouchers = [
        v for v in voucher_repo.amounts_for_fy(db, client_id, fy) if v.voucher_date <= as_of
    ]
    results = []
    for limit in applicable_limits(db, client_id, fy):
        metric = limit.metric.value
        if metric == "purchase_per_seller":
            check = _per_seller_check(vouchers, limit, include_gst)
            if check is None:
                continue  # no purchases from an identifiable seller
            results.append(
                LimitResult(
                    limit.id,
                    limit.name,
                    metric,
                    limit.amount,
                    limit.approaching_pct,
                    check.status,
                    check.cumulative,
                    check.crossed_on,
                    check.crossed_voucher_no,
                )
            )
            continue
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
    is_period_matched = False
    label, notes = None, []
    if is_ytd:
        label, notes = _apply_ytd_vouchers(db, client.id, fy, as_of, include_gst, cur, prev)
    else:
        matched = _matched_available_period(db, client.id, fy)
        if matched:
            current_period, previous_period = matched
            current_vouchers = voucher_repo.amounts_for_range(
                db, client.id, current_period.start, current_period.end
            )
            previous_vouchers = voucher_repo.amounts_for_range(
                db, client.id, previous_period.start, previous_period.end
            )
            for key, types, total in (
                ("turnover", SALES_TYPES, net_sales),
                ("purchases", PURCHASE_TYPES, net_purchases),
            ):
                if _has_type(current_vouchers, types) or _has_type(previous_vouchers, types):
                    cur[key] = total(current_vouchers, include_gst)
                    prev[key] = total(previous_vouchers, include_gst)
            is_period_matched = True
            label = _exact_period_label(current_period)
            notes.append(
                f"Comparison for {label}; one or both historical exports are incomplete. "
                "Sales and purchases use only the period covered in both years."
            )
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
    return ClientComparison(
        client.id, client.name, fy, prev_fy, is_ytd, is_period_matched, label, rows, limits, notes
    )


MONTH_LABELS = ("Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar")


@dataclass
class MonthlySeries:
    fy: str
    sales: list[Decimal]  # Apr..Mar, net of credit notes
    purchases: list[Decimal]  # Apr..Mar, net of debit notes


@dataclass
class MonthlyComparison:
    client_id: int
    client_name: str
    months: tuple[str, ...]
    current: MonthlySeries
    previous: MonthlySeries
    has_data: bool


def _fy_months(fy: str) -> list[tuple[int, int]]:
    start = int(fy[:4])
    return [(start, m) for m in range(4, 13)] + [(start + 1, m) for m in (1, 2, 3)]


def _series(db: Session, client_id: int, fy: str, include_gst: bool) -> MonthlySeries:
    by_month = monthly_series(voucher_repo.amounts_for_fy(db, client_id, fy), include_gst)
    zero = (Decimal(0), Decimal(0))
    pairs = [by_month.get(key, zero) for key in _fy_months(fy)]
    return MonthlySeries(fy, [p[0] for p in pairs], [p[1] for p in pairs])


def build_monthly(db: Session, client: Client, fy: str) -> MonthlyComparison:
    """Month-wise net sales and purchases for `fy` and the previous FY (from vouchers)."""
    include_gst = threshold_repo.get_settings(db).include_gst_in_turnover
    current = _series(db, client.id, fy, include_gst)
    previous = _series(db, client.id, previous_fy(fy), include_gst)
    has_data = any(v for s in (current, previous) for v in s.sales + s.purchases)
    return MonthlyComparison(client.id, client.name, MONTH_LABELS, current, previous, has_data)
