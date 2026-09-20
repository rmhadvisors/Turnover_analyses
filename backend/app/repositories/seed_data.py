"""Default absolute limits.

Commonly used Indian compliance figures, pre-filled as EDITABLE STARTING POINTS
ONLY - every description says 'verify current limit'. They are not authoritative
law; the CA edits or disables them in Settings.
"""

from decimal import Decimal

from app.models import AbsoluteLimitMetric as M

_V = "Verify current limit before relying on it. "

DEFAULT_LIMITS: list[dict] = [
    {
        "name": "GST registration - goods (regular states)",
        "metric": M.AGGREGATE_TURNOVER,
        "amount": Decimal(4000000),
        "description": _V + "Aggregate turnover above which a supplier of goods must register "
        "under GST (Rs 40 lakh; some states notify Rs 20 lakh).",
    },
    {
        "name": "GST registration - services (regular states)",
        "metric": M.AGGREGATE_TURNOVER,
        "amount": Decimal(2000000),
        "description": _V + "Aggregate turnover above which a service provider must register "
        "under GST (Rs 20 lakh).",
    },
    {
        "name": "GST registration - special-category states (goods)",
        "metric": M.AGGREGATE_TURNOVER,
        "amount": Decimal(2000000),
        "description": _V + "Special-category states: Rs 20 lakh for goods "
        "(Rs 10 lakh in some north-eastern states).",
    },
    {
        "name": "GST registration - special-category states (services)",
        "metric": M.AGGREGATE_TURNOVER,
        "amount": Decimal(1000000),
        "description": _V + "Special-category states: Rs 10 lakh for services.",
    },
    {
        "name": "Tax audit u/s 44AB - business (standard)",
        "metric": M.SALES_TURNOVER,
        "amount": Decimal(10000000),
        "description": _V + "Business turnover above Rs 1 crore requires a tax audit.",
    },
    {
        "name": "Tax audit u/s 44AB - business (cash receipts/payments within 5%)",
        "metric": M.SALES_TURNOVER,
        "amount": Decimal(100000000),
        "description": _V + "Higher Rs 10 crore limit applies when cash receipts and cash "
        "payments are each within the prescribed % (5%) of the total.",
    },
    {
        "name": "Presumptive taxation u/s 44AD - business (standard)",
        "metric": M.SALES_TURNOVER,
        "amount": Decimal(20000000),
        "description": _V + "Turnover limit for the presumptive scheme (Rs 2 crore).",
    },
    {
        "name": "Presumptive taxation u/s 44AD - business (cash within 5%)",
        "metric": M.SALES_TURNOVER,
        "amount": Decimal(30000000),
        "description": _V + "Higher Rs 3 crore limit when cash receipts are within the "
        "prescribed %.",
    },
    {
        "name": "Presumptive taxation u/s 44ADA - professionals (standard)",
        "metric": M.SALES_TURNOVER,
        "amount": Decimal(5000000),
        "description": _V + "Gross receipts limit for specified professionals (Rs 50 lakh).",
    },
    {
        "name": "Presumptive taxation u/s 44ADA - professionals (cash within 5%)",
        "metric": M.SALES_TURNOVER,
        "amount": Decimal(7500000),
        "description": _V + "Higher Rs 75 lakh limit when cash receipts are within the "
        "prescribed %.",
    },
    {
        "name": "E-invoicing applicability",
        "metric": M.AGGREGATE_TURNOVER,
        "amount": Decimal(50000000),
        "description": _V + "E-invoicing applies once aggregate turnover in any preceding "
        "financial year exceeds this amount (currently Rs 5 crore).",
    },
    {
        "name": "TDS u/s 194Q - purchases of goods",
        "metric": M.PURCHASE_TURNOVER,
        "amount": Decimal(5000000),
        "description": _V + "TDS at 0.1% on purchases above Rs 50 lakh from a seller in the "
        "year. The law applies per seller; this checks total purchases as a prompt.",
    },
    {
        "name": "TDS u/s 194Q - buyer turnover",
        "metric": M.SALES_TURNOVER,
        "amount": Decimal(100000000),
        "description": _V + "Section 194Q applies to buyers whose turnover in the preceding "
        "year exceeded Rs 10 crore.",
    },
]
