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
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import IO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client, ClientProfile
from app.repositories import (
    client_repo,
    figures_repo,
    import_repo,
    profile_repo,
    threshold_repo,
    voucher_repo,
)
from app.services import tally_json as tj
from app.services import tds_capture, tds_service
from app.services.fy_utils import fy_bounds, fy_label
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
    master_company_ids: Counter  # Tally company GUID -> records in the Master file(s)


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
    return Analysis(
        described, result, warnings, digest, profits, ledger_book, master.company_ids
    )


# ------------------------------------------------------- do the files belong together?

UNMATCHED_LIST_LIMIT = 50


def _main(counter: Counter) -> str | None:
    return counter.most_common(1)[0][0] if counter else None


def _client_for_company(db: Session, company: str | None, gstin: str | None) -> Client | None:
    """The client whose Tally exports carry this company GUID (else this GSTIN)."""
    if company:
        client = db.scalar(select(Client).where(Client.tally_company_id == company))
        if client is not None:
            return client
    if gstin:
        profile = db.scalar(select(ClientProfile).where(ClientProfile.gstin == gstin))
        if profile is not None:
            return client_repo.get_client(db, profile.client_id)
    return None


def _company_label(db: Session, company: str | None, gstin: str | None = None) -> str:
    """e.g. 'HOTEL KINARA (GSTIN 27AAKFH4657G1Z4, Tally company 116eb1a3...)'."""
    client = _client_for_company(db, company, gstin)
    if gstin is None and client is not None and client.profile is not None:
        gstin = client.profile.gstin
    details = [f"GSTIN {gstin}"] if gstin else []
    if company:
        details.append(f"Tally company {company[:8]}…")
    name = client.name if client else "a company not yet loaded here"
    return f"{name} ({', '.join(details)})" if details else name


def check_pairing(
    db: Session,
    client_id: int | None,
    analysis: Analysis,
    expected_gstin: str | None = None,
    expected_pan: str | None = None,
) -> tuple[list[str], list[str]]:
    """(blockers, warnings) on whether the files belong together and to this client.

    Every Tally object guid starts with its company's GUID, so a Master and a Transactions
    file from different companies are told apart reliably; the company GSTIN on the
    vouchers is a second check against the client's GSTIN / PAN. Even from the same
    company, a Master that lacks ledgers carrying more than the configured share of the
    vouchers' value is older than the Transactions file and gives wrong figures."""
    result = analysis.result
    blockers: list[str] = []
    warnings: list[str] = []
    client = client_repo.get_client(db, client_id) if client_id is not None else None
    profile = profile_repo.get_profile(db, client_id) if client_id is not None else None
    tx_company, master_company = _main(result.company_ids), _main(analysis.master_company_ids)
    tx_gstin = _main(result.gstins)

    for counter, what in (
        (result.company_ids, "Transactions"),
        (analysis.master_company_ids, "Master"),
    ):
        if len(counter) > 1:
            names = "; ".join(_company_label(db, c) for c in counter)
            blockers.append(
                f"The {what} files come from {len(counter)} different Tally companies "
                f"({names}). Upload files from one company only."
            )
    if tx_company and master_company and tx_company != master_company:
        blockers.append(
            "The Master file and the Transactions file are from different Tally companies. "
            f"Master file: {_company_label(db, master_company)}. "
            f"Transactions file: {_company_label(db, tx_company, tx_gstin)}. "
            "Export both files from the same company in Tally."
        )

    gstin = expected_gstin or (profile.gstin if profile else None)
    pan = expected_pan or (profile.pan if profile else None) or (gstin[2:12] if gstin else None)
    whose = client.name if client else "the client details entered"
    if tx_gstin and gstin and tx_gstin.upper() != gstin.upper():
        blockers.append(
            f"The Transactions file is for GSTIN {tx_gstin} "
            f"({_company_label(db, tx_company, tx_gstin)}), but {whose} has GSTIN {gstin}."
        )
    elif tx_gstin and pan and tx_gstin[2:12].upper() != pan.upper():
        blockers.append(
            f"The Transactions file is for GSTIN {tx_gstin} (PAN {tx_gstin[2:12]}), "
            f"but {whose} has PAN {pan}."
        )

    owner = _client_for_company(db, tx_company, None) if tx_company else None
    if owner is not None and client is not None and owner.id != client.id:
        blockers.append(
            f"These files are from the Tally company already imported for {owner.name}, "
            f"not {client.name}. Add them to {owner.name} instead."
        )
    elif owner is not None and client is None:
        warnings.append(
            f"These files are from the Tally company already imported for {owner.name}. "
            f"To add a year to it, use 'Add data' on {owner.name} instead of a new client."
        )
    elif (
        client is not None
        and client.tally_company_id
        and tx_company
        and tx_company != client.tally_company_id
    ):
        warnings.append(
            f"These files are from a different Tally company ({tx_company[:8]}…) than the "
            f"files imported for {client.name} before ({client.tally_company_id[:8]}…). "
            "That is expected only if the company was split or re-created in Tally."
        )

    limit = threshold_repo.get_settings(db).max_unmatched_ledger_pct
    share = result.unknown_share_pct()
    if share > limit and not any("different Tally companies" in b for b in blockers):
        blockers.append(
            "This Master looks older than the Transactions file — re-export the Master for "
            f"the same period. {share}% of the vouchers' value "
            f"({sum(result.unknown_ledger_lines.values()):,} line(s), "
            f"₹{result.unknown_value:,.2f}) is on {len(result.unknown_ledgers)} ledger(s) "
            f"missing from the Master; imports are refused above {limit}% (Settings)."
        )
    return blockers, warnings


def _unmatched(analysis: Analysis) -> list[dict]:
    result = analysis.result
    rows = sorted(
        result.unknown_ledgers, key=lambda name: (-result.unknown_ledger_amounts[name], name)
    )
    return [
        {
            "name": name,
            "lines": result.unknown_ledger_lines[name],
            "amount": result.unknown_ledger_amounts[name].quantize(Decimal("0.01")),
        }
        for name in rows[:UNMATCHED_LIST_LIMIT]
    ]


def _month_coverage(analysis: Analysis, today: date | None = None) -> list[dict]:
    """Per FY in the files: months with posted vouchers and months with none (up to today)."""
    today = today or date.today()
    months = analysis.result.months
    out = []
    for fy in sorted({fy_label(date(y, m, 1)) for y, m in months}):
        start, end = fy_bounds(fy)
        covered, missing = [], []
        year, month = start.year, start.month
        while date(year, month, 1) <= min(end, today):
            label = f"{date(year, month, 1):%b-%Y}"
            (covered if (year, month) in months else missing).append(label)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        out.append({"fy": fy, "covered": covered, "missing": missing})
    return out


def _fy_vouchers(analysis: Analysis) -> dict[str, int]:
    """Posted vouchers (any type) per FY, for the detected year."""
    return dict(Counter(fy_label(v.when) for v in analysis.result.ledger_vouchers))


# Parsed previews waiting to be confirmed: token -> (client_id, created, Analysis).
_PREVIEW_TTL_SECONDS = 3600
_PREVIEW_LIMIT = 8
_previews: OrderedDict[str, tuple[int | None, float, Analysis]] = OrderedDict()
_lock = threading.Lock()


def _remember(client_id: int | None, analysis: Analysis) -> str:
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
    # a preview made before the client existed (Add client) can be used by any client
    if item is None or item[0] not in (None, client_id):
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
    db: Session,
    client_id: int | None,
    files: list[Upload],
    progress_token: str | None = None,
    expected_gstin: str | None = None,
    expected_pan: str | None = None,
) -> dict:
    """Dry run: what was found and how much of it is new for this client (None: a client
    not created yet). The result carries a `preview_token` that `import_json` accepts
    instead of the files, and `blockers`: reasons the files must not be imported."""
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
    stored = voucher_repo.count_imported_by_fy(db, client_id) if client_id is not None else {}
    blockers, pairing_warnings = check_pairing(
        db, client_id, analysis, expected_gstin, expected_pan
    )
    fy_vouchers = _fy_vouchers(analysis)
    result = analysis.result
    tx_company, tx_gstin = _main(result.company_ids), _main(result.gstins)
    owner = _client_for_company(db, tx_company, tx_gstin)
    base = _base(analysis)
    base["warnings"] = pairing_warnings + base["warnings"]
    return {
        **base,
        "blockers": blockers,
        "can_import": not blockers,
        "company": {
            "tally_company_id": tx_company,
            "master_company_id": _main(analysis.master_company_ids),
            "gstin": tx_gstin,
            "client_name": owner.name if owner else None,
        },
        "detected_fy": (
            max(fy_vouchers, key=lambda fy: (fy_vouchers[fy], fy)) if fy_vouchers else None
        ),
        "fy_vouchers": fy_vouchers,
        "first_date": result.first_date,
        "last_date": result.last_date,
        "months": _month_coverage(analysis),
        "unmatched_ledgers": _unmatched(analysis),
        "unmatched_ledger_count": len(result.unknown_ledgers),
        "unmatched_lines": sum(result.unknown_ledger_lines.values()),
        "unmatched_amount": result.unknown_value.quantize(Decimal("0.01")),
        "unmatched_share_pct": result.unknown_share_pct(),
        "unmatched_limit_pct": threshold_repo.get_settings(db).max_unmatched_ledger_pct,
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
    blockers, _ = check_pairing(db, client_id, analysis)
    if blockers:
        raise ImportFormatError(" ".join(blockers))
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
    client = client_repo.get_client(db, client_id)
    tx_company = _main(analysis.result.company_ids)
    if client is not None and client.tally_company_id is None and tx_company:
        client.tally_company_id = tx_company
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
