"""Rupee formatting helpers: lakhs/crores and Indian comma grouping."""

from __future__ import annotations

from decimal import Decimal

LAKH = Decimal(100000)
CRORE = Decimal(10000000)


def format_indian_commas(value: Decimal) -> str:
    """Full value with Indian digit grouping, e.g. 10000000 -> '₹1,00,00,000'."""
    negative = value < 0
    value = abs(value).quantize(Decimal("0.01"))
    int_part, _, dec_part = str(value).partition(".")

    if len(int_part) > 3:
        last_three = int_part[-3:]
        rest = int_part[:-3]
        groups: list[str] = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        int_part = ",".join(groups) + "," + last_three

    result = f"₹{int_part}"
    if dec_part and dec_part != "00":
        result += f".{dec_part}"
    return f"-{result}" if negative else result


def _scaled(value: Decimal, unit: Decimal, suffix: str) -> str:
    scaled = abs(value / unit).quantize(Decimal("0.01"))
    sign = "-" if value < 0 and scaled != 0 else ""
    return f"{sign}₹{scaled} {suffix}"


def format_lakhs(value: Decimal) -> str:
    """Value expressed in lakhs, e.g. 8000000 -> '₹80.00 L' (negatives: '-₹2.00 L')."""
    return _scaled(value, LAKH, "L")


def format_crores(value: Decimal) -> str:
    """Value expressed in crores, e.g. 10000000 -> '₹1.00 Cr'."""
    return _scaled(value, CRORE, "Cr")


def format_indian(value: Decimal, unit: str = "auto") -> str:
    """Format a rupee value. unit: 'lakhs', 'crores', 'full', or 'auto'
    (crores for values >= 1 crore, lakhs otherwise)."""
    if unit == "lakhs":
        return format_lakhs(value)
    if unit == "crores":
        return format_crores(value)
    if unit == "full":
        return format_indian_commas(value)
    return format_crores(value) if abs(value) >= CRORE else format_lakhs(value)
