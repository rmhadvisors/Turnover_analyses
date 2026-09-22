"""Excel and PDF exports of the client comparison report and the all-clients summary.

Excel cells hold real numbers with number formats (never text), so the CA can
sort, sum and re-format them. Indian digit grouping / lakh / crore display is done
with number formats; for the lakh and crore units the stored number is scaled.
"""

from __future__ import annotations

import io
import math
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
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
from app.services.comparison_service import (
    ClientComparison,
    MonthlyComparison,
    MonthlySeries,
)
from app.services.report_builder import NO_COMPARISON_LABEL, limit_message
from app.utils.indian_format import CRORE, LAKH, format_indian

UNITS = ("auto", "lakhs", "crores", "full")
SIGN_FLAGS = {"turned_to_loss": "Turned to Loss", "turned_to_profit": "Turned to Profit"}

SALES_COLOUR = "#2a78d6"  # categorical slot 1 (blue), validated palette
PURCHASE_COLOUR = "#eb6834"  # categorical slot 2 (orange)
STATUS_TEXT = {"critical": "B42318", "warning": "A15C07", "ok": "137333", "none": "5F6368"}
STATUS_FILL = {"critical": "FDE7E7", "warning": "FFF1CC", "ok": "E3F4E7", "none": "F1F3F4"}

RUPEE = "₹"
FMT_INDIAN_FULL = f'[>=10000000]"{RUPEE}"##\\,##\\,##\\,##0.00;[>=100000]"{RUPEE}"##\\,##\\,##0.00;"{RUPEE}"#,##0.00'


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


def _comparison_sheet(ws, report: ClientComparison, unit: str) -> None:
    title = f"TURNOVER COMPARISON – FY {report.fy}"
    if report.is_ytd:
        title += f" (YTD {report.period_label})"
    elif report.is_period_matched:
        title += f" (Comparison for {report.period_label})"
    _text(ws, "A1", title, Font(bold=True, size=14))
    _text(ws, "A2", f"Client: {report.client_name}", Font(bold=True))
    unit_note = {"auto": "lakhs / crores", "lakhs": "lakhs", "crores": "crores", "full": "rupees"}
    ws["A3"] = f"Amounts in {unit_note[unit]}. Generated {datetime.now():%d-%b-%Y %H:%M}."
    ws["A3"].font = Font(italic=True, color="5F6368")

    _header_row(
        ws, 5, ["Particular", f"FY {report.previous_fy}", f"FY {report.fy}", "Difference",
                "Change %", "Status", "Flag"]
    )  # fmt: skip
    for offset, row in enumerate(report.rows):
        r = 6 + offset
        difference = row.comparison.difference if row.comparison else None
        pct = row.comparison.change_pct if row.comparison else None
        ws.cell(row=r, column=1, value=row.label).font = Font(bold=True)
        _put_money(ws, r, 2, row.previous, unit)
        _put_money(ws, r, 3, row.current, unit)
        diff_cell = _put_money(ws, r, 4, difference, unit, arrows=True)
        pct_cell = ws.cell(row=r, column=5)
        if pct is not None:
            pct_cell.value = float(pct / 100)
            pct_cell.number_format = "0.00%"
        else:
            pct_cell.value = "—"
        pct_cell.alignment, pct_cell.border = Alignment(horizontal="right"), _BOX
        label = BAND_DISPLAY[row.band][1] if row.band else NO_COMPARISON_LABEL
        tone = _tone(row.band.value if row.band else None)
        status = ws.cell(row=r, column=6, value=label)
        status.font = Font(bold=True, color=STATUS_TEXT[tone])
        status.fill = PatternFill("solid", fgColor=STATUS_FILL[tone])
        flag = SIGN_FLAGS.get(row.comparison.sign_change) if row.comparison else None
        ws.cell(row=r, column=7, value=flag or "")
        for col in (1, 6, 7):
            ws.cell(row=r, column=col).border = _BOX
        if difference is not None and difference != 0:
            colour = "137333" if difference > 0 else "B42318"
            diff_cell.font = pct_cell.font = Font(color=colour)

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

    for col, width in zip("ABCDEFG", (18, 16, 16, 18, 12, 26, 18), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A6"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _monthly_sheet(wb: Workbook, monthly: MonthlyComparison, fy: str, previous_fy: str) -> None:
    ws = wb.create_sheet("Month-wise")
    _text(ws, "A1", "Month-wise sales vs purchases (₹ lakhs)", Font(bold=True, size=13))
    ws["A2"] = "Net of credit / debit notes. Built from imported vouchers."
    ws["A2"].font = Font(italic=True, color="5F6368")
    _header_row(ws, 4, ["Month", f"Sales FY {fy}", f"Purchases FY {fy}",
                        f"Sales FY {previous_fy}", f"Purchases FY {previous_fy}"])  # fmt: skip
    for i, month in enumerate(monthly.months):
        r = 5 + i
        ws.cell(row=r, column=1, value=month).border = _BOX
        for col, series in ((2, monthly.current.sales), (3, monthly.current.purchases),
                            (4, monthly.previous.sales), (5, monthly.previous.purchases)):  # fmt: skip
            cell = ws.cell(row=r, column=col, value=float(series[i] / LAKH))
            cell.number_format = f'"{RUPEE}"#,##0.00" L"'
            cell.border = _BOX
    total_row = 5 + len(monthly.months)
    ws.cell(row=total_row, column=1, value="Total").font = Font(bold=True)
    for col in range(2, 6):
        letter = get_column_letter(col)
        cell = ws.cell(row=total_row, column=col, value=f"=SUM({letter}5:{letter}{total_row - 1})")
        cell.number_format = f'"{RUPEE}"#,##0.00" L"'
        cell.font = Font(bold=True)
    for col in "ABCDE":
        ws.column_dimensions[col].width = 20

    for anchor, first_col, title in (("G4", 2, f"FY {fy}"), ("G22", 4, f"FY {previous_fy}")):
        chart = BarChart()
        chart.type, chart.grouping = "col", "clustered"
        chart.title = f"Sales vs purchases – {title} (₹ lakhs)"
        chart.y_axis.title = "₹ lakhs"
        data = Reference(
            ws, min_col=first_col, max_col=first_col + 1, min_row=4, max_row=total_row - 1
        )
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=5, max_row=total_row - 1))
        chart.series[0].graphicalProperties.solidFill = SALES_COLOUR.lstrip("#")
        chart.series[1].graphicalProperties.solidFill = PURCHASE_COLOUR.lstrip("#")
        chart.height, chart.width = 8.5, 17
        ws.add_chart(chart, anchor)


def client_report_xlsx(
    report: ClientComparison, monthly: MonthlyComparison | None, unit: str = "auto"
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Comparison"
    _comparison_sheet(ws, report, unit)
    if monthly is not None and monthly.has_data:
        _monthly_sheet(wb, monthly, report.fy, report.previous_fy)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def summary_xlsx(rows: list[SummaryRow], fy: str, unit: str = "auto") -> bytes:
    """All-clients summary, biggest turnover movers first; autofilter for re-sorting."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    _text(ws, "A1", f"TURNOVER SUMMARY – FY {fy}", Font(bold=True, size=14))
    ws["A2"] = f"Sorted by size of turnover change. Generated {datetime.now():%d-%b-%Y %H:%M}."
    ws["A2"].font = Font(italic=True, color="5F6368")
    headers = ["Client", "Previous FY turnover", "Current FY turnover", "Change %", "Status",
               "Net profit flag", "Limits crossed", "Limits approaching", "Open alerts"]  # fmt: skip
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
    ws.auto_filter.ref = f"A4:I{4 + len(ordered)}"
    ws.freeze_panes = "B5"
    for col, width in zip("ABCDEFGHI", (30, 20, 20, 12, 26, 18, 14, 18, 12), strict=True):
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


def _nice_axis(monthly: MonthlyComparison) -> tuple[float, float]:
    """(max, step) for a value axis shared by both FY charts, so bars are comparable
    and the tallest bar never touches the top edge."""
    values = [
        float(v / LAKH)
        for series in (monthly.current, monthly.previous)
        for v in series.sales + series.purchases
    ]
    peak = max(values, default=0.0) or 1.0
    raw_step = peak / 5
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw_step)
    return step * math.ceil(peak / step), step


def _bar_drawing(
    series: MonthlySeries,
    months: tuple[str, ...],
    width: float,
    height: float,
    axis: tuple[float, float],
) -> Drawing:
    """Grouped bars (sales vs purchases) for one FY, in rupee lakhs, on a shared (max, step) axis."""
    drawing = Drawing(width, height)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 38, 26, width - 50, height - 62
    chart.data = [
        [float(v / LAKH) for v in series.sales],
        [float(v / LAKH) for v in series.purchases],
    ]
    chart.categoryAxis.categoryNames = list(months)
    chart.categoryAxis.labels.fontName = chart.valueAxis.labels.fontName = "DejaVu"
    chart.categoryAxis.labels.fontSize = chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax, chart.valueAxis.valueStep = axis
    chart.valueAxis.gridStrokeColor = colors.HexColor("#d9dcdf")
    chart.valueAxis.visibleGrid = True
    chart.bars[0].fillColor = colors.HexColor(SALES_COLOUR)
    chart.bars[1].fillColor = colors.HexColor(PURCHASE_COLOUR)
    chart.bars.strokeColor = None
    chart.groupSpacing, chart.barSpacing = 5, 1
    drawing.add(chart)
    drawing.add(String(width / 2, height - 12, f"FY {series.fy}", fontName="DejaVu-Bold",
                       fontSize=9, textAnchor="middle"))  # fmt: skip
    drawing.add(
        String(4, height - 30, "₹ lakhs", fontName="DejaVu", fontSize=7, fillColor=colors.grey)
    )
    return drawing


def _legend() -> Drawing:
    drawing = Drawing(240, 14)
    for x, colour, label in ((0, SALES_COLOUR, "Sales"), (70, PURCHASE_COLOUR, "Purchases")):
        drawing.add(Rect(x, 3, 9, 9, fillColor=colors.HexColor(colour), strokeColor=None))
        drawing.add(String(x + 14, 4, label, fontName="DejaVu", fontSize=8))
    return drawing


def _pdf_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("DejaVu", 7)
    canvas.setFillColor(colors.HexColor("#5f6368"))
    canvas.drawString(
        15 * mm,
        8 * mm,
        f"Generated {datetime.now():%d-%b-%Y %H:%M} · Turnover Analysis & Alert Tool",
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
    fills = []
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
        fills.append(
            (
                "BACKGROUND",
                (5, i),
                (5, i),
                colors.HexColor("#" + STATUS_FILL[_tone(row.band.value if row.band else None)]),
            )
        )
    table = Table(
        data, colWidths=[38 * mm, 36 * mm, 36 * mm, 36 * mm, 26 * mm, 84 * mm], repeatRows=1
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
        *fills,
    ]))  # fmt: skip
    return table


def client_report_pdf(
    report: ClientComparison, monthly: MonthlyComparison | None, unit: str = "auto"
) -> bytes:
    _register_fonts()
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm, title=f"Turnover Comparison FY {report.fy}",
        author="Turnover Analysis & Alert Tool",
    )  # fmt: skip
    title = f"TURNOVER COMPARISON – FY {report.fy}"
    if report.is_ytd:
        title += f" (YTD {report.period_label})"
    story = [
        Paragraph(escape(title), styles["title"]),
        Paragraph(f"Client: <b>{escape(report.client_name)}</b>", styles["client"]),
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

    if monthly is not None and monthly.has_data:
        axis = _nice_axis(monthly)
        width, height = 128 * mm, 62 * mm
        charts = Table(
            [
                [
                    _bar_drawing(monthly.current, monthly.months, width, height, axis),
                    _bar_drawing(monthly.previous, monthly.months, width, height, axis),
                ]
            ],
            colWidths=[width + 4 * mm, width + 4 * mm],
        )
        # keep the heading, legend and both charts together on one page
        story.append(
            KeepTogether(
                [
                    Paragraph("Month-wise sales vs purchases", styles["h2"]),
                    _legend(),
                    Spacer(1, 3),
                    charts,
                ]
            )
        )
    doc.build(story, onFirstPage=_pdf_footer, onLaterPages=_pdf_footer)
    return buffer.getvalue()
