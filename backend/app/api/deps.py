"""Shared router helpers."""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Client
from app.repositories import client_repo


def get_client_or_404(client_id: int, db: Session = Depends(get_db)) -> Client:
    client = client_repo.get_client(db, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client
