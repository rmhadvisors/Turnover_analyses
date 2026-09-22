"""Orchestrates an import from Tally's JSON export (Master + Transactions files)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.repositories import import_repo, voucher_repo
from app.services import tally_json as tj
from app.services.import_service import dedupe, period_text, refresh_figures_from_vouchers
from app.services.recheck_service import evaluate_client
from app.services.tally_importer import (
    REPORT_PURCHASE,
    REPORT_SALES,
    ImportFormatError,
    ParsedVoucher,
    file_hash,
)

SALES_TYPES = ("sales", "credit_note")


@dataclass
class Analysis:
    files: list[dict]
    result: tj.JsonParseResult
    warnings: list[str]
    digest: str


def analyze(files: list[tuple[str, bytes]]) -> Analysis:
    """Read, classify (master vs transactions) and parse a set of Tally JSON files."""
    if not files:
        raise ImportFormatError("Upload the Master and Transactions JSON files.")
    read = [tj.read_records(content, name) for name, content in files]
    kinds = {rf.file_name: tj.kind_of(rf.records) for rf in read}
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
    master = tj.build_master(masters)
    result = tj.parse_transactions(transactions, master)
    described = [
        {"file_name": rf.file_name, "kind": kinds[rf.file_name], "records": len(rf.records), "truncated": rf.truncated}
        for rf in read
    ]  # fmt: skip
    digest = file_hash("|".join(sorted(file_hash(content) for _, content in files)).encode())
    return Analysis(described, result, tj.describe_warnings(result, master, kinds), digest)


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


def preview_json(db: Session, client_id: int, files: list[tuple[str, bytes]]) -> dict:
    """Dry run: what was found and how much of it is new for this client."""
    analysis = analyze(files)
    fresh, duplicates = dedupe(db, client_id, analysis.result.vouchers)
    return {
        **_base(analysis),
        "would_import": len(fresh),
        "duplicates": duplicates,
        "sample": _sample(fresh),
    }


def _side_period(vouchers: list[ParsedVoucher]) -> str | None:
    if not vouchers:
        return None
    dates: list[date] = [v.voucher_date for v in vouchers]
    return period_text(min(dates), max(dates))


def import_json(db: Session, client_id: int, files: list[tuple[str, bytes]]) -> dict:
    """Store the sales and purchase vouchers (skipping duplicates), refresh the yearly
    figures, re-run every threshold check and log one import per register."""
    analysis = analyze(files)
    parsed = analysis.result.vouchers
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

    affected = {v.fy for v in fresh}
    for fy in affected:
        refresh_figures_from_vouchers(db, client_id, fy)
    raised = evaluate_client(db, client_id, affected)
    db.commit()
    return {
        **_base(analysis),
        "imported": len(fresh),
        "duplicates_skipped": duplicates,
        "import_log_ids": log_ids,
        "fys_affected": sorted(affected),
        "alerts_raised": len(raised),
    }
