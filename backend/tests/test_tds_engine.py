"""TDS rate master + calculation engine (pure functions, no database)."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.repositories.tds_seed import default_rows
from app.services import tds_engine as te
from app.services.tds_engine import INDIVIDUAL_HUF, OTHER, CrossedBy, Payment, TdsStatus

RULES = [te.SectionRule(**row) for row in default_rows()]
FY26 = date(2025, 4, 1)  # FY 2025-26: the sheet's values
FY25 = date(2024, 4, 1)


def rule(key: str, on: date = FY26) -> te.SectionRule:
    found = te.rule_on(RULES, key, on)
    assert found is not None
    return found


def pay(day: date, amount, no: str = "1", ledger: str = "Expense") -> Payment:
    return Payment(day, no, "Journal", ledger, D(amount), voucher_key=f"k{no}")


# --------------------------------------------------------------- rate master


def test_rate_master_matches_the_sheet_from_fy_2025_26() -> None:
    expected = {
        "194C": (30000, 100000, "1", "2", "20", "full"),
        "194H": (0, 20000, "2", "2", "20", "full"),
        "194J(a)": (0, 50000, "2", "2", "20", "full"),
        "194J(b)": (0, 50000, "10", "10", "20", "full"),
        "194J(ba)": (0, 0, "10", "10", "20", "full"),
        "194Q": (0, 5000000, "0.1", "0.1", "5", "excess"),
        "194I(a)": (0, 600000, "2", "2", "20", "full"),
        "194I(b)": (0, 600000, "10", "10", "20", "full"),
    }
    for key, (single, total, ind, other, no_pan, base) in expected.items():
        r = rule(key)
        assert (r.single_threshold, r.aggregate_threshold) == (D(single), D(total)), key
        assert (r.rate_individual, r.rate_other, r.rate_no_pan) == (
            D(ind),
            D(other),
            D(no_pan),
        ), key
        assert r.base == base, key


def test_older_years_keep_the_limits_then_in_force() -> None:
    assert rule("194J(b)", FY25).aggregate_threshold == D(30000)
    assert rule("194I(b)", FY25).aggregate_threshold == D(240000)
    assert rule("194H", FY25).rate_other == D(5)
    assert rule("194H", date(2024, 10, 1)).rate_other == D(2)
    assert rule("194H", date(2025, 3, 31)).aggregate_threshold == D(15000)


# ------------------------------------------------- the sheet's worked example


def test_sheet_worked_example_abc_contractors() -> None:
    row = te.checker_row(rule("194C"), OTHER, True, D(45000), D(120000), "ABC Contractors")
    assert row.crossed
    assert row.base == D(120000)
    assert row.rate.rate == D(2)
    assert row.tds == D(2400)


def test_194c_crossed_by_the_single_payment_test_alone() -> None:
    row = te.checker_row(rule("194C"), OTHER, True, D(35000), D(60000))
    assert row.crossed_by == CrossedBy.SINGLE
    # s.194C(5): only the payment above Rs 30,000 is liable while the aggregate is <= 1 lakh
    assert row.base == D(35000)
    assert row.tds == D(700)


def test_194c_crossed_by_single_payment_at_voucher_level() -> None:
    payments = [pay(date(2025, 5, 1), 10000, "1"), pay(date(2025, 6, 1), 35000, "2"),
                pay(date(2025, 7, 1), 15000, "3")]  # fmt: skip
    result = te.evaluate_payments(RULES, "194C", FY26, payments, OTHER, True)
    assert result.aggregate == D(60000)
    assert result.crossed_by == CrossedBy.SINGLE
    assert result.crossing_test == CrossedBy.SINGLE
    assert (result.crossed_on, result.crossed_voucher_no) == (date(2025, 6, 1), "2")
    assert result.base == D(35000)
    assert result.tds == D(700)


def test_194c_not_crossed() -> None:
    row = te.checker_row(rule("194C"), OTHER, True, D(25000), D(90000))
    assert not row.crossed
    assert row.base == 0 and row.tds == 0


def test_194c_crossed_by_aggregate_records_the_voucher_and_the_test() -> None:
    payments = [pay(date(2025, 4, 10), 25000, "1"), pay(date(2025, 5, 10), 25000, "2"),
                pay(date(2025, 6, 10), 25000, "3"), pay(date(2025, 7, 10), 25001, "4"),
                pay(date(2025, 8, 10), 20000, "5")]  # fmt: skip
    result = te.evaluate_payments(RULES, "194C", FY26, payments, INDIVIDUAL_HUF, True)
    assert result.crossing_test == CrossedBy.AGGREGATE
    assert (result.crossed_on, result.crossed_voucher_no) == (date(2025, 7, 10), "4")
    assert result.base == D(120001)  # the whole aggregate, earlier payments included
    assert result.tds == D(1200)  # 1% Individual/HUF
    assert [line.is_crossing for line in result.lines] == [False, False, False, True, False]


# ------------------------------------------------------------------- 194Q


def test_194q_only_the_excess_over_50_lakh() -> None:
    row = te.checker_row(rule("194Q"), OTHER, True, D(0), D(6200000))
    assert row.base == D(1200000)
    assert row.rate.rate == D("0.1")
    assert row.tds == D(1200)


def test_194q_excess_is_allocated_from_the_crossing_voucher() -> None:
    payments = [pay(date(2025, 4, 1), 3000000, "1"), pay(date(2025, 6, 1), 2500000, "2"),
                pay(date(2025, 9, 1), 700000, "3")]  # fmt: skip
    result = te.evaluate_payments(RULES, "194Q", FY26, payments, OTHER, True)
    assert result.crossed_voucher_no == "2"
    assert [line.liable for line in result.lines] == [D(0), D(500000), D(700000)]
    assert result.base == result.excess_over_threshold == D(1200000)
    assert result.tds == D(1200)


def test_194q_does_not_apply_when_buyer_turnover_was_8_crore() -> None:
    payer = te.payer_applicability("2025-26", "2024-25", D(80000000), "Company", "PAN")
    assert payer.s194q.applies is False
    assert "does not exceed ₹10 Cr" in payer.s194q.reason


def test_194q_applies_when_buyer_turnover_exceeded_10_crore() -> None:
    payer = te.payer_applicability("2025-26", "2024-25", D("100000000.01"), "Firm", "PAN")
    assert payer.s194q.applies is True


def test_194q_cannot_be_determined_without_last_years_turnover() -> None:
    payer = te.payer_applicability("2025-26", "2024-25", None, "Firm", "PAN")
    assert payer.s194q.applies is None
    assert payer.s194q.reason == te.CANNOT_DETERMINE


# ------------------------------------------------------------------ no PAN


def test_no_pan_194c_individual_is_20_percent_not_1() -> None:
    choice = te.choose_rate(rule("194C"), INDIVIDUAL_HUF, pan_available=False)
    assert choice.rate == D(20)
    assert choice.normal_rate == D(1)
    assert "206AA" in choice.reason


def test_no_pan_194q_is_5_percent() -> None:
    choice = te.choose_rate(rule("194Q"), OTHER, pan_available=False)
    assert choice.rate == D(5)
    row = te.checker_row(rule("194Q"), OTHER, False, D(0), D(6200000))
    assert row.tds == D(60000)


def test_no_pan_never_lowers_a_higher_normal_rate() -> None:
    high = te.SectionRule("X", "X", "x", D(0), D(1), D(30), D(30), D(20), "full", FY26)
    assert te.choose_rate(high, OTHER, False).rate == D(30)


# ---------------------------------------------------------------- 194J


def test_194j_categories_are_tracked_separately() -> None:
    technical = te.evaluate_payments(RULES, "194J(a)", FY26, [pay(FY26, 40000)], OTHER, True)
    professional = te.evaluate_payments(RULES, "194J(b)", FY26, [pay(FY26, 45000)], OTHER, True)
    assert not technical.crossed and not professional.crossed  # 85,000 together, but separate


def test_194j_ba_director_remuneration_has_no_threshold() -> None:
    row = te.checker_row(rule("194J(ba)"), INDIVIDUAL_HUF, True, D(10000), D(10000))
    assert row.crossed_by == CrossedBy.NO_THRESHOLD
    assert row.tds == D(1000)
    result = te.evaluate_payments(RULES, "194J(ba)", FY26, [pay(FY26, 10000)], INDIVIDUAL_HUF, True)
    assert result.crossed and result.tds == D(1000)
    assert result.crossed_on == FY26


# ------------------------------------------------------- boundary values


@pytest.mark.parametrize(
    ("key", "single", "aggregate"),
    [
        ("194C", 30000, 30000),  # single payment exactly at the limit
        ("194C", 30000, 100000),  # aggregate exactly at the limit
        ("194H", 0, 20000),
        ("194J(a)", 0, 50000),
        ("194J(b)", 0, 50000),
        ("194Q", 0, 5000000),
        ("194I(a)", 0, 600000),
        ("194I(b)", 0, 600000),
    ],
)
def test_exactly_the_threshold_does_not_cross_one_rupee_more_does(key, single, aggregate) -> None:
    r = rule(key)
    at = te.checker_row(r, OTHER, True, D(single), D(aggregate))
    assert not at.crossed and at.tds == 0
    if single and single == aggregate:  # 194C single-payment test
        over = te.checker_row(r, OTHER, True, D(single + 1), D(single + 1))
        assert over.crossed_by == CrossedBy.SINGLE
    else:
        over = te.checker_row(r, OTHER, True, D(0), D(aggregate + 1))
        assert over.crossed_by == CrossedBy.AGGREGATE
    assert over.tds > 0 or key == "194Q"  # 194Q on Re 1 of excess rounds to 0


def test_boundary_at_voucher_level() -> None:
    exactly = te.evaluate_payments(RULES, "194I(b)", FY26, [pay(FY26, 600000)], OTHER, True)
    assert not exactly.crossed and exactly.approaching
    over = te.evaluate_payments(RULES, "194I(b)", FY26, [pay(FY26, 600000), pay(FY26, 1, "2")],
                                OTHER, True)  # fmt: skip
    assert over.crossed and over.tds == D(60000)


# ------------------------------------------------- rates by date, reversals


def test_194h_rate_change_mid_year_uses_the_rate_on_each_voucher_date() -> None:
    payments = [pay(date(2024, 6, 1), 10000, "1"), pay(date(2024, 11, 1), 10000, "2")]
    result = te.evaluate_payments(RULES, "194H", FY25, payments, OTHER, True)
    assert result.rule.aggregate_threshold == D(15000)
    assert result.rates_used == {D(5): D(10000), D(2): D(10000)}
    assert result.tds == D(700)


def test_a_reversal_bringing_the_aggregate_back_under_uncrosses() -> None:
    payments = [pay(FY26, 60000, "1"), pay(date(2025, 5, 1), -15000, "2")]
    result = te.evaluate_payments(RULES, "194J(b)", FY26, payments, OTHER, True)
    assert not result.crossed and result.tds == 0 and result.crossed_on is None


def test_nil_reason_keeps_the_test_but_makes_tds_nil() -> None:
    payments = [pay(FY26, 150000)]
    result = te.evaluate_payments(RULES, "194C", FY26, payments, OTHER, True,
                                  nil_reason="Transporter declaration")  # fmt: skip
    assert result.crossed and result.tds == 0
    assert te.classify(result, D(0)) == TdsStatus.NIL


def test_approaching_uses_the_configured_percentage() -> None:
    at_80 = te.evaluate_payments(RULES, "194H", FY26, [pay(FY26, 16000)], OTHER, True)
    assert at_80.approaching
    at_90 = te.evaluate_payments(RULES, "194H", FY26, [pay(FY26, 16000)], OTHER, True,
                                 approaching_pct=D(90))  # fmt: skip
    assert not at_90.approaching


# ---------------------------------------------- computed vs deducted


@pytest.mark.parametrize(
    ("deducted", "status"),
    [
        (0, TdsStatus.NOT_DEDUCTED),
        (1200, TdsStatus.SHORT),
        (2390, TdsStatus.OK),
        (2400, TdsStatus.OK),
        (2410, TdsStatus.OK),
        (3000, TdsStatus.EXCESS),
    ],
)
def test_classification_against_tds_actually_deducted(deducted, status) -> None:
    payments = [pay(FY26, 45000, "1"), pay(date(2025, 6, 1), 75000, "2")]
    result = te.evaluate_payments(RULES, "194C", FY26, payments, OTHER, True)
    assert result.tds == D(2400)
    assert te.classify(result, D(deducted)) == status


def test_classification_below_and_approaching() -> None:
    below = te.evaluate_payments(RULES, "194C", FY26, [pay(FY26, 20000)], OTHER, True)
    assert te.classify(below, D(0)) == TdsStatus.BELOW
    near = te.evaluate_payments(RULES, "194C", FY26, [pay(FY26, 25000, "1"),
        pay(FY26, 25000, "2"), pay(FY26, 25000, "3"), pay(FY26, 10000, "4")], OTHER, True)  # fmt: skip
    assert te.classify(near, D(0)) == TdsStatus.APPROACHING


# ------------------------------------------------------- PAN and payer


def test_pan_from_gstin_and_constitution() -> None:
    assert te.pan_from_gstin("27ABFPL5804R1Z4") == "ABFPL5804R"
    # 4th character of ABFPL5804R is 'P' -> Individual (the 'F' is the 3rd character)
    assert te.constitution_from_pan("ABFPL5804R") == "Individual"
    assert te.constitution_from_pan(te.pan_from_gstin("27AAKFH4657G1Z4")) == "Firm"
    assert te.constitution_from_pan("AYWPK3086E") == "Individual"
    assert te.constitution_from_pan("AAACH1118B") == "Company"
    assert te.constitution_from_pan("AAAHX1234A") == "HUF"
    assert te.pan_from_gstin("not-a-gstin") is None
    assert te.payee_type_from_pan("BBIPS6830E") == INDIVIDUAL_HUF
    assert te.payee_type_from_pan("AAACH1118B") == OTHER
    assert te.payee_type_from_pan(None) is None


def test_non_individual_payer_always_deducts_under_194c_h_j_i() -> None:
    payer = te.payer_applicability("2025-26", "2024-25", D(1000), "Firm", "PAN")
    assert payer.others.applies is True and not payer.others.needs_confirmation


def test_individual_payer_between_1_and_10_crore_is_asked_to_confirm_audit() -> None:
    payer = te.payer_applicability("2025-26", "2024-25", D("47963293.91"), "Individual", "PAN")
    assert payer.others.applies is True
    assert payer.others.needs_confirmation
    assert "Please confirm" in payer.others.reason


def test_individual_payer_audit_override_wins() -> None:
    payer = te.payer_applicability("2025-26", "2024-25", D("47963293.91"), "Individual", "PAN",
                                   audit_override=False)  # fmt: skip
    assert payer.others.applies is False and not payer.others.needs_confirmation
    assert payer.audit_source == "confirmed by user"


def test_individual_payer_above_10_crore_is_certainly_audited() -> None:
    payer = te.payer_applicability("2025-26", "2024-25", D("289956842.26"), "Individual", "PAN")
    assert payer.others.applies is True and not payer.others.needs_confirmation


# The four real clients, with their verified FY 2024-25 sales (expected 194Q outcome for 25-26).
REAL = {
    "JIGEESHA AUTO SERVICES": ("27ABFPL5804R1Z4", "289956842.26", True),
    "ADVANCE POWER": ("27AYWPK3086E1ZY", "47963293.91", False),
    "HOTEL KINARA": ("27AAKFH4657G1Z4", "42722334.00", False),
    "SHIRKE FLEX INDUSTRIAL": ("27BBIPS6830E1ZG", "11969243.00", False),
}


@pytest.mark.parametrize("client", sorted(REAL))
def test_real_clients_194q_outcome_from_verified_turnover(client) -> None:
    gstin, turnover, expected = REAL[client]
    constitution = te.constitution_from_pan(te.pan_from_gstin(gstin))
    payer = te.payer_applicability("2025-26", "2024-25", D(turnover), constitution, "GSTIN")
    assert payer.s194q.applies is expected
    if constitution == "Individual":
        # 194C/H/J/I depend on 44AB audit in 24-25: certain only above Rs 10 Cr
        assert payer.others.needs_confirmation is (D(turnover) <= D(100000000))
    else:
        assert payer.others.applies is True
