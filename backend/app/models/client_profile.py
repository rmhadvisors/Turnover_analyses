from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClientProfile(Base):
    """Facts that decide which statutory limits apply to a client. Every field may be
    unknown (None); an unknown field never switches a limit off."""

    __tablename__ = "client_profiles"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True
    )
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str | None] = mapped_column(String(2))
    gst_registered: Mapped[bool | None] = mapped_column(Boolean)
    special_category: Mapped[bool | None] = mapped_column(Boolean)
    entity_type: Mapped[str | None] = mapped_column(String(20))
    nature: Mapped[str | None] = mapped_column(String(20))
    supplies: Mapped[str | None] = mapped_column(String(20))
    presumptive: Mapped[str | None] = mapped_column(String(10))
    cash_within_5pct: Mapped[bool | None] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
