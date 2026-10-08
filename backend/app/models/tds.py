"""TDS tables: the rate master, ledger -> section mapping, the ledger-level data captured
from Tally JSON imports, and the user's per-client / per-party / per-voucher decisions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

_CLIENT_FK = "clients.id"


class TdsSection(Base):
    """Rate & threshold master (one row per section per effective-from date). Rates are
    percentages; a threshold of 0 means 'no such test'."""

    __tablename__ = "tds_sections"
    __table_args__ = (UniqueConstraint("key", "effective_from", name="uq_tds_section_key_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(10), nullable=False)
    section: Mapped[str] = mapped_column(String(10), nullable=False)
    nature: Mapped[str] = mapped_column(String(255), nullable=False)
    single_threshold: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal(0))
    aggregate_threshold: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal(0))
    rate_individual: Mapped[Decimal] = mapped_column(Numeric(7, 3), nullable=False)
    rate_other: Mapped[Decimal] = mapped_column(Numeric(7, 3), nullable=False)
    rate_no_pan: Mapped[Decimal] = mapped_column(Numeric(7, 3), nullable=False)
    base: Mapped[str] = mapped_column(String(10), nullable=False)  # 'full' | 'excess'
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TdsLedgerMap(Base):
    """Maps a ledger (or every ledger under a group) of one client to a TDS role:
    'base' (payments under `section_key`), 'tds' (TDS deducted under `section_key`) or
    'excluded' (looked at and deliberately not TDS). A ledger rule beats a group rule."""

    __tablename__ = "tds_ledger_map"
    __table_args__ = (
        UniqueConstraint("client_id", "match_type", "name_key", name="uq_tds_map_client_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), nullable=False
    )
    match_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 'ledger' | 'group'
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # 'base' | 'tds' | 'excluded'
    section_key: Mapped[str | None] = mapped_column(String(10))
    # rent: 194I(a) (machinery, 2%) vs 194I(b) (building, 10%) must be chosen by the CA
    requires_choice: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="auto")  # 'auto' | 'user'
    reason: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TdsLedger(Base):
    """A ledger from the Tally Master file of one client and FY."""

    __tablename__ = "tds_ledgers"
    __table_args__ = (
        UniqueConstraint("client_id", "fy", "name_key", name="uq_tds_ledger_client_fy_name"),
        Index("ix_tds_ledgers_client_guid", "client_id", "guid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), nullable=False
    )
    fy: Mapped[str] = mapped_column(String(7), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    guid: Mapped[str | None] = mapped_column(String(80))
    parent: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(
        String(20), default="other"
    )  # purchase, expense, tds, party...
    groups: Mapped[list] = mapped_column(JSON, default=list)  # every group above it, nearest first
    gstin: Mapped[str | None] = mapped_column(String(15))
    tally_pan: Mapped[str | None] = mapped_column(String(10))
    tax_type: Mapped[str | None] = mapped_column(String(30))
    deductee_type: Mapped[str | None] = mapped_column(String(60))
    is_transporter: Mapped[bool] = mapped_column(Boolean, default=False)


class TdsEntry(Base):
    """One ledger line of a posted Tally voucher (credit +, debit -). Every line of every
    voucher is kept, so remapping a ledger never needs a re-import."""

    __tablename__ = "tds_entries"
    __table_args__ = (
        UniqueConstraint("client_id", "voucher_key", "line_no", name="uq_tds_entry_voucher_line"),
        Index("ix_tds_entries_client_fy", "client_id", "fy"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), nullable=False
    )
    fy: Mapped[str] = mapped_column(String(7), nullable=False)
    voucher_key: Mapped[str] = mapped_column(String(64), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    voucher_date: Mapped[date] = mapped_column(Date, nullable=False)
    voucher_type: Mapped[str | None] = mapped_column(String(100))
    voucher_no: Mapped[str | None] = mapped_column(String(100))
    party_ledger: Mapped[str | None] = mapped_column(String(255))  # Tally's party of the voucher
    ledger: Mapped[str] = mapped_column(String(255), nullable=False)
    ledger_key: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    is_party: Mapped[bool] = mapped_column(Boolean, default=False)
    import_log_id: Mapped[int | None] = mapped_column(ForeignKey("import_logs.id"))


class TdsClientSettings(Base):
    """Per-client TDS choices: constitution override and mapping approval."""

    __tablename__ = "tds_client_settings"

    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), primary_key=True
    )
    constitution: Mapped[str | None] = mapped_column(String(30))  # None = from the PAN
    mapping_approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    mapping_approved_by: Mapped[str | None] = mapped_column(String(255))


class TdsPayerYear(Base):
    """Per client and FY: was the client liable to tax audit u/s 44AB in the PRECEDING
    year (decides 194C/H/J/I for an Individual/HUF). None = not confirmed."""

    __tablename__ = "tds_payer_years"

    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), primary_key=True
    )
    fy: Mapped[str] = mapped_column(String(7), primary_key=True)
    audit_liable: Mapped[bool | None] = mapped_column(Boolean)
    note: Mapped[str | None] = mapped_column(String(255))


class TdsParty(Base):
    """User facts about one party (keyed by its Tally ledger guid, else its name)."""

    __tablename__ = "tds_parties"

    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), primary_key=True
    )
    party_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    party_name: Mapped[str | None] = mapped_column(String(255))
    pan: Mapped[str | None] = mapped_column(String(10))  # entered; beats the GSTIN / Tally PAN
    payee_type: Mapped[str | None] = mapped_column(String(20))  # override
    transporter_declaration: Mapped[bool] = mapped_column(Boolean, default=False)
    tcs_206c1h: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(255))


class TdsVoucherFlag(Base):
    """A voucher left out of the 194Q base because the seller charged TCS u/s 206C(1H)."""

    __tablename__ = "tds_voucher_flags"

    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), primary_key=True
    )
    voucher_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    flag: Mapped[str] = mapped_column(String(20), default="tcs_206c1h")
    note: Mapped[str | None] = mapped_column(String(255))


class TdsAssignment(Base):
    """A payee the CA assigned to payments with no party ledger (e.g. rent paid straight
    from the bank): every party-less voucher of that ledger in the FY goes to this payee."""

    __tablename__ = "tds_assignments"

    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), primary_key=True
    )
    fy: Mapped[str] = mapped_column(String(7), primary_key=True)
    ledger_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    ledger: Mapped[str] = mapped_column(String(255), nullable=False)
    payee_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pan: Mapped[str | None] = mapped_column(String(10))
    payee_type: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(255))


class TdsResolution(Base):
    """'TDS deducted outside Tally' for one client / FY / party / section."""

    __tablename__ = "tds_resolutions"

    client_id: Mapped[int] = mapped_column(
        ForeignKey(_CLIENT_FK, ondelete="CASCADE"), primary_key=True
    )
    fy: Mapped[str] = mapped_column(String(7), primary_key=True)
    party_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    section_key: Mapped[str] = mapped_column(String(10), primary_key=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # None = the full amount
    note: Mapped[str | None] = mapped_column(String(255))
    marked_by: Mapped[str | None] = mapped_column(String(255))
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
