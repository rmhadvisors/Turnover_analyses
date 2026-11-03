"""Excel and PDF exports of the client comparison report and the all-clients summary.

Excel cells hold real numbers with number formats (never text), so the CA can
sort, sum and re-format them. Indian digit grouping / lakh / crore display is done
with number formats; for the lakh and crore units the stored number is scaled.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference, Series
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.schemas.reports import SummaryRow
from app.services.alert_engine import BAND_DISPLAY, AbsoluteLimitStatus
from app.services.calculations import compare_metric
from app.services.comparison_service import (
    ClientComparison,
    MonthlyComparison,
)
from app.services.report_builder import NO_COMPARISON_LABEL, limit_message
from app.utils.indian_format import CRORE, LAKH, format_indian

UNITS = ("auto", "lakhs", "crores", "full")
SIGN_FLAGS = {"turned_to_loss": "Turned to Loss", "turned_to_profit": "Turned to Profit"}

SALES_COLOUR = "#2a78d6"  # categorical slot 1 (blue), validated palette
PURCHASE_COLOUR = "#eb6834"  # categorical slot 2 (orange)
STATUS_TEXT = {"critical": "B42318", "warning": "A15C07", "ok": "137333", "none": "5F6368"}
STATUS_FILL = {"critical": "FDE7E7", "warning": "FFF1CC", "ok": "E3F4E7", "none": "F1F3F4"}

# The deployed server's clock is UTC; stamp exports in India time (no DST, so a fixed offset).
IST = timezone(timedelta(hours=5, minutes=30), "IST")

RUPEE = "₹"
FMT_INDIAN_FULL = f'[>=10000000]"{RUPEE}"##\\,##\\,##\\,##0.00;[>=100000]"{RUPEE}"##\\,##\\,##0.00;"{RUPEE}"#,##0.00'


def generated_stamp() -> str:
    """When this file is being built, e.g. 'Generated 27-Oct-2026 17:16 IST'."""
    return f"Generated {datetime.now(IST):%d-%b-%Y %H:%M} IST"


def _tone(band: str | None) -> str:
    if band in ("significant_increase", "significant_decrease"):
        return "critical"
    if band in ("moderate_increase", "moderate_decrease"):
        return "warning"
    return "ok" if band == "normal" else "none"


# ------------------------------------------------------------------- Excel


def _scaled(value: Decimal | None, unit: str) -> tuple[float | None, str]:
    """(number to store, suffix key) for a rupee value in the chosen unit."""
    if value is None:
        return None, "full"
    if unit == "full":
        return float(value), "full"
    if unit == "crores" or (unit == "auto" and abs(value) >= CRORE):
        return float(value / CRORE), "cr"
    return float(value / LAKH), "l"


def _money_format(kind: str, arrows: bool = False) -> str:
    if kind == "full":
        if arrows:
            return f'"{RUPEE}"#,##0.00" ↑";"{RUPEE}"#,##0.00" ↓";"{RUPEE}"#,##0.00'
        return FMT_INDIAN_FULL
    suffix = " L" if kind == "l" else " Cr"
    base = f'"{RUPEE}"#,##0.00"{suffix}'
    if arrows:
        return f'{base} ↑";{base} ↓";{base}"'
    return base + '"'


def _text(ws, ref: str, value: str, font: Font | None = None) -> None:
    ws[ref] = value
    if font is not None:
        ws[ref].font = font


_THIN = Side(style="thin", color="BFC3C7")
_BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_BLOCK_RULE = Side(style="thin", color="1F3A5F")
_HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")


def _header_row(ws, row: int, labels: list[str]) -> None:
    for col, label in enumerate(labels, start=1):
        cell = ws.cell(row=row, column=col, value=label)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEAD_FILL
        cell.border = _BOX
        cell.alignment = Alignment(horizontal="center" if col > 1 else "left", vertical="center")


def _put_money(ws, row: int, col: int, value: Decimal | None, unit: str, arrows: bool = False):
    cell = ws.cell(row=row, column=col)
    number, kind = _scaled(value, unit)
    cell.value = number if number is not None else "—"
    if number is not None:
        cell.number_format = _money_format(kind, arrows)
    cell.alignment = Alignment(horizontal="right")
    cell.border = _BOX
    return cell


ROW_UNIT_LABEL = {"full": RUPEE, "lakhs": f"{RUPEE} L", "crores": f"{RUPEE} Cr"}


def _row_unit(values: tuple[Decimal | None, ...], unit: str) -> str:
    """One unit for a whole Comparison row, so its Unit cell is true of every figure in it."""
    if unit != "auto":
        return unit
    biggest = max((abs(v) for v in values if v is not None), default=Decimal(0))
    return "crores" if biggest >= CRORE else "lakhs"


def _put_pct(ws, row: int, col: int, pct: Decimal | None):
    """Change % as a real percentage (115.08%), green up / red down; "—" with no baseline."""
    cell = ws.cell(row=row, column=col)
    if pct is not None:
        cell.value = float(pct / 100)
        cell.number_format = "0.00%"
        if pct != 0:
            cell.font = Font(color="137333" if pct > 0 else "B42318")
    else:
        cell.value = "—"
    cell.alignment, cell.border = Alignment(horizontal="right"), _BOX
    return cell


def _comparison_sheet(ws, report: ClientComparison, unit: str, generated: str) -> None:
    _text(ws, "A1", _heading(report), Font(bold=True, size=14))
    _text(ws, "A2", f"Client: {report.client_name}", Font(bold=True))
    unit_note = {"auto": "lakhs / crores", "lakhs": "lakhs", "crores": "crores", "full": "rupees"}
    _text(ws, "A3", generated, Font(bold=True, color="1F3A5F"))
    ws["A4"] = f"Amounts in {unit_note[unit]}."
    ws["A4"].font = Font(italic=True, color="5F6368")

    _header_row(
        ws, 5, ["Particular", "Unit", f"FY {report.previous_fy}", f"FY {report.fy}", "Difference",
                "Change %", "Status", "Flag"]
    )  # fmt: skip
    for offset, row in enumerate(report.rows):
        r = 6 + offset
        difference = row.comparison.difference if row.comparison else None
        pct = row.comparison.change_pct if row.comparison else None
        row_unit = _row_unit((row.previous, row.current), unit)
        ws.cell(row=r, column=1, value=row.label).font = Font(bold=True)
        unit_cell = ws.cell(row=r, column=2, value=ROW_UNIT_LABEL[row_unit])
        unit_cell.alignment = Alignment(horizontal="center")
        _put_money(ws, r, 3, row.previous, row_unit)
        _put_money(ws, r, 4, row.current, row_unit)
        diff_cell = _put_money(ws, r, 5, difference, row_unit, arrows=True)
        _put_pct(ws, r, 6, pct)
        label = BAND_DISPLAY[row.band][1] if row.band else NO_COMPARISON_LABEL
        tone = _tone(row.band.value if row.band else None)
        status = ws.cell(row=r, column=7, value=label)
        status.font = Font(bold=True, color=STATUS_TEXT[tone])
        status.fill = PatternFill("solid", fgColor=STATUS_FILL[tone])
        flag = SIGN_FLAGS.get(row.comparison.sign_change) if row.comparison else None
        ws.cell(row=r, column=8, value=flag or "")
        for col in (1, 2, 7, 8):
            ws.cell(row=r, column=col).border = _BOX
        if difference is not None and difference != 0:
            diff_cell.font = Font(color="137333" if difference > 0 else "B42318")

    r = 6 + len(report.rows) + 1
    ws.cell(row=r, column=1, value="Absolute limit alerts").font = Font(bold=True, size=12)
    active = [x for x in report.limits if x.status != AbsoluteLimitStatus.BELOW]
    active.sort(key=lambda x: x.status != AbsoluteLimitStatus.CROSSED)
    if not active:
        ws.cell(row=r + 1, column=1, value="No absolute limit has been crossed or approached.")
    for i, limit in enumerate(active):
        tone = "critical" if limit.status == AbsoluteLimitStatus.CROSSED else "warning"
        ws.cell(row=r + 1 + i, column=1, value=limit_message(limit)).font = Font(
            color=STATUS_TEXT[tone]
        )
    r += max(len(active), 1) + 2
    footer = [
        *report.notes,
        "Limits are editable defaults - verify current limits before relying on them.",
    ]
    for note in footer:
        ws.cell(row=r, column=1, value=note).font = Font(italic=True, color="5F6368")
        r += 1

    for col, width in zip("ABCDEFGH", (18, 8, 16, 16, 18, 12, 26, 18), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A6"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True


@dataclass
class MonthRow:
    """One row of a month-wise block. `cur` is None for a month not yet in the data."""

    label: str
    prev: Decimal | None
    cur: Decimal | None
    kind: str = "month"  # "month" | "total" | "full_year"

    @property
    def pending(self) -> bool:
        return self.kind == "month" and self.cur is None

    @property
    def difference(self) -> Decimal | None:
        return None if self.prev is None or self.cur is None else self.cur - self.prev

    @property
    def change_pct(self) -> Decimal | None:
        return None if self.difference is None else compare_metric(self.prev, self.cur).change_pct


def _heading(report: ClientComparison) -> str:
    return f"TURNOVER ANALYSIS — FY {report.previous_fy} vs FY {report.fy}"


def _metric_rows(
    monthly: MonthlyComparison, previous: list[Decimal], current: list[Decimal]
) -> list[MonthRow]:
    """Month rows, then the Total. While the current year is incomplete the Total covers the
    months loaded in both years (matching the Comparison sheet) and the previous year's full
    total gets its own row; months not yet loaded stay blank rather than showing a 100% fall."""
    active = monthly.active_months
    rows = [
        MonthRow(month, previous[i], current[i] if i < active else None)
        for i, month in enumerate(monthly.months)
    ]
    zero = Decimal(0)
    prev_total, cur_total = sum(previous[:active], zero), sum(current[:active], zero)
    if not monthly.is_incomplete or active == 0:
        return [*rows, MonthRow("Total", sum(previous, zero), sum(current, zero), "total")]
    span = f"{monthly.months[0]}–{monthly.months[active - 1]}"
    return [
        *rows,
        MonthRow(f"Total ({span}, same period both years)", prev_total, cur_total, "total"),
        MonthRow(
            f"Previous year full total ({monthly.months[0]}–{monthly.months[-1]})",
            sum(previous, zero),
            None,
            "full_year",
        ),
    ]


MONTH_BLOCKS = (  # (metric title, attribute of MonthlySeries, current colour, previous colour)
    ("Sales", "sales", SALES_COLOUR, "#9dc3f0"),
    ("Purchases", "purchases", PURCHASE_COLOUR, "#f6b79c"),
)
_PENDING_FILL = PatternFill("solid", fgColor="F1F3F4")
_PENDING_FONT = Font(color="9AA0A6")
_BLOCK_ROWS = 2 + 12 + 2  # title, header, months, total and the previous-year full-year row


def _write_month_block(ws, top: int, title: str, rows: list[MonthRow], fy: str, prev_fy: str):
    ws.cell(row=top, column=1, value=f"{title.upper()} (₹ lakhs)").font = Font(bold=True, size=12)
    _header_row(ws, top + 1, ["Month", f"FY {prev_fy}", f"FY {fy}", "Difference", "Change %"])
    for r, row in enumerate(rows, start=top + 2):
        bold = row.kind != "month"
        ws.cell(row=r, column=1, value=row.label).font = Font(bold=bold)
        ws.cell(row=r, column=1).border = _BOX
        # Values, not =SUM(): readers that do not recalculate (previews, pandas,
        # openpyxl) would otherwise show blanks.
        for col, value in ((2, row.prev), (3, row.cur)):
            cell = ws.cell(row=r, column=col, value="—" if value is None else float(value / LAKH))
            if value is not None:
                cell.number_format = f'"{RUPEE}"#,##0.00" L"'
            cell.alignment, cell.border = Alignment(horizontal="right"), _BOX
            cell.font = Font(bold=bold)
        diff_cell = _put_money(ws, r, 4, row.difference, "lakhs", arrows=True)
        difference = row.difference
        colour = None if not difference else "137333" if difference > 0 else "B42318"
        diff_cell.font = Font(bold=bold, color=colour)
        pct_cell = _put_pct(ws, r, 5, row.change_pct)
        pct_cell.font = Font(bold=bold, color=pct_cell.font.color)
        if row.pending:  # a month that has not happened yet: grey, no figures
            for col in range(1, 6):
                cell = ws.cell(row=r, column=col)
                cell.fill, cell.font = _PENDING_FILL, _PENDING_FONT
    return top + 2, top + 1 + len(rows)  # first and last data row


def _monthly_sheet(
    wb: Workbook, monthly: MonthlyComparison, report: ClientComparison, generated: str
) -> None:
    fy, prev_fy = report.fy, report.previous_fy
    ws = wb.create_sheet("Month-wise")
    _text(ws, "A1", _heading(report), Font(bold=True, size=14))
    _text(ws, "A2", f"Client: {report.client_name}", Font(bold=True))
    _text(ws, "A3", generated, Font(bold=True, color="1F3A5F"))
    ws["A4"] = "Amounts in ₹ lakhs, net of credit / debit notes. Built from imported vouchers."
    ws["A4"].font = Font(italic=True, color="5F6368")
    if monthly.data_through:
        ws["A5"] = f"FY {fy} in progress — data to {monthly.data_through:%d-%b-%Y}."
        ws["A5"].font = Font(italic=True, color="5F6368")
    if monthly.later_data_note:
        ws["A6"] = monthly.later_data_note
        ws["A6"].font = Font(italic=True, color="5F6368")
    top = 7
    for metric, attr, cur_colour, prev_colour in MONTH_BLOCKS:
        rows = _metric_rows(
            monthly, getattr(monthly.previous, attr), getattr(monthly.current, attr)
        )
        first, _ = _write_month_block(ws, top, metric, rows, fy, prev_fy)
        last_month = first + len(monthly.months) - 1
        chart = BarChart()
        chart.type, chart.grouping = "col", "clustered"
        chart.title = f"{metric} — FY {prev_fy} vs FY {fy} (₹ lakhs)"
        chart.y_axis.title = "₹ lakhs"
        chart.legend.position = "b"
        for col, year, colour in ((2, prev_fy, prev_colour), (3, fy, cur_colour)):
            series = Series(
                Reference(ws, min_col=col, min_row=first, max_row=last_month),
                title=f"{metric} FY {year}",
            )
            series.graphicalProperties.solidFill = colour.lstrip("#")
            chart.series.append(series)
        chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=last_month))
        chart.height, chart.width = 7.6, 16
        ws.add_chart(chart, f"G{top}")
        top += _BLOCK_ROWS + 2
    ws.column_dimensions["A"].width = 46
    for col in "BCDE":
        ws.column_dimensions[col].width = 18
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


TDS_BLOCK_LINES = (
    ("Parties that crossed a TDS threshold", "parties_crossed", False),
    ("TDS payable on them", "tds_payable", True),
    ("TDS deducted", "tds_deducted", True),
    ("TDS not deducted (shortfall)", "not_deducted", True),
    ("Parties with no TDS deducted", "parties_not_deducted", False),
    ("Parties short deducted", "parties_short", False),
)


def tds_block_note(tds: dict) -> str | None:
    if not tds["mapping_approved"]:
        return "Ledger mapping not yet approved (Settings -> TDS): figures are provisional."
    if tds["unmapped_ledgers"]:
        return f"{tds['unmapped_ledgers']} ledger(s) with postings are not mapped to a TDS section."
    return None


def _tds_sheet(wb: Workbook, tds: dict, fy: str) -> None:
    ws = wb.create_sheet("TDS")
    _text(ws, "A1", f"TDS SUMMARY – FY {fy}", Font(bold=True, size=13))
    _header_row(ws, 3, ["", "Value"])
    for i, (label, key, money) in enumerate(TDS_BLOCK_LINES, start=4):
        ws.cell(row=i, column=1, value=label).border = _BOX
        if money:
            _put_money(ws, i, 2, Decimal(tds[key]), "full")
        else:
            ws.cell(row=i, column=2, value=tds[key]).border = _BOX
    note = tds_block_note(tds)
    if note:
        ws.cell(row=4 + len(TDS_BLOCK_LINES) + 1, column=1, value=note).font = Font(
            italic=True, color="B42318"
        )
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 20


def client_report_xlsx(
    report: ClientComparison,
    monthly: MonthlyComparison | None,
    unit: str = "auto",
    tds: dict | None = None,
) -> bytes:
    generated = generated_stamp()  # one build time for every sheet
    wb = Workbook()
    ws = wb.active
    ws.title = "Comparison"
    _comparison_sheet(ws, report, unit, generated)
    if monthly is not None and monthly.has_data:
        _monthly_sheet(wb, monthly, report, generated)
    if tds is not None and tds["has_data"]:
        _tds_sheet(wb, tds, report.fy)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def summary_xlsx(rows: list[SummaryRow], fy: str, unit: str = "auto") -> bytes:
    """All-clients summary, biggest turnover movers first; autofilter for re-sorting."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    _text(ws, "A1", f"TURNOVER SUMMARY – FY {fy}", Font(bold=True, size=14))
    ws["A2"] = f"Sorted by size of turnover change. {generated_stamp()}."
    ws["A2"].font = Font(italic=True, color="5F6368")
    headers = ["Client", "Previous FY turnover", "Current FY turnover", "Change %", "Status",
               "Net profit flag", "Limits crossed", "Limits approaching", "Open alerts",
               "TDS: parties crossed", "TDS payable", "TDS not deducted"]  # fmt: skip
    _header_row(ws, 4, headers)
    ordered = sorted(rows, key=lambda r: (r.change_pct is None, -abs(r.change_pct or 0)))
    for i, row in enumerate(ordered):
        r = 5 + i
        ws.cell(row=r, column=1, value=row.client_name).border = _BOX
        _put_money(ws, r, 2, row.previous_turnover, unit)
        _put_money(ws, r, 3, row.current_turnover, unit)
        pct = ws.cell(row=r, column=4)
        if row.change_pct is not None:
            pct.value, pct.number_format = float(row.change_pct / 100), "0.00%"
        else:
            pct.value = "—"
        pct.alignment, pct.border = Alignment(horizontal="right"), _BOX
        tone = _tone(row.band)
        status = ws.cell(row=r, column=5, value=row.status_label)
        status.font = Font(bold=True, color=STATUS_TEXT[tone])
        status.fill = PatternFill("solid", fgColor=STATUS_FILL[tone])
        ws.cell(row=r, column=6, value=SIGN_FLAGS.get(row.net_profit_flag, ""))
        for col, value in (
            (7, row.limits_crossed),
            (8, row.limits_approaching),
            (9, row.open_alerts),
        ):
            ws.cell(row=r, column=col, value=value).alignment = Alignment(horizontal="center")
        for col in (5, 6, 7, 8, 9):
            ws.cell(row=r, column=col).border = _BOX
        crossed = ws.cell(
            row=r,
            column=10,
            value=row.tds_parties_crossed if row.tds_parties_crossed is not None else "—",
        )
        crossed.alignment, crossed.border = Alignment(horizontal="center"), _BOX
        _put_money(ws, r, 11, row.tds_payable, unit)
        shortfall = _put_money(ws, r, 12, row.tds_not_deducted, unit)
        if row.tds_not_deducted:
            shortfall.font = Font(bold=True, color=STATUS_TEXT["critical"])
    ws.auto_filter.ref = f"A4:L{4 + len(ordered)}"
    ws.freeze_panes = "B5"
    widths = (30, 20, 20, 12, 26, 18, 14, 18, 12, 14, 18, 18)
    for col, width in zip("ABCDEFGHIJKL", widths, strict=True):
        ws.column_dimensions[col].width = width
    ws.page_setup.orientation = "landscape"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------- PDF

_FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
_FONTS_READY = False


def _register_fonts() -> None:
    global _FONTS_READY
    if not _FONTS_READY:
        pdfmetrics.registerFont(TTFont("DejaVu", str(_FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")
        _FONTS_READY = True


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName="DejaVu", fontSize=9, leading=12)
    return {
        "title": ParagraphStyle(
            "title", parent=base, fontName="DejaVu-Bold", fontSize=15, leading=19
        ),
        "client": ParagraphStyle("client", parent=base, fontSize=11, leading=15, spaceAfter=6),
        "h2": ParagraphStyle(
            "h2", parent=base, fontName="DejaVu-Bold", fontSize=11, spaceBefore=10, spaceAfter=4
        ),
        "small": ParagraphStyle(
            "small", parent=base, fontSize=8, textColor=colors.HexColor("#5f6368")
        ),
        "cell": base,
    }


def _nice_axis(values: list[float]) -> tuple[float, float]:
    """(max, step) for a value axis, so the tallest bar never touches the top edge."""
    peak = max(values, default=0.0) or 1.0
    raw_step = peak / 5
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw_step)
    return step * math.ceil(peak / step), step


def _lakhs(values: list[Decimal | None]) -> list[float | None]:
    return [None if v is None else float(v / LAKH) for v in values]


def _bar_drawing(
    title: str,
    months: tuple[str, ...],
    series: list[tuple[str, list[float | None], str]],  # (legend label, lakhs per month, colour)
    width: float,
    height: float,
) -> Drawing:
    """Grouped bars, one series per FY, in rupee lakhs. Months with no data have no bar."""
    drawing = Drawing(width, height)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 38, 26, width - 50, height - 62
    chart.data = [values for _label, values, _colour in series]
    chart.categoryAxis.categoryNames = list(months)
    chart.categoryAxis.labels.fontName = chart.valueAxis.labels.fontName = "DejaVu"
    chart.categoryAxis.labels.fontSize = chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax, chart.valueAxis.valueStep = _nice_axis(
        [v for _label, values, _colour in series for v in values if v is not None]
    )
    chart.valueAxis.gridStrokeColor = colors.HexColor("#d9dcdf")
    chart.valueAxis.visibleGrid = True
    for bar, (_label, _values, colour) in zip(chart.bars, series, strict=False):
        bar.fillColor = colors.HexColor(colour)
    chart.bars.strokeColor = None
    chart.groupSpacing, chart.barSpacing = 5, 1
    drawing.add(chart)
    drawing.add(String(width / 2, height - 12, title, fontName="DejaVu-Bold",
                       fontSize=9, textAnchor="middle"))  # fmt: skip
    drawing.add(
        String(4, height - 30, "₹ lakhs", fontName="DejaVu", fontSize=7, fillColor=colors.grey)
    )
    x = width - 50 - 120 * len(series)  # legend, top right, clear of the plot
    for label, _values, colour in series:
        drawing.add(Rect(x, height - 28, 9, 9, fillColor=colors.HexColor(colour), strokeColor=None))
        drawing.add(String(x + 13, height - 27, label, fontName="DejaVu", fontSize=8))
        x += 120
    return drawing


def _pdf_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("DejaVu", 7)
    canvas.setFillColor(colors.HexColor("#5f6368"))
    canvas.drawString(
        15 * mm,
        8 * mm,
        f"{generated_stamp()} · Turnover Analysis & Alert Tool",
    )
    canvas.drawRightString(landscape(A4)[0] - 15 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _status_paragraph(label: str, band: str | None, flag: str | None, styles) -> Paragraph:
    hex_colour = STATUS_TEXT[_tone(band)]
    text = f'<font color="#{hex_colour}"><b>● {escape(label)}</b></font>'
    if flag:
        text += f' <font size="7" color="#{hex_colour}">[{escape(flag)}]</font>'
    return Paragraph(text, styles["cell"])


def _comparison_table(report: ClientComparison, unit: str, styles) -> Table:
    head = [
        "Particular",
        f"FY {report.previous_fy}",
        f"FY {report.fy}",
        "Difference",
        "Change %",
        "Status",
    ]
    data: list[list] = [head]
    fills, signs = [], []
    for i, row in enumerate(report.rows, start=1):
        comparison = row.comparison
        difference = comparison.difference if comparison else None
        arrow = ""
        if difference is not None and difference != 0:
            arrow = " ↑" if difference > 0 else " ↓"
        diff_text = format_indian(abs(difference), unit) + arrow if difference is not None else "—"
        pct = comparison.change_pct if comparison else None
        label = BAND_DISPLAY[row.band][1] if row.band else NO_COMPARISON_LABEL
        flag = SIGN_FLAGS.get(comparison.sign_change) if comparison else None
        prev = format_indian(row.previous, unit) if row.previous is not None else "—"
        cur = format_indian(row.current, unit) if row.current is not None else "—"
        data.append([
            Paragraph(f"<b>{escape(row.label)}</b>", styles["cell"]), prev, cur, diff_text,
            f"{pct:.2f}%" if pct is not None else "—",
            _status_paragraph(label, row.band.value if row.band else None, flag, styles),
        ])  # fmt: skip
        signs += _sign_colours(i, difference, pct)
        fills.append(
            (
                "BACKGROUND",
                (5, i),
                (5, i),
                colors.HexColor("#" + STATUS_FILL[_tone(row.band.value if row.band else None)]),
            )
        )
    table = Table(
        data,
        colWidths=[38 * mm, 36 * mm, 36 * mm, 36 * mm, 26 * mm, 84 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"), ("FONTNAME", (0, 1), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (4, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFC3C7")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        *fills, *signs,
    ]))  # fmt: skip
    return table


def _sign_colours(row: int, difference: Decimal | None, pct: Decimal | None) -> list[tuple]:
    """Green up / red down on the Difference (col 3) and Change % (col 4) cells, as in Excel."""
    return [
        ("TEXTCOLOR", (col, row), (col, row), colors.HexColor("#137333" if v > 0 else "#B42318"))
        for col, v in ((3, difference), (4, pct))
        if v
    ]


def _lakh_text(value: Decimal | None, arrow: bool = False) -> str:
    if value is None:
        return "—"
    text = f"₹{value / LAKH:,.2f} L"
    if arrow and value != 0:
        text += " ↑" if value > 0 else " ↓"
    return text


def _month_table(rows: list[MonthRow], fy: str, prev_fy: str) -> Table:
    data = [["Month", f"FY {prev_fy}", f"FY {fy}", "Difference", "Change %"]]
    style: list[tuple] = []
    for i, row in enumerate(rows, start=1):
        pct = row.change_pct
        data.append([
            row.label, _lakh_text(row.prev), _lakh_text(row.cur),
            _lakh_text(row.difference, arrow=True), f"{pct:.2f}%" if pct is not None else "—",
        ])  # fmt: skip
        if row.kind != "month":
            style += [("FONTNAME", (0, i), (-1, i), "DejaVu-Bold")]
            if row.kind == "total":
                style += [("LINEABOVE", (0, i), (-1, i), 1, colors.HexColor("#1F3A5F"))]
        if row.pending:
            style += [
                ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F1F3F4")),
                ("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#9AA0A6")),
            ]
        else:
            style += _sign_colours(i, row.difference, pct)
    table = Table(
        data, colWidths=[82 * mm, 38 * mm, 38 * mm, 42 * mm, 28 * mm], repeatRows=1, hAlign="LEFT"
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"), ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFC3C7")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        *style,
    ]))  # fmt: skip
    return table


def _month_blocks_pdf(monthly: MonthlyComparison, report: ClientComparison, styles) -> list:
    """Sales, then Purchases: a table with its chart below it, each kept on one page."""
    blocks = []
    for metric, attr, cur_colour, prev_colour in MONTH_BLOCKS:
        previous, current = getattr(monthly.previous, attr), getattr(monthly.current, attr)
        loaded = [v if i < monthly.active_months else None for i, v in enumerate(current)]
        chart = _bar_drawing(
            f"{metric} — FY {report.previous_fy} vs FY {report.fy}",
            monthly.months,
            [
                (f"{metric} FY {report.previous_fy}", _lakhs(previous), prev_colour),
                (f"{metric} FY {report.fy}", _lakhs(loaded), cur_colour),
            ],
            250 * mm,
            62 * mm,
        )
        rows = _metric_rows(monthly, previous, current)
        blocks.append(
            KeepTogether(
                [
                    Paragraph(f"{metric.upper()} (₹ lakhs)", styles["h2"]),
                    _month_table(rows, report.fy, report.previous_fy),
                    Spacer(1, 6),
                    chart,
                ]
            )
        )
    return blocks


def _tds_pdf_block(tds: dict, styles) -> list:
    rows = [
        [label, format_indian(Decimal(tds[key]), "full") if money else str(tds[key])]
        for label, key, money in TDS_BLOCK_LINES
    ]
    table = Table(rows, colWidths=[90 * mm, 45 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFC3C7")),
        ("TEXTCOLOR", (1, 3), (1, 3), colors.HexColor(f"#{STATUS_TEXT['critical']}")),
    ]))  # fmt: skip
    block = [Paragraph("TDS summary", styles["h2"]), table]
    note = tds_block_note(tds)
    if note:
        block.append(Paragraph(escape(note), styles["small"]))
    block.append(Paragraph("Party-wise detail: the TDS Applicability report.", styles["small"]))
    return [KeepTogether(block)]


def client_report_pdf(
    report: ClientComparison,
    monthly: MonthlyComparison | None,
    unit: str = "auto",
    tds: dict | None = None,
) -> bytes:
    _register_fonts()
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm, title=_heading(report),
        author="Turnover Analysis & Alert Tool",
    )  # fmt: skip
    story = [
        Paragraph(escape(_heading(report)), styles["title"]),
        Paragraph(f"Client: <b>{escape(report.client_name)}</b>", styles["client"]),
        Paragraph(f"<b>{escape(generated_stamp())}</b>", styles["client"]),
        _comparison_table(report, unit, styles),
    ]
    for note in report.notes:
        story.append(Paragraph(f"ℹ {escape(note)}", styles["small"]))
    projections = [
        f"{r.label} ≈ {format_indian(r.annualised, unit)}"
        for r in report.rows
        if r.annualised is not None
    ]
    if projections:
        story.append(
            Paragraph(
                "Annualised projection at the current run-rate: " + "; ".join(projections),
                styles["small"],
            )
        )

    story.append(Paragraph("Absolute limit alerts", styles["h2"]))
    active = [x for x in report.limits if x.status != AbsoluteLimitStatus.BELOW]
    if not active:
        story.append(Paragraph("No absolute limit has been crossed or approached.", styles["cell"]))
    for limit in sorted(active, key=lambda x: x.status != AbsoluteLimitStatus.CROSSED):
        tone = "critical" if limit.status == AbsoluteLimitStatus.CROSSED else "warning"
        story.append(
            Paragraph(
                f'<font color="#{STATUS_TEXT[tone]}">●</font> {escape(limit_message(limit))}',
                styles["cell"],
            )
        )
    story.append(
        Paragraph(
            "Limits are editable defaults - verify current limits before relying on them.",
            styles["small"],
        )
    )
    if tds is not None and tds["has_data"]:
        story += _tds_pdf_block(tds, styles)

    if monthly is not None and monthly.has_data:
        story += _month_blocks_pdf(monthly, report, styles)
    doc.build(story, onFirstPage=_pdf_footer, onLaterPages=_pdf_footer)
    return buffer.getvalue()
