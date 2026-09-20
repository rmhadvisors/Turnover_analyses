"""Orchestrates a Tally import: parse -> de-duplicate -> store -> refresh -> re-check."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.repositories import figures_repo, import_repo, threshold_repo, voucher_repo
from app.services import tally_importer as ti
from app.services.recheck_service import evaluate_client
from app.services.turnover import PURCHASE_TYPES, SALES_TYPES, net_purchases, net_sales


def _period_text(start: date, end: date) -> str:
    return f"{start:%d-%b-%Y} to {end:%d-%b-%Y}"


def _dedupe(db: Session, client_id: int, vouchers: list[ti.ParsedVoucher]):
    """Split parsed vouchers into (new, duplicate_count) against the database and
    against repeats inside the same file."""
    seen = voucher_repo.existing_dedup_keys(db, client_id)
    fresh, duplicates = [], 0
    for voucher in vouchers:
        if voucher.dedup_key in seen:
            duplicates += 1
        else:
            seen.add(voucher.dedup_key)
            fresh.append(voucher)
    return fresh, duplicates


def _resolve_mapping(db, client_id, report_type, headers, mapping):
    if mapping:
        return mapping, "provided"
    saved = import_repo.get_mapping(db, client_id, report_type)
    if saved:
        return saved, "saved"
    return ti.suggest_mapping(headers), "suggested"


def preview_file(
    db: Session,
    client_id: int,
    report_type: str,
    filename: str,
    content: bytes,
    mapping: ti.Mapping | None = None,
    sample_size: int = 10,
) -> dict:
    """Dry run: detect the header, resolve the mapping and show what would be imported."""
    rows = ti.read_table(content, filename)
    result: dict = {"report_type": report_type, "file_name": filename, "error": None}

    if report_type == ti.REPORT_PL:
        figures = ti.parse_profit_loss(rows)
        result.update(
            profit_loss={
                "gross_profit": figures.gross_profit,
                "net_profit": figures.net_profit,
                "fy": figures.fy,
            }
        )
        return result

    header_index = ti.detect_header_row(rows)
    headers = [h for h in ti.header_labels(rows[header_index]) if h]
    used, source = _resolve_mapping(db, client_id, report_type, headers, mapping)
    result.update(
        header_row=header_index + 1,
        headers=headers,
        mapping=used,
        mapping_source=source,
        suggested_mapping=ti.suggest_mapping(headers),
        raw_rows=[
            [str(c) if c is not None else "" for c in r]
            for r in rows[header_index + 1 : header_index + 1 + sample_size]
        ],
    )
    try:
        parsed = ti.parse_vouchers(rows, header_index, used, report_type)
    except ti.ImportFormatError as exc:
        result["error"] = str(exc)
        return result
    fresh, duplicates = _dedupe(db, client_id, parsed.vouchers)
    result.update(
        rows_found=len(parsed.vouchers),
        would_import=len(fresh),
        duplicates=duplicates,
        invalid=parsed.invalid[:20],
        invalid_count=len(parsed.invalid),
        totals_ignored=parsed.totals_ignored,
        period=_period_text(*parsed.period) if parsed.period else None,
        parsed_sample=[
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
            for v in fresh[:sample_size]
        ],
    )
    return result


def refresh_figures_from_vouchers(db: Session, client_id: int, fy: str) -> None:
    """Set the FY's turnover / purchases from its vouchers (only for sides that have any)."""
    include_gst = threshold_repo.get_settings(db).include_gst_in_turnover
    vouchers = voucher_repo.amounts_for_fy(db, client_id, fy)
    values = {}
    if any(v.voucher_type in SALES_TYPES for v in vouchers):
        values["turnover"] = net_sales(vouchers, include_gst)
    if any(v.voucher_type in PURCHASE_TYPES for v in vouchers):
        values["purchases"] = net_purchases(vouchers, include_gst)
    if values:
        figures_repo.upsert_figures(db, client_id, fy, values, is_manual=False)


def import_file(
    db: Session,
    client_id: int,
    report_type: str,
    filename: str,
    content: bytes,
    mapping: ti.Mapping | None = None,
    fy: str | None = None,
    save_mapping: bool = False,
) -> dict:
    """Import one Tally export. Duplicates (same file or same vouchers) are skipped
    and counted; every import is logged; all checks are re-run afterwards."""
    digest = ti.file_hash(content)
    rows = ti.read_table(content, filename)
    if report_type == ti.REPORT_PL:
        return _import_profit_loss(db, client_id, filename, rows, digest, fy)

    header_index = ti.detect_header_row(rows)
    headers = [h for h in ti.header_labels(rows[header_index]) if h]
    used, _ = _resolve_mapping(db, client_id, report_type, headers, mapping)
    parsed = ti.parse_vouchers(rows, header_index, used, report_type)

    duplicate_file = import_repo.file_already_imported(db, client_id, report_type, digest)
    fresh, duplicates = _dedupe(db, client_id, parsed.vouchers)
    period = parsed.period
    if period is None and parsed.vouchers:
        dates = [v.voucher_date for v in parsed.vouchers]
        period = (min(dates), max(dates))
    log = import_repo.create_log(
        db,
        client_id=client_id,
        file_name=filename,
        report_type=report_type,
        period=_period_text(*period) if period else None,
        file_hash=digest,
        rows_imported=len(fresh),
        rows_skipped=duplicates + len(parsed.invalid),
    )
    voucher_repo.add_many(db, client_id, log.id, fresh)
    if save_mapping:
        import_repo.save_mapping(db, client_id, report_type, used)

    affected = {v.fy for v in fresh}
    for affected_fy in affected:
        refresh_figures_from_vouchers(db, client_id, affected_fy)
    raised = evaluate_client(db, client_id, affected)
    db.commit()
    return {
        "import_log_id": log.id,
        "report_type": report_type,
        "file_name": filename,
        "period": log.period,
        "rows_found": len(parsed.vouchers),
        "imported": len(fresh),
        "duplicates_skipped": duplicates,
        "invalid_skipped": len(parsed.invalid),
        "invalid": parsed.invalid[:20],
        "totals_ignored": parsed.totals_ignored,
        "duplicate_file": duplicate_file,
        "fys_affected": sorted(affected),
        "alerts_raised": len(raised),
    }


def _import_profit_loss(
    db: Session, client_id: int, filename: str, rows, digest: str, fy: str | None
) -> dict:
    figures = ti.parse_profit_loss(rows)
    target_fy = fy or figures.fy
    if not target_fy:
        raise ti.ImportFormatError(
            "Could not work out the financial year from the file's period - please enter it."
        )
    values = {
        "gross_profit": figures.gross_profit,
        "net_profit": figures.net_profit,
    }
    values = {k: v for k, v in values.items() if v is not None}
    figures_repo.upsert_figures(db, client_id, target_fy, values)
    log = import_repo.create_log(
        db,
        client_id=client_id,
        file_name=filename,
        report_type=ti.REPORT_PL,
        period=f"FY {target_fy}",
        file_hash=digest,
        rows_imported=len(values),
        rows_skipped=0,
    )
    raised = evaluate_client(db, client_id, [target_fy])
    db.commit()
    return {
        "import_log_id": log.id,
        "report_type": ti.REPORT_PL,
        "file_name": filename,
        "period": log.period,
        "rows_found": len(values),
        "imported": len(values),
        "duplicates_skipped": 0,
        "invalid_skipped": 0,
        "invalid": [],
        "totals_ignored": 0,
        "duplicate_file": False,
        "fys_affected": [target_fy],
        "alerts_raised": len(raised),
    }
