"""Which statutory limits apply to which client, from the client's profile.

A limit's `applies_when` rule is a list of alternatives (any may match); each
alternative maps a profile field to its allowed values (all must match), e.g.
`[{"gst_registered": [False], "supplies": ["goods", "both"]}]`. A field the profile
leaves unknown (None) never excludes a limit, so an incomplete profile keeps today's
behaviour: the limit is checked. The reserved key "known" lists fields that must be
set for the alternative to match, for limits that only concern a narrow group (an LLP,
a professional): those stay off until the profile says the client is in that group.
An empty rule applies to every client.

Pure functions only - no database access.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

Rule = list[dict[str, list[Any]]]

ENTITY_TYPES = ("individual", "huf", "firm", "llp", "company", "other")
NATURES = ("business", "profession", "both")
SUPPLIES = ("goods", "services", "both")
PRESUMPTIVE = ("none", "44AD", "44ADA")
CHOICE_FIELDS = {
    "entity_type": ENTITY_TYPES,
    "nature": NATURES,
    "supplies": SUPPLIES,
    "presumptive": PRESUMPTIVE,
}
BOOL_FIELDS = ("gst_registered", "special_category", "cash_within_5pct")

# 44AB tax-audit turnover limits used to decide "audited in the previous year" (verify).
AUDIT_LIMIT_BUSINESS = Decimal(10_000_000)  # Rs 1 crore
AUDIT_LIMIT_BUSINESS_CASH = Decimal(100_000_000)  # Rs 10 crore when cash is within 5%
AUDIT_LIMIT_PROFESSION = Decimal(5_000_000)  # Rs 50 lakh gross receipts
BUYER_194Q_TURNOVER = Decimal(100_000_000)  # Rs 10 crore in the previous year

STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}  # fmt: skip
# Special-category states for the GST registration threshold (verify; editable per client).
SPECIAL_CATEGORY_CODES = {"01", "02", "05", "11", "12", "13", "14", "15", "16", "17", "18", "38"}
# 4th character of the PAN: holder type.
PAN_ENTITY = {"P": "individual", "H": "huf", "C": "company", "A": "other", "B": "other",
              "T": "other", "L": "other", "J": "other", "G": "other"}  # fmt: skip
PAN_HINTS = {"F": "PAN type F: a partnership firm or an LLP - choose which."}


@dataclass(frozen=True)
class GstinFacts:
    gstin: str
    state_code: str
    state_name: str | None
    special_category: bool
    entity_type: str | None  # None when the PAN type is ambiguous (F = firm or LLP)
    hint: str | None


def pan_entity(pan: str | None) -> str | None:
    """Suggested entity type from the 4th character of a PAN (None if unknown / ambiguous)."""
    value = (pan or "").strip().upper()
    return PAN_ENTITY.get(value[3]) if len(value) == 10 else None


def parse_gstin(gstin: str | None) -> GstinFacts | None:
    """State, special-category flag and suggested entity type from a 15-character GSTIN."""
    value = (gstin or "").strip().upper()
    if len(value) != 15 or not value[:2].isdigit():
        return None
    code, pan_type = value[:2], value[5]
    return GstinFacts(
        gstin=value,
        state_code=code,
        state_name=STATES.get(code),
        special_category=code in SPECIAL_CATEGORY_CODES,
        entity_type=PAN_ENTITY.get(pan_type),
        hint=PAN_HINTS.get(pan_type),
    )


def context(profile: Any | None, previous_turnover: Decimal | None) -> dict[str, Any]:
    """Profile facts plus the derived previous-year tests the rules can use."""
    facts: dict[str, Any] = {
        name: getattr(profile, name, None) for name in (*CHOICE_FIELDS, *BOOL_FIELDS)
    }
    audited = over_10cr = None
    if previous_turnover is not None:
        over_10cr = previous_turnover > BUYER_194Q_TURNOVER
        if facts["nature"] == "profession":
            audited = previous_turnover > AUDIT_LIMIT_PROFESSION
        else:
            limit = AUDIT_LIMIT_BUSINESS_CASH if facts["cash_within_5pct"] else AUDIT_LIMIT_BUSINESS
            audited = previous_turnover > limit
    facts["prev_year_audit"] = audited
    facts["prev_turnover_over_10cr"] = over_10cr
    return facts


KNOWN = "known"  # reserved rule key: fields that must be set in the profile


def _matches(option: dict[str, list[Any]], facts: dict[str, Any]) -> bool:
    if any(facts.get(name) is None for name in option.get(KNOWN, ())):
        return False
    return all(
        facts.get(name) is None or facts[name] in allowed
        for name, allowed in option.items()
        if name != KNOWN
    )


def applies(rule: Rule | None, facts: dict[str, Any]) -> bool:
    if not rule:
        return True
    return any(_matches(option, facts) for option in rule)


_LABELS = {
    "gst_registered": {True: "GST-registered", False: "not GST-registered"},
    "special_category": {True: "special-category state", False: "not a special-category state"},
    "cash_within_5pct": {True: "cash within 5%", False: "cash not within 5%"},
    "prev_year_audit": {True: "audited u/s 44AB last year", False: "not audited last year"},
    "prev_turnover_over_10cr": {True: "last year's turnover over Rs 10 cr", False: "last year's turnover up to Rs 10 cr"},
}  # fmt: skip


def describe(rule: Rule | None) -> str:
    """Plain-language rule, e.g. 'not GST-registered · supplies goods/both'."""
    if not rule:
        return "Every client"
    options = []
    for option in rule:
        parts = []
        for name, allowed in option.items():
            if name == KNOWN:
                parts.append(f"{' and '.join(n.replace('_', ' ') for n in allowed)} set in the profile")
            elif name in _LABELS:
                parts.append(" / ".join(_LABELS[name].get(v, str(v)) for v in allowed))
            else:
                parts.append(f"{name.replace('_', ' ')}: {'/'.join(str(v) for v in allowed)}")
        options.append(" · ".join(parts))
    return " OR ".join(options)


_NOT_SPECIAL = {"special_category": [False]}


def _professional(cash_within_5pct: bool) -> Rule:
    """44ADA: only for a client marked as a professional, or as opting for 44ADA."""
    base = {"presumptive": ["44ADA"], "cash_within_5pct": [cash_within_5pct]}
    return [
        {"nature": ["profession", "both"], **base, KNOWN: ["nature"]},
        {**base, KNOWN: ["presumptive"]},
    ]


# Default rules, by the default limit names in seed_data.py.
DEFAULT_RULES: dict[str, Rule] = {
    "GST registration - goods (regular states)": [
        {"gst_registered": [False], "supplies": ["goods", "both"], **_NOT_SPECIAL}
    ],
    "GST registration - services (regular states)": [
        {"gst_registered": [False], "supplies": ["services", "both"], **_NOT_SPECIAL}
    ],
    "GST registration - special-category states (goods)": [
        {"gst_registered": [False], "supplies": ["goods", "both"], "special_category": [True]}
    ],
    "GST registration - special-category states (services)": [
        {"gst_registered": [False], "supplies": ["services", "both"], "special_category": [True]}
    ],
    "Tax audit u/s 44AB - business (standard)": [
        {"nature": ["business", "both"], "cash_within_5pct": [False]}
    ],
    "Tax audit u/s 44AB - business (cash receipts/payments within 5%)": [
        {"nature": ["business", "both"], "cash_within_5pct": [True]}
    ],
    "Presumptive taxation u/s 44AD - business (standard)": [
        {"presumptive": ["44AD"], "cash_within_5pct": [False], "entity_type": ["individual", "huf", "firm"]}
    ],
    "Presumptive taxation u/s 44AD - business (cash within 5%)": [
        {"presumptive": ["44AD"], "cash_within_5pct": [True], "entity_type": ["individual", "huf", "firm"]}
    ],
    "Presumptive taxation u/s 44ADA - professionals (standard)": _professional(False),
    "Presumptive taxation u/s 44ADA - professionals (cash within 5%)": _professional(True),
    "E-invoicing applicability": [{"gst_registered": [True]}],
    "LLP audit": [{"entity_type": ["llp"], KNOWN: ["entity_type"]}],
}  # fmt: skip
# Rules these limits were seeded with before; a limit still on one of them is upgraded.
OLD_DEFAULT_RULES: dict[str, Rule] = {
    "Presumptive taxation u/s 44ADA - professionals (standard)": [
        {"presumptive": ["44ADA"], "cash_within_5pct": [False]}
    ],
    "Presumptive taxation u/s 44ADA - professionals (cash within 5%)": [
        {"presumptive": ["44ADA"], "cash_within_5pct": [True]}
    ],
    "LLP audit": [{"entity_type": ["llp"]}],
}
