"""The client report (comparison, limits, monthly chart, exports, alerts) for one client + FY."""

from __future__ import annotations

import streamlit as st

import api_client as api
from components.charts import render_monthly
from components.common import unit
from components.money import format_money
from components.report_table import render_comparison
from components.tds import render_summary_block
from components.ui import empty_state, kpi_card, section


def render_client_report(client: dict, fy: str | None) -> None:
    if not fy:
        empty_state("Select a financial year in the sidebar.")
        return
    try:
        with st.spinner("Preparing client report…"):
            data = api.client_report(client["id"], fy)  # comparison + monthly + alerts, one call
    except api.ApiError as exc:
        st.error(str(exc))
        return
    report, monthly, alerts = data["comparison"], data["monthly"], data["alerts"]

    cards = st.columns(4)
    for col, row in zip(cards, report["rows"][:4]):
        change = api.to_decimal(row["change_pct"])
        delta = f"{change:+.2f}% YoY" if change is not None else "No comparison"
        value = api.to_decimal(row["current"])
        with col:
            kpi_card(row["label"], format_money(value, unit()) if value is not None else "No data", delta)

    render_comparison(report, unit())
    section("Statutory limit usage")
    if not report["limits"]:
        empty_state("No statutory limits are configured.")
    else:
        for limit in report["limits"]:
            amount = api.to_decimal(limit["amount"]) or 0
            used = api.to_decimal(limit["cumulative"]) or 0
            ratio = float(max(0, min(used / amount, 1))) if amount else 0
            st.markdown(
                f"**{limit['name']}** · {format_money(used, unit())} of "
                f"{format_money(amount, unit())} · {limit['status'].title()}"
            )
            st.progress(ratio)

    render_summary_block(data.get("tds"))

    section("Monthly turnover")
    render_monthly(monthly)

    section("Export report")
    safe_name = "".join(c if c.isalnum() else "_" for c in client["name"])
    stem = f"Turnover_Comparison_{safe_name}_FY{fy}"
    xlsx_col, pdf_col, _ = st.columns([1, 1, 3])
    xlsx_col.download_button(
        "Excel (.xlsx)",
        data=lambda: api.export_report(client["id"], fy, "xlsx", unit()),
        file_name=f"{stem}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_xlsx",
    )
    pdf_col.download_button(
        "PDF",
        data=lambda: api.export_report(client["id"], fy, "pdf", unit()),
        file_name=f"{stem}.pdf",
        mime="application/pdf",
        key="dl_pdf",
    )
    st.caption(
        "Exports use the amount display unit selected in the sidebar. Excel cells contain numeric values."
    )

    section("Alerts for this client and year")
    if not alerts:
        st.caption("No alerts recorded.")
    else:
        st.dataframe(
            [
                {
                    "Severity": a["severity"].title(),
                    "Type": f"TDS {a['section']}" if a.get("kind") == "tds" else "Turnover",
                    "Metric": a["metric_label"],
                    "Status": a["new_status"].replace("_", " ").title(),
                    "Triggered": str(a["triggered_at"]).replace("T", " ")[:16],
                    "Acknowledged": a["acknowledged_by"] or "Open",
                }
                for a in alerts
            ],
            width="stretch",
            hide_index=True,
        )
