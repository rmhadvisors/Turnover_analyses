"""Threshold classification and status-change detection.

Pure functions only - no database or UI code - so band boundaries and
status-change behaviour can be unit tested independently of how alerts get
persisted or displayed. Sending a raised alert (in-app now, email/WhatsApp
later) is a separate pluggable concern - see `notify()` below.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

DEFAULT_MODERATE_PCT = Decimal(5)
DEFAULT_SIGNIFICANT_PCT = Decimal(20)
DEFAULT_APPROACHING_PCT = Decimal(80)


class BandStatus(str, Enum):
    SIGNIFICANT_INCREASE = "significant_increase"
    MODERATE_INCREASE = "moderate_increase"
    NORMAL = "normal"
    MODERATE_DECREASE = "moderate_decrease"
    SIGNIFICANT_DECREASE = "significant_decrease"


BAND_DISPLAY: dict[BandStatus, tuple[str, str]] = {
    BandStatus.SIGNIFICANT_INCREASE: ("\U0001f534", "Significant Increase"),
    BandStatus.MODERATE_INCREASE: ("\U0001f7e1", "Moderate Increase"),
    BandStatus.NORMAL: ("\U0001f7e2", "Normal"),
    BandStatus.MODERATE_DECREASE: ("\U0001f7e1", "Moderate Decrease"),
    BandStatus.SIGNIFICANT_DECREASE: ("\U0001f534", "Significant Decrease"),
}


def classify_band(
    change_pct: Decimal,
    moderate_pct: Decimal = DEFAULT_MODERATE_PCT,
    significant_pct: Decimal = DEFAULT_SIGNIFICANT_PCT,
) -> BandStatus:
    """Classify a change % into a band.

    A value must go beyond a limit to move to the next band: exactly
    +/-significant_pct stays Moderate, exactly +/-moderate_pct stays Normal.
    """
    if change_pct > significant_pct:
        return BandStatus.SIGNIFICANT_INCREASE
    if change_pct > moderate_pct:
        return BandStatus.MODERATE_INCREASE
    if change_pct >= -moderate_pct:
        return BandStatus.NORMAL
    if change_pct >= -significant_pct:
        return BandStatus.MODERATE_DECREASE
    return BandStatus.SIGNIFICANT_DECREASE


class AbsoluteLimitStatus(str, Enum):
    BELOW = "below"
    APPROACHING = "approaching"
    CROSSED = "crossed"


def classify_absolute_limit(
    cumulative_value: Decimal,
    limit_amount: Decimal,
    approaching_pct: Decimal = DEFAULT_APPROACHING_PCT,
) -> AbsoluteLimitStatus:
    """Classify a cumulative YTD value against a configurable rupee limit."""
    if cumulative_value > limit_amount:
        return AbsoluteLimitStatus.CROSSED
    if limit_amount > 0 and cumulative_value >= limit_amount * approaching_pct / 100:
        return AbsoluteLimitStatus.APPROACHING
    return AbsoluteLimitStatus.BELOW


def status_changed(old_status: str | None, new_status: str) -> bool:
    """Alerts should only fire when a status actually changes, not every re-check."""
    return old_status != new_status


@dataclass(frozen=True)
class AlertEvent:
    metric: str
    old_status: str | None
    new_status: str
    value: Decimal
    threshold_description: str


def evaluate_band_alert(
    metric: str,
    change_pct: Decimal,
    previous_status: str | None,
    moderate_pct: Decimal = DEFAULT_MODERATE_PCT,
    significant_pct: Decimal = DEFAULT_SIGNIFICANT_PCT,
) -> AlertEvent | None:
    """Return an AlertEvent only if the % band status changed since last check."""
    new_status = classify_band(change_pct, moderate_pct, significant_pct)
    if not status_changed(previous_status, new_status.value):
        return None
    return AlertEvent(
        metric=metric,
        old_status=previous_status,
        new_status=new_status.value,
        value=change_pct,
        threshold_description=f"moderate={moderate_pct}%, significant={significant_pct}%",
    )


def evaluate_absolute_limit_alert(
    metric: str,
    cumulative_value: Decimal,
    limit_amount: Decimal,
    limit_name: str,
    previous_status: str | None,
    approaching_pct: Decimal = DEFAULT_APPROACHING_PCT,
) -> AlertEvent | None:
    """Return an AlertEvent only if the absolute-limit status changed since last check."""
    new_status = classify_absolute_limit(cumulative_value, limit_amount, approaching_pct)
    if not status_changed(previous_status, new_status.value):
        return None
    return AlertEvent(
        metric=metric,
        old_status=previous_status,
        new_status=new_status.value,
        value=cumulative_value,
        threshold_description=f"{limit_name} limit = {limit_amount}",
    )


NotifyFn = Callable[[AlertEvent], None]


def notify(event: AlertEvent, channels: list[NotifyFn] | None = None) -> None:
    """Pluggable alert dispatch. In-app only for now (persisted by the caller);
    email/WhatsApp channels can be appended to `channels` later without
    changing anything above this function.
    """
    for channel in channels or []:
        channel(event)
