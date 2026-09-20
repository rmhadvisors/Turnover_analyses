from decimal import Decimal

from app.services.alert_engine import (
    AbsoluteLimitStatus,
    BandStatus,
    classify_absolute_limit,
    classify_band,
    evaluate_absolute_limit_alert,
    evaluate_band_alert,
    status_changed,
)


def test_significant_increase_above_20() -> None:
    assert classify_band(Decimal(25)) == BandStatus.SIGNIFICANT_INCREASE


def test_exactly_20_is_moderate_increase_not_significant() -> None:
    assert classify_band(Decimal(20)) == BandStatus.MODERATE_INCREASE


def test_moderate_increase_between_5_and_20() -> None:
    assert classify_band(Decimal(10)) == BandStatus.MODERATE_INCREASE


def test_exactly_5_is_normal_not_moderate() -> None:
    assert classify_band(Decimal(5)) == BandStatus.NORMAL


def test_normal_band_covers_zero() -> None:
    assert classify_band(Decimal(0)) == BandStatus.NORMAL


def test_exactly_negative_5_is_normal() -> None:
    assert classify_band(Decimal(-5)) == BandStatus.NORMAL


def test_moderate_decrease_between_minus5_and_minus20() -> None:
    assert classify_band(Decimal(-10)) == BandStatus.MODERATE_DECREASE


def test_exactly_negative_20_is_moderate_decrease_not_significant() -> None:
    assert classify_band(Decimal(-20)) == BandStatus.MODERATE_DECREASE


def test_significant_decrease_below_minus20() -> None:
    assert classify_band(Decimal(-25)) == BandStatus.SIGNIFICANT_DECREASE


def test_custom_band_cutoffs() -> None:
    assert classify_band(Decimal(15), moderate_pct=Decimal(10), significant_pct=Decimal(15)) == (
        BandStatus.MODERATE_INCREASE
    )
    assert (
        classify_band(Decimal("15.01"), moderate_pct=Decimal(10), significant_pct=Decimal(15))
        == BandStatus.SIGNIFICANT_INCREASE
    )


def test_absolute_limit_below() -> None:
    assert classify_absolute_limit(Decimal(50), Decimal(100)) == AbsoluteLimitStatus.BELOW


def test_absolute_limit_approaching_at_default_80_pct() -> None:
    assert classify_absolute_limit(Decimal(80), Decimal(100)) == AbsoluteLimitStatus.APPROACHING


def test_absolute_limit_crossed_strictly_above() -> None:
    # Exactly at the limit is not yet "crossed" (crossed means exceeds it),
    # but it is still >= the approaching threshold.
    assert classify_absolute_limit(Decimal(100), Decimal(100)) == AbsoluteLimitStatus.APPROACHING
    assert classify_absolute_limit(Decimal("100.01"), Decimal(100)) == AbsoluteLimitStatus.CROSSED


def test_status_changed() -> None:
    assert status_changed(None, "normal") is True
    assert status_changed("normal", "normal") is False
    assert status_changed("normal", "moderate_increase") is True


def test_evaluate_band_alert_only_fires_on_change() -> None:
    event = evaluate_band_alert("turnover", Decimal(25), previous_status=None)
    assert event is not None
    assert event.new_status == "significant_increase"

    no_event = evaluate_band_alert("turnover", Decimal(25), previous_status="significant_increase")
    assert no_event is None

    changed_event = evaluate_band_alert(
        "turnover", Decimal(10), previous_status="significant_increase"
    )
    assert changed_event is not None
    assert changed_event.old_status == "significant_increase"
    assert changed_event.new_status == "moderate_increase"


def test_evaluate_absolute_limit_alert_crossed_once() -> None:
    first = evaluate_absolute_limit_alert(
        "sales_turnover",
        Decimal(9000000),
        Decimal(10000000),
        "GST threshold",
        previous_status="below",
    )
    assert first is not None
    assert first.new_status == "approaching"

    repeat = evaluate_absolute_limit_alert(
        "sales_turnover",
        Decimal(9500000),
        Decimal(10000000),
        "GST threshold",
        previous_status="approaching",
    )
    assert repeat is None

    crossed = evaluate_absolute_limit_alert(
        "sales_turnover",
        Decimal(10500000),
        Decimal(10000000),
        "GST threshold",
        previous_status="approaching",
    )
    assert crossed is not None
    assert crossed.new_status == "crossed"
