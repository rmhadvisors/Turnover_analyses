from datetime import date
from decimal import Decimal

from app.services.alert_engine import (
    AbsoluteLimitStatus,
    check_limit_series,
    evaluate_absolute_limit_alert,
    severity_for_status,
    statuses_for_severity,
)
from app.services.turnover import VoucherAmount, limit_entries

LIMIT = Decimal(1000)


def entries(*values):
    return [(date(2025, 4 + i, 10), f"V-{i + 1}", Decimal(v)) for i, v in enumerate(values)]


def test_crossing_is_reported_on_the_voucher_that_exceeds_the_limit() -> None:
    check = check_limit_series(entries(400, 400, 300, 500), LIMIT)  # 400, 800, 1100 <- V-3
    assert check.status == AbsoluteLimitStatus.CROSSED
    assert check.crossed_voucher_no == "V-3"
    assert check.crossed_on == date(2025, 6, 10)
    assert check.cumulative == Decimal(1600)


def test_exactly_at_limit_is_not_crossed() -> None:
    check = check_limit_series(entries(500, 500), LIMIT)
    assert check.status == AbsoluteLimitStatus.APPROACHING
    assert check.crossed_voucher_no is None


def test_approaching_at_80_percent_and_configurable() -> None:
    assert check_limit_series(entries(800), LIMIT).status == AbsoluteLimitStatus.APPROACHING
    assert check_limit_series(entries(799), LIMIT).status == AbsoluteLimitStatus.BELOW
    assert (
        check_limit_series(entries(500), LIMIT, Decimal(50)).status
        == AbsoluteLimitStatus.APPROACHING
    )


def test_entries_are_processed_in_date_order() -> None:
    shuffled = [entries(400, 400, 300)[i] for i in (2, 0, 1)]
    assert check_limit_series(shuffled, LIMIT).crossed_voucher_no == "V-3"


def test_credit_note_can_bring_total_back_under_limit() -> None:
    check = check_limit_series(entries(1100, -300), LIMIT)
    assert check.status == AbsoluteLimitStatus.APPROACHING
    assert check.crossed_on is None


def test_limit_entries_sign_credit_notes_and_pick_side() -> None:
    d = date(2025, 4, 1)
    vouchers = [
        VoucherAmount("sales", d, "S1", Decimal(100), Decimal(118)),
        VoucherAmount("credit_note", d, "C1", Decimal(10), Decimal(12)),
        VoucherAmount("purchase", d, "P1", Decimal(50), Decimal(59)),
    ]
    assert [e[2] for e in limit_entries(vouchers, "sales_turnover")] == [100, -10]
    assert [e[2] for e in limit_entries(vouchers, "sales_turnover", True)] == [118, -12]
    assert [e[2] for e in limit_entries(vouchers, "purchase_turnover")] == [50]


def test_alert_fires_once_per_status_change() -> None:
    def run(cumulative, previous):
        return evaluate_absolute_limit_alert("m", Decimal(cumulative), LIMIT, "L", previous)

    first = run(1100, "below")
    assert first is not None and first.new_status == "crossed"
    assert run(1500, "crossed") is None  # still crossed: no repeat alert
    assert run(1500, "crossed") is None


def test_severity_mapping() -> None:
    assert severity_for_status("crossed") == "critical"
    assert severity_for_status("significant_decrease") == "critical"
    assert severity_for_status("approaching") == "warning"
    assert severity_for_status("moderate_increase") == "warning"
    assert severity_for_status("normal") == "info"
    assert "crossed" in statuses_for_severity("critical")
