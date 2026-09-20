"""Import every model so Base.metadata is fully populated on `create_all`."""

from app.models.absolute_limit import AbsoluteLimit, AbsoluteLimitMetric
from app.models.alert import Alert
from app.models.client import Client
from app.models.column_mapping import ColumnMapping
from app.models.import_log import ImportLog
from app.models.threshold_band import ThresholdSetting
from app.models.voucher import Voucher, VoucherType
from app.models.yearly_figures import YearlyFigures

__all__ = [
    "AbsoluteLimit",
    "AbsoluteLimitMetric",
    "Alert",
    "Client",
    "ColumnMapping",
    "ImportLog",
    "ThresholdSetting",
    "Voucher",
    "VoucherType",
    "YearlyFigures",
]
