from datetime import date
from decimal import Decimal

import pytest

from app.services import tally_importer as ti
from app.services.turnover import VoucherAmount, net_purchases, net_sales


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,20,000.00 Dr", Decimal("120000.00")),
        ("1,20,000.00 Cr", Decimal("120000.00")),
        ("1,00,00,000", Decimal(10000000)),
        ("(5,000.50)", Decimal("-5000.50")),
        ("-250", Decimal(-250)),
        ("₹ 1,234.5", Decimal("1234.5")),
        (118000.0, Decimal("118000.0")),
        (42, Decimal(42)),
        ("", None),
        ("Dr", None),
        ("abc", None),
        (None, None),
        ("NaN", None),
    ],
)
def test_parse_amount(raw, expected) -> None:
    assert ti.parse_amount(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("31-03-2025", date(2025, 3, 31)),
        ("01/04/2025", date(2025, 4, 1)),
        ("5-Apr-24", date(2024, 4, 5)),
        ("17-Apr-2024", date(2024, 4, 17)),
        ("2025-03-31", date(2025, 3, 31)),
        ("31-03-2025 00:00:00", date(2025, 3, 31)),
        (date(2025, 4, 1), date(2025, 4, 1)),
        (45748, date(2025, 4, 1)),  # Excel serial
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date(raw, expected) -> None:
    assert ti.parse_date(raw) == expected


TALLY_ROWS = [
    ["ABC Traders"],
    ["Sales Register"],
    ["1-Apr-2025 to 31-Mar-2026"],
    [],
    [
        "Date",
        "Particulars",
        "Voucher Type",
        "Voucher No.",
        "Value",
        "Output CGST",
        "Gross Total",
    ],
    [
        "01-04-2025",
        "Alpha",
        "Sales",
        "S-1",
        "1,00,000.00",
        "9,000.00",
        "1,18,000.00 Cr",
    ],
    ["15-05-2025", "Beta", "Sales", "S-2", "50,000.00", "4,500.00", "59,000.00 Cr"],
    [
        "20-05-2025",
        "Beta",
        "Credit Note",
        "CN-1",
        "(10,000.00)",
        "900.00",
        "11,800.00 Dr",
    ],
    [],
    ["Grand Total", "", "", "", "1,40,000.00", "", "1,66,000.00"],
]


def test_header_row_detected_below_company_and_period_rows() -> None:
    assert ti.detect_header_row(TALLY_ROWS) == 4


def test_header_row_not_found() -> None:
    with pytest.raises(ti.ImportFormatError):
        ti.detect_header_row([["hello"], ["world"]])


def test_suggested_mapping() -> None:
    mapping = ti.suggest_mapping(ti.header_labels(TALLY_ROWS[4]))
    assert mapping["date"] == "Date"
    assert mapping["party"] == "Particulars"
    assert mapping["voucher_no"] == "Voucher No."
    assert mapping["taxable_value"] == "Value"
    assert mapping["total_value"] == "Gross Total"
    assert mapping["tax_value"] == ["Output CGST"]


def test_parse_vouchers_skips_totals_and_deducts_credit_notes() -> None:
    mapping = ti.suggest_mapping(ti.header_labels(TALLY_ROWS[4]))
    result = ti.parse_vouchers(TALLY_ROWS, 4, mapping, ti.REPORT_SALES)
    assert len(result.vouchers) == 3
    assert result.totals_ignored == 1
    assert result.invalid == []
    assert result.period == (date(2025, 4, 1), date(2026, 3, 31))

    note = result.vouchers[2]
    assert note.voucher_type == "credit_note"
    assert note.taxable_value == Decimal("10000.00")  # stored positive, deducted on aggregation

    amounts = [
        VoucherAmount(v.voucher_type, v.voucher_date, v.voucher_no, v.taxable_value, v.total_value)
        for v in result.vouchers
    ]
    assert net_sales(amounts) == Decimal("140000.00")  # 1,00,000 + 50,000 - 10,000
    assert net_sales(amounts, include_gst=True) == Decimal("165200.00")


def test_purchase_register_debit_note_reduces_purchases() -> None:
    rows = [
        ["Date", "Particulars", "Voucher Type", "Voucher No.", "Value"],
        ["01-04-2025", "X", "Purchase", "P-1", "1,000.00"],
        ["02-04-2025", "X", "Debit Note", "DN-1", "200.00"],
    ]
    mapping = ti.suggest_mapping(ti.header_labels(rows[0]))
    result = ti.parse_vouchers(rows, 0, mapping, ti.REPORT_PURCHASE)
    amounts = [
        VoucherAmount(v.voucher_type, v.voucher_date, v.voucher_no, v.taxable_value, v.total_value)
        for v in result.vouchers
    ]
    assert net_purchases(amounts) == Decimal("800.00")


def test_rows_without_date_are_reported_not_imported() -> None:
    rows = [*TALLY_ROWS[:6], ["", "continued...", "", "", "5,000.00", "", ""]]
    mapping = ti.suggest_mapping(ti.header_labels(rows[4]))
    result = ti.parse_vouchers(rows, 4, mapping, ti.REPORT_SALES)
    assert len(result.vouchers) == 1
    assert len(result.invalid) == 1


def test_party_named_total_something_is_not_dropped() -> None:
    rows = [
        TALLY_ROWS[4],
        [
            "01-04-2025",
            "Total Traders Pvt Ltd",
            "Sales",
            "S-9",
            "100.00",
            "0",
            "100.00",
        ],
    ]
    mapping = ti.suggest_mapping(ti.header_labels(rows[0]))
    assert len(ti.parse_vouchers(rows, 0, mapping, ti.REPORT_SALES).vouchers) == 1


def test_mapped_column_missing_from_file() -> None:
    with pytest.raises(ti.ImportFormatError, match="Nope"):
        ti.parse_vouchers(
            TALLY_ROWS, 4, {"date": "Nope", "taxable_value": "Value"}, ti.REPORT_SALES
        )


def test_dedup_key_is_stable_and_sensitive() -> None:
    a = ti.make_dedup_key("sales", "S-1", date(2025, 4, 1), Decimal("100.00"))
    assert a == ti.make_dedup_key("sales", " s-1 ", date(2025, 4, 1), Decimal("100.0"))
    assert a != ti.make_dedup_key("sales", "S-1", date(2025, 4, 2), Decimal("100.00"))
    assert a != ti.make_dedup_key("sales", "S-1", date(2025, 4, 1), Decimal("100.01"))
    assert a != ti.make_dedup_key("credit_note", "S-1", date(2025, 4, 1), Decimal("100.00"))


def test_profit_loss_parsing_and_fy_from_period() -> None:
    rows = [
        ["ABC Traders"],
        ["Profit & Loss A/c"],
        ["1-Apr-2025 to 31-Mar-2026"],
        ["Gross Profit c/o", "16,00,000.00", "Gross Profit b/f", "99.00"],
        ["Nett Loss", "2,00,000.00 Dr"],
    ]
    figures = ti.parse_profit_loss(rows)
    assert figures.gross_profit == Decimal("1600000.00")
    assert figures.net_profit == Decimal("-200000.00")
    assert figures.fy == "2025-26"


def test_profit_loss_without_profit_lines_fails() -> None:
    with pytest.raises(ti.ImportFormatError):
        ti.parse_profit_loss([["Just", "text"]])


def test_unsupported_and_xml_files() -> None:
    with pytest.raises(ti.ImportFormatError, match="XML"):
        ti.read_table(b"<x/>", "export.xml")
    with pytest.raises(ti.ImportFormatError, match="Unsupported"):
        ti.read_table(b"", "notes.docx")


def test_sample_files_parse(sample_dir) -> None:
    for path, report in (
        ("sharma_traders_sales_register.xlsx", ti.REPORT_SALES),  # Indian-format text
        (
            "patel_engineering_works_sales_register.csv",
            ti.REPORT_SALES,
        ),  # d-MMM-yy, CSV
        (
            "iyer_consulting_llp_purchase_register.xlsx",
            ti.REPORT_PURCHASE,
        ),  # Dr/Cr, real dates
    ):
        rows = ti.read_table((sample_dir / path).read_bytes(), path)
        header = ti.detect_header_row(rows)
        assert header == 4
        mapping = ti.suggest_mapping(ti.header_labels(rows[header]))
        result = ti.parse_vouchers(rows, header, mapping, report)
        assert len(result.vouchers) > 80
        assert result.totals_ignored == 1  # the Grand Total row
        assert result.invalid == []
        assert any(v.voucher_type in ("credit_note", "debit_note") for v in result.vouchers)
        assert {v.fy for v in result.vouchers} == {"2024-25", "2025-26"}
