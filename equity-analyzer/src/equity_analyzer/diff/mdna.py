"""
Item 7 / Item 2 (MD&A) diff.

Unlike Item 1A, the MD&A has no SEC-sanctioned boilerplate shortcut --
it's rewritten every filing, 10-K and 10-Q alike -- so this is a plain
diff with no skip case, just the missing-section guard.
"""

from __future__ import annotations

from ..data_layer.models import FilingTextSections
from .errors import MissingSectionError
from .text_diff import TextDiffResult, diff_text


def diff_mdna(
    current: FilingTextSections,
    prior: FilingTextSections,
) -> TextDiffResult:
    if current.item_7_mdna is None:
        raise MissingSectionError(
            "current filing has no extracted Item 7 / MD&A section."
        )
    if prior.item_7_mdna is None:
        raise MissingSectionError(
            "prior filing has no extracted Item 7 / MD&A section."
        )
    return diff_text(prior.item_7_mdna, current.item_7_mdna)
