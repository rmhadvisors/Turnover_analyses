from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class ProfileIn(BaseModel):
    """A client's profile. Every field may be null (unknown)."""

    gstin: str | None = None
    gst_registered: bool | None = None
    special_category: bool | None = None
    entity_type: Literal["individual", "huf", "firm", "llp", "company", "other"] | None = None
    nature: Literal["business", "profession", "both"] | None = None
    supplies: Literal["goods", "services", "both"] | None = None
    presumptive: Literal["none", "44AD", "44ADA"] | None = None
    cash_within_5pct: bool | None = None

    @field_validator("gstin")
    @classmethod
    def check_gstin(cls, value: str | None) -> str | None:
        value = (value or "").strip().upper() or None
        if value is not None and (len(value) != 15 or not value[:2].isdigit()):
            raise ValueError("GSTIN must be 15 characters starting with the 2-digit state code")
        return value


class ProfileRead(ProfileIn):
    model_config = ConfigDict(from_attributes=True)

    client_id: int
    state_code: str | None = None
    state_name: str | None = None
    entity_hint: str | None = None  # e.g. PAN type F: firm or LLP
    unknown_fields: list[str] = []  # fields still unknown (their limits still apply)
