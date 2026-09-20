import streamlit as st

import api_client as api
from components.common import page_setup, show_error

page_setup("Clients", "👥")

with st.form("add_client", clear_on_submit=True):
    name = st.text_input("New client name", placeholder="e.g. Sharma Traders")
    if st.form_submit_button("Add client", type="primary"):
        try:
            created = api.create_client(name)
            st.success(f"Added **{created['name']}**.")
        except api.ApiError as exc:
            show_error(exc)

clients = api.list_clients()
st.subheader(f"Clients ({len(clients)})")
if not clients:
    st.info("No clients yet.")

for client in clients:
    with st.expander(client["name"]):
        new_name = st.text_input("Name", client["name"], key=f"name_{client['id']}")
        save, delete = st.columns([1, 1])
        if save.button("Save name", key=f"save_{client['id']}"):
            try:
                api.rename_client(client["id"], new_name)
                st.rerun()
            except api.ApiError as exc:
                show_error(exc)
        confirm = delete.checkbox("Confirm delete", key=f"confirm_{client['id']}")
        if delete.button("Delete client", key=f"del_{client['id']}", disabled=not confirm):
            try:
                api.delete_client(client["id"])
                st.rerun()
            except api.ApiError as exc:
                show_error(exc)
        st.caption(
            "Deleting a client also deletes its vouchers, figures, alerts and import history."
        )
