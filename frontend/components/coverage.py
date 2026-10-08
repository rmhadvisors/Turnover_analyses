"""'Data coverage': which financial years have data for a client, and from where."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import api_client as api
from components.common import current_fy, fy_label

TICK, NONE = "✓", ""
PROFIT_SOURCE = {"manual": "manual", "pl_import": "P&L import", "tally_json": "from Tally"}


def _years(data_fys: set[str]) -> list[str]:
    """Years with data plus the current and two previous FYs, newest first."""
    start = int(current_fy()[:4])
    return sorted(set(data_fys) | {fy_label(y) for y in range(start - 2, start + 1)}, reverse=True)


def _by_client(rows: list[dict], client_id: int) -> dict[str, dict]:
    return {r["fy"]: r for r in rows if r["client_id"] == client_id}


def _source(row: dict | None) -> str:
    """Short label for one client/FY cell."""
    if row is None:
        return "Missing"
    parts = []
    if row["sales_vouchers"] or row["purchase_vouchers"]:
        parts.append("Tally")
    if row["figures"] == "manual":
        parts.append("Manual")
    elif row["figures"] == "imported" and not parts:
        parts.append("Imported figures")
    return " + ".join(parts) or "Missing"


def render_client_coverage(client_id: int) -> None:
    """FY rows × (Tally import / manual entry / missing) for one client."""
    try:
        rows = _by_client(api.coverage(), client_id)
    except api.ApiError as exc:
        st.error(str(exc))
        return
    table = []
    for fy in _years(set(rows)):
        row = rows.get(fy)
        vouchers = (row["sales_vouchers"], row["purchase_vouchers"]) if row else (0, 0)
        table.append(
            {
                "Financial year": f"FY {fy}" + (" (in progress)" if fy == current_fy() else ""),
                "Tally import": f"{TICK} {vouchers[0]} sales · {vouchers[1]} purchases"
                if any(vouchers)
                else NONE,
                "Manual entry": TICK if row and row["figures"] == "manual" else NONE,
                "Profit figures": f"{TICK} {PROFIT_SOURCE.get(row.get('profit_source'), '')}".strip()
                if row and row["has_profit"]
                else NONE,
                "Missing": "✗ no data" if row is None else NONE,
            }
        )
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    st.caption(
        "Tally import: sales / purchase vouchers stored for the year. Manual entry: yearly "
        "figures typed on the Data page. Profit figures: gross or net profit is recorded."
    )


def coverage_matrix(clients: list[dict]) -> pd.DataFrame:
    """Clients × FY, each cell 'Tally', 'Manual', 'Tally + Manual' or 'Missing'."""
    rows = api.coverage()
    years = _years({r["fy"] for r in rows})
    data = []
    for client in clients:
        mine = _by_client(rows, client["id"])
        data.append({"Client": client["name"], **{f"FY {fy}": _source(mine.get(fy)) for fy in years}})
    return pd.DataFrame(data)
