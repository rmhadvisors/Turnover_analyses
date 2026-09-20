"""Display-only rupee formatting (lakhs / crores / full Indian grouping).

Presentation only - every figure shown here was calculated by the backend.
"""

from __future__ import annotations

from decimal import Decimal

LAKH = Decimal(100000)
CRORE = Decimal(10000000)
UNITS = {
    "Auto": "auto",
    "Lakhs (₹80.00 L)": "lakhs",
    "Crores (₹1.20 Cr)": "crores",
    "Full (₹1,00,00,000)": "full",
}


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_full(value: Decimal) -> str:
    whole, _, paise = f"{abs(value).quantize(Decimal('0.01'))}".partition(".")
    text = f"₹{_group_indian(whole)}" + (f".{paise}" if paise != "00" else "")
    return f"-{text}" if value < 0 else text


def format_money(value: Decimal | None, unit: str = "auto") -> str:
    """Format a rupee amount in the chosen unit; '—' when there is no value."""
    if value is None:
        return "—"
    if unit == "full":
        return format_full(value)
    if unit == "crores" or (unit == "auto" and abs(value) >= CRORE):
        return f"₹{(value / CRORE).quantize(Decimal('0.01'))} Cr"
    return f"₹{(value / LAKH).quantize(Decimal('0.01'))} L"


def format_pct(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.2f}%"


ARROWS = {"up": " ↑", "down": " ↓", "flat": "", "none": ""}
