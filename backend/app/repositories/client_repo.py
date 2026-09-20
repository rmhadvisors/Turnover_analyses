"""Database access for clients."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client


def list_clients(db: Session) -> list[Client]:
    return list(db.scalars(select(Client).order_by(Client.name)))


def get_client(db: Session, client_id: int) -> Client | None:
    return db.get(Client, client_id)


def get_by_name(db: Session, name: str) -> Client | None:
    return db.scalar(select(Client).where(Client.name == name))


def create_client(db: Session, name: str) -> Client:
    client = Client(name=name)
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def rename_client(db: Session, client: Client, name: str) -> Client:
    client.name = name
    db.commit()
    db.refresh(client)
    return client


def delete_client(db: Session, client: Client) -> None:
    db.delete(client)
    db.commit()
