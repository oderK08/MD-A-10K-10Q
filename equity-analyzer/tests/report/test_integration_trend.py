"""
Integration test for Module 5b (trend.py): builds a real multi-year
trend from the same real XBRL/HTML fixtures used elsewhere, renders it
to HTML and an actual PDF. Same discipline as
test_integration_full_pipeline.py -- validate against real (or
realistically-shaped) data, not only hand-written synthetic objects.
"""

from __future__ import annotations

import json
from pathlib import Path

from equity_analyzer.data_layer.models import FormType
from equity_analyzer.data_layer.text_sections import extract_sections
from equity_analyzer.data_layer.xbrl_normalizer import build_financial_period
from equity_analyzer.report.html_renderer import render_trend_html
from equity_analyzer.report.pdf_renderer import render_pdf
from equity_analyzer.report.trend import build_trend_analysis

from .factories import make_filing

FIXTURE_COMPANYFACTS = Path(__file__).parent.parent / "fixtures" / "sample_companyfacts.json"
FIXTURE_10K = Path(__file__).parent.parent / "fixtures" / "sample_10k.html"


def test_trend_over_two_real_fixture_years_produces_a_pdf():
    company_facts = json.loads(FIXTURE_COMPANYFACTS.read_text())
    sections = extract_sections(FIXTURE_10K.read_text())

    filings = []
    for year, accession in [(2024, "0000320193-24-000010"), (2025, "0000320193-25-000010")]:
        financials = build_financial_period(
            company_facts, accession_number=accession, fiscal_year=year, fiscal_period="Q1",
        )
        filings.append(make_filing(
            ticker="AAPL", cik="0000320193", company_name="Apple Inc.",
            form_type=FormType.TEN_Q, fiscal_year=year, fiscal_period="Q1",
            filed_date=financials.filed_date, accession_number=accession,
            period_end=financials.period_end, financials=financials, text_sections=sections,
        ))

    trend = build_trend_analysis(filings)

    assert [p.fiscal_year for p in trend.points] == [2024, 2025]
    # oldest point has no prior -> only Altman attempted (and unavailable,
    # since this fixture's periods are quarterly, same as the single-
    # period integration test establishes)
    assert not trend.points[0].report.altman_z.available
    assert not trend.points[0].report.beneish_m.available

    html = render_trend_html(trend)
    assert "Apple Inc." in html
    assert "2024" in html and "2025" in html
    assert "sec.gov/Archives/edgar/data/320193/" in html  # traceability links present

    pdf_bytes = render_pdf(html)
    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 500
