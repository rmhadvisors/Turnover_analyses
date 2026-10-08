"""Party-wise TDS analysis of one client and FY, from the captured Tally ledger lines, and
the TDS alerts raised from it.

For every voucher: the amounts posted to ledgers mapped to a section (the base, GST
excluded because GST sits in its own ledgers) are added to that voucher's party; credits
to TDS ledgers mapped to a section are the TDS the client actually deducted. TDS booked
in a voucher with no party (e.g. a monthly lump-sum journal) cannot be tied to a party: it
is shown as such: attributed to a party only when it is the one party over the section's
threshold (see `_attribute_pools`), otherwise left unallocated.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, TdsEntry, TdsLedger, TdsLedgerMap
from app.repositories import (
    alert_repo,
    client_repo,
    figures_repo,
    profile_repo,
    tds_repo,
    threshold_repo,
)
from app.services import tally_json as tj
from app.services import tds_compliance as tc
from app.services import tds_engine as te
from app.services import tds_mapping as tm
from app.services.alert_engine import AlertEvent, notify
from app.services.fy_utils import fy_bounds, next_fy, previous_fy
from app.services.tds_capture import BANK_CASH, PARTY
from app.services.tds_engine import TdsStatus
from app.utils.indian_format import format_indian_commas as inr

NO_PARTY_KEY = "(no party)"
NO_PARTY_NAME = "Payments with no party ledger (cash / bank)"
SYSTEM_CLOSED = "System - TDS status no longer applies"


# ------------------------------------------------------------------ mapping


@dataclass(frozen=True)
class MappedTo:
    role: str
    section_key: str | None
    via: str  # 'ledger' or "group 'X'"


class Mapper:
    """Resolves a ledger to its mapping: its own rule, else the nearest group's rule."""

    def __init__(self, rows: list[TdsLedgerMap]):
        self.ledger = {r.name_key: r for r in rows if r.match_type == "ledger"}
        self.group = {r.name_key: r for r in rows if r.match_type == "group"}
        self._memo: dict[str, MappedTo | None] = {}

    def resolve(self, ledger_key: str, groups: list[str]) -> MappedTo | None:
        if ledger_key not in self._memo:
            self._memo[ledger_key] = self._resolve(ledger_key, groups)
        return self._memo[ledger_key]

    def _resolve(self, ledger_key: str, groups: list[str]) -> MappedTo | None:
        own = self.ledger.get(ledger_key)
        if own is not None and own.role != tm.UNMAPPED:
            return MappedTo(own.role, own.section_key, "ledger")
        for group in groups:
            rule = self.group.get(tj._key(group))
            if rule is not None and rule.role != tm.UNMAPPED:
                return MappedTo(rule.role, rule.section_key, f"group '{group}'")
        return None


def mapping_rows(db: Session, client_id: int) -> list[TdsLedgerMap]:
    return list(db.scalars(select(TdsLedgerMap).where(TdsLedgerMap.client_id == client_id)))


def ledgers_for(db: Session, client_id: int, fy: str) -> dict[str, TdsLedger]:
    """The FY's Master ledgers; a ledger missing there is taken from the nearest other year."""
    rows = list(db.scalars(select(TdsLedger).where(TdsLedger.client_id == client_id)))
    rows.sort(key=lambda r: (r.fy == fy, r.fy))  # this FY's rows last, so they win
    return {r.name_key: r for r in rows}


# ------------------------------------------------------------------ parties


@dataclass
class PartyInfo:
    key: str
    name: str
    guid: str | None = None
    gstin: str | None = None
    pan: str | None = None
    pan_source: str = "not available"
    payee_type: str = te.OTHER
    payee_source: str = "assumed (no PAN)"
    deductee_type: str | None = None
    tally_transporter: bool = False
    transporter_declaration: bool = False
    tcs_206c1h: bool = False
    note: str | None = None
    tcs_vouchers: int = 0
    tcs_amount: Decimal = Decimal(0)
    identified: bool = True
    assigned: bool = False  # a payee the CA assigned to party-less payments
    pan_warning: str | None = None  # the PAN contradicts the name (data quality)

    @property
    def pan_available(self) -> bool:
        return self.pan is not None


def _party_info(key: str, ledger: TdsLedger | None, name: str, user) -> PartyInfo:
    info = PartyInfo(key=key, name=name)
    if ledger is not None:
        info.guid, info.gstin = ledger.guid, ledger.gstin
        info.deductee_type, info.tally_transporter = ledger.deductee_type, ledger.is_transporter
    entered = te.valid_pan(user.pan) if user is not None else None
    from_gstin = te.pan_from_gstin(info.gstin)
    tally_pan = ledger.tally_pan if ledger is not None else None
    if entered:
        info.pan, info.pan_source = entered, "entered by user"
    elif from_gstin:
        info.pan, info.pan_source = from_gstin, "from GSTIN"
    elif tally_pan:
        info.pan, info.pan_source = tally_pan, "from Tally ledger PAN field"
    if user is not None and user.payee_type in te.PAYEE_TYPES:
        info.payee_type, info.payee_source = user.payee_type, "set by user"
    elif info.pan:
        info.payee_type = te.payee_type_from_pan(info.pan) or te.OTHER
        constitution = te.constitution_from_pan(info.pan)
        info.payee_source = f"PAN 4th character '{info.pan[3]}' = {constitution}"
    elif info.deductee_type:
        individual = any(w in info.deductee_type.casefold() for w in ("individual", "huf"))
        info.payee_type = te.INDIVIDUAL_HUF if individual else te.OTHER
        info.payee_source = f"Tally deductee type '{info.deductee_type}'"
    if user is not None:
        info.transporter_declaration = bool(user.transporter_declaration)
        info.tcs_206c1h = bool(user.tcs_206c1h)
        info.note = user.note
    info.pan_warning = te.pan_name_mismatch(name, info.pan)
    return info


def party_key_for(ledger: TdsLedger | None, name_key: str) -> str:
    if ledger is not None and ledger.guid:
        return ledger.guid
    return f"name:{name_key}"


# --------------------------------------------------------------- the analysis


@dataclass
class DeductionLine:
    when: date
    voucher_no: str | None
    voucher_type: str | None
    ledger: str
    amount: Decimal  # credit to the TDS ledger (+); a reversal is negative


@dataclass(frozen=True)
class Alternative:
    """What the TDS would be under a section, assuming one payee with a PAN (shown for
    payments whose payee is unknown, and for rent while 194I vs 194-IB is unconfirmed)."""

    section_key: str
    crossed: bool
    rate: Decimal
    tds: Decimal
    test: str


@dataclass
class PartySection:
    party: PartyInfo
    section_key: str
    evaluation: te.PartyEvaluation
    decision: te.Decision
    deducted_in_books: Decimal = Decimal(0)
    # party-less TDS of the section given to the only party over its threshold (counted)
    deducted_attributed: Decimal = Decimal(0)
    # party-less TDS shared pro rata between several short parties (shown, NOT counted)
    deducted_allocated: Decimal = Decimal(0)
    deducted_outside: Decimal | None = None
    outside_note: str | None = None
    deductions: list[DeductionLine] = field(default_factory=list)
    excluded_payments: list[te.Payment] = field(default_factory=list)  # 206C(1H) vouchers
    mapped_section: str | None = None  # 194I(a)/(b) when the row is tested u/s 194-IB
    alternatives: list[Alternative] = field(default_factory=list)
    provisional: bool = False  # a ledger behind it still awaits the CA's 194I(a)/(b) choice
    status: str = TdsStatus.BELOW.value
    exposure: tc.Exposure | None = None  # estimated cost as of today (None = nothing at stake)

    @property
    def at_stake(self) -> Decimal:
        return self.exposure.at_stake if self.exposure is not None else Decimal(0)

    @property
    def deducted(self) -> Decimal:
        """TDS counted against the computation (a pro-rata allocation is not)."""
        total = self.deducted_in_books + self.deducted_attributed
        if self.deducted_outside is not None:
            total += self.deducted_outside
        return total

    @property
    def shortfall(self) -> Decimal:
        if not self.evaluation.crossed or not self.party.identified:
            return Decimal(0)
        return max(Decimal(0), self.evaluation.tds - self.deducted)

    @property
    def excess(self) -> Decimal:
        if not self.evaluation.crossed or not self.party.identified:
            return Decimal(0)
        return max(Decimal(0), self.deducted - self.evaluation.tds)

    @property
    def alertable(self) -> bool:
        return self.decision.applies is not False


@dataclass
class SectionPool:
    """TDS booked to a section's TDS ledger in vouchers without a party."""

    section_key: str
    amount: Decimal = Decimal(0)
    attributed: Decimal = Decimal(0)  # to the only party over the threshold (counted)
    attributed_to: str | None = None
    allocated: Decimal = Decimal(0)  # pro rata between several parties (not counted)
    lines: list[DeductionLine] = field(default_factory=list)

    @property
    def unallocated(self) -> Decimal:
        """Not tied to any party: everything except what was attributed by elimination."""
        return self.amount - self.attributed


@dataclass
class UnmappedLedger:
    name: str
    kind: str
    groups: list[str]
    amount: Decimal  # net debit (+) in the FY
    lines: int


@dataclass
class LedgerStat:
    """One expense / purchase ledger in the FY (for the completeness view)."""

    name: str
    kind: str
    total: Decimal = Decimal(0)  # net debit
    lines: int = 0
    parties: set[str] = field(default_factory=set)
    sections: set[str] = field(default_factory=set)


@dataclass
class ClientTds:
    client_id: int
    client_name: str
    fy: str
    payer: te.PayerApplicability
    rows: list[PartySection]
    pools: dict[str, SectionPool]
    unmapped: list[UnmappedLedger]
    mapping_approved: bool
    has_data: bool
    approaching_pct: Decimal
    rules: list[te.SectionRule] = field(default_factory=list)
    materiality: Decimal = Decimal(30000)  # no-party payments above this are alerted
    ledger_stats: dict[str, LedgerStat] = field(default_factory=dict)
    return_fees: list[tc.ReturnFee] = field(default_factory=list)
    deposits: dict[str, Decimal] = field(default_factory=dict)
    pending_choice: list[str] = field(default_factory=list)  # rent ledgers awaiting a choice

    def row(self, party_key: str, section_key: str) -> PartySection | None:
        return next(
            (r for r in self.rows if r.party.key == party_key and r.section_key == section_key),
            None,
        )


def analysis_fy(db: Session) -> str:
    """The one FY TDS is analysed and alerted for (Settings -> TDS)."""
    return threshold_repo.get_settings(db).tds_analysis_fy or "2025-26"


def payer_for(db: Session, client_id: int, fy: str) -> te.PayerApplicability:
    prev = previous_fy(fy)
    figures = figures_repo.get_figures(db, client_id, prev)
    turnover = figures.turnover if figures is not None else None
    settings = tds_repo.client_settings(db, client_id)
    profile = profile_repo.get_profile(db, client_id)
    constitution, source = None, "unknown"
    if settings.constitution:
        constitution, source = settings.constitution, "set by user"
    elif profile is not None and te.pan_from_gstin(profile.gstin):
        pan = te.pan_from_gstin(profile.gstin)
        constitution = te.constitution_from_pan(pan)
        source = f"PAN {pan} from GSTIN {profile.gstin}: 4th character '{pan[3]}'"
    elif profile is not None and profile.entity_type:
        constitution = {"individual": "Individual", "huf": "HUF", "firm": "Firm", "llp": "LLP",
                        "company": "Company"}.get(profile.entity_type, "Other")  # fmt: skip
        source = "client profile"
    override = tds_repo.payer_year(db, client_id, fy)
    return te.payer_applicability(
        fy,
        prev,
        turnover,
        constitution,
        source,
        audit_override=override.audit_liable if override is not None else None,
        nature=getattr(profile, "nature", None),
        cash_within_5pct=getattr(profile, "cash_within_5pct", None),
    )


def _assigned_party(key: str, assignment) -> PartyInfo:
    info = PartyInfo(key=key, name=assignment.payee_name, assigned=True)
    pan = te.valid_pan(assignment.pan)
    if pan:
        info.pan, info.pan_source = pan, "entered by user (assigned payee)"
    if assignment.payee_type in te.PAYEE_TYPES:
        info.payee_type, info.payee_source = assignment.payee_type, "set by user"
    elif pan:
        info.payee_type = te.payee_type_from_pan(pan) or te.OTHER
        info.payee_source = f"PAN 4th character '{pan[3]}' = {te.constitution_from_pan(pan)}"
    info.note = assignment.note
    info.pan_warning = te.pan_name_mismatch(info.name, info.pan)
    return info


def base_not_covered(row: PartySection) -> Decimal:
    """The part of the base on which TDS was not deducted (for s.40(a)(ia))."""
    tds = row.evaluation.tds
    if tds <= 0 or row.shortfall <= 0:
        return Decimal(0)
    return (row.evaluation.base * row.shortfall / tds).quantize(Decimal("0.01"))


def _voucher_tds(row: PartySection) -> list[tuple[date, Decimal]]:
    """(date it became deductible, TDS) for every liable voucher of the row."""
    return [
        (line.deductible_on, line.tds)
        for line in row.evaluation.lines
        if line.liable and line.deductible_on
    ]


def _exposure(row: PartySection, today: date) -> tc.Exposure | None:
    ev = row.evaluation
    items = _voucher_tds(row)
    if row.status == UNIDENTIFIED:
        return tc.exposure(ev.tds, "indicative TDS, as if one payee with a PAN", items,
                           ev.aggregate, row.section_key, today)  # fmt: skip
    if not row.party.identified or row.decision.applies is False:
        return None
    if row.shortfall > 0:
        return tc.exposure(row.shortfall, "TDS not deducted", items, base_not_covered(row),
                           row.section_key, today)  # fmt: skip
    if row.status == TdsStatus.EXCESS.value:
        return tc.exposure(Decimal(0), "TDS excess deducted", items, Decimal(0),
                           row.section_key, today, excess=row.excess)  # fmt: skip
    return None


def _return_fees(
    rows: list[PartySection], pools: dict[str, SectionPool], today: date
) -> list[tc.ReturnFee]:
    """s.234E per return: every identified party's TDS, by the period of its return."""
    items = []
    for row in rows:
        if not row.party.identified or row.decision.applies is False or not row.evaluation.crossed:
            continue
        tds = row.evaluation.tds
        share = row.shortfall / tds if tds > 0 else Decimal(0)
        for when, line_tds in _voucher_tds(row):
            items.append((when, row.section_key, line_tds, line_tds * share))
    booked = [(d.when, row.section_key, d.amount) for row in rows for d in row.deductions]
    booked += [(d.when, key, d.amount) for key, pool in pools.items() for d in pool.lines]
    return tc.return_fees(items, today, booked)


def cross_section_cause(row: PartySection, rows: list[PartySection]) -> str | None:
    """Excess TDS that matches the rate on this party's bills booked under ANOTHER section
    (e.g. a professional bill posted to a purchase ledger): the expense is understated,
    not the TDS overstated."""
    if row.excess <= 0 or not row.party.identified:
        return None
    rate = row.evaluation.rate.rate
    slack = te.tolerance(row.evaluation.payment_count)
    others = [r for r in rows if r.party.key == row.party.key and r.section_key != row.section_key]
    for other in others:
        for line in other.evaluation.lines:
            p = line.payment
            if abs(p.amount * rate / 100 - row.excess) <= slack:
                return (
                    f"The excess {inr(row.excess)} is {te.pct_text(rate)} of {inr(p.amount)} - this "
                    f"party's bill of {p.when:%d-%b-%Y} ({p.voucher_type} {p.voucher_no}) booked in "
                    f"'{p.ledger}', i.e. under {other.section_key}. The expense is probably booked "
                    f"to the wrong ledger (TDS was deducted on it at the {row.section_key} rate), "
                    f"not over-deducted. Move that voucher to {row.section_key} in the detail view."
                )
        total = other.evaluation.aggregate
        if total > 0 and abs(total * rate / 100 - row.excess) <= slack:
            return (
                f"The excess {inr(row.excess)} is {te.pct_text(rate)} of {inr(total)} that this party "
                f"billed under {other.section_key}: the expense is probably booked to the wrong ledger."
            )
    return None


def analyze(db: Session, client_id: int, fy: str, today: date | None = None) -> ClientTds:
    """Party-wise TDS of one client for `fy`, from that FY's vouchers only (1 April to 31
    March: nothing is carried across years). The year before is used only to decide who
    must deduct (194Q turnover test, 44AB audit test)."""
    client = client_repo.get_client(db, client_id)
    rules = tds_repo.rules(db)
    fy_start, _ = fy_bounds(fy)
    today = today or date.today()
    global_settings = threshold_repo.get_settings(db)
    approaching_pct = global_settings.tds_approaching_pct or Decimal(80)
    materiality = global_settings.tds_unidentified_min
    materiality = Decimal(30000) if materiality is None else materiality
    settings = tds_repo.client_settings(db, client_id)
    payer = payer_for(db, client_id, fy)
    ledgers = ledgers_for(db, client_id, fy)
    map_rows = mapping_rows(db, client_id)
    mapper = Mapper(map_rows)
    pending = {r.name_key: r.name for r in map_rows if r.requires_choice and r.source == "auto"}
    users = tds_repo.parties(db, client_id)
    flags = tds_repo.voucher_flags(db, client_id)
    resolutions = tds_repo.resolutions(db, client_id, fy)
    assignments = tds_repo.assignments(db, client_id, fy)

    # plain rows, not ORM objects: a busy client has ~50,000 lines a year
    entries = db.execute(
        select(
            TdsEntry.voucher_key, TdsEntry.voucher_date, TdsEntry.voucher_type,
            TdsEntry.voucher_no, TdsEntry.party_ledger, TdsEntry.ledger,
            TdsEntry.ledger_key, TdsEntry.amount,
        )
        .where(TdsEntry.client_id == client_id, TdsEntry.fy == fy)
        .order_by(TdsEntry.voucher_date, TdsEntry.voucher_key, TdsEntry.line_no)
    ).all()  # fmt: skip
    by_voucher: dict[str, list] = defaultdict(list)
    for entry in entries:
        by_voucher[entry.voucher_key].append(entry)

    kinds = {key: ledger.kind for key, ledger in ledgers.items()}
    group_lists = {key: list(ledger.groups or []) for key, ledger in ledgers.items()}

    def kind(key: str) -> str:
        return kinds.get(key, "other")

    def groups(key: str) -> list[str]:
        return group_lists.get(key, [])

    def effective(section_key: str) -> str:
        """Rent paid by an Individual/HUF not liable to audit is tested u/s 194-IB."""
        if section_key in te.RENT_KEYS and payer.rent_under_194ib:
            return "194-IB"
        return section_key

    payments: dict[tuple[str, str], list[te.Payment]] = defaultdict(list)
    excluded: dict[tuple[str, str], list[te.Payment]] = defaultdict(list)
    deductions: dict[tuple[str, str], list[DeductionLine]] = defaultdict(list)
    mapped_sections: dict[tuple[str, str], str] = {}
    provisional: set[tuple[str, str]] = set()
    pools: dict[str, SectionPool] = {}
    deposits: dict[str, Decimal] = defaultdict(Decimal)
    parties: dict[str, PartyInfo] = {}
    tcs_hits: dict[str, list[Decimal]] = defaultdict(list)
    unmapped: dict[str, UnmappedLedger] = {}
    ledger_stats: dict[str, LedgerStat] = {}

    def party_of(lines: list) -> str | None:
        tally_party = lines[0].party_ledger
        if tally_party and kind(tj._key(tally_party)) == PARTY:
            return tj._key(tally_party)
        candidates = [line for line in lines if kind(line.ledger_key) == PARTY]
        if not candidates:
            return None
        return max(candidates, key=lambda line: abs(line.amount)).ledger_key

    for key, lines in by_voucher.items():
        head = lines[0]
        party_key = party_of(lines)
        has_bank = any(kind(line.ledger_key) == BANK_CASH for line in lines)
        if party_key is not None:
            ledger = ledgers.get(party_key)
            pkey = party_key_for(ledger, party_key)
            if pkey not in parties:
                name = (
                    ledger.name
                    if ledger is not None
                    else next(line.ledger for line in lines if line.ledger_key == party_key)
                )
                parties[pkey] = _party_info(pkey, ledger, name, users.get(pkey))
        else:
            assigned = next(
                (assignments[x.ledger_key] for x in lines if x.ledger_key in assignments), None
            )
            if assigned is not None:
                pkey = f"assigned:{tj._key(assigned.payee_name)}"
                parties.setdefault(pkey, _assigned_party(pkey, assigned))
            else:
                pkey = NO_PARTY_KEY
        base: dict[str, Decimal] = defaultdict(Decimal)
        base_ledgers: dict[str, list[str]] = defaultdict(list)
        for line in lines:
            line_kind = kind(line.ledger_key)
            mapped = mapper.resolve(line.ledger_key, groups(line.ledger_key))
            if line_kind in (tm.EXPENSE, tm.PURCHASE):
                stat = ledger_stats.setdefault(line.ledger_key, LedgerStat(line.ledger, line_kind))
                stat.total -= line.amount
                stat.lines += 1
                stat.parties.add(pkey)
            if line_kind == tm.TCS_LEDGER and line.amount < 0 and pkey != NO_PARTY_KEY:
                tcs_hits[pkey].append(-line.amount)
            if mapped is None:
                if line_kind in tm.MAPPABLE_KINDS and line_kind != tm.TCS_LEDGER:
                    item = unmapped.setdefault(
                        line.ledger_key,
                        UnmappedLedger(
                            line.ledger, line_kind, groups(line.ledger_key), Decimal(0), 0
                        ),
                    )
                    item.amount -= line.amount
                    item.lines += 1
                continue
            if not mapped.section_key or mapped.role not in (tm.BASE, tm.TDS):
                continue
            section_key = effective(mapped.section_key)
            if section_key != mapped.section_key:
                mapped_sections[(pkey, section_key)] = mapped.section_key
            if line.ledger_key in pending:
                provisional.add((pkey, section_key))
            if mapped.role == tm.BASE:
                moved = flags.get(key)
                if moved is not None and moved.flag.startswith("section:"):
                    section_key = effective(moved.flag.split(":", 1)[1])  # moved by the CA
                if line_kind in (tm.EXPENSE, tm.PURCHASE):
                    stat = ledger_stats.setdefault(
                        line.ledger_key, LedgerStat(line.ledger, line_kind)
                    )
                    stat.sections.add(section_key)
                base[section_key] -= line.amount  # a debit (expense) is negative
                if line.ledger not in base_ledgers[section_key]:
                    base_ledgers[section_key].append(line.ledger)
                continue
            if line.amount < 0 and has_bank:
                deposits[section_key] += -line.amount  # paid to the government
                continue
            deduction = DeductionLine(
                head.voucher_date, head.voucher_no, head.voucher_type, line.ledger, line.amount
            )
            if pkey == NO_PARTY_KEY:
                pool = pools.setdefault(section_key, SectionPool(section_key))
                pool.amount += line.amount
                pool.lines.append(deduction)
            else:
                deductions[(pkey, section_key)].append(deduction)
        for section_key, amount in base.items():
            if amount == 0:
                continue
            payment = te.Payment(
                head.voucher_date,
                head.voucher_no or "",
                head.voucher_type or "",
                ", ".join(base_ledgers[section_key]),
                amount,
                key,
            )
            if section_key == "194Q" and key in flags and flags[key].flag == "tcs_206c1h":
                excluded[(pkey, section_key)].append(payment)
            else:
                payments[(pkey, section_key)].append(payment)

    for pkey, amounts in tcs_hits.items():
        if pkey in parties:
            parties[pkey].tcs_vouchers = len(amounts)
            parties[pkey].tcs_amount = sum(amounts, Decimal(0))
    if any(k[0] == NO_PARTY_KEY for k in payments):
        parties[NO_PARTY_KEY] = PartyInfo(NO_PARTY_KEY, NO_PARTY_NAME, identified=False)

    rows: list[PartySection] = []
    for pair in sorted(set(payments) | set(deductions) | set(excluded)):
        pkey, section_key = pair
        if section_key not in te.SECTION_KEYS:
            continue
        party = parties[pkey]
        nil_reason = None
        if section_key == "194C" and party.transporter_declaration:
            nil_reason = "Transporter (<= 10 goods carriages) has furnished a declaration with PAN: NIL u/s 194C(6)"
        elif section_key == "194Q" and party.tcs_206c1h:
            nil_reason = (
                "Seller collects TCS u/s 206C(1H) on these sales: no 194Q liability for the buyer"
            )
        # an unknown payee is worked out as if it were one payee with a PAN (an indication)
        payee_type = party.payee_type if party.identified else te.OTHER
        pan_available = party.pan_available or not party.identified
        evaluation = te.evaluate_payments(
            rules, section_key, fy_start, payments.get(pair, []), payee_type,
            pan_available, approaching_pct, nil_reason,
        )  # fmt: skip
        row = PartySection(
            party, section_key, evaluation, payer.decision(section_key),
            deductions=deductions.get(pair, []), excluded_payments=excluded.get(pair, []),
            mapped_section=mapped_sections.get(pair), provisional=pair in provisional,
        )  # fmt: skip
        row.deducted_in_books = sum((d.amount for d in row.deductions), Decimal(0))
        rent = section_key in te.RENT_KEYS or section_key == "194-IB"
        if rent and (not party.identified or payer.others.needs_confirmation):
            row.alternatives = _alternatives(rules, row, fy_start, payments.get(pair, []))
        rows.append(row)

    _attribute_pools(rows, pools)
    for row in rows:
        resolution = resolutions.get((row.party.key, row.section_key))
        if resolution is not None:
            missing = max(Decimal(0), row.evaluation.tds - row.deducted)
            row.deducted_outside = resolution.amount if resolution.amount is not None else missing
            row.outside_note = resolution.note
        row.status = _status(row, materiality)
        row.exposure = _exposure(row, today)

    return ClientTds(
        client_id=client_id,
        client_name=client.name if client is not None else "",
        fy=fy,
        payer=payer,
        rows=rows,
        pools=pools,
        unmapped=sorted(
            (u for u in unmapped.values() if u.amount != 0), key=lambda u: -abs(u.amount)
        ),
        mapping_approved=settings.mapping_approved_at is not None,
        has_data=bool(entries),
        approaching_pct=approaching_pct,
        rules=rules,
        materiality=materiality,
        deposits=dict(deposits),
        ledger_stats=ledger_stats,
        return_fees=_return_fees(rows, pools, today),
        pending_choice=sorted(pending.values()),
    )


def _alternatives(rules, row: PartySection, fy_start: date, payments) -> list[Alternative]:
    keys = [row.mapped_section or row.section_key]
    if row.section_key != "194-IB":
        keys.append("194-IB")
    out = []
    for key in dict.fromkeys(keys):
        if te.rule_on(rules, key, fy_start) is None:
            continue
        ev = te.evaluate_payments(rules, key, fy_start, payments, te.OTHER, True)
        test = te.threshold_text(ev.rule)
        out.append(Alternative(key, ev.crossed, ev.rate.rate, ev.tds, test))
    return out


NOT_APPLICABLE = TdsStatus.NOT_APPLICABLE.value
UNIDENTIFIED = TdsStatus.UNIDENTIFIED.value


def _status(row: PartySection, materiality: Decimal) -> str:
    if row.decision.applies is False:
        return NOT_APPLICABLE
    if not row.party.identified:
        # payee(s) unknown: the threshold cannot be tested; material totals are alerted,
        # smaller ones are only listed
        return UNIDENTIFIED if row.evaluation.aggregate > materiality else TdsStatus.BELOW.value
    return te.classify(row.evaluation, row.deducted).value


def _attribute_pools(rows: list[PartySection], pools: dict[str, SectionPool]) -> None:
    """TDS of a section booked with no party (a monthly lump-sum journal):
    - exactly one identified party over the section's threshold: it can only be that
      party's TDS, so all of it is attributed to that party and counted (an over-deduction
      shows as excess);
    - several parties over the threshold: shared pro rata to what each is short, for
      information only - it never turns a 'not deducted' or 'short' into 'correct';
    - none: it stays unallocated."""
    for section_key, pool in pools.items():
        crossed = [
            row
            for row in rows
            if row.section_key == section_key
            and row.party.identified
            and row.evaluation.crossed
            and row.decision.applies is not False
        ]
        if len(crossed) == 1 and pool.amount != 0:
            crossed[0].deducted_attributed = pool.amount
            pool.attributed, pool.attributed_to = pool.amount, crossed[0].party.name
            continue
        short = [(row, row.evaluation.tds - row.deducted_in_books) for row in crossed]
        short = [(row, gap) for row, gap in short if gap > 0]
        total_short = sum((gap for _, gap in short), Decimal(0))
        if pool.amount <= 0 or total_short <= 0:
            continue
        available = min(pool.amount, total_short)
        for row, gap in short:
            row.deducted_allocated = min(
                (available * gap / total_short).quantize(Decimal("0.01")), gap
            )
            pool.allocated += row.deducted_allocated


# ------------------------------------------------------------------ alerts


def metric_key(section_key: str, party_key: str) -> str:
    digest = hashlib.sha1(party_key.encode("utf-8")).hexdigest()[:12]
    return f"tds:{section_key}:{digest}"


def _threshold_amount(evaluation: te.PartyEvaluation) -> Decimal:
    rule = evaluation.rule
    return rule.aggregate_threshold or rule.single_threshold


def _event(row: PartySection, previous: str) -> AlertEvent:
    evaluation = row.evaluation
    label = te.STATUS_DISPLAY[TdsStatus(row.status)][1]
    return AlertEvent(
        metric=metric_key(row.section_key, row.party.key),
        old_status=previous,
        new_status=row.status,
        value=evaluation.aggregate,
        threshold_description=(
            f"TDS {row.section_key} - {row.party.name}: {label} "
            f"({te.threshold_text(evaluation.rule)}"
            + (f"; crossed by {evaluation.crossing_test.value}" if evaluation.crossing_test else "")
            + ")"
        )[:255],
    )


def _fill(alert: Alert, row: PartySection) -> None:
    evaluation = row.evaluation
    alert.party_pan = row.party.pan
    alert.threshold = _threshold_amount(evaluation)
    alert.aggregate, alert.tds_computed = evaluation.aggregate, evaluation.tds
    alert.tds_deducted, alert.shortfall = row.deducted, row.shortfall
    alert.money_at_stake = row.at_stake


def _close_open(db: Session, client_id: int, fy: str, keep: set[str]) -> None:
    for alert in alert_repo.list_alerts(db, client_id, fy, unacknowledged_only=True):
        if alert.metric.startswith("tds:") and alert.metric not in keep:
            alert.acknowledged, alert.acknowledged_by = True, SYSTEM_CLOSED


def evaluate_fy(
    db: Session, client_id: int, fy: str, analysis: ClientTds | None = None
) -> list[Alert]:
    """Record an alert for every party/section whose TDS status changed. Only for the TDS
    analysis year, and only once the client's ledger mapping is approved; open TDS alerts
    that no longer qualify are closed (acknowledged by the system)."""
    if fy != analysis_fy(db):
        _close_open(db, client_id, fy, set())
        db.flush()
        return []
    analysis = analysis or analyze(db, client_id, fy)
    live: set[str] = set()
    raised: list[Alert] = []
    if analysis.mapping_approved:
        for row in analysis.rows:
            if not row.alertable or row.decision.applies is None:
                continue
            metric = metric_key(row.section_key, row.party.key)
            live.add(metric)
            latest = alert_repo.latest_alert(db, client_id, fy, metric)
            previous = latest.new_status if latest is not None else TdsStatus.BELOW.value
            if previous == row.status:
                if latest is not None:  # same status: keep its figures current (no new alert)
                    _fill(latest, row)
                continue
            event = _event(row, previous)
            evaluation = row.evaluation
            alert = alert_repo.create_alert(
                db, client_id, fy, event,
                crossed_on=evaluation.crossed_on,
                crossed_voucher_no=evaluation.crossed_voucher_no,
            )  # fmt: skip
            alert.section, alert.party, alert.party_key = (
                row.section_key,
                row.party.name[:255],
                row.party.key,
            )
            _fill(alert, row)
            notify(event)
            raised.append(alert)
    _close_open(db, client_id, fy, live)
    db.flush()
    return raised


def fys_with_entries(db: Session, client_id: int) -> set[str]:
    return set(db.scalars(select(TdsEntry.fy).where(TdsEntry.client_id == client_id).distinct()))


def recheck_client(db: Session, client_id: int, fys: set[str] | None = None) -> list[Alert]:
    """Re-evaluate the TDS analysis year when it (or the year before, its base year for
    applicability) is among `fys` (default: always). Other years never get TDS alerts."""
    target = analysis_fy(db)
    raised: list[Alert] = []
    for fy in fys_with_entries(db, client_id) - {target}:
        evaluate_fy(db, client_id, fy)  # closes anything open outside the analysis year
    wanted = fys is None or target in {f for fy in fys for f in (fy, next_fy(fy))}
    if wanted and target in fys_with_entries(db, client_id):
        raised += evaluate_fy(db, client_id, target)
    return raised


def recheck_all(db: Session) -> list[Alert]:
    raised: list[Alert] = []
    for client in client_repo.list_clients(db):
        raised += recheck_client(db, client.id)
    db.commit()
    return raised


# ------------------------------------------------------------ data quality


def data_quality(db: Session, client_id: int) -> list[dict]:
    """Every party whose PAN contradicts its name (Pvt Ltd with a firm's PAN, etc.)."""
    users = tds_repo.parties(db, client_id)
    latest: dict[str, TdsLedger] = {}
    for row in db.scalars(
        select(TdsLedger)
        .where(TdsLedger.client_id == client_id, TdsLedger.kind == PARTY)
        .order_by(TdsLedger.fy)
    ):
        latest[row.name_key] = row
    out = []
    for ledger in latest.values():
        info = _party_info(party_key_for(ledger, ledger.name_key), ledger, ledger.name,
                           users.get(party_key_for(ledger, ledger.name_key)))  # fmt: skip
        if info.pan_warning:
            out.append(
                {
                    "party_key": info.key,
                    "party": info.name,
                    "pan": info.pan,
                    "pan_source": info.pan_source,
                    "gstin": info.gstin,
                    "fy": ledger.fy,
                    "problem": info.pan_warning,
                    "level": te.pan_name_level(info.name, info.pan),
                    "consequence": te.WRONG_PAN_NOTE,
                }
            )
    return sorted(out, key=lambda r: (r["level"] != "contradiction", r["party"].casefold()))


# ------------------------------------------------------------ mapping screen


def mapping_view(db: Session, client_id: int) -> list[dict]:
    """Every mapping rule of a client, with each ledger's kind, groups and the net amount
    posted to it in every FY (so the CA sees what a rule moves)."""
    from sqlalchemy import func

    activity: dict[str, dict[str, Decimal]] = defaultdict(dict)
    lines: dict[str, int] = defaultdict(int)
    query = (
        select(TdsEntry.ledger_key, TdsEntry.fy, func.sum(TdsEntry.amount), func.count())
        .where(TdsEntry.client_id == client_id)
        .group_by(TdsEntry.ledger_key, TdsEntry.fy)
    )
    for key, fy, total, count in db.execute(query):
        activity[key][fy] = -Decimal(str(total))  # debit (expense) shown positive
        lines[key] += count
    ledgers = ledgers_for(db, client_id, "")  # no FY preferred: the latest year wins
    hints = _rent_hints(db, client_id)
    out = []
    for row in mapping_rows(db, client_id):
        ledger = ledgers.get(row.name_key) if row.match_type == "ledger" else None
        out.append(
            {
                "id": row.id,
                "match_type": row.match_type,
                "name": row.name,
                "role": row.role,
                "section_key": row.section_key,
                "source": row.source,
                "reason": row.reason,
                "kind": ledger.kind if ledger is not None else None,
                "groups": list(ledger.groups or []) if ledger is not None else [],
                "amounts": activity.get(row.name_key, {}) if row.match_type == "ledger" else {},
                "lines": lines.get(row.name_key, 0) if row.match_type == "ledger" else 0,
                "requires_choice": bool(row.requires_choice),
                "choice_pending": bool(row.requires_choice) and row.source == "auto",
                "hint": (hints.get(row.name_key) or {}).get("text"),
                "rent_comparison": (hints.get(row.name_key) or {}).get("comparison"),
            }
        )
    order = {tm.UNMAPPED: 0, tm.BASE: 1, tm.TDS: 2, tm.EXCLUDED: 3}
    out.sort(key=lambda r: (r["match_type"] != "group", order.get(r["role"], 9),
                            r["section_key"] or "", r["name"].casefold()))  # fmt: skip
    return out


def _rent_hints(db: Session, client_id: int) -> dict[str, str]:
    """For every rent ledger: in the TDS analysis year, who it was paid to and the TDS rate
    the client's own 'TDS on rent' ledgers imply - so the CA can tell 194I(a) from (b)."""
    fy = analysis_fy(db)
    rows = [
        r for r in mapping_rows(db, client_id) if r.requires_choice and r.match_type == "ledger"
    ]
    if not rows:
        return {}
    ledgers = ledgers_for(db, client_id, fy)
    entries = db.execute(
        select(TdsEntry.voucher_key, TdsEntry.ledger, TdsEntry.ledger_key, TdsEntry.amount).where(
            TdsEntry.client_id == client_id, TdsEntry.fy == fy
        )
    ).all()
    rent_keys = {
        r.name_key
        for r in rows
        if (ledgers.get(r.name_key) and ledgers[r.name_key].kind == tm.EXPENSE)
    }
    tds_rent = {
        r.name_key
        for r in rows
        if ledgers.get(r.name_key) and ledgers[r.name_key].kind == tm.TDS_LEDGER
    }
    paid: dict[str, Decimal] = defaultdict(Decimal)
    payees: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    credited = Decimal(0)
    vouchers: dict[str, list] = defaultdict(list)
    for entry in entries:
        vouchers[entry.voucher_key].append(entry)
    for lines in vouchers.values():
        for line in lines:
            if line.ledger_key in rent_keys:
                paid[line.ledger_key] -= line.amount
                party = next(
                    (
                        x.ledger
                        for x in lines
                        if ledgers.get(x.ledger_key) and ledgers[x.ledger_key].kind == PARTY
                    ),
                    None,
                )
                payees[line.ledger_key][
                    party or "no party ledger (paid from cash / bank)"
                ] -= line.amount
            elif line.ledger_key in tds_rent and line.amount > 0:
                credited += line.amount
    total_rent = sum(paid.values(), Decimal(0))
    implied = credited / total_rent * 100 if total_rent > 0 and credited else None
    rules = tds_repo.rules(db)
    fy_start, _ = fy_bounds(fy)
    hints: dict[str, dict] = {}
    for key, amount in paid.items():
        if amount <= 0:
            continue
        top = sorted(payees[key].items(), key=lambda kv: -kv[1])[:2]
        who = "; ".join(f"{name} {inr(value)}" for name, value in top)
        text = f"FY {fy}: {inr(amount)} - {who}."
        if implied is not None:
            text += (
                f" The TDS-on-rent ledgers hold {inr(credited)} = {implied:.1f}% of all rent, i.e. the "
                f"books treat it as {'194I(b) at 10%' if implied > 5 else '194I(a) at 2%'}."
            )
        text += (
            " If this is rent of plant, machinery or equipment (e.g. a petrol pump's dispensing "
            "units, tanks or other outlet equipment) it is 194I(a) at 2%, not 194I(b) at 10% - please confirm."
        )
        hints[key] = {
            "text": text,
            "comparison": _rent_comparison(rules, fy_start, amount, credited, fy),
        }
    return hints


def _rent_comparison(
    rules, fy_start: date, amount: Decimal, credited: Decimal, fy: str
) -> list[dict]:
    """The TDS on this rent under 194I(a) and 194I(b), against the TDS on rent in the books."""
    out = []
    for key in te.RENT_KEYS:
        rule = te.rule_on(rules, key, fy_start)
        if rule is None:
            continue
        crossed = amount > rule.aggregate_threshold
        due = te.round_rupee(amount * rule.rate_other / 100) if crossed else Decimal(0)
        out.append(
            {
                "section": key,
                "fy": fy,
                "rate": str(rule.rate_other),
                "rent": str(amount),
                "tds_due": str(due),
                "deducted": str(credited),
                "difference": str(credited - due),  # + = more deducted than due
            }
        )
    return out


def change_mapping(db: Session, client_id: int, changes: list[dict]) -> None:
    """Apply the CA's edits (source becomes 'user'); then re-evaluate the client."""
    rows = {(r.match_type, r.name_key): r for r in mapping_rows(db, client_id)}
    for change in changes:
        key = (change["match_type"], tj._key(change["name"]))
        row = rows.get(key)
        if change.get("delete"):
            if row is not None and row.match_type == "group":
                db.delete(row)
            continue
        if row is None:
            row = TdsLedgerMap(
                client_id=client_id,
                match_type=change["match_type"],
                name=change["name"].strip(),
                name_key=key[1],
            )
            db.add(row)
            rows[key] = row
        section = change.get("section_key") if change["role"] in (tm.BASE, tm.TDS) else None
        if change["role"] in (tm.BASE, tm.TDS) and not section:
            raise ValueError(f"'{change['name']}': choose the section for role '{change['role']}'")
        row.role, row.section_key, row.source = change["role"], section, "user"
        row.reason = "Set by user"
    db.flush()


def approve_mapping(db: Session, client_id: int, approved_by: str, approved: bool = True) -> None:
    """Approving accepts every current rule as the CA's own (auto rules are no longer
    refreshed) and switches TDS alerts on for the client."""
    from datetime import datetime

    settings = tds_repo.client_settings(db, client_id)
    if approved:
        pending = [
            r.name for r in mapping_rows(db, client_id) if r.requires_choice and r.source == "auto"
        ]
        if pending:
            raise ValueError(
                "Choose 194I(a) or 194I(b) for every rent ledger before approving: "
                + ", ".join(sorted(pending))
            )
        settings.mapping_approved_at, settings.mapping_approved_by = datetime.utcnow(), approved_by
        for row in mapping_rows(db, client_id):
            if row.source == "auto":
                row.source = "user"
                row.reason = f"Approved proposal: {row.reason or ''}"[:255]
    else:
        settings.mapping_approved_at = settings.mapping_approved_by = None
    db.flush()


# ------------------------------------------------------------ completeness

LOOKS_TDS = re.compile(
    r"freight|labou?r|transport|tranport|job ?work|commission|brokerage|cartage|carriage|loading|"
    r"contract|courier|hire|rent|professional|consult|technical|advertis|catering|repair|maint",
    re.IGNORECASE,
)


def completeness(db: Session, client_id: int, analysis: ClientTds) -> dict:
    """Every expense and purchase ledger with postings in the year: where it went, how many
    parties, whether any party crossed its threshold - so nothing can be missed silently."""
    rows_map = {(r.match_type, r.name_key): r for r in mapping_rows(db, client_id)}
    ledgers = ledgers_for(db, client_id, analysis.fy)
    mapper = Mapper(list(rows_map.values()))
    crossed = {(r.party.key, r.section_key) for r in analysis.rows if r.evaluation.crossed}
    out = []
    for key, stat in sorted(
        analysis.ledger_stats.items(), key=lambda kv: (kv[1].kind, -abs(kv[1].total))
    ):
        if stat.total == 0 and stat.lines == 0:
            continue
        groups = list(ledgers[key].groups or []) if key in ledgers else []
        mapped = mapper.resolve(key, groups)
        own = rows_map.get(("ledger", key))
        if mapped is None:
            treatment, reason = "Unmapped", (own.reason if own else "No rule")
        elif mapped.role == tm.EXCLUDED:
            treatment, reason = "Excluded", (
                own.reason if own and mapped.via == "ledger" else f"via {mapped.via}"
            )
        else:
            treatment = f"{'Payments' if mapped.role == tm.BASE else 'TDS'} - {mapped.section_key}"
            reason = own.reason if own and mapped.via == "ledger" else f"via {mapped.via}"
        sections = stat.sections or (
            {mapped.section_key} if mapped and mapped.section_key else set()
        )
        any_crossed = any((p, s) in crossed for p in stat.parties for s in sections)
        identified = [p for p in stat.parties if p != NO_PARTY_KEY]
        look = LOOKS_TDS.search(stat.name)
        flag = None
        if look and treatment in ("Excluded", "Unmapped"):
            flag = f"Name suggests a TDS payment ('{look.group(0)}') but it is {treatment.lower()} - check"
        elif look and NO_PARTY_KEY in stat.parties:
            flag = f"'{look.group(0)}' paid partly with no party ledger - payees cannot be tested"
        out.append(
            {
                "ledger": stat.name,
                "kind": stat.kind,
                "treatment": treatment,
                "reason": reason,
                "total": str(stat.total.quantize(Decimal("0.01"))),
                "lines": stat.lines,
                "parties": len(identified),
                "no_party": NO_PARTY_KEY in stat.parties,
                "any_crossed": any_crossed,
                "flag": flag,
                "pending_choice": key in {tj._key(n) for n in analysis.pending_choice},
            }
        )
    purchases = sum((Decimal(r["total"]) for r in out if r["kind"] == tm.PURCHASE), Decimal(0))
    expenses = sum((Decimal(r["total"]) for r in out if r["kind"] == tm.EXPENSE), Decimal(0))
    figures = figures_repo.get_figures(db, client_id, analysis.fy)
    return {
        "fy": analysis.fy,
        "rows": out,
        "total_purchase_ledgers": str(purchases),
        "total_expense_ledgers": str(expenses),
        "turnover_purchases": (
            str(figures.purchases) if figures and figures.purchases is not None else None
        ),
    }
