"""Streamlit entry point: navigation and the sidebar (the one place to pick client + FY)."""

import streamlit as st

import api_client as api
from components.common import (
    ALERTS,
    CLIENT_KEY,
    CLIENTS,
    DASHBOARD,
    DATA,
    FY_KEY,
    SETTINGS,
    apply_pending_selection,
    current_fy,
    fy_choices,
)
from components.ui import DISPLAY_MODES, inject_styles

st.set_page_config(page_title="Turnover Analysis", page_icon=":material/monitoring:", layout="wide")
inject_styles()

alerts_page = st.Page(ALERTS, title="Alerts", icon=":material/notifications:")
navigation = st.navigation(
    [
        st.Page(DASHBOARD, title="Dashboard", icon=":material/dashboard:", default=True),
        st.Page(CLIENTS, title="Clients", icon=":material/groups:"),
        st.Page(DATA, title="Data", icon=":material/upload_file:"),
        alerts_page,
        st.Page(SETTINGS, title="Settings", icon=":material/tune:"),
    ]
)

apply_pending_selection()
with st.sidebar:
    st.markdown("### Workspace")
    try:
        workspace = api.workspace()  # one cached call: clients, FYs, alert count
        clients, data_fys = workspace["clients"], workspace["fys"]
        alert_count = workspace["unacknowledged_alerts"]
    except api.ApiError as exc:
        clients, data_fys, alert_count = [], [], 0
        st.error(str(exc))

    client_map = {c["id"]: c["name"] for c in clients}
    client_options = [None, *client_map]
    if st.session_state.get(CLIENT_KEY) not in client_options:
        st.session_state[CLIENT_KEY] = None
    st.selectbox(
        "Client",
        client_options,
        format_func=lambda value: "All clients" if value is None else client_map[value],
        key=CLIENT_KEY,
    )

    fy_options = fy_choices(data_fys)
    if st.session_state.get(FY_KEY) not in fy_options:
        st.session_state[FY_KEY] = data_fys[0] if data_fys else current_fy()
    with_data = set(data_fys)
    st.selectbox(
        "Financial year",
        fy_options,
        format_func=lambda fy: f"FY {fy}" if fy in with_data else f"FY {fy} · no data yet",
        key=FY_KEY,
        help="Used by every page: reports, alerts, imports and manual entry.",
    )

    st.markdown("#### Display")
    st.selectbox("Amount display", list(DISPLAY_MODES), key="display_mode")
    st.text_input("Reviewer name", key="reviewer_name", placeholder="Used when acknowledging alerts")
    st.page_link(
        alerts_page,
        label=f"{alert_count} unacknowledged (all clients)",
        icon=":material/notifications_active:",
        help="Critical and warning alerts not yet acknowledged, across every client and year.",
    )
    if st.button("Refresh data", icon=":material/refresh:", help="Reload everything from the backend."):
        api.clear_all_caches()
        st.rerun()

navigation.run()
