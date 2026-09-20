"""Database access for vouchers."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
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


def _amounts(rows) -> list[VoucherAmount]:
    return [
        VoucherAmount(
            v.voucher_type.value,
            v.voucher_date,
            v.voucher_no,
            v.taxable_value,
            v.total_value,
        )
        for v in rows
    ]


def amounts_for_range(db: Session, client_id: int, start: date, end: date) -> list[VoucherAmount]:
    """Vouchers dated within [start, end], in date order, as plain amounts."""
    query = (
        select(Voucher)
        .where(Voucher.client_id == client_id, Voucher.voucher_date.between(start, end))
        .order_by(Voucher.voucher_date, Voucher.id)
    )
    return _amounts(db.scalars(query))


def amounts_for_fy(db: Session, client_id: int, fy: str) -> list[VoucherAmount]:
    query = (
        select(Voucher)
        .where(Voucher.client_id == client_id, Voucher.fy == fy)
        .order_by(Voucher.voucher_date, Voucher.id)
    )
    return _amounts(db.scalars(query))


def fys_with_vouchers(db: Session, client_id: int) -> list[str]:
    return sorted(set(db.scalars(select(Voucher.fy).where(Voucher.client_id == client_id))))
