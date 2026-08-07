"""
First AI pass: pick which sub-themes of a filing are worth diffing.

The pipeline used to diff every sub-theme of a section and then rank the
results for display. That put the choice of "what matters" on a word-count
proxy -- more changed words meant more important -- which is a poor stand-in
for analyst relevance: a heavily-reworded legal boilerplate section outranks
a three-word change to pricing guidance.

This module moves that judgment to where it belongs, in three steps:

  1. Python lists the sub-theme headings actually present in the filing
     (`list_subthemes` -- pure, no network, reuses the same heading
     detection the diff itself uses, so the two can never disagree about
     what a sub-theme is).
  2. The model picks up to `MAX_SELECTED_THEMES` of them: the ones an
     analyst covering this company would actually be watching
     (`select_key_subthemes`).
  3. Python diffs only those, and a later pass writes the executive
     summary over that same selection.

The model only ever CHOOSES FROM the list Python gives it -- it never
invents a heading. Any returned heading that isn't in the input list is
dropped (see `_parse_selection`), so a hallucinated sub-theme can't enter
the pipeline: at worst the selection comes back short, never wrong.

Degrades to "keep everything" whenever the selection can't be made (no
API key, network failure, unparseable answer, or a filing with no
detected sub-headings at all). That keeps the report generatable offline
and free, exactly as before this module existed -- the AI makes the
report sharper, it is not load-bearing for producing one.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

from ..diff.grouped_diff import apply_theme_selection, split_into_groups

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 1000
DEFAULT_TIMEOUT_SECONDS = 30.0

# Hard ceiling on how many sub-themes get diffed and summarized. Set by
# the user: a two-page report can't carry more than this without turning
# back into the wall of text this redesign exists to replace.
MAX_SELECTED_THEMES = 10

# Sub-themes shorter than this are almost always a table-of-contents
# echo or a stray heading-shaped line rather than a real section with
# content worth diffing. Filtered before the model ever sees them, so
# its limited picks aren't spent on noise.
_MIN_THEME_WORDS = 30

_SELECTION_SYSTEM_PROMPT = """Tu es analyste equity. On te donne la liste des sous-sections d'un filing SEC (10-K ou 10-Q) d'une societe, avec pour chacune sa taille.

Ta tache : selectionner celles qu'un analyste couvrant cette societe surveillerait REELLEMENT ce trimestre -- celles ou un changement de formulation serait materiel pour la these d'investissement.

Priorise :
- ce qui touche au chiffre d'affaires, aux prix, aux volumes, aux marges, a la demande, aux carnets de commandes ;
- la concentration client/fournisseur, les contraintes de capacite ou d'approvisionnement ;
- les risques reglementaires, juridiques ou geopolitiques susceptibles de bloquer une activite precise ;
- ce qui est specifique a cette societe et a son secteur.

Deprioriser :
- le boilerplate juridique generique present dans tous les filings ;
- les sections purement procedurales ou administratives ;
- ce qui ne bougerait la these d'aucun analyste.

Contraintes :
- Choisis AU MAXIMUM %(max_themes)d sous-sections, classees de la plus importante a la moins importante.
- Choisis UNIQUEMENT dans la liste fournie. Ne reformule pas, ne raccourcis pas, ne fusionne pas les intitules : recopie-les a l'identique.
- S'il y a moins de %(max_themes)d sous-sections pertinentes, en renvoyer moins est correct et preferable.
- Reponds UNIQUEMENT par un tableau JSON d'intitules, sans commentaire ni texte autour. Exemple de forme attendue : ["Intitule A", "Intitule B"]"""


class ThemeSelectionError(Exception):
    pass


@dataclass(frozen=True)
class SubTheme:
    heading: str
    word_count: int


@dataclass(frozen=True)
class ThemeSelection:
    """
    `headings` is the ordered selection (most important first).
    `reason` explains why the selection is what it is -- set both on
    success ("selected by <model>") and on every fallback path, so the
    report can always say honestly how its sub-themes were chosen rather
    than leaving the reader to assume an AI made the call when it didn't.
    """
    headings: list  # list[str]
    reason: str
    ai_selected: bool


def list_subthemes(text: str) -> list:
    """
    The sub-themes Python detects in `text`, as SubTheme(heading, words).

    Pure, no network. Uses the diff's own heading detection so the two
    can never disagree about what counts as a sub-theme. Drops the
    unheaded preamble (heading == "") and anything too short to be a
    real section -- see _MIN_THEME_WORDS.
    """
    themes = []
    for heading, content in split_into_groups(text):
        if not heading:
            continue
        words = len(content.split())
        if words < _MIN_THEME_WORDS:
            continue
        themes.append(SubTheme(heading=heading, word_count=words))
    return themes


def _parse_selection(raw: str, valid_headings: list) -> list:
    """
    Parses the model's JSON array of headings, keeping only headings that
    really exist in `valid_headings` -- the guarantee that a hallucinated
    or reworded sub-theme never enters the pipeline.

    Tolerates the answer being wrapped in prose or a ``` fence (asked for
    bare JSON, but a stray "Voici la selection :" shouldn't lose the whole
    pass) by extracting the outermost [...] span.
    """
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise ThemeSelectionError(f"no JSON array found in the model's answer: {raw[:200]}")
    try:
        parsed = json.loads(match.group(0))
    except ValueError as exc:
        raise ThemeSelectionError(f"unparseable JSON in the model's answer: {exc}") from exc
    if not isinstance(parsed, list):
        raise ThemeSelectionError("the model's answer was not a JSON array")

    by_heading = {h: True for h in valid_headings}
    selected = []
    for item in parsed:
        if isinstance(item, str) and item in by_heading and item not in selected:
            selected.append(item)
    return selected[:MAX_SELECTED_THEMES]


def _call_selection_api(
    themes: list,
    *,
    company: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
) -> str:
    listing = "\n".join(f'- "{t.heading}" ({t.word_count} mots)' for t in themes)
    user_content = f"Societe : {company}\n\nSous-sections disponibles :\n{listing}"
    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": DEFAULT_MAX_TOKENS,
                "temperature": 0,
                "system": _SELECTION_SYSTEM_PROMPT % {"max_themes": MAX_SELECTED_THEMES},
                "messages": [{"role": "user", "content": user_content}],
            },
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise ThemeSelectionError(f"network error calling the Claude API: {exc}") from exc

    if response.status_code != 200:
        raise ThemeSelectionError(
            f"Claude API returned HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        return response.json()["content"][0]["text"].strip()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ThemeSelectionError(f"unexpected Claude API response shape: {exc}") from exc


def _fallback(themes: list, reason: str) -> ThemeSelection:
    """
    Keeps the biggest sub-themes, up to the cap. Word count is a weak
    proxy for importance -- replacing it is this module's whole point --
    but when the model can't be reached it's the only signal available,
    and `reason` says so in the report rather than passing it off as an
    analyst-grade selection.
    """
    ranked = sorted(themes, key=lambda t: t.word_count, reverse=True)
    return ThemeSelection(
        headings=[t.heading for t in ranked[:MAX_SELECTED_THEMES]],
        reason=reason,
        ai_selected=False,
    )


def select_key_subthemes(
    text: str,
    *,
    company: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ThemeSelection:
    """
    Picks up to MAX_SELECTED_THEMES sub-themes of `text` worth diffing.

    Never raises: every failure path returns a `ThemeSelection` whose
    `reason` states plainly how the selection was actually made, so the
    report can never imply an AI chose the sub-themes when a fallback
    did.
    """
    themes = list_subthemes(text)
    if not themes:
        return ThemeSelection(
            headings=[],
            reason="aucune sous-section détectée dans cette section du filing",
            ai_selected=False,
        )
    if len(themes) <= MAX_SELECTED_THEMES:
        return ThemeSelection(
            headings=[t.heading for t in themes],
            reason=(
                f"{len(themes)} sous-section(s) détectée(s), toutes retenues "
                f"(pas plus que le maximum de {MAX_SELECTED_THEMES})"
            ),
            ai_selected=False,
        )

    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        return _fallback(
            themes,
            f"sélection IA non demandée — {MAX_SELECTED_THEMES} sous-sections les "
            f"plus volumineuses retenues à défaut (sur {len(themes)})",
        )

    resolved_model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    try:
        raw = _call_selection_api(
            themes,
            company=company,
            api_key=resolved_key,
            model=resolved_model,
            timeout_seconds=timeout_seconds,
        )
        selected = _parse_selection(raw, [t.heading for t in themes])
    except ThemeSelectionError as exc:
        return _fallback(
            themes,
            f"sélection IA indisponible ({exc}) — {MAX_SELECTED_THEMES} sous-sections "
            f"les plus volumineuses retenues à défaut",
        )

    if not selected:
        return _fallback(
            themes,
            "la sélection IA n'a retourné aucune sous-section valide — "
            f"{MAX_SELECTED_THEMES} sous-sections les plus volumineuses retenues à défaut",
        )

    return ThemeSelection(
        headings=selected,
        reason=(
            f"{len(selected)} sous-section(s) sur {len(themes)} retenues par {resolved_model} "
            f"comme les plus suivies par les analystes"
        ),
        ai_selected=True,
    )


def attach_theme_selection(
    report,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
):
    """
    Returns a NEW ReportData whose Risk Factors diff carries the
    analyst-relevant sub-theme selection (step 2 of the three-step flow
    in this module's docstring), plus a `theme_selection` section
    recording how that selection was made.

    Applied to Risk Factors only, deliberately: that's the section real
    filings organize under named sub-themes, and the only one where
    `split_into_groups` finds anything to select FROM. The MD&A is
    almost always one unheaded block, so "selecting sub-themes" in it
    would be selecting the whole thing -- a no-op dressed up as a
    decision.

    Never raises. Import is local to avoid a circular import at module
    load (report_data imports the diff layer, this imports report_data's
    types only at call time).
    """
    import dataclasses

    from .report_data import SectionResult

    rf_section = report.risk_factors_diff
    if rf_section is None or not rf_section.available or rf_section.value.skipped:
        reason = "diff Risk Factors indisponible — aucune sous-section à sélectionner"
        return dataclasses.replace(
            report,
            theme_selection=SectionResult(value=None, unavailable_reason=reason),
        )

    current_text = report.filing.text_sections.item_1a_risk_factors
    selection = select_key_subthemes(
        current_text,
        company=f"{report.filing.company_name} ({report.filing.ticker})",
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
    )
    if not selection.headings:
        return dataclasses.replace(
            report,
            theme_selection=SectionResult(value=selection, unavailable_reason=None),
        )

    rf_result = rf_section.value
    selected_diff = apply_theme_selection(rf_result.diff, selection.headings)
    return dataclasses.replace(
        report,
        risk_factors_diff=SectionResult(
            value=dataclasses.replace(rf_result, diff=selected_diff),
            unavailable_reason=None,
        ),
        theme_selection=SectionResult(value=selection, unavailable_reason=None),
    )


__all__ = [
    "MAX_SELECTED_THEMES",
    "SubTheme",
    "ThemeSelection",
    "ThemeSelectionError",
    "attach_theme_selection",
    "list_subthemes",
    "select_key_subthemes",
]
