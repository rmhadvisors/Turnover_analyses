from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

MappingValue = str | list[str]


class ImportPreview(BaseModel):
    report_type: str
    file_name: str
    error: str | None = None
    header_row: int | None = None
    headers: list[str] = []
    mapping: dict[str, MappingValue] = {}
    mapping_source: str | None = None
    suggested_mapping: dict[str, MappingValue] = {}
    raw_rows: list[list[str]] = []
    rows_found: int = 0
    would_import: int = 0
    duplicates: int = 0
    invalid: list[str] = []
    invalid_count: int = 0
    totals_ignored: int = 0
    period: str | None = None
    parsed_sample: list[dict[str, Any]] = []
    profit_loss: dict[str, Decimal | str | None] | None = None


class ImportResult(BaseModel):
    import_log_id: int
    report_type: str
    file_name: str
    period: str | None
    rows_found: int
    imported: int
    duplicates_skipped: int
    invalid_skipped: int
    invalid: list[str]
    totals_ignored: int
    duplicate_file: bool
    fys_affected: list[str]
    alerts_raised: int


class ImportLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    file_name: str
    report_type: str
    period: str | None
    rows_imported: int
    rows_skipped: int
    imported_at: datetime


class MappingBody(BaseModel):
    mapping: dict[str, MappingValue]


class JsonFileInfo(BaseModel):
    file_name: str
    kind: str  # master | transactions
    records: int
    truncated: bool


class JsonFyTotals(BaseModel):
    fy: str
    sales: Decimal
    purchases: Decimal
    sales_count: int
    purchase_count: int


class JsonProfit(BaseModel):
    """Gross / net profit worked out from the export for one FY."""

    fy: str
    gross_profit: Decimal | None
    net_profit: Decimal | None
    opening_stock: Decimal | None = None
    closing_stock: Decimal | None = None
    will_store: bool = False  # preview: would be saved (no manual / P&L figures to keep)
    stored: bool = False  # import result: was saved
    notes: list[str] = []


class JsonSummary(BaseModel):
    files: list[JsonFileInfo]
    period: str | None
    company_gstins: list[str]
    vouchers_read: int
    excluded: dict[str, int]
    other_vouchers: int
    sales_records: int
    purchase_records: int
    by_fy: list[JsonFyTotals]
    warnings: list[str]


class UnmatchedLedger(BaseModel):
    name: str
    lines: int
    amount: Decimal  # total of |amount| on its voucher lines


class MonthCoverage(BaseModel):
    fy: str
    covered: list[str]  # e.g. "Apr-2025": months with posted vouchers
    missing: list[str]  # months of the FY (up to today) with no voucher at all


class FileCompany(BaseModel):
    tally_company_id: str | None  # GUID of the Transactions file's Tally company
    master_company_id: str | None
    gstin: str | None
    client_name: str | None  # the client already linked to this company, if any


class JsonPreview(JsonSummary):
    blockers: list[str] = []  # reasons these files must not be imported (import is refused)
    can_import: bool = True
    company: FileCompany | None = None
    detected_fy: str | None = None  # the FY with the most vouchers
    fy_vouchers: dict[str, int] = {}  # FY -> posted vouchers of any type
    first_date: date | None = None
    last_date: date | None = None
    months: list[MonthCoverage] = []
    unmatched_ledgers: list[UnmatchedLedger] = []  # largest first (at most 50)
    unmatched_ledger_count: int = 0
    unmatched_lines: int = 0
    unmatched_amount: Decimal = Decimal(0)
    unmatched_share_pct: Decimal = Decimal(0)  # of the vouchers' line value
    unmatched_limit_pct: Decimal | None = None  # imports are refused above this share
    would_import: int
    duplicates: int
    sample: list[dict[str, Any]]
    fys_already_imported: list[str] = []  # FYs in these files that already have imported data
    existing_vouchers: dict[str, int] = {}  # FY -> imported vouchers already stored
    profits: list[JsonProfit] = []
    preview_token: str | None = None  # pass to /tally-json/confirm instead of the files


class ImportProgress(BaseModel):
    stage: str  # reading | parsing | done
    fraction: float  # 0..1 of the uploaded bytes parsed


class JsonImportResult(JsonSummary):
    imported: int
    duplicates_skipped: int
    import_log_ids: list[int]
    fys_affected: list[str]
    alerts_raised: int
    replaced: dict[str, int] = {}  # FY -> previously imported vouchers removed
    skipped_fys: list[str] = []
    profits: list[JsonProfit] = []
