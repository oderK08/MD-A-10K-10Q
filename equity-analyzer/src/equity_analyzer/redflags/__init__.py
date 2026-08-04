"""
Module 2 -- Red Flags: Altman Z-Score, Beneish M-Score, Piotroski F-Score.

All three operate on FinancialPeriod objects from Module 1 (data_layer),
never on raw XBRL. Beneish and Piotroski require a comparable
(current, prior) year-over-year pair -- see `require_comparable_yoy` for
the rule that enforces this instead of leaving it as a convention.
"""

from .altman_z import AltmanZResult, altman_z_score, classify_zone
from .beneish_m import BeneishMResult, beneish_m_score
from .comparability import require_comparable_yoy
from .errors import IncomparablePeriodsError, InsufficientDataError, RedFlagError
from .piotroski_f import PiotroskiFResult, piotroski_f_score

__all__ = [
    "AltmanZResult",
    "altman_z_score",
    "classify_zone",
    "BeneishMResult",
    "beneish_m_score",
    "PiotroskiFResult",
    "piotroski_f_score",
    "require_comparable_yoy",
    "RedFlagError",
    "InsufficientDataError",
    "IncomparablePeriodsError",
]
