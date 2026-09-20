"""Streamlit entry point for the Turnover Analysis & Alert Tool (home page)."""

import streamlit as st

import api_client as api
from components.common import page_setup

page_setup("Turnover Analysis & Alert Tool", "📊")

st.write(
    "Compare each client's sales, purchases, gross profit and net profit with the previous "
    "financial year, and get alerted the moment a value crosses a threshold."
)

clients = api.list_clients()
actionable = [a for a in api.list_alerts(unacknowledged_only=True) if a["severity"] != "info"]

left, middle, right = st.columns(3)
left.metric("Clients", len(clients))
middle.metric("Open alerts", len(actionable))
right.metric("Critical", sum(a["severity"] == "critical" for a in actionable))

if not clients:
    st.info("Start on **Clients** to add a client, then import Tally data or enter figures.")
elif actionable:
    st.subheader("Latest open alerts")
    for alert in actionable[:8]:
        emoji = "🔴" if alert["severity"] == "critical" else "🟡"
        st.markdown(
            f"{emoji} **{alert['client_name']}** · FY {alert['fy']} · {alert['metric_label']} "
            f"→ {alert['new_status'].replace('_', ' ')}"
        )
    st.caption("Open the **Alerts** page to filter and acknowledge them.")

st.divider()
st.markdown(
    "**Workflow:** 1 Clients → 2 Import Tally Data (or 3 Manual Entry) → "
    "5 Client Report / 6 Summary → 7 Alerts. Thresholds are on page 4."
)
