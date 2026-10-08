"""The step-by-step Tally JSON import (Master + one year's Transactions) and the import log."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import api_client as api
from components.common import CLIENTS, choose_fy, page_link, unit
from components.money import format_money

KIND_LABELS = {"master": "Master (ledgers and groups)", "transactions": "Transactions (vouchers)"}
STAGE_LABELS = {
    "uploading": "Uploading files",
    "reading": "Reading files",
    "parsing": "Reading vouchers",
    "done": "Checking against existing data",
}
STEPS = ("Choose client", "Select files", "Review", "Import")
INSTRUCTIONS = (
    "Upload the **Master** file + **ONE financial year's Transactions** file together "
    "(Ctrl+click both). The year is detected from voucher dates. Repeat for each year."
)
ADD, REPLACE, SKIP = "add", "replace", "skip"
_STATE_KEY = "tally_json_preview"
_ROUND_KEY = "tally_json_round"  # bumped to clear the file picker for the next year


def _stepper(current: int) -> None:
    """'✓ 1 · Choose client → 2 · Select files → …' with the current step in bold."""
    parts = []
    for index, label in enumerate(STEPS, start=1):
        if index < current:
            parts.append(f"✓ {index} · {label}")
        elif index == current:
            parts.append(f"**{index} · {label}**")
        else:
            parts.append(f":gray[{index} · {label}]")
    st.markdown(" → ".join(parts))


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


def _profits_table(profits: list[dict], after_import: bool) -> None:
    """Gross / net profit worked out from the files, with why a figure is missing."""
    if not profits:
        return
    st.markdown("**Gross and net profit from Tally's P&L ledgers**")
    rows = []
    for p in profits:
        def money(key):
            value = api.to_decimal(p[key])
            return format_money(value, unit()) if value is not None else "—"

        if after_import:
            status = "Saved" if p["stored"] else "Not saved"
        else:
            status = "Will be saved" if p["will_store"] else "Not saved"
        rows.append(
            {
                "Financial year": f"FY {p['fy']}",
                "Gross profit": money("gross_profit"),
                "Net profit": money("net_profit"),
                "Opening → closing stock": f"{money('opening_stock')} → {money('closing_stock')}",
                "Status": status,
                "Notes": " ".join(p["notes"]),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "GP = sales, purchases and direct items + closing stock - opening stock; NP = GP + "
        "indirect incomes and expenses (as in Tally's P&L). Figures entered manually or from a "
        "P&L import are never replaced."
    )


def _preview(client: dict, uploaded: list) -> dict | None:
    """Preview once per selection of files; reruns reuse it instead of re-uploading."""
    selection = (client["id"], tuple(f.file_id for f in uploaded))
    state = st.session_state.get(_STATE_KEY)
    if state and state["selection"] == selection:
        return state
    bar = st.progress(0.0, text="Uploading files…")

    def show_progress(stage: str, fraction: float) -> None:
        bar.progress(fraction, text=f"{STAGE_LABELS.get(stage, stage)}… {fraction:.0%}")

    try:
        preview = api.preview_tally_json(client["id"], [(f.name, f) for f in uploaded], show_progress)
    except api.ApiError as exc:
        st.error(str(exc))
        return None
    finally:
        bar.empty()
    state = {"selection": selection, "preview": preview, "result": None}
    st.session_state[_STATE_KEY] = state
    return state


def _year_choices(preview: dict) -> dict[str, str]:
    """For each FY in the files that already has imported data: add / replace / skip."""
    choices: dict[str, str] = {}
    existing = preview.get("existing_vouchers", {})
    for fy in preview.get("fys_already_imported", []):
        with st.container(border=True):
            st.warning(f"FY {fy} is already imported for this client ({existing.get(fy, 0)} vouchers stored).")
            choice = st.radio(
                f"What should happen to FY {fy}?",
                (ADD, REPLACE, SKIP),
                format_func={
                    ADD: "Add only vouchers not imported yet (keeps existing data)",
                    REPLACE: "Replace: delete this year's imported vouchers, then import these files",
                    SKIP: "Skip this year (import nothing for it)",
                }.get,
                key=f"fy_choice_{fy}",
            )
            if choice == REPLACE and not st.checkbox(
                f"I understand {existing.get(fy, 0)} imported vouchers for FY {fy} will be deleted first.",
                key=f"fy_replace_ok_{fy}",
            ):
                choice = "unconfirmed"
            choices[fy] = choice
    return choices


def _show_result(result: dict) -> None:
    st.success(
        f"Imported **{result['imported']}** entries "
        f"({result['duplicates_skipped']} already stored, skipped). "
        f"{result['alerts_raised']} new alert(s) raised."
    )
    for fy, count in result.get("replaced", {}).items():
        st.caption(f"FY {fy}: {count} previously imported vouchers were replaced.")
    if result.get("skipped_fys"):
        st.caption("Left unchanged: " + ", ".join(f"FY {fy}" for fy in result["skipped_fys"]))
    if result["fys_affected"]:
        st.caption("Financial years updated: " + ", ".join(f"FY {y}" for y in result["fys_affected"]))


def render_json_import(client: dict) -> None:
    """Choose client (sidebar) → select files → review the detected year → import."""
    round_no = st.session_state.get(_ROUND_KEY, 0)
    state = st.session_state.get(_STATE_KEY)
    step = 4 if state and state.get("result") else 3 if state else 2
    _stepper(step)
    st.caption(f"Client: **{client['name']}** (change it in the sidebar)")
    st.info(INSTRUCTIONS, icon=":material/info:")

    uploaded = st.file_uploader(
        "Master + Transactions JSON files",
        type=["json"],
        accept_multiple_files=True,
        key=f"json_files_{client['id']}_{round_no}",
    )
    if not uploaded:
        st.session_state.pop(_STATE_KEY, None)
        return
    if len(uploaded) < 2:
        st.warning("Select both files together: the Master file and one Transactions file.")
        return

    state = _preview(client, uploaded)
    if state is None:
        return
    preview, result = state["preview"], state["result"]

    st.subheader("Review")
    fys = [row["fy"] for row in preview["by_fy"]]
    if preview["period"]:
        detected = ", ".join(f"FY {fy}" for fy in fys) or "no sales or purchases"
        st.markdown(f"Vouchers dated **{preview['period']}** → **{detected}**")
    if len(fys) > 1:
        st.warning(
            f"These files hold {len(fys)} financial years. Import one year's Transactions file "
            "at a time so each year can be checked on its own."
        )
    if preview["by_fy"]:
        st.dataframe(_totals_table(preview["by_fy"]), hide_index=True, width="stretch")
    _profits_table(result["profits"] if result else preview.get("profits", []), result is not None)
    for warning in preview["warnings"]:
        st.warning(warning)
    with st.expander("Details: files and voucher counts"):
        st.dataframe(_files_table(preview["files"]), hide_index=True, width="stretch")
        a, b, c, d = st.columns(4)
        a.metric("Vouchers read", preview["vouchers_read"])
        b.metric("Sales entries", preview["sales_records"])
        c.metric("Purchase entries", preview["purchase_records"])
        d.metric("Other (receipts, payments…)", preview["other_vouchers"])
        gstin = ", ".join(preview["company_gstins"]) or "not shown"
        st.caption(f"Company GSTIN in the file: {gstin}")
        if preview["sample"]:
            st.markdown("**First few new entries**")
            st.dataframe(pd.DataFrame(preview["sample"]), hide_index=True, width="stretch")

    if result is not None:
        _show_result(result)
        page_link(CLIENTS, "Open the client report →")
        if st.button("Import another year", icon=":material/add:"):
            st.session_state.pop(_STATE_KEY, None)
            st.session_state[_ROUND_KEY] = round_no + 1
            st.rerun()
        return

    choices = _year_choices(preview)
    replace = [fy for fy, choice in choices.items() if choice == REPLACE]
    skip = [fy for fy, choice in choices.items() if choice == SKIP]
    importing = [fy for fy in fys if fy not in skip]
    new_count = preview["would_import"]
    st.metric("New entries to import", new_count, help="Entries already stored are skipped.")
    blocked = "unconfirmed" in choices.values()
    profit_updates = [p for p in preview.get("profits", []) if p["will_store"] and p["fy"] not in skip]
    nothing = not importing or (not new_count and not replace and not profit_updates)
    if nothing and not blocked:
        st.info("Nothing new to import from these files.")
    label = "Import " + (", ".join(f"FY {fy}" for fy in importing) if importing else "")
    if st.button(label.strip(), type="primary", disabled=blocked or nothing):
        try:
            with st.spinner("Importing and re-checking alerts…"):
                state["result"] = api.confirm_tally_json(
                    client["id"], preview["preview_token"], replace, skip
                )
        except api.ApiError as exc:
            st.error(str(exc))
            return
        if state["result"]["fys_affected"]:
            choose_fy(max(state["result"]["fys_affected"]))  # show the imported year
        st.rerun()  # so the sidebar's years and alert count include the new data


def show_import_log(client_id: int) -> None:
    st.markdown("**Import log**")
    try:
        log = api.import_log(client_id)
    except api.ApiError as exc:
        st.error(str(exc))
        return
    if not log:
        st.caption("No imports yet for this client.")
        return
    columns = ["imported_at", "file_name", "report_type", "period", "rows_imported", "rows_skipped"]
    frame = pd.DataFrame(log)[columns].rename(
        columns={
            "imported_at": "Imported at",
            "file_name": "Files",
            "report_type": "Register",
            "period": "Period",
            "rows_imported": "Imported",
            "rows_skipped": "Skipped",
        }
    )
    frame["Imported at"] = frame["Imported at"].astype(str).str.replace("T", " ").str[:16]
    st.dataframe(frame, width="stretch", hide_index=True)
