"""Compliance calendar and consequences of a TDS default, with figures where they can be
worked out ('Compliance & Penalties' sheet). Pure functions only.

General reference - verify due dates against the latest CBDT notifications. Not tax advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.services.tds_engine import round_rupee
from app.utils.indian_format import format_indian_commas as inr

DISCLAIMER = (
    "General reference - verify due dates against the latest CBDT notifications. Not tax advice."
)
LATE_DEDUCTION_PCT = Decimal(1)  # per month or part, s.201(1A)(i)
LATE_DEPOSIT_PCT = Decimal("1.5")  # per month or part, s.201(1A)(ii)
LATE_FEE_PER_DAY = Decimal(200)  # s.234E
DISALLOWANCE_PCT = Decimal(30)  # s.40(a)(ia)
PENALTY_271H = "₹10,000 to ₹1,00,000"


def form_26qc_due(deducted_on: date) -> date:
    """s.194-IB: Form 26QC (challan-cum-statement) within 30 days of the end of the month of
    deduction."""
    first_next = date(deducted_on.year + (deducted_on.month == 12), deducted_on.month % 12 + 1, 1)
    return first_next - timedelta(days=1) + timedelta(days=30)


def deposit_due(deducted_on: date) -> date:
    """7th of the following month; TDS deducted in March by 30 April."""
    if deducted_on.month == 3:
        return date(deducted_on.year, 4, 30)
    year, month = (
        (deducted_on.year + 1, 1)
        if deducted_on.month == 12
        else (deducted_on.year, deducted_on.month + 1)
    )
    return date(year, month, 7)


def months_or_part(start: date, end: date) -> int:
    """Calendar months from `start` to `end`, a part of a month counting as a whole month
    (the way interest u/s 201(1A) is worked out). 0 when `end` is before `start`."""
    if end < start:
        return 0
    return (end.year - start.year) * 12 + end.month - start.month + 1


QUARTERS = (
    {  # first month of the quarter -> (label, return due (month, day), due in next calendar year)
        4: ("Q1 (Apr-Jun)", (7, 31), False),
        7: ("Q2 (Jul-Sep)", (10, 31), False),
        10: ("Q3 (Oct-Dec)", (1, 31), True),
        1: ("Q4 (Jan-Mar)", (5, 31), False),
    }
)


def quarter_return(on: date) -> tuple[str, date]:
    """(quarter label, Form 26Q due date) for the quarter containing `on`."""
    first = {1: 1, 2: 1, 3: 1, 4: 4, 5: 4, 6: 4, 7: 7, 8: 7, 9: 7, 10: 10, 11: 10, 12: 10}[on.month]
    label, (month, day), next_year = QUARTERS[first]
    return label, date(on.year + (1 if next_year else 0), month, day)


def form_16a_due(return_due: date) -> date:
    return return_due + timedelta(days=15)


@dataclass(frozen=True)
class InterestWorking:
    amount: Decimal
    pct_per_month: Decimal
    start: date
    end: date
    months: int
    interest: Decimal

    @property
    def text(self) -> str:
        return (
            f"{inr(self.amount)} x {self.pct_per_month}% x {self.months} month(s) "
            f"({self.start:%d-%b-%Y} to {self.end:%d-%b-%Y}; part of a month counts as a month) "
            f"= {inr(self.interest)}"
        )


def interest(amount: Decimal, pct: Decimal, start: date, end: date) -> InterestWorking:
    months = months_or_part(start, end) if amount > 0 else 0
    value = round_rupee(amount * pct / 100 * months)
    return InterestWorking(amount, pct, start, end, months, value)


def late_fee_234e(return_due: date, today: date, cap: Decimal) -> tuple[int, Decimal]:
    """(days late, fee) if the return is still not filed today: ₹200 a day, capped at
    the TDS amount."""
    days = max(0, (today - return_due).days)
    return days, min(LATE_FEE_PER_DAY * days, max(cap, Decimal(0)))


def disallowance(expenditure: Decimal) -> Decimal:
    return round_rupee(max(expenditure, Decimal(0)) * DISALLOWANCE_PCT / 100)


@dataclass
class Compliance:
    deductible_on: date | None
    deposit_due: date | None
    deposit_due_passed: bool | None
    quarter: str | None
    return_due: date | None
    return_due_passed: bool | None
    form_16a_due: date | None
    late_deduction: InterestWorking | None
    late_deposit: InterestWorking | None
    late_fee_days: int
    late_fee: Decimal
    penalty_271c: Decimal
    penalty_271h: str
    disallowance_base: Decimal
    disallowance: Decimal
    today: date


def compliance(
    crossed_on: date | None,
    tds_computed: Decimal,
    deducted: Decimal,
    shortfall: Decimal,
    base_not_covered: Decimal,
    today: date,
    section_key: str = "",
) -> Compliance:
    """Figures for one party/section. TDS becomes deductible on the crossing voucher's
    date (credit to the party's account, the earlier event in the books)."""
    if crossed_on is None:
        return Compliance(None, None, None, None, None, None, None, None, None, 0, Decimal(0),
                          Decimal(0), PENALTY_271H, Decimal(0), Decimal(0), today)  # fmt: skip
    if section_key == "194-IB":  # one challan-cum-statement, no quarterly 26Q
        due = return_due = form_26qc_due(crossed_on)
        quarter = "Form 26QC"
    else:
        due = deposit_due(crossed_on)
        quarter, return_due = quarter_return(crossed_on)
    late_deduction = interest(shortfall, LATE_DEDUCTION_PCT, crossed_on, today)
    late_deposit = interest(deducted, LATE_DEPOSIT_PCT, crossed_on, today) if deducted > 0 else None
    days, fee = late_fee_234e(return_due, today, tds_computed)
    return Compliance(
        deductible_on=crossed_on,
        deposit_due=due,
        deposit_due_passed=today > due,
        quarter=quarter,
        return_due=return_due,
        return_due_passed=today > return_due,
        form_16a_due=form_16a_due(return_due),
        late_deduction=late_deduction,
        late_deposit=late_deposit,
        late_fee_days=days,
        late_fee=fee,
        penalty_271c=shortfall,
        penalty_271h=PENALTY_271H,
        disallowance_base=base_not_covered,
        disallowance=disallowance(base_not_covered),
        today=today,
    )


# ------------------------------------------------- money at stake (estimates)

ESTIMATE_LABEL = (
    "Estimated, assuming not yet deposited / filed - verify against challans and the TRACES portal."
)


@dataclass(frozen=True)
class InterestLine:
    """Interest on the part of the unpaid TDS that became deductible on one date."""

    deductible_on: date
    vouchers: int
    tds: Decimal  # unpaid TDS that became deductible on this date
    months: int
    interest_1: Decimal  # 1% a month or part: not deducted
    interest_15: Decimal  # 1.5% a month or part: deducted but not deposited (alternative)
    deposit_due: date


@dataclass(frozen=True)
class Exposure:
    """Estimated cost of one party/section's default as of `today`.

    `basis` is the TDS not deducted (for an unidentified payee: the indicative TDS as if it
    were one payee). It is spread over the vouchers in proportion to each voucher's TDS and
    interest runs from the date each became deductible. Money at stake = basis + 30%
    disallowance + 1% interest (+ any excess deducted, which has to be corrected). The 1.5%
    figure is an alternative; the s.234E fee is per return (see `return_fees`)."""

    basis: Decimal
    basis_label: str
    lines: tuple[InterestLine, ...]
    disallowance_base: Decimal
    disallowance: Decimal
    excess: Decimal
    today: date

    @property
    def interest(self) -> Decimal:
        return sum((line.interest_1 for line in self.lines), Decimal(0))

    @property
    def interest_15(self) -> Decimal:
        return sum((line.interest_15 for line in self.lines), Decimal(0))

    @property
    def deductible_on(self) -> date | None:
        return self.lines[0].deductible_on if self.lines else None

    @property
    def at_stake(self) -> Decimal:
        return self.basis + self.disallowance + self.interest + self.excess


def due_for(deducted_on: date, section_key: str) -> date:
    return form_26qc_due(deducted_on) if section_key == "194-IB" else deposit_due(deducted_on)


def exposure(
    basis: Decimal,
    basis_label: str,
    items: list[tuple[date, Decimal]],
    disallowance_base: Decimal,
    section_key: str,
    today: date,
    excess: Decimal = Decimal(0),
) -> Exposure:
    """`items`: (date the TDS became deductible, TDS on that voucher) for every voucher."""
    total = sum((tds for _, tds in items), Decimal(0))
    by_date: dict[date, list[Decimal]] = {}
    for when, tds in items:
        if tds:
            by_date.setdefault(when, []).append(tds)
    lines: list[InterestLine] = []
    if basis > 0 and total > 0:
        for when in sorted(by_date):
            share = (sum(by_date[when], Decimal(0)) * basis / total).quantize(Decimal("0.01"))
            months = months_or_part(when, today)
            lines.append(
                InterestLine(
                    deductible_on=when,
                    vouchers=len(by_date[when]),
                    tds=share,
                    months=months,
                    interest_1=round_rupee(share * LATE_DEDUCTION_PCT / 100 * months),
                    interest_15=round_rupee(share * LATE_DEPOSIT_PCT / 100 * months),
                    deposit_due=due_for(when, section_key),
                )
            )
    return Exposure(
        basis=basis,
        basis_label=basis_label,
        lines=tuple(lines),
        disallowance_base=disallowance_base,
        disallowance=disallowance(disallowance_base),
        excess=excess,
        today=today,
    )


@dataclass(frozen=True)
class ReturnFee:
    """s.234E late fee for ONE return (all parties), assuming it is not yet filed."""

    label: str  # 'Q4 (Jan-Mar) 26Q' / 'Form 26QC Mar-2026'
    due: date
    tds: Decimal  # all TDS deductible in the return's period (the fee cap)
    shortfall: Decimal  # of which not deducted
    days: int
    fee: Decimal
    deducted_in_books: Decimal = Decimal(0)  # TDS the client booked in the period

    @property
    def likely_unfiled(self) -> bool:
        """No TDS deducted in the books for the period: the return was probably never filed,
        so the fee is counted as at risk. Otherwise it is shown 'only if not filed'."""
        return self.deducted_in_books <= 0


def _return_key(when: date, section_key: str) -> tuple[str, date]:
    if section_key == "194-IB":
        return f"Form 26QC {when:%b-%Y}", form_26qc_due(when)
    label, due = quarter_return(when)
    return f"{label} Form 26Q", due


def return_fees(
    items: list[tuple[date, str, Decimal, Decimal]],
    today: date,
    deductions: list[tuple[date, str, Decimal]] = (),
) -> list[ReturnFee]:
    """`items`: (date deductible, section, TDS, TDS not deducted) for every voucher of every
    party; `deductions`: (date, section, TDS booked). One 26Q per quarter; one 26QC per
    194-IB deduction month."""
    groups: dict[tuple[str, date], list[Decimal]] = {}
    for when, section_key, tds, short in items:
        bucket = groups.setdefault(_return_key(when, section_key), [Decimal(0)] * 3)
        bucket[0] += tds
        bucket[1] += short
    for when, section_key, amount in deductions:
        key = _return_key(when, section_key)
        if key in groups:
            groups[key][2] += amount
    out = []
    for (label, due), (tds, short, booked) in sorted(groups.items(), key=lambda kv: kv[0][1]):
        tds, short = round_rupee(tds), round_rupee(short)
        days, fee = late_fee_234e(due, today, tds)
        out.append(ReturnFee(label, due, tds, short, days, fee, round_rupee(booked)))
    return out
