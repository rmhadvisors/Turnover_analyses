"""Keeps the ledger-level Tally data the TDS analysis needs: every ledger of the Master
(with guid, GSTIN, PAN and groups) and every ledger line of every posted voucher.

Captured on each Tally JSON import, separately from the sales / purchase vouchers, so it
never changes turnover figures. Re-importing the same files adds nothing twice; replacing
a year replaces that year's lines.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import TdsEntry, TdsLedger, TdsLedgerMap
from app.services import tally_json as tj
from app.services import tds_mapping as tm
from app.services.fy_utils import fy_label
from app.services.tds_engine import valid_pan

INCOME_GROUPS = {"sales accounts", "direct incomes", "indirect incomes"}
BANK_CASH_GROUPS = {"bank accounts", "bank od a/c", "cash-in-hand", "bank occ a/c"}
NOT_PARTY_GROUPS = {
    "duties & taxes",
    "stock-in-hand",
    "fixed assets",
    "capital account",
    "reserves & surplus",
    "provisions",
    "suspense a/c",
    "investments",
    "misc. expenses (asset)",
    "branch / divisions",
}
BANK_CASH, PARTY, TAX, INCOME, OTHER = "bank_cash", "party", "tax", "income", "other"


@dataclass
class LedgerInfo:
    name: str
    guid: str | None
    parent: str | None
    groups: list[str]  # display names, nearest first
    group_keys: list[str]
    gstin: str | None
    tally_pan: str | None
    tax_type: str | None
    deductee_type: str | None
    is_transporter: bool
    kind: str = OTHER

    @property
    def key(self) -> str:
        return tj._key(self.name)


@dataclass
class LedgerBook:
    """Ledgers of one Master export, by lookup key."""

    ledgers: dict[str, LedgerInfo] = field(default_factory=dict)

    def get(self, name: str) -> LedgerInfo | None:
        return self.ledgers.get(tj._key(name))


def classify(info: LedgerInfo, master: tj.Master | None) -> str:
    groups = set(info.group_keys)
    name = info.name.casefold()
    tax_type = (info.tax_type or "").casefold()
    if tax_type == "tds" or " tds" in f" {name}" or name.startswith("tds"):
        return tm.TDS_LEDGER
    if tax_type == "tcs" or " tcs" in f" {name}" or name.startswith("tcs"):
        return tm.TCS_LEDGER
    if "purchase accounts" in groups:
        return tm.PURCHASE
    if groups & INCOME_GROUPS:
        return INCOME
    if master is not None and master.profit_kind(info.name) is not None:
        return tm.EXPENSE
    if {"direct expenses", "indirect expenses"} & groups:
        return tm.EXPENSE
    if "duties & taxes" in groups:
        return TAX
    if groups & BANK_CASH_GROUPS:
        return BANK_CASH
    if not groups or groups & NOT_PARTY_GROUPS:
        return OTHER
    return PARTY


def _gstin(record: dict) -> tuple[str | None, bool]:
    gstin, transporter = None, False
    for detail in record.get("ledgstregdetails") or []:
        if isinstance(detail, dict):
            gstin = str(detail.get("gstin") or "").strip().upper() or gstin
            transporter = transporter or detail.get("istransporter") is True
    return gstin, transporter


def read_ledgers(
    master_files: Iterable[tj.RecordsFile | tj.RecordsStream], master: tj.Master
) -> LedgerBook:
    group_names: dict[str, str] = {}
    raw: list[dict] = []
    for file in master_files:
        for record in file.records:
            record_type = record.get("metadata", {}).get("type")
            name = tj._record_name(record)
            if not name:
                continue
            if record_type == "Group":
                group_names[tj._key(name)] = name
            elif record_type == "Ledger":
                raw.append(record)
    book = LedgerBook()
    for record in raw:
        name = tj._record_name(record)
        chain: list[str] = []
        group = master.ledger_parent.get(tj._key(name))
        while group and group not in chain:
            chain.append(group)
            group = master.group_parent.get(group)
        gstin, transporter = _gstin(record)
        info = LedgerInfo(
            name=name,
            guid=str(record.get("guid") or "") or None,
            parent=tj._clean(record["parent"]) if record.get("parent") else None,
            groups=[group_names.get(key, key) for key in chain],
            group_keys=chain,
            gstin=gstin if gstin and len(gstin) == 15 else None,
            tally_pan=valid_pan(record.get("incometaxnumber")),
            tax_type=str(record.get("taxtype") or "") or None,
            deductee_type=str(record.get("tdsdeducteetype") or "") or None,
            is_transporter=transporter or record.get("istransporter") is True,
        )
        info.kind = classify(info, master)
        book.ledgers[info.key] = info
    return book


def voucher_key(voucher: tj.LedgerVoucher) -> str:
    raw = f"{voucher.guid}|{voucher.when.isoformat()}|{voucher.type_name}|{voucher.number}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ storing


def _store_ledgers(db: Session, client_id: int, fy: str, book: LedgerBook) -> None:
    existing = {
        row.name_key: row
        for row in db.scalars(
            select(TdsLedger).where(TdsLedger.client_id == client_id, TdsLedger.fy == fy)
        )
    }
    for key, info in book.ledgers.items():
        row = existing.get(key) or TdsLedger(client_id=client_id, fy=fy, name_key=key)
        row.name, row.guid, row.parent = info.name, info.guid, info.parent
        row.kind, row.groups = info.kind, info.groups
        row.gstin, row.tally_pan, row.tax_type = info.gstin, info.tally_pan, info.tax_type
        row.deductee_type, row.is_transporter = info.deductee_type, info.is_transporter
        db.add(row)


def propose_mappings(
    db: Session, client_id: int, ledgers: Iterable[tuple[str, str, list[str]]]
) -> int:
    """Add an 'auto' proposal for every mappable ledger without a ledger rule, and refresh
    existing 'auto' proposals to the current rules. A rule the user edited or approved
    (source 'user') is never changed. `ledgers`: (name, kind, groups). Returns how many
    were added."""
    rows = {
        row.name_key: row
        for row in db.scalars(
            select(TdsLedgerMap).where(
                TdsLedgerMap.client_id == client_id, TdsLedgerMap.match_type == "ledger"
            )
        )
    }
    added = 0
    for name, kind, groups in ledgers:
        key = tj._key(name)
        if kind not in tm.MAPPABLE_KINDS:
            continue
        proposal = tm.propose(name, kind, [g.casefold() for g in groups])
        row = rows.get(key)
        if row is None:
            row = TdsLedgerMap(client_id=client_id, match_type="ledger", name=name, name_key=key)
            db.add(row)
            rows[key] = row
            added += 1
        elif row.source != "auto":
            continue
        row.role, row.section_key = proposal.role, proposal.section_key
        row.source, row.reason = "auto", proposal.reason[:255]
        row.requires_choice = proposal.requires_choice
    db.flush()
    return added


@dataclass
class CaptureResult:
    fys: list[str]
    lines_added: int
    vouchers_added: int
    ledgers: int
    proposals_added: int


def store(
    db: Session,
    client_id: int,
    book: LedgerBook,
    vouchers: list[tj.LedgerVoucher],
    skip_fys: set[str] | frozenset[str] = frozenset(),
    replace_fys: set[str] | frozenset[str] = frozenset(),
    import_log_id: int | None = None,
) -> CaptureResult:
    by_fy: dict[str, list[tj.LedgerVoucher]] = {}
    for voucher in vouchers:
        fy = fy_label(voucher.when)
        if fy not in skip_fys:
            by_fy.setdefault(fy, []).append(voucher)
    lines = vouchers_added = 0
    for fy, items in sorted(by_fy.items()):
        _store_ledgers(db, client_id, fy, book)
        if fy in replace_fys:
            db.execute(delete(TdsEntry).where(TdsEntry.client_id == client_id, TdsEntry.fy == fy))
        seen = set(
            db.scalars(
                select(TdsEntry.voucher_key)
                .where(TdsEntry.client_id == client_id, TdsEntry.fy == fy)
                .distinct()
            )
        )
        rows: list[dict] = []
        for voucher in items:
            key = voucher_key(voucher)
            if key in seen:
                continue
            seen.add(key)
            vouchers_added += 1
            for line_no, entry in enumerate(voucher.entries):
                rows.append(
                    {
                        "client_id": client_id,
                        "fy": fy,
                        "voucher_key": key,
                        "line_no": line_no,
                        "voucher_date": voucher.when,
                        "voucher_type": voucher.type_name[:100] or None,
                        "voucher_no": voucher.number[:100] or None,
                        "party_ledger": (voucher.party or "")[:255] or None,
                        "ledger": entry.ledger[:255],
                        "ledger_key": tj._key(entry.ledger)[:255],
                        "amount": entry.amount,
                        "is_party": entry.is_party,
                        "import_log_id": import_log_id,
                    }
                )
        for start in range(0, len(rows), 5000):
            db.execute(insert(TdsEntry), rows[start : start + 5000])
        lines += len(rows)
    db.flush()
    added = propose_mappings(
        db, client_id, ((i.name, i.kind, i.groups) for i in book.ledgers.values())
    ) if by_fy else 0  # fmt: skip
    return CaptureResult(sorted(by_fy), lines, vouchers_added, len(book.ledgers), added)


def first_day(fy: str) -> date:
    return date(int(fy[:4]), 4, 1)


def repropose(db: Session, client_id: int) -> int:
    """Refresh the 'auto' proposals from the ledgers already captured (no re-import)."""
    latest: dict[str, TdsLedger] = {}
    for row in db.scalars(
        select(TdsLedger).where(TdsLedger.client_id == client_id).order_by(TdsLedger.fy)
    ):
        latest[row.name_key] = row
    return propose_mappings(
        db, client_id, ((r.name, r.kind, list(r.groups or [])) for r in latest.values())
    )
