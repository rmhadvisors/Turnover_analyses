from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.import_log import ImportLog


class VoucherType(str, enum.Enum):
    SALES = "sales"
    PURCHASE = "purchase"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"


class Voucher(Base):
    """One imported voucher row (sales/purchase register line)."""

    __tablename__ = "vouchers"
    __table_args__ = (
        UniqueConstraint("client_id", "dedup_key", name="uq_voucher_client_dedup_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    voucher_type: Mapped[VoucherType] = mapped_column(
        SAEnum(VoucherType, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    voucher_date: Mapped[date] = mapped_column(Date, nullable=False)
    voucher_no: Mapped[str] = mapped_column(String(100), nullable=False)
    party: Mapped[str | None] = mapped_column(String(255))
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal(0))
    tax_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal(0))
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal(0))
    fy: Mapped[str] = mapped_column(String(7), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    import_log_id: Mapped[int | None] = mapped_column(ForeignKey("import_logs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    client: Mapped[Client] = relationship(back_populates="vouchers")
    import_log: Mapped[ImportLog | None] = relationship(back_populates="vouchers")
