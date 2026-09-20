"""Indian financial-year (1 April - 31 March) assignment and period math.

Pure functions only - no database or UI code, so FY logic can be unit tested
independently of how vouchers or reports are stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


def fy_start_year(on_date: date) -> int:
    """Calendar year in which the FY containing `on_date` starts (Apr-Mar)."""
    return on_date.year if on_date.month >= 4 else on_date.year - 1


def fy_label(on_date: date) -> str:
    """FY label for a date, e.g. 2026-01-15 -> '2025-26', 2025-04-01 -> '2025-26'."""
    start = fy_start_year(on_date)
    return f"{start}-{str(start + 1)[2:]}"


def fy_bounds(fy: str) -> tuple[date, date]:
    """(start, end) dates for an FY label like '2024-25' -> (2024-04-01, 2025-03-31)."""
    start_year = int(fy[:4])
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def next_fy(fy: str) -> str:
    start_year = int(fy[:4]) + 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


def previous_fy(fy: str) -> str:
    start_year = int(fy[:4]) - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


def is_fy_in_progress(fy: str, as_of: date) -> bool:
    start, end = fy_bounds(fy)
    return start <= as_of < end


@dataclass(frozen=True)
class Period:
    start: date
    end: date


def ytd_period(fy: str, as_of: date) -> Period:
    """Apr 1 (FY start) through as_of, clipped to the FY's own bounds."""
    start, end = fy_bounds(fy)
    effective_end = min(max(as_of, start), end)
    return Period(start, effective_end)


def months_elapsed_in_fy(fy: str, as_of: date) -> int:
    """Whole months elapsed in the FY as of `as_of`, from 0 to 12."""
    start, end = fy_bounds(fy)
    if as_of < start:
        return 0
    effective = min(as_of, end)
    months = (effective.year - start.year) * 12 + (effective.month - start.month) + 1
    return max(0, min(12, months))


def _add_months_end(start: date, months: int) -> date:
    """Last day of the `months`-th month starting from (and including) `start`'s month."""
    month_index = start.month - 1 + (months - 1)
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    next_month_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return next_month_first - timedelta(days=1)


def same_period_previous_fy(fy: str, as_of: date) -> Period:
    """The matching Apr-1..same-month window in the previous FY, for YTD comparison."""
    months = months_elapsed_in_fy(fy, as_of)
    prev = previous_fy(fy)
    prev_start, prev_end = fy_bounds(prev)
    if months <= 0:
        return Period(prev_start, prev_start)
    period_end = min(_add_months_end(prev_start, months), prev_end)
    return Period(prev_start, period_end)


def period_label(period: Period) -> str:
    """Short label for a period, e.g. 'Apr-Aug'."""
    return f"{period.start.strftime('%b')}-{period.end.strftime('%b')}"


def is_valid_fy(fy: str) -> bool:
    """True for labels like '2024-25' whose second part follows the first year."""
    if len(fy) != 7 or fy[4] != "-" or not (fy[:4] + fy[5:]).isdigit():
        return False
    return int(fy[5:]) == (int(fy[:4]) + 1) % 100


def ytd_window_pair(fy: str, as_of: date) -> tuple[Period, Period]:
    """Month-aligned (current, previous-FY) windows for a like-for-like YTD comparison."""
    months = months_elapsed_in_fy(fy, as_of)
    start, end = fy_bounds(fy)
    if months <= 0:
        return Period(start, start), same_period_previous_fy(fy, as_of)
    current_end = min(_add_months_end(start, months), end)
    return Period(start, current_end), same_period_previous_fy(fy, as_of)
