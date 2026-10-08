import pandas as pd
import streamlit as st

import api_client as api
from components.common import page_setup, show_error, unit
from components.money import format_money
from components.tds import SECTIONS, STATUS_LABELS, render_detail, rs
from components.ui import empty_state, section

page_setup("Alerts", subtitle="Review threshold and TDS events, open a TDS alert for the full working, and acknowledge what you have handled.")
client_id = st.session_state.get("global_client_id")
fy = st.session_state.get("global_fy")
SEVERITY_COLOURS = {"Critical": "#ffc2c2", "High": "#ffcfa8", "Warning": "#ffdfaa", "Info": "#c4dcff"}
SEVERITY_EMOJI = {"critical": "🔴", "high": "🟠", "warning": "🟡", "info": "🟢"}


def _row(alert: dict) -> dict:
    is_tds = alert["kind"] == "tds"
    is_limit = alert["metric"].startswith("limit:")
    threshold = alert.get("threshold_description") or ""
    value = api.to_decimal(alert["value"])
    if is_tds:
        status = STATUS_LABELS.get(alert["new_status"], alert["new_status"])
        shown_value, limit = rs(alert["aggregate"]), rs(alert["threshold"])
        crossed_by = threshold.rsplit("; crossed by ", 1)[1].rstrip(")") if "; crossed by " in threshold else ""
    else:
        amount = threshold.split(" limit = ")[-1] if " limit = " in threshold else None
        status = {"crossed": "Crossed limit", "approaching": "Approaching limit"}.get(
            alert["new_status"], alert["new_status"].replace("_", " ").title()
        )
        shown_value = format_money(value, unit()) if is_limit else f"{value:.2f}%" if value is not None else "—"
        limit = format_money(api.to_decimal(amount), unit()) if is_limit and amount else threshold
        crossed_by = ""
    return {
        "Severity": f"{SEVERITY_EMOJI.get(alert['severity'], '')} {alert['severity'].title()}",
        "Client": alert["client_name"],
        "FY": alert["fy"],
        "Type": f"TDS {alert['section']}" if is_tds else "Turnover",
        "Alert": alert["party"] if is_tds else alert["metric_label"].removeprefix("Limit: "),
        "PAN": (alert["party_pan"] or "not available") if is_tds else "",
        "Status": status,
        "Value / aggregate": shown_value,
        "Limit / threshold": limit,
        "TDS computed": rs(alert["tds_computed"]) if is_tds else "",
        "TDS deducted": rs(alert["tds_deducted"]) if is_tds else "",
        "Shortfall": rs(alert["shortfall"]) if is_tds else "",
        "Money at stake": rs(alert.get("money_at_stake")) if is_tds else "",
        "Crossed by": crossed_by,
        "Crossed on": str(alert["crossed_on"] or "—"),
        "Voucher": alert["crossed_voucher_no"] or "—",
        "Triggered at": str(alert["triggered_at"]).replace("T", " ")[:16],
        "Acknowledged by": alert["acknowledged_by"] or "—",
    }


@st.fragment
def render_alerts() -> None:
    """Filters, table, acknowledgement and the TDS detail panel."""
    f1, f2, f3 = st.columns([2, 3, 2])
    kind_label = f1.segmented_control("Type", ["All", "Turnover", "TDS"], default="All", key="alerts_kind")
    levels = f2.pills("Severity", ["All", "Critical", "High", "Warning", "Info"], default="All", key="alerts_level")
    section_key = f3.selectbox("TDS section", [None, *SECTIONS], format_func=lambda v: "All sections" if v is None else v,
                               key="alerts_section", disabled=kind_label == "Turnover")  # fmt: skip
    t1, t2, t3 = st.columns(3)
    open_only = t1.toggle("Unacknowledged only", value=True)
    show_ok = t2.toggle("Show 🟢 'TDS correctly deducted' (informational)", value=False)
    ranked = t3.toggle("Rank by money at stake", value=True,
                       help="TDS shortfall + 30% s.40(a)(ia) disallowance + estimated interest at 1% a month, "
                       "largest first. Turnover alerts follow, newest first.")  # fmt: skip
    kind = {"All": None, "Turnover": "turnover", "TDS": "tds"}[kind_label or "All"]
    try:
        with st.spinner("Loading alerts…"):
            alerts = api.list_alerts(
                client_id=client_id, fy=fy, unacknowledged_only=open_only, kind=kind,
                section=section_key if kind != "turnover" else None,
                sort="at_stake" if ranked else "recent",
            )  # fmt: skip
    except api.ApiError as exc:
        show_error(exc)
        return

    if levels and levels != "All":
        alerts = [a for a in alerts if a["severity"] == levels.lower()]
    if not show_ok:
        alerts = [a for a in alerts if not (a["kind"] == "tds" and a["severity"] == "info")]
    section(f"{len(alerts)} alert(s)")
    if not alerts:
        empty_state("No alerts match the selected client, year and filters.")
        return

    frame = pd.DataFrame([_row(a) for a in alerts])
    styled = frame.style.map(
        lambda val: next((f"color: {c}; font-weight: 700" for k, c in SEVERITY_COLOURS.items() if str(val).endswith(k)), ""),
        subset=["Severity"],
    )  # fmt: skip
    event = st.dataframe(styled, width="stretch", hide_index=True, on_select="rerun",
                         selection_mode="multi-row", key="alerts_table")  # fmt: skip
    selected_rows = list(getattr(event.selection, "rows", []))
    selected = [alerts[i] for i in selected_rows if i < len(alerts)]
    st.caption(f"{len(selected)} row(s) selected. Select one TDS alert to open its full working below.")
    button_col, note_col = st.columns([1, 4], vertical_alignment="center")
    reviewer = st.session_state.get("reviewer_name", "").strip()
    if button_col.button("Acknowledge selected", type="primary", disabled=not selected or not reviewer):
        try:
            api.acknowledge_alerts([a["id"] for a in selected], reviewer)
        except api.ApiError as exc:
            show_error(exc)
        else:
            st.success(f"Acknowledged {len(selected)} alert(s) as {reviewer}.")
            st.rerun()
    note_col.caption(
        "Enter a reviewer name in the sidebar to enable this action."
        if not reviewer
        else "Reviewer name is saved in the sidebar and applied to every selected alert."
    )

    tds_selected = [a for a in selected if a["kind"] == "tds"]
    if len(tds_selected) == 1:
        st.divider()
        try:
            with st.spinner("Opening the TDS working…"):
                detail = api.tds_alert_detail(tds_selected[0]["id"])
        except api.ApiError as exc:
            show_error(exc)
            return
        render_detail(detail, detail.get("alert"))


render_alerts()
