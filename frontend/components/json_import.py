"""The 'Tally JSON export' import flow (Master + Transactions files) and the import log."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import api_client as api
from components.common import page_link, show_error, unit
from components.money import format_money

KIND_LABELS = {"master": "Master (ledgers and groups)", "transactions": "Transactions (vouchers)"}


def _files_table(files: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "File": f["file_name"],
                "Recognised as": KIND_LABELS.get(f["kind"], f["kind"]),
                "Records read": f["records"],
                "Complete?": "⚠️ cut off" if f["truncated"] else "✅ yes",
            }
            for f in files
        ]
    )


def _totals_table(by_fy: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Financial year": f"FY {row['fy']}",
                "Sales (excl. GST)": format_money(api.to_decimal(row["sales"]), unit()),
                "Sales entries": row["sales_count"],
                "Purchases (excl. GST)": format_money(api.to_decimal(row["purchases"]), unit()),
                "Purchase entries": row["purchase_count"],
            }
            for row in by_fy
        ]
    )


def _show_summary(summary: dict) -> None:
    st.markdown("**Files**")
    st.dataframe(_files_table(summary["files"]), hide_index=True, width="stretch")

    a, b, c, d = st.columns(4)
    a.metric("Vouchers read", summary["vouchers_read"])
    b.metric("Sales entries", summary["sales_records"])
    c.metric("Purchase entries", summary["purchase_records"])
    d.metric("Other (receipts, payments…)", summary["other_vouchers"])
    if summary["period"]:
        gstin = ", ".join(summary["company_gstins"]) or "not shown"
        st.caption(f"Voucher dates: {summary['period']} · Company GSTIN in the file: {gstin}")
    if summary["by_fy"]:
        st.markdown("**Totals found**")
        st.dataframe(_totals_table(summary["by_fy"]), hide_index=True, width="stretch")
    for warning in summary["warnings"]:
        st.warning(warning)


def render_json_import(client: dict) -> None:
    """Upload -> preview -> confirm for Tally's JSON export."""
    uploaded = st.file_uploader(
        "Tally JSON files - select the Master file and the Transactions file together",
        type=["json"],
        accept_multiple_files=True,
        key=f"json_files_{client['id']}",
    )
    st.caption(
        "Both files must come from the same company. The Master file tells the tool which "
        "ledgers are sales, purchases and GST; the Transactions file holds the vouchers."
    )
    if not uploaded:
        return

    files = [(f.name, f.getvalue()) for f in uploaded]
    try:
        preview = api.preview_tally_json(client["id"], files)
    except api.ApiError as exc:
        show_error(exc)
        return

    _show_summary(preview)
    st.subheader("Ready to import")
    x, y = st.columns(2)
    x.metric("New entries to import", preview["would_import"])
    y.metric("Already imported (will be skipped)", preview["duplicates"])
    if preview["sample"]:
        with st.expander("First few entries"):
            st.dataframe(pd.DataFrame(preview["sample"]), hide_index=True, width="stretch")
    if not preview["would_import"]:
        st.info("Nothing new to import from these files.")

    if st.button("Confirm import", type="primary", disabled=not preview["would_import"]):
        try:
            result = api.confirm_tally_json(client["id"], files)
        except api.ApiError as exc:
            show_error(exc)
            return
        st.success(
            f"Imported **{result['imported']}** entries "
            f"({result['duplicates_skipped']} duplicates skipped). "
            f"{result['alerts_raised']} new alert(s) raised."
        )
        if result["fys_affected"]:
            st.caption(
                "Financial years updated: " + ", ".join(f"FY {y}" for y in result["fys_affected"])
            )
        page_link("pages/5_Client_Report.py", "Open the client report →")


def show_import_log(client_id: int) -> None:
    st.divider()
    st.subheader("Import log")
    log = api.import_log(client_id)
    if not log:
        st.caption("No imports yet for this client.")
        return
    columns = ["imported_at", "file_name", "report_type", "period", "rows_imported", "rows_skipped"]
    st.dataframe(pd.DataFrame(log)[columns], width="stretch", hide_index=True)
