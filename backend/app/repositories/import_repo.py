"""Database access for import logs and saved column mappings."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ColumnMapping, ImportLog
from app.services.tally_importer import Mapping


def create_log(db: Session, **fields) -> ImportLog:
    log = ImportLog(**fields)
    db.add(log)
    db.flush()
    return log


def file_already_imported(db: Session, client_id: int, report_type: str, digest: str) -> bool:
    query = select(ImportLog.id).where(
        ImportLog.client_id == client_id,
        ImportLog.report_type == report_type,
        ImportLog.file_hash == digest,
        ImportLog.rows_imported > 0,
    )
    return db.scalar(query.limit(1)) is not None


def list_logs(db: Session, client_id: int | None = None) -> list[ImportLog]:
    query = select(ImportLog).order_by(ImportLog.id.desc())
    if client_id is not None:
        query = query.where(ImportLog.client_id == client_id)
    return list(db.scalars(query))


def _mapping_row(db: Session, client_id: int, report_type: str) -> ColumnMapping | None:
    return db.scalar(
        select(ColumnMapping).where(
            ColumnMapping.client_id == client_id,
            ColumnMapping.report_type == report_type,
        )
    )


def get_mapping(db: Session, client_id: int, report_type: str) -> Mapping | None:
    row = _mapping_row(db, client_id, report_type)
    return json.loads(row.mapping_json) if row else None


def save_mapping(db: Session, client_id: int, report_type: str, mapping: Mapping) -> None:
    row = _mapping_row(db, client_id, report_type)
    if row is None:
        row = ColumnMapping(client_id=client_id, report_type=report_type, mapping_json="{}")
        db.add(row)
    row.mapping_json = json.dumps(mapping)
    db.flush()
