"""
The document itself: what lands on which page, and the two-page
guarantee.

THE PAGE COUNTS HERE ARE MEASURED, NOT ASSERTED FROM A STYLESHEET. Each
one renders a real PDF and counts its real page objects. That is the
only check that catches the failure this constraint exists to prevent,
because a stylesheet that looks like it fits and a document that fits
are different claims, and the earlier version of this project found two
genuine rendering bugs that its entire green test suite had missed.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from equity_analyzer.data_layer.models import FilingTextSections, FormType, PeriodDuration
from equity_analyzer.report import html_renderer
from equity_analyzer.report.fonts import font_face_css
from equity_analyzer.report.html_renderer import MAX_READING_WORDS, render_html
from equity_analyzer.report.pdf_renderer import page_count, render_pdf, render_pdf_fitted
from equity_analyzer.report.report_data import build_call_report
from equity_analyzer.sentiment.lm_dictionary import load_lm_dictionary

from .factories import make_analysis, make_expectation, make_filing, make_transcript
from ..redflags.factories import make_period

DICTIONARY = load_lm_dictionary(
    Path(__file__).parent.parent / "fixtures" / "sample_lm_dictionary.csv"
)

# HOW MUCH FITS ON A PAGE IS A PROPERTY OF THE TYPEFACE, so the tests
# that assert something MUST overflow are pinned to the font the report
# actually ships with. Without Lato installed, xhtml2pdf falls back to
# Helvetica, which is narrower: those tests would then measure a face no
# reader ever sees, pass, and let a real overflow through. That is not a
# hypothetical. The page 1 cap was first calibrated on a machine with no
# Lato, every test was green, and CI went red on the first real run
# because the runner installs `fonts-lato`.
#
# The tests that assert something must FIT are left running everywhere:
# the fallback face is narrower, so a budget that holds in Lato holds in
# Helvetica too.
REPORT_FONT_INSTALLED = bool(font_face_css())
needs_report_font = pytest.mark.skipif(
    not REPORT_FONT_INSTALLED,
    reason=(
        "police du rapport (Lato) absente : xhtml2pdf se rabattrait sur "
        "Helvetica, plus etroite, et cette mesure porterait sur une fonte "
        "que personne ne lit. `apt-get install fonts-lato` pour l'activer."
    ),
)

_ANNUAL_PRIOR = dict(
    net_income=50_000, total_assets=1_000_000, long_term_debt=300_000,
    current_assets=400_000, current_liabilities=300_000, shares_outstanding=1_000_000,
    revenue=800_000, gross_profit=300_000, total_equity=500_000,
    total_liabilities=500_000, retained_earnings=200_000, operating_income=90_000,
)
_ANNUAL_CURRENT = dict(
    net_income=80_000, total_assets=1_000_000, operating_cash_flow=100_000,
    long_term_debt=200_000, current_assets=500_000, current_liabilities=250_000,
    shares_outstanding=1_000_000, revenue=1_000_000, gross_profit=450_000,
    total_equity=600_000, total_liabilities=400_000, retained_earnings=280_000,
    operating_income=150_000,
)

READING = """## Verdict
Plutot bullish. La direction releve sa guidance de prix et l'assume dans la Q&A.

## Face aux attentes
Le trimestre bat le consensus de 8%. "We came in ahead of our own outlook" dit le CFO,
mais il ne repete pas l'exercice pour le trimestre suivant.

## Les declarations cles
- "We expect average selling prices to increase approximately 5%" (CEO). Sur une base de
  cout stable, cela se transmet presque integralement a la marge brute.
- "Capex will remain within the range we gave in February" (CFO).

## Les esquives
Un analyste demande la part du plus gros client. La reponse parle de diversification
sans jamais donner le pourcentage.

## A surveiller
- La hausse de prix de 5% apparait elle dans la marge brute du prochain trimestre ?
- La concentration client est elle publiee au prochain 10-Q ?
"""


def _annual(year, **metrics):
    return make_filing(
        form_type=FormType.TEN_K, fiscal_year=year, fiscal_period="FY",
        period_end=date(year, 12, 31),
        financials=make_period(
            duration=PeriodDuration.TWELVE_MONTH, fiscal_year=year, fiscal_period="FY",
            period_end=date(year, 12, 31), accession_number=f"acc-{year}", **metrics,
        ),
    )


def _quarter_filing():
    return make_filing(
        form_type=FormType.TEN_Q, fiscal_year=2026, fiscal_period="Q1",
        period_end=date(2025, 12, 31),
        text_sections=FilingTextSections(
            item_1a_risk_factors=None,
            item_7_mdna=(
                "Revenue increased on strong demand and improved pricing. "
                "Margins benefited from favorable input costs."
            ),
            item_9a_controls=None,
            is_risk_factors_boilerplate=False,
        ),
    )


def build_report(reading=READING, **overrides):
    kwargs = dict(
        call_quarter="2026Q1",
        expectation=make_expectation(),
        expectations_history=[
            make_expectation(period_end=date(2025, 9, 30), surprise_pct=2.1),
            make_expectation(period_end=date(2025, 6, 30), surprise_pct=-1.4),
        ],
        quarter_filing=_quarter_filing(),
        annual_filing=_annual(2024, **_ANNUAL_CURRENT),
        prior_annual_filing=_annual(2023, **_ANNUAL_PRIOR),
        lm_dictionary=DICTIONARY,
        source_filing_url="https://www.sec.gov/Archives/edgar/data/1/000.htm",
        generated_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    kwargs.update(overrides)
    return build_call_report(
        "TEST", "Test Company Inc.", "0000000001",
        make_transcript(
            prepared="Revenue grew strongly this quarter and margins improved on good demand.",
            qa="An analyst asked about pricing. The answer was vague and uncertain.",
        ),
        make_analysis(text=reading),
        **kwargs,
    )


# -- What lands where ---------------------------------------------------


def test_page_one_is_the_reading_and_page_two_is_the_numbers():
    html = render_html(build_report())
    break_at = html.index('class="page-break"')

    assert html.index("Les declarations cles") < break_at
    assert break_at < html.index("Red flags")
    assert break_at < html.index("Tonalité")


def test_the_expectations_sit_above_the_reading_not_below_it():
    """
    They are the frame the reading is written in. A reader should know
    the quarter beat by 8% before reading a paragraph about
    management's tone, not after.
    """
    html = render_html(build_report())
    assert html.index("Ce qui était attendu") < html.index("Les declarations cles")


def test_the_consensus_figures_appear_on_the_page():
    html = render_html(build_report())
    assert "1.10" in html      # attendu
    assert "1.19" in html      # publié
    assert "+8.2%" in html
    assert "au-dessus" in html


def test_an_unavailable_consensus_is_stated_on_the_page_with_its_reason():
    """
    A blank where a number belongs reads as "nothing to report". The
    reason has to be printed, because it changes how much the reading
    above can be trusted.
    """
    html = render_html(
        build_report(expectation=None, expectations_reason="quota Alpha Vantage épuisé")
    )
    assert "Consensus indisponible" in html
    assert "quota Alpha Vantage épuisé" in html


def test_a_stale_call_is_flagged_above_the_reading():
    html = render_html(build_report(quarters_back=1))
    assert "n'est pas encore publié" in html
    assert html.index("n'est pas encore publié") < html.index("Verdict")


def test_a_period_mismatch_warning_is_printed():
    """
    A wrong pairing between the quarter asked for and the call received
    is invisible in the output otherwise: the reading would be fluent,
    grounded and about the wrong three months.
    """
    html = render_html(
        build_report(period_warning="demandé 2026Q1 mais la société annonce 2025Q3")
    )
    assert "2025Q3" in html


def test_the_red_flags_say_which_year_they_come_from():
    """
    Page 2 sits right behind a page about one quarter's earnings call. A
    reader who assumed these described that quarter would read them
    wrong, and nothing in the numbers themselves would say otherwise.
    """
    html = render_html(build_report())
    assert "Modèles annuels" in html
    assert "2024" in html
    assert "jamais sur le trimestre lu en page 1" in html


def test_the_gap_between_script_and_qa_is_computed_not_left_to_the_reader():
    html = render_html(build_report())
    assert "Écart script contre Q&amp;A" in html


def test_the_tone_table_carries_the_limit_of_the_method():
    """
    Loughran-McDonald counts words and does not handle negation. Printed
    next to the score, because a number on a page reads as a
    measurement unless it says otherwise.
    """
    html = render_html(build_report())
    assert "négation" in html


def test_filing_text_is_escaped():
    report = build_report()
    report = build_call_report(
        "TEST", 'Ampersand & <Co>', "0000000001",
        make_transcript(), make_analysis(text=READING), call_quarter="2026Q1",
    )
    html = render_html(report)
    assert "Ampersand &amp; &lt;Co&gt;" in html
    assert "<Co>" not in html


# -- The two-page guarantee ---------------------------------------------


def test_a_normal_report_is_exactly_two_pages():
    pdf = render_pdf(render_html(build_report()))
    assert pdf[:5] == b"%PDF-"
    assert page_count(pdf) == 2


def _filler(word_count):
    """A reading of exactly `word_count` whitespace separated words."""
    body = [f"mot{i}." if i % 12 == 11 else f"mot{i}" for i in range(word_count - 2)]
    return "## Verdict\n" + " ".join(body)


def test_a_reading_at_the_cap_still_leaves_the_report_at_two_pages():
    """
    This is the measurement that sets MAX_READING_WORDS. The prompt asks
    for 450 to 600 words; the cap sits above that range as a safety net
    for a model that overruns. If a future style change shrinks the real
    capacity of page 1, this fails here rather than on a real report.
    """
    pdf = render_pdf(render_html(build_report(reading=_filler(MAX_READING_WORDS))))
    assert page_count(pdf) == 2


@needs_report_font
def test_the_cap_sits_at_the_real_limit_and_not_far_below_it(monkeypatch):
    """
    The other half of the measurement, and the one that keeps the test
    above honest: without it MAX_READING_WORDS could be lowered to 50
    and everything would still pass while page 1 sat two thirds empty.
    Raising the cap by forty words has to actually overflow.
    """
    monkeypatch.setattr(html_renderer, "MAX_READING_WORDS", MAX_READING_WORDS + 40)
    pdf = render_pdf(render_html(build_report(reading=_filler(MAX_READING_WORDS + 40))))
    assert page_count(pdf) == 3


@needs_report_font
def test_the_cap_plus_every_caveat_is_compacted_back_to_two_pages():
    """
    The tight case, handled downstream rather than by lowering the cap
    for everyone: a stale call AND a period mismatch both warning at
    once costs page 1 around sixty words of room. That combination is
    rare and, when it happens, the report needs those warnings more than
    it needs a roomy page, so the fitter absorbs it.
    """
    report = build_report(
        reading=_filler(MAX_READING_WORDS - 1),
        quarters_back=2,
        period_warning=(
            "demandé 2026Q1 mais la société annonce 2025Q3 (« third quarter of fiscal "
            "2025 »), appariement à vérifier avant de conclure quoi que ce soit"
        ),
    )
    html = render_html(report)

    assert page_count(render_pdf(html)) > 2, "ce cas doit vraiment déborder au naturel"
    assert page_count(render_pdf_fitted(html, max_pages=2)) == 2


def test_an_overlong_reading_is_truncated_and_the_report_says_so():
    """
    The cap allows for the note: a reading that HAS been truncated
    carries the sentence saying so, and the page still has to hold both.
    """
    html = render_html(build_report(reading=_filler(MAX_READING_WORDS + 400)))

    assert "Lecture tronquée" in html
    assert f"mot{MAX_READING_WORDS + 300}" not in html
    assert page_count(render_pdf(html)) == 2


@needs_report_font
def test_the_worst_realistic_case_is_compacted_back_to_two_pages():
    """
    Built to overflow from both ends at once: a reading at the cap with
    both page 1 caveats firing, and a page 2 where every red flag is
    unavailable with a long reason and Piotroski names failed criteria.
    Verified to ACTUALLY overflow at natural size first, otherwise the
    second half of this test would prove nothing.
    """
    failing_prior = dict(_ANNUAL_CURRENT)
    failing_current = dict(
        _ANNUAL_PRIOR, operating_cash_flow=10, shares_outstanding=2_000_000
    )
    report = build_report(
        reading=_filler(MAX_READING_WORDS),
        annual_filing=_annual(2024, **failing_current),
        prior_annual_filing=_annual(2023, **failing_prior),
        period_warning=(
            "demandé 2026Q1 mais la société annonce 2025Q3 (« third quarter of fiscal "
            "2025 »), appariement à vérifier avant de conclure quoi que ce soit"
        ),
        quarters_back=2,
    )
    html = render_html(report)

    assert page_count(render_pdf(html)) > 2, "le pire cas doit vraiment déborder"
    assert page_count(render_pdf_fitted(html, max_pages=2)) == 2


# -- Style constraints --------------------------------------------------


_HEX_COLOUR_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def test_the_report_uses_no_colour_anywhere():
    """
    Every distinction the stylesheet makes is carried by weight, a rule,
    an indent or a word, so nothing is lost printed in black and white.
    A future style tweak cannot reintroduce red and green without
    failing here.
    """
    html = render_html(build_report())
    for match in _HEX_COLOUR_RE.finditer(html):
        value = match.group(1)
        if len(value) == 3:
            channels = [c * 2 for c in value]
        else:
            channels = [value[i:i + 2] for i in (0, 2, 4)]
        assert len(set(c.lower() for c in channels)) == 1, f"couleur trouvée : #{value}"


def test_no_em_dash_reaches_the_rendered_page():
    """
    Em dashes used as punctuation are a tell of generated text. Banned
    from the template as well as from the model's answer, so that
    relaxing one side cannot quietly relax the other.
    """
    html = render_html(build_report())
    assert "—" not in html
    assert "–" not in html


def test_every_page_carries_its_number():
    html = render_html(build_report())
    assert "<pdf:pagenumber" in html
    assert "<pdf:pagecount" in html


def test_the_footer_says_where_every_part_came_from():
    """
    The page makes checkable claims: quotes from a transcript, scores
    from a 10-K, a reading by a named model. A reader who wants to
    verify one needs to know which source to open.
    """
    html = render_html(build_report())
    assert "Alpha Vantage" in html
    assert "claude-sonnet-5" in html
    assert "SEC EDGAR" in html


def test_a_missing_xbrl_metric_is_said_in_french_and_names_the_metric():
    """
    The red flag modules raise in English, shaped for a traceback:
    "FinancialPeriod (current, accession 0000-...) is missing
    'receivables'; cannot compute Beneish M-Score." Three lines of
    accession number in a report cell crowd out the one fact the reader
    needs, which is which figure the 10-K did not carry.
    """
    html = render_html(build_report())
    assert "donnée absente du 10-K : receivables" in html
    assert "cannot compute" not in html


def test_a_reason_the_report_does_not_recognise_is_shown_verbatim():
    """
    A message this layer cannot parse is still a reason the reader is
    entitled to see. Swallowing it would turn an explained gap into a
    blank.
    """
    from equity_analyzer.report.html_renderer import _readable_reason

    assert _readable_reason("quelque chose d'inattendu") == "quelque chose d'inattendu"


def test_the_mdna_label_follows_the_form_the_quarter_was_reported_in():
    """
    One quarter in four is reported in the 10-K rather than a 10-Q, and
    the MD&A is Item 7 there, not Item 2. A hardcoded "10-Q (Item 2)"
    would be wrong on a quarter of all reports, and wrong in the
    direction that sends a reader looking for a document that does not
    exist.
    """
    # "&" is escaped on the way into the page, so this is the string a
    # reader actually gets.
    quarterly = render_html(build_report())
    assert "MD&amp;A du 10-Q (Item 2)" in quarterly

    annual_quarter = make_filing(
        form_type=FormType.TEN_K, fiscal_year=2026, fiscal_period="FY",
        period_end=date(2026, 6, 30),
        text_sections=_quarter_filing().text_sections,
    )
    annual = render_html(build_report(quarter_filing=annual_quarter))
    assert "MD&amp;A du 10-K (Item 7)" in annual
    assert "MD&amp;A du 10-Q" not in annual
