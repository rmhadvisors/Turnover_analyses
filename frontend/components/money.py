"""Compatibility wrappers for display-only rupee and percentage formatting."""

from decimal import Decimal

from components.ui import format_inr

UNITS = {
    "Auto": "auto",
    "Lakhs": "lakhs",
    "Crores": "crores",
    "Full": "full",
}


def format_full(value: Decimal) -> str:
    return format_inr(value, "full")


def format_money(value: Decimal | None, unit: str = "auto") -> str:
    return format_inr(value, unit)


def format_pct(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.2f}%"


ARROWS = {"up": " ↑", "down": " ↓", "flat": "", "none": ""}
