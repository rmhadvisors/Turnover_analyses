from decimal import Decimal

from app.services.calculations import annualise, compare_metric, compare_ytd


def test_worked_example_turnover() -> None:
    result = compare_metric(Decimal(8000000), Decimal(10000000))
    assert result.difference == Decimal("2000000.00")
    assert result.change_pct == Decimal("25.00")
    assert result.no_comparison is False
    assert result.sign_change is None


def test_worked_example_gross_profit() -> None:
    result = compare_metric(Decimal(1600000), Decimal(1800000))
    assert result.difference == Decimal("200000.00")
    assert result.change_pct == Decimal("12.50")


def test_worked_example_net_profit_decline() -> None:
    result = compare_metric(Decimal(700000), Decimal(500000))
    assert result.difference == Decimal("-200000.00")
    assert result.change_pct == Decimal("-28.57")


def test_previous_zero_is_no_comparison() -> None:
    result = compare_metric(Decimal(0), Decimal(500000))
    assert result.no_comparison is True
    assert result.change_pct is None
    assert result.sign_change == "turned_to_profit"


def test_previous_missing_is_no_comparison() -> None:
    result = compare_metric(None, Decimal(500000))
    assert result.no_comparison is True
    assert result.change_pct is None
    assert result.difference is None


def test_profit_turned_to_loss() -> None:
    result = compare_metric(Decimal(500000), Decimal(-300000))
    assert result.sign_change == "turned_to_loss"
    assert result.difference == Decimal("-800000.00")
    assert result.change_pct == Decimal("-160.00")


def test_loss_turned_to_profit() -> None:
    result = compare_metric(Decimal(-500000), Decimal(300000))
    assert result.sign_change == "turned_to_profit"
    assert result.difference == Decimal("800000.00")
    assert result.change_pct == Decimal("160.00")


def test_negative_to_negative_no_sign_change() -> None:
    result = compare_metric(Decimal(-500000), Decimal(-100000))
    assert result.sign_change is None
    assert result.change_pct == Decimal("80.00")


def test_no_float_artifacts() -> None:
    result = compare_metric(Decimal(700000), Decimal(500000))
    assert str(result.change_pct) == "-28.57"


def test_annualise_projects_full_year() -> None:
    assert annualise(Decimal(500000), 5) == Decimal("1200000.00")


def test_annualise_zero_months_returns_none() -> None:
    assert annualise(Decimal(500000), 0) is None


def test_compare_ytd_includes_projection() -> None:
    result = compare_ytd(Decimal(400000), Decimal(500000), months_elapsed=5, period_label="Apr-Aug")
    assert result.period_label == "Apr-Aug"
    assert result.comparison.change_pct == Decimal("25.00")
    assert result.annualised_current == Decimal("1200000.00")
