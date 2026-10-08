"""Database access for vouchers."""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Voucher, VoucherType
from app.services.tally_importer import ParsedVoucher
from app.services.turnover import VoucherAmount


def existing_dedup_keys(db: Session, client_id: int) -> set[str]:
    return set(db.scalars(select(Voucher.dedup_key).where(Voucher.client_id == client_id)))


def add_many(db: Session, client_id: int, import_log_id: int, parsed: list[ParsedVoucher]) -> None:
    db.add_all(
        Voucher(
            client_id=client_id,
            import_log_id=import_log_id,
            voucher_type=VoucherType(p.voucher_type),
            voucher_date=p.voucher_date,
            voucher_no=p.voucher_no,
            party=p.party,
            taxable_value=p.taxable_value,
            tax_value=p.tax_value,
            total_value=p.total_value,
            fy=p.fy,
            dedup_key=p.dedup_key,
        )
        for p in parsed
    )
    db.flush()


# Only the columns the turnover calculations use: loading full ORM objects was most
# of the time spent by the report endpoints.
_AMOUNT_COLUMNS = (
    Voucher.voucher_type,
    Voucher.voucher_date,
    Voucher.voucher_no,
    Voucher.taxable_value,
    Voucher.total_value,
    Voucher.party,
)


def _amounts(query) -> list[VoucherAmount]:
    return [
        VoucherAmount(voucher_type.value, voucher_date, voucher_no, taxable, total, party)
        for voucher_type, voucher_date, voucher_no, taxable, total, party in query
    ]


def amounts_for_range(db: Session, client_id: int, start: date, end: date) -> list[VoucherAmount]:
    """Vouchers dated within [start, end], in date order, as plain amounts."""
    query = (
        select(*_AMOUNT_COLUMNS)
        .where(Voucher.client_id == client_id, Voucher.voucher_date.between(start, end))
        .order_by(Voucher.voucher_date, Voucher.id)
    )
    return _amounts(db.execute(query))


def amounts_for_fy(db: Session, client_id: int, fy: str) -> list[VoucherAmount]:
    query = (
        select(*_AMOUNT_COLUMNS)
        .where(Voucher.client_id == client_id, Voucher.fy == fy)
        .order_by(Voucher.voucher_date, Voucher.id)
    )
    return _amounts(db.execute(query))


def fys_with_vouchers(db: Session, client_id: int) -> list[str]:
    return sorted(db.scalars(select(Voucher.fy).where(Voucher.client_id == client_id).distinct()))


def counts_by_client_fy(db: Session) -> list[tuple[int, str, str, int]]:
    """(client_id, fy, voucher_type, count) for every client."""
    query = select(Voucher.client_id, Voucher.fy, Voucher.voucher_type, func.count()).group_by(
        Voucher.client_id, Voucher.fy, Voucher.voucher_type
    )
    return [(cid, fy, vtype.value, n) for cid, fy, vtype, n in db.execute(query)]


def count_imported_by_fy(db: Session, client_id: int) -> dict[str, int]:
    """Imported vouchers stored for this client, per FY."""
    query = (
        select(Voucher.fy, func.count())
        .where(Voucher.client_id == client_id, Voucher.import_log_id.is_not(None))
        .group_by(Voucher.fy)
    )
    return dict(db.execute(query).all())


def delete_imported_for_fy(db: Session, client_id: int, fy: str) -> int:
    """Remove this client's imported vouchers for one FY (used by 'replace'). Returns the count."""
    result = db.execute(
        delete(Voucher).where(
            Voucher.client_id == client_id,
            Voucher.fy == fy,
            Voucher.import_log_id.is_not(None),
        )
    )
    return result.rowcount or 0


def all_fys(db: Session) -> set[str]:
    """Every FY with vouchers for any client."""
    return set(db.scalars(select(Voucher.fy).distinct()))
