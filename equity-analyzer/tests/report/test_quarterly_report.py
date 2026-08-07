"""
Tests for the quarterly shape of the report: a 10-Q read against the
filing before it, with the annual scores kept separate and the risk
factors reduced to an alert.

The pipeline originally compared two consecutive 10-Ks. That could not
answer the question it was built for -- what did management say this
quarter that it did not say last quarter -- because on a company that
had just published its Q3 it read annual text up to a year old and never
opened the Q3 at all. These tests pin the pieces of the correction that
would otherwise be easy to regress into the old shape.
"""

from datetime import date

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.report.ai_summary import build_prompt_context
from equity_analyzer.report.html_renderer import render_html
from equity_analyzer.report.report_data import build_report_data
from equity_analyzer.report.risk_alert import evaluate_risk_alert
from equity_analyzer.report.theme_selection import attach_theme_selection, default_section_for
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

# The "no material changes" clause that Part II Item 1A of a 10-Q
# carries in the overwhelming majority of quarters.
BOILERPLATE = (
    "There have been no material changes to the risk factors disclosed in "
    "our Annual Report on Form 10-K for the fiscal year ended August 2025."
)

# A 10-Q MD&A shaped like the real thing: a handful of named
# sub-sections, one of them (critical accounting estimates) structurally
# identical every quarter, which is exactly the kind the selection pass
# is supposed to leave behind.
_LIQUIDITY = """Liquidity and Capital Resources

We held 4.1 billion dollars in cash and cash equivalents at the end of the quarter.
We expect capital expenditures of approximately 900 million dollars this fiscal year.
Our revolving credit facility remains undrawn and matures in 2029.
Operating cash flow funded all of our capital expenditures during the period.
"""

_ESTIMATES = """Critical Accounting Estimates

Our critical accounting estimates are unchanged from those described in our most
recent Annual Report on Form 10-K, and require management to make judgments about
matters that are inherently uncertain, including inventory valuation, revenue
recognition and the recoverability of long lived assets.
"""

MDNA_Q2 = f"""Results of Operations

Revenue increased 12% compared to the prior quarter, driven by data center demand.
Gross margin was 62% compared to 60% in the prior quarter, reflecting product mix.
Operating expenses grew 4% as we continued to hire engineering staff.

{_LIQUIDITY}
{_ESTIMATES}"""

MDNA_Q3 = f"""Results of Operations

Revenue increased 27% compared to the prior quarter, driven by data center demand.
Gross margin was 68% compared to 62% in the prior quarter, reflecting product mix.
Operating expenses grew 4% as we continued to hire engineering staff.
We expect average selling prices to increase approximately 5% next quarter.

{_LIQUIDITY}
{_ESTIMATES}"""


def _quarter(
    *,
    accession="acc-q3",
    fiscal_period="Q3",
    period_end=date(2026, 6, 30),
    mdna=MDNA_Q3,
    risk_factors=BOILERPLATE,
    boilerplate=True,
    form_type=FormType.TEN_Q,
):
    financials = make_financial_period(
        fiscal_year=2026, fiscal_period=fiscal_period,
        duration=PeriodDuration.THREE_MONTH,
        accession_number=accession, period_end=period_end, **_METRICS,
    )
    return make_filing(
        ticker="TEST", company_name="Test Company Inc.", form_type=form_type,
        fiscal_year=2026, fiscal_period=fiscal_period, accession_number=accession,
        period_end=period_end, filed_date=period_end, financials=financials,
        text_sections=FilingTextSections(
            item_1a_risk_factors=risk_factors,
            item_7_mdna=mdna,
            is_risk_factors_boilerplate=boilerplate,
        ),
    )


def _annual(*, fiscal_year=2025, accession="acc-fy"):
    financials = make_financial_period(
        fiscal_year=fiscal_year, fiscal_period="FY",
        duration=PeriodDuration.TWELVE_MONTH,
        accession_number=accession, period_end=date(fiscal_year, 8, 31), **_METRICS,
    )
    return make_filing(
        ticker="TEST", company_name="Test Company Inc.", form_type=FormType.TEN_K,
        fiscal_year=fiscal_year, fiscal_period="FY", accession_number=accession,
        period_end=date(fiscal_year, 8, 31), filed_date=date(fiscal_year, 10, 1),
        financials=financials,
        text_sections=FilingTextSections(
            item_1a_risk_factors="Our revenue could decline due to competition.",
            item_7_mdna="Revenue grew due to strong growth this year.",
            is_risk_factors_boilerplate=False,
        ),
    )


def _quarterly_report(**kwargs):
    current = kwargs.pop("current", None) or _quarter()
    prior = kwargs.pop("prior", None) or _quarter(
        accession="acc-q2", fiscal_period="Q2",
        period_end=date(2026, 3, 31), mdna=MDNA_Q2,
    )
    return build_report_data(
        current, prior, DICTIONARY,
        annual_filing=kwargs.pop("annual_filing", _annual()),
        prior_annual_filing=kwargs.pop("prior_annual_filing", _annual(
            fiscal_year=2024, accession="acc-fy-1")),
        **kwargs,
    )


# --- red flags stay on annual data --------------------------------------


def test_red_flags_are_computed_from_the_10k_not_from_the_quarter():
    report = _quarterly_report()
    assert report.piotroski_f.available
    assert report.annual_filing.fiscal_year == 2025
    # The report is about Q3 2026; the scores are about FY2025, and the
    # report keeps hold of which filing they came from so it can say so.
    assert report.filing.fiscal_period == "Q3"


def test_a_quarter_without_any_annual_filing_gets_no_scores_rather_than_wrong_ones():
    """
    Altman, Beneish and Piotroski are annual models. Run on a quarter
    they return a confident number that means nothing, and no reader
    could tell from the number itself. Refusing is the only honest
    output.
    """
    report = build_report_data(_quarter(), _quarter(accession="acc-q2"), DICTIONARY)
    for section in (report.altman_z, report.beneish_m, report.piotroski_f):
        assert not section.available
        assert "annual models" in section.unavailable_reason


def test_an_annual_report_still_scores_itself_without_extra_arguments():
    """The pre-existing annual path is untouched: no regression for 10-K reports."""
    report = build_report_data(_annual(), _annual(fiscal_year=2024, accession="acc-fy-1"), DICTIONARY)
    assert report.altman_z.available
    assert report.piotroski_f.available


# --- the risk-factor alert ----------------------------------------------


def test_boilerplate_item_1a_reports_one_line_and_does_not_fire():
    report = _quarterly_report()
    alert = report.risk_alert
    assert alert is not None
    assert alert.triggered is False
    assert "aucun changement significatif" in alert.headline


def test_writing_real_risk_text_into_a_10q_fires_the_alert():
    """
    The rare, high-signal case: counsel would not have let the standard
    clause stand if nothing had changed.
    """
    current = _quarter(
        risk_factors=(
            "Supply Chain Concentration\n\n"
            "A single supplier provides substantially all of our advanced packaging "
            "capacity, and that supplier has notified us of a capacity reduction.\n"
        ),
        boilerplate=False,
    )
    report = _quarterly_report(current=current)
    alert = report.risk_alert
    assert alert.triggered is True
    assert "Alerte" in alert.headline
    assert alert.total_added_sentences >= 1


def test_the_alert_does_not_apply_to_an_annual_report():
    """
    A 10-K rewriting its Item 1A is routine, not an event. Firing there
    would make the alert meaningless.
    """
    assert evaluate_risk_alert(_annual(), None) is None


def test_removals_in_a_quarterly_item_1a_are_counted_but_never_called_a_signal():
    """
    A 10-Q that writes its own risk factors restates only the ones it is
    updating; the rest stay on file and in force. So the diff shows most
    of the prior filing as removed, which is a formatting artefact. The
    prompt has to carry that warning, or the model will write a confident
    paragraph about a company withdrawing its risk disclosures.
    """
    current = _quarter(
        risk_factors="Supply Chain\n\nA new tariff applies to our imports.\n",
        boilerplate=False,
    )
    prior = _quarter(
        accession="acc-q2", fiscal_period="Q2", period_end=date(2026, 3, 31),
        mdna=MDNA_Q2,
        risk_factors=(
            "Supply Chain\n\nWe depend on third party foundries.\n"
            "Our margins may fall if wafer prices rise.\n"
        ),
        boilerplate=False,
    )
    report = _quarterly_report(current=current, prior=prior)
    assert report.risk_alert.removed_sentence_count > 0
    context = build_prompt_context(report)
    assert "n'en conclus PAS que la societe a retire un risque" in context


# --- sub-theme selection follows the form type --------------------------


def test_a_quarter_selects_sub_themes_from_the_mdna():
    """
    A 10-Q's Item 1A is the boilerplate clause, so there is nothing there
    to select. The quarter's actual discussion is in Part I Item 2.
    """
    assert default_section_for(_quarter()) == "mdna"
    report = attach_theme_selection(_quarterly_report(), api_key=None)
    selection = report.theme_selection.value
    assert selection.section_key == "mdna"
    assert "Results of Operations" in selection.headings


def test_an_annual_report_still_selects_from_risk_factors():
    assert default_section_for(_annual()) == "risk_factors"


def test_the_selection_lists_every_named_sub_section_of_the_quarter():
    report = attach_theme_selection(_quarterly_report(), api_key=None)
    headings = report.theme_selection.value.headings
    assert "Results of Operations" in headings
    assert "Liquidity and Capital Resources" in headings


def test_the_selection_marks_the_mdna_diff_and_leaves_the_rest_unselected():
    """
    With a key, the model's pick is applied to the MD&A diff: the chosen
    sub-theme is marked selected and the boilerplate one is not. Nothing
    is dropped -- an unselected sub-theme still exists, is still counted,
    and still appears in the detail report.
    """
    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"content": [{"type": "text", "text": '["Results of Operations"]'}]}

    from equity_analyzer.report import theme_selection as ts

    original_post = ts.requests.post
    ts.requests.post = lambda *a, **k: _Response()
    try:
        report = attach_theme_selection(_quarterly_report(), api_key="sk-test")
    finally:
        ts.requests.post = original_post

    assert report.theme_selection.value.ai_selected is True
    by_heading = {g.heading: g.selected for g in report.mdna_diff.value.groups}
    assert by_heading["Results of Operations"] is True
    assert by_heading["Critical Accounting Estimates"] is False
    # and the unselected one is still there, not deleted
    assert "Critical Accounting Estimates" in by_heading


# --- what the model is actually sent ------------------------------------


def test_the_prompt_names_both_filings_being_compared():
    context = build_prompt_context(attach_theme_selection(_quarterly_report(), api_key=None))
    assert "10-Q Q3 2026" in context
    assert "10-Q Q2 2026" in context


def test_the_prompt_sends_the_mdna_as_the_material_for_a_quarter():
    context = build_prompt_context(attach_theme_selection(_quarterly_report(), api_key=None))
    assert "MD&A (Item 2)" in context
    # the real changed sentence, verbatim -- the model is asked to quote
    # it, so it has to have been sent it
    assert "increase approximately 5%" in context


def test_the_prompt_warns_when_the_previous_filing_is_the_annual_report():
    """
    At a Q1 boundary the prior filing is the 10-K, whose MD&A discusses a
    whole year at much greater length. The volume of change is then an
    artefact of the two document formats, and the model has to be told
    so before it reads significance into it.
    """
    report = build_report_data(
        _quarter(fiscal_period="Q1", period_end=date(2025, 11, 30)),
        _annual(),
        DICTIONARY,
        annual_filing=_annual(),
    )
    context = build_prompt_context(report)
    assert "le depot precedent est le rapport ANNUEL" in context
    assert "regle 3 bis" in context


def test_the_prompt_states_the_risk_clause_in_one_line_when_nothing_happened():
    context = build_prompt_context(_quarterly_report())
    risk_lines = [ln for ln in context.split("\n") if "Item 1A" in ln]
    assert len(risk_lines) == 1


# --- what the reader sees -----------------------------------------------


def test_the_report_names_the_quarter_and_what_it_is_compared_to():
    html = render_html(_quarterly_report())
    assert "T3 2026" in html
    assert "Comparé au dépôt précédent" in html
    assert "T2 2026" in html


def test_the_report_says_which_year_the_red_flags_come_from():
    html = render_html(_quarterly_report())
    assert "10-K de l'exercice 2025" in html
    assert "pas sur le trimestre analysé" in html


def test_a_fired_alert_appears_on_page_one_above_the_summary():
    current = _quarter(
        risk_factors="Supply Chain\n\nA new tariff applies to our imports.\n",
        boilerplate=False,
    )
    html = render_html(_quarterly_report(current=current))
    assert html.index("Alerte") < html.index("Résumé exécutif")


def test_an_unfired_alert_stays_on_page_two_as_a_single_line():
    html = render_html(_quarterly_report())
    break_idx = html.index('<div class="page-break">')
    assert html.index("aucun changement significatif") > break_idx
    assert "Alerte" not in html


def test_the_quarterly_worst_case_still_fits_two_pages():
    """
    Page 1 now carries two things it didn't before: a line naming the
    filing being compared against, and, in the rare fired case, the risk
    alert above the summary. Both eat into the room reserved for a
    summary written to fill the page, so the worst case has to be
    measured rather than assumed still fine.
    """
    import dataclasses

    from equity_analyzer.report.html_renderer import _MAX_SUMMARY_WORDS
    from equity_analyzer.report.pdf_renderer import page_count, render_pdf
    from equity_analyzer.report.report_data import SectionResult

    current = _quarter(
        risk_factors=(
            "Supply Chain Concentration\n\n"
            "A single supplier provides substantially all of our advanced packaging "
            "capacity, and that supplier has notified us of a capacity reduction "
            "that we expect to constrain our output for several quarters.\n"
        ),
        boilerplate=False,
    )
    report = attach_theme_selection(_quarterly_report(current=current), api_key=None)
    sentence = "Cette phrase de test a une longueur representative d'une note reelle. "
    at_cap = " ".join((sentence * 60).split()[:_MAX_SUMMARY_WORDS])
    report = dataclasses.replace(
        report,
        ai_summary=SectionResult(
            value={"text": at_cap, "model": "test-model"}, unavailable_reason=None
        ),
    )
    assert report.risk_alert.triggered  # the heavier of the two page-1 layouts
    assert page_count(render_pdf(render_html(report))) == 2
