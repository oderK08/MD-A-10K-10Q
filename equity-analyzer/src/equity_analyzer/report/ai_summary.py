"""
Module 6 (opt-in) -- an AI-generated plain-language synthesis of the
report's ALREADY-COMPUTED sections (red flags, sentiment, the grouped
Risk Factors/MD&A diff from grouped_diff.py), via the Claude API.

Deliberately kept SEPARATE from build_report_data(), never called
automatically: unlike every other module in this project, a real call
here is billed, requires network + an API key, and is not
deterministic. A caller must explicitly opt in via `attach_ai_summary`,
handed an already-built (free, deterministic) ReportData.

Two framing decisions, made explicitly rather than left to prompt
improvisation, because this is an advisory-use tool:

1. ANCHORED INTERPRETATION, not a bare restatement of numbers.
   Originally shipped as a strict factual synthesis with NO
   interpretation at all -- the user later asked (explicitly, after
   seeing that version in practice) for the model to actually connect
   the computed signals and give a real reading of what they mean
   together (e.g. "a Piotroski decline alongside a more negative MD&A
   tone points to..."), not just list them. Re-confirmed via
   AskUserQuestion rather than assumed. What's still forbidden,
   unchanged: an explicit directional or investment call ("bullish",
   "buy", "management is downplaying the risk", a judgment on
   management quality) -- forming that call is the human reader's job,
   not the tool's. Interpreting a signal is not the same as advising an
   action on it.
2. GROUNDED ONLY in this report's own computed fields -- financials,
   red-flag results, per-sub-theme diff summaries with short real
   excerpts, sentiment scores -- never in whatever the model happens
   to know about the company from training data. Re-confirmed via the
   same AskUserQuestion as (1), not relaxed alongside it: a model's
   background knowledge of a real company is impossible to verify or
   attribute to this specific filing, so letting it leak in would
   silently blend real, sourced analysis with unverifiable recall. The
   system prompt says so explicitly and asks the model to say "données
   insuffisantes" rather than fill a gap from outside knowledge.

If the API call fails for ANY reason (no key, network, rate limit, bad
response shape), the section comes back `unavailable` with a plain
reason -- exactly like every other optional SectionResult in this
project -- so this one extra, non-essential module can never take down
report generation.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Optional

import requests

from .report_data import ReportData, SectionResult

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
# Haiku 4.5: cheap and fast, appropriate for a short factual synthesis
# task with all the real grounding already handed to it in the prompt --
# not asked to reason hard, just to restate structured facts in prose.
# Override via the ANTHROPIC_MODEL env var (or the `model` parameter)
# for a stronger model if desired.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 500
DEFAULT_TIMEOUT_SECONDS = 30.0

_SYSTEM_PROMPT = """Tu rediges une synthese courte (4 a 6 phrases, en francais) pour la section "Synthese IA" d'un rapport d'analyse actions.

Ton role est d'INTERPRETER les donnees deja calculees ci-dessous, pas seulement de les lister : relie les signaux entre eux et donne une vraie lecture de ce qui se degage de leur combinaison (ex: un score de red flag qui se degrade en meme temps qu'une tonalite qui devient plus negative sur le meme sous-theme, c'est un signal plus fort que chacun pris isolement -- dis-le explicitement).

Regles strictes, non negociables :
1. N'utilise QUE les informations fournies ci-dessous pour construire ton interpretation. N'utilise JAMAIS une connaissance que tu aurais sur cette societe ou son secteur par ailleurs (autres filings, actualites, memoire d'entrainement) -- si une information n'est pas dans le texte fourni, ne l'invente pas et ne la mentionne pas. Interpreter ne veut pas dire deviner : chaque lien que tu fais entre deux signaux doit etre justifiable a partir des donnees fournies, pas d'une connaissance externe.
2. Interpreter un signal n'est pas la meme chose que conseiller une action : ne donne AUCUN avis directionnel ou conseil d'investissement (pas de "haussier"/"baissier", pas de recommandation d'achat/vente, pas de jugement de valeur sur la qualite du management). Decris ce qui a change, relie les signaux entre eux, mais laisse la decision au lecteur humain.
3. Si les donnees fournies sont insuffisantes pour interpreter quoi que ce soit d'utile, dis-le explicitement plutot que de combler avec des generalites ou une connaissance externe.
4. Ne repete pas mecaniquement les chiffres deja affiches ailleurs dans le rapport (revenue, resultat net) sauf si c'est necessaire pour donner du contexte a ton interpretation.
5. Reponds uniquement avec le texte de la synthese, sans titre, sans introduction du type "Voici la synthese"."""


def _fmt(value, suffix: str = "") -> str:
    return "indisponible" if value is None else f"{value}{suffix}"


_EXAMPLE_EXCERPT_MAX_CHARS = 160


def _truncate(text: str) -> str:
    if len(text) > _EXAMPLE_EXCERPT_MAX_CHARS:
        return text[:_EXAMPLE_EXCERPT_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return text


def _diff_group_lines(groups: list) -> list:
    """
    One line per DiffGroup (see diff/grouped_diff.py) that actually
    changed, with a short REAL excerpt (never a paraphrase or
    invention) so the model has concrete grounding beyond bare counts.
    """
    lines = []
    for g in groups:
        added = [seg for seg in g.diff.segments if seg.kind == "added"]
        removed = [seg for seg in g.diff.segments if seg.kind == "removed"]
        modified = [seg for seg in g.diff.segments if seg.kind == "modified"]
        if not added and not removed and not modified:
            continue
        status_label = {
            "added": "nouvelle sous-thematique",
            "removed": "sous-thematique supprimee",
            "matched": "modifiee",
        }[g.status]
        heading = g.heading or "(introduction, sans titre)"
        line = (
            f'  - "{heading}" ({status_label}) : {len(added)} ajout(s), '
            f'{len(removed)} suppression(s), {len(modified)} phrase(s) reformulee(s)'
        )
        if added or removed:
            example = _truncate((added or removed)[0].text)
            line += f'. Exemple : "{example}"'
        elif modified:
            seg = modified[0]
            line += f'. Exemple : "{_truncate(seg.text)}" -> "{_truncate(seg.replacement)}"'
        lines.append(line)
    return lines


def build_prompt_context(report: ReportData) -> str:
    """
    Builds the compact, structured fact sheet handed to the model --
    pure function, no network, fully unit-testable. This IS the
    grounding: everything the model is allowed to talk about must be
    reachable from this string.
    """
    filing = report.filing
    lines = [
        f"Societe : {filing.company_name} ({filing.ticker}), exercice {filing.fiscal_year} {filing.fiscal_period}",
    ]

    if report.altman_z.available:
        z = report.altman_z.value
        lines.append(f"Altman Z-Score : {z.score:.2f} (zone {z.zone})")
    if report.beneish_m.available:
        m = report.beneish_m.value
        lines.append(f"Beneish M-Score : {m.score:.2f} ({'signale' if m.flagged else 'non signale'})")
    if report.piotroski_f.available:
        f = report.piotroski_f.value
        lines.append(f"Piotroski F-Score : {f.score}/{f.max_score}")

    if report.risk_factors_diff.available and not report.risk_factors_diff.value.skipped:
        rf = report.risk_factors_diff.value.diff
        lines.append(
            f"Risk Factors (Item 1A) -- changement global : "
            f"{rf.overall.similarity_ratio * 100:.1f}% similaire, "
            f"{rf.overall.added_word_count} mots ajoutes, {rf.overall.removed_word_count} mots supprimes"
        )
        group_lines = _diff_group_lines(rf.groups)
        if group_lines:
            lines.append("Sous-themes avec changement :")
            lines.extend(group_lines)

    if report.mdna_diff.available:
        md = report.mdna_diff.value
        lines.append(
            f"MD&A (Item 7) -- changement : "
            f"{md.overall.added_word_count} mots ajoutes, {md.overall.removed_word_count} mots supprimes"
        )
    if report.mdna_sentiment.available:
        lines.append(f"Tonalite MD&A (Loughran-McDonald) : {report.mdna_sentiment.value.net_tone:.2f}")
    if report.risk_factors_sentiment.available and not report.risk_factors_sentiment.value.skipped:
        lines.append(
            f"Tonalite Risk Factors (Loughran-McDonald) : "
            f"{report.risk_factors_sentiment.value.result.net_tone:.2f}"
        )

    return "\n".join(lines)


class AISummaryError(Exception):
    pass


def _call_claude_api(
    prompt_context: str,
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    timeout_seconds: float,
) -> str:
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
                "max_tokens": max_tokens,
                "temperature": 0,  # minimize run-to-run drift for a factual-synthesis task
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt_context}],
            },
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise AISummaryError(f"network error calling the Claude API: {exc}") from exc

    if response.status_code != 200:
        raise AISummaryError(
            f"Claude API returned HTTP {response.status_code}: {response.text[:300]}"
        )

    try:
        payload = response.json()
        text = payload["content"][0]["text"].strip()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AISummaryError(f"unexpected Claude API response shape: {exc}") from exc

    if not text:
        raise AISummaryError("Claude API returned an empty summary.")
    return text


def attach_ai_summary(
    report: ReportData,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ReportData:
    """
    Returns a NEW ReportData with `ai_summary` populated -- the input
    `report` (and everything else about it) is untouched. Never raises:
    any failure (no key, network, bad response) comes back as an
    `unavailable` SectionResult with the reason, same as every other
    optional section in this project.
    """
    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        return dataclasses.replace(
            report,
            ai_summary=SectionResult(
                value=None,
                unavailable_reason=(
                    "no ANTHROPIC_API_KEY provided -- the AI summary is opt-in "
                    "and was not requested for this report"
                ),
            ),
        )

    resolved_model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    prompt_context = build_prompt_context(report)

    try:
        text = _call_claude_api(
            prompt_context,
            api_key=resolved_key,
            model=resolved_model,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
    except AISummaryError as exc:
        return dataclasses.replace(
            report,
            ai_summary=SectionResult(value=None, unavailable_reason=str(exc)),
        )

    return dataclasses.replace(
        report,
        ai_summary=SectionResult(value={"text": text, "model": resolved_model}, unavailable_reason=None),
    )
