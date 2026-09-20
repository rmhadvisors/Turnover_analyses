import pandas as pd
import streamlit as st

import api_client as api
from components.common import page_link, page_setup, select_client, show_error, unit
from components.money import format_money

page_setup("Import Tally Data", "📥")

REPORT_TYPES = {
    "Sales Register": "sales_register",
    "Purchase Register": "purchase_register",
    "Profit & Loss / Trial Balance summary": "profit_loss",
}
FIELD_LABELS = {
    "date": "Date *",
    "voucher_no": "Voucher No.",
    "party": "Party / Particulars",
    "voucher_type": "Voucher Type",
    "taxable_value": "Value (excl. GST)",
    "total_value": "Gross Total (incl. GST)",
}
NONE = "(not in file)"

client = select_client()
if not client:
    st.stop()

report_label = st.radio("Report type", list(REPORT_TYPES), horizontal=True)
report_type = REPORT_TYPES[report_label]
uploaded = st.file_uploader("Tally export", type=["xlsx", "xls", "csv"])
st.caption(
    "Export from Tally as Excel or CSV. Header rows above the table and Grand Total rows are handled."
)

if uploaded is None:
    st.stop()

file = (uploaded.name, uploaded.getvalue())
try:
    preview = api.preview_import(client["id"], report_type, file)
except api.ApiError as exc:
    show_error(exc)
    st.stop()


def mapping_editor(preview: dict) -> dict:
    """Let the user confirm/adjust the column mapping; returns the chosen mapping."""
    source = {"saved": "saved for this client", "suggested": "auto-detected"}.get(
        preview["mapping_source"], preview["mapping_source"]
    )
    st.markdown(f"**Column mapping** ({source}; header found on row {preview['header_row']})")
    options = [NONE, *preview["headers"]]
    current = preview["mapping"]
    chosen: dict = {}
    columns = st.columns(3)
    for index, (field, label) in enumerate(FIELD_LABELS.items()):
        wanted = current.get(field)
        default = wanted if isinstance(wanted, str) and wanted in options else NONE
        pick = columns[index % 3].selectbox(
            label, options, index=options.index(default), key=f"map_{field}_{uploaded.name}"
        )
        if pick != NONE:
            chosen[field] = pick
    tax_default = [h for h in (current.get("tax_value") or []) if h in preview["headers"]]
    tax = st.multiselect(
        "GST / tax columns (summed)",
        preview["headers"],
        default=tax_default,
        key=f"map_tax_{uploaded.name}",
    )
    if tax:
        chosen["tax_value"] = tax
    return chosen


mapping = None
fy_override = None
if report_type == "profit_loss":
    figures = preview["profit_loss"]
    st.subheader("Figures found")
    left, right = st.columns(2)
    left.metric("Gross Profit", format_money(api.to_decimal(figures["gross_profit"]), unit()))
    right.metric("Net Profit", format_money(api.to_decimal(figures["net_profit"]), unit()))
    if figures["fy"]:
        st.success(f"Financial year detected from the file's period: FY {figures['fy']}")
    else:
        fy_override = st.text_input("Financial year (could not be detected)", placeholder="2025-26")
else:
    mapping = mapping_editor(preview)
    if mapping != preview["mapping"]:
        try:
            preview = api.preview_import(client["id"], report_type, file, mapping)
        except api.ApiError as exc:
            show_error(exc)
            st.stop()

    if preview["error"]:
        st.error(preview["error"])
        st.stop()

    st.subheader("Preview")
    a, b, c, d = st.columns(4)
    a.metric("Vouchers found", preview["rows_found"])
    b.metric("Will import", preview["would_import"])
    c.metric("Duplicates (skipped)", preview["duplicates"])
    d.metric("Total rows ignored", preview["totals_ignored"])
    if preview["period"]:
        st.caption(f"Period in file: {preview['period']}")
    if preview["duplicates"] and not preview["would_import"]:
        st.warning("Every voucher in this file has already been imported - nothing new to add.")
    if preview["invalid_count"]:
        with st.expander(f"⚠️ {preview['invalid_count']} row(s) could not be read"):
            st.write("\n".join(f"- {m}" for m in preview["invalid"]))
    if preview["parsed_sample"]:
        sample = pd.DataFrame(preview["parsed_sample"]).rename(
            columns={
                "voucher_no": "Voucher",
                "taxable_value": "Taxable",
                "tax_value": "Tax",
                "total_value": "Total",
            }
        )
        st.dataframe(sample, width="stretch", hide_index=True)
    with st.expander("First rows of the file, as read"):
        st.dataframe(pd.DataFrame(preview["raw_rows"]), hide_index=True)

can_import = report_type == "profit_loss" or bool(
    preview.get("would_import") or preview.get("duplicates")
)
save_mapping = (
    st.checkbox("Remember this column mapping for this client", value=True)
    if mapping is not None
    else True
)
if st.button("Confirm import", type="primary", disabled=not can_import):
    try:
        result = api.confirm_import(
            client["id"], report_type, file, mapping, fy_override, save_mapping
        )
    except api.ApiError as exc:
        show_error(exc)
    else:
        st.success(
            f"Imported **{result['imported']}** row(s) from {result['file_name']}. "
            f"Skipped {result['duplicates_skipped']} duplicate(s)"
            + (
                f" and {result['invalid_skipped']} unreadable row(s)"
                if result["invalid_skipped"]
                else ""
            )
            + f". {result['alerts_raised']} new alert(s) raised."
        )
        if result["duplicate_file"]:
            st.warning("This exact file had been imported before.")
        if result["fys_affected"]:
            st.caption(
                "Financial years updated: " + ", ".join(f"FY {y}" for y in result["fys_affected"])
            )
        page_link("pages/5_Client_Report.py", "Open the client report →")

st.divider()
st.subheader("Import log")
log = api.import_log(client["id"])
if log:
    frame = pd.DataFrame(log)[
        ["imported_at", "file_name", "report_type", "period", "rows_imported", "rows_skipped"]
    ]
    st.dataframe(frame, width="stretch", hide_index=True)
else:
    st.caption("No imports yet for this client.")
