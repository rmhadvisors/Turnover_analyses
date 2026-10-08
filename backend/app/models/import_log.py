from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.voucher import Voucher


class ImportLog(Base):
    """Record of one Tally-export file import for a client."""

    __tablename__ = "import_logs"
    __table_args__ = (Index("ix_import_logs_client", "client_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    period: Mapped[str | None] = mapped_column(String(50))
    file_hash: Mapped[str | None] = mapped_column(String(64))
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    client: Mapped[Client] = relationship(back_populates="import_logs")
    vouchers: Mapped[list[Voucher]] = relationship(back_populates="import_log")
