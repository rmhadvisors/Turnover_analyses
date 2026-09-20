from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import Boolean, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AbsoluteLimitMetric(str, enum.Enum):
    SALES_TURNOVER = "sales_turnover"
    PURCHASE_TURNOVER = "purchase_turnover"
    AGGREGATE_TURNOVER = "aggregate_turnover"


class AbsoluteLimit(Base):
    """A configurable rupee limit (e.g. GST registration, tax audit u/s 44AB).

    Defaults are pre-filled as commonly used Indian compliance figures but are
    NOT authoritative law - `is_default_seed` marks rows the CA should verify
    and edit/disable as needed.
    """

    __tablename__ = "absolute_limits"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    metric: Mapped[AbsoluteLimitMetric] = mapped_column(
        SAEnum(AbsoluteLimitMetric, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fy_scope: Mapped[str | None] = mapped_column(String(7))
    description: Mapped[str | None] = mapped_column(Text)
    approaching_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("80.00"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default_seed: Mapped[bool] = mapped_column(Boolean, default=False)
