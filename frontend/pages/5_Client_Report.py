import streamlit as st

import api_client as api
from components.alert_badge import alert_summary
from components.charts import render_monthly
from components.common import page_setup, select_client, select_fy, show_error, unit
from components.report_table import render_comparison, render_limits
from components.status import with_emoji

page_setup("Client Report", "📑")

left, right = st.columns(2)
with left:
    client = select_client()
with right:
    fy = select_fy()

if not client or not fy:
    st.info("Add figures or import Tally data first - then a report appears here.")
    st.stop()

try:
    report = api.comparison(client["id"], fy)
    monthly = api.monthly(client["id"], fy)
except api.ApiError as exc:
    show_error(exc)
    st.stop()

render_comparison(report, unit())
st.divider()
render_limits(report)

st.divider()
render_monthly(monthly)

st.divider()
st.markdown("**Export this report**")
safe_name = "".join(c if c.isalnum() else "_" for c in client["name"])
stem = f"Turnover_Comparison_{safe_name}_FY{fy}"
excel_col, pdf_col, _ = st.columns([1, 1, 3])
# `data` is a callable, so the file is only generated when the button is clicked
excel_col.download_button(
    "⬇️ Excel (.xlsx)",
    data=lambda: api.export_report(client["id"], fy, "xlsx", unit()),
    file_name=f"{stem}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    key="dl_xlsx",
)
pdf_col.download_button(
    "⬇️ PDF",
    data=lambda: api.export_report(client["id"], fy, "pdf", unit()),
    file_name=f"{stem}.pdf",
    mime="application/pdf",
    key="dl_pdf",
)
st.caption(
    "Exports use the amount display unit chosen in the sidebar. Excel cells hold real numbers."
)

st.divider()
st.markdown("**Alerts for this client and year**")
alerts = api.list_alerts(client_id=client["id"], fy=fy)
if not alerts:
    st.caption("No alerts recorded.")
for alert in alerts[:15]:
    state = "✅ acknowledged" if alert["acknowledged"] else "open"
    st.markdown(f"{with_emoji(alert['new_status'])} · {alert_summary(alert)} · _{state}_")
