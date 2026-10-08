from datetime import datetime
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


class JsonPreview(JsonSummary):
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
