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
cut instead of rejecting the whole file. Files are read as a stream (ijson), so a
500+ MB export is never held in memory as one string or one list of records.

Amount convention in the export: credit is positive, debit is negative.
"""

from __future__ import annotations

import codecs
import io
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import IO, Any

import ijson

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
_STOCK_KEY = "stock-in-hand"  # Tally's reserved group, matched case-insensitively
_TAX_NAME = re.compile(r"\b(cgst|sgst|utgst|igst|gst|cess)\b", re.IGNORECASE)


def _clean(name: Any) -> str:
    """Ledger / group names can carry stray whitespace or line breaks in the export."""
    return " ".join(str(name).split())


def _key(name: Any) -> str:
    """Case- and whitespace-insensitive lookup key ('Sales GST @ 18%' == 'SALES GST @ 18%')."""
    return _clean(name).casefold()


# ------------------------------------------------------------------- reading


def _sniff_encoding(head: bytes) -> tuple[str, int]:
    """(codec, BOM length) from the first bytes of a file."""
    if head[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return "utf-16", 0  # the utf-16 codec reads the BOM itself
    if head[:3] == codecs.BOM_UTF8:
        return "utf-8", 3
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        return "utf-8", 0
    except UnicodeDecodeError:
        pass
    if bytes(1) in head:  # NUL bytes: UTF-16 without a BOM
        return "utf-16", 0
    return "cp1252", 0


class _Utf8Stream:
    """Re-encodes a UTF-16 / UTF-8 / cp1252 byte stream as UTF-8, chunk by chunk, for
    ijson. `on_read(n)` is told how many source bytes were consumed (for progress)."""

    CHUNK = 1 << 20

    def __init__(self, raw: IO[bytes], on_read: Callable[[int], None] | None = None):
        head = raw.read(4096)
        encoding, bom = _sniff_encoding(head)
        raw.seek(bom)
        self._raw = raw
        self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        self._buffer = bytearray()
        self._done = False
        self._on_read = on_read

    def read(self, size: int = -1) -> bytes:
        while not self._done and (size < 0 or len(self._buffer) < size):
            chunk = self._raw.read(self.CHUNK)
            if self._on_read and chunk:
                self._on_read(len(chunk))
            self._done = not chunk
            self._buffer += self._decoder.decode(chunk, final=self._done).encode("utf-8")
        if size < 0:
            size = len(self._buffer)
        out = bytes(self._buffer[:size])
        del self._buffer[:size]
        return out


def _records_prefix(raw: IO[bytes], file_name: str) -> str:
    """ijson path of the `tallymessage` array, wherever it sits in the document."""
    raw.seek(0)
    try:
        events = ijson.parse(_Utf8Stream(raw))
        for prefix, event, value in events:
            if event == "map_key" and value == "tallymessage":
                path = f"{prefix}.tallymessage" if prefix else "tallymessage"
                _, next_event, _ = next(events, (None, None, None))
                if next_event != "start_array":
                    raise ImportFormatError(
                        f"'{file_name}' has no record list after 'tallymessage'."
                    )
                return path
    except ijson.JSONError:
        pass
    raise ImportFormatError(f"'{file_name}' is not a Tally JSON export (no 'tallymessage' data).")


def _array_closed(raw: IO[bytes], path: str) -> bool:
    """True if the `tallymessage` array ends with its ']' (so a later error is only in
    the document's trailer, not a cut-off record). Only used on files that failed to parse."""
    raw.seek(0)
    try:
        for prefix, event, _ in ijson.parse(_Utf8Stream(raw)):
            if event == "end_array" and prefix == path:
                return True
    except ijson.JSONError:
        pass
    return False


class RecordsStream:
    """The `tallymessage` records of one file, read lazily and only once per pass.

    `truncated` and `count` are known after a full pass. A file cut off mid-record
    yields every complete record before the cut, then sets `truncated`."""

    def __init__(
        self,
        raw: IO[bytes],
        file_name: str = "file.json",
        on_read: Callable[[int], None] | None = None,
    ):
        self.file_name = file_name
        self._raw = raw
        self._on_read = on_read
        self._path = _records_prefix(raw, file_name)
        self.truncated = False
        self.count = 0

    def head(self, limit: int) -> list[dict]:
        """The first `limit` records (a cheap peek, e.g. to tell Master from Transactions)."""
        out: list[dict] = []
        self._raw.seek(0)
        try:
            for record in ijson.items(_Utf8Stream(self._raw), f"{self._path}.item", use_float=True):
                if isinstance(record, dict):
                    out.append(record)
                    if len(out) >= limit:
                        break
        except ijson.JSONError:
            pass
        return out

    @property
    def records(self) -> Iterator[dict]:
        self._raw.seek(0)
        self.count, self.truncated = 0, False
        stream = _Utf8Stream(self._raw, self._on_read)
        try:
            for record in ijson.items(stream, f"{self._path}.item", use_float=True):
                if isinstance(record, dict):
                    self.count += 1
                    yield record
        except ijson.JSONError:
            self.truncated = not _array_closed(self._raw, self._path)


@dataclass
class RecordsFile:
    file_name: str
    records: list[dict]
    truncated: bool

    @property
    def count(self) -> int:
        return len(self.records)


def read_records(content: bytes, file_name: str = "file.json") -> RecordsFile:
    """Read the whole `tallymessage` array into memory (small files and tests). A file
    cut off mid-record yields all complete records before the cut with `truncated=True`."""
    stream = RecordsStream(io.BytesIO(content), file_name)
    records = list(stream.records)
    return RecordsFile(file_name, records, stream.truncated)


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
    # Tally's own P&L flags on groups: 'affectsgrossprofit' (trading account) and 'isrevenue'.
    gross_profit_groups: set[str] = field(default_factory=set)
    revenue_groups: set[str] = field(default_factory=set)
    # Ledger balances in the Master: opening balance, and dated closing values (closing stock).
    opening: dict[str, Decimal] = field(default_factory=dict)  # _key(ledger) -> amount
    closing: dict[str, dict[date, Decimal]] = field(default_factory=dict)
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

    def profit_kind(self, ledger: str) -> str | None:
        """'trading' (affects gross profit), 'pl' (other revenue) or None (balance sheet)."""
        ancestors = self.ancestors(ledger)
        if ancestors & self.gross_profit_groups:
            return "trading"
        if ancestors & self.revenue_groups:
            return "pl"
        return None

    def stock_ledgers(self) -> list[str]:
        """Ledgers under Stock-in-hand (their values are the opening / closing stock)."""
        return [key for key in self.ledger_parent if _STOCK_KEY in self.ancestors(key)]


def _record_name(record: dict) -> str | None:
    name = record.get("metadata", {}).get("name") or record.get("name")
    if not name:
        languages = record.get("languagename") or []
        if languages and isinstance(languages[0], dict):
            inner = languages[0].get("name")
            name = inner[-1] if isinstance(inner, list) and inner else inner
    return _clean(name) if name else None


def build_master(record_files: Iterable[RecordsFile | RecordsStream]) -> Master:
    master = Master()
    for file in record_files:
        for record in file.records:
            record_type = record.get("metadata", {}).get("type")
            name = _record_name(record)
            parent = _clean(record["parent"]) if record.get("parent") else ""
            if not name or record_type not in ("Ledger", "Group"):
                continue
            key = _key(name)
            if record_type == "Group":
                master.group_parent[key] = _key(parent)
                if record.get("affectsgrossprofit") is True:
                    master.gross_profit_groups.add(key)
                if record.get("isrevenue") is True:
                    master.revenue_groups.add(key)
                continue
            master.ledger_parent[key] = _key(parent)
            opening = parse_amount(record.get("openingbalance") or 0)
            if opening:
                master.opening[key] = opening
            for value in record.get("ledgerclosingvalues") or []:
                when = _voucher_date(value) if isinstance(value, dict) else None
                amount = parse_amount(value.get("amount")) if when else None
                if when and amount is not None:
                    master.closing.setdefault(key, {})[when] = amount
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
class ProfitParts:
    """One FY's P&L totals from vouchers (credit +, debit -)."""

    trading: Decimal = Decimal(0)
    pl: Decimal = Decimal(0)
    unknown: Decimal = Decimal(0)


@dataclass(frozen=True)
class LedgerVoucher:
    """Every ledger line of one posted voucher, kept for the TDS analysis (it needs expense,
    TDS and party ledgers too, not only sales and purchases)."""

    when: date
    guid: str
    type_name: str
    number: str
    party: str | None
    entries: tuple[Entry, ...]


@dataclass
class JsonParseResult:
    vouchers: list[ParsedVoucher] = field(default_factory=list)
    ledger_vouchers: list[LedgerVoucher] = field(default_factory=list)
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
    profit_parts: dict[str, ProfitParts] = field(default_factory=dict)  # FY -> P&L totals


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


def _record(
    kind: str, amount: Decimal, tax: Decimal, total: Decimal,
    voucher: dict, when: date, position: int, party: str | None,
) -> ParsedVoucher:  # fmt: skip
    """One sales / purchase record; a net-reversal becomes a credit / debit note."""
    if kind == "sales":
        voucher_type = "sales" if amount >= 0 else "credit_note"
    else:
        voucher_type = "purchase" if amount >= 0 else "debit_note"
    taxable, tax_value, gross = (_quantize(abs(v)) for v in (amount, tax, total))
    number = str(voucher.get("vouchernumber") or "").strip()
    return ParsedVoucher(
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


@dataclass
class _Outcome:
    """What one posted, dated voucher contributes to the result. Kept (instead of the
    voucher itself) until the file's cut-off day is known, so memory stays small."""

    when: date
    type_name: str
    gstin: Any
    unreadable: int
    unbalanced: bool
    unknown: list[tuple[str, Decimal]]  # (ledger, abs amount) for ledgers not in the Master
    mixed: bool
    records: list[ParsedVoucher]
    trading: Decimal = Decimal(0)  # net of ledgers in gross-profit groups (credit +)
    pl: Decimal = Decimal(0)  # net of other revenue ledgers (indirect incomes / expenses)
    unknown_net: Decimal = Decimal(0)  # net of ledgers missing from the Master
    ledger_voucher: LedgerVoucher | None = None


def _evaluate(voucher: dict, when: date, position: int, master: Master) -> _Outcome:
    entries, unreadable = collect_entries(voucher)
    unbalanced = abs(sum((e.amount for e in entries), Decimal(0))) > 1
    unknown: list[tuple[str, Decimal]] = []
    sales = purchase = tax = party_total = Decimal(0)
    trading = pl = unknown_net = Decimal(0)
    for entry in entries:
        if not master.knows(entry.ledger):
            unknown.append((entry.ledger, abs(entry.amount)))
            unknown_net += entry.amount
        else:
            profit_kind = master.profit_kind(entry.ledger)
            if profit_kind == "trading":
                trading += entry.amount
            elif profit_kind == "pl":
                pl += entry.amount
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
    ledger_voucher = LedgerVoucher(
        when=when,
        guid=str(voucher.get("guid") or ""),
        type_name=str(voucher.get("vouchertypename") or ""),
        number=str(voucher.get("vouchernumber") or "").strip(),
        party=_clean(party) if party else None,
        entries=tuple(entries),
    )

    records: list[ParsedVoucher] = []
    if sales != 0:
        # tax on the sales side has the same sign as the sale; flip for a return
        side_tax = tax if sales > 0 else -tax
        gross = abs(party_total) if party_total else abs(sales) + abs(side_tax)
        records.append(_record("sales", sales, side_tax, gross, voucher, when, position, party))
    if purchase != 0:
        side_tax = -tax if purchase > 0 else tax
        gross = abs(party_total) if party_total and sales == 0 else abs(purchase) + abs(side_tax)
        records.append(
            _record("purchase", purchase, side_tax, gross, voucher, when, position, party)
        )
    return _Outcome(
        when=when,
        type_name=str(voucher.get("vouchertypename")),
        gstin=voucher.get("cmpgstin"),
        unreadable=unreadable,
        unbalanced=unbalanced,
        unknown=unknown,
        mixed=sales != 0 and purchase != 0,
        records=records,
        trading=trading,
        pl=pl,
        unknown_net=unknown_net,
        ledger_voucher=ledger_voucher,
    )


def _apply(result: JsonParseResult, outcome: _Outcome) -> None:
    when = outcome.when
    parts = result.profit_parts.setdefault(fy_label(when), ProfitParts())
    parts.trading += outcome.trading
    parts.pl += outcome.pl
    parts.unknown += outcome.unknown_net
    result.voucher_types[outcome.type_name] += 1
    if outcome.ledger_voucher is not None:
        result.ledger_vouchers.append(outcome.ledger_voucher)
    if outcome.gstin:
        result.gstins[outcome.gstin] += 1
    result.first_date = min(result.first_date or when, when)
    result.last_date = max(result.last_date or when, when)
    result.unreadable_amounts += outcome.unreadable
    if outcome.unbalanced:
        result.unbalanced += 1
    for ledger, amount in outcome.unknown:
        result.unknown_ledgers.add(ledger)
        result.unknown_ledger_lines[ledger] += 1
        result.unknown_ledger_amounts[ledger] = (
            result.unknown_ledger_amounts.get(ledger, Decimal(0)) + amount
        )
    if not outcome.records:
        result.other_vouchers += 1
        return
    if outcome.mixed:
        result.mixed += 1
    result.vouchers.extend(outcome.records)


def parse_transactions(
    record_files: Iterable[RecordsFile | RecordsStream], master: Master
) -> JsonParseResult:
    """Parse one or more Transactions files for the same company/period. When more than
    one file is given (e.g. two overlapping exports), vouchers are merged and de-duplicated
    by their Tally `guid` - a later file's copy of a voucher already seen is skipped.
    Each file is read once, record by record."""
    result = JsonParseResult()
    position = 0
    seen_guids: set[str] = set()
    for file in record_files:
        outcomes: list[_Outcome] = []
        first_day: date | None = None
        last_day: date | None = None
        for voucher in file.records:
            record_day = _voucher_date(voucher)
            if record_day is not None:
                first_day = min(first_day or record_day, record_day)
                last_day = max(last_day or record_day, record_day)
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
            if record_day is None:
                result.undated += 1
                continue
            outcomes.append(_evaluate(voucher, record_day, position, master))

        # A cut export may contain a few syntactically complete vouchers from
        # its final day, while other vouchers from that same day are missing.
        # Treat the entire terminal day as unreliable so comparisons never mix
        # a partial day with a complete matching day in the other FY.
        if file.truncated:
            result.truncated_files.append(file.file_name)
        drop_last_day = file.truncated and first_day is not None and first_day < last_day
        for outcome in outcomes:
            if drop_last_day and outcome.when == last_day:
                result.excluded_cutoff_dates.add(outcome.when)
                continue
            _apply(result, outcome)
    return result


# ------------------------------------------------------------ gross / net profit

UNKNOWN_LEDGER_SHARE = Decimal("0.05")  # hold NP back when missing ledgers exceed 5% of it


@dataclass
class ProfitFigures:
    """Gross / net profit derived for one FY, or None with the reason in `notes`."""

    fy: str
    gross_profit: Decimal | None
    net_profit: Decimal | None
    opening_stock: Decimal | None = None
    closing_stock: Decimal | None = None
    notes: list[str] = field(default_factory=list)


def _stock_values(master: Master, fy: str) -> tuple[Decimal, Decimal] | str:
    """(opening, closing) stock for an FY from the Stock-in-hand ledgers, or the reason
    they are not available. Stock is a debit balance, exported as a negative amount."""
    start, end = date(int(fy[:4]), 4, 1), date(int(fy[:4]) + 1, 3, 31)
    opening = closing = Decimal(0)
    for key in master.stock_ledgers():
        values = master.closing.get(key, {})
        if end not in values:
            return f"closing stock at {end:%d-%b-%Y} is not in the Master file"
        closing -= values[end]
        # the previous year-end value if present, else the Master's opening balance
        opening -= values.get(start - timedelta(days=1), master.opening.get(key, Decimal(0)))
    return opening, closing


def derive_profits(
    result: JsonParseResult, master: Master, today: date | None = None
) -> list[ProfitFigures]:
    """Tally's P&L: GP = trading-account ledgers + closing stock - opening stock;
    NP = GP + all other revenue ledgers (indirect incomes / expenses, including custom
    revenue groups such as appropriations). Only for one complete, finished FY."""
    today = today or date.today()
    fys = sorted(result.profit_parts)
    if len(fys) != 1:
        note = "Profit is worked out only when the files hold exactly one financial year."
        return [ProfitFigures(fy, None, None, notes=[note]) for fy in fys]
    fy = fys[0]
    parts = result.profit_parts[fy]
    end = date(int(fy[:4]) + 1, 3, 31)
    if result.truncated_files:
        return [ProfitFigures(fy, None, None, notes=["The Transactions file is cut off."])]
    if today <= end:
        return [ProfitFigures(fy, None, None, notes=["The year is not finished yet."])]
    months = {(v.voucher_date.year, v.voucher_date.month) for v in result.vouchers}
    if len(months) <= 6:  # same rule as the part-year warning
        note = f"Only {len(months)} month(s) of trading in the files, so this is not a full year."
        return [ProfitFigures(fy, None, None, notes=[note])]
    notes: list[str] = []
    if master.stock_ledgers():
        stock = _stock_values(master, fy)
        if isinstance(stock, str):
            return [ProfitFigures(fy, None, None, notes=[f"Gross profit needs stock: {stock}."])]
        opening, closing = stock
    else:
        opening = closing = Decimal(0)
        notes.append("No stock ledger in Tally, so gross profit excludes stock.")
    gross = _quantize(parts.trading + closing - opening)
    net: Decimal | None = _quantize(gross + parts.pl)
    gap = abs(parts.unknown)
    if gap and (net == 0 or gap > abs(net) * UNKNOWN_LEDGER_SHARE):
        notes.append(
            f"Net profit not stored: Rs {gap:,.2f} is on ledgers missing from the Master file "
            f"(more than {UNKNOWN_LEDGER_SHARE:.0%} of the net profit of Rs {net:,.2f})."
        )
        net = None
    return [ProfitFigures(fy, gross, net, _quantize(opening), _quantize(closing), notes)]


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
