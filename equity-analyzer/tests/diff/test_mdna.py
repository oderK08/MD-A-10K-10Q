import pytest

from equity_analyzer.data_layer.models import FilingTextSections
from equity_analyzer.diff.errors import MissingSectionError
from equity_analyzer.diff.mdna import diff_mdna

PRIOR_MDNA = (
    "Revenue increased 5% year over year driven by strong demand.\n\n"
    "Operating margin was flat compared to the prior period."
)
CURRENT_MDNA = (
    "Revenue increased 12% year over year driven by strong demand.\n\n"
    "Operating margin was flat compared to the prior period.\n\n"
    "We expect continued growth in the next fiscal year."
)


def test_raises_when_current_section_missing():
    current = FilingTextSections(item_7_mdna=None)
    prior = FilingTextSections(item_7_mdna=PRIOR_MDNA)
    with pytest.raises(MissingSectionError, match="current filing"):
        diff_mdna(current, prior)


def test_raises_when_prior_section_missing():
    current = FilingTextSections(item_7_mdna=CURRENT_MDNA)
    prior = FilingTextSections(item_7_mdna=None)
    with pytest.raises(MissingSectionError, match="prior filing"):
        diff_mdna(current, prior)


def test_diffs_mdna_normally_no_boilerplate_concept():
    current = FilingTextSections(item_7_mdna=CURRENT_MDNA)
    prior = FilingTextSections(item_7_mdna=PRIOR_MDNA)

    result = diff_mdna(current, prior)

    kinds = {seg.kind for seg in result.segments}
    assert "added" in kinds
    assert "removed" in kinds  # the rewritten "5%" -> "12%" sentence
    assert "equal" in kinds
