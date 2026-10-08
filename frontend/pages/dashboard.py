"""Dashboard: KPIs plus the all-clients turnover comparison (formerly the Summary page)."""

from decimal import Decimal

import pandas as pd
import plotly.express as px
import streamlit as st

import api_client as api
from components.common import ALERTS, CLIENTS, DATA, choose_client, page_setup, show_error, unit
from components.money import format_money
from components.ui import empty_state, kpi_card, section

BAND_COLOURS = {
    "Significant Increase": "#ff9da8",
    "Significant Decrease": "#ff9da8",
    "Moderate Increase": "#ffdfaa",
    "Moderate Decrease": "#ffdfaa",
    "Normal": "#a7b3c2",
    "No data": "#7f8b99",
}

page_setup("Dashboard", subtitle="Turnover movement, statutory limits and open alerts across clients.")
fy = st.session_state.get("global_fy")
client_id = st.session_state.get("global_client_id")
if not fy:
    empty_state("Select a financial year in the sidebar.")
    st.stop()

try:
    with st.spinner("Loading dashboard…"):
        clients = api.workspace()["clients"]
        data = api.dashboard(fy)
        tds_counts = api.tds_alert_counts(fy)
        tds_fy = api.tds_analysis_fy()
except api.ApiError as exc:
    show_error(exc)
    st.stop()

rows, counts = data["rows"], data["open_alerts"]
if client_id is not None:
    name = next((c["name"] for c in clients if c["id"] == client_id), "this client")
    st.info(f"Showing only **{name}** (selected in the sidebar).", icon=":material/filter_alt:")
    st.button("Show all clients", on_click=choose_client, args=(None,), icon=":material/groups:")
    clients = [c for c in clients if c["id"] == client_id]
    rows = [r for r in rows if r["client_id"] == client_id]
    counts = [c for c in counts if c["client_id"] == client_id]

k1, k2, k3, k4, k5 = st.columns(5)
with k1:
    kpi_card("Clients", len(clients))
with k2:
    kpi_card("Critical alerts", sum(c["critical"] for c in counts), f"Open, FY {fy}")
with k3:
    kpi_card("Warning alerts", sum(c["warning"] for c in counts), f"Open, FY {fy}")
with k4:
    kpi_card("Info alerts", sum(c["info"] for c in counts), f"Open, FY {fy}")
with k5:
    kpi_card("Clients over a limit", sum(int(r["limits_crossed"]) > 0 for r in rows), f"FY {fy}")

if fy != tds_fy:
    st.caption(f"TDS analysis is available for FY {tds_fy} only - choose it in the sidebar to see TDS figures.")
t1, t2, t3, t4 = st.columns(4)
tds_rows = [r for r in rows if r.get("tds_parties_crossed") is not None]
with t1:
    kpi_card("TDS alerts open", tds_counts["critical"] + tds_counts["high"] + tds_counts["warning"],
             f"🔴 {tds_counts['critical']} not deducted · 🟠 {tds_counts['high']} short · 🟡 {tds_counts['warning']} near")  # fmt: skip
with t2:
    kpi_card("Parties over a TDS threshold", sum(r["tds_parties_crossed"] for r in tds_rows), f"FY {fy}")
with t3:
    kpi_card("TDS payable", format_money(sum((api.to_decimal(r["tds_payable"]) for r in tds_rows), Decimal(0)), unit()))
with t4:
    kpi_card("TDS not deducted", format_money(sum((api.to_decimal(r["tds_not_deducted"]) for r in tds_rows), Decimal(0)), unit()),
             "Shortfall across clients")  # fmt: skip


def has_data(row: dict) -> bool:
    return row["current_turnover"] is not None or row["previous_turnover"] is not None


@st.fragment
def client_table(rows: list[dict]) -> None:
    """Filter, table, chart and export; changing the filter reruns only this part."""
    section("Client turnover")
    if not rows:
        empty_state("No clients yet. Add one on the Clients page.")
        return
    only_flagged = st.toggle("Only clients that need attention", value=False)
    if only_flagged:
        rows = [
            r
            for r in rows
            if r["band"] not in (None, "normal") or r["limits_crossed"] or r["open_alerts"]
            or api.to_decimal(r.get("tds_not_deducted"))
        ]
        if not rows:
            empty_state("No clients need attention for this year.")
            return

    mode = unit()
    table = []
    for row in rows:
        change = api.to_decimal(row["change_pct"])
        present = has_data(row)
        table.append(
            {
                "Client": row["client_name"],
                f"FY {fy} turnover" + (" (YTD)" if row["is_ytd"] else ""): format_money(
                    api.to_decimal(row["current_turnover"]), mode
                )
                if row["current_turnover"] is not None
                else "No data",
                "Previous year": format_money(api.to_decimal(row["previous_turnover"]), mode)
                if row["previous_turnover"] is not None
                else "No data",
                "Change %": float(change) if change is not None else None,
                "Band": row["status_label"] if present else "No data",
                "Limits crossed": row["limits_crossed"],
                "Approaching": row["limits_approaching"],
                "Open alerts": row["open_alerts"],
                "TDS: parties crossed": row.get("tds_parties_crossed"),
                "TDS not deducted": format_money(api.to_decimal(row["tds_not_deducted"]), mode)
                if row.get("tds_not_deducted") is not None
                else "No TDS data",
            }
        )
    frame = pd.DataFrame(table)
    styled = frame.style.map(
        lambda value: f"color: {BAND_COLOURS[value]}; font-weight: 600" if value in BAND_COLOURS else "",
        subset=["Band"],
    )
    st.dataframe(
        styled,
        width="stretch",
        hide_index=True,
        column_config={
            "Change %": st.column_config.NumberColumn(format="%.2f%%"),
            "Open alerts": st.column_config.NumberColumn(help="Unacknowledged critical, high and warning alerts"),
            "TDS: parties crossed": st.column_config.NumberColumn(help="Parties over a TDS threshold this year"),
        },
    )
    missing = [r["client_name"] for r in rows if not has_data(r)]
    if missing:
        st.caption(
            f"No data for FY {fy} or the year before: {', '.join(missing)}. "
            "Import Tally data or enter figures on the Data page."
        )

    values = [api.to_decimal(r["current_turnover"]) for r in rows]
    chart_rows = [(r["client_name"], v) for r, v in zip(rows, values) if v is not None]
    if chart_rows:
        section("Turnover by client")
        peak = max(abs(v) for _, v in chart_rows)
        if mode == "full":
            divisor, suffix = Decimal(1), "₹"
        elif mode == "crores" or (mode == "auto" and peak >= Decimal(10_000_000)):
            divisor, suffix = Decimal(10_000_000), "₹ crores"
        else:
            divisor, suffix = Decimal(100_000), "₹ lakhs"
        chart = pd.DataFrame([{"Client": n, "Turnover": float(v / divisor)} for n, v in chart_rows])
        fig = px.bar(
            chart, x="Client", y="Turnover", labels={"Turnover": f"Turnover ({suffix})"},
            color_discrete_sequence=["#a78bfa"],
        )  # fmt: skip
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin={"t": 20, "b": 20}, showlegend=False,
        )  # fmt: skip
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    section("Export")
    csv_col, excel_col = st.columns(2)
    csv_col.download_button(
        "Download CSV",
        frame.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"Turnover_Summary_FY{fy}.csv",
        mime="text/csv",
    )
    if client_id is None:
        excel_col.download_button(
            "Download Excel (.xlsx)",
            data=lambda: api.export_summary(fy, unit()),
            file_name=f"Turnover_Summary_FY{fy}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


client_table(rows)

section("Quick actions")
a, b, c = st.columns(3)
a.page_link(DATA, label="Import Tally data or enter figures", icon=":material/upload_file:")
b.page_link(CLIENTS, label="Client reports", icon=":material/assessment:")
c.page_link(ALERTS, label="Review alerts", icon=":material/notifications:")
