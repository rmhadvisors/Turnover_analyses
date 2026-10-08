"""TDS thresholds, rates and the per-party calculation for ss.194C, 194H, 194J(a)/(b)/(ba),
194Q and 194I(a)/(b) (Income-tax Act 1961 numbering; s.393 of the 2025 Act from 01.04.2026).

Pure functions only - no database - so every rule can be unit tested on its own. The
rates and thresholds themselves are data (the `tds_sections` rate master); the rules that
are law rather than data live here:

- A threshold is crossed only when an amount EXCEEDS it (">", never ">=").
- 194C crosses on either test: one payment above the single-payment limit, or the FY
  aggregate above the annual limit. While only the single-payment test is met, only the
  payments above that limit are liable (s.194C(5)); once the aggregate is crossed the whole
  FY aggregate is.
- 'excess' base (194Q): only the amount above the threshold is liable. 'full' base: the
  whole aggregate once the threshold is crossed.
- A section with no threshold (194J(ba) director remuneration) applies from the first rupee.
- No PAN: the higher of the normal rate and the s.206AA rate.
- Each section key is tracked separately, so 194J(a) and 194J(b) never add up together.
- Amounts are always the value excluding GST shown separately (CBDT Circular 13/2021).
- s.206AB (non-filer higher rate) was omitted w.e.f. 01.04.2025 and is not applied.

Rates are percentages (2 means 2%). TDS is rounded to the nearest rupee, as in the
Applicability Checker sheet (Excel ROUND: half away from zero).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from app.utils.indian_format import format_indian_commas as inr

SECTION_KEYS = (
    "194C", "194H", "194J(a)", "194J(b)", "194J(ba)", "194Q", "194I(a)", "194I(b)", "194-IB",
)  # fmt: skip
RENT_KEYS = ("194I(a)", "194I(b)")
# 'monthly' (s.194-IB): the threshold is rent for a month; TDS once, in the last month,
# on the whole rent of the year / tenancy; without a PAN capped at the last month's rent.
FULL, EXCESS, MONTHLY = "full", "excess", "monthly"
BASES = (FULL, EXCESS, MONTHLY)
INDIVIDUAL_HUF, OTHER = "individual_huf", "other"
PAYEE_TYPES = (INDIVIDUAL_HUF, OTHER)
PAYEE_LABELS = {INDIVIDUAL_HUF: "Individual/HUF", OTHER: "Other"}
DEFAULT_APPROACHING_PCT = Decimal(80)
_RUPEE = Decimal(1)
_HUNDRED = Decimal(100)


def round_rupee(value: Decimal) -> Decimal:
    return value.quantize(_RUPEE, rounding=ROUND_HALF_UP)


def pct_text(rate: Decimal) -> str:
    """2.000 -> '2%', 0.100 -> '0.1%'."""
    text = format(rate.normalize(), "f")
    return f"{text}%"


# ---------------------------------------------------------------- rate master


@dataclass(frozen=True)
class SectionRule:
    """One row of the rate master, effective from `effective_from`."""

    key: str
    section: str
    nature: str
    single_threshold: Decimal  # 0 = no single-payment test
    aggregate_threshold: Decimal  # 0 (with single 0) = no threshold at all
    rate_individual: Decimal
    rate_other: Decimal
    rate_no_pan: Decimal
    base: str  # FULL, EXCESS or MONTHLY
    effective_from: date
    remarks: str = ""

    @property
    def has_threshold(self) -> bool:
        return self.single_threshold > 0 or self.aggregate_threshold > 0


def rule_on(rules: Sequence[SectionRule], key: str, on: date) -> SectionRule | None:
    """The row for `key` in force on `on` (latest effective_from not after it). If every
    row starts later, the earliest row is used rather than none."""
    rows = [r for r in rules if r.key == key]
    if not rows:
        return None
    in_force = [r for r in rows if r.effective_from <= on]
    if in_force:
        return max(in_force, key=lambda r: r.effective_from)
    return min(rows, key=lambda r: r.effective_from)


@dataclass(frozen=True)
class RateChoice:
    rate: Decimal  # percent
    normal_rate: Decimal
    reason: str


def choose_rate(rule: SectionRule, payee_type: str, pan_available: bool) -> RateChoice:
    """Normal rate by payee type; without a PAN the higher of that and the s.206AA rate."""
    individual = payee_type == INDIVIDUAL_HUF
    normal = rule.rate_individual if individual else rule.rate_other
    who = "Individual/HUF payee" if individual else "Other payee (firm, company, etc.)"
    if pan_available:
        return RateChoice(normal, normal, f"{who}, PAN available: normal rate {pct_text(normal)}")
    rate = max(normal, rule.rate_no_pan)
    return RateChoice(
        rate,
        normal,
        f"{who}, PAN NOT available: s.206AA - higher of normal {pct_text(normal)} and "
        f"{pct_text(rule.rate_no_pan)} = {pct_text(rate)}",
    )


# ------------------------------------------------------------ threshold test


class CrossedBy(str, Enum):
    SINGLE = "single payment"
    AGGREGATE = "aggregate"
    BOTH = "single payment and aggregate"
    NO_THRESHOLD = "no threshold"
    MONTHLY = "monthly rent"


def threshold_test(
    rule: SectionRule, largest_single: Decimal, aggregate: Decimal
) -> CrossedBy | None:
    """Which test is met (None = threshold not crossed). Strictly greater than."""
    if not rule.has_threshold:
        return CrossedBy.NO_THRESHOLD if aggregate > 0 else None
    single = rule.single_threshold > 0 and largest_single > rule.single_threshold
    total = rule.aggregate_threshold > 0 and aggregate > rule.aggregate_threshold
    if single and total:
        return CrossedBy.BOTH
    if total:
        return CrossedBy.AGGREGATE
    if single:
        return CrossedBy.SINGLE
    return None


def threshold_text(rule: SectionRule) -> str:
    if not rule.has_threshold:
        return "No threshold"
    if rule.base == MONTHLY:
        return f"rent > {inr(rule.single_threshold)} for a month"
    parts = []
    if rule.single_threshold > 0:
        parts.append(f"single payment > {inr(rule.single_threshold)}")
    if rule.aggregate_threshold > 0:
        parts.append(f"FY aggregate > {inr(rule.aggregate_threshold)}")
    return " OR ".join(parts)


# ------------------------------------------- Applicability Checker (one row)


@dataclass(frozen=True)
class CheckerRow:
    """One row of the 'Applicability Checker' sheet."""

    party: str
    rule: SectionRule
    payee_type: str
    pan_available: bool
    single_payment: Decimal
    aggregate: Decimal
    crossed_by: CrossedBy | None
    base: Decimal
    rate: RateChoice
    tds: Decimal

    @property
    def crossed(self) -> bool:
        return self.crossed_by is not None


def checker_row(
    rule: SectionRule,
    payee_type: str,
    pan_available: bool,
    single_payment: Decimal,
    aggregate: Decimal,
    party: str = "",
) -> CheckerRow:
    """Threshold crossed? -> TDS base -> rate -> TDS, from a single payment and the FY
    aggregate (including that payment), as the sheet does. When only the single-payment
    test of 194C is met, the base is that payment (s.194C(5)), not the aggregate."""
    crossed_by = threshold_test(rule, single_payment, aggregate)
    if crossed_by is None:
        base = Decimal(0)
    elif rule.base == EXCESS:
        base = max(Decimal(0), aggregate - rule.aggregate_threshold)
    elif crossed_by == CrossedBy.SINGLE:
        base = single_payment
    else:
        base = aggregate
    rate = choose_rate(rule, payee_type, pan_available)
    tds = round_rupee(base * rate.rate / _HUNDRED)
    return CheckerRow(
        party,
        rule,
        payee_type,
        pan_available,
        single_payment,
        aggregate,
        crossed_by,
        base,
        rate,
        tds,
    )


# ------------------------------------------------ voucher-level evaluation


@dataclass(frozen=True)
class Payment:
    """One amount credited/paid to a party under one section, excluding GST.
    Positive = expense / purchase; negative = a reversal (debit note, credit)."""

    when: date
    voucher_no: str
    voucher_type: str
    ledger: str
    amount: Decimal
    voucher_key: str = ""


@dataclass
class PaymentLine:
    payment: Payment
    running_total: Decimal
    liable: Decimal = Decimal(0)
    rate: Decimal = Decimal(0)
    is_crossing: bool = False
    # when the TDS on this line became deductible: its own date, or the crossing date for a
    # payment made before the threshold was crossed (194-IB: the last month)
    deductible_on: date | None = None

    @property
    def tds(self) -> Decimal:
        """Unrounded TDS on this line (liable x rate)."""
        return self.liable * self.rate / _HUNDRED


@dataclass
class PartyEvaluation:
    key: str
    rule: SectionRule  # in force at the start of the FY: decides the thresholds
    payee_type: str
    pan_available: bool
    aggregate: Decimal
    largest_single: Decimal
    crossed_by: CrossedBy | None  # tests met on the FY totals
    crossing_test: CrossedBy | None  # test met on the voucher where it was first crossed
    crossed_on: date | None
    crossed_voucher_no: str | None
    crossed_voucher_key: str | None
    base: Decimal
    tds: Decimal
    rate: RateChoice  # the rate in force at the start of the FY (see `rates_used`)
    rates_used: dict[Decimal, Decimal]  # rate -> liable amount at that rate
    lines: list[PaymentLine]
    approaching: bool
    approaching_pct: Decimal
    nil_reason: str | None = None
    deduct_on: date | None = None  # when the TDS falls due (194-IB: the last month)
    months: dict[str, Decimal] | None = None  # 194-IB: rent per month ('2025-04')

    @property
    def crossed(self) -> bool:
        return self.crossed_by is not None

    @property
    def excess_over_threshold(self) -> Decimal:
        return max(Decimal(0), self.aggregate - self.rule.aggregate_threshold)

    @property
    def payment_count(self) -> int:
        return len(self.lines)


def evaluate_payments(
    rules: Sequence[SectionRule],
    key: str,
    fy_start: date,
    payments: Sequence[Payment],
    payee_type: str,
    pan_available: bool,
    approaching_pct: Decimal = DEFAULT_APPROACHING_PCT,
    nil_reason: str | None = None,
) -> PartyEvaluation:
    """Walk one party's payments under one section in date order: running total, the
    voucher on which the threshold was crossed (and by which test), the liable amount of
    every payment, and the TDS (each payment at the rate in force on its date). A
    `nil_reason` (e.g. a transporter's declaration) keeps the test but makes the TDS nil."""
    rule = rule_on(rules, key, fy_start)
    if rule is None:
        raise ValueError(f"No rate master row for section {key}")
    ordered = sorted(payments, key=lambda p: (p.when, p.voucher_no))
    if rule.base == MONTHLY:
        return _evaluate_monthly(rules, rule, ordered, payee_type, pan_available, nil_reason)
    single_limit, total_limit = rule.single_threshold, rule.aggregate_threshold

    lines: list[PaymentLine] = []
    running = Decimal(0)
    crossing: PaymentLine | None = None
    crossing_test: CrossedBy | None = None
    for payment in ordered:
        running += payment.amount
        line = PaymentLine(payment, running)
        lines.append(line)
        if crossing is None:
            crossing_test = threshold_test(rule, payment.amount, running)
            if crossing_test is not None:
                crossing = line

    aggregate = running
    largest = max((p.amount for p in ordered), default=Decimal(0))
    crossed_by = threshold_test(rule, largest, aggregate)
    if crossed_by is None:
        crossing = crossing_test = None
    elif crossed_by == CrossedBy.SINGLE and crossing_test != CrossedBy.SINGLE:
        # the aggregate went over and came back under: the first single payment over the limit
        crossing = next(line for line in lines if line.payment.amount > single_limit)
        crossing_test = CrossedBy.SINGLE
    if crossing is not None:
        crossing.is_crossing = True

    # liable amount of each payment
    if crossed_by is not None:
        before = Decimal(0)
        for line in lines:
            if rule.base == EXCESS:
                line.liable = max(Decimal(0), line.running_total - total_limit) - max(
                    Decimal(0), before - total_limit
                )
            elif crossed_by == CrossedBy.SINGLE:
                line.liable = (
                    line.payment.amount if line.payment.amount > single_limit else Decimal(0)
                )
            else:
                line.liable = line.payment.amount
            before = line.running_total

    rates_used: dict[Decimal, Decimal] = {}
    exact = Decimal(0)
    for line in lines:
        dated_rule = rule_on(rules, key, line.payment.when) or rule
        line.rate = (
            Decimal(0) if nil_reason else choose_rate(dated_rule, payee_type, pan_available).rate
        )
        if line.liable:
            rates_used[line.rate] = rates_used.get(line.rate, Decimal(0)) + line.liable
            exact += line.liable * line.rate / _HUNDRED
    base = sum((line.liable for line in lines), Decimal(0))
    if crossing is not None:
        for line in lines:
            if line.liable:
                line.deductible_on = max(line.payment.when, crossing.payment.when)
    approaching = (
        crossed_by is None
        and total_limit > 0
        and aggregate >= total_limit * approaching_pct / _HUNDRED
    )
    return PartyEvaluation(
        key=key,
        rule=rule,
        payee_type=payee_type,
        pan_available=pan_available,
        aggregate=aggregate,
        largest_single=largest,
        crossed_by=crossed_by,
        crossing_test=crossing_test,
        crossed_on=crossing.payment.when if crossing else None,
        crossed_voucher_no=crossing.payment.voucher_no if crossing else None,
        crossed_voucher_key=crossing.payment.voucher_key if crossing else None,
        base=base,
        tds=round_rupee(exact),
        rate=choose_rate(rule, payee_type, pan_available),
        rates_used=rates_used,
        lines=lines,
        approaching=approaching,
        approaching_pct=approaching_pct,
        nil_reason=nil_reason,
        deduct_on=crossing.payment.when if crossing else None,
    )


def _evaluate_monthly(
    rules: Sequence[SectionRule],
    rule: SectionRule,
    ordered: list[Payment],
    payee_type: str,
    pan_available: bool,
    nil_reason: str | None,
) -> PartyEvaluation:
    """s.194-IB: crossed when the rent for any month exceeds the monthly limit; TDS once, in
    the last month of the tenancy / FY, on the whole rent, at the rate then in force. With
    no PAN: the higher rate, but never more than the last month's rent."""
    months: dict[str, Decimal] = {}
    lines: list[PaymentLine] = []
    running = Decimal(0)
    crossing: PaymentLine | None = None
    for payment in ordered:
        running += payment.amount
        month = payment.when.strftime("%Y-%m")
        months[month] = months.get(month, Decimal(0)) + payment.amount
        line = PaymentLine(payment, running)
        lines.append(line)
        if crossing is None and months[month] > rule.single_threshold:
            crossing = line
    aggregate = running
    crossed = crossing is not None and aggregate > 0
    last = ordered[-1].when if ordered else None
    dated = rule_on(rules, rule.key, last) if last else rule
    choice = choose_rate(dated or rule, payee_type, pan_available)
    tds = Decimal(0)
    if crossed:
        crossing.is_crossing = True
        for line in lines:
            line.liable = line.payment.amount
            line.rate = Decimal(0) if nil_reason else choice.rate
            line.deductible_on = last
        if not nil_reason:
            tds = round_rupee(aggregate * choice.rate / _HUNDRED)
            if not pan_available:
                tds = min(tds, months[max(months)])  # capped at the last month's rent
    return PartyEvaluation(
        key=rule.key,
        rule=rule,
        payee_type=payee_type,
        pan_available=pan_available,
        aggregate=aggregate,
        largest_single=max(months.values(), default=Decimal(0)),
        crossed_by=CrossedBy.MONTHLY if crossed else None,
        crossing_test=CrossedBy.MONTHLY if crossed else None,
        crossed_on=crossing.payment.when if crossed else None,
        crossed_voucher_no=crossing.payment.voucher_no if crossed else None,
        crossed_voucher_key=crossing.payment.voucher_key if crossed else None,
        base=aggregate if crossed else Decimal(0),
        tds=tds,
        rate=choice,
        rates_used={choice.rate: aggregate} if crossed and not nil_reason else {},
        lines=lines,
        approaching=False,
        approaching_pct=DEFAULT_APPROACHING_PCT,
        nil_reason=nil_reason,
        deduct_on=last if crossed else None,
        months=months,
    )


# ------------------------------------------- computed vs actually deducted


class TdsStatus(str, Enum):
    BELOW = "tds_below"
    APPROACHING = "tds_approaching"
    NOT_DEDUCTED = "tds_not_deducted"
    SHORT = "tds_short_deducted"
    OK = "tds_ok"
    EXCESS = "tds_excess"
    NIL = "tds_nil"  # crossed, but nil TDS (declaration / certificate / TCS charged)
    # payments with no party ledger whose total is over the threshold: cannot be tested per payee
    UNIDENTIFIED = "tds_unidentified"
    NOT_APPLICABLE = "tds_not_applicable"  # the section does not apply to this payer


STATUS_DISPLAY = {
    TdsStatus.NOT_DEDUCTED: ("\U0001f534", "Threshold crossed & TDS NOT deducted"),
    TdsStatus.SHORT: ("\U0001f7e0", "Threshold crossed & TDS short deducted"),
    TdsStatus.UNIDENTIFIED: ("\U0001f7e0", "Cannot determine - payee unidentified"),
    TdsStatus.EXCESS: ("\U0001f535", "Threshold crossed & TDS EXCESS deducted"),
    TdsStatus.APPROACHING: ("\U0001f7e1", "Approaching threshold"),
    TdsStatus.OK: ("\U0001f7e2", "Threshold crossed & TDS correctly deducted"),
    TdsStatus.NIL: ("\U0001f7e2", "Threshold crossed - nil TDS"),
    TdsStatus.BELOW: ("\u26aa", "Below threshold"),
    TdsStatus.NOT_APPLICABLE: ("\u26aa", "Section does not apply to this client for the year"),
}


TOLERANCE_MIN, TOLERANCE_MAX = Decimal(10), Decimal(100)


def tolerance(payment_count: int) -> Decimal:
    """Rounding slack when comparing with the books: 50 paise per voucher (Tally rounds TDS
    per voucher, the computation once), at least ₹10 and NEVER more than ₹100 - so a real
    difference (a missed relief, a wrong rate) is always reported, in either direction."""
    return min(TOLERANCE_MAX, max(TOLERANCE_MIN, Decimal(payment_count) / 2))


def classify(evaluation: PartyEvaluation, deducted: Decimal) -> TdsStatus:
    if not evaluation.crossed:
        return TdsStatus.APPROACHING if evaluation.approaching else TdsStatus.BELOW
    computed = evaluation.tds
    slack = tolerance(evaluation.payment_count)
    if computed <= 0:
        return TdsStatus.EXCESS if deducted > slack else TdsStatus.NIL
    if deducted <= 0:
        return TdsStatus.NOT_DEDUCTED
    if deducted < computed - slack:
        return TdsStatus.SHORT
    if deducted > computed + slack:
        return TdsStatus.EXCESS
    return TdsStatus.OK


def likely_cause(
    evaluation: PartyEvaluation, deducted: Decimal, rules: Sequence[SectionRule]
) -> str | None:
    """A plain-language guess at why the TDS in the books differs from the computation."""
    ev, computed = evaluation, evaluation.tds
    if deducted <= 0 or abs(deducted - computed) <= tolerance(ev.payment_count):
        return None
    rule, rate = ev.rule, ev.rate.rate
    if rule.base == EXCESS and ev.crossed:
        full = round_rupee(ev.aggregate * rate / _HUNDRED)
        relief = round_rupee(rule.aggregate_threshold * rate / _HUNDRED)
        if abs(deducted - full) <= max(tolerance(ev.payment_count), relief / 4):
            return (
                f"TDS appears to have been deducted on the full value without the "
                f"{inr(rule.aggregate_threshold)} relief: {pct_text(rate)} of {inr(ev.aggregate)} "
                f"= {inr(full)}, close to the {inr(deducted)} in the books. Only the amount above "
                f"{inr(rule.aggregate_threshold)} is taxable u/s 194Q (that relief is worth "
                f"{inr(relief)} of TDS)."
            )
    if ev.base <= 0:
        return None
    implied = deducted / ev.base * _HUNDRED
    candidates: list[tuple[Decimal, str]] = []
    for other in rules:
        for value, who in (
            (other.rate_other, "other payee"),
            (other.rate_individual, "Individual/HUF"),
            (other.rate_no_pan, "no PAN, s.206AA"),
        ):
            if value > 0 and value != rate:
                candidates.append((value, f"the {other.key} rate ({who})"))
    if candidates:
        value, label = min(candidates, key=lambda c: abs(c[0] - implied))
        if abs(implied - value) <= value * Decimal("0.05"):
            return (
                f"TDS in the books is about {pct_text(value)} of the base ({implied:.2f}% "
                f"actual) - {label} - instead of {pct_text(rate)}."
            )
    word = "more" if deducted > computed else "less"
    return (
        f"{inr(abs(deducted - computed))} {word} than computed; the books imply "
        f"{implied:.2f}% of the base against {pct_text(rate)}."
    )


# ---------------------------------------------- PAN / payer constitution

_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
PAN_CONSTITUTION = {
    "P": "Individual",
    "H": "HUF",
    "F": "Firm",
    "C": "Company",
    "A": "AOP",
    "T": "Trust",
    "B": "BOI",
    "L": "Local authority",
    "J": "Artificial juridical person",
    "G": "Government",
}
CONSTITUTIONS = ("Individual", "HUF", "Firm", "LLP", "Company", "AOP", "BOI", "Trust", "Other")


def valid_pan(pan: str | None) -> str | None:
    value = (pan or "").strip().upper()
    return value if _PAN.match(value) else None


def pan_from_gstin(gstin: str | None) -> str | None:
    """Characters 3-12 of a GSTIN are the holder's PAN: 27ABFPL5804R1Z4 -> ABFPL5804R."""
    value = (gstin or "").strip().upper()
    return valid_pan(value[2:12]) if len(value) == 15 else None


def constitution_from_pan(pan: str | None) -> str | None:
    """The PAN's 4th character is the holder type (P = Individual, F = Firm, ...)."""
    value = valid_pan(pan)
    return PAN_CONSTITUTION.get(value[3]) if value else None


def is_individual_or_huf(constitution: str | None) -> bool:
    return constitution in ("Individual", "HUF")


def payee_type_from_pan(pan: str | None) -> str | None:
    constitution = constitution_from_pan(pan)
    if constitution is None:
        return None
    return INDIVIDUAL_HUF if is_individual_or_huf(constitution) else OTHER


# --------------------------------------------------- who must deduct (payer)

BUYER_194Q_TURNOVER = Decimal(100_000_000)  # ₹10 crore in the immediately preceding FY
AUDIT_LIMIT_BUSINESS = Decimal(10_000_000)  # 44AB: ₹1 crore
AUDIT_LIMIT_BUSINESS_CASH = Decimal(100_000_000)  # ₹10 crore when cash is within 5%
AUDIT_LIMIT_PROFESSION = Decimal(5_000_000)  # ₹50 lakh gross receipts
CANNOT_DETERMINE = "Cannot determine - previous year's turnover not imported"


@dataclass(frozen=True)
class Decision:
    applies: bool | None  # None = cannot determine
    reason: str
    needs_confirmation: bool = False


@dataclass(frozen=True)
class PayerApplicability:
    fy: str
    previous_fy: str
    previous_turnover: Decimal | None
    constitution: str | None
    constitution_source: str
    audit_liable: bool | None
    audit_source: str
    s194q: Decision
    others: Decision  # 194C / 194H / 194J / 194I
    s194ib: Decision = Decision(None, "")  # rent by an Individual/HUF not liable to audit

    def decision(self, key: str) -> Decision:
        if key == "194Q":
            return self.s194q
        if key == "194-IB":
            return self.s194ib
        return self.others

    @property
    def rent_under_194ib(self) -> bool:
        """Rent is tested u/s 194-IB instead of 194I (Individual/HUF not liable to audit)."""
        return self.s194ib.applies is True


def _crore(amount: Decimal) -> str:
    return f"{inr(amount / Decimal(10_000_000))} Cr"


def audit_suggestion(
    previous_turnover: Decimal | None, nature: str | None, cash_within_5pct: bool | None
) -> tuple[bool | None, bool, str]:
    """(likely liable to 44AB audit, certain?, why) from last year's turnover alone."""
    if previous_turnover is None:
        return None, False, CANNOT_DETERMINE
    t = _crore(previous_turnover)
    if nature == "profession":
        liable = previous_turnover > AUDIT_LIMIT_PROFESSION
        return liable, False, f"gross receipts {t} vs the ₹0.50 Cr limit for a profession"
    if previous_turnover > AUDIT_LIMIT_BUSINESS_CASH:
        return True, True, f"turnover {t} exceeds ₹10 Cr, so liable under either 44AB limit"
    if previous_turnover > AUDIT_LIMIT_BUSINESS:
        if cash_within_5pct is False:
            return True, True, f"turnover {t} exceeds ₹1 Cr (cash not within 5%)"
        return (
            True,
            False,
            f"turnover {t} exceeds ₹1 Cr but not ₹10 Cr - liable unless cash receipts and "
            f"payments were each within 5% (then the limit is ₹10 Cr)",
        )
    return False, False, f"turnover {t} is below ₹1 Cr (audit can still apply, e.g. u/s 44AD(4))"


def payer_applicability(
    fy: str,
    previous_fy: str,
    previous_turnover: Decimal | None,
    constitution: str | None,
    constitution_source: str,
    audit_override: bool | None = None,
    nature: str | None = None,
    cash_within_5pct: bool | None = None,
) -> PayerApplicability:
    """Whether the client must deduct, per section family, for `fy`, from the preceding
    year's turnover. 194Q: buyer's turnover > ₹10 Cr. Others: always for a non-individual;
    for an Individual/HUF only if liable to tax audit u/s 44AB in the preceding FY."""
    if previous_turnover is None:
        s194q = Decision(None, CANNOT_DETERMINE)
    elif previous_turnover > BUYER_194Q_TURNOVER:
        s194q = Decision(
            True, f"FY {previous_fy} turnover {_crore(previous_turnover)} exceeds ₹10 Cr"
        )
    else:
        s194q = Decision(
            False, f"FY {previous_fy} turnover {_crore(previous_turnover)} does not exceed ₹10 Cr"
        )

    audit_liable: bool | None = None
    audit_source = "not needed"
    if constitution is None:
        others = Decision(
            None, "Cannot determine - the client's constitution (PAN / GSTIN) is not known"
        )
    elif not is_individual_or_huf(constitution):
        others = Decision(True, f"{constitution}: a non-individual payer always deducts")
    elif audit_override is not None:
        audit_liable, audit_source = audit_override, "confirmed by user"
        others = Decision(
            audit_override,
            f"{constitution}: {'liable' if audit_override else 'not liable'} to tax audit u/s 44AB "
            f"in FY {previous_fy} (confirmed)",
        )
    else:
        likely, certain, why = audit_suggestion(previous_turnover, nature, cash_within_5pct)
        audit_liable = likely
        audit_source = "derived from turnover" if certain else "suggested - please confirm"
        if likely is None:
            others = Decision(None, f"{constitution}: deducts only if liable to audit u/s 44AB in "
                                    f"FY {previous_fy} - {CANNOT_DETERMINE}", True)  # fmt: skip
        else:
            verdict = "liable" if likely else "not liable"
            others = Decision(
                likely,
                f"{constitution}: deducts only if liable to tax audit u/s 44AB in FY {previous_fy}. "
                f"{'Liable' if certain else f'Probably {verdict}'}: {why}."
                + ("" if certain else " Was the client liable to audit? Please confirm."),
                needs_confirmation=not certain,
            )
    if is_individual_or_huf(constitution) and others.applies is False:
        s194ib = Decision(
            True,
            f"{constitution} not liable to audit u/s 44AB in FY {previous_fy}: rent above "
            f"₹50,000 a month falls under s.194-IB (2%, deducted once a year) instead of 194I",
        )
    elif others.applies is True:
        s194ib = Decision(False, "The client deducts under 194I, so s.194-IB does not apply")
    else:
        s194ib = Decision(None, others.reason)
    return PayerApplicability(
        fy=fy,
        previous_fy=previous_fy,
        previous_turnover=previous_turnover,
        constitution=constitution,
        constitution_source=constitution_source,
        audit_liable=audit_liable,
        audit_source=audit_source,
        s194q=s194q,
        others=others,
        s194ib=s194ib,
    )


@dataclass
class SectionTotals:
    """Report subtotal for one section."""

    key: str
    parties: int = 0
    crossed: int = 0
    aggregate: Decimal = Decimal(0)
    tds: Decimal = Decimal(0)
    deducted: Decimal = Decimal(0)
    shortfall: Decimal = Decimal(0)


# ------------------------------------------------- data quality: PAN vs name

# a proprietor often trades as '... & Co' / '& Associates': worth a check, not a contradiction
_TRADE_NAME_HINT = "'& Associates' / '& Co'"
_NAME_CONSTITUTION = (
    (re.compile(r"\bLLP\b|LIMITED LIABILITY PARTNERSHIP"), "Firm", "LLP"),
    (re.compile(r"\b(PVT|PRIVATE|PUBLIC)\b.*\b(LTD|LIMITED)\b|\b(LTD|LIMITED)\b"), "Company",
     "Pvt Ltd / Ltd"),
    (re.compile(r"(&|\bAND)\s*(ASSOCIATES|CO\b|CO\.|COMPANY|PARTNERS)"), "Firm",
     _TRADE_NAME_HINT),
    (re.compile(r"\bTRUST\b"), "Trust", "Trust"),
    (re.compile(r"\bHUF\b"), "HUF", "HUF"),
)  # fmt: skip
WRONG_PAN_NOTE = (
    "If this PAN is not the payee's own, the 26Q return quotes a wrong PAN: the deductee is "
    "treated as having no PAN, so TDS is due at 20% (s.206AA) and the payee gets no credit."
)


# co-operative societies ('... Sahakari Society Ltd') hold AOP / 'J' PANs: not a mismatch
_COOPERATIVE = re.compile(r"CO-?\s?OP|COOPERATIVE|SAHAKARI|SOCIETY|\bSOC\b|SANSTHA|SANGH|MANDAL")


def pan_name_level(name: str, pan: str | None) -> str | None:
    """'contradiction' (Pvt Ltd / LLP / Trust / HUF with another holder type), 'check'
    ('& Co' / '& Associates' with an individual's PAN - may be a proprietor's trade name)."""
    problem = pan_name_mismatch(name, pan)
    if problem is None:
        return None
    return "check" if _TRADE_NAME_HINT in problem else "contradiction"


def pan_name_mismatch(name: str, pan: str | None) -> str | None:
    """Why a PAN looks wrong for a party's name (its 4th character contradicts the entity
    type the name implies), or None."""
    holder = constitution_from_pan(pan)
    if holder is None:
        return None
    upper = " ".join(name.upper().split())
    if _COOPERATIVE.search(upper):
        return None
    for pattern, expected, hint in _NAME_CONSTITUTION:
        if pattern.search(upper):
            if holder == expected:
                return None
            return (
                f"The name ({hint}) implies a {expected}, but PAN {pan} has '{pan[3]}' as its "
                f"4th character = {holder}."
            )
    return None
