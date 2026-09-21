"""Parsing of Tally's JSON export (a Master file plus a Transactions file).

Pure functions only: bytes in, dataclasses out, no database. Turnover follows Tally's
own definition - the totals of the *Sales Accounts* and *Purchase Accounts* groups -
using the Master file to know which group every ledger belongs to. That makes it
independent of voucher-type names (custom types such as "Purchase New" work), finds
the Sales ledger even when it is nested inside an item invoice's inventory lines, and
treats a net-debit sales entry as a credit note and a net-credit purchase entry as a
debit note. Deleted, cancelled, optional and void vouchers are excluded, as in Tally.

Tally exports these files as UTF-16 and, if the export is interrupted, the file can end
in the middle of a voucher. `read_records` keeps every complete record and reports the
cut instead of rejecting the whole file.

Amount convention in the export: credit is positive, debit is negative.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.services.fy_utils import fy_label
from app.services.tally_importer import (
    ImportFormatError,
    ParsedVoucher,
    make_dedup_key,
    parse_amount,
)

SALES_GROUP = "Sales Accounts"
PURCHASE_GROUP = "Purchase Accounts"
TAX_GROUP = "Duties & Taxes"
EXCLUDED_FLAGS = (
    ("isdeleted", "deleted"),
    ("iscancelled", "cancelled"),
    ("isvoid", "void"),
    ("isoptional", "optional (not posted)"),
)
_TWO_PLACES = Decimal("0.01")
_TAX_NAME = re.compile(r"\b(cgst|sgst|utgst|igst|gst|cess)\b", re.IGNORECASE)


def _clean(name: Any) -> str:
    """Ledger / group names can carry stray whitespace or line breaks in the export."""
    return " ".join(str(name).split())


# ------------------------------------------------------------------- reading


def decode_text(content: bytes) -> str:
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return content.decode("utf-16", errors="replace")
    if content[:3] == b"\xef\xbb\xbf":
        return content.decode("utf-8-sig", errors="replace")
    for encoding in ("utf-8", "utf-16"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("cp1252", errors="replace")


@dataclass
class RecordsFile:
    file_name: str
    records: list[dict]
    truncated: bool


def read_records(content: bytes, file_name: str = "file.json") -> RecordsFile:
    """Read the `tallymessage` array. A file cut off mid-record yields all complete
    records before the cut with `truncated=True`."""
    text = decode_text(content)
    marker = text.find('"tallymessage"')
    if marker < 0:
        raise ImportFormatError(
            f"'{file_name}' is not a Tally JSON export (no 'tallymessage' data)."
        )
    start = text.find("[", marker)
    if start < 0:
        raise ImportFormatError(f"'{file_name}' has no record list after 'tallymessage'.")
    decoder = json.JSONDecoder()
    records: list[dict] = []
    position, size = start + 1, len(text)
    while True:
        while position < size and text[position] in " \r\n\t,":
            position += 1
        if position >= size:
            return RecordsFile(file_name, records, truncated=True)  # ran out before the "]"
        if text[position] == "]":
            return RecordsFile(file_name, records, truncated=False)
        try:
            record, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            return RecordsFile(file_name, records, truncated=True)
        if isinstance(record, dict):
            records.append(record)


def kind_of(records: list[dict]) -> str:
    """'master', 'transactions' or 'unknown', from the record types inside."""
    types = Counter(r.get("metadata", {}).get("type") for r in records[:200])
    if types.get("Voucher"):
        return "transactions"
    if types.get("Ledger") or types.get("Group"):
        return "master"
    return "unknown"


# -------------------------------------------------------------------- master


@dataclass
class Master:
    ledger_parent: dict[str, str] = field(default_factory=dict)
    group_parent: dict[str, str] = field(default_factory=dict)
    _cache: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    def ancestors(self, ledger: str) -> frozenset[str]:
        """Every group above a ledger, nearest first-to-primary (cycle-safe)."""
        if ledger in self._cache:
            return self._cache[ledger]
        chain: list[str] = []
        group = self.ledger_parent.get(ledger)
        while group and group not in chain:
            chain.append(group)
            group = self.group_parent.get(group)
        result = frozenset(chain)
        self._cache[ledger] = result
        return result

    def knows(self, ledger: str) -> bool:
        return ledger in self.ledger_parent


def _record_name(record: dict) -> str | None:
    name = record.get("metadata", {}).get("name") or record.get("name")
    if not name:
        languages = record.get("languagename") or []
        if languages and isinstance(languages[0], dict):
            inner = languages[0].get("name")
            name = inner[-1] if isinstance(inner, list) and inner else inner
    return _clean(name) if name else None


def build_master(record_files: list[RecordsFile]) -> Master:
    master = Master()
    for file in record_files:
        for record in file.records:
            record_type = record.get("metadata", {}).get("type")
            name = _record_name(record)
            parent = _clean(record["parent"]) if record.get("parent") else ""
            if not name or record_type not in ("Ledger", "Group"):
                continue
            target = master.ledger_parent if record_type == "Ledger" else master.group_parent
            target[name] = parent
    return master


# -------------------------------------------------------------- transactions


@dataclass(frozen=True)
class Entry:
    ledger: str
    amount: Decimal
    is_party: bool


def collect_entries(voucher: dict) -> tuple[list[Entry], int]:
    """All ledger lines of a voucher, wherever they sit (main ledger list, or nested in
    item-invoice inventory lines). Returns (entries, unreadable_amount_count)."""
    entries: list[Entry] = []
    unreadable = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal unreadable
        if depth > 4:
            return
        if isinstance(node, dict):
            if "ledgername" in node and "amount" in node:
                amount = parse_amount(node["amount"])
                if amount is None:
                    unreadable += 1
                else:
                    entries.append(
                        Entry(_clean(node["ledgername"]), amount, node.get("ispartyledger") is True)
                    )
                return
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(voucher, 0)
    return entries, unreadable


def _ledger_kind(master: Master, ledger: str) -> str | None:
    ancestors = master.ancestors(ledger)
    if SALES_GROUP in ancestors:
        return "sales"
    if PURCHASE_GROUP in ancestors:
        return "purchase"
    if TAX_GROUP in ancestors:
        return "tax"
    if not master.knows(ledger) and _TAX_NAME.search(ledger):
        return "tax"
    return None


def _voucher_date(voucher: dict) -> date | None:
    raw = str(voucher.get("date") or "").strip()
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


@dataclass
class JsonParseResult:
    vouchers: list[ParsedVoucher] = field(default_factory=list)
    total_vouchers: int = 0
    excluded: Counter = field(default_factory=Counter)
    other_vouchers: int = 0  # posted, but not sales / purchase (receipts, payments, contra ...)
    voucher_types: Counter = field(default_factory=Counter)
    unknown_ledgers: set[str] = field(default_factory=set)
    unreadable_amounts: int = 0
    undated: int = 0
    unbalanced: int = 0
    mixed: int = 0  # one voucher touching both a Sales and a Purchase ledger
    gstins: Counter = field(default_factory=Counter)
    first_date: date | None = None
    last_date: date | None = None
    truncated_files: list[str] = field(default_factory=list)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES)


def _emit(
    result: JsonParseResult, kind: str, amount: Decimal, tax: Decimal, total: Decimal,
    voucher: dict, when: date, position: int, party: str | None,
) -> None:  # fmt: skip
    """Append one sales / purchase record; a net-reversal becomes a credit / debit note."""
    if kind == "sales":
        voucher_type = "sales" if amount >= 0 else "credit_note"
    else:
        voucher_type = "purchase" if amount >= 0 else "debit_note"
    taxable, tax_value, gross = (_quantize(abs(v)) for v in (amount, tax, total))
    number = str(voucher.get("vouchernumber") or "").strip()
    result.vouchers.append(
        ParsedVoucher(
            voucher_type=voucher_type,
            voucher_date=when,
            voucher_no=number,
            party=party,
            taxable_value=taxable,
            tax_value=tax_value,
            total_value=gross,
            fy=fy_label(when),
            dedup_key=make_dedup_key(voucher_type, number, when, gross),
            source_row=position,
        )
    )


def parse_transactions(record_files: list[RecordsFile], master: Master) -> JsonParseResult:
    result = JsonParseResult()
    result.truncated_files = [f.file_name for f in record_files if f.truncated]
    position = 0
    for file in record_files:
        for voucher in file.records:
            if voucher.get("metadata", {}).get("type") != "Voucher":
                continue
            position += 1
            result.total_vouchers += 1
            reason = next(
                (label for flag, label in EXCLUDED_FLAGS if voucher.get(flag) is True), None
            )
            if reason:
                result.excluded[reason] += 1
                continue
            when = _voucher_date(voucher)
            if when is None:
                result.undated += 1
                continue
            result.voucher_types[str(voucher.get("vouchertypename"))] += 1
            if voucher.get("cmpgstin"):
                result.gstins[voucher["cmpgstin"]] += 1
            result.first_date = min(result.first_date or when, when)
            result.last_date = max(result.last_date or when, when)

            entries, unreadable = collect_entries(voucher)
            result.unreadable_amounts += unreadable
            if abs(sum((e.amount for e in entries), Decimal(0))) > 1:
                result.unbalanced += 1

            sales = purchase = tax = party_total = Decimal(0)
            for entry in entries:
                if not master.knows(entry.ledger):
                    result.unknown_ledgers.add(entry.ledger)
                kind = _ledger_kind(master, entry.ledger)
                if kind == "sales":
                    sales += entry.amount  # credit (+) is a sale
                elif kind == "purchase":
                    purchase -= entry.amount  # debit (-) is a purchase
                elif kind == "tax":
                    tax += entry.amount
                if entry.is_party:
                    party_total += entry.amount
            party = str(voucher.get("partyledgername") or "").strip() or None

            if sales == 0 and purchase == 0:
                result.other_vouchers += 1
                continue
            if sales != 0 and purchase != 0:
                result.mixed += 1
            if sales != 0:
                # tax on the sales side has the same sign as the sale; flip for a return
                side_tax = tax if sales > 0 else -tax
                gross = abs(party_total) if party_total else abs(sales) + abs(side_tax)
                _emit(result, "sales", sales, side_tax, gross, voucher, when, position, party)
            if purchase != 0:
                side_tax = -tax if purchase > 0 else tax
                gross = (
                    abs(party_total)
                    if party_total and sales == 0
                    else abs(purchase) + abs(side_tax)
                )
                _emit(result, "purchase", purchase, side_tax, gross, voucher, when, position, party)
    return result


# ------------------------------------------------------------------ warnings


def describe_warnings(result: JsonParseResult, master: Master, kinds: dict[str, str]) -> list[str]:
    """Plain-language problems the user should know about before relying on the numbers."""
    warnings: list[str] = []
    for name in result.truncated_files:
        warnings.append(
            f"'{name}' was cut off in the middle of a voucher (the export looks incomplete). "
            f"Every complete voucher before the cut was read; the rest is missing. "
            f"Export it again from Tally to get everything."
        )
    if result.total_vouchers == 0:
        warnings.append("The Transactions file contains no vouchers.")
    elif not result.vouchers:
        warnings.append(
            "No sales or purchase entries were found: the vouchers here are only receipts, "
            "payments, journals or similar. This looks like an export with no trading data "
            "for the period, or it was taken before the year's sales were entered."
        )
    if len(result.gstins) > 1:
        warnings.append(
            f"The vouchers carry {len(result.gstins)} different company GST numbers - check the files belong together."
        )
    if result.excluded:
        parts = ", ".join(f"{count} {reason}" for reason, count in result.excluded.items())
        warnings.append(f"Left out (as Tally does): {parts}.")
    if result.unknown_ledgers:
        warnings.append(
            f"{len(result.unknown_ledgers)} ledger(s) used in vouchers are not in the Master file, "
            f"so they were treated as neither sales nor purchase nor GST."
        )
    if result.unbalanced:
        warnings.append(
            f"{result.unbalanced} voucher(s) did not balance to zero - some entries may be missing from the export."
        )
    if result.mixed:
        warnings.append(
            f"{result.mixed} voucher(s) touch both Sales and Purchase ledgers; GST was not split between them."
        )
    if result.unreadable_amounts:
        warnings.append(
            f"{result.unreadable_amounts} amount(s) could not be read (for example foreign-currency entries) and were skipped."
        )
    if result.undated:
        warnings.append(f"{result.undated} voucher(s) had no readable date and were skipped.")
    if "master" not in kinds.values():
        warnings.append("No Master file was supplied, so ledger groups are unknown.")
    if result.first_date and result.last_date and result.vouchers:
        months = {(v.voucher_date.year, v.voucher_date.month) for v in result.vouchers}
        fys = sorted({v.fy for v in result.vouchers})
        if len(months) < 12 * len(fys) and len(months) <= 6:
            span = f"{result.first_date:%d-%b-%Y} to {result.last_date:%d-%b-%Y}"
            warnings.append(
                f"Only {len(months)} month(s) of trading ({span}) are included, so FY totals here are "
                f"part-year figures and cannot be compared with a full year."
            )
    return warnings


def summarize(vouchers: list[ParsedVoucher]) -> dict[str, dict[str, Decimal | int]]:
    """Totals by financial year: net sales and net purchases (excluding GST), with counts."""
    out: dict[str, dict[str, Decimal | int]] = {}
    signs = {"sales": 1, "credit_note": -1, "purchase": 1, "debit_note": -1}
    for v in vouchers:
        row = out.setdefault(
            v.fy, {"sales": Decimal(0), "purchases": Decimal(0), "sales_count": 0, "purchase_count": 0}
        )  # fmt: skip
        if v.voucher_type in ("sales", "credit_note"):
            row["sales"] += signs[v.voucher_type] * v.taxable_value
            row["sales_count"] += 1
        else:
            row["purchases"] += signs[v.voucher_type] * v.taxable_value
            row["purchase_count"] += 1
    return out
