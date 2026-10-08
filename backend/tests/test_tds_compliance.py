"""Compliance calendar and consequence figures (pure functions)."""

from datetime import date
from decimal import Decimal as D

from app.services import tds_compliance as tc


def test_deposit_due_is_the_7th_of_next_month_and_30_april_for_march() -> None:
    assert tc.deposit_due(date(2025, 4, 7)) == date(2025, 5, 7)
    assert tc.deposit_due(date(2025, 12, 31)) == date(2026, 1, 7)
    assert tc.deposit_due(date(2026, 3, 15)) == date(2026, 4, 30)


def test_part_of_a_month_counts_as_a_month() -> None:
    assert tc.months_or_part(date(2025, 4, 7), date(2025, 4, 30)) == 1
    assert tc.months_or_part(date(2025, 4, 30), date(2025, 5, 1)) == 2
    assert tc.months_or_part(date(2025, 4, 7), date(2026, 10, 6)) == 19
    assert tc.months_or_part(date(2025, 5, 1), date(2025, 4, 1)) == 0


def test_quarterly_return_due_dates() -> None:
    assert tc.quarter_return(date(2025, 4, 7)) == ("Q1 (Apr-Jun)", date(2025, 7, 31))
    assert tc.quarter_return(date(2025, 9, 30)) == ("Q2 (Jul-Sep)", date(2025, 10, 31))
    assert tc.quarter_return(date(2025, 11, 2)) == ("Q3 (Oct-Dec)", date(2026, 1, 31))
    assert tc.quarter_return(date(2026, 2, 1)) == ("Q4 (Jan-Mar)", date(2026, 5, 31))
    assert tc.form_16a_due(date(2025, 7, 31)) == date(2025, 8, 15)


def test_interest_working() -> None:
    working = tc.interest(D(7000), tc.LATE_DEDUCTION_PCT, date(2025, 5, 2), date(2025, 7, 1))
    assert working.months == 3
    assert working.interest == D(210)
    assert "₹7,000 x 1% x 3 month(s)" in working.text
    deposit = tc.interest(D(10000), tc.LATE_DEPOSIT_PCT, date(2025, 5, 2), date(2025, 5, 20))
    assert deposit.interest == D(150)


def test_late_fee_is_capped_at_the_tds_amount() -> None:
    assert tc.late_fee_234e(date(2025, 7, 31), date(2025, 8, 5), D(5000)) == (5, D(1000))
    assert tc.late_fee_234e(date(2025, 7, 31), date(2026, 7, 31), D(5000)) == (365, D(5000))
    assert tc.late_fee_234e(date(2025, 7, 31), date(2025, 7, 1), D(5000)) == (0, D(0))


def test_disallowance_is_30_percent() -> None:
    assert tc.disallowance(D(120000)) == D(36000)


def test_compliance_bundle() -> None:
    c = tc.compliance(date(2025, 5, 2), D(7000), D(0), D(7000), D(35000), date(2025, 10, 6))
    assert c.deposit_due == date(2025, 6, 7) and c.deposit_due_passed
    assert c.return_due == date(2025, 7, 31) and c.return_due_passed
    assert c.late_deduction.months == 6 and c.late_deduction.interest == D(420)
    assert c.late_deposit is None
    assert c.late_fee == D(7000)  # 67 days x 200 = 13,400, capped at the TDS amount
    assert c.penalty_271c == D(7000)
    assert c.disallowance == D(10500)
    none = tc.compliance(None, D(0), D(0), D(0), D(0), date(2025, 10, 6))
    assert none.deposit_due is None
