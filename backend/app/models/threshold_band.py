from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ThresholdSetting(Base):
    """The editable +/-5% and +/-20% band cutoffs used by alert_engine.classify_band.

    Single-row settings table (id=1) rather than one row per band: the five
    bands are always symmetric around these two numbers, per the requirement
    sheet ('set a threshold, for example 20%').
    """

    __tablename__ = "threshold_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    moderate_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("5.00"))
    significant_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("20.00"))
    include_gst_in_turnover: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
