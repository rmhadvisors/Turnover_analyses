from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AbsoluteLimit
from app.repositories import threshold_repo
from app.schemas.thresholds import LimitBase, LimitRead, SettingsRead, SettingsUpdate
from app.services.recheck_service import evaluate_all_clients

router = APIRouter(prefix="/thresholds", tags=["thresholds"])


def _limit_or_404(db: Session, limit_id: int) -> AbsoluteLimit:
    limit = threshold_repo.get_limit(db, limit_id)
    if limit is None:
        raise HTTPException(404, "Limit not found")
    return limit


@router.get("/settings", response_model=SettingsRead)
def read_settings(db: Session = Depends(get_db)):
    return threshold_repo.get_settings(db)


@router.put("/settings", response_model=SettingsRead)
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)):
    settings = threshold_repo.get_settings(db)
    settings.moderate_pct = body.moderate_pct
    settings.significant_pct = body.significant_pct
    settings.include_gst_in_turnover = body.include_gst_in_turnover
    db.commit()
    evaluate_all_clients(db)
    db.refresh(settings)
    return settings


@router.get("/limits", response_model=list[LimitRead])
def list_limits(db: Session = Depends(get_db)):
    return threshold_repo.list_limits(db)


@router.post("/limits", response_model=LimitRead, status_code=status.HTTP_201_CREATED)
def create_limit(body: LimitBase, db: Session = Depends(get_db)):
    limit = AbsoluteLimit(**body.model_dump())
    db.add(limit)
    db.commit()
    evaluate_all_clients(db)
    db.refresh(limit)
    return limit


@router.put("/limits/{limit_id}", response_model=LimitRead)
def update_limit(limit_id: int, body: LimitBase, db: Session = Depends(get_db)):
    limit = _limit_or_404(db, limit_id)
    values = body.model_dump()
    if "applies_when" not in body.model_fields_set:
        values.pop("applies_when")  # an update without a rule keeps the existing one
    for key, value in values.items():
        setattr(limit, key, value)
    db.commit()
    evaluate_all_clients(db)
    db.refresh(limit)
    return limit


@router.delete("/limits/{limit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_limit(limit_id: int, db: Session = Depends(get_db)):
    db.delete(_limit_or_404(db, limit_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
