"""Shared presentation helpers and the app's single custom stylesheet."""

from __future__ import annotations

from decimal import Decimal
from html import escape

import streamlit as st

LAKH = Decimal(100_000)
CRORE = Decimal(10_000_000)
DISPLAY_MODES = {
    "Auto": "auto",
    "Lakhs": "lakhs",
    "Crores": "crores",
    "Full": "full",
}

_CSS = """
<style>
.block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1500px;}
h1 {letter-spacing: -.035em;}
.ui-subtitle {color: #a7b3c2; margin-top: -.65rem; margin-bottom: 1.4rem;}
.ui-kpi {background: #202b39; border: 1px solid #354253; border-radius: 12px; padding: 1rem 1.1rem; min-height: 104px;}
.ui-kpi-label {color: #a7b3c2; font-size: .84rem; margin-bottom: .45rem;}
.ui-kpi-value {font-size: 1.55rem; font-weight: 650; line-height: 1.2;}
.ui-kpi-delta {color: #a7b3c2; font-size: .82rem; margin-top: .35rem;}
.ui-badge {display:inline-block; padding:.17rem .55rem; border-radius:999px; font-size:.78rem; font-weight:650;}
.ui-critical {color:#ffc2c2; background:#532e37;} .ui-high {color:#ffcfa8; background:#5a3a26;} .ui-warning {color:#ffdfaa; background:#55452d;}
.ui-info {color:#c4dcff; background:#2e435d;} .ui-neutral {color:#d4dde8; background:#384657;}
</style>
"""


def inject_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str) -> None:
    st.title(title)
    st.markdown(f'<div class="ui-subtitle">{escape(subtitle)}</div>', unsafe_allow_html=True)


def section(title: str) -> None:
    st.subheader(title)


def kpi_card(label: str, value: str | int, delta: str | None = None) -> None:
    detail = f'<div class="ui-kpi-delta">{escape(delta)}</div>' if delta else ""
    st.markdown(
        f'<div class="ui-kpi"><div class="ui-kpi-label">{escape(label)}</div>'
        f'<div class="ui-kpi-value">{escape(str(value))}</div>{detail}</div>',
        unsafe_allow_html=True,
    )


def severity_badge(level: str) -> str:
    level = (level or "info").lower()
    css_class = level if level in {"critical", "high", "warning", "info"} else "neutral"
    return f'<span class="ui-badge ui-{css_class}">{escape(level.title())}</span>'


def empty_state(msg: str) -> None:
    st.info(msg)


def format_inr(value, mode: str = "auto") -> str:
    """Format any rupee amount in Indian grouping or the selected scaled unit."""
    if value is None or value == "":
        return "—"
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    mode = DISPLAY_MODES.get(mode, mode)
    if mode == "full":
        absolute = f"{abs(amount):,.2f}"
        whole, _, paise = absolute.partition(".")
        # Convert Western grouping to Indian grouping.
        digits = whole.replace(",", "")
        grouped = digits if len(digits) <= 3 else digits[-3:]
        head = digits[:-3]
        while head:
            grouped = head[-2:] + "," + grouped
            head = head[:-2]
        text = f"₹{grouped}" + (f".{paise}" if paise != "00" else "")
        return f"-{text}" if amount < 0 else text
    divisor, suffix = (CRORE, "Cr") if mode == "crores" or (mode == "auto" and abs(amount) >= CRORE) else (LAKH, "L")
    scaled = abs(amount / divisor).quantize(Decimal("0.01"))
    sign = "-" if amount < 0 and scaled else ""
    return f"{sign}₹{scaled} {suffix}"
