"""
Renders a CallReport into one self-contained HTML document (inline CSS,
no external assets).

Self-contained because the PDF renderer has no network access to fetch a
stylesheet or a font, and because a standalone HTML file is useful on
its own for checking a report in a browser before making a PDF.

THE DOCUMENT IS EXACTLY TWO PAGES, and that is a guarantee rather than a
target. Page 1 is the reading of the call and nothing else. Page 2 is
the numbers: the annual red flags and the tone. The break between them
is explicit, page 1's text is capped and truncated at a sentence
boundary if the model overruns, and the PDF renderer counts the real
pages of the real output and compacts the stylesheet until it fits (see
pdf_renderer.render_pdf_fitted). Nothing is ever dropped silently.

THREE STYLE CONSTRAINTS, all carried over deliberately because they were
explicit decisions rather than defaults:

  NO COLOUR. Every distinction the stylesheet makes is carried by
  weight, a rule, an indent or a word, so nothing is lost when the page
  is read or printed in black and white. Locked by a test that rejects
  any hex colour whose R, G and B are not equal.

  NO EM DASHES. Used as punctuation they are a tell of generated text.
  Banned from the template and from the model's answer alike. Hyphens
  inside words and identifiers (10-K, Loughran-McDonald, Z-Score) are
  ordinary and untouched.

  NOTHING THAT LOOKS LIKE A SLIDE. No tinted callout boxes, no badges,
  no emoji. The page is set like a plain analyst note.
"""

from __future__ import annotations

import re
from html import escape as _e
from typing import Optional

from .fonts import body_font_stack, font_face_css
from .markdown import markdown_to_html, truncate_words
from .report_data import CallReport, SectionResult


def _t(value) -> str:
    """
    Escape for a TEXT node: the three characters that would otherwise be
    read as markup, and not the apostrophe.

    `html.escape` turns "'" into "&#x27;" by default, which is correct
    inside an attribute and wrong here: this report is written in
    French, so nearly every sentence carries an apostrophe, and escaping
    them all turns the HTML source into something no one can read or
    grep. Attributes keep the strict `_e`.
    """
    return _e(str(value), quote=False)


# @page / @frame is xhtml2pdf's own (non-standard) mechanism for a footer
# that repeats on every page, confirmed by rendering a real multi-page
# PDF and inspecting each page rather than assumed from documentation. A
# plain in-flow <pdf:pagenumber/> appears once, wherever it sits in the
# document flow; wrapping it in the #footer_content frame target is what
# makes it repeat.
_CSS_TEMPLATE = """
  %(font_faces)s
  @page {
    size: a4 portrait;
    margin: 1.9cm 1.9cm 1.7cm 1.9cm;
    @frame footer_frame {
      -pdf-frame-content: footer_content;
      bottom: 1cm; margin-left: 1.9cm; margin-right: 1.9cm; height: 1cm;
    }
  }
  body { font-family: %(body_font)s; color: #111; font-size: 9.5pt; line-height: 1.34; }
  h1 { font-size: 15pt; font-weight: bold; margin: 0 0 1pt 0; letter-spacing: -0.2pt; }
  h2 { font-size: 10.5pt; font-weight: bold; margin: 10pt 0 4pt 0;
       border-bottom: 0.6pt solid #111; padding-bottom: 2.5pt; }
  p { margin: 0 0 5pt 0; }
  /* Bullets are paragraphs with an explicit marker, not a <ul>: see
     markdown.markdown_to_html for why the default list marker cannot be
     used here. The negative indent hangs the wrapped lines. */
  p.bullet { margin: 0 0 3pt 0; padding-left: 9pt; text-indent: -9pt; }
  .subtitle { color: #555; margin: 0 0 2pt 0; font-size: 8.5pt; }
  .kicker { font-size: 7.5pt; letter-spacing: 0.8pt; text-transform: uppercase;
            color: #555; margin: 0 0 5pt 0; }
  table { border-collapse: collapse; width: 100%%; margin-top: 4pt; }
  th, td { text-align: left; padding: 2.2pt 5pt; border-bottom: 0.4pt solid #ccc; font-size: 8.5pt; }
  th { border-bottom: 0.6pt solid #111; font-weight: bold; }
  td.num, th.num { text-align: right; }
  .muted { font-size: 8pt; color: #666; font-style: italic; }
  .unavailable { color: #666; font-style: italic; }
  .note { font-size: 8pt; color: #444; margin-top: 3pt; }
  /* Severity, formerly colour coded: weight plus a word that says it. */
  .flag-on { font-weight: bold; }
  .flag-off { font-weight: normal; color: #444; }
  /* A caveat the reader must not skim past: a rule and an indent, not a
     tinted box. */
  .caveat { border-left: 1.5pt solid #111; padding: 1pt 0 1pt 7pt; margin: 0 0 6pt 0;
            font-size: 8.5pt; }
  .reading h2:first-child { margin-top: 6pt; }
  .footer { margin-top: 11pt; color: #666; font-size: 7.5pt;
            border-top: 0.4pt solid #bbb; padding-top: 4pt; }
  #footer_content { text-align: center; font-size: 7.5pt; color: #888; }
  .page-break { page-break-before: always; }
"""

# What page 1 actually holds, MEASURED against rendered PDFs rather than
# estimated. A FIXED WORD CAP WAS THE WRONG TOOL, and a real MSFT report
# showed why: page 1 was truncated in the middle of "A surveiller" while
# a third of the page sat empty. The cap has to cover the WORST layout
# (both caveats firing, which eats about sixty words of room), so on the
# common report where no caveat fires it cuts long before the page is
# full. A single number cannot be both the worst-case ceiling and the
# common-case target.
#
# So the reading is no longer cut by counting words. It is cut by
# MEASURING: page 1 is rendered on its own and the reading is kept as
# long as it stays on one page, whatever the caveats, the consensus
# strip or the font happen to cost this particular report. Nothing is
# truncated that fits, and nothing that fits is left off. See
# `_fit_reading`. This is the same "measure, don't guess" rule the page
# fitter already follows for the whole document.
#
# MAX_READING_WORDS survives only as a HARD CEILING on that search, so a
# model that returns three thousand words does not get three thousand
# rendered even if compaction could technically swallow them: past this,
# the page stops being a reading and becomes a wall. It sits well above
# what a page ever holds, so in practice the measurement binds first and
# this only catches the runaway.
MAX_READING_WORDS = 900

# Below this many words a reading always fits page 1, even with both
# caveats firing (worst-case capacity is around 540). Under it, the
# measurement is skipped entirely: no render, and the reading is kept
# whole. This keeps the common short reading free and the test suite
# fast, and means the measured path runs only when a reading is actually
# long enough to be at risk.
_ALWAYS_FITS_WORDS = 380


def _css() -> str:
    """
    The stylesheet with the report font resolved to real @font-face
    rules. Built at call time rather than import time: whether the font
    file exists is a property of the machine rendering the report, so a
    font installed after import still gets picked up.
    """
    return _CSS_TEMPLATE % {
        "font_faces": font_face_css(),
        "body_font": body_font_stack(),
    }


def _fmt_ratio(value) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_eps(value) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _quarter_label(quarter: str) -> str:
    """"2026Q2" reads as "T2 2026" to a French speaker."""
    year, _, number = str(quarter).partition("Q")
    if year.isdigit() and number.isdigit():
        return f"T{number} {year}"
    return quarter


# -- Page 1 -------------------------------------------------------------


def _render_header(report: CallReport) -> str:
    call = report.call
    # Two different facts, each labelled as what it is. The provider
    # stamps most transcripts with a fiscal label and no date, so the
    # release date from the consensus data is usually the only one there
    # is, and calling it "the call date" would claim a precision we do
    # not have.
    if call.call_date is not None:
        date_part = f" · call du {call.call_date.isoformat()}"
    elif call.release_date is not None:
        date_part = f" · résultats publiés le {call.release_date.isoformat()}"
    else:
        date_part = " · date du call non fournie par la source"
    link = (
        f' · <a href="{_e(report.source_filing_url)}">source SEC EDGAR</a>'
        if report.source_filing_url else ""
    )
    # No CIK line outside the SEC's reach: the company has none, and
    # printing "CIK " with nothing after it advertises a field the
    # report could not fill.
    identifier = f" · CIK {_t(report.cik)}" if report.cik else ""
    return f"""
    <h1>{_t(report.company_name)} ({_t(report.ticker)})</h1>
    <p class="subtitle">
      Earnings call {_t(_quarter_label(call.quarter))}{date_part}
      · transcript de {call.word_count} mots{identifier}{link}
    </p>
    """


def _render_caveats(report: CallReport) -> str:
    """
    The things that change how the page below should be read, above the
    page below.

    Both of these are cases where the report is still useful but means
    something different from what it appears to mean, and neither is
    visible from the reading itself. A stale call analysed as if it were
    current, or a call for a quarter other than the one requested, is
    exactly the kind of error that produces a confident and wrong note.
    """
    call = report.call
    caveats = []
    if call.quarters_back:
        caveats.append(
            f"Le call du dernier trimestre déposé n'est pas encore publié chez le "
            f"fournisseur. Ceci est le call disponible le plus récent, "
            f"{call.quarters_back} trimestre(s) en arrière."
        )
    if call.period_warning:
        caveats.append(f"Appariement de période : {call.period_warning}")
    if not call.verbatim:
        # Not a footnote. Page 1 quotes, and it quotes because a
        # provider transcript is an official written record a reader can
        # open and check. A speech-to-text transcript is accurate on
        # prose and unreliable on exactly the words that matter here: a
        # guidance number. "Fifteen" and "fifty" differ by one phoneme.
        caveats.append(
            "Ce transcript est une transcription automatique, pas le compte rendu "
            "officiel de la société. Les citations ci-dessous restituent fidèlement "
            "le propos mais peuvent contenir des erreurs de transcription, en "
            "particulier sur les chiffres. À vérifier avant de s'appuyer dessus."
        )
    if not caveats:
        return ""
    lines = "".join(f"<p>{_t(text)}</p>" for text in caveats)
    return f'<div class="caveat">{lines}</div>'


def _render_expectations(report: CallReport) -> str:
    """
    What the quarter was measured against, in one strip above the
    reading.

    Placed BEFORE the analysis and not after it, because it is the frame
    the analysis is written in: the reader should know the quarter beat
    by 8% before reading a paragraph about management's tone, not after.

    Kept to a single line plus a history line. The full beat and miss
    record is in the prompt, where it does work; on the page it would
    spend a third of page 1 restating a table the reader does not act on
    directly.
    """
    if not report.expectations.available:
        return (
            '<p class="note"><span class="flag-on">Consensus indisponible</span> : '
            f"{_t(report.expectations.unavailable_reason)}. La lecture ci-dessous "
            "n'a pas pu situer le trimestre par rapport aux attentes.</p>"
        )

    expectation = report.expectations.value
    history = [
        q for q in report.expectations_history
        if q.surprise_pct is not None
    ]
    history_html = ""
    if history:
        record = " · ".join(
            f"{q.fiscal_date_ending.isoformat()} {q.surprise_pct:+.1f}%" for q in history
        )
        history_html = f'<p class="note">Trimestres précédents : {_t(record)}</p>'

    surprise = (
        f"{expectation.surprise_pct:+.1f}%" if expectation.surprise_pct is not None else "n/a"
    )
    # An ecart this wide is not a surprise, it is two numbers struck on
    # different bases (see earnings_expectations.IMPLAUSIBLE_SURPRISE_PCT).
    # Printed as a caveat rather than swallowed: the figures are real and
    # worth showing, it is the COMPARISON that is not.
    doubt = ""
    if not expectation.comparable:
        doubt = (
            '<p class="note"><span class="flag-on">Comparaison non exploitable</span> : '
            "un écart de cette ampleur indique deux bases différentes. Le BPA publié "
            "est le chiffre GAAP, un consensus d'analystes est établi sur une base "
            "ajustée. Les deux chiffres sont exacts, leur écart ne dit rien du trimestre.</p>"
        )
    return f"""
    <p class="kicker">Ce qui était attendu</p>
    <table>
      <tr>
        <th>BPA consensus</th>
        <th class="num">BPA publié</th>
        <th class="num">Écart</th>
        <th>Lecture</th>
      </tr>
      <tr>
        <td>{_fmt_eps(expectation.estimated_eps)}</td>
        <td class="num">{_fmt_eps(expectation.reported_eps)}</td>
        <td class="num">{_t(surprise)}</td>
        <td><span class="flag-on">{_t(expectation.verdict)}</span></td>
      </tr>
    </table>
    {doubt}
    {history_html}
    """


_TRUNCATION_NOTE = (
    '<p class="muted">Lecture tronquée en fin de section pour tenir en une '
    "page. Le transcript intégral reste en cache et l'analyse peut être "
    "relancée.</p>"
)


def _reading_html(text: str, truncated: bool) -> str:
    note = _TRUNCATION_NOTE if truncated else ""
    return f'<div class="reading">{markdown_to_html(text)}{note}</div>'


def _page_one_pages(report: CallReport, reading_html: str, css: str = "") -> int:
    """
    How many pages page 1 ALONE takes: the header, the caveats, the
    consensus strip and the reading, rendered as a standalone document
    with the same stylesheet and footer as the real one.

    `css` applies the SAME compaction override the whole document will
    use, so the reading is fitted to the room it will really have. This
    is the fix for the bug the user hit: fitting page 1 at natural size
    and then letting the document compact its font left the reading
    filling only part of a now smaller page.
    """
    from .pdf_renderer import _with_override_css, page_count, render_pdf

    body = (
        f"{_render_header(report)}{_render_caveats(report)}"
        f"{_render_expectations(report)}{reading_html}"
    )
    doc = _document("page 1", body)
    if css:
        doc = _with_override_css(doc, css)
    return page_count(render_pdf(doc))


def _fit_reading_words(report: CallReport, css: str = "") -> Optional[int]:
    """
    The largest reading, in words, that keeps page 1 to one page AT THE
    GIVEN compaction level. None means the whole reading fits and nothing
    is trimmed.

    Measured, so it holds for whatever font the final document uses: a
    heavily compacted document has a smaller font, so page 1 holds MORE
    words, so less is trimmed and no white space is left.
    """
    full = report.analysis.text or ""
    ceiling, _ = truncate_words(full, MAX_READING_WORDS)
    trimmed_to_ceiling = ceiling != full

    if len(ceiling.split()) <= _ALWAYS_FITS_WORDS:
        return None if not trimmed_to_ceiling else MAX_READING_WORDS
    if _page_one_pages(report, _reading_html(ceiling, trimmed_to_ceiling), css) <= 1:
        return None if not trimmed_to_ceiling else MAX_READING_WORDS

    words = ceiling.split()
    lo, hi, best = _ALWAYS_FITS_WORDS, len(words), _ALWAYS_FITS_WORDS
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate, _ = truncate_words(full, mid)
        if _page_one_pages(report, _reading_html(candidate, True), css) <= 1:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _render_reading(report: CallReport, reading_words: Optional[int] = None) -> str:
    """
    Page 1's reason for existing: the model's reading of the call.

    `reading_words` is how many words to keep. None means the whole
    reading, up to the hard ceiling; an int is the fit computed by the
    orchestrator for the compaction level the document will use. Fitting
    is NOT done here any more, so this function does no rendering and the
    document assembly stays cheap. See `render_report_pdf`.
    """
    full = report.analysis.text or ""
    limit = MAX_READING_WORDS if reading_words is None else min(reading_words, MAX_READING_WORDS)
    text, truncated = truncate_words(full, limit)
    return _reading_html(text, truncated)


# -- Page 2 -------------------------------------------------------------


_ZONE_LABELS = {"safe": "sûre", "grey": "grise", "distress": "détresse"}


# The red flag modules raise in English, with a message shaped for a
# developer reading a traceback: "FinancialPeriod (current, accession
# 0000-00-000001) is missing 'receivables'; cannot compute Beneish
# M-Score." That is the right message at the point it is raised and the
# wrong one in a cell of a French report, where three lines of accession
# number crowd out the one fact the reader needs, which is WHICH FIGURE
# the 10-K did not carry.
_MISSING_METRIC_RE = re.compile(r"is missing '([a-z_]+)'", re.IGNORECASE)


def _readable_reason(reason: str) -> str:
    """
    The one message shape that actually occurs in practice, said in
    French. Anything else passes through untouched.

    Rewritten rather than translated wholesale: a message this layer
    does not recognise is shown verbatim, because a reason the report
    cannot parse is still a reason the reader is entitled to see. The
    metric name is kept exactly as the module named it, so it stays
    greppable against the XBRL tag list.
    """
    match = _MISSING_METRIC_RE.search(reason or "")
    if match is None:
        return reason
    return f"donnée absente du 10-K : {match.group(1)}"


def _unavailable_cell(section: SectionResult) -> str:
    return f'<td class="unavailable">{_t(_readable_reason(section.unavailable_reason))}</td>'


def _red_flag_rows(report: CallReport) -> str:
    """
    The three scores as rows of one compact table rather than three
    bordered cards: page 2 carries all of them plus the tone table, and
    cards spend most of their height on padding. Severity is bold text
    and a word, never colour.
    """
    rows = []

    if report.altman_z.available:
        z = report.altman_z.value
        emphasis = "flag-on" if z.zone == "distress" else "flag-off"
        rows.append(
            f'<tr><td>Altman Z-Score <span class="muted">({_t(z.variant)})</span></td>'
            f'<td class="num">{_fmt_ratio(z.score)}</td>'
            f'<td><span class="{emphasis}">zone {_t(_ZONE_LABELS.get(z.zone, z.zone))}</span></td></tr>'
        )
    else:
        rows.append(
            '<tr><td>Altman Z-Score</td><td class="num">n/a</td>'
            f"{_unavailable_cell(report.altman_z)}</tr>"
        )

    if report.beneish_m.available:
        m = report.beneish_m.value
        emphasis = "flag-on" if m.flagged else "flag-off"
        label = "signalé" if m.flagged else "non signalé"
        rows.append(
            f'<tr><td>Beneish M-Score <span class="muted">(seuil {_fmt_ratio(m.threshold)})</span></td>'
            f'<td class="num">{_fmt_ratio(m.score)}</td>'
            f'<td><span class="{emphasis}">{label}</span></td></tr>'
        )
    else:
        rows.append(
            '<tr><td>Beneish M-Score</td><td class="num">n/a</td>'
            f"{_unavailable_cell(report.beneish_m)}</tr>"
        )

    if report.piotroski_f.available:
        f = report.piotroski_f.value
        failed = [name.replace("_", " ") for name, passed in f.criteria.items() if not passed]
        # Only the FAILED criteria are named. The passed ones are implied
        # by the score and would cost a third of the page to list.
        detail = "tous critères validés" if not failed else "échoue : " + ", ".join(failed)
        rows.append(
            '<tr><td>Piotroski F-Score</td>'
            f'<td class="num">{f.score} / {f.max_score}</td>'
            f"<td>{_t(detail)}</td></tr>"
        )
    else:
        rows.append(
            '<tr><td>Piotroski F-Score</td><td class="num">n/a</td>'
            f"{_unavailable_cell(report.piotroski_f)}</tr>"
        )

    return "\n".join(rows)


def _red_flag_source_note(report: CallReport) -> str:
    """
    Says which filing the three scores were computed from.

    Not a footnote. Page 1 is about one quarter's earnings call and page
    2 sits right behind it, so a reader who assumed these numbers
    described that quarter would read them wrong, and nothing in the
    numbers themselves would tell them otherwise. They are annual
    models on annual filings, and they describe the company's underlying
    health rather than the news of the quarter.
    """
    annual = report.annual_filing
    if annual is None:
        return (
            '<p class="note">Altman, Beneish et Piotroski sont des modèles annuels. '
            "Faute de 10-K exploitable, ils ne sont pas calculés : un score obtenu "
            "sur un trimestre serait une erreur de catégorie, pas une approximation.</p>"
        )
    prior = report.prior_annual_filing
    against = f", comparé à l'exercice {prior.fiscal_year}" if prior is not None else ""
    return (
        f'<p class="note">Modèles annuels : calculés sur le 10-K de l\'exercice '
        f"{annual.fiscal_year}{against}, jamais sur le trimestre lu en page 1. "
        f"Ils décrivent la santé de fond de la société, pas les nouvelles du trimestre.</p>"
    )


def _tone_row(label: str, section: SectionResult) -> str:
    if not section.available:
        return (
            f'<tr><td>{_t(label)}</td><td class="num">n/a</td>'
            f"{_unavailable_cell(section)}</tr>"
        )
    result = section.value
    tone = result.net_tone
    direction = "positive" if tone > 0.1 else "négative" if tone < -0.1 else "neutre"
    return (
        f'<tr><td>{_t(label)}</td><td class="num">{_fmt_ratio(tone)}</td>'
        f"<td>{direction} · {result.total_word_count} mots analysés</td></tr>"
    )


def _tone_gap_note(report: CallReport) -> str:
    """
    The number worth reading on this table is not either tone, it is the
    distance between them.

    Prepared remarks are written, lawyered and rehearsed, so their tone
    is a decision. The Q&A is unscripted. A script markedly warmer than
    the answers that follow it is a specific and common pattern, and it
    is invisible if the reader has to subtract two numbers themselves.
    """
    if not (report.tone_prepared.available and report.tone_qa.available):
        return ""
    gap = report.tone_prepared.value.net_tone - report.tone_qa.value.net_tone
    if abs(gap) < 0.10:
        reading = "le ton du script et celui des réponses spontanées sont alignés"
    elif gap > 0:
        reading = (
            "le script est nettement plus positif que les réponses spontanées, "
            "écart à interpréter comme une prudence non écrite"
        )
    else:
        reading = (
            "les réponses spontanées sont plus positives que le script, "
            "cas plus rare, souvent un management plus confiant que ses juristes"
        )
    return f'<p class="note">Écart script contre Q&amp;A : {gap:+.2f}, {reading}.</p>'


def _tone_caveat() -> str:
    return (
        '<p class="note">Loughran-McDonald compte des mots, il ne comprend pas de '
        "phrases : la négation n'est pas gérée (« not profitable » compte "
        "« profitable » comme positif). Un score se lit comme une tendance, "
        "pas comme une mesure.</p>"
    )


def _render_red_flags(report: CallReport) -> str:
    return f"""
    <h2>Red flags</h2>
    <table>
      <tr><th>Indicateur</th><th class="num">Score</th><th>Lecture</th></tr>
      {_red_flag_rows(report)}
    </table>
    {_red_flag_source_note(report)}
    """


def _mdna_label(report: CallReport) -> str:
    """
    The MD&A's item number depends on the form: Item 2 in a 10-Q, Item 7
    in a 10-K. Not cosmetic. One quarter in four is reported in the
    10-K rather than a 10-Q (see cik_lookup.latest_reported_period), so
    a hardcoded "10-Q" would be wrong on a quarter of all reports, and
    wrong in the direction that makes a reader look for a document that
    does not exist.
    """
    filing = report.quarter_filing
    if filing is None:
        return "MD&A du dépôt"
    form = getattr(filing.form_type, "value", filing.form_type)
    item = "Item 7" if form == "10-K" else "Item 2"
    return f"MD&A du {form} ({item})"


def _render_tone(report: CallReport) -> str:
    mdna_label = _mdna_label(report)
    return f"""
    <h2>Tonalité (Loughran-McDonald)</h2>
    <table>
      <tr><th>Texte</th><th class="num">Tonalité nette</th><th>Lecture</th></tr>
      {_tone_row("Call, remarques préparées", report.tone_prepared)}
      {_tone_row("Call, questions et réponses", report.tone_qa)}
      {_tone_row(mdna_label, report.tone_mdna)}
    </table>
    {_tone_gap_note(report)}
    {_tone_caveat()}
    """


def _render_provenance(report: CallReport) -> str:
    analysis = report.analysis
    filing_line = ""
    if report.quarter_filing is not None:
        filing = report.quarter_filing
        form = getattr(filing.form_type, "value", filing.form_type)
        filing_line = (
            f" {form} {filing.fiscal_period} {filing.fiscal_year}, déposé le "
            f"{filing.filed_date.isoformat()}, accession {filing.accession_number}."
        )
    # No EDGAR line outside its perimeter: there are no SEC figures in an
    # international report, and claiming a source it does not have is
    # exactly the kind of quiet inaccuracy this project refuses.
    figures = "" if report.international else f" Chiffres : SEC EDGAR.{filing_line}"
    return f"""
    <p class="footer">
      Généré le {report.generated_at.date().isoformat()}.
      Transcript : {_t(report.call.source)}, {report.call.word_count} mots.
      Lecture : {_t(getattr(analysis, "model", "n/a"))}.{_t(figures)}
      Les citations de la page 1 sont vérifiables mot pour mot dans le transcript en cache.
    </p>
    """


# -- The document -------------------------------------------------------


def render_html(report: CallReport, reading_words: Optional[int] = None) -> str:
    """
    One document, three pages, in the order a reader works through them.

      PAGE 1  the reading of the call
      PAGE 2  the same call dissected: what was dodged, what was
              conceded, what slipped out with forward value
      PAGE 3  the numbers that do not come from the call at all

    THE ORDER IS THE ARGUMENT. Pages 1 and 2 are both the quarter's news
    and belong together, the second going back over the session the
    first only skims. Page 3 is the slowest moving thing here, the
    backdrop the quarter happened against, so it comes last rather than
    interrupting the two halves of the call.

    `reading_words` bounds the page 1 reading. None means the whole
    reading (up to the hard ceiling); the orchestrator `render_report_pdf`
    passes the fit it measured for the compaction the document will use.
    Pure string assembly, no rendering, so it is cheap to call in a loop.

    Page 2 disappears entirely when there is no Q&A to dissect, and the
    document is two pages again. An empty page carrying six empty
    headings would be worse than no page.
    """
    title = f"{_t(report.company_name)} · earnings call {_t(report.call.quarter)}"
    body = f"""
  {_render_header(report)}
  {_render_caveats(report)}
  {_render_expectations(report)}
  {_render_reading(report, reading_words)}
  {_render_qa_page(report)}
  {_render_backdrop(report)}
"""
    return _document(title, body)


def render_report_pdf(report: CallReport, max_pages: int) -> tuple:
    """
    The report as PDF bytes, and the page count it actually has.

    THE READING AND THE COMPACTION ARE FITTED TOGETHER, which is the fix
    for the white space the user saw on a GOOG report: page 1's reading
    was trimmed to fill a NATURAL-size page, then the whole document
    compacted its font because the Q&A was long, and the already-trimmed
    reading no longer filled the now smaller page.

    So it walks the compaction levels from none to tightest and, at each
    one, fits the reading to page 1 AT THAT level before rendering the
    whole document. The first level whose document fits the budget wins,
    with the reading filling page 1 at the exact font the reader sees. If
    even the tightest level overruns, that render is returned rather than
    dropping a finding, same policy as the old fitter.
    """
    from .pdf_renderer import _COMPACTION_STEPS, _with_override_css, page_count, render_pdf

    attempts = []
    for level, css in enumerate(["", *_COMPACTION_STEPS]):
        words = _fit_reading_words(report, css)
        html = render_html(report, reading_words=words)
        pdf = render_pdf(html if not css else _with_override_css(html, css))
        pages = page_count(pdf)
        if pages <= max_pages:
            return pdf, pages
        attempts.append((pages, level, pdf))

    # Nothing fits the budget. Return the best effort: the FEWEST pages,
    # and on a tie the LEAST compacted, because a document that is going
    # to overflow anyway should stay as readable as it can rather than be
    # shrunk to a tiny font for no gain.
    attempts.sort(key=lambda a: (a[0], a[1]))
    best_pages, _, best_pdf = attempts[0]
    return best_pdf, best_pages


def _render_backdrop(report: CallReport) -> str:
    """
    Page 3, or its absence.

    Outside the SEC's reach there is no 10-K to compute red flags on and
    no MD&A to score, and the Loughran-McDonald lexicon is English by
    construction, so the tone of a call held in another language would
    be noise dressed as a number. The whole block is therefore dropped,
    and provenance follows the Q&A on the same run of pages rather than
    forcing a near-empty third sheet. The reading and the Q&A already
    state, in their own words, what they were written without.
    """
    if report.international:
        return _render_provenance(report)
    return f"""<div class="page-break">
    {_render_red_flags(report)}
    {_render_tone(report)}
    {_render_provenance(report)}
  </div>"""


def _document(title: str, body: str) -> str:
    """The document shell: one stylesheet, one repeating page footer."""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>{_css()}</style>
</head>
<body>
  <div id="footer_content">Page <pdf:pagenumber /> / <pdf:pagecount /></div>
{body}
</body>
</html>
"""


# -- Page 2: the Q&A, laid out -------------------------------------------
#
# ONE DOCUMENT, by explicit user request after a first version shipped
# this as a separate PDF. Two files for one quarter is two things to
# find, and the second one gets lost. The page budget moved from two to
# three to make room rather than the section being trimmed to fit.

_SEVERITY_LABELS = {
    "high": "grave",
    "medium": "moyenne",
    "low": "faible",
}
_DIRECTION_LABELS = {
    "positive": "favorable",
    "negative": "défavorable",
    "neutral": "neutre",
}


def _qa_dodged(analysis) -> str:
    if not analysis.dodged_questions:
        return "<p class=\"muted\">Aucune esquive nette relevée.</p>"
    rows = []
    for item in analysis.dodged_questions:
        severity = str(item.get("severity", "")).lower()
        label = _SEVERITY_LABELS.get(severity, severity or "n/a")
        # Bold for the ones where a precise figure was asked for and
        # refused. Weight, not colour, like everywhere else.
        emphasis = "flag-on" if severity == "high" else "flag-off"
        rows.append(
            f"<tr><td>{_t(item.get('analyst') or 'analyste non nommé')}</td>"
            f"<td>{_t(item.get('question') or '')}</td>"
            f"<td>{_t(item.get('what_was_asked') or '')}</td>"
            f"<td>{_t(item.get('what_was_given') or '')}</td>"
            f'<td><span class="{emphasis}">{_t(label)}</span></td></tr>'
        )
    return f"""
    <table>
      <tr><th>Analyste</th><th>Question</th><th>Demandé</th><th>Obtenu</th><th>Gravité</th></tr>
      {"".join(rows)}
    </table>
    """


def _qa_guidance(analysis) -> str:
    if not analysis.implicit_guidance:
        return "<p class=\"muted\">Rien de prospectif glissé hors communiqué.</p>"
    rows = []
    for item in analysis.implicit_guidance:
        direction = str(item.get("direction", "")).lower()
        rows.append(
            f"<tr><td>{_t(item.get('topic') or '')}</td>"
            f"<td>{_t(item.get('signal') or '')}</td>"
            f"<td>{_t(item.get('buried_in') or '')}</td>"
            f"<td>{_t(_DIRECTION_LABELS.get(direction, direction or 'n/a'))}</td></tr>"
        )
    return f"""
    <table>
      <tr><th>Sujet</th><th>Signal prospectif</th><th>Glissé dans</th><th>Sens</th></tr>
      {"".join(rows)}
    </table>
    """


def _qa_concessions(analysis) -> str:
    if not analysis.concessions:
        return "<p class=\"muted\">Aucune concession relevée.</p>"
    return "\n".join(
        f'<p class="bullet">· <strong>{_t(item.get("topic") or "")}</strong> : '
        f'{_t(item.get("admission") or "")}'
        + (f' « {_t(item.get("verbatim"))} »' if item.get("verbatim") else "")
        + "</p>"
        for item in analysis.concessions
    )


def _qa_themes(analysis) -> str:
    if not analysis.recurring_themes:
        return ""
    rows = "".join(
        f"<tr><td>{_t(item.get('theme') or '')}</td>"
        f'<td class="num">{_t(item.get("analyst_count") or "n/a")}</td>'
        f"<td>{_t(item.get('summary') or '')}</td></tr>"
        for item in analysis.recurring_themes
    )
    return f"""
    <h2>Ce sur quoi plusieurs analystes sont revenus</h2>
    <p class="note">Le nombre d'analystes qui posent la même question est une
    mesure de ce que le marché n'a pas compris, ou n'a pas cru.</p>
    <table>
      <tr><th>Thème</th><th class="num">Analystes</th><th>Résumé</th></tr>
      {rows}
    </table>
    """


def _qa_lists(analysis) -> str:
    blocks = []
    if analysis.uncertain_figures:
        items = "\n".join(
            f'<p class="bullet">· {_t(figure)}</p>' for figure in analysis.uncertain_figures
        )
        blocks.append(
            "<h2>Chiffres à vérifier</h2>"
            '<p class="note">Repérés par le modèle comme probablement mal '
            "transcrits. À contrôler sur le communiqué avant de s'en servir.</p>"
            f"{items}"
        )
    return "\n".join(blocks)


def _qa_period_check(report: CallReport, analysis) -> str:
    """
    A third, independent check on the pairing.

    EDGAR said which quarter this is and `verify_against_declared` read
    the opening of the call. This is the model saying which period it
    thinks it just read, and it never overrides either of them: it only
    speaks up when it disagrees, because three sources agreeing is worth
    nothing to print and two disagreeing is worth a lot.
    """
    declared = (analysis.declared_period or "").strip()
    if not declared:
        return ""
    asked = report.call.quarter
    if declared == asked or _quarter_label(asked).lower() in declared.lower():
        return ""
    return (
        f'<div class="caveat"><p>La lecture situe cette session en '
        f"« {_t(declared)} », alors que le dépôt SEC désigne {_t(_quarter_label(asked))}. "
        f"Appariement à vérifier avant de conclure.</p></div>"
    )


def _render_qa_page(report: CallReport) -> str:
    """
    Page 2: the session, laid out. Empty string when there is nothing to
    lay out, so the document falls back to two pages rather than
    carrying a page of empty headings.
    """
    analysis = report.qa_analysis
    if analysis is None:
        return ""
    hard = len(analysis.hard_dodges)
    lede = (
        "Aucune question esquivée, aucune concession, rien de prospectif hors "
        "communiqué. Une session sans prise est elle aussi une information."
        if analysis.is_empty else
        f"{len(analysis.dodged_questions)} esquive(s) dont {hard} sur une donnée "
        f"chiffrée refusée, {len(analysis.concessions)} concession(s), "
        f"{len(analysis.implicit_guidance)} signal(aux) prospectif(s)."
    )
    return f"""
  <div class="page-break">
    <p class="kicker">Questions et réponses</p>
    <p class="lede">{lede}</p>
    {_qa_period_check(report, analysis)}
    <h2>Les esquives</h2>
    <p class="note">Gravité « grave » : une information chiffrée précise a été
    demandée et refusée.</p>
    {_qa_dodged(analysis)}
    <h2>Ce qui a valeur prospective, hors communiqué</h2>
    <p class="note">La partie la plus utile : ce qui engage l'avenir et ne
    figurait pas dans le communiqué de résultats.</p>
    {_qa_guidance(analysis)}
    <h2>Les concessions</h2>
    {_qa_concessions(analysis)}
    {_qa_themes(analysis)}
    {_qa_lists(analysis)}
  </div>
"""


__all__ = ["MAX_READING_WORDS", "render_html", "render_report_pdf"]
