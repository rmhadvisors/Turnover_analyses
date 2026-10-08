"""Client profile: the facts that decide which statutory limits apply to a client."""

from __future__ import annotations

import streamlit as st

import api_client as api

UNKNOWN = "Unknown"
YES_NO = {UNKNOWN: None, "Yes": True, "No": False}
CHOICES = {
    "entity_type": ("Entity type", {"Individual": "individual", "HUF": "huf", "Partnership firm": "firm",
                                    "LLP": "llp", "Company": "company", "Other (AOP, trust …)": "other"}),
    "nature": ("Nature of work", {"Business": "business", "Profession": "profession", "Both": "both"}),
    "supplies": ("Supplies", {"Goods": "goods", "Services": "services", "Both": "both"}),
    "presumptive": ("Presumptive scheme opted", {"None": "none", "44AD (business)": "44AD",
                                                 "44ADA (profession)": "44ADA"}),
}  # fmt: skip
BOOLS = {
    "gst_registered": "GST registered",
    "special_category": "Special-category state (GST)",
    "cash_within_5pct": "Cash receipts & payments within 5%",
}
LABELS = {**{k: v[0] for k, v in CHOICES.items()}, **BOOLS}


def _pick(label: str, options: dict, current, key: str):
    names = [UNKNOWN, *options]
    values = [None, *options.values()]
    chosen = st.selectbox(label, names, index=values.index(current) if current in values else 0, key=key)
    return dict(zip(names, values, strict=True))[chosen]


def profile_gaps(client_id: int) -> list[str]:
    """Labels of profile fields still unknown (their limits keep being checked)."""
    try:
        return [LABELS.get(name, name) for name in api.get_profile(client_id)["unknown_fields"]]
    except api.ApiError:
        return []


def render_profile(client: dict) -> None:
    try:
        profile = api.get_profile(client["id"])
    except api.ApiError as exc:
        st.error(str(exc))
        return
    st.caption(
        "These facts decide which statutory limits are checked for this client. A field left "
        "**Unknown** keeps its limits switched on. GSTIN, state, registration and entity type "
        "are filled automatically from a Tally import."
    )
    prefix = f"profile_{client['id']}"
    with st.form(f"{prefix}_form"):
        gstin = st.text_input("GSTIN", value=profile["gstin"] or "", max_chars=15)
        if profile["state_name"]:
            st.caption(f"State from GSTIN: {profile['state_name']} ({profile['state_code']})")
        if profile.get("entity_hint"):
            st.info(profile["entity_hint"], icon=":material/help:")
        left, right = st.columns(2)
        values = {"gstin": gstin.strip() or None}
        with left:
            for name in ("entity_type", "nature", "supplies", "presumptive"):
                label, options = CHOICES[name]
                values[name] = _pick(label, options, profile[name], f"{prefix}_{name}")
        with right:
            for name, label in BOOLS.items():
                values[name] = _pick(label, {"Yes": True, "No": False}, profile[name], f"{prefix}_{name}")
        submitted = st.form_submit_button("Save profile and re-check alerts", type="primary")
    if submitted:
        try:
            saved = api.save_profile(client["id"], values)
        except api.ApiError as exc:
            st.error(str(exc))
            return
        st.session_state[f"{prefix}_saved"] = saved["unknown_fields"]
        st.rerun()
    if f"{prefix}_saved" in st.session_state:
        unknown = st.session_state.pop(f"{prefix}_saved")
        st.success(
            "Profile saved. Alerts for limits that no longer apply were acknowledged as "
            "'System - not applicable to client profile'."
        )
        if unknown:
            st.caption("Still unknown (their limits stay on): " + ", ".join(LABELS.get(u, u) for u in unknown))
