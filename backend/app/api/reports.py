from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_client_or_404
from app.database import get_db
from app.models import Client
from app.repositories import alert_repo, client_repo, figures_repo, voucher_repo
from app.schemas.reports import ComparisonRead, MonthlyRead, SummaryRow
from app.services import report_export
from app.services.alert_engine import severity_for_status
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
    years: set[str] = set()
    for client in client_repo.list_clients(db):
        years |= {f.fy for f in figures_repo.list_figures(db, client.id)}
        years |= set(voucher_repo.fys_with_vouchers(db, client.id))
    return sorted(years, reverse=True)


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
    rows = []
    for client in client_repo.list_clients(db):
        comparison = build_comparison(db, client, fy, as_of)
        open_alerts = [
            a
            for a in alert_repo.list_alerts(db, client.id, fy, unacknowledged_only=True)
            if severity_for_status(a.new_status) != "info"
        ]
        rows.append(summary_row(comparison, len(open_alerts)))
    return rows


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
    if format == "pdf":
        content = report_export.client_report_pdf(comparison, monthly, unit)
        return _attachment(content, "application/pdf", f"{stem}.pdf")
    content = report_export.client_report_xlsx(comparison, monthly, unit)
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
