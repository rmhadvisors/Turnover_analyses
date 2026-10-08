from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st

import api_client as api
from components.common import page_setup, select_fy, selected_client, show_error
from components.money import format_money
from components.tds import render_mapping, render_payer, render_rate_master
from components.ui import empty_state, section

page_setup("Settings", subtitle="Change bands, statutory turnover limits and the TDS rate master and ledger mapping.")
try:
    with st.spinner("Loading threshold settings…"):
        settings = api.get_settings()
        limits = api.list_limits()
except api.ApiError as exc:
    show_error(exc)
    st.stop()

METRICS = {
    "sales_turnover": "Sales turnover",
    "purchase_turnover": "Purchase turnover",
    "aggregate_turnover": "Aggregate turnover",
    "purchase_per_seller": "Purchases from one seller",
}
bands_tab, limits_tab, tds_tab = st.tabs(["Change bands", "Statutory limits", "TDS"])
with bands_tab:
    section("Year-on-year change bands")
    st.caption("Applied to turnover, purchases, gross profit and net profit. Changes must move beyond a band edge to enter the next band.")
    with st.form("bands"):
        a, b = st.columns(2)
        moderate = a.number_input("Moderate limit (%)", 0.1, 1000.0, float(settings["moderate_pct"]), 0.5)
        significant = b.number_input("Significant limit (%)", 0.1, 1000.0, float(settings["significant_pct"]), 0.5)
        include_gst = st.checkbox("Include GST in turnover and purchases", value=settings["include_gst_in_turnover"])
        st.caption(f"Critical: beyond ±{significant:g}% · Warning: {moderate:g}% to {significant:g}% · Info: within ±{moderate:g}%")
        if st.form_submit_button("Save bands and re-check all clients", type="primary"):
            if moderate >= significant:
                st.error("The moderate limit must be below the significant limit.")
            else:
                try:
                    api.save_settings(f"{moderate:g}", f"{significant:g}", include_gst)
                    st.success("Saved. All clients were re-checked.")
                    if include_gst != settings["include_gst_in_turnover"]:
                        st.info("The GST setting applies to vouchers imported from now on.")
                except api.ApiError as exc:
                    show_error(exc)

@st.dialog("Delete statutory limits")
def delete_limits_dialog(selected: list[dict]) -> None:
    st.warning("These limits will be deleted permanently:")
    st.markdown("\n".join(f"- {limit['name']}" for limit in selected))
    left, right = st.columns(2)
    if left.button("Delete", type="primary", icon=":material/delete:"):
        try:
            for limit in selected:
                api.delete_limit(limit["id"])
        except api.ApiError as exc:
            show_error(exc)
        else:
            st.session_state.pop("limit_editor", None)
            st.rerun()
    if right.button("Cancel"):
        st.rerun()


@st.fragment
def limits_table(limits: list[dict]) -> None:
    """One editable table: edit cells and save, or tick rows and delete them."""
    if not limits:
        empty_state("No statutory limits are configured.")
        return
    verified = st.session_state.setdefault("verified_limits", {})
    data = [
        {
            "Select": False,
            "ID": limit["id"],
            "Name": limit["name"],
            "Metric": limit["metric"],
            # Full rupees, whatever the display unit: this cell is parsed back on save.
            "Amount": format_money(api.to_decimal(limit["amount"]), "full"),
            "Approaching %": float(limit["approaching_pct"]),
            "FY scope": limit["fy_scope"] or "",
            "Enabled": limit["is_enabled"],
            "Verified": bool(verified.get(str(limit["id"]), False)),
            "Applies to": limit.get("applies_to") or "Every client",
            "Why it matters": limit["description"] or "",
        }
        for limit in limits
    ]
    edited = st.data_editor(
        pd.DataFrame(data),
        width="stretch",
        hide_index=True,
        disabled=["ID", "Applies to"],
        column_config={
            "Select": st.column_config.CheckboxColumn(help="Tick rows to delete them"),
            "ID": None,
            "Name": st.column_config.TextColumn(required=True),
            "Metric": st.column_config.SelectboxColumn(options=list(METRICS), format_func=METRICS.get),
            "Amount": st.column_config.TextColumn(help="Enter rupees; Indian comma grouping is accepted."),
            "Approaching %": st.column_config.NumberColumn(min_value=0.1, max_value=100.0, step=1.0),
            "Enabled": st.column_config.CheckboxColumn(),
            "Verified": st.column_config.CheckboxColumn(help="UI-only reviewer marker; no database field is available."),
            "Applies to": st.column_config.TextColumn(
                help="Which clients this limit is checked for, from each client's profile (Clients -> Profile). "
                "A profile field left Unknown never switches a limit off."
            ),
        },
        key="limit_editor",
    )
    for _, row in edited.iterrows():
        verified[str(int(row["ID"]))] = bool(row["Verified"])
    by_id = {limit["id"]: limit for limit in limits}
    selected = [by_id[int(row["ID"])] for _, row in edited.iterrows() if row["Select"]]

    save_col, delete_col, _ = st.columns([1, 1, 3])
    if save_col.button("Save changes", type="primary", icon=":material/save:"):
        try:
            for _, row in edited.iterrows():
                limit_id = int(row["ID"])
                amount = Decimal(str(row["Amount"]).replace("₹", "").replace(",", "").strip())
                body = {
                    "name": str(row["Name"]).strip(),
                    "metric": str(row["Metric"]),
                    "amount": str(amount),
                    "approaching_pct": str(row["Approaching %"]),
                    "fy_scope": str(row["FY scope"]).strip() or None,
                    "description": str(row["Why it matters"]).strip() or None,
                    "is_enabled": bool(row["Enabled"]),
                }
                old = by_id[limit_id]
                changed = (
                    body["name"] != old["name"]
                    or body["metric"] != old["metric"]
                    or amount != api.to_decimal(old["amount"])
                    or Decimal(body["approaching_pct"]) != api.to_decimal(old["approaching_pct"])
                    or body["fy_scope"] != old["fy_scope"]
                    or body["description"] != old["description"]
                    or body["is_enabled"] != old["is_enabled"]
                )
                if changed:
                    api.update_limit(limit_id, body)
        except (ValueError, InvalidOperation, api.ApiError) as exc:
            show_error(exc)
        else:
            st.session_state.pop("limit_editor", None)
            st.rerun()
    if delete_col.button(
        f"Delete selected ({len(selected)})", icon=":material/delete:", disabled=not selected
    ):
        delete_limits_dialog(selected)


with limits_tab:
    st.info("Pre-filled compliance limits are editable starting points. Verify each limit against current law before relying on it.")
    limits_table(limits)

    with st.expander("Add statutory limit"):
        with st.form("new_limit", clear_on_submit=True):
            name = st.text_input("Name")
            metric = st.selectbox("Metric", list(METRICS), format_func=METRICS.get)
            amount = st.text_input("Amount (₹)")
            approaching = st.number_input("Approaching at (% of limit)", 1.0, 100.0, 80.0)
            fy_scope = st.text_input("Only for FY (blank = every year)", placeholder="2025-26")
            description = st.text_area("Why it matters")
            enabled = st.checkbox("Enabled", value=True)
            if st.form_submit_button("Add limit", type="primary"):
                try:
                    parsed_amount = Decimal(amount.replace(",", "").replace("₹", "").strip())
                    api.create_limit({"name": name.strip(), "metric": metric, "amount": str(parsed_amount), "approaching_pct": str(approaching), "fy_scope": fy_scope.strip() or None, "description": description or None, "is_enabled": enabled})
                except (ValueError, InvalidOperation, api.ApiError) as exc:
                    show_error(exc)
                else:
                    st.rerun()


with tds_tab:
    master_tab, client_tab = st.tabs(["Rate & threshold master", "Client: who deducts & ledger mapping"])
    with master_tab:
        render_rate_master()
    with client_tab:
        tds_client = selected_client()
        if tds_client is None:
            try:
                all_clients = api.workspace()["clients"]
            except api.ApiError as exc:
                show_error(exc)
                all_clients = []
            names = {c["id"]: c for c in all_clients}
            picked = st.selectbox("Client", list(names), format_func=lambda cid: names[cid]["name"],
                                  index=None, placeholder="Choose a client (or pick one in the sidebar)")  # fmt: skip
            tds_client = names.get(picked)
        if tds_client is None:
            empty_state("Choose a client to review who must deduct and its ledger mapping.")
        else:
            tds_fy = select_fy()
            section("Who must deduct")
            if tds_fy:
                render_payer(tds_client, tds_fy)
            section("Ledger → TDS section mapping")
            render_mapping(tds_client)
    section("Data quality: PAN vs party name (all clients)")
    try:
        problems = api.tds_data_quality()
    except api.ApiError as exc:
        show_error(exc)
        problems = []
    if not problems:
        st.caption("No party has a PAN that contradicts its name.")
    else:
        st.caption(problems[0]["consequence"])
        st.dataframe(
            pd.DataFrame([{"Level": "⛔ Contradiction" if p["level"] == "contradiction" else "⚠ Check",
                           "Client": p["client_name"], "Party": p["party"], "PAN": p["pan"],
                           "PAN from": p["pan_source"], "Problem": p["problem"]} for p in problems]),
            hide_index=True, width="stretch",
        )  # fmt: skip
