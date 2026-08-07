"""
Renders a ReportData into a single self-contained HTML document (inline
CSS, no external assets) -- self-contained because the PDF renderer
(Module 5's other half) has no network access to fetch stylesheets or
fonts, and because a standalone HTML file is also useful on its own for
quick review in a browser before generating a PDF.

Every piece of text that ultimately comes from a filing (company name,
diff segment text, ...) is escaped through `html.escape`, matching the
data layer's own carefulness -- SEC filing text is not adversarial, but
an unescaped "&" or "<" from real prose would silently corrupt the
rendered page.
"""

from __future__ import annotations

import re
from html import escape as _e

from ..diff.text_diff import word_level_diff
from .charts import bar_chart_data_uri
from .fonts import body_font_stack, font_face_css
from .report_data import ReportData, SectionResult
from .trend import TrendAnalysis, TrendPoint

# @page / @frame is xhtml2pdf's (non-standard, pisa-specific) mechanism
# for a footer that repeats on every page -- confirmed by rendering a
# real test PDF and inspecting every page, not assumed from docs. A
# plain in-flow <pdf:pagenumber/> only appears once, at whatever point
# in the document flow it's placed; wrapping it in the #footer_content
# frame target is what makes it repeat.
# Entirely greyscale, by explicit user request ("je veux pas de couleur").
# Every distinction the old stylesheet carried in colour -- an Altman
# distress zone, a flagged Beneish, added vs removed text -- is now
# carried by weight, rule, indent or a typographic marker instead, so
# nothing is lost when the page is read (or printed) in black and white.
# The second request, "que ça ressemble pas à de l'IA", is why there are
# no tinted callout boxes, no badges and no emoji: the page is set like
# a plain analyst note.
_CSS_TEMPLATE = """
  %(font_faces)s
  @page {
    size: a4 portrait;
    margin: 2cm 1.9cm 1.8cm 1.9cm;
    @frame footer_frame {
      -pdf-frame-content: footer_content;
      bottom: 1cm; margin-left: 1.9cm; margin-right: 1.9cm; height: 1cm;
    }
  }
  body { font-family: %(body_font)s; color: #111; font-size: 9.5pt; line-height: 1.35; }
  h1 { font-size: 15pt; font-weight: bold; margin: 0 0 1pt 0; letter-spacing: -0.2pt; }
  h2 { font-size: 10.5pt; font-weight: bold; margin: 11pt 0 4pt 0;
       border-bottom: 0.6pt solid #111; padding-bottom: 2.5pt; }
  h3 { font-size: 9.5pt; font-weight: bold; margin: 9pt 0 3pt 0; }
  h4 { font-size: 9pt; font-weight: bold; margin: 8pt 0 2pt 0; }
  p { margin: 0 0 5pt 0; }
  .subtitle { color: #555; margin: 0 0 2pt 0; font-size: 8.5pt; }
  .lede { font-size: 9.5pt; margin-bottom: 7pt; }
  table { border-collapse: collapse; width: 100%%; margin-top: 4pt; }
  th, td { text-align: left; padding: 2pt 5pt; border-bottom: 0.4pt solid #ccc; font-size: 8.5pt; }
  td.wide, th.wide { width: 52%%; }
  td.narrow, th.narrow { width: 12%%; }
  th { border-bottom: 0.6pt solid #111; font-weight: bold; }
  td.num, th.num { text-align: right; }
  .muted { font-size: 8pt; color: #666; font-style: italic; }
  .unavailable { color: #666; font-style: italic; }
  .note { font-size: 8pt; color: #444; margin-top: 3pt; }
  /* Verdict/severity, formerly colour-coded: now weight + a bracketed word. */
  .flag-on { font-weight: bold; }
  .flag-off { font-weight: normal; color: #444; }
  /* Diff marks, formerly green/red: strike-through for gone text, an
     underline for new text, both legible in pure black. */
  .segment-added { display: block; margin: 1.5pt 0; padding-left: 10pt; text-decoration: underline; }
  .segment-removed { display: block; margin: 1.5pt 0; padding-left: 10pt; text-decoration: line-through; color: #555; }
  .segment-modified { display: block; margin: 1.5pt 0; padding-left: 10pt; }
  .word-removed { text-decoration: line-through; color: #555; }
  .word-added { text-decoration: underline; font-weight: bold; }
  .skip-note { font-style: italic; color: #444; }
  .footer { margin-top: 12pt; color: #666; font-size: 7.5pt;
            border-top: 0.4pt solid #bbb; padding-top: 4pt; }
  #footer_content { text-align: center; font-size: 7.5pt; color: #888; }
  .exec-summary p { margin-bottom: 6pt; text-align: justify; }
  .page-break { page-break-before: always; }
  .kicker { font-size: 7.5pt; letter-spacing: 0.8pt; text-transform: uppercase;
            color: #555; margin: 0 0 6pt 0; }
  /* Trend report only -- kept from the previous layout. */
  .cover { text-align: center; margin-top: 200pt; }
  .cover .cover-kicker { font-size: 10pt; color: #555; letter-spacing: 1pt; text-transform: uppercase; }
  .cover .cover-title { font-size: 24pt; font-weight: bold; margin-top: 8pt; }
  .cover .cover-subtitle { font-size: 12pt; color: #444; margin-top: 6pt; }
  .cover .cover-meta { font-size: 8.5pt; color: #666; margin-top: 70pt; }
"""


def _css() -> str:
    """
    The stylesheet with the report font resolved to real @font-face
    rules. Built at call time, not import time: the font file's presence
    is a property of the machine rendering the report, and this way a
    font installed after import still gets picked up.
    """
    return _CSS_TEMPLATE % {
        "font_faces": font_face_css(),
        "body_font": body_font_stack(),
    }


def _fmt_currency(value):
    return "n/a" if value is None else f"${value:,.0f}"


def _fmt_ratio(value):
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _unavailable_html(section: SectionResult) -> str:
    return f'<p class="unavailable">Indisponible : {_e(section.unavailable_reason)}</p>'


def _form_of(filing) -> str:
    return getattr(filing.form_type, "value", filing.form_type)


def _period_label(filing) -> str:
    """
    "T3 2026" for a quarter, "exercice 2025" for an annual filing.

    A quarterly report and an annual one are read differently and the
    reader has to be able to tell which one is in front of them at a
    glance; "exercice 2026" on a document covering three months would be
    actively misleading about what the numbers mean.
    """
    period = (filing.fiscal_period or "").upper()
    if period.startswith("Q") and period[1:].isdigit():
        return f"T{period[1:]} {filing.fiscal_year}"
    return f"exercice {filing.fiscal_year}"


def _mdna_label(filing) -> str:
    """The MD&A's item number depends on the form: Item 2 in a 10-Q, Item 7 in a 10-K."""
    return "MD&A (Item 2)" if _form_of(filing) == "10-Q" else "MD&A (Item 7)"


def _executive_summary_lines(report: ReportData) -> list:
    """
    Plain-language synthesis lines shown at the very top of the report,
    before any detail section -- a reader should be able to grasp the
    headline facts and any warning signs without reading all seven
    sections first. Each line only appears when the underlying data was
    actually available; this never fabricates a summary from missing
    data.
    """
    lines = []
    highlights_by_label = {h.label: h.value for h in report.financial_highlights}
    revenue = highlights_by_label.get("Revenue")
    net_income = highlights_by_label.get("Net Income")
    period = _period_label(report.filing)
    if revenue is not None and net_income is not None:
        lines.append(
            f"Revenue de {_fmt_currency(revenue)} et résultat net de "
            f"{_fmt_currency(net_income)} sur {period}."
        )
    elif revenue is not None:
        lines.append(f"Revenue de {_fmt_currency(revenue)} sur {period}.")

    red_flag_sections = [
        ("Altman Z-Score", report.altman_z),
        ("Beneish M-Score", report.beneish_m),
        ("Piotroski F-Score", report.piotroski_f),
    ]
    computed = [name for name, section in red_flag_sections if section.available]
    if computed:
        lines.append(f"{len(computed)}/3 indicateurs de red flags calculés ({', '.join(computed)}).")
    else:
        lines.append("Aucun indicateur de red flags calculable pour cette période (données insuffisantes).")
    if report.altman_z.available and report.altman_z.value.zone == "distress":
        lines.append('<span class="flag-on">Altman Z-Score en zone de détresse financière.</span>')
    if report.beneish_m.available and report.beneish_m.value.flagged:
        lines.append('<span class="flag-on">Beneish M-Score signale un risque de manipulation comptable.</span>')

    if report.mdna_diff.available:
        mdna_segments = report.mdna_diff.value.overall.segments
        added = sum(1 for seg in mdna_segments if seg.kind == "added")
        removed = sum(1 for seg in mdna_segments if seg.kind == "removed")
        modified = sum(1 for seg in mdna_segments if seg.kind == "modified")
        lines.append(
            f"{_e(_mdna_label(report.filing))} : {added} ajout(s), {removed} suppression(s), "
            f"{modified} phrase(s) reformulée(s) vs le dépôt précédent."
        )

    if report.mdna_sentiment.available:
        tone = report.mdna_sentiment.value.net_tone
        direction = "positive" if tone > 0.1 else "négative" if tone < -0.1 else "neutre"
        lines.append(f"Tonalité du MD&amp;A : {direction} ({_fmt_ratio(tone)}).")

    return lines


# Hard cap on the executive summary, so page 1 can never spill onto
# page 2 and push the whole two-page layout out by a page.
#
# Measured, not guessed: at this body size page 1 holds the summary up
# to somewhere between 850 and 899 words (850 renders two pages, 900
# renders three). The prompt asks for 650-780 so the page fills, and
# this cap sits above that band with margin for prose that runs wider
# than the measurement text. It is the backstop for a model that
# overruns its brief, not the working target.
#
# Truncation lands on a sentence boundary and says so, rather than
# cutting mid-word or silently dropping the tail.
_MAX_SUMMARY_WORDS = 820


def _truncate_summary(text: str) -> tuple:
    """Returns (text, was_truncated), cut at the last sentence end that fits."""
    words = text.split()
    if len(words) <= _MAX_SUMMARY_WORDS:
        return text, False
    clipped = " ".join(words[:_MAX_SUMMARY_WORDS])
    last_stop = max(clipped.rfind(". "), clipped.rfind(".\n"))
    if last_stop > len(clipped) // 2:
        clipped = clipped[: last_stop + 1]
    return clipped, True


def _render_executive_summary(report: ReportData) -> str:
    """
    Page 1: the AI's read of the selected sub-themes, as continuous
    prose. Deliberately NOT the old bulleted digest of headline numbers
    -- those now live on page 2, and duplicating them here would spend
    the page the user reserved for interpretation on figures the reader
    is about to see anyway.

    Falls back to the computed digest when no AI summary was requested
    or the call failed, so page 1 is never blank: a report generated
    without an API key still opens with something useful, just flagged
    as computed rather than written.
    """
    if report.ai_summary is not None and report.ai_summary.available:
        text, truncated = _truncate_summary(report.ai_summary.value["text"])
        paragraphs = "\n".join(
            f"<p>{_e(para.strip())}</p>" for para in text.split("\n") if para.strip()
        )
        note = ""
        if truncated:
            note = (
                '<p class="muted">Synthèse tronquée pour tenir en une page. '
                "le détail complet des changements figure dans le rapport « détail ».</p>"
            )
        return f"""
        <div class="exec-summary">
          <h2>Résumé exécutif</h2>
          {paragraphs}
          {note}
          {_render_selection_note(report)}
        </div>
        """

    lines = _executive_summary_lines(report)
    items = "\n".join(f"<p>{line}</p>" for line in lines)
    unavailable = ""
    if report.ai_summary is not None and not report.ai_summary.available:
        # Not a muted footnote. When the reason is that no filing text
        # could be read, the ABSENCE of a reading is the single most
        # important thing on the page: a reader who skims past it would
        # take the numbers below for a full analysis of the quarter. It
        # is stated in bold, at the top, above everything else.
        unavailable = (
            f'<p class="lede"><span class="flag-on">Pas de lecture interprétative '
            f"pour ce dépôt : {_e(report.ai_summary.unavailable_reason)}.</span></p>"
        )
    if not lines and not unavailable:
        return ""
    return f"""
    <div class="exec-summary">
      <h2>Résumé exécutif</h2>
      {unavailable}
      <p class="muted">Synthèse calculée à partir des indicateurs du rapport.</p>
      {items}
      {_render_selection_note(report)}
    </div>
    """


def _render_selection_note(report: ReportData) -> str:
    """
    States which sub-themes the summary above was written over, and how
    they were chosen. Always rendered when a selection exists: whether
    an analyst-grade model picked them or a word-count fallback did
    changes how much weight the reader should give the summary, so it
    is never left to be assumed.
    """
    section = getattr(report, "theme_selection", None)
    if section is None or not section.available:
        return ""
    selection = section.value
    if not selection.headings:
        return f'<p class="note">{_e(selection.reason)}.</p>'
    listed = " · ".join(_e(h) for h in selection.headings)
    return f'<p class="note">Sous-thématiques retenues : {listed}. ({_e(selection.reason)}.)</p>'


def _render_ai_disclaimer(report: ReportData) -> str:
    """
    The provenance line for the AI-written summary. Kept out of
    `_render_executive_summary` so it can sit at the very bottom of
    page 1 rather than interrupting the prose, and rendered only when an
    AI summary actually appears -- a computed digest needs no AI caveat.
    """
    if report.ai_summary is None or not report.ai_summary.available:
        return ""
    model = _e(report.ai_summary.value["model"])
    # The old wording claimed "aucune connaissance externe sur la société",
    # which the user asked to remove. It was also no longer strictly
    # accurate: the sub-theme SELECTION pass does draw on sector
    # knowledge (that is the point of it), even though the summary itself
    # still may not import outside facts. Dropped rather than reworded
    # into something longer. What stays is the part that protects the
    # reader: this is a read of the filing's changes, not investment
    # advice.
    return f"""
    <p class="muted">Lecture rédigée par {model}. Porte sur la balance des changements
    de ce dépôt, pas sur la valorisation du titre.
    À vérifier ; ne constitue pas un conseil en investissement.</p>
    """


def _comparison_subtitle(report: ReportData) -> str:
    """
    The two filings this report compares, on their own line.

    The whole point of the report is what moved between two consecutive
    filings, so which two they are is the first thing a reader has to be
    able to check. When the previous filing is the annual report (a Q1),
    that is called out: the volume of change is then inflated by the
    difference in document format, not by the business.
    """
    prior = report.prior_filing
    if prior is None:
        return (
            '<p class="subtitle">Aucun dépôt précédent disponible : les changements '
            "textuels ne sont pas calculables pour ce tirage.</p>"
        )
    note = ""
    if _form_of(prior) == "10-K" and _form_of(report.filing) == "10-Q":
        note = (
            " Comparaison de début d'exercice, contre le rapport annuel : "
            "l'ampleur des changements tient en partie au format des deux documents."
        )
    return (
        f'<p class="subtitle">Comparé au dépôt précédent : '
        f"{_e(_form_of(prior))} {_e(_period_label(prior))}, période close le "
        f"{prior.period_end.isoformat()}, déposé le {prior.filed_date.isoformat()}.{note}</p>"
    )


def _render_header(report: ReportData) -> str:
    filing = report.filing
    source_link = (
        f' · <a href="{_e(report.source_filing_url)}">source SEC EDGAR</a>'
        if report.source_filing_url else ""
    )
    return f"""
    <h1>{_e(filing.company_name)} ({_e(filing.ticker)})</h1>
    <p class="subtitle">
      {_e(_form_of(filing))} · {_e(_period_label(filing))}
      · période close le {filing.period_end.isoformat()}
      · déposé le {filing.filed_date.isoformat()}
      · CIK {_e(filing.cik)}{source_link}
    </p>
    {_comparison_subtitle(report)}
    """


_ZONE_LABELS = {"safe": "sûre", "grey": "grise", "distress": "détresse"}


def _red_flag_rows(report: ReportData) -> str:
    """
    The three red flags as rows of one compact table rather than three
    bordered cards -- page 2 has to carry all of them plus the per-theme
    change table, and cards spend most of their height on padding.
    Severity is bold text, never colour (see the stylesheet note).
    """
    rows = []

    if report.altman_z.available:
        z = report.altman_z.value
        emphasis = "flag-on" if z.zone == "distress" else "flag-off"
        rows.append(
            f"<tr><td>Altman Z-Score <span class=\"muted\">({_e(z.variant)})</span></td>"
            f'<td class="num">{_fmt_ratio(z.score)}</td>'
            f'<td><span class="{emphasis}">zone {_e(_ZONE_LABELS.get(z.zone, z.zone))}</span></td></tr>'
        )
    else:
        rows.append(
            "<tr><td>Altman Z-Score</td><td class=\"num\">n/a</td>"
            f'<td class="unavailable">{_e(report.altman_z.unavailable_reason)}</td></tr>'
        )

    if report.beneish_m.available:
        m = report.beneish_m.value
        emphasis = "flag-on" if m.flagged else "flag-off"
        label = "signalé" if m.flagged else "non signalé"
        rows.append(
            f"<tr><td>Beneish M-Score <span class=\"muted\">(seuil {_fmt_ratio(m.threshold)})</span></td>"
            f'<td class="num">{_fmt_ratio(m.score)}</td>'
            f'<td><span class="{emphasis}">{label}</span></td></tr>'
        )
    else:
        rows.append(
            "<tr><td>Beneish M-Score</td><td class=\"num\">n/a</td>"
            f'<td class="unavailable">{_e(report.beneish_m.unavailable_reason)}</td></tr>'
        )

    if report.piotroski_f.available:
        f = report.piotroski_f.value
        failed = [name.replace("_", " ") for name, passed in f.criteria.items() if not passed]
        # Only the FAILED criteria are named. The passed ones are implied
        # by the score and would cost a third of the page to list.
        detail = "tous critères validés" if not failed else "échoue : " + ", ".join(failed)
        rows.append(
            "<tr><td>Piotroski F-Score</td>"
            f'<td class="num">{f.score} / {f.max_score}</td>'
            f"<td>{_e(detail)}</td></tr>"
        )
    else:
        rows.append(
            "<tr><td>Piotroski F-Score</td><td class=\"num\">n/a</td>"
            f'<td class="unavailable">{_e(report.piotroski_f.unavailable_reason)}</td></tr>'
        )

    return "\n".join(rows)


def _red_flag_source_note(report: ReportData) -> str:
    """
    Says which filing the three scores were computed from.

    On a quarterly report this is not a detail: Altman, Beneish and
    Piotroski are annual models, so they come from the company's 10-K,
    not from the quarter the rest of the page is about (see
    report_data.build_report_data). A reader who assumed these numbers
    described the quarter would be reading them wrong, and they have no
    way to tell from the numbers themselves.
    """
    annual = report.annual_filing
    if annual is None:
        # An annual report needs no note: the scores come from the same
        # filing the whole page is about.
        if _form_of(report.filing) == "10-K":
            return ""
        return (
            '<p class="note">Altman, Beneish et Piotroski sont des modèles annuels '
            "et ne sont pas calculés sur des données trimestrielles.</p>"
        )
    prior = report.prior_annual_filing
    against = (
        f" comparé à l'exercice {prior.fiscal_year}" if prior is not None else ""
    )
    return (
        f'<p class="note">Modèles annuels : calculés sur le 10-K de l\'exercice '
        f"{annual.fiscal_year}{against}, pas sur le trimestre analysé ci-dessus. "
        f"Ils décrivent la santé de fond de la société, pas les nouvelles du trimestre.</p>"
    )


def _render_red_flags(report: ReportData) -> str:
    return f"""
    <h2>Red flags</h2>
    <table>
      <tr><th>Indicateur</th><th class="num">Score</th><th>Lecture</th></tr>
      {_red_flag_rows(report)}
    </table>
    {_red_flag_source_note(report)}
    """


def _theme_change_rows(grouped) -> str:
    """
    One row per sub-theme: how much of it changed, in percent, plus the
    raw counts behind that percentage.

    The percentage is changed words over the sub-theme's total word
    count -- deliberately NOT the diff's `similarity_ratio`, which is
    computed over whole sentences and so reads 0% for a sub-theme where
    a single figure moved inside every sentence. Words are the unit a
    reader means by "how much of this changed".
    """
    rows = []
    for group in grouped.selected_groups:
        diff = group.diff
        changed = diff.added_word_count + diff.removed_word_count
        base = max(diff.prior_word_count, diff.current_word_count)
        pct = (changed / base) if base else 0.0
        added = sum(1 for s in diff.segments if s.kind == "added")
        removed = sum(1 for s in diff.segments if s.kind == "removed")
        modified = sum(1 for s in diff.segments if s.kind == "modified")
        status_suffix = {
            "added": " (nouvelle)",
            "removed": " (supprimée)",
            "matched": "",
        }[group.status]
        heading = _e(group.heading) if group.heading else "Introduction"
        rows.append(
            f'<tr><td class="wide">{heading}{status_suffix}</td>'
            f'<td class="num">{pct * 100:.0f}%</td>'
            f'<td class="num">{added}</td><td class="num">{removed}</td>'
            f'<td class="num">{modified}</td></tr>'
        )
    return "\n".join(rows)


def _selection_section(report: ReportData) -> tuple:
    """
    Returns (label, grouped_diff, message) for whichever section carries
    the sub-theme selection this report was written over -- the MD&A for
    a quarter, Risk Factors for an annual report. `grouped_diff` is None
    when there is nothing to table, and `message` says why.

    Driven by the selection itself (`theme_selection.section_key`) so
    the table and the summary can never end up describing different
    sections of the filing.
    """
    from .theme_selection import default_section_for

    selection = getattr(report, "theme_selection", None)
    if selection is not None and selection.available and selection.value is not None:
        section_key = selection.value.section_key
    else:
        section_key = default_section_for(report.filing)

    if section_key == "mdna":
        label = _mdna_label(report.filing)
        section = report.mdna_diff
        if not section.available:
            return label, None, f"Indisponible : {section.unavailable_reason}"
        return label, section.value, None

    label = "Risk Factors (Item 1A)"
    section = report.risk_factors_diff
    if not section.available:
        return label, None, f"Indisponible : {section.unavailable_reason}"
    if section.value.skipped:
        return label, None, section.value.skip_reason
    return label, section.value.diff, None


def _render_theme_change_table(report: ReportData) -> str:
    """Page 2: how much each retained sub-theme moved, at a glance."""
    label, grouped, message = _selection_section(report)
    # "Changements par sous-thématique (MD&A (Item 2))" nests brackets
    # inside brackets; a colon reads cleanly for every section label.
    heading = f"<h2>Changements par sous-thématique : {_e(label)}</h2>"
    if grouped is None:
        return heading + f'<p class="skip-note">{_e(message)}</p>'

    rows = _theme_change_rows(grouped)
    if not rows:
        return heading + '<p class="unavailable">Aucune sous-thématique retenue pour ce dépôt.</p>'

    overall = grouped.overall
    unselected = [g for g in grouped.groups if not g.selected]
    unselected_note = ""
    if unselected:
        # Never a silent drop: the reader is told how many sub-themes
        # exist beyond the retained set, and where to read them.
        unselected_note = (
            f'<p class="note">{len(unselected)} autre(s) sous-thématique(s) non retenue(s) '
            f"dans la sélection ci-dessus, comptabilisées dans le total ci-dessous et "
            f"détaillées dans le rapport « détail ».</p>"
        )

    return f"""
    {heading}
    <table>
      <tr><th class="wide">Sous-thématique</th><th class="num narrow">% modifié</th>
          <th class="num narrow">Ajouts</th><th class="num narrow">Suppr.</th><th class="num narrow">Reform.</th></tr>
      {rows}
    </table>
    <p class="note">{_e(label)} dans son ensemble : {_fmt_pct(overall.similarity_ratio)} de similarité,
    {overall.added_word_count} mots ajoutés, {overall.removed_word_count} mots supprimés.</p>
    {unselected_note}
    """


def _render_risk_alert(report: ReportData, *, headline_only: bool) -> str:
    """
    The quarterly Item 1A signal (see report/risk_alert.py).

    Two renderings, because the two cases deserve very different amounts
    of the reader's attention. The normal case, where the 10-Q points
    back at the 10-K, is one plain line on page 2: the reader asked an
    implicit question and gets an answer, at the cost of a line. The
    rare case, where the company actually wrote risk text into a 10-Q,
    gets a bold line at the very top of page 1, above the summary,
    because it is the single highest-signal thing a quarter can contain
    and burying it in a table would defeat the point of flagging it.
    """
    alert = report.risk_alert
    if alert is None:
        return ""
    if headline_only:
        if not alert.triggered:
            return ""
        return f'<p class="lede"><span class="flag-on">{_e(alert.headline)}</span></p>'

    if alert.triggered:
        detail = ""
        if alert.added_headings:
            detail = (
                f'<p class="note">Intitulés nouveaux : '
                f"{' · '.join(_e(h) for h in alert.added_headings)}. "
                f"Texte intégral dans le rapport « détail ».</p>"
            )
        return f"""
        <h2>Facteurs de risque (Item 1A)</h2>
        <p><span class="flag-on">{_e(alert.headline)}</span></p>
        {detail}
        """
    return f"""
    <h2>Facteurs de risque (Item 1A)</h2>
    <p>{_e(alert.headline)}</p>
    """


def _modified_segment_html(seg) -> str:
    """
    Inline word-level diff for a "modified" segment: the old wording with
    just the changed words struck through, the replacement words right
    next to them in green and in parentheses -- real user feedback:
    reading a whole sentence struck through immediately above the whole
    new sentence in green took longer to scan than seeing inline what
    actually changed. Unchanged words stay plain text, in their original
    place, so the sentence still reads naturally.
    """
    parts = []
    for tag, text in word_level_diff(seg.text, seg.replacement):
        if tag == "equal":
            parts.append(_e(text))
        elif tag == "removed":
            parts.append(f'<span class="word-removed">{_e(text)}</span>')
        elif tag == "added":
            parts.append(f'<span class="word-added">({_e(text)})</span>')
    return " ".join(parts)


def _diff_body_html(segments) -> str:
    parts = []
    for seg in segments:
        if seg.kind == "removed":
            parts.append(f'<span class="segment-removed">{_e(seg.text)}</span>')
        elif seg.kind == "added":
            parts.append(f'<span class="segment-added">{_e(seg.text)}</span>')
        elif seg.kind == "modified":
            parts.append(f'<div class="segment-modified">{_modified_segment_html(seg)}</div>')
    return "".join(parts)


_GROUP_STATUS_NOTE = {
    "added": " (nouvelle sous-thématique)",
    "removed": " (sous-thématique supprimée)",
    "matched": "",
}

# How many re-worded ("matched") sub-themes get their full before/after text
# reproduced. A real filing can restructure a dozen+ sub-themes in a single
# revision; showing every one of them in full made the report too long to
# actually read (real user feedback on a Micron 10-K report). Total changed
# word count -- already computed per group, and the same proxy this project
# already uses elsewhere as a stand-in for "how substantial is this change"
# -- ranks them; only the top ones get the full treatment.
_MAX_DETAILED_GROUPS = 5


def _group_change_weight(group) -> int:
    return group.diff.added_word_count + group.diff.removed_word_count


def _text_diff_summary_html(title: str, grouped) -> str:
    """
    Just the headline line for a GroupedTextDiffResult (see
    diff/grouped_diff.py) -- similarity % and ajout/suppression/
    reformulation/inchangé counts, the exact same aggregate the flat
    diff would have shown. Deliberately separate from
    `_text_diff_detail_html`: the summary sits with the report's other
    quick-scan numbers (financials, red flags, sentiment), while the
    full text detail -- the longest, most detailed reading material in
    the report -- is rendered near the end instead (see render_html).
    """
    overall = grouped.overall
    added = [seg for seg in overall.segments if seg.kind == "added"]
    removed = [seg for seg in overall.segments if seg.kind == "removed"]
    modified = [seg for seg in overall.segments if seg.kind == "modified"]
    equal_count = sum(1 for seg in overall.segments if seg.kind == "equal")
    return f"""
    <h3>{_e(title)}</h3>
    <p>Similarité : {_fmt_pct(overall.similarity_ratio)}
       · {len(added)} ajout(s), {len(removed)} suppression(s), {len(modified)} phrase(s)
       reformulée(s), {equal_count} inchangé(s)</p>
    """


def _text_diff_detail_html(title: str, grouped) -> str:
    """
    The full text detail for a GroupedTextDiffResult -- everything
    `_text_diff_summary_html` doesn't already say. Rendered as its own
    section near the end of the report (see render_html): this is the
    longest, most detailed reading material a report has, so it comes
    after every quick-scan number, not interleaved with them.

    When the section has no internal sub-headings (`grouped.groups` is
    a single unheaded group -- the common case), the body renders
    exactly as the old flat diff always did: no sub-heading wrapper is
    introduced where the source document didn't have one. Sub-theme
    breakdown only appears for a section that's actually structured
    that way in the real filing (confirmed against a real NVIDIA 10-K's
    Item 1A, whose "Risks Related to X" headings this was built for).

    Two things are deliberately never shown in full, per user feedback on
    a real report: (1) a sub-theme that was wholesale added or removed --
    its "diff" body is by construction 100% one-sided (all-added or
    all-removed), so reproducing it teaches the reader nothing the
    heading + status note doesn't already say; (2) a re-worded sub-theme
    outside the `_MAX_DETAILED_GROUPS` most heavily changed ones. Both
    still get a compact one-line mention -- this project never drops
    content silently, it only condenses it.
    """
    overall = grouped.overall
    added = [seg for seg in overall.segments if seg.kind == "added"]
    removed = [seg for seg in overall.segments if seg.kind == "removed"]
    modified = [seg for seg in overall.segments if seg.kind == "modified"]
    title_html = f"<h3>{_e(title)}</h3>"
    if not added and not removed and not modified:
        return title_html + "<p>Aucun changement détecté.</p>"

    groups = grouped.groups
    if len(groups) == 1 and groups[0].heading == "":
        return title_html + _diff_body_html(groups[0].diff.segments)

    changed_groups = []
    unchanged_group_count = 0
    for group in groups:
        g_added = sum(1 for seg in group.diff.segments if seg.kind == "added")
        g_removed = sum(1 for seg in group.diff.segments if seg.kind == "removed")
        g_modified = sum(1 for seg in group.diff.segments if seg.kind == "modified")
        if not g_added and not g_removed and not g_modified:
            unchanged_group_count += 1
            continue
        changed_groups.append((group, g_added, g_removed, g_modified))

    matched_groups = sorted(
        (item for item in changed_groups if item[0].status == "matched"),
        key=lambda item: _group_change_weight(item[0]),
        reverse=True,
    )
    detailed_ids = {id(item[0]) for item in matched_groups[:_MAX_DETAILED_GROUPS]}

    parts = [title_html]
    compact_count = 0
    for group, g_added, g_removed, g_modified in changed_groups:
        heading_label = _e(group.heading) if group.heading else "Introduction"
        status_note = _GROUP_STATUS_NOTE[group.status]
        stats = f"{g_added} ajout(s), {g_removed} suppression(s), {g_modified} reformulation(s)"
        if group.status != "matched" or id(group) not in detailed_ids:
            compact_count += 1
            parts.append(
                f'<p class="group-stats"><strong>{heading_label}</strong>{status_note}'
                f" · {stats}</p>"
            )
            continue
        parts.append(f"""
        <h4>{heading_label}{status_note}</h4>
        <p class="group-stats">{stats}</p>
        {_diff_body_html(group.diff.segments)}
        """)

    notes = []
    if unchanged_group_count:
        notes.append(f"{unchanged_group_count} sous-thème(s) sans changement")
    if compact_count:
        notes.append(
            f"{compact_count} sous-thème(s) résumé(s) sans le texte complet "
            f"(sous-thématique entièrement ajoutée/supprimée, ou en dehors des "
            f"{_MAX_DETAILED_GROUPS} sous-thèmes les plus modifiés)"
        )
    if notes:
        parts.append(
            f'<p class="muted">{" ; ".join(notes)} '
            f"(déjà compté(s) dans le résumé ci-dessus).</p>"
        )
    return "\n".join(parts)


def _render_mdna_diff_summary(section: SectionResult, label: str = "MD&A (Item 7)") -> str:
    if not section.available:
        return f"<h3>{_e(label)}</h3>{_unavailable_html(section)}"
    # NOTE: `label` carries a plain "&", not "&amp;" -- it goes through
    # _text_diff_summary_html's own _e(title) call, which would otherwise
    # double-escape it into "&amp;amp;A", rendering as the literal text
    # "MD&amp;A" in the PDF instead of "MD&A" (caught by visually
    # inspecting a real rendered report, not just running the tests).
    return _text_diff_summary_html(label, section.value)


def _render_mdna_diff_detail(section: SectionResult, label: str = "MD&A (Item 7)") -> str:
    # Unavailable is already explained in the summary section above (see
    # render_html's section order) -- nothing to add here, not a silent
    # drop of information the reader hasn't already seen.
    if not section.available:
        return ""
    return _text_diff_detail_html(label, section.value)


def _render_risk_factors_diff_summary(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>Risk Factors (Item 1A)</h3>{_unavailable_html(section)}"
    rf_result = section.value  # RiskFactorsDiffResult
    if rf_result.skipped:
        return (
            f"<h3>Risk Factors (Item 1A)</h3>"
            f'<p class="skip-note">{_e(rf_result.skip_reason)}</p>'
        )
    return _text_diff_summary_html("Risk Factors (Item 1A)", rf_result.diff)


def _render_risk_factors_diff_detail(section: SectionResult) -> str:
    if not section.available:
        return ""
    rf_result = section.value
    if rf_result.skipped:
        return ""
    return _text_diff_detail_html("Risk Factors (Item 1A)", rf_result.diff)


def _sentiment_row(label: str, section: SectionResult, skipped_attr: bool = False) -> str:
    if not section.available:
        return (
            f"<tr><td>{label}</td><td class=\"num\">n/a</td>"
            f'<td class="unavailable">{_e(section.unavailable_reason)}</td></tr>'
        )
    result = section.value
    if skipped_attr:
        if result.skipped:
            # The underlying skip_reason is a five-line English
            # explanation of the 10-Q boilerplate rule. Correct, but it
            # now appears on every quarterly report and would take a
            # third of this table to say what the report already states
            # in full under "Facteurs de risque". One short French line
            # here, the explanation there.
            return (
                f'<tr><td>{label}</td><td class="num">n/a</td>'
                f'<td class="skip-note">section non rédigée ce trimestre, '
                f"voir Facteurs de risque ci-dessus</td></tr>"
            )
        result = result.result
    tone = result.net_tone
    direction = "positive" if tone > 0.1 else "négative" if tone < -0.1 else "neutre"
    return (
        f"<tr><td>{label}</td><td class=\"num\">{_fmt_ratio(tone)}</td>"
        f"<td>{direction} · {result.total_word_count} mots analysés</td></tr>"
    )


def _render_sentiment_section(report: ReportData) -> str:
    """
    Compact two-row table instead of the previous two full
    category-breakdown tables: page 2 has to hold red flags, per-theme
    change and this, and the seven Loughran-McDonald category counts are
    detail-report material. The net tone -- the number a reader actually
    acts on -- stays here.
    """
    # _sentiment_row inserts its label into the row as-is, so escape here.
    mdna_label = _e(_mdna_label(report.filing))
    return f"""
    <h2>Tonalité (Loughran-McDonald)</h2>
    <table>
      <tr><th>Section</th><th class="num">Tonalité nette</th><th>Lecture</th></tr>
      {_sentiment_row("Risk Factors (Item 1A)", report.risk_factors_sentiment, skipped_attr=True)}
      {_sentiment_row(mdna_label, report.mdna_sentiment)}
    </table>
    """


def _document_html(title: str, body: str) -> str:
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


def render_html(report: ReportData) -> str:
    """
    The main report: exactly two pages.

    Page 1 is the executive summary -- the AI's read of the sub-themes
    selected as analyst-relevant, plus which ones those were and how
    they were chosen, headed by the risk-factor alert on the rare
    quarters where it fires. Page 2 is every number, in the order that
    matches what the report is for: first how much each retained
    sub-theme moved between the two filings, then the risk-factor line,
    then the annual red-flag scores (which describe the company's
    underlying health and do not move quarter to quarter), then tone.

    The detailed changed text is NOT here. It lives in its own document
    (`render_detail_html`) because it's unbounded in length -- a heavily
    rewritten Item 1A runs to dozens of pages -- and mixing it in is
    what made earlier versions of this report unreadable.

    Two pages is a hard constraint, not a target: the page break between
    the two halves is explicit, page 1's summary is capped and truncated
    at a sentence boundary if the model overruns, and page 2's tables
    are sized to fit. Nothing is dropped without a visible note pointing
    to the detail report.
    """
    filing = report.filing
    title = (
        f"{_e(filing.company_name)} · {_e(filing.form_type.value)} "
        f"{filing.fiscal_year} {_e(filing.fiscal_period)}"
    )
    body = f"""
  {_render_header(report)}
  {_render_risk_alert(report, headline_only=True)}
  {_render_executive_summary(report)}
  {_render_ai_disclaimer(report)}
  <div class="page-break">
    {_render_theme_change_table(report)}
    {_render_risk_alert(report, headline_only=False)}
    {_render_red_flags(report)}
    {_render_sentiment_section(report)}
    <p class="footer">
      Généré le {report.generated_at.date().isoformat()} à partir des données SEC EDGAR.
      Détail intégral des changements textuels : rapport « détail » joint.
    </p>
  </div>
"""
    return _document_html(title, body)


def render_detail_html(report: ReportData) -> str:
    """
    The companion "détail" document: the full changed text, sub-theme by
    sub-theme, with no length cap.

    Split out of the main report at the user's request. Selected
    sub-themes come first, in the order the selection ranked them, then
    the rest -- so the document opens on what the summary was written
    over, but still contains everything (nothing is dropped just for not
    having been selected).
    """
    filing = report.filing
    title = (
        f"{_e(filing.company_name)} · détail des changements "
        f"{_e(_period_label(filing))}"
    )
    rf_detail = _render_risk_factors_diff_detail(report.risk_factors_diff)
    mdna_detail = _render_mdna_diff_detail(report.mdna_diff, _mdna_label(filing))
    # On a quarterly report the MD&A leads: it is what the main report
    # was written over, so the detail document opens on the same
    # material rather than on the risk factors.
    ordered = (
        (mdna_detail, rf_detail) if _form_of(filing) == "10-Q"
        else (rf_detail, mdna_detail)
    )
    if not any(ordered):
        sections = (
            '<p class="unavailable">Aucun changement textuel détaillé disponible '
            "pour ce filing (sections manquantes ou diff non calculable).</p>"
        )
    else:
        sections = "\n".join(part for part in ordered if part)

    body = f"""
  {_render_header(report)}
  <p class="kicker">Détail des changements textuels</p>
  <p class="muted">Texte supprimé barré, texte ajouté souligné. Pour une phrase
  reformulée, l'ancienne formulation est barrée et la nouvelle suit entre parenthèses.</p>
  {sections}
  <p class="footer">
    Généré le {report.generated_at.date().isoformat()} à partir des données SEC EDGAR.
    Document d'accompagnement du rapport principal.
  </p>
"""
    return _document_html(title, body)


def _trend_altman_cell(section: SectionResult) -> str:
    if not section.available:
        return '<span class="unavailable">n/a</span>'
    result = section.value
    return f'{_fmt_ratio(result.score)} ({_e(_ZONE_LABELS.get(result.zone, result.zone))})'


def _trend_beneish_cell(section: SectionResult) -> str:
    if not section.available:
        return '<span class="unavailable">n/a</span>'
    result = section.value
    emphasis = "flag-on" if result.flagged else "flag-off"
    return f'<span class="{emphasis}">{_fmt_ratio(result.score)}</span>'


def _trend_piotroski_cell(section: SectionResult) -> str:
    if not section.available:
        return '<span class="unavailable">n/a</span>'
    result = section.value
    return f"{result.score} / {result.max_score}"


def _trend_sentiment_cell(section: SectionResult) -> str:
    if not section.available:
        return '<span class="unavailable">n/a</span>'
    return _fmt_ratio(section.value.net_tone)


def _render_trend_row(point: TrendPoint) -> str:
    financials = point.filing.financials
    revenue = financials.revenue.value if financials and financials.revenue else None
    net_income = financials.net_income.value if financials and financials.net_income else None
    report = point.report
    source_link = (
        f'<a href="{_e(report.source_filing_url)}">filing</a>'
        if report.source_filing_url else "n/a"
    )
    return f"""
    <tr>
      <td>{point.fiscal_year}</td>
      <td>{_fmt_currency(revenue)}</td>
      <td>{_fmt_currency(net_income)}</td>
      <td>{_fmt_pct(report.financial_data_completeness)}</td>
      <td>{_trend_altman_cell(report.altman_z)}</td>
      <td>{_trend_beneish_cell(report.beneish_m)}</td>
      <td>{_trend_piotroski_cell(report.piotroski_f)}</td>
      <td>{_trend_sentiment_cell(report.mdna_sentiment)}</td>
      <td>{source_link}</td>
    </tr>
    """


def _render_trend_cover(trend: TrendAnalysis) -> str:
    last_filing = trend.points[-1].filing
    years_span = f"{trend.points[0].fiscal_year} à {trend.points[-1].fiscal_year}"
    return f"""
    <div class="cover">
      <div class="cover-kicker">Analyse de tendance</div>
      <div class="cover-title">{_e(last_filing.company_name)} ({_e(last_filing.ticker)})</div>
      <div class="cover-subtitle">Exercices {years_span}</div>
      <div class="cover-meta">
        Généré automatiquement à partir des données SEC EDGAR · {len(trend.points)} exercice(s) analysé(s)
      </div>
    </div>
    """


def _trend_executive_summary_lines(trend: TrendAnalysis) -> list:
    """Same spirit as _executive_summary_lines: only states what the data actually supports."""
    lines = []
    first, last = trend.points[0], trend.points[-1]

    def _revenue(point: TrendPoint):
        financials = point.filing.financials
        return financials.revenue.value if financials and financials.revenue else None

    first_revenue, last_revenue = _revenue(first), _revenue(last)
    if first_revenue is not None and last_revenue is not None and first_revenue != 0:
        growth = (last_revenue - first_revenue) / first_revenue
        direction = "hausse" if growth >= 0 else "baisse"
        lines.append(
            f"Revenue en {direction} de {_fmt_pct(abs(growth))} entre "
            f"{first.fiscal_year} et {last.fiscal_year} "
            f"({_fmt_currency(first_revenue)} → {_fmt_currency(last_revenue)})."
        )

    piotroski_scores = [
        (p.fiscal_year, p.report.piotroski_f.value.score)
        for p in trend.points if p.report.piotroski_f.available
    ]
    if len(piotroski_scores) >= 2:
        (fy0, s0), (fy1, s1) = piotroski_scores[0], piotroski_scores[-1]
        direction = "amélioré" if s1 > s0 else "dégradé" if s1 < s0 else "resté stable"
        lines.append(f"Piotroski F-Score {direction} : {s0}/9 en {fy0} → {s1}/9 en {fy1}.")

    flagged_years = [
        str(p.fiscal_year) for p in trend.points
        if p.report.beneish_m.available and p.report.beneish_m.value.flagged
    ]
    if flagged_years:
        lines.append(
            f'<span class="flag-on">Beneish M-Score a signalé un risque de manipulation '
            f"comptable pour : {', '.join(flagged_years)}.</span>"
        )

    distress_years = [
        str(p.fiscal_year) for p in trend.points
        if p.report.altman_z.available and p.report.altman_z.value.zone == "distress"
    ]
    if distress_years:
        lines.append(
            f'<span class="flag-on">Altman Z-Score en zone de détresse pour : '
            f"{', '.join(distress_years)}.</span>"
        )

    return lines


def _render_trend_executive_summary(trend: TrendAnalysis) -> str:
    lines = _trend_executive_summary_lines(trend)
    if not lines:
        return ""
    items = "\n".join(f"<li>{line}</li>" for line in lines)
    return f'<div class="exec-summary"><h2>Résumé exécutif</h2><ul>{items}</ul></div>'


def _render_trend_charts(trend: TrendAnalysis) -> str:
    fiscal_years = [str(p.fiscal_year) for p in trend.points]

    def _revenue(point: TrendPoint):
        financials = point.filing.financials
        return financials.revenue.value if financials and financials.revenue else None

    revenue_values = [_revenue(p) for p in trend.points]
    revenue_chart = ""
    if any(v is not None for v in revenue_values):
        revenue_uri = bar_chart_data_uri(
            revenue_values,
            labels=fiscal_years,
            value_labels=[_fmt_currency(v) for v in revenue_values],
            width=220,
        )
        revenue_chart = f"""
        <h3>Revenue</h3>
        <img src="{revenue_uri}" />
        """

    piotroski_values = [
        p.report.piotroski_f.value.score if p.report.piotroski_f.available else None
        for p in trend.points
    ]
    piotroski_chart = ""
    if any(v is not None for v in piotroski_values):
        piotroski_uri = bar_chart_data_uri(
            piotroski_values,
            labels=fiscal_years,
            value_labels=[f"{v} / 9" if v is not None else "n/a" for v in piotroski_values],
            max_value=9,
            width=220,
        )
        piotroski_chart = f"""
        <h3>Piotroski F-Score</h3>
        <img src="{piotroski_uri}" />
        """

    if not revenue_chart and not piotroski_chart:
        return ""
    return f"""
    <h2>Graphiques</h2>
    <div class="card">{revenue_chart}</div>
    <div class="card">{piotroski_chart}</div>
    """


def render_trend_html(trend: TrendAnalysis) -> str:
    """
    Renders a TrendAnalysis (see trend.py) into a complete,
    self-contained HTML document: one row per fiscal year, each score
    computed against the immediately preceding year in the series --
    the point of this view is seeing a score move over time, not just
    its value in isolation.
    """
    if not trend.points:
        raise ValueError("trend has no points to render")
    last_filing = trend.points[-1].filing
    years_span = f"{trend.points[0].fiscal_year} à {trend.points[-1].fiscal_year}"
    rows = "\n".join(_render_trend_row(p) for p in trend.points)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{_e(last_filing.company_name)} · historique {years_span}</title>
  <style>{_css()}</style>
</head>
<body>
  <div id="footer_content">Page <pdf:pagenumber /> / <pdf:pagecount /></div>
  {_render_trend_cover(trend)}
  <div class="page-break">
    <h1>{_e(last_filing.company_name)} ({_e(last_filing.ticker)}) · historique {years_span}</h1>
    <p class="subtitle">
      Chaque exercice est comparé à celui immédiatement précédent dans la série
      (jamais une année sautée). Beneish M, Piotroski F et la tonalité MD&amp;A
      reflètent donc toujours une vraie comparaison année sur année.
    </p>
    {_render_trend_executive_summary(trend)}
    {_render_trend_charts(trend)}
    <h2>Détail par exercice</h2>
    <table>
      <tr>
        <th>Exercice</th><th>Revenue</th><th>Net Income</th>
        <th>Data %</th><th>Altman Z</th><th>Beneish M</th>
        <th>Piotroski F</th><th>Ton. MD&amp;A</th><th>Source</th>
      </tr>
      {rows}
    </table>
    <p class="footer">
      Rapport de tendance généré automatiquement à partir des données SEC EDGAR.
      Pas de comparaison sectorielle dans cette vue, voir le rapport détaillé
      de chaque exercice pour le détail complet de chaque section.
    </p>
  </div>
</body>
</html>
"""
