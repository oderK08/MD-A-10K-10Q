"""
Full pipeline integration test: Module 1 (real XBRL fixture + real HTML
fixture) -> Modules 2-4 -> Module 5 (report), ending in an actual
rendered PDF. Follows the same discipline adopted after the Module 3
paragraph-splitting bug and the Module 4 production-dictionary test:
validate against real (or realistically-shaped) data, not only
hand-written synthetic objects.

Uses the real Loughran-McDonald dictionary when present, and skips that
part gracefully otherwise (see test_integration_production_dictionary.py
in tests/sentiment for why the file might be absent).
"""

from __future__ import annotations

import json
from pathlib import Path

from equity_analyzer.data_layer.models import FormType
from equity_analyzer.data_layer.text_sections import extract_sections
from equity_analyzer.data_layer.xbrl_normalizer import build_financial_period
from equity_analyzer.report.html_renderer import render_html
from equity_analyzer.report.pdf_renderer import render_pdf
from equity_analyzer.report.report_data import build_report_data
from equity_analyzer.sentiment.lm_dictionary import load_lm_dictionary

from .factories import make_filing

FIXTURE_COMPANYFACTS = Path(__file__).parent.parent / "fixtures" / "sample_companyfacts.json"
FIXTURE_10K = Path(__file__).parent.parent / "fixtures" / "sample_10k.html"
DICTIONARY_PATH = (
    Path(__file__).parent.parent.parent
    / "data"
    / "Loughran-McDonald_MasterDictionary_1993-2025.csv"
)


def test_full_pipeline_produces_a_pdf_with_real_fixtures():
    company_facts = json.loads(FIXTURE_COMPANYFACTS.read_text())

    # Two real, comparable (same fiscal quarter, consecutive years)
    # FinancialPeriods, built the same way Module 1's own tests build them.
    current_financials = build_financial_period(
        company_facts, accession_number="0000320193-25-000010",
        fiscal_year=2025, fiscal_period="Q1",
    )
    prior_financials = build_financial_period(
        company_facts, accession_number="0000320193-24-000010",
        fiscal_year=2024, fiscal_period="Q1",
    )

    # Real extracted text sections (same HTML fixture used for both
    # periods is fine here -- this test exercises pipeline wiring, not
    # diff/sentiment correctness, which are already covered elsewhere).
    sections = extract_sections(FIXTURE_10K.read_text())

    current_filing = make_filing(
        ticker="AAPL", cik="0000320193", company_name="Apple Inc.",
        form_type=FormType.TEN_Q, fiscal_year=2025, fiscal_period="Q1",
        filed_date=current_financials.filed_date,
        accession_number=current_financials.accession_number,
        period_end=current_financials.period_end,
        financials=current_financials, text_sections=sections,
    )
    prior_filing = make_filing(
        ticker="AAPL", cik="0000320193", company_name="Apple Inc.",
        form_type=FormType.TEN_Q, fiscal_year=2024, fiscal_period="Q1",
        filed_date=prior_financials.filed_date,
        accession_number=prior_financials.accession_number,
        period_end=prior_financials.period_end,
        financials=prior_financials, text_sections=sections,
    )

    dictionary = load_lm_dictionary(DICTIONARY_PATH) if DICTIONARY_PATH.exists() else None

    report = build_report_data(current_filing, prior_filing, dictionary)

    # The fixture's companyfacts.json doesn't include cogs/sga_expense/
    # long_term_debt (see tests/fixtures/sample_companyfacts.json), and
    # the periods are quarterly -- so red flags degrade gracefully rather
    # than computing full (and misleadingly precise-looking) scores from
    # incomplete data. This is the realistic case, not a special one.
    assert not report.altman_z.available
    assert "12-month" in report.altman_z.unavailable_reason
    assert not report.beneish_m.available
    assert not report.piotroski_f.available

    # Text-based sections don't depend on those missing XBRL tags at all.
    assert report.mdna_diff.available
    assert report.risk_factors_diff.available

    html = render_html(report)
    assert "Apple Inc." in html
    assert "Indisponible" in html  # the red-flags gaps are shown, not hidden

    pdf_bytes = render_pdf(html)
    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 500
