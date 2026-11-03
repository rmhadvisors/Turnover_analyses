"""Excel / PDF exports and the month-wise report data."""

import io
import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook

from app.services import report_export
from app.services.calculations import round2
from app.services.comparison_service import ClientComparison, MonthlyComparison, MonthlySeries
from app.utils.indian_format import CRORE, LAKH
from tests.test_api import enter_worked_example, make_client, upload


def download(api, path, **params):
    response = api.get(path, params=params)
    assert response.status_code == 200, response.text
    return response


def test_excel_report_has_real_numbers_and_formats(api) -> None:
    client_id = make_client(api, "Acme Traders")
    enter_worked_example(api, client_id)
    response = download(api, f"/reports/export/{client_id}", fy="2025-26", unit="lakhs")
    assert "spreadsheetml" in response.headers["content-type"]
    assert (
        "Turnover_Comparison_Acme_Traders_FY2025-26.xlsx" in response.headers["content-disposition"]
    )

    sheet = load_workbook(io.BytesIO(response.content))["Comparison"]
    assert sheet["A1"].value == "TURNOVER ANALYSIS — FY 2024-25 vs FY 2025-26"
    assert sheet["A2"].value == "Client: Acme Traders"
    assert [c.value for c in sheet[5]][:7] == [
        "Particular", "Unit", "FY 2024-25", "FY 2025-26", "Difference", "Change %", "Status",
    ]  # fmt: skip

    turnover = {c.column_letter: c for c in sheet[6]}
    assert turnover["B"].value == "₹ L"  # every figure in the row is in that unit
    assert turnover["C"].value == 80 and turnover["D"].value == 100  # numbers, in lakhs
    assert turnover["E"].value == 20
    assert isinstance(turnover["C"].value, (int, float))  # not text
    assert "₹" in turnover["C"].number_format and '" L' in turnover["C"].number_format
    assert "↑" in turnover["E"].number_format and "↓" in turnover["E"].number_format
    assert turnover["F"].value == 0.25 and turnover["F"].number_format == "0.00%"
    assert turnover["G"].value == "Significant Increase"

    net_profit = {c.column_letter: c for c in sheet[9]}
    assert net_profit["A"].value == "Net Profit"
    assert net_profit["E"].value == -2 and abs(net_profit["F"].value - (-0.2857)) < 0.00005
    assert net_profit["F"].font.color.rgb.endswith("B42318")  # red: down
    assert net_profit["G"].value == "Significant Decrease"

    purchases = {c.column_letter: c for c in sheet[7]}
    assert purchases["G"].value == "New / No comparison" and purchases["F"].value == "—"

    text = " ".join(str(c.value) for row in sheet.iter_rows() for c in row if c.value)
    assert "GST registration - goods" in text  # limit alert listed below the table


def test_excel_units_and_full_indian_format(api) -> None:
    client_id = make_client(api)
    enter_worked_example(api, client_id)

    def turnover_row(unit):
        content = download(api, f"/reports/export/{client_id}", fy="2025-26", unit=unit).content
        return {c.column_letter: c for c in load_workbook(io.BytesIO(content))["Comparison"][6]}

    crores = turnover_row("crores")
    assert crores["B"].value == "₹ Cr"
    assert crores["D"].value == 1 and '" Cr' in crores["D"].number_format
    full = turnover_row("full")
    assert full["B"].value == "₹" and full["D"].value == 10000000
    assert "##\\,##\\,##" in full["D"].number_format  # Indian grouping format, still numeric
    auto = turnover_row("auto")
    # 1 crore switches the whole row to Cr, so the Unit cell holds for both years
    assert auto["B"].value == "₹ Cr" and auto["C"].value == 0.8 and auto["D"].value == 1


def test_export_is_built_fresh_and_stamped_in_ist(api, sample_dir) -> None:
    client_id = make_client(api, "Sharma Traders")
    upload(api, sample_dir / "sharma_traders_sales_register.xlsx", client_id, "sales_register")
    before = datetime.now(report_export.IST).replace(second=0, microsecond=0)
    response = download(api, f"/reports/export/{client_id}", fy="2025-26")
    after = datetime.now(report_export.IST)
    assert response.headers["cache-control"] == "no-store"
    workbook = load_workbook(io.BytesIO(response.content))
    stamps = {workbook["Comparison"]["A3"].value, workbook["Month-wise"]["A3"].value}
    assert len(stamps) == 1  # one build time on every sheet
    (stamp,) = stamps
    built = datetime.strptime(stamp, "Generated %d-%b-%Y %H:%M IST").replace(
        tzinfo=report_export.IST
    )
    assert before <= built <= after  # the time of this request, in India time
    assert workbook["Comparison"]["A4"].value == "Amounts in lakhs / crores."
    pdf = download(api, f"/reports/export/{client_id}", fy="2025-26", format="pdf")
    assert pdf.headers["cache-control"] == "no-store"


def test_export_rejects_bad_parameters(api) -> None:
    client_id = make_client(api)
    assert api.get(f"/reports/export/{client_id}", params={"fy": "bad"}).status_code == 422
    good = {"fy": "2025-26"}
    assert (
        api.get(f"/reports/export/{client_id}", params={**good, "unit": "yards"}).status_code == 422
    )
    assert (
        api.get(f"/reports/export/{client_id}", params={**good, "format": "doc"}).status_code == 422
    )
    assert api.get("/reports/export/999", params=good).status_code == 404


def test_pdf_report_is_a_valid_document(api, sample_dir) -> None:
    client_id = make_client(api, "Sharma Traders")
    upload(api, sample_dir / "sharma_traders_sales_register.xlsx", client_id, "sales_register")
    upload(
        api, sample_dir / "sharma_traders_purchase_register.xlsx", client_id, "purchase_register"
    )
    response = download(api, f"/reports/export/{client_id}", fy="2025-26", format="pdf")
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response.content.rstrip().endswith(b"%%EOF")
    assert len(re.findall(rb"/Type\s*/Page\b", response.content)) >= 1
    assert b"DejaVu" in response.content  # embedded font that has the rupee sign


def test_pdf_and_excel_work_without_any_vouchers(api) -> None:
    client_id = make_client(api)
    enter_worked_example(api, client_id)  # manual entry only: no month-wise data
    assert (
        download(api, f"/reports/export/{client_id}", fy="2025-26", format="pdf").content[:4]
        == b"%PDF"
    )
    content = download(api, f"/reports/export/{client_id}", fy="2025-26").content
    assert load_workbook(io.BytesIO(content)).sheetnames == ["Comparison"]


def test_monthly_data_and_workbook_chart(api, sample_dir) -> None:
    client_id = make_client(api, "Sharma Traders")
    upload(api, sample_dir / "sharma_traders_sales_register.xlsx", client_id, "sales_register")
    upload(
        api, sample_dir / "sharma_traders_purchase_register.xlsx", client_id, "purchase_register"
    )

    monthly = download(api, f"/reports/monthly/{client_id}", fy="2025-26").json()
    assert monthly["has_data"] is True
    assert monthly["months"] == [
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
        "Jan",
        "Feb",
        "Mar",
    ]
    assert monthly["current"]["fy"] == "2025-26" and monthly["previous"]["fy"] == "2024-25"
    # month-wise totals add up to the FY turnover / purchases held in the report
    figures = {f["fy"]: f for f in api.get(f"/entries/{client_id}").json()}
    assert sum(Decimal(v) for v in monthly["current"]["sales"]) == Decimal(
        figures["2025-26"]["turnover"]
    )
    assert sum(Decimal(v) for v in monthly["previous"]["purchases"]) == Decimal(
        figures["2024-25"]["purchases"]
    )

    content = download(api, f"/reports/export/{client_id}", fy="2025-26").content
    workbook = load_workbook(io.BytesIO(content))
    assert workbook.sheetnames == ["Comparison", "Month-wise"]
    assert len(workbook["Month-wise"]._charts) == 2  # current and previous FY
    assert isinstance(workbook["Month-wise"]["B9"].value, float)


UNIT_SCALE = {"₹": Decimal(1), "₹ L": LAKH, "₹ Cr": CRORE}


def rupees(value: float, scale: Decimal) -> Decimal:
    """A stored (scaled) cell value back in rupees, to the paisa."""
    return round2(Decimal(repr(value)) * scale)


def block_top(ws, metric: str) -> int:
    """Row of a month-wise block's bold title ("SALES (₹ lakhs)"): header +1, Apr +2, Total +14."""
    return next(
        r
        for r in range(1, ws.max_row + 1)
        if str(ws.cell(row=r, column=1).value).startswith(metric)
    )


def month_wise_totals_match_comparison(workbook) -> dict[str, tuple[Decimal, Decimal]]:
    """Assert each Month-wise Total row equals the Comparison sheet's figures, to the paisa."""
    comparison, monthly = workbook["Comparison"], workbook["Month-wise"]
    totals = {}
    for r, label, block in ((6, "Turnover", "SALES"), (7, "Purchases", "PURCHASES")):
        assert comparison.cell(row=r, column=1).value == label
        total = block_top(monthly, block) + 14
        assert str(monthly.cell(row=total, column=1).value).startswith("Total")
        scale = UNIT_SCALE[comparison.cell(row=r, column=2).value]
        annual = tuple(rupees(comparison.cell(row=r, column=c).value, scale) for c in (3, 4))
        totals[label] = tuple(rupees(monthly.cell(row=total, column=c).value, LAKH) for c in (2, 3))
        assert totals[label] == annual, label
        difference = rupees(comparison.cell(row=r, column=5).value, scale)
        assert rupees(monthly.cell(row=total, column=4).value, LAKH) == difference, label
        assert monthly.cell(row=total, column=5).value == comparison.cell(row=r, column=6).value
    return totals


@pytest.mark.parametrize(
    ("name", "slug", "ext"),
    [
        ("Sharma Traders", "sharma_traders", "xlsx"),
        ("Patel Engineering Works", "patel_engineering_works", "csv"),
        ("Iyer Consulting LLP", "iyer_consulting_llp", "xlsx"),
    ],
)
@pytest.mark.parametrize("unit", ["auto", "lakhs", "crores", "full"])
def test_month_wise_totals_equal_comparison_sheet(api, sample_dir, name, slug, ext, unit) -> None:
    client_id = make_client(api, name)
    for kind in ("sales", "purchase"):
        upload(api, sample_dir / f"{slug}_{kind}_register.{ext}", client_id, f"{kind}_register")
    content = download(api, f"/reports/export/{client_id}", fy="2025-26", unit=unit).content
    totals = month_wise_totals_match_comparison(load_workbook(io.BytesIO(content)))
    figures = {f["fy"]: f for f in api.get(f"/entries/{client_id}").json()}
    for i, fy in enumerate(("2024-25", "2025-26")):
        assert totals["Turnover"][i] == Decimal(figures[fy]["turnover"])
        assert totals["Purchases"][i] == Decimal(figures[fy]["purchases"])


MONTHS = ("Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar")


def monthly_comparison(prev_sales, cur_sales, prev_purchases, cur_purchases, active=12):
    def series(fy, sales, purchases):
        pad = ["0"] * (12 - len(sales))
        return MonthlySeries(
            fy, [Decimal(v) for v in [*sales, *pad]], [Decimal(v) for v in [*purchases, *pad]]
        )

    return MonthlyComparison(
        client_id=1,
        client_name="X",
        months=MONTHS,
        has_data=True,
        previous=series("2024-25", prev_sales, prev_purchases),
        current=series("2025-26", cur_sales, cur_purchases),
        active_months=active,
    )


def stub_report() -> ClientComparison:
    return ClientComparison(
        client_id=1,
        client_name="X",
        fy="2025-26",
        previous_fy="2024-25",
        is_ytd=False,
        is_period_matched=False,
        period_label=None,
        rows=[],
        limits=[],
    )


def test_month_wise_two_stacked_blocks_with_change_pct() -> None:
    zeros = ["0"] * 10
    monthly = monthly_comparison(
        ["100000", "0", *zeros],
        ["150000", "70000", *zeros],
        ["200000", "50000", *zeros],
        ["100000", "50000", *zeros],
    )
    workbook = Workbook()
    report_export._monthly_sheet(workbook, monthly, stub_report(), "Generated now")
    ws = workbook["Month-wise"]
    assert ws["A1"].value == "TURNOVER ANALYSIS — FY 2024-25 vs FY 2025-26"
    assert ws["A2"].value == "Client: X" and ws["A3"].value == "Generated now"
    sales, purchases = block_top(ws, "SALES"), block_top(ws, "PURCHASES")
    assert (sales, purchases) == (7, 25)  # one above the other, blank rows between
    assert ws.cell(row=sales, column=1).value == "SALES (₹ lakhs)"
    assert ws.cell(row=sales, column=1).font.b and ws.cell(row=purchases, column=1).font.b
    for top in (sales, purchases):
        assert [c.value for c in ws[top + 1]][:5] == [
            "Month", "FY 2024-25", "FY 2025-26", "Difference", "Change %",
        ]  # fmt: skip
        assert ws.cell(row=top + 2, column=1).value == "Apr"
        assert ws.cell(row=top + 13, column=1).value == "Mar"
        assert ws.cell(row=top + 14, column=1).value == "Total"
        assert ws.cell(row=top + 15, column=1).value is None  # complete year: no extra row

    def cell(row, col):
        return ws.cell(row=row, column=col)

    apr = sales + 2
    assert cell(apr, 4).value == 0.5 and "↑" in cell(apr, 4).number_format
    assert cell(apr, 4).font.color.rgb.endswith("137333")
    assert cell(apr, 5).value == 0.5 and cell(apr, 5).number_format == "0.00%"
    assert cell(apr, 5).font.color.rgb.endswith("137333")  # green: up
    assert cell(apr + 1, 4).value == 0.7 and cell(apr + 1, 5).value == "—"  # no baseline: no %
    p_apr = purchases + 2
    assert cell(p_apr, 4).value == -1 and cell(p_apr, 5).value == -0.5
    assert cell(p_apr, 5).font.color.rgb.endswith("B42318")  # red: down
    assert cell(p_apr + 1, 4).value == 0 and cell(p_apr + 1, 4).font.color is None
    assert [cell(sales + 14, c).value for c in (2, 3, 4, 5)] == [1, 2.2, 1.2, 1.2]
    assert [cell(purchases + 14, c).value for c in (2, 3, 4, 5)] == [2.5, 1.5, -1, -0.4]
    assert all(cell(sales + 14, c).font.b for c in range(1, 6))


def test_month_wise_charts_are_grouped_by_metric() -> None:
    monthly = monthly_comparison(["100000"], ["200000"], ["300000"], ["400000"])
    workbook = Workbook()
    report_export._monthly_sheet(workbook, monthly, stub_report(), "Generated now")
    sales, purchases = workbook["Month-wise"]._charts
    names = [[s.tx.v for s in chart.series] for chart in (sales, purchases)]
    assert names == [
        ["Sales FY 2024-25", "Sales FY 2025-26"],
        ["Purchases FY 2024-25", "Purchases FY 2025-26"],
    ]
    # each chart sits beside its own table block
    assert (sales.anchor, purchases.anchor) == ("G7", "G25")


def test_incomplete_year_total_is_same_period_and_future_months_blank() -> None:
    monthly = monthly_comparison(
        ["100000"] * 12, ["300000"] * 5, ["50000"] * 12, ["80000"] * 5, active=5
    )
    monthly.data_through = date(2025, 8, 31)
    workbook = Workbook()
    report_export._monthly_sheet(workbook, monthly, stub_report(), "Generated now")
    ws = workbook["Month-wise"]

    def cell(row, col):
        return ws.cell(row=row, column=col)

    assert ws["A5"].value == "FY 2025-26 in progress — data to 31-Aug-2025."
    top = block_top(ws, "SALES")
    sep = top + 7  # September: has not happened yet
    assert cell(sep, 1).value == "Sep" and cell(sep, 2).value == 1  # last year's still shown
    assert [cell(sep, c).value for c in (3, 4, 5)] == ["—", "—", "—"]  # not 0, not -100%
    assert cell(sep, 1).fill.fgColor.rgb.endswith("F1F3F4")
    aug = top + 6
    assert cell(aug, 3).value == 3 and cell(aug, 5).value == 2.0
    total = top + 14
    assert cell(total, 1).value == "Total (Apr–Aug, same period both years)"
    assert [cell(total, c).value for c in (2, 3, 4, 5)] == [5, 15, 10, 2.0]  # +200%
    full = top + 15
    assert cell(full, 1).value == "Previous year full total (Apr–Mar)" and cell(full, 2).value == 12
    assert all(cell(full, c).value == "—" for c in (3, 4, 5))


def in_progress_client(api, name="Progress Traders") -> int:
    """FY 2025-26 for all 12 months, FY 2026-27 only up to 31-Aug-2026."""
    client_id = make_client(api, name)
    for kind, label, base in (("sales", "Sales", 100000), ("purchase", "Purchase", 60000)):
        rows = ["Date,Particulars,Voucher Type,Voucher No.,Value,Gross Total"]
        for i in range(12):  # FY 2025-26, one voucher a month
            month, year = (4 + i, 2025) if i < 9 else (i - 8, 2026)
            amount = base + 1000 * i
            rows.append(f"15-{month:02d}-{year},P,{label},{label[0]}-{i},{amount},{amount}")
        for i in range(5):  # FY 2026-27, Apr..Aug, the last voucher on 31-Aug
            day = 31 if i == 4 else 10
            amount = base * 3 + 777 * i
            rows.append(f"{day:02d}-{4 + i:02d}-2026,P,{label},{label[0]}-n{i},{amount},{amount}")
        response = api.post(
            "/imports/confirm",
            data={"client_id": client_id, "report_type": f"{kind}_register"},
            files={"file": (f"{kind}.csv", "\n".join(rows).encode())},
        )
        assert response.status_code == 200, response.text
    return client_id


AS_OF = "2026-11-03"  # three months after the data stops
HEADING = "TURNOVER ANALYSIS — FY 2025-26 vs FY 2026-27"
DATA_NOTE = "FY 2026-27 in progress — data to 31-Aug-2026."


def test_in_progress_export_has_one_headline_number_and_the_real_data_date(api) -> None:
    client_id = in_progress_client(api)
    for unit in ("auto", "lakhs", "crores", "full"):
        content = download(
            api, f"/reports/export/{client_id}", fy="2026-27", unit=unit, as_of=AS_OF
        ).content
        workbook = load_workbook(io.BytesIO(content))
        totals = month_wise_totals_match_comparison(workbook)  # to the paisa, in every unit
    # Apr-Aug of both years: not 12 months vs 5, and not Apr-Nov
    assert totals["Turnover"] == (
        Decimal(sum(100000 + 1000 * i for i in range(5))),
        Decimal(sum(300000 + 777 * i for i in range(5))),
    )
    assert workbook["Comparison"]["A1"].value == HEADING
    assert workbook["Month-wise"]["A1"].value == HEADING
    text = [str(c.value) for sheet in workbook for row in sheet.iter_rows() for c in row if c.value]
    assert DATA_NOTE in text and workbook["Month-wise"]["A5"].value == DATA_NOTE
    assert not any("YTD" in t or "Apr-Nov" in t or "Apr–Nov" in t for t in text)
    monthly = workbook["Month-wise"]
    top = block_top(monthly, "SALES")
    assert monthly.cell(row=top + 14, column=1).value == "Total (Apr–Aug, same period both years)"
    assert monthly.cell(row=top + 15, column=1).value == "Previous year full total (Apr–Mar)"
    purchases = block_top(monthly, "PURCHASES")
    assert monthly.cell(row=purchases + 7, column=3).value == "—"  # Sep 2026: not yet
    assert monthly.cell(row=purchases + 7, column=5).value == "—"


def test_in_progress_pdf_has_the_tables_and_nothing_is_cut_off(api) -> None:
    import fitz

    client_id = in_progress_client(api)
    pdf = download(
        api, f"/reports/export/{client_id}", fy="2026-27", format="pdf", as_of=AS_OF
    ).content
    document = fitz.open(stream=pdf, filetype="pdf")
    text = "\n".join(page.get_text() for page in document)
    assert HEADING in text and DATA_NOTE in text
    assert "YTD" not in text and "Apr-Nov" not in text
    for expected in (
        "Particular",
        "Turnover",
        "SALES (₹ lakhs)",
        "PURCHASES (₹ lakhs)",
        "Total (Apr–Aug, same period both years)",
        "Previous year full total (Apr–Mar)",
    ):
        assert expected in text, expected
    assert text.index("SALES (₹ lakhs)") < text.index("PURCHASES (₹ lakhs)")
    for page in document:  # every word sits inside the page, clear of the edge
        width, height = page.rect.width, page.rect.height
        for x0, y0, x1, y1, *_ in page.get_text("words"):
            assert 10 <= x0 and x1 <= width - 10 and 10 <= y0 and y1 <= height - 5, (x0, x1, y1)


def test_monthly_without_vouchers_is_empty(api) -> None:
    client_id = make_client(api)
    enter_worked_example(api, client_id)
    monthly = download(api, f"/reports/monthly/{client_id}", fy="2025-26").json()
    assert monthly["has_data"] is False
    assert set(monthly["current"]["sales"]) == {"0"}


def test_summary_export_sorted_by_biggest_mover(api, sample_dir) -> None:
    ids = {}
    for name, slug, ext in (
        ("Sharma Traders", "sharma_traders", "xlsx"),
        ("Patel Engineering Works", "patel_engineering_works", "csv"),
        ("Iyer Consulting LLP", "iyer_consulting_llp", "xlsx"),
    ):
        ids[name] = make_client(api, name)
        upload(api, sample_dir / f"{slug}_sales_register.{ext}", ids[name], "sales_register")
    make_client(api, "No Data Yet")

    content = download(api, "/reports/summary/export", fy="2025-26", unit="lakhs").content
    sheet = load_workbook(io.BytesIO(content))["Summary"]
    assert sheet.auto_filter.ref == "A4:L8"  # incl. the TDS columns
    names = [sheet.cell(row=r, column=1).value for r in range(5, 9)]
    assert names == [
        "Patel Engineering Works",
        "Sharma Traders",
        "Iyer Consulting LLP",
        "No Data Yet",
    ]
    patel = {c.column_letter: c for c in sheet[5]}
    assert patel["D"].number_format == "0.00%" and patel["D"].value < -0.3
    assert patel["E"].value == "Significant Decrease"
    assert isinstance(patel["C"].value, float)
    assert sheet["D8"].value == "—"  # no comparison sorts last


def test_sales_and_purchases_loaded_to_different_dates_use_the_earlier(api) -> None:
    """Sales end 31-Aug but purchases run to 30-Sep: compare Apr-Aug for both, and say so."""
    client_id = in_progress_client(api)
    rows = [
        "Date,Particulars,Voucher Type,Voucher No.,Value,Gross Total",
        "30-09-2026,P,Purchase,P-sep,500000,500000",
    ]
    response = api.post(
        "/imports/confirm",
        data={"client_id": client_id, "report_type": "purchase_register"},
        files={"file": ("p.csv", "\n".join(rows).encode())},
    )
    assert response.status_code == 200, response.text
    content = download(api, f"/reports/export/{client_id}", fy="2026-27", as_of=AS_OF).content
    workbook = load_workbook(io.BytesIO(content))
    month_wise_totals_match_comparison(workbook)  # Apr-Aug on both sheets, Sep purchase excluded
    note = "Vouchers are also recorded through Sep-2026"
    assert any(str(c.value).startswith(note) for row in workbook["Month-wise"]["A"] for c in [row])
    sheet = workbook["Month-wise"]
    sep_purchases = block_top(sheet, "PURCHASES") + 7
    assert sheet.cell(row=sep_purchases, column=3).value == "—"
