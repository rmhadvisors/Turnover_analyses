from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.alerts import AlertRead


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
    is_period_matched: bool
    period_label: str | None
    rows: list[MetricRowRead]
    limits: list[LimitResultRead]
    notes: list[str]


class SummaryRow(BaseModel):
    client_id: int
    client_name: str
    fy: str
    is_ytd: bool
    previous_turnover: Decimal | None
    current_turnover: Decimal | None
    change_pct: Decimal | None
    band: str | None
    status_label: str
    net_profit_flag: str | None  # turned_to_loss / turned_to_profit
    limits_crossed: int
    limits_approaching: int
    open_alerts: int
    # TDS (None when the client has no ledger-level Tally data for the year)
    tds_parties_crossed: int | None = None
    tds_payable: Decimal | None = None
    tds_not_deducted: Decimal | None = None


class MonthlySeriesRead(BaseModel):
    fy: str
    sales: list[Decimal]
    purchases: list[Decimal]


class MonthlyRead(BaseModel):
    client_id: int
    client_name: str
    months: list[str]
    current: MonthlySeriesRead
    previous: MonthlySeriesRead
    has_data: bool


class AlertCounts(BaseModel):
    client_id: int
    critical: int
    high: int = 0
    warning: int
    info: int


class DashboardRead(BaseModel):
    """Everything the dashboard shows for one FY, in a single response."""

    fy: str
    rows: list[SummaryRow]
    open_alerts: list[AlertCounts]  # unacknowledged alerts for the FY, by client and severity


class ClientReportRead(BaseModel):
    """Everything the client report page shows, in a single response."""

    comparison: ComparisonRead
    monthly: MonthlyRead
    alerts: list[AlertRead]
    tds: dict | None = None  # TDS summary block (services/tds_report.summary_block)
