from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import AbsoluteLimitMetric
from app.services.fy_utils import is_valid_fy


class SettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    moderate_pct: Decimal
    significant_pct: Decimal
    include_gst_in_turnover: bool


class SettingsUpdate(BaseModel):
    moderate_pct: Decimal = Field(gt=0, le=1000)
    significant_pct: Decimal = Field(gt=0, le=1000)
    include_gst_in_turnover: bool = False

    @model_validator(mode="after")
    def check_order(self) -> "SettingsUpdate":
        if self.moderate_pct >= self.significant_pct:
            raise ValueError("The moderate band limit must be below the significant limit")
        return self


class LimitBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    metric: AbsoluteLimitMetric
    amount: Decimal = Field(gt=0)
    fy_scope: str | None = None
    description: str | None = None
    approaching_pct: Decimal = Field(default=Decimal(80), gt=0, le=100)
    is_enabled: bool = True

    @model_validator(mode="after")
    def check_fy(self) -> "LimitBase":
        if self.fy_scope and not is_valid_fy(self.fy_scope):
            raise ValueError("fy_scope must look like 2025-26 (or be empty for all years)")
        return self


class LimitRead(LimitBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_default_seed: bool
