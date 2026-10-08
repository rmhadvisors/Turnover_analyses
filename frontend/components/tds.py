"""TDS screens: the party/section detail panel, the TDS Applicability report, the Client
Report's TDS block, and the Settings -> TDS editors (rate master, payer, ledger mapping)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st

import api_client as api
from components.common import show_error
from components.money import format_money
from components.ui import empty_state, kpi_card, section

SECTIONS = ("194C", "194H", "194J(a)", "194J(b)", "194J(ba)", "194Q", "194I(a)", "194I(b)", "194-IB")
MAPPABLE_SECTIONS = SECTIONS[:-1]  # 194-IB is applied automatically, never mapped
STATUS_STYLE = {
    "tds_not_deducted": ("🔴", "#ffc2c2"),
    "tds_short_deducted": ("🟠", "#ffcfa8"),
    "tds_approaching": ("🟡", "#ffdfaa"),
    "tds_ok": ("🟢", "#b7e4c7"),
    "tds_excess": ("🔵", "#b9d4ff"),
    "tds_nil": ("🟢", "#b7e4c7"),
    "tds_below": ("⚪", "#a7b3c2"),
    "tds_not_applicable": ("⚪", "#a7b3c2"),
    "tds_unidentified": ("🟠", "#ffcfa8"),
}
STATUS_LABELS = {
    "tds_not_deducted": "Threshold crossed & TDS NOT deducted",
    "tds_short_deducted": "Threshold crossed & TDS short deducted",
    "tds_approaching": "Approaching threshold",
    "tds_ok": "Threshold crossed & TDS correctly deducted",
    "tds_excess": "Threshold crossed & TDS EXCESS deducted",
    "tds_nil": "Threshold crossed - nil TDS",
    "tds_below": "Below threshold",
    "tds_unidentified": "Cannot determine - payee unidentified",
    "tds_not_applicable": "Section does not apply to this client",
}
ROLE_LABELS = {
    "base": "Payments (TDS base)",
    "tds": "TDS deducted",
    "excluded": "Excluded - not TDS",
    "unmapped": "Unmapped",
}
PAYEE_TYPES = {"individual_huf": "Individual/HUF", "other": "Other (firm, company, ...)"}
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def rs(value) -> str:
    """Full rupees with Indian grouping (TDS working is always shown in full)."""
    amount = api.to_decimal(value)
    return "—" if amount is None else format_money(amount, "full")


def _reviewer() -> str:
    return st.session_state.get("reviewer_name", "").strip()


def _safe(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text)[:60]


# ----------------------------------------------------------------- detail


def render_detail(detail: dict, alert: dict | None = None) -> None:
    """The click-through panel for one party and section."""
    party, sec, threshold, calc = detail["party"], detail["section"], detail["threshold"], detail["calculation"]
    key = f"tds_{_safe(party['key'])}_{_safe(sec['key'])}"
    emoji, _ = STATUS_STYLE.get(detail["status"], ("", ""))
    st.subheader(f"{emoji} {party['name']} · {sec['key']}")
    st.caption(f"{detail['client_name']} · FY {detail['fy']} · {detail['status_label']}")
    if not detail["mapping_approved"]:
        st.warning("The ledger mapping of this client is not approved yet (Settings → TDS): figures are provisional.")

    # a) party   b) section
    left, right = st.columns(2)
    with left:
        st.markdown("**a) Party**")
        flags = []
        if party["transporter_declaration"]:
            flags.append("Transporter declaration (NIL u/s 194C)")
        if party["tcs_206c1h"]:
            flags.append("Seller charges TCS u/s 206C(1H)")
        st.markdown(
            f"- Name: **{party['name']}**\n"
            f"- PAN: **{party['pan'] or 'not available'}** ({party['pan_source']})\n"
            f"- Payee type: **{party['payee_type_label']}** ({party['payee_source']})\n"
            f"- GSTIN: {party['gstin'] or '—'}\n"
            f"- Special flags: {', '.join(flags) or 'none'}"
        )
        hints = []
        if party["tally_transporter"]:
            hints.append("Tally marks this party as a transporter.")
        if party["tcs_vouchers"]:
            hints.append(f"TCS was charged on {party['tcs_vouchers']} voucher(s) ({rs(party['tcs_amount'])}).")
        for hint in hints:
            st.caption(f"Hint: {hint}")
        if party.get("pan_warning"):
            st.error(f"PAN check: {party['pan_warning']} {party['pan_warning_consequence']}", icon=":material/badge:")
    with right:
        st.markdown("**b) Section**")
        why = sec["why"]
        verdict = {True: "Applies", False: "Does not apply", None: "Cannot determine"}[why["applies"]]
        turnover = f"{rs(why['previous_turnover'])} (FY {why['previous_fy']})" if why["previous_turnover"] else f"not imported (FY {why['previous_fy']})"
        st.markdown(
            f"- **{sec['key']}** – {sec['nature']}\n"
            f"- Who must deduct: {sec['who_must_deduct']}\n"
            f"- Payee: {sec['payee']}\n"
            f"- Client: {why['constitution'] or 'unknown'} ({why['constitution_source']})\n"
            f"- Previous-year turnover used: {turnover}\n"
            f"- **{verdict}:** {why['reason']}"
        )
        if why["needs_confirmation"]:
            st.warning("Audit liability last year is not confirmed – set it on Settings → TDS.", icon=":material/help:")
        if sec.get("mapped_section"):
            st.info(f"Mapped to {sec['mapped_section']}, tested u/s 194-IB because the client is an Individual/HUF "
                    "not liable to audit.")  # fmt: skip
        if sec.get("provisional"):
            st.warning("Provisional: the rent ledger still needs your choice of 194I(a) or 194I(b) (Settings → TDS).",
                       icon=":material/rule:")  # fmt: skip

    unidentified = detail.get("unidentified")
    if unidentified:
        st.error(unidentified["message"], icon=":material/person_search:")
        alternatives = detail.get("alternatives") or []
        rows = [{"If it is": f"u/s {a['section']}", "Test": a["test"], "Crossed?": "Yes" if a["crossed"] else "No",
                 "Rate": f"{Decimal(a['rate']).normalize():f}%", "TDS": rs(a["tds"])} for a in alternatives]  # fmt: skip
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(f"Disallowance u/s 40(a)(ia) if no TDS: {unidentified['disallowance_text']}")
        _assign_form(detail, unidentified)

    # c) threshold test
    st.markdown("**c) Threshold test**")
    cols = st.columns(5)
    cols[0].metric("Threshold", threshold["text"].replace("Rs ", "₹"))
    cols[1].metric("FY aggregate", rs(threshold["aggregate"]))
    cols[2].metric("Largest single payment", rs(threshold["largest_single"]))
    cols[3].metric("Crossed by", (threshold["crossing_test"] or "not crossed").capitalize())
    crossed = f"{threshold['crossed_on']} · #{threshold['crossed_voucher_no']}" if threshold["crossed_on"] else "—"
    cols[4].metric("Crossed on (voucher)", crossed)
    if threshold["excess_over_threshold"] is not None:
        st.caption(f"194Q: excess over ₹50 lakh = **{rs(threshold['excess_over_threshold'])}** (TDS only on this).")
    if threshold["approaching"]:
        st.caption(f"Approaching: the aggregate is at or above {threshold['approaching_pct']}% of the threshold.")
    if threshold.get("months"):
        months = " · ".join(f"{k}: {rs(v)}" for k, v in threshold["months"].items())
        st.caption(f"194-IB monthly rent: {months}. TDS falls due once, in {threshold['deduct_on']}.")

    # d) calculation
    st.markdown("**d) Calculation**")
    steps = []
    for step in calc["steps"]:
        value = step["value"]
        shown = rs(value) if api.to_decimal(value) is not None else value
        steps.append({"Step": step["step"], "Value": shown, "Why": step["note"]})
    st.dataframe(pd.DataFrame(steps), hide_index=True, width="stretch")
    if calc.get("likely_cause"):
        st.warning(f"Likely cause: {calc['likely_cause']}", icon=":material/troubleshoot:")
    estimate = detail.get("estimate")
    if estimate:
        st.markdown(f"**Estimated amounts as of {estimate['as_of']}** · money at stake **{rs(estimate['money_at_stake'])}**")
        st.warning(estimate["label"], icon=":material/calculate:")
        st.dataframe(
            pd.DataFrame([{"Item": line["item"], "Amount": rs(line["amount"]) if line["amount"] else "",
                           "Working": line["working"]} for line in estimate["lines"]]),
            hide_index=True, width="stretch", column_config={"Working": st.column_config.TextColumn(width="large")},
        )  # fmt: skip
        if estimate.get("interest_working"):
            with st.expander("Interest working - voucher by voucher (grouped by the date the TDS became deductible)"):
                st.dataframe(pd.DataFrame([
                    {"Deductible on": w["deductible_on"], "Vouchers": w["vouchers"], "Unpaid TDS": rs(w["tds"]),
                     "Deposit due": w["deposit_due"], "Months to " + estimate["as_of"]: w["months"],
                     "Interest 1%": rs(w["interest_1"]), "Interest 1.5% (alt.)": rs(w["interest_15"])}
                    for w in estimate["interest_working"]
                ]), hide_index=True, width="stretch")  # fmt: skip
        st.caption(f"Money at stake = {estimate['money_at_stake_working']}. The 1.5% interest is an alternative. "
                   f"{estimate['fee_note']}")  # fmt: skip
    if detail.get("year_end_note"):
        st.info(detail["year_end_note"], icon=":material/event_note:")
    other = detail.get("other_section_vouchers") or []
    if other:
        with st.expander(f"This party's vouchers under other sections ({len(other)}) - move one here if it was booked to the wrong ledger"):
            st.dataframe(pd.DataFrame([{k: o[k] for k in ("section", "date", "voucher_type", "voucher_no", "ledger", "amount")}
                                       for o in other]), hide_index=True, width="stretch")  # fmt: skip
            labels = {o["voucher_key"]: f"{o['date']} · {o['voucher_type']} {o['voucher_no']} · {o['ledger']} · {rs(o['amount'])} ({o['section']})"
                      for o in other}  # fmt: skip
            chosen = st.selectbox("Voucher", list(labels), format_func=labels.get, key=f"move_{_safe(party['key'])}_{_safe(sec['key'])}")
            if st.button(f"Count this voucher under {sec['key']}", key=f"move_btn_{_safe(party['key'])}_{_safe(sec['key'])}"):
                _do(lambda: api.save_tds_voucher_flag(detail["client_id"], chosen, True,
                                                       "Moved by the CA", f"section:{sec['key']}"), "Moved; re-checked.")  # fmt: skip
    pool = detail.get("section_pool")
    if pool:
        who = f"attributed to {pool['attributed_to']} (the only party over the threshold)" if pool["attributed_to"] else "not tied to any party"
        st.caption(
            f"Section {sec['key']}: {rs(pool['amount'])} of TDS was booked in {pool['entries']} entry(ies) with no "
            f"party - {who}. Attributed {rs(pool['attributed'])} · shared pro rata, not counted {rs(pool['allocated'])} · "
            f"unallocated {rs(pool['unallocated'])}. Paid to the government per Tally: {rs(pool['deposited'])}."
        )

    # e) vouchers
    st.markdown("**e) Vouchers making up the aggregate**")
    lines = detail["vouchers"]
    frame = pd.DataFrame(
        [
            {
                "Date": date.fromisoformat(v["date"]),
                "Voucher type": v["voucher_type"],
                "Voucher no": v["voucher_no"],
                "Ledger": v["ledger"],
                "Amount excl. GST": float(Decimal(v["amount"])),
                "Running total": float(Decimal(v["running_total"])),
                "Liable": float(Decimal(v["liable"])),
                "Rate %": float(Decimal(v["rate"])),
                "Threshold crossed": "◀ crossed here" if v["is_crossing"] else "",
            }
            for v in lines
        ]
    )
    if not frame.empty:
        styled = frame.style.apply(
            lambda row: ["background-color: #5b2b2b" if row["Threshold crossed"] else "" for _ in row], axis=1
        ).format({"Amount excl. GST": "{:,.2f}", "Running total": "{:,.2f}", "Liable": "{:,.2f}", "Rate %": "{:g}"})
        st.dataframe(styled, hide_index=True, width="stretch", height=min(420, 38 + 35 * len(frame)))
        st.download_button("Vouchers (CSV)", frame.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"TDS_vouchers_{_safe(party['name'])}_{sec['key']}.csv", mime="text/csv",
                           key=f"{key}_csv")  # fmt: skip
    if detail["excluded_vouchers"]:
        st.caption(f"{len(detail['excluded_vouchers'])} voucher(s) left out of 194Q (seller charged TCS u/s 206C(1H)).")
    if detail["deductions"]:
        with st.expander(f"TDS deducted in this party's vouchers ({len(detail['deductions'])})"):
            st.dataframe(pd.DataFrame(detail["deductions"]), hide_index=True, width="stretch")

    # f) compliance
    st.markdown("**f) Compliance and consequences**")
    st.warning(detail["compliance"]["disclaimer"], icon=":material/gavel:")
    rows = []
    for item in detail["compliance"]["items"]:
        figure = item["figure"]
        if figure and api.to_decimal(figure) is not None:
            figure = rs(figure)
        rows.append({"Item": item["item"], "Detail": item["detail"], "Figure": figure or ""})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Detail": st.column_config.TextColumn(width="large")})  # fmt: skip

    # g) actions
    st.markdown("**g) Actions**")
    _actions(detail, alert, key)


def _actions(detail: dict, alert: dict | None, key: str) -> None:
    party, sec, calc = detail["party"], detail["section"], detail["calculation"]
    client_id, fy = detail["client_id"], detail["fy"]
    reviewer = _reviewer()
    stem = f"TDS_{sec['key']}_{_safe(party['name'])}_FY{fy}"
    ack_col, outside_col, export_col = st.columns(3)
    with ack_col:
        if alert is None:
            st.caption("Open this from an alert to acknowledge it.")
        elif alert["acknowledged"]:
            st.success(f"Acknowledged by {alert['acknowledged_by']}" + (f": {alert['note']}" if alert.get("note") else ""))
        else:
            note = st.text_input("Note", key=f"{key}_ack_note", placeholder="What was done / agreed")
            if st.button("Mark as acknowledged", key=f"{key}_ack", type="primary", disabled=not reviewer,
                         help=None if reviewer else "Enter a reviewer name in the sidebar"):  # fmt: skip
                _do(lambda: api.acknowledge_tds_alert(alert["id"], reviewer, note or None), "Acknowledged.")
    with outside_col:
        if calc["deducted_outside"] is not None:
            st.info(f"Marked as deducted outside Tally: {rs(calc['deducted_outside'])}" + (f" – {calc['outside_note']}" if calc.get("outside_note") else ""))
            if st.button("Remove the mark", key=f"{key}_unmark"):
                _do(lambda: api.save_tds_resolution(client_id, {"fy": fy, "party_key": party["key"], "section_key": sec["key"], "remove": True}), "Mark removed.")
        else:
            amount = st.text_input("Amount deducted outside Tally (blank = the full shortfall)", key=f"{key}_out_amt")
            note = st.text_input("Note (challan / voucher reference)", key=f"{key}_out_note")
            if st.button("Mark as 'TDS deducted outside Tally'", key=f"{key}_out"):
                try:
                    parsed = api.parse_money_input(amount)
                except ValueError as exc:
                    show_error(exc)
                else:
                    body = {"fy": fy, "party_key": party["key"], "section_key": sec["key"],
                            "amount": parsed, "note": note or None, "marked_by": reviewer or None}  # fmt: skip
                    _do(lambda: api.save_tds_resolution(client_id, body), "Marked.")
    with export_col:
        st.download_button("Export working (Excel)", key=f"{key}_xlsx", mime=XLSX_MIME, file_name=f"{stem}.xlsx",
                           data=lambda: api.export_tds_detail(client_id, fy, party["key"], sec["key"], "xlsx"))  # fmt: skip
        st.download_button("Export working (PDF)", key=f"{key}_pdf", mime="application/pdf", file_name=f"{stem}.pdf",
                           data=lambda: api.export_tds_detail(client_id, fy, party["key"], sec["key"], "pdf"))  # fmt: skip

    if not party["identified"]:
        return
    with st.expander("Party facts and special cases (PAN, payee type, declarations)"):
        with st.form(f"{key}_party"):
            pan = st.text_input("PAN (overrides GSTIN / Tally)", value=party["pan"] if party["pan_source"] == "entered by user" else "")
            options = [None, *PAYEE_TYPES]
            current = party["payee_type"] if party["payee_source"] == "set by user" else None
            payee = st.selectbox("Payee type override", options, index=options.index(current),
                                 format_func=lambda v: "From the PAN" if v is None else PAYEE_TYPES[v])  # fmt: skip
            transporter = st.checkbox("Transporter (≤ 10 goods carriages) has given a declaration with PAN – NIL TDS u/s 194C",
                                      value=party["transporter_declaration"])  # fmt: skip
            tcs = st.checkbox("Seller charges TCS u/s 206C(1H) on all sales to the client – no 194Q", value=party["tcs_206c1h"])
            note = st.text_input("Note", value=party.get("note") or "")
            if st.form_submit_button("Save party facts", type="primary"):
                body = {"party_key": party["key"], "party_name": party["name"], "pan": pan.strip() or None,
                        "payee_type": payee, "transporter_declaration": transporter, "tcs_206c1h": tcs,
                        "note": note or None}  # fmt: skip
                _do(lambda: api.save_tds_party(client_id, body), "Saved and re-checked.")
    if sec["key"] == "194Q" and detail["vouchers"]:
        with st.expander("Leave single vouchers out of 194Q (seller charged TCS u/s 206C(1H))"):
            labels = {v["voucher_key"]: f"{v['date']} · {v['voucher_type']} {v['voucher_no']} · {rs(v['amount'])}"
                      for v in detail["vouchers"]}  # fmt: skip
            chosen = st.multiselect("Vouchers", list(labels), format_func=labels.get, key=f"{key}_flags")
            if st.button("Leave out", key=f"{key}_flag_btn", disabled=not chosen):
                _do(lambda: [api.save_tds_voucher_flag(client_id, k, True) for k in chosen], "Re-checked.")
            for item in detail["excluded_vouchers"]:
                cols = st.columns([4, 1])
                cols[0].caption(f"Left out: {item['date']} · {item['voucher_type']} {item['voucher_no']} · {rs(item['amount'])}")
                if cols[1].button("Include", key=f"{key}_inc_{item['voucher_key']}"):
                    _do(lambda k=item["voucher_key"]: api.save_tds_voucher_flag(client_id, k, False), "Re-checked.")


def _assign_form(detail: dict, unidentified: dict) -> None:
    """Assign the payee (e.g. the landlord) of party-less payments."""
    with st.form(f"assign_{_safe(detail['section']['key'])}"):
        st.markdown("**Assign the payee of these payments**")
        cols = st.columns([2, 2, 1, 1])
        ledger = cols[0].selectbox("Ledger", unidentified["ledgers"])
        name = cols[1].text_input("Payee name (e.g. the landlord)")
        pan = cols[2].text_input("PAN (optional)")
        payee = cols[3].selectbox("Payee type", [None, *PAYEE_TYPES], format_func=lambda v: "From the PAN" if v is None else PAYEE_TYPES[v])
        st.caption("Every payment of this ledger with no party ledger in the year goes to this payee.")
        if st.form_submit_button("Assign payee", type="primary"):
            body = {"fy": detail["fy"], "ledger": ledger, "payee_name": name, "pan": pan.strip() or None, "payee_type": payee}
            _do(lambda: api.save_tds_assignment(detail["client_id"], body), "Payee assigned; re-checked.")


def _do(action, message: str) -> None:
    try:
        action()
    except api.ApiError as exc:
        show_error(exc)
    else:
        st.toast(message)
        st.rerun()


# ----------------------------------------------------------------- report


def render_summary_block(tds: dict | None) -> None:
    """TDS block of the Client Report."""
    section("TDS summary")
    if tds is None:
        target = api.tds_analysis_fy()
        st.caption(f"TDS analysis is available for FY {target} only." if target else "No TDS analysis.")
        return
    if not tds["has_data"]:
        st.caption("No ledger-level Tally data for this year: import the Tally JSON export to analyse TDS.")
        return
    cols = st.columns(4)
    with cols[0]:
        kpi_card("Parties over a TDS threshold", tds["parties_crossed"])
    with cols[1]:
        kpi_card("TDS payable", rs(tds["tds_payable"]))
    with cols[2]:
        kpi_card("TDS deducted", rs(tds["tds_deducted"]))
    with cols[3]:
        kpi_card("TDS not deducted", rs(tds["not_deducted"]),
                 f"{tds['parties_not_deducted']} not deducted · {tds['parties_short']} short")  # fmt: skip
    extras = []
    if tds.get("parties_excess"):
        extras.append(f"🔵 {tds['parties_excess']} excess deducted ({rs(tds['excess'])})")
    if tds.get("unidentified"):
        extras.append(f"🟠 {tds['unidentified']} payee(s) unidentified")
    if Decimal(tds.get("unallocated") or 0):
        extras.append(f"TDS booked without a party, unallocated: {rs(tds['unallocated'])}")
    if extras:
        st.caption(" · ".join(extras))
    if not tds["mapping_approved"]:
        st.caption("Provisional: approve the ledger mapping on Settings → TDS.")
    if tds["unmapped_ledgers"]:
        st.caption(f"{tds['unmapped_ledgers']} ledger(s) with postings are not mapped to a TDS section.")
    st.caption("Party-wise detail: the TDS tab.")


def render_payer_box(report: dict) -> None:
    payer = report["payer"]
    turnover = rs(payer["previous_turnover"]) if payer["previous_turnover"] else "not imported"
    icons = {True: "✅", False: "⛔", None: "❔"}
    st.markdown(
        f"**Who must deduct (FY {report['fy']})** · client: {payer['constitution'] or 'unknown'} "
        f"({payer['constitution_source']}) · FY {payer['previous_fy']} turnover: {turnover}\n\n"
        f"- {icons[payer['s194q']['applies']]} **194Q:** {payer['s194q']['reason']}\n"
        f"- {icons[payer['others']['applies']]} **194C / 194H / 194J / 194I:** {payer['others']['reason']}"
    )
    if payer.get("rent_under_194ib"):
        st.caption("Rent is tested u/s 194-IB (2%, once a year) instead of 194I.")


def render_report(client: dict, fy: str | None) -> None:
    """The TDS Applicability report, with click-through to the party detail."""
    if not fy:
        empty_state("Select a financial year in the sidebar.")
        return
    target = api.tds_analysis_fy()
    if target and fy != target:
        st.info(f"TDS analysis is available for FY {target} only. Choose FY {target} in the sidebar "
                "(the year is set on Settings → TDS).", icon=":material/event:")  # fmt: skip
        return
    try:
        with st.spinner("Working out TDS…"):
            report = api.tds_report(client["id"], fy)
    except api.ApiError as exc:
        show_error(exc)
        return
    if not report["has_data"]:
        empty_state("No ledger-level Tally data for this year. Import the Tally JSON export (Master + Transactions) on the Data page.")
        return
    if not report["mapping_approved"]:
        st.warning("Ledger mapping not yet approved: review it on Settings → TDS. Alerts start after approval.", icon=":material/rule:")
    render_payer_box(report)
    if report.get("pending_choice"):
        st.warning("Choose 194I(a) or 194I(b) for these rent ledgers on Settings → TDS (their figures are "
                   f"provisional): {', '.join(report['pending_choice'])}", icon=":material/rule:")  # fmt: skip
    for item in report.get("data_quality") or []:
        show = st.error if item.get("level") == "contradiction" else st.warning
        show(f"PAN check – {item['party']}: {item['problem']}", icon=":material/badge:")
    totals = report["totals"]
    cols = st.columns(4)
    with cols[0]:
        kpi_card("Parties over a threshold", totals["crossed"])
    with cols[1]:
        kpi_card("TDS payable", rs(totals["tds"]))
    with cols[2]:
        kpi_card("TDS deducted", rs(totals["deducted"]))
    with cols[3]:
        kpi_card("Total shortfall", rs(totals["shortfall"]), f"{totals['not_deducted']} not deducted · {totals['short']} short")
    st.caption(
        f"🔵 Excess deducted: {rs(totals['excess'])} ({totals['excess_parties']} party/ies) · "
        f"TDS booked without a party: {rs(totals['booked_without_party'])}, of which unallocated "
        f"**{rs(totals['unallocated'])}** · payments with no party ledger (not in the totals): "
        f"{rs(totals['unidentified_payments'])}"
    )

    ranked = report.get("ranked") or []
    if ranked:
        section(f"Ranked by money at stake · total {rs(report['money_at_stake'])}")
        st.caption(report["estimate_label"])
        frame = pd.DataFrame([
            {"#": i, "Status": f"{STATUS_STYLE.get(r['status'], ('', ''))[0]} {r['status_label']}",
             "Party": r["party"], "Section": r["section"], "Aggregate": float(Decimal(r["aggregate"])),
             "Shortfall": float(Decimal(r["shortfall"])), "Excess": float(Decimal(r["excess"])),
             "Money at stake": float(Decimal(r["money_at_stake"]))}
            for i, r in enumerate(ranked, start=1)
        ])  # fmt: skip
        event = st.dataframe(
            frame.style.format({c: "{:,.2f}" for c in ("Aggregate", "Shortfall", "Excess", "Money at stake")}),
            hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="tds_ranked",
        )  # fmt: skip
        picked = list(getattr(event.selection, "rows", []))
        if picked:
            st.session_state["tds_open"] = (ranked[picked[0]]["party_key"], ranked[picked[0]]["section"])
        section("By section (Applicability Checker layout)")
    only_crossed = st.toggle("Only parties over or near a threshold", value=True, key="tds_only_crossed")
    for sec in report["sections"]:
        rows = sec["rows"]
        if only_crossed:
            keep = ("tds_approaching", "tds_unidentified")
            rows = [r for r in rows if r["crossed"].startswith("Yes") or r["status"] in keep]
        sub = sec["subtotal"]
        title = f"{sec['key']} – {sec['nature']} · {sub['crossed']} over threshold · shortfall {rs(sub['shortfall'])}"
        with st.expander(title, expanded=Decimal(sub["shortfall"]) > 0):
            if sec["applies"] is False:
                st.caption(f"Does not apply to this client: {sec['reason']}")
            if not rows:
                st.caption("No party over or near the threshold.")
                continue
            frame = pd.DataFrame([
                {
                    "Status": f"{STATUS_STYLE.get(r['status'], ('', ''))[0]} {r['status_label']}",
                    "Party": r["party"],
                    "PAN": r["pan"] or "not available",
                    "Payee type": r["payee_type"],
                    "Largest single": float(Decimal(r["single_payment"])),
                    "Aggregate": float(Decimal(r["aggregate"])),
                    "Crossed?": "Yes" if r["crossed"].startswith("Yes") else "No",
                    "Crossed on": f"{r['crossed_on']} #{r['crossed_voucher_no']}" if r["crossed_on"] else "",
                    "Base": float(Decimal(r["base"])),
                    "Rate": r["rate"],
                    "TDS": float(Decimal(r["tds"])),
                    "Deducted": float(Decimal(r["deducted"])),
                    "Shortfall": float(Decimal(r["shortfall"])),
                    "Excess": float(Decimal(r["excess"])),
                    "Money at stake": float(Decimal(r["money_at_stake"])),
                }
                for r in rows
            ])  # fmt: skip
            money_cols = {c: "{:,.2f}" for c in ("Largest single", "Aggregate", "Base", "TDS", "Deducted", "Shortfall", "Excess", "Money at stake")}
            event = st.dataframe(frame.style.format(money_cols), hide_index=True, width="stretch",
                                 on_select="rerun", selection_mode="single-row", key=f"tds_rep_{sec['key']}")  # fmt: skip
            picked = list(getattr(event.selection, "rows", []))
            if picked:
                st.session_state["tds_open"] = (rows[picked[0]]["party_key"], sec["key"])
            st.caption(
                f"Subtotal {sec['key']}: aggregate {rs(sub['aggregate'])} · TDS {rs(sub['tds'])} · "
                f"deducted {rs(sub['deducted'])} · shortfall {rs(sub['shortfall'])}"
            )
            pool = sec.get("pool")
            if pool:
                who = f"attributed to {pool['attributed_to']}" if pool["attributed_to"] else "not tied to a party"
                st.caption(f"TDS booked without a party: {rs(pool['amount'])} – {who}; unallocated {rs(pool['unallocated'])}.")
    st.markdown(f"**Grand total shortfall: {rs(totals['shortfall'])}** (TDS payable {rs(totals['tds'])}, deducted {rs(totals['deducted'])})")
    fees = report.get("return_fees") or []
    if fees:
        section(f"s.234E late fee - per return (all parties) · at risk {rs(report['return_fee_total'])}")
        st.caption(report["estimate_label"])
        st.dataframe(pd.DataFrame([
            {"Return": f["return"], "Due": f["due"], "TDS in the return": rs(f["tds"]), "of which not deducted": rs(f["shortfall"]),
             "Days late to today": f["days"], "Fee (₹200/day, capped at the return's TDS)": rs(f["fee"]),
             "TDS booked in the period": rs(f["deducted_in_books"]),
             "Counted": "at risk - no TDS booked, return probably not filed" if f["likely_unfiled"] else "only if not filed"}
            for f in fees
        ]), hide_index=True, width="stretch")  # fmt: skip
        st.caption(f"A return filed on time has no fee. 'At risk' counts only periods where the books show no TDS "
                   f"deducted (the return was probably never filed); if no return at all was filed the total is "
                   f"{rs(report['return_fee_if_none_filed'])}. Check TRACES.")  # fmt: skip
    for g in report.get("pan_groups") or []:
        st.warning(
            f"PAN {g['pan']} ({g['section']}) is behind {len(g['parties'])} party ledgers: {', '.join(g['parties'])}. "
            f"Form 26Q reports them as one deductee: aggregate {rs(g['aggregate'])}, TDS {rs(g['tds'])}, "
            f"deducted {rs(g['deducted'])}. If they are different payees, one PAN is wrong.",
            icon=":material/badge:",
        )
    _render_completeness(client, fy)
    if report["unmapped"]:
        with st.expander(f"⚠ Unmapped ledgers with postings ({len(report['unmapped'])}) – not in the figures above"):
            st.dataframe(pd.DataFrame(report["unmapped"]), hide_index=True, width="stretch")

    stem = f"TDS_Applicability_{_safe(client['name'])}_FY{fy}"
    a, b, _ = st.columns([1, 1, 3])
    a.download_button("Excel (.xlsx)", data=lambda: api.export_tds_report(client["id"], fy, "xlsx"),
                      file_name=f"{stem}.xlsx", mime=XLSX_MIME, key="tds_rep_xlsx")  # fmt: skip
    b.download_button("PDF", data=lambda: api.export_tds_report(client["id"], fy, "pdf"),
                      file_name=f"{stem}.pdf", mime="application/pdf", key="tds_rep_pdf")  # fmt: skip

    opened = st.session_state.get("tds_open")
    if opened:
        st.divider()
        if st.button("Close detail", icon=":material/close:"):
            st.session_state.pop("tds_open", None)
            st.rerun()
        try:
            render_detail(api.tds_detail(client["id"], fy, opened[0], opened[1]))
        except api.ApiError as exc:
            show_error(exc)


def _render_completeness(client: dict, fy: str) -> None:
    try:
        view = api.tds_completeness(client["id"], fy)
    except api.ApiError as exc:
        show_error(exc)
        return
    rows = view["rows"]
    flagged = [r for r in rows if r["flag"]]
    title = f"Completeness: every expense and purchase ledger ({len(rows)})" + (f" · ⚠ {len(flagged)} to check" if flagged else "")
    with st.expander(title):
        st.dataframe(pd.DataFrame([
            {"Ledger": r["ledger"], "Kind": r["kind"], "Treatment": r["treatment"], "Why": r["reason"],
             "Total FY " + view["fy"]: float(Decimal(r["total"])), "Parties": r["parties"],
             "No-party payments": "yes" if r["no_party"] else "", "Any party over its threshold": "yes" if r["any_crossed"] else "no",
             "Check": r["flag"] or ""}
            for r in rows
        ]).style.format({"Total FY " + view["fy"]: "{:,.2f}"}), hide_index=True, width="stretch")  # fmt: skip
        purchases = view["turnover_purchases"]
        st.caption(
            f"Purchase ledgers total {rs(view['total_purchase_ledgers'])}"
            + (f" (turnover purchases figure {rs(purchases)})" if purchases else "")
            + f" · expense ledgers total {rs(view['total_expense_ledgers'])}."
        )


# ---------------------------------------------------------------- settings


def render_rate_master() -> None:
    try:
        data = api.tds_sections()
        settings = api.tds_settings()
    except api.ApiError as exc:
        show_error(exc)
        return
    st.info(data["note"], icon=":material/gavel:")
    with st.form("tds_approaching"):
        left, right = st.columns(2)
        start = int(settings["analysis_fy"][:4])
        years = [f"{y}-{str(y + 1)[2:]}" for y in range(start - 2, start + 3)]
        analysis_fy = left.selectbox(
            "TDS analysis year", years, index=years.index(settings["analysis_fy"]),
            help="TDS is analysed and alerted for this year only; the year before is used only to decide "
            "who must deduct (194Q turnover test, 44AB audit test). Move it forward each year.",
        )  # fmt: skip
        pct = right.number_input("Raise 'approaching threshold' alerts at (% of the threshold)", 1.0, 100.0,
                                 float(settings["approaching_pct"]), 5.0)  # fmt: skip
        materiality = st.number_input(
            "Payments with no party ledger: alert above (₹)", 0.0, 1e10, float(settings["unidentified_min"]), 5000.0,
            help="Below this total they are listed in the report but not alerted. Default ₹30,000, the lowest TDS threshold.",
        )  # fmt: skip
        if st.form_submit_button("Save and re-check"):
            _do(lambda: api.save_tds_settings(pct, analysis_fy, materiality), "Saved.")

    rows = data["sections"]
    frame = pd.DataFrame([
        {
            "ID": r["id"], "Key": r["key"], "Nature of payment": r["nature"],
            "Single payment threshold (0 = n/a)": float(r["single_threshold"]),
            "Annual / aggregate threshold": float(r["aggregate_threshold"]),
            "Rate Ind/HUF %": float(r["rate_individual"]), "Rate other %": float(r["rate_other"]),
            "Rate no PAN %": float(r["rate_no_pan"]), "Base": r["base"],
            "Effective from": date.fromisoformat(r["effective_from"]), "Remarks": r["remarks"] or "",
            "Delete": False,
        }
        for r in rows
    ])  # fmt: skip
    st.caption("Each row applies from its effective-from date: a later row for the same section changes the rate "
               "from that date, and earlier years keep theirs. 'Excess' = TDS only on the amount above the threshold (194Q).")  # fmt: skip
    edited = st.data_editor(
        frame, hide_index=True, width="stretch", key="tds_master_editor", disabled=["ID", "Key"],
        column_config={
            "ID": None,
            "Base": st.column_config.SelectboxColumn(options=["full", "excess"]),
            "Effective from": st.column_config.DateColumn(format="DD-MM-YYYY"),
            "Remarks": st.column_config.TextColumn(width="large"),
            "Delete": st.column_config.CheckboxColumn(help="Tick to delete the row"),
        },
    )  # fmt: skip
    if st.button("Save rate master", type="primary", icon=":material/save:"):
        try:
            originals = {r["id"]: r for r in rows}
            for _, row in edited.iterrows():
                section_id = int(row["ID"])
                if row["Delete"]:
                    api.delete_tds_section(section_id)
                    continue
                body = _section_body(row)
                old = originals[section_id]
                if any(not _same(body[k], old[k]) for k in body):
                    api.update_tds_section(section_id, body)
        except (api.ApiError, ValueError, InvalidOperation) as exc:
            show_error(exc)
        else:
            st.session_state.pop("tds_master_editor", None)
            st.toast("Rate master saved; every client was re-checked.")
            st.rerun()
    with st.expander("Add a rate change (a new row from a later date)"):
        with st.form("tds_new_row"):
            key = st.selectbox("Section", SECTIONS)
            base_row = max((r for r in rows if r["key"] == key), key=lambda r: r["effective_from"], default=None)
            effective = st.date_input("Effective from", value=date.today())
            c = st.columns(5)
            single = c[0].text_input("Single payment threshold", value=str(base_row["single_threshold"]) if base_row else "0")
            aggregate = c[1].text_input("Aggregate threshold", value=str(base_row["aggregate_threshold"]) if base_row else "0")
            ind = c[2].text_input("Rate Ind/HUF %", value=str(base_row["rate_individual"]) if base_row else "")
            other = c[3].text_input("Rate other %", value=str(base_row["rate_other"]) if base_row else "")
            no_pan = c[4].text_input("Rate no PAN %", value=str(base_row["rate_no_pan"]) if base_row else "20")
            remarks = st.text_input("Remarks (source of the change)")
            if st.form_submit_button("Add row"):
                body = {"key": key, "nature": base_row["nature"] if base_row else key, "single_threshold": single,
                        "aggregate_threshold": aggregate, "rate_individual": ind, "rate_other": other,
                        "rate_no_pan": no_pan, "base": base_row["base"] if base_row else "full",
                        "effective_from": effective.isoformat(), "remarks": remarks or None}  # fmt: skip
                _do(lambda: api.create_tds_section(body), "Row added.")


def _same(new, old) -> bool:
    """Equal as amounts when both are numbers ('30000' == '30000.00'), else as text."""
    a, b = api.to_decimal(new), api.to_decimal(old)
    if a is not None and b is not None:
        return a == b
    return (new or "") == (old or "")


def _section_body(row) -> dict:
    def number(value) -> str:
        return format(Decimal(str(value)).normalize(), "f")

    effective = row["Effective from"]
    return {
        "key": row["Key"],
        "nature": str(row["Nature of payment"]).strip(),
        "single_threshold": number(row["Single payment threshold (0 = n/a)"]),
        "aggregate_threshold": number(row["Annual / aggregate threshold"]),
        "rate_individual": number(row["Rate Ind/HUF %"]),
        "rate_other": number(row["Rate other %"]),
        "rate_no_pan": number(row["Rate no PAN %"]),
        "base": row["Base"],
        "effective_from": effective.isoformat() if hasattr(effective, "isoformat") else str(effective),
        "remarks": str(row["Remarks"]).strip() or None,
    }


def render_payer(client: dict, fy: str) -> None:
    """Constitution and audit-liability overrides for the sidebar client and FY."""
    try:
        payer = api.tds_payer(client["id"], fy)
    except api.ApiError as exc:
        show_error(exc)
        return
    turnover = rs(payer["previous_turnover"]) if payer["previous_turnover"] else "not imported"
    st.markdown(
        f"**{client['name']} – FY {fy}.** Constitution: **{payer['constitution'] or 'unknown'}** "
        f"({payer['constitution_source']}). FY {payer['previous_fy']} turnover: **{turnover}**.\n\n"
        f"- 194Q: {payer['s194q']['reason']}\n- 194C / H / J / I: {payer['others']['reason']}"
    )
    with st.form("tds_payer"):
        options = [None, *payer["constitutions"]]
        constitution = st.selectbox("Constitution", options, index=options.index(payer["constitution_override"]),
                                    format_func=lambda v: "From the PAN (GSTIN)" if v is None else v)  # fmt: skip
        audit_options = [None, True, False]
        audit = st.radio(
            f"Liable to tax audit u/s 44AB in FY {payer['previous_fy']}? (decides 194C/H/J/I for an Individual/HUF)",
            audit_options, index=audit_options.index(payer["audit_override"]), horizontal=True,
            format_func=lambda v: {None: "Not confirmed (use the turnover test)", True: "Yes", False: "No"}[v],
        )  # fmt: skip
        if st.form_submit_button("Save and re-check", type="primary"):
            _do(lambda: api.save_tds_payer(client["id"], fy, constitution, audit), "Saved.")


def render_mapping(client: dict) -> None:
    """The proposed ledger -> section mapping, editable, with approval."""
    try:
        mapping = api.tds_mapping(client["id"])
    except api.ApiError as exc:
        show_error(exc)
        return
    rows = mapping["rows"]
    if not rows:
        empty_state("No ledgers yet: import this client's Tally JSON export (Master + Transactions).")
        return
    if mapping["approved_at"]:
        st.success(f"Mapping approved by {mapping['approved_by']} on {mapping['approved_at'][:10]}. TDS alerts are on.")
    else:
        st.warning("Proposed mapping – review it, correct it, then approve. TDS alerts start only after approval.",
                   icon=":material/rule:")  # fmt: skip
    fys = sorted({fy for r in rows for fy in r["amounts"]}, reverse=True)
    show = st.segmented_control("Show", ["With postings", "Unmapped", "All"], default="With postings", key="tds_map_show")
    ledgers = [r for r in rows if r["match_type"] == "ledger"]
    if show == "With postings":
        ledgers = [r for r in ledgers if any(Decimal(v) != 0 for v in r["amounts"].values())]
    elif show == "Unmapped":
        ledgers = [r for r in ledgers if r["role"] == "unmapped"]
    unmapped_active = [r for r in rows if r["match_type"] == "ledger" and r["role"] == "unmapped"
                       and any(Decimal(v) != 0 for v in r["amounts"].values())]  # fmt: skip
    if unmapped_active:
        st.error(f"Unmapped ledgers with payments: {', '.join(r['name'] for r in unmapped_active)}", icon=":material/warning:")
    frame = pd.DataFrame([
        {
            "Ledger": r["name"],
            "Kind": r["kind"] or "",
            "Group": " > ".join(reversed(r["groups"][:3])),
            **{f"FY {fy}": float(Decimal(r["amounts"].get(fy, "0"))) for fy in fys},
            "Rent: confirm section": False if r.get("choice_pending") else None,
            "Role": r["role"],
            "Section": r["section_key"],
            "Why": r["reason"] or "",
            "By": r["source"],
        }
        for r in ledgers
    ])  # fmt: skip
    for r in ledgers:
        if r.get("hint"):
            st.warning(f"**{r['name']}** – {r['hint']}", icon=":material/help:")
        if r.get("rent_comparison"):
            st.dataframe(pd.DataFrame([
                {"If it is": f"{c['section']} at {Decimal(c['rate']).normalize():f}%", "Rent (FY " + c["fy"] + ")": rs(c["rent"]),
                 "TDS due": rs(c["tds_due"]), "TDS on rent in the books": rs(c["deducted"]),
                 "Books minus due": rs(c["difference"])}
                for c in r["rent_comparison"]
            ]), hide_index=True, width="stretch")  # fmt: skip
    edited = st.data_editor(
        frame, hide_index=True, width="stretch", key=f"tds_map_editor_{client['id']}_{show}",
        disabled=["Ledger", "Kind", "Group", *[f"FY {fy}" for fy in fys], "Why", "By"],
        column_config={
            "Role": st.column_config.SelectboxColumn(options=list(ROLE_LABELS), required=True,
                                                     help="; ".join(f"{k} = {v}" for k, v in ROLE_LABELS.items())),
            "Section": st.column_config.SelectboxColumn(options=list(MAPPABLE_SECTIONS)),
            "Rent: confirm section": st.column_config.CheckboxColumn(
                help="Rent ledgers only: set Section to 194I(a) (plant, machinery, equipment - 2%) or "
                "194I(b) (land, building, furniture - 10%) and tick to confirm. Needed before approval."
            ),
            "Why": st.column_config.TextColumn(width="large"),
            **{f"FY {fy}": st.column_config.NumberColumn(format="%.2f", help="Net debit posted in the year") for fy in fys},
        },
    )  # fmt: skip
    save_col, approve_col, refresh_col = st.columns(3)
    if save_col.button("Save mapping changes", type="primary", icon=":material/save:"):
        changes = []
        for (_, new), old in zip(edited.iterrows(), ledgers):
            section_key = new["Section"] if isinstance(new["Section"], str) and new["Section"] else None
            confirmed = bool(old.get("choice_pending") and new["Rent: confirm section"] is True)
            if confirmed and section_key not in ("194I(a)", "194I(b)") and new["Role"] in ("base", "tds"):
                st.error(f"{old['name']}: choose 194I(a) or 194I(b) before confirming.")
                return
            if new["Role"] != old["role"] or section_key != old["section_key"] or confirmed:
                changes.append({"match_type": "ledger", "name": old["name"], "role": new["Role"], "section_key": section_key})
        if changes:
            _do(lambda: api.update_tds_mapping(client["id"], changes), f"Saved {len(changes)} change(s); re-checked.")
        else:
            st.toast("Nothing changed.")
    reviewer = _reviewer()
    if mapping["approved_at"]:
        if approve_col.button("Withdraw approval (stop TDS alerts)"):
            _do(lambda: api.approve_tds_mapping(client["id"], reviewer or "user", approved=False), "Approval withdrawn.")
    elif approve_col.button("Approve mapping", disabled=not reviewer, icon=":material/verified:",
                            help=None if reviewer else "Enter a reviewer name in the sidebar"):  # fmt: skip
        _do(lambda: api.approve_tds_mapping(client["id"], reviewer), "Approved: TDS alerts are on for this client.")
    if refresh_col.button("Re-run proposals", help="Re-applies the rules to ledgers nobody has edited or approved"):
        _do(lambda: api.refresh_tds_mapping(client["id"]), "Proposals refreshed.")

    groups = [r for r in rows if r["match_type"] == "group"]
    with st.expander(f"Group rules ({len(groups)}) – map every ledger under a Tally group"):
        for rule in groups:
            cols = st.columns([3, 2, 1, 1])
            cols[0].write(rule["name"])
            cols[1].write(f"{rule['role']} {rule['section_key'] or ''}")
            if cols[3].button("Delete", key=f"tds_grp_del_{rule['id']}"):
                _do(lambda r=rule: api.update_tds_mapping(client["id"], [{"match_type": "group", "name": r["name"], "role": r["role"], "delete": True}]), "Deleted.")
        with st.form("tds_group_rule"):
            cols = st.columns(3)
            name = cols[0].text_input("Tally group name")
            role = cols[1].selectbox("Role", list(ROLE_LABELS)[:3], format_func=ROLE_LABELS.get)
            section_key = cols[2].selectbox("Section", [None, *SECTIONS], format_func=lambda v: "—" if v is None else v)
            st.caption("A ledger's own rule always beats a group rule.")
            if st.form_submit_button("Add group rule") and name.strip():
                change = {"match_type": "group", "name": name.strip(), "role": role, "section_key": section_key}
                _do(lambda: api.update_tds_mapping(client["id"], [change]), "Group rule added.")
