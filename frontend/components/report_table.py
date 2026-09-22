"""The Turnover Comparison table from the requirement sheet, colour coded."""

from __future__ import annotations

from html import escape

import streamlit as st

from api_client import to_decimal
from components.money import ARROWS, format_money, format_pct
from components.status import SIGN_FLAGS, style_for

_TABLE_CSS = """
<style>
.tc-table {width:100%; border-collapse:collapse; font-size:0.95rem;}
.tc-table th {text-align:left; padding:8px 10px; border-bottom:2px solid rgba(128,128,128,.5);}
.tc-table td {padding:8px 10px; border-bottom:1px solid rgba(128,128,128,.25);}
.tc-table td.num, .tc-table th.num {text-align:right; white-space:nowrap;}
.tc-flag {font-size:.8rem; padding:1px 6px; border-radius:8px; border:1px solid currentColor; margin-left:6px;}
</style>
"""


def _status_cell(row: dict) -> str:
    emoji, colour = style_for(row["band"])
    text = f"{emoji} {escape(row['status_label'])}"
    flag = SIGN_FLAGS.get(row["sign_change"])
    badge = f'<span class="tc-flag">{flag}</span>' if flag else ""
    return f'<span style="color:{colour};font-weight:600">{text}</span>{badge}'


def _difference(row: dict, unit: str) -> str:
    difference = to_decimal(row["difference"])
    if difference is None:
        return "—"
    return format_money(abs(difference), unit) + ARROWS[row["direction"]]


def render_comparison(report: dict, unit: str = "auto") -> None:
    """Draw the comparison table for a /reports/comparison response."""
    heading = f"TURNOVER COMPARISON – FY {report['fy']}"
    if report["is_ytd"]:
        heading += f" (YTD {report['period_label']})"
    elif report.get("is_period_matched"):
        heading += f" (Comparison for {report['period_label']})"
    st.subheader(heading)
    st.markdown(f"**Client:** {escape(report['client_name'])}")

    body = ""
    for row in report["rows"]:
        colour = "inherit"
        change = to_decimal(row["change_pct"])
        if change is not None:
            colour = "#137333" if change > 0 else "#b42318" if change < 0 else "inherit"
        body += (
            f"<tr><td><b>{escape(row['label'])}</b></td>"
            f"<td class='num'>{format_money(to_decimal(row['previous']), unit)}</td>"
            f"<td class='num'>{format_money(to_decimal(row['current']), unit)}</td>"
            f"<td class='num' style='color:{colour}'>{_difference(row, unit)}</td>"
            f"<td class='num' style='color:{colour}'>{format_pct(change)}</td>"
            f"<td>{_status_cell(row)}</td></tr>"
        )
    header = (
        f"<tr><th>Particular</th><th class='num'>FY {report['previous_fy']}</th>"
        f"<th class='num'>FY {report['fy']}</th><th class='num'>Difference</th>"
        f"<th class='num'>Change %</th><th>Status</th></tr>"
    )
    st.markdown(
        f"{_TABLE_CSS}<table class='tc-table'>{header}{body}</table>", unsafe_allow_html=True
    )

    for note in report["notes"]:
        st.caption(f"ℹ️ {note}")
    projections = [
        f"{r['label']} ≈ {format_money(to_decimal(r['annualised']), unit)}"
        for r in report["rows"]
        if r["annualised"] is not None
    ]
    if projections:
        st.caption("Annualised projection at the current run-rate: " + "; ".join(projections))


def render_limits(report: dict) -> None:
    """Absolute-limit messages below the table (crossed / approaching only)."""
    active = [x for x in report["limits"] if x["status"] in ("crossed", "approaching")]
    st.markdown("**Absolute limit alerts**")
    if not active:
        st.success("No absolute limit has been crossed or is being approached.")
        return
    for limit in sorted(active, key=lambda x: x["status"] != "crossed"):
        emoji, _ = style_for(limit["status"])
        st.markdown(f"{emoji} {limit['message']}")
    st.caption("Limits are editable defaults - verify current limits under Settings.")
