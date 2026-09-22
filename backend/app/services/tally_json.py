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


def _key(name: Any) -> str:
    """Case- and whitespace-insensitive lookup key ('Sales GST @ 18%' == 'SALES GST @ 18%')."""
    return _clean(name).casefold()


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
    """Ledger/group parent lookups, keyed case- and whitespace-insensitively so
    'Sales GST @ 18%' in a voucher matches 'SALES GST @ 18%' in the Master."""

    ledger_parent: dict[str, str] = field(default_factory=dict)  # _key(ledger) -> _key(group)
    group_parent: dict[str, str] = field(default_factory=dict)  # _key(group) -> _key(group)
    _cache: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    def ancestors(self, ledger: str) -> frozenset[str]:
        """Every group above a ledger (as lookup keys), nearest-first (cycle-safe)."""
        key = _key(ledger)
        if key in self._cache:
            return self._cache[key]
        chain: list[str] = []
        group = self.ledger_parent.get(key)
        while group and group not in chain:
            chain.append(group)
            group = self.group_parent.get(group)
        result = frozenset(chain)
        self._cache[key] = result
        return result

    def knows(self, ledger: str) -> bool:
        return _key(ledger) in self.ledger_parent


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
            target[_key(name)] = _key(parent)
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
            if "ledgername" in node:
                if "amount" not in node:
                    amount = Decimal(0)  # a ledger line with no amount key counts as zero
                else:
                    amount = parse_amount(node["amount"])
                    if amount is None:
                        unreadable += 1
                        return
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


_SALES_KEY, _PURCHASE_KEY, _TAX_KEY = _key(SALES_GROUP), _key(PURCHASE_GROUP), _key(TAX_GROUP)


def _ledger_kind(master: Master, ledger: str) -> str | None:
    ancestors = master.ancestors(ledger)
    if _SALES_KEY in ancestors:
        return "sales"
    if _PURCHASE_KEY in ancestors:
        return "purchase"
    if _TAX_KEY in ancestors:
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
    unknown_ledger_lines: Counter = field(default_factory=Counter)  # ledger name -> line count
    unknown_ledger_amounts: dict[str, Decimal] = field(
        default_factory=dict
    )  # ledger name -> total abs amount
    unreadable_amounts: int = 0
    undated: int = 0
    unbalanced: int = 0
    mixed: int = 0  # one voucher touching both a Sales and a Purchase ledger
    gstins: Counter = field(default_factory=Counter)
    first_date: date | None = None
    last_date: date | None = None
    truncated_files: list[str] = field(default_factory=list)
    excluded_cutoff_dates: set[date] = field(default_factory=set)
    duplicate_guid_vouchers: int = 0  # same voucher present in more than one Transactions file


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES)


def _dedup_key(
    voucher_type: str, number: str, when: date, gross: Decimal, party: str | None
) -> str:
    """type+number+date+amount, as for Excel/CSV imports - except a blank voucher
    number (common on POS-style cash sales) also folds in the party name, since two
    different customers can otherwise pay the same round amount on the same day and
    collide. Tally's JSON `guid` is NOT used here: its numeric suffix is an internal
    per-period object sequence that Tally reuses across separate period exports (the
    same guid can name two unrelated vouchers in the 24-25 and 25-26 files), so it is
    not a safe cross-file identity."""
    effective_number = number or f"(blank)/{(party or '').strip().casefold()}"
    return make_dedup_key(voucher_type, effective_number, when, gross)


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
            dedup_key=_dedup_key(voucher_type, number, when, gross, party),
            source_row=position,
        )
    )


def parse_transactions(record_files: list[RecordsFile], master: Master) -> JsonParseResult:
    """Parse one or more Transactions files for the same company/period. When more than
    one file is given (e.g. two overlapping exports), vouchers are merged and de-duplicated
    by their Tally `guid` - a later file's copy of a voucher already seen is skipped."""
    result = JsonParseResult()
    result.truncated_files = [f.file_name for f in record_files if f.truncated]
    position = 0
    seen_guids: set[str] = set()
    for file in record_files:
        # A cut export may contain a few syntactically complete vouchers from
        # its final day, while other vouchers from that same day are missing.
        # Treat the entire terminal day as unreliable so comparisons never mix
        # a partial day with a complete matching day in the other FY.
        dates = [_voucher_date(record) for record in file.records]
        cutoff_date = max((value for value in dates if value is not None), default=None)
        has_prior_day = bool(
            cutoff_date and any(value is not None and value < cutoff_date for value in dates)
        )
        for voucher in file.records:
            if voucher.get("metadata", {}).get("type") != "Voucher":
                continue
            guid = voucher.get("guid")
            if guid:
                if guid in seen_guids:
                    result.duplicate_guid_vouchers += 1
                    continue
                seen_guids.add(guid)
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
            if file.truncated and has_prior_day and when == cutoff_date:
                result.excluded_cutoff_dates.add(when)
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
                    result.unknown_ledger_lines[entry.ledger] += 1
                    result.unknown_ledger_amounts[entry.ledger] = result.unknown_ledger_amounts.get(
                        entry.ledger, Decimal(0)
                    ) + abs(entry.amount)
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
    if result.excluded_cutoff_dates:
        days = ", ".join(f"{day:%d-%b-%Y}" for day in sorted(result.excluded_cutoff_dates))
        warnings.append(
            f"All vouchers dated {days} were excluded because that is the cut-off day; "
            "other vouchers from the same day may be missing."
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
    if result.duplicate_guid_vouchers:
        warnings.append(
            f"{result.duplicate_guid_vouchers} voucher(s) appeared in more than one file and were "
            f"only counted once."
        )
    if result.unknown_ledgers:
        total_lines = sum(result.unknown_ledger_lines.values())
        total_amount = sum(result.unknown_ledger_amounts.values(), Decimal(0))
        top = sorted(result.unknown_ledger_lines.items(), key=lambda kv: -kv[1])[:5]
        detail = "; ".join(
            f"{name} ({count} line(s), ₹{result.unknown_ledger_amounts[name]:,.2f})"
            for name, count in top
        )
        warnings.append(
            f"{len(result.unknown_ledgers)} ledger(s) used in vouchers are not in the Master file "
            f"({total_lines} line(s) totalling ₹{total_amount:,.2f}), so they were treated as "
            f"neither sales nor purchase nor GST. Largest: {detail}."
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
