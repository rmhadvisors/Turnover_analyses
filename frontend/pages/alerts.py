import streamlit as st

import api_client as api
from components.alert_badge import alert_summary, alert_value, severity_chip
from components.common import page_setup, show_error, unit

page_setup("Alerts", "🔔")

clients = {c["id"]: c["name"] for c in api.list_clients()}
years = api.financial_years()

f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
client_id = f1.selectbox(
    "Client", [None, *clients], format_func=lambda i: "All clients" if i is None else clients[i]
)
fy = f2.selectbox(
    "Financial year", [None, *years], format_func=lambda y: "All" if y is None else f"FY {y}"
)
severity = f3.selectbox(
    "Severity",
    [None, "critical", "warning", "info"],
    format_func=lambda s: "All" if s is None else s.title(),
)
open_only = f4.checkbox("Unacknowledged only", value=True)

by = st.text_input("Acknowledge as", key="ack_by", placeholder="Your name")

try:
    alerts = api.list_alerts(client_id, fy, severity, open_only)
except api.ApiError as exc:
    show_error(exc)
    st.stop()

st.caption(f"{len(alerts)} alert(s)")
if not alerts:
    st.success("Nothing to show for these filters.")

for alert in alerts:
    with st.container(border=True):
        text, action = st.columns([5, 1])
        text.markdown(
            f"{severity_chip(alert['severity'])} &nbsp; **{alert['client_name']}** · FY {alert['fy']}",
            unsafe_allow_html=True,
        )
        text.markdown(alert_summary(alert))
        text.caption(
            f"Value: {alert_value(alert, unit())} · {alert['threshold_description'] or ''} · "
            f"triggered {alert['triggered_at'][:16].replace('T', ' ')}"
        )
        if alert["acknowledged"]:
            action.caption(f"✅ {alert['acknowledged_by']}")
        elif action.button("Acknowledge", key=f"ack_{alert['id']}", disabled=not by.strip()):
            try:
                api.acknowledge_alert(alert["id"], by.strip())
                st.rerun()
            except api.ApiError as exc:
                show_error(exc)

if not by.strip() and any(not a["acknowledged"] for a in alerts):
    st.caption("Enter your name above to enable the Acknowledge buttons.")
