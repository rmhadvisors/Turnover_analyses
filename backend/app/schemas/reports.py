from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class MetricRowRead(BaseModel):
    key: str
    label: str
    previous: Decimal | None
    current: Decimal | None
    difference: Decimal | None
    change_pct: Decimal | None
    direction: str  # up | down | flat | none
    no_comparison: bool
    sign_change: str | None
    band: str | None
    status_label: str  # e.g. "Significant Increase" / "New / No comparison"
    annualised: Decimal | None


class LimitResultRead(BaseModel):
    limit_id: int
    name: str
    metric: str
    amount: Decimal
    status: str
    cumulative: Decimal
    crossed_on: date | None
    crossed_voucher_no: str | None
    message: str


class ComparisonRead(BaseModel):
    client_id: int
    client_name: str
    fy: str
    previous_fy: str
    is_ytd: bool
    period_label: str | None
    rows: list[MetricRowRead]
    limits: list[LimitResultRead]
    notes: list[str]
