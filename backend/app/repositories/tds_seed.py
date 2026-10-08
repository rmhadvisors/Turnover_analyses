"""Default rows of the TDS rate & threshold master.

Rows effective 01.04.2025 are the 'Rate Master' sheet of
docs/TDS_Applicability_Threshold_194C_194H_194J_194Q_194I.xlsx (as amended up to Finance
Act 2025). The earlier rows keep FY 2024-25 and before on the limits then in force, so a
later change never re-computes an older year. All of them are editable starting points:
the CA verifies them every year.
"""

from datetime import date
from decimal import Decimal as D

FROM_FA2025 = date(2025, 4, 1)
EARLIER = date(2023, 4, 1)
_OLD = "Value before Finance Act 2025 (verify). "

NATURE = {
    "194C": "Payment to contractor / sub-contractor (incl. labour, advertising, catering, transport, job work)",
    "194H": "Commission or brokerage (not insurance commission u/s 194D)",
    "194J(a)": "Fees for technical services, call-centre services, film royalty",
    "194J(b)": "Fees for professional services, royalty (other than film), non-compete fee",
    "194J(ba)": "Remuneration / fee / commission to a director (not salary)",
    "194Q": "Purchase of goods from a resident seller",
    "194I(a)": "Rent of plant & machinery / equipment",
    "194I(b)": "Rent of land, building (incl. factory building) or furniture / fittings",
    "194-IB": "Rent paid by an Individual/HUF not liable to audit u/s 44AB, above ₹50,000 a month",
}
SECTION = {key: key.split("(")[0] for key in NATURE}
SECTION["194-IB"] = "194-IB"

# key: (single, aggregate, rate Ind/HUF %, rate other %, rate no PAN %, base, remarks)
SHEET_ROWS = {
    "194C": (30000, 100000, "1", "2", "20", "full",
             "Single payment > ₹30,000 OR FY aggregate > ₹1,00,000. Transporter owning <= 10 "
             "goods carriages who furnishes a declaration with PAN: NIL. Work contract is covered; "
             "a contract for sale of goods is not."),
    "194H": (0, 20000, "2", "2", "20", "full",
             "Threshold raised from ₹15,000 w.e.f. 01.04.2025. Rate 2% w.e.f. 01.10.2024 (earlier 5%)."),
    "194J(a)": (0, 50000, "2", "2", "20", "full",
                "₹50,000 per category (raised from ₹30,000 w.e.f. 01.04.2025); tracked "
                "separately from 194J(b)."),
    "194J(b)": (0, 50000, "10", "10", "20", "full",
                "₹50,000 per category (raised from ₹30,000 w.e.f. 01.04.2025). Professionals: "
                "legal, medical, engineering, architecture, accountancy, interior decoration, etc."),
    "194J(ba)": (0, 0, "10", "10", "20", "full",
                 "Fee / remuneration / commission to a director (not salary): NO threshold."),
    "194Q": (0, 5000000, "0.1", "0.1", "5", "excess",
             "Per seller, cumulative from 1 April; TDS only on the amount above ₹50 lakh. Buyer "
             "only if its turnover > ₹10 Cr in the preceding FY. Not where the seller charged TCS "
             "u/s 206C(1H), or TDS applies under another section / 194-O."),
    "194I(a)": (0, 600000, "2", "2", "20", "full",
                "Per payee per FY (raised from ₹2,40,000 w.e.f. 01.04.2025)."),
    "194I(b)": (0, 600000, "10", "10", "20", "full",
                "Per payee per FY (raised from ₹2,40,000 w.e.f. 01.04.2025). Individual/HUF not "
                "liable to audit paying rent > ₹50,000 a month: s.194-IB instead."),
    "194-IB": (50000, 0, "2", "2", "20", "monthly",
               "Single payment column = the MONTHLY rent limit (rent > ₹50,000 for a month or part "
               "of a month). Deducted once, in the last month of the tenancy or of the FY, on the "
               "whole rent; with no PAN the TDS is capped at the last month's rent. Deposit with "
               "Form 26QC within 30 days of the end of the month of deduction; Form 16C."),
}  # fmt: skip

# Rows in force before FY 2025-26 (thresholds before Finance Act 2025).
EARLIER_ROWS = {
    "194C": (EARLIER, 30000, 100000, "1", "2", "20", "full", ""),
    "194H": (EARLIER, 0, 15000, "5", "5", "20", "full", "₹15,000; rate 5% until 30.09.2024."),
    "194H@oct24": (date(2024, 10, 1), 0, 15000, "2", "2", "20", "full",
                   "Rate cut to 2% w.e.f. 01.10.2024; threshold still ₹15,000."),
    "194J(a)": (EARLIER, 0, 30000, "2", "2", "20", "full", "₹30,000 per category."),
    "194J(b)": (EARLIER, 0, 30000, "10", "10", "20", "full", "₹30,000 per category."),
    "194J(ba)": (EARLIER, 0, 0, "10", "10", "20", "full", "No threshold."),
    "194Q": (EARLIER, 0, 5000000, "0.1", "0.1", "5", "excess", ""),
    "194I(a)": (EARLIER, 0, 240000, "2", "2", "20", "full", "₹2,40,000 per payee per FY."),
    "194I(b)": (EARLIER, 0, 240000, "10", "10", "20", "full", "₹2,40,000 per payee per FY."),
    "194-IB": (EARLIER, 50000, 0, "5", "5", "20", "monthly", "5% until 30.09.2024."),
    "194-IB@oct24": (date(2024, 10, 1), 50000, 0, "2", "2", "20", "monthly", "2% w.e.f. 01.10.2024."),
}  # fmt: skip


def _row(key, effective_from, single, aggregate, ind, other, no_pan, base, remarks) -> dict:
    return {
        "key": key,
        "section": SECTION[key],
        "nature": NATURE[key],
        "single_threshold": D(single),
        "aggregate_threshold": D(aggregate),
        "rate_individual": D(ind),
        "rate_other": D(other),
        "rate_no_pan": D(no_pan),
        "base": base,
        "effective_from": effective_from,
        "remarks": remarks,
    }


def default_rows() -> list[dict]:
    rows = [_row(key, FROM_FA2025, *values) for key, values in SHEET_ROWS.items()]
    for name, (start, *values) in EARLIER_ROWS.items():
        key = name.split("@")[0]
        remarks = _OLD + values[-1]
        rows.append(_row(key, start, *values[:-1], remarks.strip()))
    return rows


# 'Who deducts (Payer)' and 'Payee' columns of the TDS Summary sheet.
_NOT_SMALL_INDIVIDUAL = "Any person other than an Individual/HUF not liable to audit u/s 44AB"
WHO_DEDUCTS = {
    "194C": _NOT_SMALL_INDIVIDUAL + " (Govt., companies, firms, AOP, etc.)",
    "194H": "Individual/HUF liable to audit u/s 44AB and all other persons",
    "194J(a)": _NOT_SMALL_INDIVIDUAL,
    "194J(b)": _NOT_SMALL_INDIVIDUAL,
    "194J(ba)": _NOT_SMALL_INDIVIDUAL,
    "194Q": "A buyer whose turnover / gross receipts / sales exceeded ₹10 crore in the "
    "immediately preceding FY",
    "194I(a)": _NOT_SMALL_INDIVIDUAL,
    "194I(b)": _NOT_SMALL_INDIVIDUAL,
}
WHO_DEDUCTS["194-IB"] = "An Individual/HUF NOT liable to audit u/s 44AB (instead of 194I)"
PAYEE = {
    "194C": "Resident contractor / sub-contractor",
    "194Q": "Resident seller",
    "194-IB": "Resident landlord",
}
