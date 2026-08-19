"""
The sector synthesis, laid out. Same look as the single report, reusing
its stylesheet, font and page shell, so the two read as one product.

WHAT GETS A TABLE AND WHAT GETS PROSE. The fil directeur and the tone are
prose, because they are read. The divergences and the guidance are
table-shaped ("this company said X, that one said Y") and a paragraph
would flatten exactly the contrast that is the point, so they are laid
out as tables. Same principle the single report follows.
"""

from __future__ import annotations

from .html_renderer import _document, _e, _t
from .markdown import markdown_to_html


def _header(synthesis) -> str:
    pairs = ", ".join(
        f"{_t(t)} ({_t(synthesis.quarters.get(t, '?'))})" for t in synthesis.tickers
    )
    warn = ""
    if synthesis.mixed_quarters:
        warn = (
            '<p class="note">Trimestres non alignes : chaque societe est lue sur son '
            "dernier call disponible, le trimestre est indique a cote de son nom. Un "
            "ecart entre pairs peut donc porter sur des moments differents.</p>"
        )
    return f"""
    <h1>Synthese sectorielle</h1>
    <p class="subtitle">{pairs}</p>
    {warn}
    """


def _fil_directeur(synthesis) -> str:
    if not synthesis.fil_directeur:
        return ""
    return f"""
    <h2>Le fil directeur du secteur</h2>
    {markdown_to_html(synthesis.fil_directeur)}
    """


def _divergences(synthesis) -> str:
    if not synthesis.divergences:
        return ""
    rows = []
    for d in synthesis.divergences:
        positions = "<br/>".join(
            f"<b>{_t(p.get('ticker', '?'))}</b> : {_t(p.get('position', ''))}"
            for p in d.get("positions", []) if isinstance(p, dict)
        )
        rows.append(
            f"<tr><td>{_t(d.get('driver', ''))}</td><td>{positions}</td>"
            f"<td>{_t(d.get('qui_diverge', ''))}</td></tr>"
        )
    return f"""
    <h2>Les divergences qui comptent</h2>
    <table>
      <tr><th>Driver</th><th>Ce que dit chacun</th><th>Ou l'ecart est net</th></tr>
      {''.join(rows)}
    </table>
    """


def _questions_marche(synthesis) -> str:
    if not synthesis.questions_marche:
        return ""
    blocks = []
    for q in synthesis.questions_marche:
        presse = ", ".join(_t(x) for x in q.get("presse_chez", []) or [])
        repondu = ", ".join(_t(x) for x in q.get("repondu_par", []) or [])
        esquive = ", ".join(_t(x) for x in q.get("esquive_par", []) or [])
        detail = []
        if presse:
            detail.append(f"Pressee chez : {presse}.")
        if repondu:
            detail.append(f"Repondu par : {repondu}.")
        if esquive:
            detail.append(f"Esquive par : {esquive}.")
        blocks.append(
            f'<p class="bullet">· <b>{_t(q.get("sujet", ""))}</b> '
            f'{" ".join(detail)} {_t(q.get("lecture", ""))}</p>'
        )
    return f"""
    <h2>Ce que le marche redoute, vu par les questions</h2>
    {''.join(blocks)}
    """


def _guidance(synthesis) -> str:
    if not synthesis.guidance:
        return ""
    rows = []
    for g in synthesis.guidance:
        cells = "<br/>".join(
            f"<b>{_t(s.get('ticker', '?'))}</b> : {_t(s.get('mouvement', ''))}"
            f"{(', ' + _t(s.get('detail', ''))) if s.get('detail') else ''}"
            for s in g.get("par_societe", []) if isinstance(s, dict)
        )
        rows.append(f"<tr><td>{_t(g.get('metrique', ''))}</td><td>{cells}</td></tr>")
    return f"""
    <h2>Guidance : qui bouge, qui tient</h2>
    <table>
      <tr><th>Metrique</th><th>Par societe</th></tr>
      {''.join(rows)}
    </table>
    """


def _read_throughs(synthesis) -> str:
    if not synthesis.read_throughs:
        return ""
    blocks = []
    for r in synthesis.read_throughs:
        base = f' <span class="muted">({_t(r.get("base", ""))})</span>' if r.get("base") else ""
        blocks.append(
            f'<p class="bullet">· <b>{_t(r.get("de", "?"))} → {_t(r.get("vers", "?"))}</b> : '
            f'{_t(r.get("implication", ""))}{base}</p>'
        )
    return f"""
    <h2>Read-throughs</h2>
    <p class="note">Ce que la declaration d'une societe implique pour une autre. Deductions, appuyees sur un fait du call.</p>
    {''.join(blocks)}
    """


def _ton(synthesis) -> str:
    if not synthesis.ton:
        return ""
    return f"""
    <h2>Ton et posture</h2>
    {markdown_to_html(synthesis.ton)}
    """


def _provenance(synthesis) -> str:
    return f"""
    <p class="footer">
      Synthese de {len(synthesis.tickers)} societes, lue par {_t(synthesis.model)},
      a partir des rapports archives (lecture, attentes, questions-reponses). Aucune
      donnee de valorisation, aucun cours : la matiere est ce qui a ete dit dans les
      calls. Pas de recommandation.
    </p>
    """


def render_sector_html(synthesis) -> str:
    """One document, two to three pages, in reading order."""
    title = "Synthese sectorielle " + ", ".join(_t(t) for t in synthesis.tickers)
    body = f"""
  {_header(synthesis)}
  {_fil_directeur(synthesis)}
  {_divergences(synthesis)}
  {_questions_marche(synthesis)}
  {_guidance(synthesis)}
  {_read_throughs(synthesis)}
  {_ton(synthesis)}
  {_provenance(synthesis)}
"""
    return _document(title, body)


__all__ = ["render_sector_html"]
