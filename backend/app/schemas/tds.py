from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.fy_utils import is_valid_fy
from app.services.tds_engine import BASES, SECTION_KEYS

LAW_NOTE = (
    "Thresholds and rates are as amended up to Finance Act 2025. From 01.04.2026 these "
    "provisions sit in s.393 of the Income-tax Act 2025. The CA must verify them each year."
)


class SectionBase(BaseModel):
    key: str
    nature: str = Field(min_length=1, max_length=255)
    single_threshold: Decimal = Field(ge=0)
    aggregate_threshold: Decimal = Field(ge=0)
    rate_individual: Decimal = Field(ge=0, le=100)
    rate_other: Decimal = Field(ge=0, le=100)
    rate_no_pan: Decimal = Field(ge=0, le=100)
    base: str
    effective_from: date
    remarks: str | None = None

    @field_validator("key")
    @classmethod
    def known_key(cls, value: str) -> str:
        if value not in SECTION_KEYS:
            raise ValueError(f"key must be one of {', '.join(SECTION_KEYS)}")
        return value

    @field_validator("base")
    @classmethod
    def known_base(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in BASES:
            raise ValueError("base must be 'full', 'excess' or 'monthly'")
        return value


class SectionRead(SectionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    section: str


class SectionsOut(BaseModel):
    note: str = LAW_NOTE
    sections: list[SectionRead]


class MappingRow(BaseModel):
    id: int | None = None
    match_type: str  # 'ledger' | 'group'
    name: str
    role: str
    section_key: str | None
    source: str
    reason: str | None
    kind: str | None = None
    groups: list[str] = []
    amounts: dict[str, Decimal] = {}  # FY -> net debit posted to the ledger
    lines: int = 0


class MappingOut(BaseModel):
    client_id: int
    approved_at: datetime | None
    approved_by: str | None
    rows: list[MappingRow]
    roles: list[str]
    sections: list[str]


class MappingChange(BaseModel):
    match_type: str = Field(pattern="^(ledger|group)$")
    name: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern="^(base|tds|excluded|unmapped)$")
    section_key: str | None = None
    delete: bool = False  # group rules only

    @field_validator("section_key")
    @classmethod
    def known_section(cls, value: str | None) -> str | None:
        if value and value not in SECTION_KEYS:
            raise ValueError(f"section_key must be one of {', '.join(SECTION_KEYS)}")
        return value or None


class MappingUpdate(BaseModel):
    changes: list[MappingChange]


class ApproveIn(BaseModel):
    approved_by: str = Field(min_length=1, max_length=255)
    approved: bool = True


class PartyIn(BaseModel):
    party_key: str = Field(min_length=1, max_length=300)
    party_name: str | None = None
    pan: str | None = Field(default=None, max_length=10)
    payee_type: str | None = Field(default=None, pattern="^(individual_huf|other)$")
    transporter_declaration: bool = False
    tcs_206c1h: bool = False
    note: str | None = Field(default=None, max_length=255)


class ResolutionIn(BaseModel):
    fy: str
    party_key: str = Field(min_length=1, max_length=300)
    section_key: str
    amount: Decimal | None = Field(default=None, ge=0)  # None = the full shortfall
    note: str | None = Field(default=None, max_length=255)
    marked_by: str | None = Field(default=None, max_length=255)
    remove: bool = False


class VoucherFlagIn(BaseModel):
    voucher_key: str = Field(min_length=1, max_length=64)
    on: bool = True
    # 'tcs_206c1h' (leave out of 194Q) or 'section:<key>' (count it under another section)
    flag: str = Field(default="tcs_206c1h", pattern=r"^(tcs_206c1h|section:.{3,10})$")
    note: str | None = Field(default=None, max_length=255)


class PayerIn(BaseModel):
    fy: str
    constitution: str | None = None  # None = from the PAN
    audit_liable: bool | None = None  # None = not confirmed
    note: str | None = Field(default=None, max_length=255)


class TdsSettingsIO(BaseModel):
    approaching_pct: Decimal = Field(gt=0, le=100)
    analysis_fy: str = "2025-26"  # the one FY TDS is analysed and alerted for
    # payments with no party ledger: alerted above this total, only listed below it
    unidentified_min: Decimal = Field(default=Decimal(30000), ge=0)

    @field_validator("analysis_fy")
    @classmethod
    def valid_fy(cls, value: str) -> str:
        if not is_valid_fy(value):
            raise ValueError("analysis_fy must look like 2025-26")
        return value


class AssignmentIn(BaseModel):
    """Assign a payee to every party-less payment of one ledger in the FY."""

    fy: str
    ledger: str = Field(min_length=1, max_length=255)
    payee_name: str | None = Field(default=None, max_length=255)
    pan: str | None = Field(default=None, max_length=10)
    payee_type: str | None = Field(default=None, pattern="^(individual_huf|other)$")
    note: str | None = Field(default=None, max_length=255)
    remove: bool = False


class AcknowledgeTdsIn(BaseModel):
    acknowledged_by: str = Field(min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=500)
