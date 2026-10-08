"""Clients: the directory (add / open) and, for the sidebar's client, its report,
data coverage and management (rename / delete)."""

import streamlit as st

import api_client as api
from components.client_report import render_client_report
from components.common import DATA, choose_client, page_setup, select_fy, selected_client, show_error
from components.coverage import coverage_matrix, render_client_coverage
from components.profile import profile_gaps, render_profile
from components.tds import render_report as render_tds_report
from components.ui import empty_state, section


@st.dialog("Add client")
def add_client_dialog():
    name = st.text_input("Client name", placeholder="e.g. Sharma Traders", key="add_client_name")
    if st.button("Add client", type="primary", disabled=not name.strip()):
        try:
            created = api.create_client(name.strip())
        except api.ApiError as exc:
            show_error(exc)
        else:
            st.session_state.pop("add_client_name", None)
            choose_client(created["id"])
            st.rerun()


@st.dialog("Rename client")
def rename_dialog(client_id: int, current_name: str):
    name = st.text_input("Client name", value=current_name, key=f"rename_{client_id}")
    if st.button("Save name", type="primary", disabled=not name.strip()):
        try:
            api.rename_client(client_id, name.strip())
        except api.ApiError as exc:
            show_error(exc)
        else:
            st.rerun()


@st.dialog("Delete client")
def delete_dialog(client_id: int, name: str):
    st.error("Deleting this client permanently removes its vouchers, figures, alerts and import history.")
    confirmation = st.text_input(f"Type {name} to confirm deletion", key=f"delete_confirm_{client_id}")
    if st.button("Delete client", type="primary", disabled=confirmation != name):
        try:
            api.delete_client(client_id)
        except api.ApiError as exc:
            show_error(exc)
        else:
            choose_client(None)
            st.rerun()


def directory() -> None:
    page_setup("Clients", subtitle="Open a client to see its report, data coverage and settings.")
    if st.button("Add client", type="primary", icon=":material/person_add:"):
        add_client_dialog()
    try:
        with st.spinner("Loading clients…"):
            clients = api.workspace()["clients"]
            matrix = coverage_matrix(clients)
    except api.ApiError as exc:
        show_error(exc)
        return
    section(f"Client directory · {len(clients)}")
    if not clients:
        empty_state("No clients yet. Add a client to begin importing data or entering figures.")
        return
    st.caption("Select a row to open the client. Each year shows where its data came from.")
    event = st.dataframe(
        matrix,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="client_directory",
    )
    picked = list(getattr(event.selection, "rows", []))
    if picked:
        chosen = clients[picked[0]]
        st.button(
            f"Open {chosen['name']}",
            type="primary",
            icon=":material/arrow_forward:",
            on_click=choose_client,
            args=(chosen["id"],),
        )


def client_view(client: dict) -> None:
    st.button("All clients", icon=":material/arrow_back:", on_click=choose_client, args=(None,))
    page_setup(client["name"], subtitle="Client report, data coverage and client settings.")
    fy = select_fy()
    gaps = profile_gaps(client["id"])
    if gaps:
        st.info(
            f"Profile incomplete ({', '.join(gaps)}): limits that depend on these are still "
            "checked. Complete it on the Profile tab so only relevant alerts are raised.",
            icon=":material/assignment_ind:",
        )
    report_tab, tds_tab, coverage_tab, profile_tab, manage_tab = st.tabs(
        ["Report", "TDS", "Data coverage", "Profile", "Manage client"]
    )
    with report_tab:
        if fy:
            st.caption(f"FY {fy} compared with the previous year. Change the year in the sidebar.")
        render_client_report(client, fy)
    with tds_tab:
        st.caption(f"TDS Applicability, FY {fy}: one row per party per section. Select a row for the full working.")
        render_tds_report(client, fy)
    with coverage_tab:
        render_client_coverage(client["id"])
        st.page_link(DATA, label="Add missing years on the Data page", icon=":material/upload_file:")
    with profile_tab:
        render_profile(client)
    with manage_tab:
        left, right, _ = st.columns([1, 1, 4])
        if left.button("Rename", icon=":material/edit:"):
            rename_dialog(client["id"], client["name"])
        if right.button("Delete", icon=":material/delete:"):
            delete_dialog(client["id"], client["name"])


current = selected_client()
if current is None:
    directory()
else:
    client_view(current)
