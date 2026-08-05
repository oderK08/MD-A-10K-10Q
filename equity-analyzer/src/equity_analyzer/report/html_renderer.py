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

from .charts import bar_chart_data_uri
from .report_data import ReportData, SectionResult
from .trend import TrendAnalysis, TrendPoint

# @page / @frame is xhtml2pdf's (non-standard, pisa-specific) mechanism
# for a footer that repeats on every page -- confirmed by rendering a
# real test PDF and inspecting every page, not assumed from docs. A
# plain in-flow <pdf:pagenumber/> only appears once, at whatever point
# in the document flow it's placed; wrapping it in the #footer_content
# frame target is what makes it repeat.
_CSS = """
  @page {
    size: a4 portrait;
    margin: 2.4cm 2cm 2cm 2cm;
    @frame footer_frame {
      -pdf-frame-content: footer_content;
      bottom: 1cm; margin-left: 2cm; margin-right: 2cm; height: 1cm;
    }
  }
  body { font-family: Helvetica, Arial, sans-serif; color: #1a1a1a; font-size: 11pt; }
  h1 { font-size: 18pt; margin-bottom: 2pt; }
  h2 { font-size: 13pt; margin-top: 20pt; border-bottom: 1px solid #ccc; padding-bottom: 4pt; }
  h3 { font-size: 11pt; margin-top: 12pt; }
  h4 { font-size: 10pt; margin-top: 10pt; margin-bottom: 2pt; color: #2a2a2a;
       border-left: 3pt solid #ccc; padding-left: 6pt; }
  .group-stats { font-size: 9pt; color: #666; margin-top: 0; margin-bottom: 4pt; }
  .muted { font-size: 9pt; color: #888; font-style: italic; }
  .subtitle { color: #555; margin-top: 0; }
  table { border-collapse: collapse; width: 100%; margin-top: 6pt; }
  th, td { text-align: left; padding: 4pt 8pt; border-bottom: 1px solid #ddd; font-size: 10pt; }
  th { background: #f2f2f2; }
  .card { border: 1px solid #ddd; border-radius: 4pt; padding: 10pt; margin-top: 8pt; }
  .unavailable { color: #888; font-style: italic; }
  .zone-safe { color: #1a7f37; font-weight: bold; }
  .zone-grey { color: #9a6700; font-weight: bold; }
  .zone-distress { color: #cf222e; font-weight: bold; }
  .flagged-true { color: #cf222e; font-weight: bold; }
  .flagged-false { color: #1a7f37; font-weight: bold; }
  .segment-added { background: #e6ffed; color: #1a7f37; padding: 2pt 4pt; display: block; margin: 2pt 0; }
  .segment-removed { background: #ffeef0; color: #cf222e; text-decoration: line-through; padding: 2pt 4pt; display: block; margin: 2pt 0; }
  .skip-note { color: #9a6700; background: #fff8e6; padding: 6pt; border-radius: 4pt; }
  .footer { margin-top: 24pt; color: #888; font-size: 8pt; border-top: 1px solid #ccc; padding-top: 6pt; }
  #footer_content { text-align: center; font-size: 8pt; color: #999; }
  .cover { text-align: center; margin-top: 200pt; }
  .cover .cover-kicker { font-size: 11pt; color: #888; letter-spacing: 1pt; text-transform: uppercase; }
  .cover .cover-title { font-size: 28pt; font-weight: bold; margin-top: 8pt; }
  .cover .cover-subtitle { font-size: 13pt; color: #555; margin-top: 6pt; }
  .cover .cover-meta { font-size: 9pt; color: #999; margin-top: 70pt; }
  .exec-summary { background: #f5f8fa; border-left: 3pt solid #2a6f97; padding: 8pt 12pt; margin: 10pt 0; }
  .exec-summary h2 { margin-top: 0; border-bottom: none; padding-bottom: 0; }
  .exec-summary ul { margin: 4pt 0; padding-left: 16pt; }
  .exec-summary li { margin: 3pt 0; }
  .exec-summary .warn { color: #9a6700; font-weight: bold; }
  .ai-summary { background: #f7f5fb; border-left: 3pt solid #6f42c1; padding: 8pt 12pt; margin: 10pt 0; }
  .ai-summary h2 { margin-top: 0; border-bottom: none; padding-bottom: 0; }
  .ai-badge { font-size: 8pt; font-weight: normal; color: #6f42c1; border: 1pt solid #6f42c1;
              border-radius: 3pt; padding: 1pt 5pt; margin-left: 6pt; vertical-align: middle; }
  .ai-disclaimer { font-size: 8pt; color: #888; font-style: italic; margin-top: 6pt; margin-bottom: 0; }
  .page-break { page-break-before: always; }
  .chart-row { margin: 3pt 0; }
  .chart-label { display: inline-block; width: 40pt; font-size: 9pt; vertical-align: middle; }
  .chart-value { display: inline-block; width: 46pt; font-size: 9pt; text-align: right;
                 vertical-align: middle; padding-right: 6pt; }
"""


def _fmt_currency(value):
    return "—" if value is None else f"${value:,.0f}"


def _fmt_ratio(value):
    return "—" if value is None else f"{value:.2f}"


def _fmt_pct(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def _unavailable_html(section: SectionResult) -> str:
    return f'<p class="unavailable">Indisponible — {_e(section.unavailable_reason)}</p>'


_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _humanize_xbrl_tag(concept: str) -> str:
    """
    Inserts spaces at camelCase boundaries in an XBRL concept name (e.g.
    "RevenueFromContractWithCustomerExcludingAssessedTax" -> "Revenue
    From Contract With Customer Excluding Assessed Tax") so it wraps
    across multiple lines in a table cell instead of overflowing the
    page edge.

    Confirmed necessary, and confirmed to be the only technique that
    actually works, by rendering real reports and inspecting the pages:
    xhtml2pdf respects neither `word-break: break-all` nor `<wbr>` (both
    left a long unbroken tag name running off the page), but DOES wrap
    normally at real spaces -- so inserting them is what's used here,
    not word-break or wbr.
    """
    return _CAMEL_CASE_BOUNDARY_RE.sub(" ", concept)


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
    if revenue is not None and net_income is not None:
        lines.append(
            f"Revenue de {_fmt_currency(revenue)} et résultat net de "
            f"{_fmt_currency(net_income)} pour l'exercice {report.filing.fiscal_year}."
        )
    elif revenue is not None:
        lines.append(f"Revenue de {_fmt_currency(revenue)} pour l'exercice {report.filing.fiscal_year}.")

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
        lines.append('<span class="warn">⚠ Altman Z-Score en zone de détresse financière.</span>')
    if report.beneish_m.available and report.beneish_m.value.flagged:
        lines.append('<span class="warn">⚠ Beneish M-Score signale un risque de manipulation comptable.</span>')

    if report.mdna_diff.available:
        added = sum(1 for seg in report.mdna_diff.value.overall.segments if seg.kind == "added")
        removed = sum(1 for seg in report.mdna_diff.value.overall.segments if seg.kind == "removed")
        lines.append(f"MD&amp;A : {added} ajout(s), {removed} suppression(s) vs la période précédente.")

    if report.mdna_sentiment.available:
        tone = report.mdna_sentiment.value.net_tone
        direction = "positive" if tone > 0.1 else "négative" if tone < -0.1 else "neutre"
        lines.append(f"Tonalité du MD&amp;A : {direction} ({_fmt_ratio(tone)}).")

    return lines


def _render_executive_summary(report: ReportData) -> str:
    lines = _executive_summary_lines(report)
    if not lines:
        return ""
    items = "\n".join(f"<li>{line}</li>" for line in lines)
    return f'<div class="exec-summary"><h2>Résumé exécutif</h2><ul>{items}</ul></div>'


def _render_ai_summary(report: ReportData) -> str:
    """
    Opt-in only (see report/ai_summary.py): `report.ai_summary` is None
    unless a caller explicitly called `attach_ai_summary`, in which case
    this renders nothing at all -- no "indisponible" placeholder either,
    since most reports simply never requested this section, and showing
    an empty/failed AI box by default would misleadingly suggest it's a
    normal part of every report.
    """
    if report.ai_summary is None:
        return ""
    if not report.ai_summary.available:
        return f"""
        <div class="ai-summary">
          <h2>Synthèse générée par IA</h2>
          <p class="unavailable">Indisponible — {_e(report.ai_summary.unavailable_reason)}</p>
        </div>
        """
    text = _e(report.ai_summary.value["text"])
    model = _e(report.ai_summary.value["model"])
    return f"""
    <div class="ai-summary">
      <h2>Synthèse générée par IA <span class="ai-badge">{model}</span></h2>
      <p>{text}</p>
      <p class="ai-disclaimer">Générée automatiquement à partir des données déjà
      calculées dans ce rapport (aucune connaissance externe sur la société) —
      à vérifier, ne constitue pas un conseil en investissement.</p>
    </div>
    """


def _render_header(report: ReportData) -> str:
    filing = report.filing
    source_link = (
        f' — <a href="{_e(report.source_filing_url)}">source SEC EDGAR</a>'
        if report.source_filing_url else ""
    )
    return f"""
    <h1>{_e(filing.company_name)} ({_e(filing.ticker)})</h1>
    <p class="subtitle">
      {_e(filing.form_type.value)} — exercice {filing.fiscal_year} {_e(filing.fiscal_period)}
      — déposé le {filing.filed_date.isoformat()}
      — CIK {_e(filing.cik)} — accession {_e(filing.accession_number)}{source_link}
    </p>
    """


def _render_financial_highlights(report: ReportData) -> str:
    if not report.financial_highlights:
        return '<h2>Chiffres clés</h2><p class="unavailable">Aucune donnée financière extraite pour ce filing.</p>'
    rows = "\n".join(
        f"<tr><td>{_e(h.label)}</td><td>{_fmt_currency(h.value)}</td>"
        f"<td>{_e(_humanize_xbrl_tag(h.concept)) if h.concept else '—'}</td></tr>"
        for h in report.financial_highlights
    )
    completeness_note = ""
    if report.financial_data_completeness is not None:
        completeness_note = (
            f'<p>Complétude des données financières : '
            f'<strong>{_fmt_pct(report.financial_data_completeness)}</strong> '
            f"des métriques attendues ont été résolues pour ce filing "
            f"(le reste — voir les sections indisponibles ci-dessous —"
            f" n'est pas forcément un manque : certaines métriques ne "
            f"s'appliquent pas à tous les secteurs).</p>"
        )
    return f"""
    <h2>Chiffres clés</h2>
    {completeness_note}
    <table>
      <tr><th>Poste</th><th>Valeur</th><th>Tag XBRL</th></tr>
      {rows}
    </table>
    """


def _render_altman(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>Altman Z-Score</h3>{_unavailable_html(section)}"
    result = section.value
    return f"""
    <h3>Altman Z-Score ({_e(result.variant)})</h3>
    <p>Score : <strong>{_fmt_ratio(result.score)}</strong>
       — zone : <span class="zone-{_e(result.zone)}">{_e(result.zone)}</span></p>
    """


def _render_beneish(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>Beneish M-Score</h3>{_unavailable_html(section)}"
    result = section.value
    flagged_class = "flagged-true" if result.flagged else "flagged-false"
    flagged_label = "signalé" if result.flagged else "non signalé"
    return f"""
    <h3>Beneish M-Score</h3>
    <p>Score : <strong>{_fmt_ratio(result.score)}</strong> (seuil {_fmt_ratio(result.threshold)})
       — <span class="{flagged_class}">{flagged_label}</span></p>
    """


def _render_piotroski(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>Piotroski F-Score</h3>{_unavailable_html(section)}"
    result = section.value
    criteria_rows = "\n".join(
        f"<tr><td>{_e(name.replace('_', ' '))}</td><td>{'✓' if passed else '✗'}</td></tr>"
        for name, passed in result.criteria.items()
    )
    return f"""
    <h3>Piotroski F-Score</h3>
    <p>Score : <strong>{result.score} / {result.max_score}</strong></p>
    <table>{criteria_rows}</table>
    """


def _render_red_flags(report: ReportData) -> str:
    return f"""
    <h2>Red Flags</h2>
    <div class="card">{_render_altman(report.altman_z)}</div>
    <div class="card">{_render_beneish(report.beneish_m)}</div>
    <div class="card">{_render_piotroski(report.piotroski_f)}</div>
    """


def _diff_body_html(segments) -> str:
    removed = [seg for seg in segments if seg.kind == "removed"]
    added = [seg for seg in segments if seg.kind == "added"]
    body = "".join(f'<span class="segment-removed">{_e(seg.text)}</span>' for seg in removed)
    body += "".join(f'<span class="segment-added">{_e(seg.text)}</span>' for seg in added)
    return body


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


def _render_text_diff(title: str, grouped) -> str:
    """
    Renders a GroupedTextDiffResult (see diff/grouped_diff.py). The
    overall similarity/added/removed summary line is always the exact
    same aggregate the flat diff would have shown.

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
    equal_count = sum(1 for seg in overall.segments if seg.kind == "equal")
    header = f"""
    <h3>{_e(title)}</h3>
    <p>Similarité : {_fmt_pct(overall.similarity_ratio)}
       — {len(added)} ajout(s), {len(removed)} suppression(s), {equal_count} inchangé(s)</p>
    """
    if not added and not removed:
        return header + "<p>Aucun changement détecté.</p>"

    groups = grouped.groups
    if len(groups) == 1 and groups[0].heading == "":
        return header + _diff_body_html(groups[0].diff.segments)

    changed_groups = []
    unchanged_group_count = 0
    for group in groups:
        g_added = sum(1 for seg in group.diff.segments if seg.kind == "added")
        g_removed = sum(1 for seg in group.diff.segments if seg.kind == "removed")
        if not g_added and not g_removed:
            unchanged_group_count += 1
            continue
        changed_groups.append((group, g_added, g_removed))

    matched_groups = sorted(
        (item for item in changed_groups if item[0].status == "matched"),
        key=lambda item: _group_change_weight(item[0]),
        reverse=True,
    )
    detailed_ids = {id(item[0]) for item in matched_groups[:_MAX_DETAILED_GROUPS]}

    parts = [header]
    compact_count = 0
    for group, g_added, g_removed in changed_groups:
        heading_label = _e(group.heading) if group.heading else "Introduction"
        status_note = _GROUP_STATUS_NOTE[group.status]
        if group.status != "matched" or id(group) not in detailed_ids:
            compact_count += 1
            parts.append(
                f'<p class="group-stats"><strong>{heading_label}</strong>{status_note}'
                f" — {g_added} ajout(s), {g_removed} suppression(s)</p>"
            )
            continue
        parts.append(f"""
        <h4>{heading_label}{status_note}</h4>
        <p class="group-stats">{g_added} ajout(s), {g_removed} suppression(s)</p>
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


def _render_mdna_diff(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>MD&amp;A (Item 7)</h3>{_unavailable_html(section)}"
    # NOTE: plain "&", not "&amp;" -- this goes through _render_text_diff's
    # own _e(title) call, which would otherwise double-escape it into
    # "&amp;amp;A", rendering as the literal text "MD&amp;A" in the PDF
    # instead of "MD&A" (caught by visually inspecting a real rendered
    # report, not just running the tests).
    return _render_text_diff("MD&A (Item 7)", section.value)


def _render_risk_factors_diff(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>Risk Factors (Item 1A)</h3>{_unavailable_html(section)}"
    rf_result = section.value  # RiskFactorsDiffResult
    if rf_result.skipped:
        return (
            f"<h3>Risk Factors (Item 1A)</h3>"
            f'<p class="skip-note">{_e(rf_result.skip_reason)}</p>'
        )
    return _render_text_diff("Risk Factors (Item 1A)", rf_result.diff)


def _render_diff_section(report: ReportData) -> str:
    return f"""
    <h2>Changements textuels vs période précédente</h2>
    <div class="card">{_render_risk_factors_diff(report.risk_factors_diff)}</div>
    <div class="card">{_render_mdna_diff(report.mdna_diff)}</div>
    """


def _render_sentiment_result(title: str, result) -> str:
    counts_rows = "\n".join(
        f"<tr><td>{_e(category.replace('_', ' '))}</td><td>{count}</td>"
        f"<td>{_fmt_pct(result.proportions[category])}</td></tr>"
        for category, count in result.counts.items()
    )
    return f"""
    <h3>{_e(title)}</h3>
    <p>Tonalité nette : <strong>{_fmt_ratio(result.net_tone)}</strong>
       ({result.total_word_count} mots analysés)</p>
    <table><tr><th>Catégorie</th><th>Occurrences</th><th>Proportion</th></tr>{counts_rows}</table>
    """


def _render_mdna_sentiment(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>MD&amp;A (Item 7)</h3>{_unavailable_html(section)}"
    # plain "&" here too -- see the matching note in _render_mdna_diff.
    return _render_sentiment_result("MD&A (Item 7)", section.value)


def _render_risk_factors_sentiment(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>Risk Factors (Item 1A)</h3>{_unavailable_html(section)}"
    rf_result = section.value  # RiskFactorsSentimentResult
    if rf_result.skipped:
        return (
            f"<h3>Risk Factors (Item 1A)</h3>"
            f'<p class="skip-note">{_e(rf_result.skip_reason)}</p>'
        )
    return _render_sentiment_result("Risk Factors (Item 1A)", rf_result.result)


def _render_sentiment_section(report: ReportData) -> str:
    return f"""
    <h2>Sentiment (Loughran-McDonald)</h2>
    <div class="card">{_render_risk_factors_sentiment(report.risk_factors_sentiment)}</div>
    <div class="card">{_render_mdna_sentiment(report.mdna_sentiment)}</div>
    """


def render_html(report: ReportData) -> str:
    """Renders `report` into a complete, self-contained HTML document."""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{_e(report.filing.company_name)} — {_e(report.filing.form_type.value)} {report.filing.fiscal_year} {_e(report.filing.fiscal_period)}</title>
  <style>{_CSS}</style>
</head>
<body>
  <div id="footer_content">Page <pdf:pagenumber /> / <pdf:pagecount /></div>
  {_render_header(report)}
  {_render_executive_summary(report)}
  {_render_ai_summary(report)}
  {_render_financial_highlights(report)}
  {_render_red_flags(report)}
  {_render_diff_section(report)}
  {_render_sentiment_section(report)}
  <p class="footer">
    Rapport généré automatiquement le {report.generated_at.isoformat()} à partir des
    données SEC EDGAR. Voir le code source du projet pour la méthodologie complète
    et les approximations documentées de chaque module.
  </p>
</body>
</html>
"""


def _trend_altman_cell(section: SectionResult) -> str:
    if not section.available:
        return '<span class="unavailable">—</span>'
    result = section.value
    return f'{_fmt_ratio(result.score)} <span class="zone-{_e(result.zone)}">({_e(result.zone)})</span>'


def _trend_beneish_cell(section: SectionResult) -> str:
    if not section.available:
        return '<span class="unavailable">—</span>'
    result = section.value
    flagged_class = "flagged-true" if result.flagged else "flagged-false"
    return f'<span class="{flagged_class}">{_fmt_ratio(result.score)}</span>'


def _trend_piotroski_cell(section: SectionResult) -> str:
    if not section.available:
        return '<span class="unavailable">—</span>'
    result = section.value
    return f"{result.score} / {result.max_score}"


def _trend_sentiment_cell(section: SectionResult) -> str:
    if not section.available:
        return '<span class="unavailable">—</span>'
    return _fmt_ratio(section.value.net_tone)


def _render_trend_row(point: TrendPoint) -> str:
    financials = point.filing.financials
    revenue = financials.revenue.value if financials and financials.revenue else None
    net_income = financials.net_income.value if financials and financials.net_income else None
    report = point.report
    source_link = (
        f'<a href="{_e(report.source_filing_url)}">filing</a>'
        if report.source_filing_url else "—"
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
    years_span = f"{trend.points[0].fiscal_year}–{trend.points[-1].fiscal_year}"
    return f"""
    <div class="cover">
      <div class="cover-kicker">Analyse de tendance</div>
      <div class="cover-title">{_e(last_filing.company_name)} ({_e(last_filing.ticker)})</div>
      <div class="cover-subtitle">Exercices {years_span}</div>
      <div class="cover-meta">
        Généré automatiquement à partir des données SEC EDGAR — {len(trend.points)} exercice(s) analysé(s)
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
            f'<span class="warn">⚠ Beneish M-Score a signalé un risque de manipulation '
            f"comptable pour : {', '.join(flagged_years)}.</span>"
        )

    distress_years = [
        str(p.fiscal_year) for p in trend.points
        if p.report.altman_z.available and p.report.altman_z.value.zone == "distress"
    ]
    if distress_years:
        lines.append(
            f'<span class="warn">⚠ Altman Z-Score en zone de détresse pour : '
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
            value_labels=[f"{v} / 9" if v is not None else "—" for v in piotroski_values],
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
    years_span = f"{trend.points[0].fiscal_year}–{trend.points[-1].fiscal_year}"
    rows = "\n".join(_render_trend_row(p) for p in trend.points)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{_e(last_filing.company_name)} — historique {years_span}</title>
  <style>{_CSS}</style>
</head>
<body>
  <div id="footer_content">Page <pdf:pagenumber /> / <pdf:pagecount /></div>
  {_render_trend_cover(trend)}
  <div class="page-break">
    <h1>{_e(last_filing.company_name)} ({_e(last_filing.ticker)}) — historique {years_span}</h1>
    <p class="subtitle">
      Chaque exercice est comparé à celui immédiatement précédent dans la série
      (jamais une année sautée) — Beneish M, Piotroski F et la tonalité MD&amp;A
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
      Pas de comparaison sectorielle dans cette vue — voir le rapport détaillé
      de chaque exercice pour le détail complet de chaque section.
    </p>
  </div>
</body>
</html>
"""
