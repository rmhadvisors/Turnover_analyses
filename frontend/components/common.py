"""Shared page helpers and access to the global sidebar selections.

The sidebar is the single place where the client and financial year are chosen;
pages read them with `selected_client()` / `select_fy()` and never offer their own
pickers. To change the selection from a page, use the `choose_*` callbacks (they run
before the sidebar is drawn on the next rerun).
"""

from __future__ import annotations

from datetime import date

import streamlit as st

import api_client as api
from components.ui import DISPLAY_MODES, page_header

# Page files, for st.page_link / st.switch_page.
DASHBOARD = "pages/dashboard.py"
CLIENTS = "pages/clients.py"
DATA = "pages/data.py"
ALERTS = "pages/alerts.py"
SETTINGS = "pages/settings.py"

CLIENT_KEY = "global_client_id"
FY_KEY = "global_fy"


def page_setup(title: str, icon: str | None = None, subtitle: str | None = None) -> None:
    """Compatibility wrapper for the page header; config and CSS are set in app.py."""
    page_header(title, subtitle or "Turnover, financial comparisons and compliance alerts.")


def unit() -> str:
    selected = st.session_state.get("display_mode", "Auto")
    return DISPLAY_MODES.get(selected, "auto")


def show_error(exc: Exception) -> None:
    st.error(str(exc))


# ------------------------------------------------------------ financial years


def fy_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def current_fy(today: date | None = None) -> str:
    today = today or date.today()
    return fy_label(today.year if today.month >= 4 else today.year - 1)


def previous_fy(fy: str) -> str:
    return fy_label(int(fy[:4]) - 1)


def fy_choices(data_fys: list[str]) -> list[str]:
    """Sidebar years, newest first: every FY with data plus the next FY and the
    current and five previous FYs, so figures can be entered for a year with no data."""
    start = int(current_fy()[:4])
    recent = {fy_label(year) for year in range(start - 5, start + 2)}
    return sorted(recent | set(data_fys), reverse=True)


# -------------------------------------------------------------- selection


def selected_client() -> dict | None:
    client_id = st.session_state.get(CLIENT_KEY)
    if client_id is None:
        return None
    try:
        return next((c for c in api.workspace()["clients"] if c["id"] == client_id), None)
    except api.ApiError:
        return None


def select_client(label: str = "Client", key: str = "client_id") -> dict | None:
    """Return the globally selected client; show an actionable hint if it is All clients."""
    del label, key
    client = selected_client()
    if client is None:
        st.info("Choose a client in the sidebar to continue, or add one on the Clients page.")
    return client


def select_fy(label: str = "Financial year", key: str = "fy") -> str | None:
    del label, key
    return st.session_state.get(FY_KEY)


def choose_client(client_id: int | None) -> None:
    """on_click callback: change the sidebar client."""
    st.session_state[CLIENT_KEY] = client_id


def choose_fy(fy: str) -> None:
    """Change the sidebar FY on the next rerun (safe to call after the sidebar is drawn)."""
    st.session_state["pending_global_fy"] = fy


def apply_pending_selection() -> None:
    """Called by app.py before the sidebar widgets are created."""
    if "pending_global_fy" in st.session_state:
        st.session_state[FY_KEY] = st.session_state.pop("pending_global_fy")


def page_link(page: str, label: str, icon: str | None = None) -> None:
    try:
        st.page_link(page, label=label, icon=icon)
    except Exception:
        pass
