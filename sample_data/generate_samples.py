"""Generates dummy Tally-style exports for three fictional clients.

Run from the repo root:  python sample_data/generate_samples.py
Output is deterministic (fixed random seed), so the committed files can be
regenerated identically. Needs openpyxl (already in backend/requirements.txt).

Each register mimics a Tally export: company / report / period header rows, a
blank row, the real header row, voucher rows (including credit / debit notes),
a blank row and a 'Grand Total' row.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).parent
GST = Decimal("0.18")

# name -> file style, and annual (sales, purchases) targets in rupees per FY
CLIENTS = {
    "Sharma Traders": {
        "style": "xlsx_plain",  # Indian-formatted text amounts, dd-mm-yyyy dates
        "sales": {"2024-25": 8_000_000, "2025-26": 10_000_000},  # +25%
        "purchases": {"2024-25": 5_600_000, "2025-26": 7_200_000},
        "gp": {"2024-25": 1_600_000, "2025-26": 1_800_000},
        "np": {"2024-25": 700_000, "2025-26": 500_000},  # -28.57%
    },
    "Patel Engineering Works": {
        "style": "csv",  # CSV, d-MMM-yy dates
        "sales": {
            "2024-25": 14_000_000,
            "2025-26": 9_000_000,
        },  # -35.7%, crosses 44AB in FY24-25
        "purchases": {"2024-25": 9_500_000, "2025-26": 6_800_000},
        "gp": {"2024-25": 3_000_000, "2025-26": 1_500_000},
        "np": {"2024-25": 900_000, "2025-26": -200_000},  # profit -> loss
    },
    "Iyer Consulting LLP": {
        "style": "xlsx_drcr",  # amounts carry Dr/Cr suffixes, real Excel dates
        "sales": {
            "2024-25": 3_000_000,
            "2025-26": 3_150_000,
        },  # +5%, crosses GST services limit
        "purchases": {"2024-25": 900_000, "2025-26": 980_000},
        "gp": {"2024-25": 2_100_000, "2025-26": 2_170_000},
        "np": {"2024-25": 1_200_000, "2025-26": 1_260_000},
    },
}


def fy_months(fy: str):
    start = int(fy[:4])
    return [(start, m) for m in range(4, 13)] + [(start + 1, m) for m in (1, 2, 3)]


def split_amount(rng: random.Random, total: int | Decimal, parts: int) -> list[Decimal]:
    weights = [rng.uniform(0.5, 1.5) for _ in range(parts)]
    scale = float(total) / sum(weights)
    amounts = [Decimal(round(w * scale * 100)) / 100 for w in weights]
    amounts[-1] += Decimal(total) - sum(amounts)  # make the parts add up exactly
    return amounts


def make_vouchers(rng, kind: str, annual: dict[str, int], prefix: str):
    """Returns (date, voucher_type, no, party, taxable) rows across both FYs."""
    parties = [
        "Alpha Enterprises",
        "Beta Supplies",
        "Gamma Industries",
        "Delta & Co",
        "Omega Ltd",
    ]
    rows, counter = [], 1
    for fy, total in annual.items():
        months = fy_months(fy)
        per_month = split_amount(rng, total, len(months))
        for (year, month), month_total in zip(months, per_month, strict=True):
            n = rng.randint(3, 5)
            for amount in split_amount(rng, month_total, n):
                day = rng.randint(1, 28)
                rows.append(
                    (
                        date(year, month, day),
                        kind,
                        f"{prefix}-{counter:04d}",
                        rng.choice(parties),
                        amount,
                    )
                )
                counter += 1
    # a couple of returns per FY (credit / debit notes), as a small % of the year
    note_kind = "Credit Note" if kind == "Sales" else "Debit Note"
    note_prefix = "CN" if kind == "Sales" else "DN"
    for i, (fy, total) in enumerate(annual.items(), start=1):
        for k in range(2):
            year, month = fy_months(fy)[3 + k * 4]
            amount = Decimal(round(total * 0.004 * 100)) / 100
            rows.append(
                (
                    date(year, month, 20),
                    note_kind,
                    f"{note_prefix}-{i}{k + 1:02d}",
                    rng.choice(parties),
                    amount,
                )
            )
    return sorted(rows, key=lambda r: (r[0], r[2]))


def indian(n: Decimal) -> str:
    s = f"{n:.2f}"
    whole, dec = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{whole}.{dec}"


def register_rows(name, title, vouchers, style):
    """Header block + table + Grand Total, as Tally lays out a register."""
    header = [
        "Date",
        "Particulars",
        "Voucher Type",
        "Voucher No.",
        "Value",
        "Output CGST",
        "Output SGST",
        "Gross Total",
    ]
    if title == "Purchase Register":
        header[5:7] = ["Input CGST", "Input SGST"]
    out = [[name], [title], ["1-Apr-2024 to 31-Mar-2026"], []]
    out.append(header)
    total_value = total_gross = Decimal(0)
    for d, vtype, no, party, taxable in vouchers:
        cgst = (taxable * GST / 2).quantize(Decimal("0.01"))
        gross = taxable + 2 * cgst
        sign = -1 if vtype in ("Credit Note", "Debit Note") else 1
        total_value += sign * taxable
        total_gross += sign * gross
        if style == "csv":
            date_cell = f"{d.day}-{d:%b-%y}"
        elif style == "xlsx_drcr":
            date_cell = d
        else:
            date_cell = f"{d:%d-%m-%Y}"
        if style == "xlsx_drcr":
            side = "Cr" if title == "Sales Register" else "Dr"
            side = ("Dr" if side == "Cr" else "Cr") if sign < 0 else side
            cells = [f"{indian(v)} {side}" for v in (taxable, cgst, cgst, gross)]
        elif style == "xlsx_plain":
            cells = [indian(v) for v in (taxable, cgst, cgst, gross)]
        else:
            cells = [f"{v:.2f}" for v in (taxable, cgst, cgst, gross)]
        out.append([date_cell, party, vtype, no, *cells])
    out.append([])
    out.append(["Grand Total", "", "", "", indian(total_value), "", "", indian(total_gross)])
    return out


def profit_loss_rows(name, fy, gp, np_):
    """A simplified Tally horizontal P&L: expenses on the left, income on the right."""
    start = int(fy[:4])
    gp_label = "Gross Profit c/o" if gp >= 0 else "Gross Loss c/o"
    np_label = "Nett Profit" if np_ >= 0 else "Nett Loss"
    return [
        [name],
        ["Profit & Loss A/c"],
        [f"1-Apr-{start} to 31-Mar-{start + 1}"],
        [],
        ["Particulars", "", "Particulars", ""],
        ["Opening Stock", "", "Sales Accounts", ""],
        [
            gp_label,
            indian(Decimal(abs(gp))),
            "Gross Profit b/f",
            indian(Decimal(abs(gp))),
        ],
        ["Indirect Expenses", "", "Indirect Incomes", ""],
        [np_label, indian(Decimal(abs(np_))), "", ""],
        [],
    ]


def write_rows(path: Path, rows, style: str):
    if path.suffix == ".csv":
        with path.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        return
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    for cell in ws["A"]:
        if isinstance(cell.value, date):
            cell.number_format = "dd-mm-yyyy"
    wb.save(path)


def main() -> None:
    for index, (name, cfg) in enumerate(CLIENTS.items()):
        rng = random.Random(1000 + index)
        slug = name.lower().replace(" ", "_")
        ext = "csv" if cfg["style"] == "csv" else "xlsx"
        for kind, title, prefix, key in (
            ("Sales", "Sales Register", "S", "sales"),
            ("Purchase", "Purchase Register", "P", "purchases"),
        ):
            vouchers = make_vouchers(rng, kind, cfg[key], prefix)
            rows = register_rows(name, title, vouchers, cfg["style"])
            write_rows(
                OUT / f"{slug}_{title.lower().replace(' ', '_')}.{ext}",
                rows,
                cfg["style"],
            )
        for fy in cfg["gp"]:
            rows = profit_loss_rows(name, fy, cfg["gp"][fy], cfg["np"][fy])
            write_rows(OUT / f"{slug}_profit_loss_fy{fy}.xlsx", rows, "xlsx_plain")
    print("Sample files written to", OUT)


if __name__ == "__main__":
    main()
