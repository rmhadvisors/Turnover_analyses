from datetime import date

from app.services.fy_utils import (
    fy_bounds,
    fy_label,
    is_fy_in_progress,
    months_elapsed_in_fy,
    next_fy,
    period_label,
    previous_fy,
    same_period_previous_fy,
    ytd_period,
)


def test_31_march_belongs_to_ending_fy() -> None:
    assert fy_label(date(2026, 3, 31)) == "2025-26"


def test_1_april_belongs_to_starting_fy() -> None:
    assert fy_label(date(2026, 4, 1)) == "2026-27"


def test_mid_year_date() -> None:
    assert fy_label(date(2025, 12, 15)) == "2025-26"


def test_fy_bounds() -> None:
    start, end = fy_bounds("2024-25")
    assert start == date(2024, 4, 1)
    assert end == date(2025, 3, 31)


def test_next_and_previous_fy() -> None:
    assert next_fy("2024-25") == "2025-26"
    assert previous_fy("2025-26") == "2024-25"


def test_is_fy_in_progress() -> None:
    assert is_fy_in_progress("2025-26", date(2025, 8, 1)) is True
    assert is_fy_in_progress("2025-26", date(2026, 4, 1)) is False


def test_ytd_period_clips_to_fy_bounds() -> None:
    period = ytd_period("2025-26", date(2025, 8, 20))
    assert period.start == date(2025, 4, 1)
    assert period.end == date(2025, 8, 20)

    full_year = ytd_period("2024-25", date(2026, 1, 1))
    assert full_year.end == date(2025, 3, 31)


def test_months_elapsed_in_fy() -> None:
    assert months_elapsed_in_fy("2025-26", date(2025, 4, 1)) == 1
    assert months_elapsed_in_fy("2025-26", date(2025, 8, 31)) == 5
    assert months_elapsed_in_fy("2025-26", date(2026, 3, 31)) == 12
    assert months_elapsed_in_fy("2025-26", date(2025, 1, 1)) == 0


def test_same_period_previous_fy_matches_months() -> None:
    period = same_period_previous_fy("2025-26", date(2025, 8, 20))
    assert period.start == date(2024, 4, 1)
    assert period.end == date(2024, 8, 31)


def test_period_label() -> None:
    period = ytd_period("2025-26", date(2025, 8, 20))
    assert period_label(period) == "Apr-Aug"


def test_is_valid_fy() -> None:
    from app.services.fy_utils import is_valid_fy

    assert is_valid_fy("2024-25") and is_valid_fy("2099-00")
    for bad in ("2024-26", "2024/25", "24-25", "2024-2025", "abcd-ef", ""):
        assert not is_valid_fy(bad)


def test_ytd_window_pair_is_month_aligned() -> None:
    from app.services.fy_utils import ytd_window_pair

    current, previous = ytd_window_pair("2025-26", date(2025, 8, 20))
    assert (current.start, current.end) == (date(2025, 4, 1), date(2025, 8, 31))
    assert (previous.start, previous.end) == (date(2024, 4, 1), date(2024, 8, 31))
