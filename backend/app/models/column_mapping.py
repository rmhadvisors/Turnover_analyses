from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ColumnMapping(Base):
    """Saved column mapping (source header -> field) for a client + report
    type, so repeat imports of the same Tally export layout are automatic."""

    __tablename__ = "column_mappings"
    __table_args__ = (
        UniqueConstraint("client_id", "report_type", name="uq_column_mapping_client_report"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    mapping_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    client: Mapped[Client] = relationship(back_populates="column_mappings")
