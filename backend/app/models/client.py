from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    yearly_figures: Mapped[list[YearlyFigures]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    vouchers: Mapped[list[Voucher]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    alerts: Mapped[list[Alert]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    import_logs: Mapped[list[ImportLog]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    column_mappings: Mapped[list[ColumnMapping]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
