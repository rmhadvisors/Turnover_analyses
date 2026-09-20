"""Small coloured alert chips and the alert card used on the Alerts page."""

from __future__ import annotations

from html import escape

import streamlit as st

from api_client import to_decimal
from components.money import format_money
from components.status import SEVERITY_STYLE, status_text, style_for


def chip(status: str | None, label: str | None = None) -> str:
    """HTML for a coloured status chip."""
    emoji, colour = style_for(status)
    text = escape(label or status_text(status))
    return f'<span style="color:{colour};font-weight:600">{emoji} {text}</span>'


def severity_chip(severity: str) -> str:
    emoji, colour = SEVERITY_STYLE[severity]
    return f'<span style="color:{colour};font-weight:700">{emoji} {severity.title()}</span>'


def alert_value(alert: dict, unit: str) -> str:
    """The alert's value: a % for band alerts, rupees for absolute-limit alerts."""
    value = to_decimal(alert["value"])
    if alert["metric"].startswith("limit:"):
        return format_money(value, unit)
    return "—" if value is None else f"{value:.2f}%"


def alert_summary(alert: dict) -> str:
    """One readable sentence for an alert."""
    old = status_text(alert["old_status"]) if alert["old_status"] else "-"
    text = f"{alert['metric_label']}: {old} → {status_text(alert['new_status'])}"
    if alert["crossed_on"]:
        voucher = f" (voucher {alert['crossed_voucher_no']})" if alert["crossed_voucher_no"] else ""
        text += f", crossed on {alert['crossed_on']}{voucher}"
    return text


def render_alert_count(count: int) -> None:
    (st.error if count else st.success)(f"🔔 {count} unacknowledged alert(s)")
