"""Manual entry of yearly figures for the sidebar's client and financial year."""

from __future__ import annotations

import streamlit as st

import api_client as api
from components.common import ALERTS, CLIENTS, page_link, previous_fy
from components.ui import section, severity_badge

FIELDS = (
    ("turnover", "Turnover"),
    ("purchases", "Purchases"),
    ("gross_profit", "Gross profit"),
    ("net_profit", "Net profit (negative = loss)"),
)
_RESULT_KEY = "manual_entry_result"


def render_manual_entry(client: dict, current_fy: str | None) -> None:
    if not current_fy:
        st.info("Select the financial year in the sidebar.")
        return
    prior_fy = previous_fy(current_fy)
    st.caption(
        f"Entering **FY {current_fy}** and its previous year **FY {prior_fy}**. "
        "To enter another year, change the financial year in the sidebar."
    )
    result = st.session_state.pop(_RESULT_KEY, None)
    if result is not None:
        st.success(f"Saved. Checks completed; {result['alerts_raised']} new alert(s) raised.")
        if result["alerts_raised"]:
            page_link(ALERTS, "View alerts")
        page_link(CLIENTS, "Open the client report")

    try:
        with st.spinner("Loading saved figures…"):
            saved = {row["fy"]: row for row in api.list_figures(client["id"])}
            settings = api.get_settings()
    except api.ApiError as exc:
        st.error(str(exc))
        return

    typed = {}
    left, right = st.columns(2)
    for column, heading, fy, prefix in (
        (left, "Previous FY", prior_fy, "previous"),
        (right, "Current FY", current_fy, "current"),
    ):
        with column.container(border=True):
            section(f"{heading} · {fy}")
            for key, label in FIELDS:
                old = (saved.get(fy) or {}).get(key)
                default = "" if old is None else str(old)
                typed[f"{prefix}_{key}"] = st.text_input(
                    f"{label} (₹)", value=default, key=f"manual_{client['id']}_{fy}_{prefix}_{key}"
                )
    st.caption("Leave a box empty to keep the stored value. Commas are fine (1,00,000).")

    section("Live year-on-year preview")
    moderate = api.to_decimal(settings["moderate_pct"]) or 5
    significant = api.to_decimal(settings["significant_pct"]) or 20
    for col, (metric, label) in zip(st.columns(4), FIELDS):
        try:
            previous = api.parse_money_input(typed[f"previous_{metric}"])
            current = api.parse_money_input(typed[f"current_{metric}"])
        except ValueError:
            previous = current = None
        delta = (
            (current - previous) / abs(previous) * 100
            if previous not in (None, 0) and current is not None
            else None
        )
        if delta is None:
            band, text = "Info", "No comparison"
        else:
            magnitude = abs(delta)
            band = "Critical" if magnitude > significant else "Warning" if magnitude > moderate else "Info"
            text = f"{delta:+.2f}%"
        with col.container(border=True):
            st.markdown(f"**{label}**")
            st.markdown(text)
            st.markdown(severity_badge(band), unsafe_allow_html=True)

    if st.button("Save & re-check", type="primary", icon=":material/check:"):
        try:
            payload = {"client_id": client["id"], "previous_fy": prior_fy, "current_fy": current_fy}
            for key, value in typed.items():
                prefix, field = key.split("_", 1)
                fy = current_fy if prefix == "current" else prior_fy
                amount = api.parse_money_input(value)
                stored = api.to_decimal((saved.get(fy) or {}).get(field))
                # Unchanged pre-filled values are not re-sent, so imported figures stay "imported".
                payload[key] = None if amount == stored else amount
            st.session_state[_RESULT_KEY] = api.save_entry(payload)
        except (ValueError, api.ApiError) as exc:
            st.error(str(exc))
        else:
            st.rerun()  # so the sidebar's alert count and years reflect the save
