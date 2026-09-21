import pandas as pd
import streamlit as st

import api_client as api
from components.common import page_setup, select_fy, show_error, unit
from components.money import format_money
from components.status import SIGN_FLAGS, with_emoji

page_setup("Summary - All Clients", "🧮")

fy = select_fy()
if not fy:
    st.info("No data yet.")
    st.stop()

try:
    rows = api.summary(fy)
except api.ApiError as exc:
    show_error(exc)
    st.stop()

if not rows:
    st.info("No clients yet.")
    st.stop()

only_flagged = st.checkbox(
    "Only clients that need attention (band changed, limit hit or open alert)"
)
table = []
for row in rows:
    change = api.to_decimal(row["change_pct"])
    attention = row["band"] not in (None, "normal") or row["limits_crossed"] or row["open_alerts"]
    if only_flagged and not attention:
        continue
    table.append(
        {
            "Client": row["client_name"],
            f"FY {fy} turnover"
            + (" (YTD)" if row["is_ytd"] else ""): format_money(
                api.to_decimal(row["current_turnover"]), unit()
            ),
            "Previous year": format_money(api.to_decimal(row["previous_turnover"]), unit()),
            "Change %": float(change) if change is not None else None,
            "Status": with_emoji(row["band"], row["status_label"]),
            "Net profit flag": SIGN_FLAGS.get(row["net_profit_flag"], ""),
            "Limits crossed": row["limits_crossed"],
            "Approaching": row["limits_approaching"],
            "Open alerts": row["open_alerts"],
        }
    )

st.caption("Click a column heading to sort - e.g. by Change % to see the biggest movers first.")
frame = pd.DataFrame(table)
st.dataframe(
    frame,
    width="stretch",
    hide_index=True,
    column_config={"Change %": st.column_config.NumberColumn(format="%.2f%%")},
)

st.download_button(
    "⬇️ Download summary (Excel)",
    data=lambda: api.export_summary(fy, unit()),
    file_name=f"Turnover_Summary_FY{fy}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
