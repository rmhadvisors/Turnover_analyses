"""Data: import Tally exports or enter yearly figures for the sidebar's client."""

import streamlit as st

from components.common import page_setup, select_client, select_fy
from components.coverage import render_client_coverage
from components.json_import import render_json_import, show_import_log
from components.manual_entry import render_manual_entry
from components.register_import import render_register_import

page_setup("Data", subtitle="Import Tally data or enter yearly figures for the selected client.")
client = select_client()
if client is not None:
    with st.expander("Data coverage: which years this client has", expanded=False):
        render_client_coverage(client["id"])

    tally_tab, manual_tab = st.tabs(["Tally import", "Manual entry"])
    with tally_tab:
        render_json_import(client)
        with st.expander("Other formats: Sales Register, Purchase Register, P&L (Excel / CSV)"):
            render_register_import(client)
        st.divider()
        show_import_log(client["id"])
    with manual_tab:
        render_manual_entry(client, select_fy())
