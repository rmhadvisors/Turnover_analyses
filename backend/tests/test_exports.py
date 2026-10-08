"""Excel / PDF exports and the month-wise report data."""

import io
import re
from decimal import Decimal

from openpyxl import load_workbook

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
    assert sheet["A1"].value == "TURNOVER COMPARISON – FY 2025-26"
    assert sheet["A2"].value == "Client: Acme Traders"
    assert [c.value for c in sheet[5]][:6] == [
        "Particular", "FY 2024-25", "FY 2025-26", "Difference", "Change %", "Status",
    ]  # fmt: skip

    turnover = {c.column_letter: c for c in sheet[6]}
    assert turnover["B"].value == 80 and turnover["C"].value == 100  # numbers, in lakhs
    assert turnover["D"].value == 20
    assert isinstance(turnover["B"].value, (int, float))  # not text
    assert "₹" in turnover["B"].number_format and '" L' in turnover["B"].number_format
    assert "↑" in turnover["D"].number_format and "↓" in turnover["D"].number_format
    assert turnover["E"].value == 0.25 and turnover["E"].number_format == "0.00%"
    assert turnover["F"].value == "Significant Increase"

    net_profit = {c.column_letter: c for c in sheet[9]}
    assert net_profit["A"].value == "Net Profit"
    assert net_profit["D"].value == -2 and abs(net_profit["E"].value - (-0.2857)) < 0.00005
    assert net_profit["F"].value == "Significant Decrease"

    purchases = {c.column_letter: c for c in sheet[7]}
    assert purchases["F"].value == "New / No comparison" and purchases["E"].value == "—"

    text = " ".join(str(c.value) for row in sheet.iter_rows() for c in row if c.value)
    assert "GST registration - goods" in text  # limit alert listed below the table


def test_excel_units_and_full_indian_format(api) -> None:
    client_id = make_client(api)
    enter_worked_example(api, client_id)

    def turnover_row(unit):
        content = download(api, f"/reports/export/{client_id}", fy="2025-26", unit=unit).content
        return {c.column_letter: c for c in load_workbook(io.BytesIO(content))["Comparison"][6]}

    crores = turnover_row("crores")
    assert crores["C"].value == 1 and '" Cr' in crores["C"].number_format
    full = turnover_row("full")
    assert full["C"].value == 10000000
    assert "##\\,##\\,##" in full["C"].number_format  # Indian grouping format, still numeric
    auto = turnover_row("auto")
    assert auto["B"].value == 80 and auto["C"].value == 1  # 1 crore switches to Cr


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
    assert isinstance(workbook["Month-wise"]["B5"].value, float)


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
