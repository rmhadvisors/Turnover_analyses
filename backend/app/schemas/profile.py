from typing import Literal

import re

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

PAN_PATTERN = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")


class ProfileIn(BaseModel):
    """A client's profile. Every field may be null (unknown)."""

    gstin: str | None = None
    pan: str | None = None
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

    @field_validator("pan")
    @classmethod
    def check_pan(cls, value: str | None) -> str | None:
        value = (value or "").strip().upper() or None
        if value is not None and not PAN_PATTERN.fullmatch(value):
            raise ValueError("PAN must be 10 characters, e.g. ABCDE1234F")
        return value

    @model_validator(mode="after")
    def pan_matches_gstin(self) -> "ProfileIn":
        if self.gstin and self.pan and self.gstin[2:12] != self.pan:
            raise ValueError(f"The PAN in GSTIN {self.gstin} is {self.gstin[2:12]}, not {self.pan}")
        return self


class ProfileRead(ProfileIn):
    model_config = ConfigDict(from_attributes=True)

    client_id: int
    state_code: str | None = None
    state_name: str | None = None
    entity_hint: str | None = None  # e.g. PAN type F: firm or LLP
    unknown_fields: list[str] = []  # fields still unknown (their limits still apply)
