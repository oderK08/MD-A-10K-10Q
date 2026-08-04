"""
Integration test: run the actual Module 1 extraction pipeline
(extract_sections) on the real fixture HTML, then diff it through Module 3.

This exists because the unit tests elsewhere in this package use clean,
hand-written text -- which caught the formula/logic bugs but not the fact
that real SEC HTML wraps whole multi-sentence sections in a single <p>
tag (see text_diff.py's module docstring). Only exercising the two
modules together against real extracted output caught that gap, so it
stays as a permanent regression check.
"""

from __future__ import annotations

from pathlib import Path

from equity_analyzer.data_layer.text_sections import extract_sections
from equity_analyzer.diff.mdna import diff_mdna
from equity_analyzer.data_layer.models import FilingTextSections

FIXTURE_10K = Path(__file__).parent.parent / "fixtures" / "sample_10k.html"


def test_mdna_diff_on_real_extracted_text_isolates_single_sentence_change():
    html = FIXTURE_10K.read_text()
    prior_sections = extract_sections(html)
    assert prior_sections.item_7_mdna is not None, (
        "fixture no longer has an extractable MD&A section -- update this "
        "test if sample_10k.html changed"
    )

    # Simulate the next quarter's filing changing exactly one figure in
    # the MD&A (the rest of the filing, including surrounding items,
    # stays byte-identical).
    current_mdna = prior_sections.item_7_mdna.replace(
        "Revenue increased 12%", "Revenue increased 5%"
    )
    assert current_mdna != prior_sections.item_7_mdna, (
        "replacement did not match anything -- fixture text changed"
    )
    current_sections = FilingTextSections(item_7_mdna=current_mdna)

    result = diff_mdna(current_sections, prior_sections)
    kinds = [seg.kind for seg in result.segments]

    assert kinds.count("removed") == 1
    assert kinds.count("added") == 1
    # the rest of the MD&A block must be reported as unchanged, not
    # swallowed into the same removed/added pair.
    assert kinds.count("equal") >= 1
