from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.client import Client


class Alert(Base):
    """A recorded threshold status change (band or absolute-limit)."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_client_fy_metric", "client_id", "fy", "metric"),
        Index("ix_alerts_acknowledged", "acknowledged"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    fy: Mapped[str] = mapped_column(String(7), nullable=False)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(100))
    new_status: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    threshold_description: Mapped[str | None] = mapped_column(String(255))
    crossed_on: Mapped[date | None] = mapped_column(Date)
    crossed_voucher_no: Mapped[str | None] = mapped_column(String(100))
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(String(500))
    # TDS alerts only (metric 'tds:<section>:<party hash>')
    section: Mapped[str | None] = mapped_column(String(10))
    party: Mapped[str | None] = mapped_column(String(255))
    party_key: Mapped[str | None] = mapped_column(String(300))
    party_pan: Mapped[str | None] = mapped_column(String(10))
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    aggregate: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    tds_computed: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    tds_deducted: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    shortfall: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    # shortfall + 30% s.40(a)(ia) disallowance + estimated interest (refreshed on every re-check)
    money_at_stake: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    client: Mapped[Client] = relationship(back_populates="alerts")
