from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.alerts import to_read as alert_to_read
from app.api.deps import get_client_or_404
from app.database import get_db
from app.models import Client
from app.repositories import alert_repo, client_repo, figures_repo, voucher_repo
from app.schemas.reports import (
    AlertCounts,
    ClientReportRead,
    ComparisonRead,
    DashboardRead,
    MonthlyRead,
    SummaryRow,
)
from app.services import report_export, tds_report, tds_service
from app.services.comparison_service import build_comparison, build_monthly
from app.services.fy_utils import is_valid_fy
from app.services.report_builder import comparison_to_read, summary_row

router = APIRouter(prefix="/reports", tags=["reports"])


def _check_fy(fy: str) -> None:
    if not is_valid_fy(fy):
        raise HTTPException(422, "fy must look like 2025-26")


@router.get("/fys", response_model=list[str])
def financial_years(db: Session = Depends(get_db)):
    """Every FY that has data for any client, newest first."""
    return all_financial_years(db)


def all_financial_years(db: Session) -> list[str]:
    return sorted(figures_repo.all_fys(db) | voucher_repo.all_fys(db), reverse=True)


@router.get("/comparison/{client_id}", response_model=ComparisonRead)
def client_comparison(
    fy: str,
    as_of: date | None = None,
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    """Current-vs-previous FY comparison; `as_of` overrides today for YTD logic."""
    _check_fy(fy)
    return comparison_to_read(build_comparison(db, client, fy, as_of))


@router.get("/summary", response_model=list[SummaryRow])
def summary(fy: str, as_of: date | None = None, db: Session = Depends(get_db)):
    """One row per client for `fy`: turnover change, status and open alerts."""
    _check_fy(fy)
    return _summary_rows(db, fy, as_of, alert_repo.open_counts_by_client(db, fy))


def _summary_rows(
    db: Session, fy: str, as_of: date | None, counts: dict[int, dict[str, int]]
) -> list[SummaryRow]:
    rows = []
    for client in client_repo.list_clients(db):
        comparison = build_comparison(db, client, fy, as_of)
        open_alerts = counts.get(client.id, {})  # "open" = critical + high + warning, not info
        open_count = sum(open_alerts.get(level, 0) for level in ("critical", "high", "warning"))
        row = summary_row(comparison, open_count)
        tds = _tds_block(db, client.id, fy)
        if tds is not None and tds["has_data"]:
            row.tds_parties_crossed = tds["parties_crossed"]
            row.tds_payable = Decimal(tds["tds_payable"])
            row.tds_not_deducted = Decimal(tds["not_deducted"])
        rows.append(row)
    return rows


def _tds_block(db: Session, client_id: int, fy: str) -> dict | None:
    """The client's TDS summary for the year; None outside the TDS analysis year or when the
    client has no ledger-level data."""
    if fy != tds_service.analysis_fy(db) or fy not in tds_service.fys_with_entries(db, client_id):
        return None
    return tds_report.summary_block(tds_service.analyze(db, client_id, fy))


@router.get("/dashboard", response_model=DashboardRead)
def dashboard(fy: str, as_of: date | None = None, db: Session = Depends(get_db)):
    """The all-clients summary plus open-alert counts by severity, in one call."""
    _check_fy(fy)
    counts = alert_repo.open_counts_by_client(db, fy)
    return DashboardRead(
        fy=fy,
        rows=_summary_rows(db, fy, as_of, counts),
        open_alerts=[AlertCounts(client_id=cid, **row) for cid, row in counts.items()],
    )


@router.get("/client/{client_id}", response_model=ClientReportRead)
def client_report(
    fy: str,
    as_of: date | None = None,
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    """Comparison, month-wise series and alerts for one client and FY, in one call."""
    _check_fy(fy)
    return ClientReportRead(
        comparison=comparison_to_read(build_comparison(db, client, fy, as_of)),
        monthly=MonthlyRead.model_validate(build_monthly(db, client, fy), from_attributes=True),
        # alerts of a deleted limit (e.g. the TDS sections once seeded as turnover limits)
        # stay in the Alerts history but are not part of the client's report
        alerts=[
            alert_to_read(a)
            for a in alert_repo.without_deleted_limits(db, alert_repo.list_alerts(db, client.id, fy))
        ],
        tds=_tds_block(db, client.id, fy),
    )


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _attachment(content: bytes, media_type: str, filename: str) -> Response:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


UnitParam = Query("auto", pattern="^(auto|lakhs|crores|full)$")


@router.get("/monthly/{client_id}", response_model=MonthlyRead)
def client_monthly(
    fy: str, client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)
):
    """Month-wise net sales and purchases for `fy` and the previous FY."""
    _check_fy(fy)
    return build_monthly(db, client, fy)


@router.get("/export/{client_id}")
def export_client_report(
    fy: str,
    format: str = Query("xlsx", pattern="^(xlsx|pdf)$"),
    unit: str = UnitParam,
    as_of: date | None = None,
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    """Download the Turnover Comparison report as Excel or PDF."""
    _check_fy(fy)
    comparison = build_comparison(db, client, fy, as_of)
    monthly = build_monthly(db, client, fy)
    stem = f"Turnover_Comparison_{client.name}_FY{fy}"
    tds = _tds_block(db, client.id, fy)
    if format == "pdf":
        content = report_export.client_report_pdf(comparison, monthly, unit, tds)
        return _attachment(content, "application/pdf", f"{stem}.pdf")
    content = report_export.client_report_xlsx(comparison, monthly, unit, tds)
    return _attachment(content, XLSX, f"{stem}.xlsx")


@router.get("/summary/export")
def export_summary(
    fy: str, unit: str = UnitParam, as_of: date | None = None, db: Session = Depends(get_db)
):
    """Download the all-clients summary as Excel."""
    rows = summary(fy, as_of, db)
    return _attachment(
        report_export.summary_xlsx(rows, fy, unit), XLSX, f"Turnover_Summary_FY{fy}.xlsx"
    )
