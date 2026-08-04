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

from html import escape as _e

from .report_data import ReportData, SectionResult
from .trend import TrendAnalysis, TrendPoint

_CSS = """
  body { font-family: Helvetica, Arial, sans-serif; color: #1a1a1a; font-size: 11pt; }
  h1 { font-size: 18pt; margin-bottom: 2pt; }
  h2 { font-size: 13pt; margin-top: 20pt; border-bottom: 1px solid #ccc; padding-bottom: 4pt; }
  h3 { font-size: 11pt; margin-top: 12pt; }
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
"""


def _fmt_currency(value):
    return "—" if value is None else f"${value:,.0f}"


def _fmt_ratio(value):
    return "—" if value is None else f"{value:.2f}"


def _fmt_pct(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def _unavailable_html(section: SectionResult) -> str:
    return f'<p class="unavailable">Indisponible — {_e(section.unavailable_reason)}</p>'


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
        f"<td>{_e(h.concept) if h.concept else '—'}</td></tr>"
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


def _render_text_diff(title: str, diff_result) -> str:
    added = [seg for seg in diff_result.segments if seg.kind == "added"]
    removed = [seg for seg in diff_result.segments if seg.kind == "removed"]
    equal_count = sum(1 for seg in diff_result.segments if seg.kind == "equal")
    body = "".join(f'<span class="segment-removed">{_e(seg.text)}</span>' for seg in removed)
    body += "".join(f'<span class="segment-added">{_e(seg.text)}</span>' for seg in added)
    if not added and not removed:
        body = "<p>Aucun changement détecté.</p>"
    return f"""
    <h3>{_e(title)}</h3>
    <p>Similarité : {_fmt_pct(diff_result.similarity_ratio)}
       — {len(added)} ajout(s), {len(removed)} suppression(s), {equal_count} inchangé(s)</p>
    {body}
    """


def _render_mdna_diff(section: SectionResult) -> str:
    if not section.available:
        return f"<h3>MD&amp;A (Item 7)</h3>{_unavailable_html(section)}"
    return _render_text_diff("MD&amp;A (Item 7)", section.value)


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
    return _render_sentiment_result("MD&amp;A (Item 7)", section.value)


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
  {_render_header(report)}
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
  <h1>{_e(last_filing.company_name)} ({_e(last_filing.ticker)}) — historique {years_span}</h1>
  <p class="subtitle">
    Chaque exercice est comparé à celui immédiatement précédent dans la série
    (jamais une année sautée) — Beneish M, Piotroski F et la tonalité MD&amp;A
    reflètent donc toujours une vraie comparaison année sur année.
  </p>
  <table>
    <tr>
      <th>Exercice</th><th>Revenue</th><th>Net Income</th>
      <th>Complétude</th><th>Altman Z</th><th>Beneish M</th>
      <th>Piotroski F</th><th>Tonalité MD&amp;A</th><th>Source</th>
    </tr>
    {rows}
  </table>
  <p class="footer">
    Rapport de tendance généré automatiquement à partir des données SEC EDGAR.
    Pas de comparaison sectorielle dans cette vue — voir le rapport détaillé
    de chaque exercice pour le détail complet de chaque section.
  </p>
</body>
</html>
"""
