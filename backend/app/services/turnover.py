"""Pure turnover aggregation over voucher amounts (no database access).

Sales turnover = sales - credit notes / sales returns.
Purchase turnover = purchases - debit notes / purchase returns.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

SALES_TYPES = {"sales": Decimal(1), "credit_note": Decimal(-1)}
PURCHASE_TYPES = {"purchase": Decimal(1), "debit_note": Decimal(-1)}


@dataclass(frozen=True)
class VoucherAmount:
    voucher_type: str
    voucher_date: date
    voucher_no: str
    taxable: Decimal
    total: Decimal


def _basis(v: VoucherAmount, include_gst: bool) -> Decimal:
    return v.total if include_gst else v.taxable


def _net(vouchers: Iterable[VoucherAmount], signs: dict[str, Decimal], include_gst: bool):
    return sum(
        (
            signs[v.voucher_type] * _basis(v, include_gst)
            for v in vouchers
            if v.voucher_type in signs
        ),
        Decimal(0),
    )


def net_sales(vouchers: Iterable[VoucherAmount], include_gst: bool = False) -> Decimal:
    return _net(vouchers, SALES_TYPES, include_gst)


def net_purchases(vouchers: Iterable[VoucherAmount], include_gst: bool = False) -> Decimal:
    return _net(vouchers, PURCHASE_TYPES, include_gst)


def monthly_series(
    vouchers: Iterable[VoucherAmount], include_gst: bool = False
) -> dict[tuple[int, int], tuple[Decimal, Decimal]]:
    """{(year, month): (net_sales, net_purchases)} for month-wise charts."""
    buckets: dict[tuple[int, int], list[VoucherAmount]] = defaultdict(list)
    for v in vouchers:
        buckets[(v.voucher_date.year, v.voucher_date.month)].append(v)
    return {
        key: (net_sales(items, include_gst), net_purchases(items, include_gst))
        for key, items in sorted(buckets.items())
    }


def limit_entries(
    vouchers: Iterable[VoucherAmount], metric: str, include_gst: bool = False
) -> list[tuple[date, str, Decimal]]:
    """Signed (date, voucher_no, value) entries feeding a limit's running total.

    'aggregate_turnover' is the outward-supply total, i.e. the same series as
    sales turnover: only the sales register is treated as outward supplies.
    """
    signs = PURCHASE_TYPES if metric == "purchase_turnover" else SALES_TYPES
    return [
        (v.voucher_date, v.voucher_no, signs[v.voucher_type] * _basis(v, include_gst))
        for v in vouchers
        if v.voucher_type in signs
    ]
