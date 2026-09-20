"""Shared page scaffolding: page setup, sidebar (alert badge, unit picker), selectors."""

from __future__ import annotations

import streamlit as st

import api_client as api
from components.money import UNITS


def page_setup(title: str, icon: str = "📊") -> None:
    st.set_page_config(page_title=f"{title} · Turnover Analysis", page_icon=icon, layout="wide")
    _sidebar()
    st.title(f"{icon} {title}")


def _sidebar() -> None:
    with st.sidebar:
        try:
            count = api.unacknowledged_count()
        except api.ApiError as exc:
            st.error(str(exc))
            st.stop()
        if count:
            st.error(f"🔔 **{count}** unacknowledged alert{'s' if count != 1 else ''}")
        else:
            st.success("🔔 No open alerts")
        st.selectbox("Amount display", list(UNITS), key="unit_label")


def unit() -> str:
    return UNITS[st.session_state.get("unit_label", next(iter(UNITS)))]


def show_error(exc: Exception) -> None:
    st.error(str(exc))


def select_client(label: str = "Client", key: str = "client_id") -> dict | None:
    """Client dropdown; returns the chosen client dict, or None (with a hint) if none exist."""
    clients = api.list_clients()
    if not clients:
        st.info("No clients yet - add one on the **Clients** page first.")
        return None
    by_id = {c["id"]: c for c in clients}
    chosen = st.selectbox(label, list(by_id), format_func=lambda i: by_id[i]["name"], key=key)
    return by_id[chosen]


def select_fy(label: str = "Financial year", key: str = "fy") -> str | None:
    years = api.financial_years()
    return st.selectbox(label, years, format_func=lambda y: f"FY {y}", key=key) if years else None


def page_link(page: str, label: str) -> None:
    """Link to another page; silently skipped if Streamlit cannot resolve it."""
    try:
        st.page_link(page, label=label)
    except Exception:
        pass
