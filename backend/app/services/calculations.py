"""Pure calculation functions: differences, change %, YTD comparison, annualisation.

No database or UI code here so these can be unit-tested on their own. Money is
always Decimal - never float - so results round cleanly (no
-0.28570000000000001 artifacts).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")


def round2(value: Decimal) -> Decimal:
    """Round a Decimal to 2 places using standard (half-up) rounding."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class MetricComparison:
    previous: Decimal | None
    current: Decimal
    difference: Decimal | None
    change_pct: Decimal | None
    no_comparison: bool
    sign_change: str | None  # "turned_to_profit" | "turned_to_loss" | None


def compare_metric(previous: Decimal | None, current: Decimal | None) -> MetricComparison:
    """Compare a previous-year and current-year value for one metric.

    Change % uses abs(previous) as the denominator so a loss<->profit swing
    still gets the correct sign. previous == 0 or None means there is nothing
    to divide by ("New / No comparison") - a sign_change flag is still raised
    if a genuine zero baseline moved to a nonzero current value.
    """
    current = Decimal(0) if current is None else current

    if previous is None or previous == 0:
        sign_change = None
        if previous == 0 and current != 0:
            sign_change = "turned_to_profit" if current > 0 else "turned_to_loss"
        return MetricComparison(
            previous=previous,
            current=current,
            difference=None if previous is None else round2(current - previous),
            change_pct=None,
            no_comparison=True,
            sign_change=sign_change,
        )

    difference = current - previous
    change_pct = round2((difference / abs(previous)) * 100)

    sign_change = None
    if previous < 0 and current >= 0:
        sign_change = "turned_to_profit"
    elif previous >= 0 and current < 0:
        sign_change = "turned_to_loss"

    return MetricComparison(
        previous=previous,
        current=current,
        difference=round2(difference),
        change_pct=change_pct,
        no_comparison=False,
        sign_change=sign_change,
    )


def annualise(ytd_value: Decimal, months_elapsed: int) -> Decimal | None:
    """Project a full-FY value from a year-to-date value and months elapsed.

    Returns None when there is no elapsed period to extrapolate from.
    """
    if months_elapsed <= 0:
        return None
    return round2(ytd_value * 12 / months_elapsed)


@dataclass(frozen=True)
class YtdComparison:
    period_label: str
    comparison: MetricComparison
    annualised_current: Decimal | None


def compare_ytd(
    previous_ytd: Decimal,
    current_ytd: Decimal,
    months_elapsed: int,
    period_label: str,
) -> YtdComparison:
    """Compare same-period-so-far YTD values, with an annualised projection."""
    return YtdComparison(
        period_label=period_label,
        comparison=compare_metric(previous_ytd, current_ytd),
        annualised_current=annualise(current_ytd, months_elapsed),
    )
