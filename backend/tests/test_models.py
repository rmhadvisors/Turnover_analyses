from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    AbsoluteLimit,
    AbsoluteLimitMetric,
    Client,
    Voucher,
    VoucherType,
    YearlyFigures,
)


def test_create_client_and_yearly_figures(db_session) -> None:
    client = Client(name="Acme Traders")
    db_session.add(client)
    db_session.commit()

    figures = YearlyFigures(
        client_id=client.id,
        fy="2025-26",
        turnover=Decimal("10000000.00"),
        purchases=Decimal("6000000.00"),
        gross_profit=Decimal("1800000.00"),
        net_profit=Decimal("500000.00"),
        is_manual=True,
    )
    db_session.add(figures)
    db_session.commit()

    assert client.yearly_figures[0].turnover == Decimal("10000000.00")


def test_client_name_must_be_unique(db_session) -> None:
    db_session.add(Client(name="Acme Traders"))
    db_session.commit()
    db_session.add(Client(name="Acme Traders"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_duplicate_voucher_is_rejected_by_unique_constraint(db_session) -> None:
    client = Client(name="Beta Corp")
    db_session.add(client)
    db_session.commit()

    voucher_kwargs = {
        "client_id": client.id,
        "voucher_type": VoucherType.SALES,
        "voucher_date": date(2025, 6, 1),
        "voucher_no": "S-101",
        "total_value": Decimal("50000.00"),
        "fy": "2025-26",
        "dedup_key": "sales|S-101|2025-06-01|50000.00",
    }
    db_session.add(Voucher(**voucher_kwargs))
    db_session.commit()

    db_session.add(Voucher(**voucher_kwargs))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_absolute_limit_defaults(db_session) -> None:
    limit = AbsoluteLimit(
        name="GST registration (goods)",
        metric=AbsoluteLimitMetric.AGGREGATE_TURNOVER,
        amount=Decimal("4000000.00"),
        description="Verify current limit - varies by state and supply type.",
        is_default_seed=True,
    )
    db_session.add(limit)
    db_session.commit()

    assert limit.is_enabled is True
    assert limit.approaching_pct == Decimal("80.00")
