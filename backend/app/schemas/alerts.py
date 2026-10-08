from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class AlertRead(BaseModel):
    id: int
    client_id: int
    client_name: str
    fy: str
    metric: str
    metric_label: str
    old_status: str | None
    new_status: str
    severity: str
    value: Decimal
    threshold_description: str | None
    crossed_on: date | None
    crossed_voucher_no: str | None
    triggered_at: datetime
    acknowledged: bool
    acknowledged_by: str | None
    note: str | None = None
    # TDS alerts only
    kind: str = "turnover"  # 'turnover' | 'tds'
    section: str | None = None
    party: str | None = None
    party_key: str | None = None
    party_pan: str | None = None
    threshold: Decimal | None = None
    aggregate: Decimal | None = None
    tds_computed: Decimal | None = None
    tds_deducted: Decimal | None = None
    shortfall: Decimal | None = None
    money_at_stake: Decimal | None = None


class AlertCount(BaseModel):
    unacknowledged: int


class AcknowledgeIn(BaseModel):
    acknowledged_by: str = Field(min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=500)


class RecheckResult(BaseModel):
    alerts_raised: int
