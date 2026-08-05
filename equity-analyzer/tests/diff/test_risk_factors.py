import pytest

from equity_analyzer.data_layer.models import FilingTextSections
from equity_analyzer.diff.errors import MissingSectionError
from equity_analyzer.diff.risk_factors import diff_risk_factors

REWRITTEN_RISK_TEXT = (
    "We face substantial competition in our core markets.\n\n"
    "Our supply chain depends on a small number of suppliers."
)
BOILERPLATE_TEXT = (
    "There have been no material changes to the risk factors disclosed "
    "in our Annual Report on Form 10-K."
)


def _sections(risk_text, boilerplate=False):
    return FilingTextSections(
        item_1a_risk_factors=risk_text,
        is_risk_factors_boilerplate=boilerplate,
    )


def test_raises_when_current_section_missing():
    current = FilingTextSections(item_1a_risk_factors=None)
    prior = _sections(REWRITTEN_RISK_TEXT)
    with pytest.raises(MissingSectionError, match="current filing"):
        diff_risk_factors(current, prior)


def test_raises_when_prior_section_missing():
    current = _sections(REWRITTEN_RISK_TEXT)
    prior = FilingTextSections(item_1a_risk_factors=None)
    with pytest.raises(MissingSectionError, match="prior filing"):
        diff_risk_factors(current, prior)


def test_skips_when_current_is_boilerplate():
    current = _sections(BOILERPLATE_TEXT, boilerplate=True)
    prior = _sections(REWRITTEN_RISK_TEXT)

    result = diff_risk_factors(current, prior)

    assert result.skipped is True
    assert result.diff is None
    assert "no material changes" in result.skip_reason.lower() or "boilerplate" in result.skip_reason.lower()


def test_diffs_normally_when_neither_is_boilerplate():
    current_text = REWRITTEN_RISK_TEXT + "\n\nWe are also exposed to new cybersecurity risks."
    current = _sections(current_text)
    prior = _sections(REWRITTEN_RISK_TEXT)

    result = diff_risk_factors(current, prior)

    assert result.skipped is False
    assert result.skip_reason is None
    assert result.diff is not None
    assert any(seg.kind == "added" for seg in result.diff.overall.segments)


def test_diffs_when_only_prior_was_boilerplate():
    """A real rewrite after a boilerplate quarter is a genuine signal, not skipped."""
    current = _sections(REWRITTEN_RISK_TEXT, boilerplate=False)
    prior = _sections(BOILERPLATE_TEXT, boilerplate=True)

    result = diff_risk_factors(current, prior)

    assert result.skipped is False
    assert result.prior_was_boilerplate is True
    assert result.diff is not None
