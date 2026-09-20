from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_client_or_404
from app.database import get_db
from app.models import Client
from app.repositories import client_repo
from app.schemas.client import ClientIn, ClientRead

router = APIRouter(prefix="/clients", tags=["clients"])


def _ensure_name_free(db: Session, name: str, own_id: int | None = None) -> None:
    existing = client_repo.get_by_name(db, name)
    if existing is not None and existing.id != own_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "A client with this name already exists")


@router.get("", response_model=list[ClientRead])
def list_clients(db: Session = Depends(get_db)):
    return client_repo.list_clients(db)


@router.post("", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
def create_client(body: ClientIn, db: Session = Depends(get_db)):
    _ensure_name_free(db, body.name)
    return client_repo.create_client(db, body.name)


@router.get("/{client_id}", response_model=ClientRead)
def read_client(client: Client = Depends(get_client_or_404)):
    return client


@router.put("/{client_id}", response_model=ClientRead)
def rename_client(
    body: ClientIn,
    client: Client = Depends(get_client_or_404),
    db: Session = Depends(get_db),
):
    _ensure_name_free(db, body.name, client.id)
    return client_repo.rename_client(db, client, body.name)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client: Client = Depends(get_client_or_404), db: Session = Depends(get_db)):
    client_repo.delete_client(db, client)
