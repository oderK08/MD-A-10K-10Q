from datetime import date, datetime, timezone

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.report.html_renderer import render_html
from equity_analyzer.report.report_data import build_report_data
from equity_analyzer.sentiment.lm_dictionary import LMDictionary

from .factories import make_filing, make_financial_period

DICTIONARY = LMDictionary(
    words_by_category={
        "negative": frozenset({"decline"}),
        "positive": frozenset({"growth"}),
        "uncertainty": frozenset(),
        "litigious": frozenset(),
        "strong_modal": frozenset(),
        "weak_modal": frozenset(),
        "constraining": frozenset(),
    }
)

_METRICS = dict(
    revenue=1_200_000, cogs=700_000, gross_profit=500_000, sga_expense=100_000,
    depreciation_amortization=50_000, operating_income=150_000, net_income=100_000,
    total_assets=1_000_000, current_assets=600_000, receivables=150_000,
    ppe_net=300_000, total_liabilities=400_000, current_liabilities=200_000,
    long_term_debt=100_000, retained_earnings=300_000, total_equity=600_000,
    shares_outstanding=1_000_000, operating_cash_flow=120_000,
)


def _filing(company_name="Acme & Sons <Test> Inc.", **overrides):
    financials = make_financial_period(
        fiscal_year=2024, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
        accession_number="acc-1", period_end=date(2024, 12, 31), **_METRICS,
    )
    text_sections = FilingTextSections(
        item_1a_risk_factors="our revenue could decline due to competition",
        item_7_mdna="revenue grew due to strong growth this year",
        is_risk_factors_boilerplate=False,
    )
    kwargs = dict(
        company_name=company_name, fiscal_year=2024, fiscal_period="FY",
        form_type=FormType.TEN_K, accession_number="acc-1", period_end=date(2024, 12, 31),
        financials=financials, text_sections=text_sections,
    )
    kwargs.update(overrides)
    return make_filing(**kwargs)


def test_renders_company_name_and_ticker():
    report = build_report_data(_filing(ticker="ACME"), None, DICTIONARY)
    html = render_html(report)
    assert "ACME" in html
    assert "Acme &amp; Sons &lt;Test&gt; Inc." in html


def test_escapes_html_special_characters_in_company_name():
    """A raw '<Test>' from a company name must never appear unescaped --
    that would corrupt the page and, in principle, allow markup injection."""
    report = build_report_data(_filing(company_name="<script>alert(1)</script>"), None, DICTIONARY)
    html = render_html(report)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_renders_financial_highlights_table():
    report = build_report_data(_filing(), None, DICTIONARY)
    html = render_html(report)
    assert "Revenue" in html
    assert "$1,200,000" in html


def test_renders_unavailable_reason_when_section_missing():
    report = build_report_data(_filing(), prior_filing=None, lm_dictionary=None)
    html = render_html(report)
    assert "Indisponible" in html
    assert "dictionary" in html.lower() or "prior-period" in html.lower()


def test_renders_altman_zone_and_piotroski_score():
    report = build_report_data(_filing(), None, DICTIONARY)
    html = render_html(report)
    assert "Altman Z-Score" in html
    assert "zone-" in html
    assert "Piotroski F-Score" in html


def test_renders_diff_segments_for_mdna():
    from .factories import make_filing as _mk

    current = _filing()
    prior = _filing(
        accession_number="acc-prior",
        financials=make_financial_period(
            fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
            accession_number="acc-prior", period_end=date(2023, 12, 31), **_METRICS,
        ),
        text_sections=FilingTextSections(
            item_1a_risk_factors="our revenue could decline due to weak demand",
            item_7_mdna="revenue was flat this year",
            is_risk_factors_boilerplate=False,
        ),
        fiscal_year=2023,
    )
    report = build_report_data(current, prior, DICTIONARY)
    html = render_html(report)
    assert "segment-added" in html
    assert "segment-removed" in html


def test_renders_boilerplate_skip_note():
    current = _filing()
    current.text_sections.is_risk_factors_boilerplate = True
    prior = _filing(
        accession_number="acc-prior",
        financials=make_financial_period(
            fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
            accession_number="acc-prior", period_end=date(2023, 12, 31), **_METRICS,
        ),
        text_sections=FilingTextSections(
            item_1a_risk_factors="our revenue could decline due to weak demand",
            item_7_mdna="revenue was flat this year",
            is_risk_factors_boilerplate=False,
        ),
        fiscal_year=2023,
    )
    report = build_report_data(current, prior, DICTIONARY)
    html = render_html(report)
    assert "skip-note" in html


def test_output_is_a_complete_html_document():
    report = build_report_data(_filing(), None, DICTIONARY, generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    html = render_html(report)
    assert html.strip().startswith("<!doctype html>")
    assert "</html>" in html
    assert "2025-01-01" in html
