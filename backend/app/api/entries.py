from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_client_or_404
from app.database import get_db
from app.models import Client
from app.repositories import client_repo, figures_repo
from app.schemas.entry import FiguresRead, ManualEntryIn, ManualEntryResult
from app.services import entry_service

router = APIRouter(prefix="/entries", tags=["entries"])


@router.post("", response_model=ManualEntryResult)
def save_entry(body: ManualEntryIn, db: Session = Depends(get_db)):
    if client_repo.get_client(db, body.client_id) is None:
        raise HTTPException(404, "Client not found")
    previous, current = body.split()
    raised = entry_service.save_manual_entry(
        db, body.client_id, body.previous_fy, body.current_fy, previous, current
    )
    figures = [
        figures_repo.get_figures(db, body.client_id, fy)
        for fy in (body.previous_fy, body.current_fy)
    ]
    return ManualEntryResult(
        figures=[FiguresRead.model_validate(f) for f in figures],
        alerts_raised=len(raised),
    )


@router.get("/{client_id}", response_model=list[FiguresRead])
def list_entries(client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    return figures_repo.list_figures(db, client.id)
