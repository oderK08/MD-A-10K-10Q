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
# estimated, and measured WITH THE PRODUCTION FONT EMBEDDED. That last
# part is not a detail: the first version of this number was calibrated
# on a machine where Lato was not installed, so the report fell back to
# Helvetica, which is narrower. The number looked right, every test was
# green, and CI went red on the first real run because the runner
# installs `fonts-lato` and therefore renders the wider face. A page
# budget measured in the wrong typeface is not a measurement.
#
# The Lato numbers, with the header, the consensus strip and the beat
# history all present:
#   620 mots  -> 2 pages      625 mots -> 3 pages
#   610 mots  -> 2 pages once the "reading truncated" note is added too,
#                and that note is why the cap is 610 and not 620.
# The prompt asks for 450 to 600, so this still sits above the requested
# range: it is a safety net for a model that overruns, not the target.
#
# One case is tighter and is handled downstream rather than by lowering
# this number for everyone: when BOTH page 1 caveats fire at once (a
# stale call AND a period mismatch, which is rare and means the report
# needs those warnings more than it needs a roomy page), real capacity
# drops to around 540 words and the natural render runs to three pages.
# `save_pdf(..., max_pages=2)` compacts it back, which is the mechanism
# this project already chose over letting a report overflow.
#
# Frozen from both sides by tests, and those tests refuse to run at all
# when the production font is missing rather than quietly re-measuring
# Helvetica (see tests/report/test_call_report.py).
MAX_READING_WORDS = 610


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
    return f"""
    <h1>{_t(report.company_name)} ({_t(report.ticker)})</h1>
    <p class="subtitle">
      Earnings call {_t(_quarter_label(call.quarter))}{date_part}
      · transcript de {call.word_count} mots
      · CIK {_t(report.cik)}{link}
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


def _render_reading(report: CallReport) -> str:
    """Page 1's reason for existing: the model's reading of the call."""
    analysis = report.analysis
    text, truncated = truncate_words(analysis.text, MAX_READING_WORDS)
    note = ""
    if truncated:
        note = (
            '<p class="muted">Lecture tronquée en fin de section pour tenir en une '
            "page. Le transcript intégral reste en cache et l'analyse peut être "
            "relancée.</p>"
        )
    return f'<div class="reading">{markdown_to_html(text)}{note}</div>'


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
    return f"""
    <p class="footer">
      Généré le {report.generated_at.date().isoformat()}.
      Transcript : {_t(report.call.source)}, {report.call.word_count} mots.
      Lecture : {_t(getattr(analysis, "model", "n/a"))}.
      Chiffres : SEC EDGAR.{_t(filing_line)}
      Les citations de la page 1 sont vérifiables mot pour mot dans le transcript en cache.
    </p>
    """


# -- The document -------------------------------------------------------


def render_html(report: CallReport) -> str:
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

    THREE PAGES IS NOW A TARGET WITH A NET, NOT A GUARANTEE, and that is
    a deliberate trade. Page 2's length is a property of the call: a
    session that dodged eight questions has eight rows. The old two page
    promise held because everything on the page was bounded; this is
    not, so `save_pdf(..., max_pages=3)` compacts the stylesheet until it
    fits, and if even the tightest step overruns it returns that rather
    than dropping a finding. A slightly long report beats a report
    missing the row that mattered.

    Page 2 disappears entirely when there is no Q&A to dissect, and the
    document is two pages again. An empty page carrying six empty
    headings would be worse than no page.
    """
    title = f"{_t(report.company_name)} · earnings call {_t(report.call.quarter)}"
    body = f"""
  {_render_header(report)}
  {_render_caveats(report)}
  {_render_expectations(report)}
  {_render_reading(report)}
  {_render_qa_page(report)}
  <div class="page-break">
    {_render_red_flags(report)}
    {_render_tone(report)}
    {_render_provenance(report)}
  </div>
"""
    return _document(title, body)


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


__all__ = ["MAX_READING_WORDS", "render_html"]
