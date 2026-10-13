"""Undo one import: remove what an uploaded file added, then refresh figures and alerts."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Alert, ImportLog, TdsEntry, Voucher
from app.repositories import figures_repo
from app.services import tds_service
from app.services.import_service import refresh_figures_from_vouchers
from app.services.recheck_service import evaluate_client

# A Tally JSON upload logs its sales and purchase registers separately, moments apart.
SAME_UPLOAD = timedelta(seconds=10)


def upload_logs(db: Session, log: ImportLog) -> list[ImportLog]:
    """The log and its siblings from the same upload (same files, same moment)."""
    if log.file_hash is None:
        return [log]
    query = select(ImportLog).where(
        ImportLog.client_id == log.client_id,
        ImportLog.file_hash == log.file_hash,
        ImportLog.file_name == log.file_name,
        ImportLog.imported_at.between(log.imported_at - SAME_UPLOAD, log.imported_at + SAME_UPLOAD),
    )
    return list(db.scalars(query))


def delete_import(db: Session, log: ImportLog) -> dict:
    """Delete the upload `log` belongs to: its vouchers and TDS lines, then recompute the
    yearly figures of the years it touched and re-run their checks. Manual figures stay.
    A year left with no imported vouchers loses its TDS lines and alerts too."""
    client_id = log.client_id
    logs = upload_logs(db, log)
    ids = [row.id for row in logs]
    fys = set(db.scalars(select(Voucher.fy).where(Voucher.import_log_id.in_(ids)).distinct()))
    fys |= set(db.scalars(select(TdsEntry.fy).where(TdsEntry.import_log_id.in_(ids)).distinct()))

    removed = db.execute(delete(Voucher).where(Voucher.import_log_id.in_(ids))).rowcount or 0
    db.execute(delete(TdsEntry).where(TdsEntry.import_log_id.in_(ids)))
    db.execute(delete(ImportLog).where(ImportLog.id.in_(ids)))
    db.flush()

    emptied: set[str] = set()
    for fy in fys:
        still_imported = db.scalar(
            select(Voucher.id)
            .where(Voucher.client_id == client_id, Voucher.fy == fy, Voucher.import_log_id.is_not(None))
            .limit(1)
        )
        if still_imported is None:
            emptied.add(fy)
            # TDS lines stored before import logs were linked carry no log id
            db.execute(delete(TdsEntry).where(TdsEntry.client_id == client_id, TdsEntry.fy == fy))
        figures = figures_repo.get_figures(db, client_id, fy)
        if figures is None or figures.is_manual:
            continue
        figures.turnover = figures.purchases = None
        if still_imported is None and figures.profit_source == "tally_json":
            figures.gross_profit = figures.net_profit = figures.profit_source = None
        db.flush()
        refresh_figures_from_vouchers(db, client_id, fy)
        if all(getattr(figures, f) is None for f in ("turnover", "purchases", "gross_profit", "net_profit")):
            db.delete(figures)
    db.flush()

    for fy in emptied:
        if figures_repo.get_figures(db, client_id, fy) is None:
            db.execute(delete(Alert).where(Alert.client_id == client_id, Alert.fy == fy))
    evaluate_client(db, client_id, fys)
    tds_service.recheck_client(db, client_id, fys)
    db.commit()
    return {"import_log_ids": ids, "vouchers_removed": removed, "fys_affected": sorted(fys)}
