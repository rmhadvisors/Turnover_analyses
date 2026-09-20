"""Parsing of Tally exports (Excel / CSV) into voucher rows and P&L figures.

Pure functions only: bytes/rows in, parsed dataclasses out. Nothing here touches
the database, so the logic can be tested against sample files directly. Other
input formats (e.g. Tally XML) plug in by registering a reader in `READERS` that
returns the same list-of-rows shape.

Dr/Cr suffixes are stripped and treated as informational: the sign of an amount
comes only from brackets or a minus sign, and whether a voucher adds to or
deducts from turnover comes from its voucher type (credit / debit notes are
stored as positive amounts and deducted during aggregation).
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.fy_utils import fy_label

Row = list[Any]
Mapping = dict[str, str | list[str]]

REPORT_SALES = "sales_register"
REPORT_PURCHASE = "purchase_register"
REPORT_PL = "profit_loss"
REPORT_TYPES = (REPORT_SALES, REPORT_PURCHASE, REPORT_PL)

FIELD_LABELS = {
    "date": "Date",
    "voucher_no": "Voucher No.",
    "party": "Party / Particulars",
    "voucher_type": "Voucher Type",
    "taxable_value": "Taxable value (excl. GST)",
    "tax_value": "GST / tax amount",
    "total_value": "Gross total (incl. GST)",
}
REQUIRED_FIELDS = ("date",)


class ImportFormatError(ValueError):
    """The file could not be understood (bad type, no header row, missing columns)."""


# ---------------------------------------------------------------- value parsing

_SIDE_RE = re.compile(r"\s*(dr|cr)\.?\s*$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^(\d+(\.\d*)?|\.\d+)$")


def parse_amount(value: Any) -> Decimal | None:
    """Parse a Tally amount: '1,20,000.00 Dr', '(500.00)', '₹1,00,00,000', 12.5 ..."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return None if math.isnan(value) else Decimal(str(value))
    text = _SIDE_RE.sub("", str(value).strip())
    for junk in ("₹", "Rs.", "Rs", ",", " "):
        text = text.replace(junk, "")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("-"):
        negative, text = True, text[:-1]
    if text.startswith("-"):
        negative, text = True, text[1:]
    if not _NUMBER_RE.match(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return -number if negative else number


_DATE_FORMATS = (
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d-%m-%y",
    "%d/%m/%y",
    "%d-%b-%y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %b %y",
    "%d-%B-%Y",
    "%d-%B-%y",
    "%Y-%m-%d",
    "%d/%b/%Y",
    "%d/%b/%y",
)
_EXCEL_EPOCH = date(1899, 12, 30)


def _plausible(d: date) -> bool:
    return 1990 <= d.year <= 2100


def parse_date(value: Any) -> date | None:
    """Parse dd-mm-yyyy, d-MMM-yy, ISO, Excel date cells and Excel serial numbers."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date() if _plausible(value) else None
    if isinstance(value, date):
        return value if _plausible(value) else None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        if 20000 <= value <= 80000:
            return _EXCEL_EPOCH + timedelta(days=int(value))
        return None
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.split(" ")[0]):
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
            return parsed if _plausible(parsed) else None
    return None


# --------------------------------------------------------------------- reading


def _read_csv(content: bytes) -> list[Row]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = content.decode("latin-1")
    return [list(r) for r in csv.reader(io.StringIO(text))]


def _read_excel(content: bytes) -> list[Row]:
    import pandas as pd

    frame = pd.read_excel(io.BytesIO(content), header=None, dtype=object, sheet_name=0)
    return [[None if pd.isna(c) else c for c in row] for row in frame.values.tolist()]


READERS: dict[str, Callable[[bytes], list[Row]]] = {
    ".csv": _read_csv,
    ".txt": _read_csv,
    ".xlsx": _read_excel,
    ".xlsm": _read_excel,
    ".xls": _read_excel,
}


def read_table(content: bytes, filename: str) -> list[Row]:
    """Read a file into raw rows, using the reader registered for its extension."""
    lowered = filename.lower()
    for extension, reader in READERS.items():
        if lowered.endswith(extension):
            try:
                return reader(content)
            except Exception as exc:  # corrupt / password-protected files
                raise ImportFormatError(f"Could not read '{filename}': {exc}") from exc
    if lowered.endswith(".xml"):
        raise ImportFormatError("Tally XML import is not supported yet - export as Excel or CSV.")
    raise ImportFormatError(f"Unsupported file type: '{filename}'. Use .xlsx, .xls or .csv.")


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ------------------------------------------------------------- header detection


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"[^a-z0-9% ]+", " ", str(value).lower())
    return re.sub(r"\s+", " ", text).strip()


SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("date", "voucher date", "vch date", "invoice date", "bill date"),
    "party": ("particulars", "party name", "party", "buyer", "supplier", "ledger", "name"),
    "voucher_type": ("voucher type", "vch type", "type"),
    "voucher_no": ("voucher no", "voucher number", "vch no", "invoice no", "bill no", "ref no"),
    "taxable_value": (
        "value",
        "taxable value",
        "basic value",
        "net value",
        "taxable amount",
        "amount",
    ),
    "total_value": (
        "gross total",
        "total",
        "invoice value",
        "invoice amount",
        "total amount",
        "gross amount",
    ),
}
_TAX_HINTS = ("cgst", "sgst", "igst", "utgst", "gst", "cess")
_TAX_EXACT = {"tax", "tax amount", "gst amount"}
_KNOWN_HEADERS = {s for group in SYNONYMS.values() for s in group} | {
    "gstin",
    "debit",
    "credit",
}


def detect_header_row(rows: list[Row], max_scan: int = 40) -> int:
    """Index of the real table header: the earliest row (within the first `max_scan`)
    with the most cells matching known Tally column names. Needs >= 2 matches."""
    best_index, best_score = -1, 1
    for index, row in enumerate(rows[:max_scan]):
        score = sum(1 for cell in row if _norm(cell) in _KNOWN_HEADERS)
        if score > best_score:
            best_index, best_score = index, score
    if best_index < 0:
        raise ImportFormatError(
            "Could not find the table header row (expected columns like Date, Particulars, "
            "Voucher No., Value, Gross Total)."
        )
    return best_index


def header_labels(row: Row) -> list[str]:
    return [str(c).strip() if c is not None else "" for c in row]


def suggest_mapping(headers: list[str]) -> Mapping:
    """Best-guess field -> header mapping from common Tally column names."""
    normalised = {h: _norm(h) for h in headers if h}
    mapping: Mapping = {}
    for field_name, names in SYNONYMS.items():
        for wanted in names:
            match = next((h for h, n in normalised.items() if n == wanted), None)
            if match:
                mapping[field_name] = match
                break
    tax_cols = [
        h
        for h, n in normalised.items()
        if n in _TAX_EXACT or (any(t in n for t in _TAX_HINTS) and "taxable" not in n)
    ]
    if tax_cols:
        mapping["tax_value"] = tax_cols
    return mapping


_PERIOD_RE = re.compile(
    r"(\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{2,4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})"
    r"\s*(?:to|-)\s*"
    r"(\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{2,4}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})",
    re.IGNORECASE,
)


def find_period(rows: list[Row], upto: int | None = None) -> tuple[date, date] | None:
    """Look for a '1-Apr-24 to 31-Mar-25' style period in the rows above the table."""
    for row in rows[: upto if upto is not None else 15]:
        for cell in row:
            match = _PERIOD_RE.search(str(cell)) if isinstance(cell, str) else None
            if match:
                start, end = parse_date(match.group(1)), parse_date(match.group(2))
                if start and end:
                    return start, end
    return None


def fy_from_period(period: tuple[date, date] | None) -> str | None:
    """FY label if the period is exactly one Apr-Mar year, else None."""
    if (
        period
        and period[0] == date(period[0].year, 4, 1)
        and period[1] == date(period[0].year + 1, 3, 31)
    ):
        return fy_label(period[0])
    return None


# -------------------------------------------------------------- voucher parsing


@dataclass(frozen=True)
class ParsedVoucher:
    voucher_type: str  # sales | credit_note | purchase | debit_note
    voucher_date: date
    voucher_no: str
    party: str | None
    taxable_value: Decimal
    tax_value: Decimal
    total_value: Decimal
    fy: str
    dedup_key: str
    source_row: int


@dataclass
class ParseResult:
    vouchers: list[ParsedVoucher] = field(default_factory=list)
    totals_ignored: int = 0
    invalid: list[str] = field(default_factory=list)
    period: tuple[date, date] | None = None


_TOTAL_RE = re.compile(r"^(grand\s*total|sub\s*total|total)s?\s*[:.]?$", re.IGNORECASE)
_TWO_PLACES = Decimal("0.01")


def make_dedup_key(voucher_type: str, voucher_no: str, voucher_date: date, total: Decimal) -> str:
    """Stable hash of voucher type + number + date + amount (the client is scoped
    separately by the database's unique constraint)."""
    raw = f"{voucher_type}|{voucher_no.strip().lower()}|{voucher_date.isoformat()}|{total:.2f}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _column_indexes(headers: list[str], mapping: Mapping) -> dict[str, list[int]]:
    """Resolve mapped header names to column positions (first match wins per header)."""
    lookup: dict[str, int] = {}
    normalised: dict[str, int] = {}
    for position, label in enumerate(headers):
        lookup.setdefault(label, position)
        normalised.setdefault(_norm(label), position)
    resolved: dict[str, list[int]] = {}
    for field_name, headers_for_field in mapping.items():
        names = [headers_for_field] if isinstance(headers_for_field, str) else headers_for_field
        positions = []
        for name in names:
            position = lookup.get(name, normalised.get(_norm(name)))
            if position is None:
                raise ImportFormatError(f"Mapped column '{name}' was not found in the file.")
            positions.append(position)
        if positions:
            resolved[field_name] = positions
    return resolved


def _cell(row: Row, positions: list[int] | None) -> Any:
    if not positions or positions[0] >= len(row):
        return None
    return row[positions[0]]


def _amount_sum(row: Row, positions: list[int] | None) -> Decimal | None:
    values = [parse_amount(row[p]) for p in positions or [] if p < len(row)]
    values = [v for v in values if v is not None]
    return sum(values, Decimal(0)) if values else None


def _voucher_type(raw: Any, report_type: str) -> str:
    text = _norm(raw)
    if report_type == REPORT_SALES:
        return "credit_note" if ("credit note" in text or "sales return" in text) else "sales"
    return "debit_note" if ("debit note" in text or "purchase return" in text) else "purchase"


def _split_amounts(taxable, tax, total) -> tuple[Decimal, Decimal, Decimal] | None:
    if taxable is None and total is None:
        return None
    if taxable is None:
        taxable = total - (tax or Decimal(0))
    if total is None:
        total = taxable + (tax or Decimal(0))
    if tax is None:
        tax = total - taxable
    return taxable, tax, total


def parse_vouchers(
    rows: list[Row], header_index: int, mapping: Mapping, report_type: str
) -> ParseResult:
    """Turn register rows below `header_index` into vouchers, skipping blank and
    Total / Grand Total rows and reporting rows that could not be understood."""
    if report_type not in (REPORT_SALES, REPORT_PURCHASE):
        raise ImportFormatError(f"'{report_type}' is not a voucher register.")
    headers = header_labels(rows[header_index])
    columns = _column_indexes(headers, mapping)
    if "date" not in columns or not ({"taxable_value", "total_value"} & set(columns)):
        raise ImportFormatError(
            "Map at least the Date column and one amount column (Value or Gross Total)."
        )

    result = ParseResult(period=find_period(rows, header_index))
    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        texts = [c.strip() for c in row if isinstance(c, str) and c.strip()]
        if not texts and all(c is None or c == "" for c in row):
            continue
        if any(_TOTAL_RE.match(t) for t in texts):
            result.totals_ignored += 1
            continue
        voucher_date = parse_date(_cell(row, columns["date"]))
        if voucher_date is None:
            result.invalid.append(f"Row {offset}: no valid date")
            continue
        amounts = _split_amounts(
            _amount_sum(row, columns.get("taxable_value")),
            _amount_sum(row, columns.get("tax_value")),
            _amount_sum(row, columns.get("total_value")),
        )
        if amounts is None:
            result.invalid.append(f"Row {offset}: no amount")
            continue
        vtype = _voucher_type(_cell(row, columns.get("voucher_type")), report_type)
        if vtype in ("credit_note", "debit_note"):
            amounts = tuple(abs(a) for a in amounts)
        taxable, tax, total = (a.quantize(_TWO_PLACES) for a in amounts)
        number = str(_cell(row, columns.get("voucher_no")) or "").strip()
        party = str(_cell(row, columns.get("party")) or "").strip() or None
        result.vouchers.append(
            ParsedVoucher(
                voucher_type=vtype,
                voucher_date=voucher_date,
                voucher_no=number,
                party=party,
                taxable_value=taxable,
                tax_value=tax,
                total_value=total,
                fy=fy_label(voucher_date),
                dedup_key=make_dedup_key(vtype, number, voucher_date, total),
                source_row=offset,
            )
        )
    return result


# ------------------------------------------------------------------ P&L parsing

_PL_LINES = (
    ("gross_profit", re.compile(r"^gross\s+profit\b"), 1),
    ("gross_profit", re.compile(r"^gross\s+loss\b"), -1),
    ("net_profit", re.compile(r"^net+\s+profit\b"), 1),
    ("net_profit", re.compile(r"^net+\s+loss\b"), -1),
)


@dataclass
class PlFigures:
    gross_profit: Decimal | None = None
    net_profit: Decimal | None = None
    fy: str | None = None
    period: tuple[date, date] | None = None


def parse_profit_loss(rows: list[Row]) -> PlFigures:
    """Find Gross Profit/Loss and Net (Nett) Profit/Loss lines in a P&L or trial
    balance summary. The amount is the first number to the right of the label;
    the first occurrence of each figure wins (Tally repeats them on both sides)."""
    figures = PlFigures(period=find_period(rows, 20))
    figures.fy = fy_from_period(figures.period)
    for row in rows:
        for position, cell in enumerate(row):
            if not isinstance(cell, str):
                continue
            label = _norm(cell)
            for key, pattern, sign in _PL_LINES:
                if pattern.match(label) and getattr(figures, key) is None:
                    amount = next(
                        (
                            a
                            for a in (parse_amount(c) for c in row[position + 1 :])
                            if a is not None
                        ),
                        None,
                    )
                    if amount is not None:
                        setattr(figures, key, (sign * abs(amount)).quantize(_TWO_PLACES))
    if figures.gross_profit is None and figures.net_profit is None:
        raise ImportFormatError("No 'Gross Profit' or 'Net Profit' lines found in this file.")
    return figures
