from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_client_or_404
from app.database import get_db
from app.models import Client
from app.schemas.reports import ComparisonRead
from app.services.comparison_service import build_comparison
from app.services.fy_utils import is_valid_fy
from app.services.report_builder import comparison_to_read

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/comparison/{client_id}", response_model=ComparisonRead)
def client_comparison(
    fy: str,
    as_of: date | None = None,
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    """Current-vs-previous FY comparison; `as_of` overrides today for YTD logic."""
    if not is_valid_fy(fy):
        raise HTTPException(422, "fy must look like 2025-26")
    return comparison_to_read(build_comparison(db, client, fy, as_of))
