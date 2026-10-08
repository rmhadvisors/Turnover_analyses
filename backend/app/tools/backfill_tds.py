"""Capture the TDS ledger-level data for a client from Tally JSON files already imported,
without touching its sales / purchase vouchers or yearly figures.

    python -m app.tools.backfill_tds "CLIENT NAME" Master.json Transactions.json [more.json ...]

Same reading as an import (Master + one or more Transactions files of one company), then
only the TDS tables are written and the client's TDS status is re-evaluated.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

import app.models  # noqa: F401  (registers every table)
from app.database import Base, SessionLocal, engine
from app.migrations import upgrade
from app.models import Client
from app.repositories import tds_repo, threshold_repo
from app.services import tally_json as tj
from app.services import tds_capture, tds_service


def prepare_database() -> None:
    Base.metadata.create_all(engine)
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(engine, checkfirst=True)
    upgrade(engine)
    with SessionLocal() as db:
        threshold_repo.seed_defaults(db)
        tds_repo.seed_sections(db)


def backfill(client_name: str, paths: list[Path]) -> tds_capture.CaptureResult:
    handles = [path.open("rb") for path in paths]
    try:
        streams = [
            tj.RecordsStream(handle, path.name)
            for handle, path in zip(handles, paths, strict=False)
        ]
        kinds = {s.file_name: tj.kind_of(s.head(200)) for s in streams}
        masters = [s for s in streams if kinds[s.file_name] == "master"]
        transactions = [s for s in streams if kinds[s.file_name] == "transactions"]
        if not masters or not transactions:
            raise SystemExit("Give the Master file and at least one Transactions file.")
        master = tj.build_master(masters)
        book = tds_capture.read_ledgers(masters, master)
        result = tj.parse_transactions(transactions, master)
    finally:
        for handle in handles:
            handle.close()
    with SessionLocal() as db:
        client = db.scalar(select(Client).where(Client.name == client_name))
        if client is None:
            raise SystemExit(f"No client named {client_name!r}")
        captured = tds_capture.store(db, client.id, book, result.ledger_vouchers)
        tds_service.recheck_client(db, client.id, set(captured.fys))
        db.commit()
    return captured


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    prepare_database()
    captured = backfill(argv[0], [Path(p) for p in argv[1:]])
    print(
        f"{argv[0]}: FY {', '.join(captured.fys)} - {captured.vouchers_added} vouchers / "
        f"{captured.lines_added} ledger lines added, {captured.ledgers} ledgers, "
        f"{captured.proposals_added} mapping proposals"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
