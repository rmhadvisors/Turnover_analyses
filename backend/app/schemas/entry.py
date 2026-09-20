from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from app.services.fy_utils import is_valid_fy, previous_fy


class ManualEntryIn(BaseModel):
    """The section 1a form. Omitted / null amounts are left untouched."""

    client_id: int
    previous_fy: str
    current_fy: str
    previous_turnover: Decimal | None = None
    current_turnover: Decimal | None = None
    previous_purchases: Decimal | None = None
    current_purchases: Decimal | None = None
    previous_gross_profit: Decimal | None = None
    current_gross_profit: Decimal | None = None
    previous_net_profit: Decimal | None = None
    current_net_profit: Decimal | None = None

    @model_validator(mode="after")
    def check_years(self) -> "ManualEntryIn":
        for label, value in (
            ("previous_fy", self.previous_fy),
            ("current_fy", self.current_fy),
        ):
            if not is_valid_fy(value):
                raise ValueError(f"{label} must look like 2024-25")
        if previous_fy(self.current_fy) != self.previous_fy:
            raise ValueError("previous_fy must be the financial year just before current_fy")
        return self

    def split(self) -> tuple[dict, dict]:
        keys = ("turnover", "purchases", "gross_profit", "net_profit")
        return (
            {k: getattr(self, f"previous_{k}") for k in keys},
            {k: getattr(self, f"current_{k}") for k in keys},
        )


class FiguresRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    client_id: int
    fy: str
    turnover: Decimal | None
    purchases: Decimal | None
    gross_profit: Decimal | None
    net_profit: Decimal | None
    is_manual: bool
    computed_at: datetime


class ManualEntryResult(BaseModel):
    figures: list[FiguresRead]
    alerts_raised: int
