"""Month-wise sales vs purchases chart for the current and previous FY.

Two panels share one value axis so the years are directly comparable. Colours are
categorical slots 1 (blue, sales) and 2 (orange, purchases) of the validated
palette, with the dark-mode steps chosen when Streamlit is in dark mode. Text uses
neutral ink, never the series colour.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from api_client import to_decimal
from components.common import unit as selected_unit
from components.money import format_money

LAKH = Decimal(100000)
CRORE = Decimal(10000000)
PALETTE = {
    "light": {"sales": "#8b75d7", "purchases": "#668077", "grid": "#e4e5e2", "ink": "#52514e"},
    "dark": {"sales": "#a78bfa", "purchases": "#82968f", "grid": "#33332f", "ink": "#c3c2b7"},
}


def _mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def _in_unit(values: list[str], divisor: Decimal) -> list[float]:
    return [float((to_decimal(v) or Decimal(0)) / divisor) for v in values]


def monthly_frame(monthly: dict) -> pd.DataFrame:
    """Long-form table behind the chart (also shown as the accessible table view)."""
    rows = []
    for key in ("previous", "current"):
        series = monthly[key]
        for index, month in enumerate(monthly["months"]):
            rows.append(
                {
                    "FY": f"FY {series['fy']}",
                    "Month": month,
                    "Sales": to_decimal(series["sales"][index]),
                    "Purchases": to_decimal(series["purchases"][index]),
                }
            )
    return pd.DataFrame(rows)


def build_monthly_figure(monthly: dict, mode: str = "light", amount_mode: str = "auto") -> go.Figure:
    """The two-panel grouped bar chart as a Plotly figure (no Streamlit calls)."""
    colours = PALETTE[mode]
    everything = [
        to_decimal(v) or Decimal(0)
        for key in ("current", "previous")
        for side in ("sales", "purchases")
        for v in monthly[key][side]
    ]
    peak_value = max((abs(value) for value in everything), default=Decimal(0))
    if amount_mode == "full":
        divisor, unit_label = Decimal(1), chr(0x20b9)
    elif amount_mode == "crores" or (amount_mode == "auto" and peak_value >= CRORE):
        divisor, unit_label = CRORE, chr(0x20b9) + " crores"
    else:
        divisor, unit_label = LAKH, chr(0x20b9) + " lakhs"
    peak = float(peak_value / divisor)

    panels = ("current", "previous")
    figure = make_subplots(
        rows=1,
        cols=2,
        shared_yaxes=True,
        horizontal_spacing=0.04,
        subplot_titles=[f"FY {monthly[p]['fy']}" for p in panels],
    )
    for column, panel in enumerate(panels, start=1):
        for side, label in (("sales", "Sales"), ("purchases", "Purchases")):
            figure.add_trace(
                go.Bar(
                    name=label,
                    x=monthly["months"],
                    y=_in_unit(monthly[panel][side], divisor),
                    marker_color=colours[side],
                    legendgroup=side,
                    showlegend=column == 1,
                    hovertemplate=f"%{{x}} | {label}: {unit_label} %{{y:,.2f}}<extra>FY {monthly[panel]['fy']}</extra>",
                ),
                row=1,
                col=column,
            )
    figure.update_layout(
        barmode="group",
        bargap=0.25,
        bargroupgap=0.06,
        height=380,
        margin={"l": 70, "r": 10, "t": 60, "b": 40},
        legend={"orientation": "h", "y": 1.14, "x": 0, "font": {"color": colours["ink"]}},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": colours["ink"]},
        hovermode="closest",
    )
    figure.update_yaxes(
        range=[0, peak * 1.12],
        gridcolor=colours["grid"],
        zeroline=False,
        showline=False,
        title_text=unit_label,
        title_standoff=12,
        automargin=True,
        col=1,
    )
    figure.update_yaxes(gridcolor=colours["grid"], showline=False, col=2)
    figure.update_xaxes(showgrid=False, linecolor=colours["grid"], automargin=True)
    return figure


def render_monthly(monthly: dict) -> None:
    """Draw the month-wise chart (or a hint when there are no vouchers)."""
    st.markdown("**Month-wise sales vs purchases**")
    if not monthly["has_data"]:
        st.caption("Month-wise data comes from imported Sales / Purchase Registers - none yet.")
        return
    figure = build_monthly_figure(monthly, _mode(), selected_unit())
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})

    with st.expander("Table view"):
        frame = monthly_frame(monthly)
        for column in ("Sales", "Purchases"):
            frame[column] = frame[column].map(lambda v: format_money(v, selected_unit()))
        st.dataframe(frame, hide_index=True, width="stretch")
