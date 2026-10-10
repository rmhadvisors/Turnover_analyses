from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.client_profile import ClientProfile
    from app.models.column_mapping import ColumnMapping
    from app.models.import_log import ImportLog
    from app.models.voucher import Voucher
    from app.models.yearly_figures import YearlyFigures


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # The Tally company GUID its JSON exports carry (set by the first Tally import).
    tally_company_id: Mapped[str | None] = mapped_column(String(36))

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
    profile: Mapped[ClientProfile | None] = relationship(cascade="all, delete-orphan")
