from decimal import Decimal, InvalidOperation

import streamlit as st

import api_client as api
from components.common import page_setup, show_error
from components.money import format_full

page_setup("Settings / Thresholds", "⚙️")

METRICS = {
    "sales_turnover": "Sales turnover",
    "purchase_turnover": "Purchase turnover",
    "aggregate_turnover": "Aggregate turnover",
}


def dec(text: str, label: str) -> Decimal:
    try:
        return Decimal(text.replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a number") from exc


# --------------------------------------------------------- percentage bands
settings = api.get_settings()
st.subheader("Percentage-change bands")
st.caption(
    "Applied to turnover, purchases, gross profit and net profit. A value must go *beyond* a "
    "limit to move to the next band (exactly +20% is still Moderate)."
)
with st.form("bands"):
    left, right = st.columns(2)
    moderate = left.number_input(
        "Moderate limit (%)", 0.1, 1000.0, float(settings["moderate_pct"]), 0.5
    )
    significant = right.number_input(
        "Significant limit (%)", 0.1, 1000.0, float(settings["significant_pct"]), 0.5
    )
    include_gst = st.checkbox(
        "Include GST in turnover and purchases (default: taxable value, excluding GST)",
        value=settings["include_gst_in_turnover"],
    )
    st.caption(
        f"🔴 above +{significant:g}%  ·  🟡 +{moderate:g}% to +{significant:g}%  ·  "
        f"🟢 -{moderate:g}% to +{moderate:g}%  ·  🟡 -{significant:g}% to -{moderate:g}%  ·  "
        f"🔴 below -{significant:g}%"
    )
    if st.form_submit_button("Save bands and re-check all clients", type="primary"):
        try:
            api.save_settings(f"{moderate:g}", f"{significant:g}", include_gst)
            st.success("Saved. All clients were re-checked.")
            if include_gst != settings["include_gst_in_turnover"]:
                st.info("The GST setting applies to vouchers imported from now on.")
        except api.ApiError as exc:
            show_error(exc)

# ---------------------------------------------------------- absolute limits
st.divider()
st.subheader("Absolute turnover limits")
st.warning(
    "The pre-filled limits are common Indian compliance figures kept as editable starting "
    "points - **verify each against current law** before relying on it. Edit, disable or add "
    "your own."
)


def limit_form(limit: dict | None) -> None:
    key = f"limit_{limit['id']}" if limit else "limit_new"
    with st.form(key, clear_on_submit=limit is None):
        name = st.text_input("Name", limit["name"] if limit else "")
        a, b, c = st.columns(3)
        metric_keys = list(METRICS)
        metric = a.selectbox(
            "Metric",
            metric_keys,
            index=metric_keys.index(limit["metric"]) if limit else 0,
            format_func=METRICS.get,
        )
        amount = b.text_input(
            "Amount (₹)", str(Decimal(limit["amount"]).quantize(Decimal(1))) if limit else ""
        )
        approaching = c.number_input(
            "Approaching at (% of limit)",
            1.0,
            100.0,
            float(limit["approaching_pct"]) if limit else 80.0,
        )
        fy_scope = st.text_input(
            "Only for FY (blank = every year)",
            limit["fy_scope"] or "" if limit else "",
            placeholder="2025-26",
        )
        description = st.text_area("Why it matters", (limit["description"] or "") if limit else "")
        enabled = st.checkbox("Enabled", value=limit["is_enabled"] if limit else True)
        buttons = st.columns(2)
        save = buttons[0].form_submit_button("Save" if limit else "Add limit", type="primary")
        remove = buttons[1].form_submit_button("Delete") if limit else False
    if save:
        try:
            body = {
                "name": name,
                "metric": metric,
                "amount": str(dec(amount, "Amount")),
                "approaching_pct": f"{approaching:g}",
                "fy_scope": fy_scope.strip() or None,
                "description": description or None,
                "is_enabled": enabled,
            }
            if limit:
                api.update_limit(limit["id"], body)
            else:
                api.create_limit(body)
            st.rerun()
        except (ValueError, api.ApiError) as exc:
            show_error(exc)
    if remove:
        try:
            api.delete_limit(limit["id"])
            st.rerun()
        except api.ApiError as exc:
            show_error(exc)


limits = api.list_limits()
for limit in limits:
    marker = "" if limit["is_enabled"] else " (disabled)"
    seed = " · default - verify" if limit["is_default_seed"] else ""
    title = f"{limit['name']} — {format_full(Decimal(limit['amount']))}{marker}{seed}"
    with st.expander(title):
        limit_form(limit)

with st.expander("➕ Add a limit"):
    limit_form(None)
