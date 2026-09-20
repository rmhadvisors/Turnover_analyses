from datetime import date

import streamlit as st

import api_client as api
from components.common import page_link, page_setup, select_client, show_error

page_setup("Manual Entry", "✍️")

FIELDS = (
    ("turnover", "Turnover (₹)"),
    ("purchases", "Purchases (₹)"),
    ("gross_profit", "Gross Profit (₹)"),
    ("net_profit", "Net Profit (₹, negative = loss)"),
)


def fy_options() -> list[str]:
    """Financial years to choose from: two years back to next year."""
    start = date.today().year if date.today().month >= 4 else date.today().year - 1
    return [f"{y}-{str(y + 1)[2:]}" for y in range(start + 1, start - 6, -1)]


def previous_of(fy: str) -> str:
    y = int(fy[:4]) - 1
    return f"{y}-{str(y + 1)[2:]}"


client = select_client()
if client:
    years = fy_options()
    current_fy = st.selectbox("Current FY", years, index=1, format_func=lambda y: f"FY {y}")
    previous_fy = previous_of(current_fy)
    st.caption(f"Compared with previous FY **{previous_fy}**.")

    saved = {f["fy"]: f for f in api.list_figures(client["id"])}

    def prefill(fy: str, key: str) -> str:
        value = (saved.get(fy) or {}).get(key)
        return "" if value is None else str(value)

    st.info("Leave a box empty to keep the value already stored. Commas are fine (1,00,000).")
    with st.form("entry"):
        left, right = st.columns(2)
        left.markdown(f"##### Previous year · FY {previous_fy}")
        right.markdown(f"##### Current year · FY {current_fy}")
        typed: dict[str, str] = {}
        for key, label in FIELDS:
            typed[f"previous_{key}"] = left.text_input(
                label, prefill(previous_fy, key), key=f"p_{key}_{client['id']}_{previous_fy}"
            )
            typed[f"current_{key}"] = right.text_input(
                label, prefill(current_fy, key), key=f"c_{key}_{client['id']}_{current_fy}"
            )
        submitted = st.form_submit_button("Save and re-check", type="primary")

    if submitted:
        try:
            payload = {
                "client_id": client["id"],
                "previous_fy": previous_fy,
                "current_fy": current_fy,
            }
            payload.update({k: api.parse_money_input(v) for k, v in typed.items()})
            result = api.save_entry(payload)
        except (ValueError, api.ApiError) as exc:
            show_error(exc)
        else:
            st.success(f"Saved. All checks re-run: {result['alerts_raised']} new alert(s) raised.")
            if result["alerts_raised"]:
                page_link("pages/7_Alerts.py", "View alerts →")
            page_link("pages/5_Client_Report.py", "Open the client report →")
