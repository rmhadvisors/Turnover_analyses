from decimal import Decimal

from app.utils.indian_format import (
    format_crores,
    format_indian,
    format_indian_commas,
    format_lakhs,
)


def test_format_indian_commas_one_crore() -> None:
    assert format_indian_commas(Decimal(10000000)) == "₹1,00,00,000"


def test_format_indian_commas_small_value() -> None:
    assert format_indian_commas(Decimal(500)) == "₹500"


def test_format_indian_commas_thousands() -> None:
    assert format_indian_commas(Decimal(120000)) == "₹1,20,000"


def test_format_indian_commas_negative() -> None:
    assert format_indian_commas(Decimal(-50000)) == "-₹50,000"


def test_format_indian_commas_with_paise() -> None:
    assert format_indian_commas(Decimal("1234.5")) == "₹1,234.50"


def test_format_lakhs() -> None:
    assert format_lakhs(Decimal(8000000)) == "₹80.00 L"


def test_format_crores() -> None:
    assert format_crores(Decimal(10000000)) == "₹1.00 Cr"


def test_format_indian_auto_picks_crores_above_one_crore() -> None:
    assert format_indian(Decimal(15000000), unit="auto") == "₹1.50 Cr"


def test_format_indian_auto_picks_lakhs_below_one_crore() -> None:
    assert format_indian(Decimal(8000000), unit="auto") == "₹80.00 L"
