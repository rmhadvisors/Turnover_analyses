"""Orchestrates an import from Tally's JSON export (Master + Transactions files).

Files are parsed as streams, so large exports are never loaded whole. A preview keeps
its parsed result for a while under a token, so confirming does not need the files
uploaded and parsed a second time.
"""

from __future__ import annotations

import hashlib
import io
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from typing import IO

from sqlalchemy.orm import Session

from app.repositories import figures_repo, import_repo, profile_repo, voucher_repo
from app.services import tally_json as tj
from app.services import tds_capture, tds_service
from app.services.import_service import dedupe, period_text, refresh_figures_from_vouchers
from app.services.recheck_service import evaluate_client, fys_with_data
from app.services.tally_importer import (
    REPORT_PURCHASE,
    REPORT_SALES,
    ImportFormatError,
    ParsedVoucher,
    file_hash,
)

Upload = tuple[str, bytes | IO[bytes]]  # (file name, content or a seekable binary file)

SALES_TYPES = ("sales", "credit_note")


@dataclass
class Analysis:
    files: list[dict]
    result: tj.JsonParseResult
    warnings: list[str]
    digest: str
    profits: list[tj.ProfitFigures]
    ledger_book: tds_capture.LedgerBook


def _stream_hash(raw: IO[bytes]) -> str:
    """Same value as `file_hash(content)`, without holding the content in memory."""
    digest = hashlib.sha256()
    raw.seek(0)
    for chunk in iter(lambda: raw.read(1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _size(raw: IO[bytes]) -> int:
    raw.seek(0, io.SEEK_END)
    size = raw.tell()
    raw.seek(0)
    return size


class Progress:
    """Share of the uploaded bytes parsed so far, readable from another request."""

    def __init__(self) -> None:
        self.total = 0
        self.done = 0
        self.stage = "reading"

    def add(self, count: int) -> None:
        self.done += count

    def as_dict(self) -> dict:
        fraction = min(self.done / self.total, 1.0) if self.total else 0.0
        return {"stage": self.stage, "fraction": round(fraction, 3)}


_progress: dict[str, Progress] = {}


def progress_for(token: str) -> dict | None:
    item = _progress.get(token)
    return item.as_dict() if item else None


def analyze(files: list[Upload], progress: Progress | None = None) -> Analysis:
    """Read, classify (master vs transactions) and parse a set of Tally JSON files."""
    if not files:
        raise ImportFormatError("Upload the Master and Transactions JSON files.")
    progress = progress or Progress()
    raws = [(name, io.BytesIO(c) if isinstance(c, bytes) else c) for name, c in files]
    # Master and Transactions are each read in full once; that is what progress measures.
    progress.total = sum(_size(raw) for _, raw in raws)
    read = [tj.RecordsStream(raw, name, progress.add) for name, raw in raws]
    kinds = {rs.file_name: tj.kind_of(rs.head(200)) for rs in read}
    unknown = [name for name, kind in kinds.items() if kind == "unknown"]
    if unknown:
        raise ImportFormatError(
            f"Could not tell what '{unknown[0]}' contains - expected Tally Master (ledgers and "
            f"groups) or Transactions (vouchers) JSON."
        )
    masters = [rf for rf in read if kinds[rf.file_name] == "master"]
    transactions = [rf for rf in read if kinds[rf.file_name] == "transactions"]
    if not masters:
        raise ImportFormatError(
            "The Master file is missing. Upload it together with the Transactions file: it tells "
            "the tool which ledgers are sales, purchases and GST."
        )
    if not transactions:
        raise ImportFormatError("The Transactions file is missing.")
    progress.stage = "parsing"
    master = tj.build_master(masters)
    ledger_book = tds_capture.read_ledgers(masters, master)
    result = tj.parse_transactions(transactions, master)
    described = [
        {"file_name": rf.file_name, "kind": kinds[rf.file_name], "records": rf.count, "truncated": rf.truncated}
        for rf in read
    ]  # fmt: skip
    progress.stage = "done"  # parsing finished; hashing and duplicate checks remain
    digest = file_hash("|".join(sorted(_stream_hash(raw) for _, raw in raws)).encode())
    warnings = tj.describe_warnings(result, master, kinds)
    profits = tj.derive_profits(result, master)
    return Analysis(described, result, warnings, digest, profits, ledger_book)


# Parsed previews waiting to be confirmed: token -> (client_id, created, Analysis).
_PREVIEW_TTL_SECONDS = 3600
_PREVIEW_LIMIT = 8
_previews: OrderedDict[str, tuple[int, float, Analysis]] = OrderedDict()
_lock = threading.Lock()


def _remember(client_id: int, analysis: Analysis) -> str:
    token = uuid.uuid4().hex
    with _lock:
        now = time.monotonic()
        for key in [
            k for k, (_, made, _a) in _previews.items() if now - made > _PREVIEW_TTL_SECONDS
        ]:
            del _previews[key]
        _previews[token] = (client_id, now, analysis)
        while len(_previews) > _PREVIEW_LIMIT:
            _previews.popitem(last=False)
    return token


def _recall(client_id: int, token: str) -> Analysis:
    with _lock:
        item = _previews.pop(token, None)
    if item is None or item[0] != client_id:
        raise ImportFormatError(
            "This preview has expired. Select the files again to re-check them."
        )
    return item[2]


def _by_fy(vouchers: list[ParsedVoucher]) -> list[dict]:
    totals = tj.summarize(vouchers)
    return [{"fy": fy, **row} for fy, row in sorted(totals.items())]


def _sample(vouchers: list[ParsedVoucher], size: int = 10) -> list[dict]:
    return [
        {
            "date": v.voucher_date,
            "voucher_no": v.voucher_no,
            "party": v.party,
            "type": v.voucher_type,
            "taxable_value": v.taxable_value,
            "tax_value": v.tax_value,
            "total_value": v.total_value,
            "fy": v.fy,
        }
        for v in vouchers[:size]
    ]


def _base(analysis: Analysis) -> dict:
    result = analysis.result
    period = (
        period_text(result.first_date, result.last_date)
        if result.first_date and result.last_date
        else None
    )
    return {
        "files": analysis.files,
        "period": period,
        "company_gstins": list(result.gstins),
        "vouchers_read": result.total_vouchers,
        "excluded": dict(result.excluded),
        "other_vouchers": result.other_vouchers,
        "sales_records": sum(v.voucher_type in SALES_TYPES for v in result.vouchers),
        "purchase_records": sum(v.voucher_type not in SALES_TYPES for v in result.vouchers),
        "by_fy": _by_fy(result.vouchers),
        "warnings": analysis.warnings,
    }


def _profit_rows(db: Session, client_id: int, analysis: Analysis, stored: set[str] | None = None):
    """Derived GP / NP per FY for the preview (`stored` None) or the import result."""
    rows = []
    for p in analysis.profits:
        existing = figures_repo.get_figures(db, client_id, p.fy)
        kept = existing is not None and existing.profit_source in figures_repo.PROFIT_SOURCES_KEPT
        kept = kept and (existing.gross_profit is not None or existing.net_profit is not None)
        notes = list(p.notes)
        if kept and p.gross_profit is not None:
            notes.append(
                f"Existing {existing.profit_source.replace('_', ' ')} profit figures are kept."
            )
        rows.append(
            {
                "fy": p.fy,
                "gross_profit": p.gross_profit,
                "net_profit": p.net_profit,
                "opening_stock": p.opening_stock,
                "closing_stock": p.closing_stock,
                "will_store": p.gross_profit is not None and not kept,
                "stored": p.fy in stored if stored is not None else False,
                "notes": notes,
            }
        )
    return rows


def preview_json(
    db: Session, client_id: int, files: list[Upload], progress_token: str | None = None
) -> dict:
    """Dry run: what was found and how much of it is new for this client. The result
    carries a `preview_token` that `import_json` accepts instead of the files."""
    progress = Progress()
    if progress_token:
        _progress[progress_token] = progress
    try:
        analysis = analyze(files, progress)
    finally:
        if progress_token:
            _progress.pop(progress_token, None)
    fresh, duplicates = dedupe(db, client_id, analysis.result.vouchers)
    fys = sorted({v.fy for v in analysis.result.vouchers})
    stored = voucher_repo.count_imported_by_fy(db, client_id)
    return {
        **_base(analysis),
        "would_import": len(fresh),
        "duplicates": duplicates,
        "sample": _sample(fresh),
        "fys_already_imported": [fy for fy in fys if stored.get(fy)],
        "profits": _profit_rows(db, client_id, analysis),
        "existing_vouchers": {fy: stored[fy] for fy in fys if stored.get(fy)},
        "preview_token": _remember(client_id, analysis),
    }


def _side_period(vouchers: list[ParsedVoucher]) -> str | None:
    if not vouchers:
        return None
    dates: list[date] = [v.voucher_date for v in vouchers]
    return period_text(min(dates), max(dates))


def import_json(
    db: Session,
    client_id: int,
    files: list[Upload] | None = None,
    preview_token: str | None = None,
    replace_fys: set[str] | frozenset[str] = frozenset(),
    skip_fys: set[str] | frozenset[str] = frozenset(),
) -> dict:
    """Store the sales and purchase vouchers (skipping duplicates), refresh the yearly
    figures, re-run every threshold check and log one import per register. Pass either
    the files or the `preview_token` of an earlier preview.

    Per FY found in the files: by default only vouchers not stored yet are added;
    `skip_fys` leaves those years untouched; `replace_fys` first deletes that year's
    previously imported vouchers, so the files become the year's only imported data."""
    if set(replace_fys) & set(skip_fys):
        raise ImportFormatError("A financial year cannot be both replaced and skipped.")
    analysis = _recall(client_id, preview_token) if preview_token else analyze(files or [])
    parsed = [v for v in analysis.result.vouchers if v.fy not in skip_fys]
    in_files = {v.fy for v in parsed}
    replaced: dict[str, int] = {}
    for fy in sorted(set(replace_fys) & in_files):
        replaced[fy] = voucher_repo.delete_imported_for_fy(db, client_id, fy)
        figures = figures_repo.get_figures(db, client_id, fy)
        if figures is not None and not figures.is_manual:
            figures.turnover = figures.purchases = None  # recomputed from the new vouchers
    db.flush()
    fresh, duplicates = dedupe(db, client_id, parsed)
    fresh_ids = {id(v) for v in fresh}  # identity, so in-file duplicates are never re-added
    file_names = ", ".join(f["file_name"] for f in analysis.files)
    if analysis.result.truncated_files:
        file_names += " [cut off]"
    file_names = file_names[:255]

    log_ids: list[int] = []
    for register, is_side in (
        (REPORT_SALES, lambda v: v.voucher_type in SALES_TYPES),
        (REPORT_PURCHASE, lambda v: v.voucher_type not in SALES_TYPES),
    ):
        side_all = [v for v in parsed if is_side(v)]
        if not side_all:
            continue
        side_fresh = [v for v in side_all if id(v) in fresh_ids]
        log = import_repo.create_log(
            db,
            client_id=client_id,
            file_name=file_names,
            report_type=register,
            period=_side_period(side_all),
            file_hash=analysis.digest,
            rows_imported=len(side_fresh),
            rows_skipped=len(side_all) - len(side_fresh),
        )
        voucher_repo.add_many(db, client_id, log.id, side_fresh)
        log_ids.append(log.id)

    affected = {v.fy for v in fresh} | set(replaced)
    for fy in affected:
        refresh_figures_from_vouchers(db, client_id, fy)
    profit_stored = {
        p.fy
        for p in analysis.profits
        if p.gross_profit is not None
        and p.fy not in skip_fys
        and figures_repo.store_derived_profit(db, client_id, p.fy, p.gross_profit, p.net_profit)
    }
    affected |= profit_stored
    if len(analysis.result.gstins) == 1:  # one company GSTIN: fill the empty profile facts
        (gstin,) = analysis.result.gstins
        if profile_repo.fill_from_gstin(db, client_id, gstin):
            affected |= fys_with_data(db, client_id)  # applicable limits may have changed
    raised = evaluate_client(db, client_id, affected)
    # ledger-level data for the TDS analysis (separate from the turnover vouchers)
    captured = tds_capture.store(
        db, client_id, analysis.ledger_book, analysis.result.ledger_vouchers,
        skip_fys, replace_fys, log_ids[0] if log_ids else None,
    )  # fmt: skip
    tds_raised = tds_service.recheck_client(db, client_id, set(captured.fys))
    db.commit()
    return {
        **_base(analysis),
        "imported": len(fresh),
        "duplicates_skipped": duplicates,
        "import_log_ids": log_ids,
        "fys_affected": sorted(affected),
        "alerts_raised": len(raised) + len(tds_raised),
        "tds_lines_captured": captured.lines_added,
        "replaced": replaced,
        "profits": _profit_rows(db, client_id, analysis, profit_stored),
        "skipped_fys": sorted(set(skip_fys)),
    }
