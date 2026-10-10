from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Numeric, String
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
    # TDS 'approaching threshold' alert at this % of a section's aggregate threshold
    tds_approaching_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("80.00"))
    # the one FY TDS is analysed and alerted for (the year before is only its base year)
    tds_analysis_fy: Mapped[str] = mapped_column(String(7), default="2025-26")
    # payments with no party ledger above this total are alerted; below it only listed
    tds_unidentified_min: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal(30000))
    # a Tally import is refused when more of its voucher line value (in %) is on ledgers
    # missing from the Master file (the Master is older than the Transactions file)
    max_unmatched_ledger_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("2.00")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
