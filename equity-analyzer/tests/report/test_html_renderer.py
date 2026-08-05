from datetime import date, datetime, timezone

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.diff.grouped_diff import DiffGroup, GroupedTextDiffResult
from equity_analyzer.diff.text_diff import DiffSegment, TextDiffResult
from equity_analyzer.report.html_renderer import _humanize_xbrl_tag, _render_text_diff, render_html, render_trend_html
from equity_analyzer.report.report_data import build_report_data
from equity_analyzer.report.trend import build_trend_analysis
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


def test_renders_source_filing_traceability_link():
    report = build_report_data(_filing(cik="0000320193", accession_number="0000320193-24-000010"), None, DICTIONARY)
    html = render_html(report)
    assert "source SEC EDGAR" in html
    assert "sec.gov/Archives/edgar/data/320193/" in html
    assert "0000320193-24-000010-index.htm" in html


def test_renders_data_completeness_percentage():
    report = build_report_data(_filing(), None, DICTIONARY)
    html = render_html(report)
    assert "Complétude des données" in html
    assert "%" in html


def _trend_filing(year, **overrides):
    financials = make_financial_period(
        fiscal_year=year, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
        accession_number=f"acc-{year}", period_end=date(year, 12, 31), **_METRICS,
    )
    text_sections = FilingTextSections(
        item_1a_risk_factors="our revenue could decline due to competition",
        item_7_mdna="revenue grew due to strong growth this year",
        is_risk_factors_boilerplate=False,
    )
    kwargs = dict(
        fiscal_year=year, fiscal_period="FY", form_type=FormType.TEN_K,
        accession_number=f"acc-{year}", period_end=date(year, 12, 31),
        financials=financials, text_sections=text_sections,
    )
    kwargs.update(overrides)
    return make_filing(**kwargs)


def test_render_trend_html_shows_one_row_per_year():
    filings = [_trend_filing(y) for y in (2021, 2022, 2023)]
    trend = build_trend_analysis(filings, DICTIONARY)
    html = render_trend_html(trend)

    assert html.strip().startswith("<!doctype html>")
    assert "2021" in html
    assert "2022" in html
    assert "2023" in html
    assert "2021–2023" in html


def test_render_trend_html_shows_unavailable_for_first_year_yoy_sections():
    filings = [_trend_filing(y) for y in (2021, 2022)]
    trend = build_trend_analysis(filings, DICTIONARY)
    html = render_trend_html(trend)
    # Beneish/Piotroski show "—" for the first (oldest) year specifically,
    # not a crash or a fabricated score.
    assert 'class="unavailable">—' in html


# --- Regression tests for two bugs only caught by rendering a real PDF
# and visually inspecting it (not by any of the string-matching tests
# above, which is exactly why this pass happened) ---

def test_mdna_heading_is_not_double_escaped():
    """
    _render_text_diff / _render_sentiment_result already call _e() on
    their `title` argument -- passing a pre-escaped "MD&amp;A" string
    into them re-escapes the "&" a second time, so the PDF renders the
    literal text "MD&amp;A" instead of "MD&A". Confirmed by rendering an
    actual report and reading the output PDF, not by running the test
    suite (which was green the whole time this bug existed).
    """
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
    assert "MD&amp;amp;A" not in html
    assert "MD&amp;A" in html  # the correctly-escaped, single-encoded form


def test_long_xbrl_tag_names_are_humanized_for_wrapping():
    """
    A raw camelCase XBRL concept name (e.g.
    "RevenueFromContractWithCustomerExcludingAssessedTax") runs off the
    page edge in the rendered PDF: xhtml2pdf respects neither
    `word-break: break-all` nor `<wbr>`, confirmed by rendering real test
    PDFs, so a long unbroken identifier has nowhere to wrap. Inserting
    real spaces at camelCase boundaries is the fix that was actually
    confirmed to work the same way.
    """
    assert _humanize_xbrl_tag("RevenueFromContractWithCustomerExcludingAssessedTax") == (
        "Revenue From Contract With Customer Excluding Assessed Tax"
    )
    # the report factories tag every fact with the placeholder concept
    # "TestConcept" -- still camelCase, so it's enough to confirm the
    # humanizer is actually wired into the rendered table, not just
    # correct in isolation.
    report = build_report_data(_filing(), None, DICTIONARY)
    html = render_html(report)
    assert ">TestConcept<" not in html
    assert ">Test Concept<" in html


def test_trend_report_has_cover_page_and_page_break():
    filings = [_trend_filing(y) for y in (2021, 2022)]
    trend = build_trend_analysis(filings, DICTIONARY)
    html = render_trend_html(trend)
    assert 'class="cover"' in html
    assert 'class="page-break"' in html


def test_trend_report_includes_revenue_chart_as_embedded_svg():
    filings = [_trend_filing(y) for y in (2021, 2022)]
    trend = build_trend_analysis(filings, DICTIONARY)
    html = render_trend_html(trend)
    assert "data:image/svg+xml;base64," in html


def test_single_report_and_trend_report_have_pagination_footer():
    report = build_report_data(_filing(), None, DICTIONARY)
    html = render_html(report)
    assert '<pdf:pagenumber' in html
    assert '<pdf:pagecount' in html

    filings = [_trend_filing(y) for y in (2021, 2022)]
    trend = build_trend_analysis(filings, DICTIONARY)
    trend_html = render_trend_html(trend)
    assert '<pdf:pagenumber' in trend_html
    assert '<pdf:pagecount' in trend_html


def test_single_report_executive_summary_present():
    report = build_report_data(_filing(), None, DICTIONARY)
    html = render_html(report)
    assert "Résumé exécutif" in html


def test_trend_executive_summary_mentions_revenue_growth():
    filings = [_trend_filing(y) for y in (2021, 2022)]
    trend = build_trend_analysis(filings, DICTIONARY)
    html = render_trend_html(trend)
    assert "Résumé exécutif" in html
    assert "Revenue en hausse" in html or "Revenue en baisse" in html


def test_ai_summary_not_rendered_when_never_requested():
    """report.ai_summary is None by default (opt-in only, see
    report/ai_summary.py) -- no placeholder should appear either."""
    report = build_report_data(_filing(), None, DICTIONARY)
    assert report.ai_summary is None
    html = render_html(report)
    assert "Synthèse générée par IA" not in html


def test_ai_summary_shows_unavailable_reason_when_it_failed():
    import dataclasses
    from equity_analyzer.report.report_data import SectionResult

    report = build_report_data(_filing(), None, DICTIONARY)
    report = dataclasses.replace(
        report,
        ai_summary=SectionResult(value=None, unavailable_reason="no ANTHROPIC_API_KEY provided"),
    )
    html = render_html(report)
    assert "Synthèse générée par IA" in html
    assert "Indisponible" in html
    assert "no ANTHROPIC_API_KEY provided" in html


def test_ai_summary_renders_text_model_badge_and_disclaimer():
    import dataclasses
    from equity_analyzer.report.report_data import SectionResult

    report = build_report_data(_filing(), None, DICTIONARY)
    report = dataclasses.replace(
        report,
        ai_summary=SectionResult(
            value={"text": "Le risque X a été retiré cette année.", "model": "claude-haiku-4-5-20251001"},
            unavailable_reason=None,
        ),
    )
    html = render_html(report)
    assert "Le risque X a été retiré cette année." in html
    assert "claude-haiku-4-5-20251001" in html
    assert "ne constitue pas un conseil en investissement" in html


# --- Regression tests for real user feedback on a Micron 10-K report:
# the grouped diff report was too long, and reproduced the full text of
# sub-themes that had been wholesale added or removed ---

def _diff_result(segments):
    added_words = sum(len(s.text.split()) for s in segments if s.kind == "added")
    removed_words = sum(len(s.text.split()) for s in segments if s.kind == "removed")
    return TextDiffResult(
        segments=segments,
        similarity_ratio=0.5,
        prior_word_count=10,
        current_word_count=10,
        added_word_count=added_words,
        removed_word_count=removed_words,
    )


def test_wholesale_removed_subtheme_does_not_reproduce_its_text():
    removed_text = "This entire risk factor about supply chain has been removed from the filing."
    group = DiffGroup(
        heading="Supply Chain Risk",
        status="removed",
        diff=_diff_result([DiffSegment(kind="removed", text=removed_text)]),
    )
    grouped = GroupedTextDiffResult(overall=group.diff, groups=[group])

    html = _render_text_diff("Risk Factors (Item 1A)", grouped)

    assert "Supply Chain Risk" in html
    assert "sous-thématique supprimée" in html
    assert removed_text not in html


def test_wholesale_added_subtheme_does_not_reproduce_its_text():
    added_text = "A brand new risk factor about cybersecurity has been introduced this year."
    group = DiffGroup(
        heading="Cybersecurity Risk",
        status="added",
        diff=_diff_result([DiffSegment(kind="added", text=added_text)]),
    )
    grouped = GroupedTextDiffResult(overall=group.diff, groups=[group])

    html = _render_text_diff("Risk Factors (Item 1A)", grouped)

    assert "Cybersecurity Risk" in html
    assert "nouvelle sous-thématique" in html
    assert added_text not in html


def test_only_the_most_changed_matched_subthemes_show_full_text():
    """
    A filing can restructure a dozen+ sub-themes at once. Only the
    _MAX_DETAILED_GROUPS most heavily reworded ones (by total changed
    word count) should be reproduced in full; the rest still get a
    one-line mention -- never a silent drop.
    """
    def _matched_group(heading, weight_words):
        slug = heading.replace(" ", "")
        text = " ".join(f"{slug}word{i}" for i in range(weight_words))
        return DiffGroup(
            heading=heading,
            status="matched",
            diff=_diff_result([DiffSegment(kind="added", text=text)]),
        )

    # 5 heavily-changed groups, 2 lightly-changed ones -- 7 total.
    weights = {"Theme A": 100, "Theme B": 90, "Theme C": 80, "Theme D": 70,
               "Theme E": 60, "Theme F": 10, "Theme G": 5}
    groups = [_matched_group(h, w) for h, w in weights.items()]
    all_segments = [seg for g in groups for seg in g.diff.segments]
    grouped = GroupedTextDiffResult(overall=_diff_result(all_segments), groups=groups)

    html = _render_text_diff("Risk Factors (Item 1A)", grouped)

    for heading in ["Theme A", "Theme B", "Theme C", "Theme D", "Theme E"]:
        slug = heading.replace(" ", "")
        assert f"{slug}word0" in html, f"{heading} should be shown in full"

    for heading in ["Theme F", "Theme G"]:
        slug = heading.replace(" ", "")
        assert f"{slug}word0" not in html, f"{heading} should be compact-only"
        assert heading in html, f"{heading} must still be mentioned, not silently dropped"

    assert "2 sous-thème(s) résumé(s)" in html
