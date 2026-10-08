"""Presentation helpers for the client comparison and limit messages."""

import pandas as pd
import streamlit as st

from api_client import to_decimal
from components.money import ARROWS, format_money, format_pct


def render_comparison(report: dict, unit: str = "auto") -> None:
    heading = f"Turnover comparison · FY {report['fy']}"
    if report["is_ytd"]:
        heading += f" · YTD {report['period_label']}"
    elif report.get("is_period_matched"):
        heading += f" · Matched period {report['period_label']}"
    st.subheader(heading)
    table = []
    for row in report["rows"]:
        difference = to_decimal(row["difference"])
        change = to_decimal(row["change_pct"])
        table.append({
            "Metric": row["label"],
            f"FY {report['previous_fy']}": format_money(to_decimal(row["previous"]), unit),
            f"FY {report['fy']}": format_money(to_decimal(row["current"]), unit),
            "Difference": format_money(abs(difference), unit) + ARROWS[row["direction"]] if difference is not None else "—",
            "Change %": format_pct(change),
            "Status": row["status_label"],
        })
    st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)
    for note in report["notes"]:
        st.caption(note)
    projections = [f"{row['label']} ≈ {format_money(to_decimal(row['annualised']), unit)}" for row in report["rows"] if row["annualised"] is not None]
    if projections:
        st.caption("Annualised projection at the current run-rate: " + "; ".join(projections))


def render_limits(report: dict) -> None:
    active = [limit for limit in report["limits"] if limit["status"] in ("crossed", "approaching")]
    st.markdown("**Absolute limit alerts**")
    if not active:
        st.info("No absolute limit has been crossed or is being approached.")
        return
    for limit in active:
        st.write(limit["message"])
