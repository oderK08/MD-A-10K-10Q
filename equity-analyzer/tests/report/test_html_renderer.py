from datetime import date, datetime, timezone

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.diff.grouped_diff import DiffGroup, GroupedTextDiffResult
from equity_analyzer.diff.text_diff import DiffSegment, TextDiffResult
from equity_analyzer.report.html_renderer import (
    _text_diff_detail_html,
    render_detail_html,
    render_html,
    render_trend_html,
)
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


def test_headline_figures_survive_the_removal_of_the_key_figures_table():
    """
    The "Chiffres clés" table was dropped from the report at the user's
    request. Revenue/net income still reach the reader through the
    computed executive-summary digest (the page-1 fallback used when no
    AI summary was requested), so the figures aren't lost -- they just
    aren't a table anymore.
    """
    report = build_report_data(_filing(), None, DICTIONARY)
    html = render_html(report)
    assert "$1,200,000" in html
    assert "Chiffres clés" not in html
    assert "Tag XBRL" not in html


def test_renders_unavailable_reason_when_section_missing():
    report = build_report_data(_filing(), prior_filing=None, lm_dictionary=None)
    html = render_html(report)
    assert "Indisponible" in html
    assert "dictionary" in html.lower() or "prior-period" in html.lower()


def test_renders_altman_zone_and_piotroski_score():
    """
    The Altman zone is now words, not a colour class: the whole report
    is greyscale by request, so severity has to survive being read in
    black and white.
    """
    report = build_report_data(_filing(), None, DICTIONARY)
    html = render_html(report)
    assert "Altman Z-Score" in html
    assert "zone grise" in html or "zone sûre" in html or "zone détresse" in html
    assert "Piotroski F-Score" in html


def test_report_uses_no_colour_anywhere():
    """
    "Je veux pas de couleur" -- asserted on the rendered document, not
    just reviewed by eye, so a future style tweak can't quietly
    reintroduce a green/red diff or a tinted callout. Greys (equal R=G=B)
    are allowed; anything else is a colour.
    """
    import re as _re

    report = build_report_data(_filing(), None, DICTIONARY)
    for html in (render_html(report), render_detail_html(report)):
        for hex_colour in _re.findall(r"#([0-9a-fA-F]{3,6})\b", html):
            if len(hex_colour) == 3:
                r, g, b = (c * 2 for c in hex_colour)
            elif len(hex_colour) == 6:
                r, g, b = hex_colour[0:2], hex_colour[2:4], hex_colour[4:6]
            else:
                continue
            assert r.lower() == g.lower() == b.lower(), f"non-grey colour #{hex_colour}"


def test_renders_diff_segments_for_mdna():
    """
    Two genuinely UNRELATED sentences (near-zero word overlap, well below
    the modified-pairing threshold) must still render as real separate
    removed/added blocks -- checked via the actual opening tag, not a
    bare substring, since ".segment-added"/".segment-removed" are also
    CSS selector names that appear in every report's <style> block
    regardless of whether anything actually uses them.

    Both texts end in a period deliberately: a short line with NO
    trailing punctuation is exactly what `grouped_diff.py`'s heading
    heuristic looks for, and this test isn't exercising that.
    """
    current = _filing(
        text_sections=FilingTextSections(
            item_1a_risk_factors="our revenue could decline due to competition",
            item_7_mdna="Revenue grew due to strong demand this year.",
            is_risk_factors_boilerplate=False,
        ),
    )
    prior = _filing(
        accession_number="acc-prior",
        financials=make_financial_period(
            fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
            accession_number="acc-prior", period_end=date(2023, 12, 31), **_METRICS,
        ),
        text_sections=FilingTextSections(
            item_1a_risk_factors="our revenue could decline due to competition",
            item_7_mdna="Our manufacturing facility experienced unrelated water damage last quarter.",
            is_risk_factors_boilerplate=False,
        ),
        fiscal_year=2023,
    )
    report = build_report_data(current, prior, DICTIONARY)
    # The changed text lives in the companion "détail" document now --
    # the two-page main report carries only counts and percentages.
    html = render_detail_html(report)
    assert '<span class="segment-added">' in html
    assert '<span class="segment-removed">' in html
    assert '<span class="segment-added">' not in render_html(report)


def test_renders_modified_segment_as_inline_word_diff():
    """
    A sentence recognizably reworded (not wholesale replaced) renders as
    ONE inline diff -- unchanged words in place, the old wording struck
    through, the new wording right next to it in green parentheses --
    not two full duplicated blocks. Real user feedback: reading a whole
    sentence struck through immediately above the whole new sentence in
    green took longer to scan than seeing inline what actually changed.

    Both texts end in a period deliberately -- see the note in
    test_renders_diff_segments_for_mdna about the heading heuristic.
    """
    current = _filing(
        text_sections=FilingTextSections(
            item_1a_risk_factors="our revenue could decline due to competition",
            item_7_mdna="Revenue grew due to strong growth this year.",
            is_risk_factors_boilerplate=False,
        ),
    )
    prior = _filing(
        accession_number="acc-prior",
        financials=make_financial_period(
            fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
            accession_number="acc-prior", period_end=date(2023, 12, 31), **_METRICS,
        ),
        text_sections=FilingTextSections(
            item_1a_risk_factors="our revenue could decline due to competition",
            # differs from `current`'s MD&A by only one word -- recognizably
            # the same sentence, genuinely reworded.
            item_7_mdna="Revenue fell due to strong growth this year.",
            is_risk_factors_boilerplate=False,
        ),
        fiscal_year=2023,
    )
    report = build_report_data(current, prior, DICTIONARY)
    html = render_detail_html(report)
    assert '<div class="segment-modified">' in html
    assert '<span class="word-removed">fell</span>' in html
    assert '<span class="word-added">(grew)</span>' in html
    # the unchanged words stay in place, in plain text.
    assert "Revenue" in html
    assert "due to strong growth this year" in html


def test_main_report_is_summary_then_numbers_with_detail_split_out():
    """
    The requested two-document split: page 1 is the executive summary,
    page 2 (after an explicit page break) is every number -- red flags,
    per-sub-theme change, financials, tone. The changed TEXT is not in
    this document at all; it's in the companion "détail" report.
    """
    current = _filing(
        text_sections=FilingTextSections(
            item_1a_risk_factors="our revenue could decline due to competition",
            item_7_mdna="Revenue grew due to strong demand this year.",
            is_risk_factors_boilerplate=False,
        ),
    )
    prior = _filing(
        accession_number="acc-prior",
        financials=make_financial_period(
            fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
            accession_number="acc-prior", period_end=date(2023, 12, 31), **_METRICS,
        ),
        text_sections=FilingTextSections(
            item_1a_risk_factors="our revenue could decline due to competition",
            item_7_mdna="Our manufacturing facility experienced unrelated water damage last quarter.",
            is_risk_factors_boilerplate=False,
        ),
        fiscal_year=2023,
    )
    report = build_report_data(current, prior, DICTIONARY)
    html = render_html(report)

    summary_idx = html.index("Résumé exécutif")
    break_idx = html.index('<div class="page-break">')
    red_flags_idx = html.index("Red flags")
    themes_idx = html.index("Changements par sous-thématique")
    tone_idx = html.index("Tonalité (Loughran-McDonald)")

    # page 1 = summary, page 2 = the numbers, in that order
    assert summary_idx < break_idx < red_flags_idx < themes_idx < tone_idx

    # no changed text anywhere in the main report -- that's the whole
    # point of splitting the detail out
    assert '<span class="segment-added">' not in html
    assert '<span class="segment-removed">' not in html
    assert '<div class="segment-modified">' not in html

    # exactly one page break: two pages, not three
    assert html.count('class="page-break"') == 1

    # ...and it really is in the detail document instead
    assert '<span class="segment-added">' in render_detail_html(report)


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
    assert "2021 à 2023" in html


def test_render_trend_html_shows_unavailable_for_first_year_yoy_sections():
    filings = [_trend_filing(y) for y in (2021, 2022)]
    trend = build_trend_analysis(filings, DICTIONARY)
    html = render_trend_html(trend)
    # Beneish/Piotroski show "n/a" for the first (oldest) year
    # specifically, not a crash or a fabricated score. (The marker used
    # to be an em dash; dashes were removed from the report at the
    # user's request, and an empty-cell em dash is still a dash.)
    assert 'class="unavailable">n/a' in html


# --- Regression tests for two bugs only caught by rendering a real PDF
# and visually inspecting it (not by any of the string-matching tests
# above, which is exactly why this pass happened) ---

def test_mdna_heading_is_not_double_escaped():
    """
    _text_diff_summary_html / _text_diff_detail_html / _render_sentiment_result already call _e() on
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
    # A failed AI call falls back to the computed digest rather than
    # leaving page 1 blank -- but says plainly that the interpretive
    # read is missing, and why.
    assert "Résumé exécutif" in html
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
    # The "not investment advice" safeguard stays...
    assert "ne constitue pas un conseil en investissement" in html
    # ...but the "aucune connaissance externe sur la société" claim was
    # removed at the user's request. It had also stopped being strictly
    # true: the sub-theme selection pass does use sector knowledge.
    assert "aucune connaissance externe" not in html


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

    html = _text_diff_detail_html("Risk Factors (Item 1A)", grouped)

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

    html = _text_diff_detail_html("Risk Factors (Item 1A)", grouped)

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

    html = _text_diff_detail_html("Risk Factors (Item 1A)", grouped)

    for heading in ["Theme A", "Theme B", "Theme C", "Theme D", "Theme E"]:
        slug = heading.replace(" ", "")
        assert f"{slug}word0" in html, f"{heading} should be shown in full"

    for heading in ["Theme F", "Theme G"]:
        slug = heading.replace(" ", "")
        assert f"{slug}word0" not in html, f"{heading} should be compact-only"
        assert heading in html, f"{heading} must still be mentioned, not silently dropped"

    assert "2 sous-thème(s) résumé(s)" in html


# --- The two-page guarantee (user: "compacter, jamais déborder") ---

def _worst_case_report():
    """
    The realistic worst case for page 2: the full cap of 10 selected
    sub-themes, all with long headings, plus an over-length summary and
    several failed Piotroski criteria. Anything heavier than this can't
    occur -- MAX_SELECTED_THEMES bounds the table.
    """
    import dataclasses

    from equity_analyzer.report.report_data import SectionResult
    from equity_analyzer.report.theme_selection import attach_theme_selection

    names = [
        f"Risks Related to {topic}"
        for topic in [
            "Demand, Supply, and Manufacturing Capacity Constraints",
            "Our Global Operating Business and International Expansion",
            "Regulatory, Legal, Our Stock and Other Matters",
            "Intellectual Property Protection and Third-Party Claims",
            "Cybersecurity Incidents and Data Protection",
            "Customer Concentration and Contractual Commitments",
            "Talent Retention and Key Personnel Dependence",
            "Climate, Environmental and Sustainability Obligations",
            "Tax Matters and Transfer Pricing Arrangements",
            "Debt Covenants and Access to Capital Markets",
            "Acquisitions, Divestitures and Integration",
            "Competition and Pricing Pressure in Core Markets",
        ]
    ]

    def body(extra=""):
        return "\n\n".join(
            f"Sentence {i} about this risk with enough length to count as real content.{extra}"
            for i in range(6)
        )

    prior_rf = "\n\n".join(f"{n}\n\n{body()}" for n in names)
    current_rf = "\n\n".join(f"{n}\n\n{body(' Prices rose 5% this year.')}" for n in names)

    prior_metrics = dict(_METRICS)
    prior_metrics.update(revenue=2_000_000, net_income=-50_000, operating_cash_flow=-10_000)

    current = _filing(
        company_name="Very Long Company Name Corporation Incorporated",
        text_sections=FilingTextSections(
            item_1a_risk_factors=current_rf,
            item_7_mdna="Revenue grew. Margin fell.",
            is_risk_factors_boilerplate=False,
        ),
    )
    prior = _filing(
        company_name="Very Long Company Name Corporation Incorporated",
        accession_number="acc-prior",
        financials=make_financial_period(
            fiscal_year=2023, fiscal_period="FY", duration=PeriodDuration.TWELVE_MONTH,
            accession_number="acc-prior", period_end=date(2023, 12, 31), **prior_metrics,
        ),
        text_sections=FilingTextSections(
            item_1a_risk_factors=prior_rf,
            item_7_mdna="Revenue grew. Margin fell.",
            is_risk_factors_boilerplate=False,
        ),
        fiscal_year=2023,
    )
    report = build_report_data(current, prior, DICTIONARY)
    report = attach_theme_selection(report, api_key=None)
    long_summary = " ".join(
        f"Phrase numéro {i} du résumé exécutif avec de la matière." for i in range(60)
    )
    return dataclasses.replace(
        report,
        ai_summary=SectionResult(
            value={"text": long_summary, "model": "test-model"}, unavailable_reason=None
        ),
    )


def test_theme_selection_is_capped_at_ten():
    from equity_analyzer.report.theme_selection import MAX_SELECTED_THEMES

    report = _worst_case_report()
    assert len(report.theme_selection.value.headings) == MAX_SELECTED_THEMES


def test_main_report_fits_two_pages_in_the_worst_case():
    """
    The deliverable: the heaviest report the pipeline can produce (the
    full cap of 10 sub-themes, long headings, an over-length summary)
    comes out at exactly two pages.

    Note it now fits at NATURAL size -- dropping the "Chiffres clés"
    table freed roughly the page it used to need. The fitter below is
    therefore a safety net rather than load-bearing here, and
    test_the_page_fitter_actually_compacts_overflowing_content proves
    separately that the net isn't a no-op.
    """
    from equity_analyzer.report.pdf_renderer import page_count, render_pdf_fitted

    html = render_html(_worst_case_report())
    assert page_count(render_pdf_fitted(html, max_pages=2)) == 2


def test_the_page_fitter_actually_compacts_overflowing_content():
    """
    Proves the two-page guarantee has real teeth: content that genuinely
    needs three pages comes back compacted to two. Deliberately synthetic
    and oversized -- no real filing produces this -- so the mechanism
    stays covered even though the actual report is now comfortably short.

    Sized to a THREE-page overflow on purpose, which is the fitter's real
    working range: measured, the compaction steps recover a 3-page
    document but not a 4-page one (60 rows + 220 sentences still comes
    out at 3). That limit is deliberate and documented in
    `render_pdf_fitted`, which returns its tightest attempt rather than
    raising -- a slightly long report beats no report. Asserting a range
    the mechanism doesn't actually have would make this test a lie.
    """
    from equity_analyzer.report.html_renderer import _css
    from equity_analyzer.report.pdf_renderer import page_count, render_pdf, render_pdf_fitted

    rows = "".join(
        f"<tr><td>Sous-thématique numéro {i} au titre volontairement long</td>"
        f"<td>{i}%</td></tr>"
        for i in range(50)
    )
    html = (
        f"<!doctype html><html><head><style>{_css()}</style></head><body>"
        f"<h1>Test</h1><p>{'Phrase de résumé exécutif. ' * 180}</p>"
        f'<div class="page-break"><h2>Tableau</h2><table>{rows}</table></div>'
        "</body></html>"
    )
    assert page_count(render_pdf(html)) > 2, "fixture must overflow or this proves nothing"
    assert page_count(render_pdf_fitted(html, max_pages=2)) == 2


def test_detail_report_is_not_page_capped():
    """
    The detail document is deliberately unbounded: capping it would mean
    dropping changed text, which is the one thing this project never
    does silently.
    """
    from equity_analyzer.report.pdf_renderer import page_count, render_pdf

    pdf = render_pdf(render_detail_html(_worst_case_report()))
    assert page_count(pdf) >= 1


def test_no_dashes_anywhere_in_the_rendered_documents():
    """
    "Dans le texte je veux que tu arrêtes d'utiliser les tirets."

    Checks the em dash, the en dash and the double hyphen: the three
    forms used as PUNCTUATION. Ordinary hyphens inside names and
    identifiers ("10-K", "Loughran-McDonald", "Z-Score", an ISO date)
    are untouched, since those aren't the thing being objected to and
    removing them would corrupt real words.

    Asserted on the rendered documents rather than reviewed by eye, so a
    later edit can't quietly reintroduce one.

    The <style> block is stripped first: CSS comments never reach the
    page, and this codebase's comment style uses " -- " throughout, so
    including them would fail the test on something the reader can't
    see.
    """
    import dataclasses
    import re as _re

    from equity_analyzer.report.report_data import SectionResult

    report = build_report_data(_filing(), None, DICTIONARY)
    report = dataclasses.replace(
        report,
        ai_summary=SectionResult(
            value={"text": "Plutôt bullish. Les prix montent.", "model": "test-model"},
            unavailable_reason=None,
        ),
    )
    for name, html in (
        ("main", render_html(report)),
        ("detail", render_detail_html(report)),
    ):
        visible = _re.sub(r"<style>.*?</style>", "", html, flags=_re.DOTALL)
        for dash in ("—", "–", " -- "):
            assert dash not in visible, f"{name} report still contains {dash!r}"


def test_the_ai_prompt_forbids_dashes_in_the_answer():
    """
    The report's own chrome is dash-free by construction (test above),
    but most of page 1 is text the MODEL writes, and an em dash used as
    an incise is a classic AI tell. The rule has to be in the prompt too.
    """
    from equity_analyzer.report.ai_summary import _SYSTEM_PROMPT

    assert "TIRET" in _SYSTEM_PROMPT.upper()


def test_a_summary_at_the_cap_still_leaves_the_report_at_two_pages():
    """
    The summary is meant to FILL page 1, so the cap sits close to what
    the page actually holds. Measured: 850 words render two pages, 900
    render three, and `_MAX_SUMMARY_WORDS` is set below that with margin.

    This pins the relationship. If a later style change (bigger body
    text, more leading, an extra line of chrome on page 1) shrinks the
    real capacity below the cap, a summary at the cap starts spilling
    and this fails, instead of the spill being discovered on a real
    report.
    """
    import dataclasses

    from equity_analyzer.report.html_renderer import _MAX_SUMMARY_WORDS
    from equity_analyzer.report.pdf_renderer import page_count, render_pdf
    from equity_analyzer.report.report_data import SectionResult

    sentence = "Cette phrase de test a une longueur representative d'une note reelle. "
    at_cap = " ".join((sentence * 60).split()[:_MAX_SUMMARY_WORDS])
    report = build_report_data(_filing(), None, DICTIONARY)
    report = dataclasses.replace(
        report,
        ai_summary=SectionResult(
            value={"text": at_cap, "model": "test-model"}, unavailable_reason=None
        ),
    )
    assert page_count(render_pdf(render_html(report))) == 2


def test_an_overlong_summary_is_truncated_at_a_sentence_boundary_and_says_so():
    """
    The cap is a backstop, and it must degrade visibly: cut on a
    sentence end, with a note pointing at the detail report, never
    mid-word and never a silent drop.
    """
    import dataclasses

    from equity_analyzer.report.html_renderer import _MAX_SUMMARY_WORDS
    from equity_analyzer.report.report_data import SectionResult

    sentence = "Cette phrase de test a une longueur representative d'une note reelle. "
    too_long = sentence * 200  # comfortably past the cap
    assert len(too_long.split()) > _MAX_SUMMARY_WORDS

    report = build_report_data(_filing(), None, DICTIONARY)
    report = dataclasses.replace(
        report,
        ai_summary=SectionResult(
            value={"text": too_long, "model": "test-model"}, unavailable_reason=None
        ),
    )
    html = render_html(report)
    assert "Synthèse tronquée" in html
    # the visible summary ends on a full stop, not a severed word
    body = html.split('<div class="exec-summary">')[1].split("</div>")[0]
    last_paragraph = body.split("<p>")[1].split("</p>")[0].strip()
    assert last_paragraph.endswith(".")
