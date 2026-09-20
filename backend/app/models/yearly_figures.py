from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.client import Client


class YearlyFigures(Base):
    """One client's turnover/purchases/profit figures for one FY, either
    entered manually or aggregated from imported vouchers and P&L data."""

    __tablename__ = "yearly_figures"
    __table_args__ = (UniqueConstraint("client_id", "fy", name="uq_yearly_figures_client_fy"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    fy: Mapped[str] = mapped_column(String(7), nullable=False)
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    purchases: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    is_manual: Mapped[bool] = mapped_column(Boolean, default=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    client: Mapped[Client] = relationship(back_populates="yearly_figures")
