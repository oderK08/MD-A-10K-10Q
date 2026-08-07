"""
Module 6 (opt-in) -- an AI-generated plain-language synthesis of the
report's ALREADY-COMPUTED sections (red flags, sentiment, the grouped
Risk Factors/MD&A diff from grouped_diff.py), via the Claude API.

Deliberately kept SEPARATE from build_report_data(), never called
automatically: unlike every other module in this project, a real call
here is billed, requires network + an API key, and is not
deterministic. A caller must explicitly opt in via `attach_ai_summary`,
handed an already-built (free, deterministic) ReportData.

Framing has moved twice, each time an explicit user request after
seeing the previous version in real use, never assumed:

1. v1 -- strict factual synthesis, zero interpretation, zero
   directional read. Chosen explicitly (via AskUserQuestion) over two
   more interpretive options.
2. v2 -- anchored interpretation: the model connects the computed
   signals into a real reading ("a Piotroski decline alongside a more
   negative MD&A tone points to..."), but still forbidden from any
   directional/bullish-bearish call.
3. v3 (current) -- after reading a real generated report, the user
   said the actual point was for the model to read what was added and
   removed, weigh it against context, and give a CONCRETE bullish/
   bearish verdict on the balance of the changes -- quoting the 2-3
   most important real sentences as evidence (their own worked example:
   a disclosed 5% DRAM price increase is "hyper bullish" given what
   that implies for revenue). This explicitly reverses the "no
   directional call" restriction from v1/v2.

What did NOT move across all three versions, restated each time rather
than silently carried over or dropped:

- GROUNDED ONLY in this report's own computed fields -- financials,
  red-flag results, per-sub-theme diff excerpts (now real quoted
  sentences, not just counts -- see `_diff_group_lines`), sentiment
  scores -- never in whatever the model happens to know about this
  specific company from training data. A model's background knowledge
  of a real company is impossible to verify or attribute to this
  specific filing, so letting it leak in would silently blend real,
  sourced analysis with unverifiable recall. GENERAL business/economic
  reasoning about what a disclosed fact implies (a price increase on a
  key product implies more revenue) is allowed and is in fact the whole
  point of v3 -- the line is reasoning FROM the provided facts, not
  importing new ones about this company from outside the report.
- NEVER an explicit buy/sell recommendation or price target. A
  bullish/bearish READ of what changed is now requested; being told to
  buy or sell a specific security is a different, heavier claim this
  tool still never makes -- that stays the human reader's call.

If the API call fails for ANY reason (no key, network, rate limit, bad
response shape), the section comes back `unavailable` with a plain
reason -- exactly like every other optional SectionResult in this
project -- so this one extra, non-essential module can never take down
report generation.
"""

from __future__ import annotations

import dataclasses
import os
import re
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
# Raised from 500 alongside the verdict-with-evidence prompt: the answer
# now carries 2-3 VERBATIM quoted filing sentences (which are long, and
# cost tokens the model can't compress without breaking the "quote
# exactly" instruction) plus a verdict and a counterpoint. At 500 the
# response risked being cut mid-quote.
DEFAULT_MAX_TOKENS = 1000
DEFAULT_TIMEOUT_SECONDS = 30.0

_SYSTEM_PROMPT = """Tu es un analyste equity chevronne, du calibre de ceux que les gerants appellent avant de bouger une ligne. Ton metier : lire ce qu'une societe a change dans la formulation de ses filings SEC d'une annee sur l'autre, reperer immediatement les elements qui devront etre verifies en priorite, et en tirer une synthese des points reellement cruciaux qui ont ete discutes dans le document.

On te donne les sous-thematiques que tu as toi-meme jugees les plus suivies par les analystes de cette societe, et pour chacune le texte reellement AJOUTE, SUPPRIME ou REFORMULE entre les deux depots. C'est LA MATIERE de ton analyse : ta synthese doit sortir de la lecture de ces passages, pas d'indicateurs calcules ailleurs.

Ta reponse, en francais, enchainee ainsi :

1. VERDICT (1 phrase). Commence directement par "Plutot bullish", "Plutot bearish", "Mitige" ou "Neutre", suivi de la raison principale. C'est la premiere chose que le lecteur doit voir, ne la noie pas.

2. CE QUI A ETE DIT, sous-thematique par sous-thematique. Pour chacune de celles qui portent un vrai changement : cite VERBATIM entre guillemets le passage qui compte, et explique ce qu'il implique economiquement. Ne cite que du texte reellement present dans les donnees fournies, jamais une phrase que tu reconstruis ou resumes en la presentant comme une citation. Une phrase SUPPRIMEE compte autant qu'une ajoutee : une societe qui retire un risque qu'elle mentionnait l'an dernier envoie un signal, dis-le. Si une sous-thematique retenue n'a en fait pas bouge, c'est une information en soi, dis-le au lieu de la passer sous silence.

3. A VERIFIER EN PRIORITE (2 a 4 points). C'est la valeur ajoutee attendue d'un analyste : quelles donnees precises faudra-t-il aller chercher pour trancher ce que le texte laisse ouvert. Sois concret (une ligne du compte de resultat, un volume, un prix moyen, une echeance contractuelle, une decision reglementaire attendue), pas generique.

4. NUANCE (1 a 2 phrases). Ce qui va dans le sens inverse de ton verdict, ou ce qui manque pour trancher. Un verdict sans contrepartie honnete n'a pas de valeur.

Comment peser un passage : raisonne sur ce qu'il IMPLIQUE economiquement, ne te contente pas de la tonalite des mots.
- Un fait chiffre concret pese plus qu'une formule prudente generique. Exemple : "we expect DRAM prices to increase approximately 5%" est fortement bullish (hausse de prix sur un produit cle, donc revenu et marge en hausse), meme si la phrase ne contient aucun mot "positif" au sens d'un dictionnaire.
- A l'inverse, un risque qui passe d'hypothetique a realise est fortement bearish : "could adversely affect" devenu "has adversely affected", ou l'ajout d'un client, fournisseur ou pays nommement cite comme perdu ou restreint.
- Le boilerplate juridique qui bouge sans changer de fond ne vaut pas un verdict : ignore-le, ou dis explicitement que c'est cosmetique.

Regles strictes, non negociables :
1. N'utilise QUE les informations fournies ci-dessous. N'utilise JAMAIS une connaissance que tu aurais sur CETTE societe par ailleurs (autres filings, actualites, resultats trimestriels, cours de bourse, memoire d'entrainement). Si un fait n'est pas dans le texte fourni, ne l'invente pas et ne le mentionne pas. En revanche, raisonner economiquement sur ce qu'un fait fourni implique est exactement ce qu'on te demande : la limite est de ne pas importer de faits nouveaux sur cette societe, pas de t'interdire de reflechir.
2. Ton verdict porte sur la BALANCE DES CHANGEMENTS de ce depot, pas sur la valorisation du titre. Ne donne JAMAIS de recommandation d'achat ou de vente, ni d'objectif de cours, ni de conseil d'allocation.
3. Si les changements fournis sont trop minces ou trop cosmetiques pour trancher, dis "Neutre" et explique pourquoi, plutot que de forcer un verdict sur du bruit.
4. N'ECRIS AUCUN TIRET dans ta reponse. Ni tiret cadratin, ni tiret demi-cadratin, ni double tiret, ni tiret utilise comme ponctuation ou pour introduire une incise. Utilise une virgule, un deux-points, une parenthese ou une phrase separee. Aucune liste a puces non plus.
5. Reponds uniquement avec le texte de l'analyse, sans titre, sans introduction du type "Voici l'analyse", sans numerotation apparente des parties. Enchaine en paragraphes de prose continue, comme une note d'analyste ecrite a la main.
6. Ecris sobrement, dans le registre d'une note interne : pas de formule d'accroche, pas de conclusion qui resume ce qui vient d'etre dit, pas d'adverbe d'insistance empile ("particulierement significatif", "notablement", "il est important de noter que"). Va au fait.
7. Longueur : entre 650 et 780 mots, citations comprises. Ta reponse occupe une page entiere du rapport et doit la remplir, donc developpe reellement chaque sous-thematique retenue au lieu d'expedier en une ligne. En revanche n'etire jamais artificiellement un contenu mince : s'il y a vraiment peu de matiere dans les passages fournis, dis-le franchement et arrete-toi, une page a moitie vide vaut mieux qu'un remplissage."""


def _fmt(value, suffix: str = "") -> str:
    return "indisponible" if value is None else f"{value}{suffix}"


# Long enough to carry a whole real filing sentence intact. The prompt
# asks the model to QUOTE sentences verbatim as evidence for its verdict,
# so an excerpt truncated mid-sentence is actively harmful here: it would
# either be quoted incomplete, or push the model to reconstruct the
# missing end (i.e. invent it). Raised from 160 for exactly that reason.
_EXCERPT_MAX_CHARS = 400

# How many changed sentences to send per section. The model is asked to
# pick the 2-3 most meaningful, so it needs a real pool to choose from --
# but the whole diff of a 40k-word MD&A would be both expensive and
# mostly boilerplate. These caps keep a typical prompt in the low
# thousands of tokens (a fraction of a cent on Haiku) while still
# covering every sentence that scores as materially informative.
_MAX_EXCERPTS_PER_SECTION = 14
_MAX_EXCERPTS_PER_GROUP = 3

# Matches a quantified MAGNITUDE, not just any digit. A bare "\d" was
# the first version and ranked badly on real text: a plain year ("fiscal
# 2026"), a section number or a list index all contain digits without
# carrying any magnitude, so everything scored equal and the tiebreak
# (length) handed the budget to the longest boilerplate. Matches instead:
# a percentage, a currency amount, a scaled figure (million/billion),
# basis points, or a multiplier -- the shapes a material disclosure
# actually takes ("increase approximately 5%", "$1.2 billion", "150
# basis points").
_QUANTIFIED_FACT_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|percent|percentage points?|basis points?|bps|x\b)"
    r"|[$€£]\s*\d"
    r"|\d+(?:[.,]\d+)?\s*(?:million|billion|trillion|thousand)",
    re.IGNORECASE,
)


def _truncate(text: str) -> str:
    if len(text) > _EXCERPT_MAX_CHARS:
        return text[:_EXCERPT_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return text


def _excerpt_priority(text: str) -> tuple:
    """
    Ranks a changed sentence by how likely it is to actually move a
    bullish/bearish read, so the capped excerpt budget goes to the
    sentences worth quoting rather than the first N in document order.

    A sentence carrying a NUMBER outranks one that doesn't: the user's
    own worked example for this feature ("we expect DRAM prices to
    increase approximately 5%") is exactly that shape -- a concrete
    quantified disclosure, decision-relevant precisely because it's
    specific, and easy to lose among pages of unquantified boilerplate
    ("could adversely affect", "we may be unable to..."). Length breaks
    ties as a rough stand-in for substance, the same proxy this project
    already uses to rank sub-themes in the renderer.

    Deliberately a heuristic for BUDGETING what to send, not a judgment:
    it decides which real sentences the model gets to see, never what
    the verdict should be. A wrong ranking costs the model a candidate
    quote; it can't fabricate one.
    """
    return (bool(_QUANTIFIED_FACT_RE.search(text)), len(text.split()))


def _segment_excerpt_lines(segments: list, limit: int) -> list:
    """
    Real, verbatim changed sentences from a diff, highest-priority
    first, labeled by direction (added / removed / reworded). Removals
    are labeled explicitly and kept in the same pool as additions: a
    company dropping a risk it disclosed last year is a signal in its
    own right, and the prompt tells the model to weigh it as one.
    """
    candidates = []
    for seg in segments:
        if seg.kind == "added":
            candidates.append((_excerpt_priority(seg.text), f'    + AJOUTE : "{_truncate(seg.text)}"'))
        elif seg.kind == "removed":
            candidates.append((_excerpt_priority(seg.text), f'    - SUPPRIME : "{_truncate(seg.text)}"'))
        elif seg.kind == "modified":
            priority = max(_excerpt_priority(seg.text), _excerpt_priority(seg.replacement))
            candidates.append((
                priority,
                f'    ~ REFORMULE : "{_truncate(seg.text)}" -> "{_truncate(seg.replacement)}"',
            ))
    candidates.sort(key=lambda c: c[0], reverse=True)
    return [line for _priority, line in candidates[:limit]]


def _diff_group_lines(groups: list) -> list:
    """
    Per changed DiffGroup (see diff/grouped_diff.py): its heading, its
    status, its counts, and its most informative REAL changed sentences
    (never a paraphrase or invention) -- the concrete evidence the model
    is asked to quote from.

    Groups are themselves ranked by total changed word count (the same
    proxy the renderer uses to pick which sub-themes to show in full),
    so when the excerpt budget runs out it runs out on the least-changed
    sub-themes, not on whichever happened to come last in the document.
    """
    changed = []
    for g in groups:
        added = sum(1 for s in g.diff.segments if s.kind == "added")
        removed = sum(1 for s in g.diff.segments if s.kind == "removed")
        modified = sum(1 for s in g.diff.segments if s.kind == "modified")
        if not added and not removed and not modified:
            continue
        weight = g.diff.added_word_count + g.diff.removed_word_count
        changed.append((weight, g, added, removed, modified))
    changed.sort(key=lambda c: c[0], reverse=True)

    lines = []
    budget = _MAX_EXCERPTS_PER_SECTION
    for _weight, g, added, removed, modified in changed:
        status_label = {
            "added": "nouvelle sous-thematique",
            "removed": "sous-thematique supprimee",
            "matched": "modifiee",
        }[g.status]
        heading = g.heading or "(introduction, sans titre)"
        lines.append(
            f'  - "{heading}" ({status_label}) : {added} ajout(s), '
            f'{removed} suppression(s), {modified} phrase(s) reformulee(s)'
        )
        if budget > 0:
            excerpts = _segment_excerpt_lines(
                g.diff.segments, min(_MAX_EXCERPTS_PER_GROUP, budget)
            )
            lines.extend(excerpts)
            budget -= len(excerpts)
    return lines


def build_prompt_context(report: ReportData) -> str:
    """
    Builds the structured fact sheet handed to the model -- pure
    function, no network, fully unit-testable. This IS the grounding:
    everything the model is allowed to talk about must be reachable from
    this string, so anything the prompt asks it to do must actually be
    supported by what's built here.

    The MD&A used to be sent as word counts ONLY, with no actual text.
    That made the current prompt's core instruction -- quote the
    meaningful changed sentences as evidence -- literally impossible to
    satisfy for the section where concrete guidance normally lives
    (pricing, margins, demand commentary): the model could not quote
    what it was never sent, and the resulting summaries talked only
    about counts and sentiment scores. Both sections now send real,
    verbatim changed sentences.

    The computed indicators (Altman, Beneish, Piotroski, Loughran-
    McDonald tone) are deliberately NOT sent, at the user's explicit
    request: the summary must come from reading the selected
    sub-themes, not from restating ratios the reader already has on
    page 2. Withholding them is a stronger guarantee than instructing
    the model to ignore them -- it cannot lean on what it never
    receives. They remain in the report itself, just not in this
    prompt.
    """
    filing = report.filing
    lines = [
        f"Societe : {filing.company_name} ({filing.ticker}), exercice {filing.fiscal_year} {filing.fiscal_period}",
    ]

    if report.risk_factors_diff.available and not report.risk_factors_diff.value.skipped:
        rf = report.risk_factors_diff.value.diff
        lines.append(
            f"Risk Factors (Item 1A) -- changement global : "
            f"{rf.overall.similarity_ratio * 100:.1f}% similaire, "
            f"{rf.overall.added_word_count} mots ajoutes, {rf.overall.removed_word_count} mots supprimes"
        )
        # Only the sub-themes selected upstream as analyst-relevant (see
        # report/theme_selection.py). `selected_groups` returns them
        # ranked most-important-first, and falls back to every group in
        # document order when no selection was ever applied -- so this
        # stays correct whether or not the selection pass ran.
        selected = rf.selected_groups
        unselected_changed = sum(
            1
            for g in rf.groups
            if not g.selected
            and any(s.kind in ("added", "removed", "modified") for s in g.diff.segments)
        )
        group_lines = _diff_group_lines(selected)
        if group_lines:
            lines.append(
                "Sous-thematiques retenues comme les plus suivies par les analystes, "
                "par ordre d'importance (avec les phrases reellement modifiees) :"
            )
            lines.extend(group_lines)
        if unselected_changed:
            # Stated rather than hidden: the model must know its view is
            # partial, so it can't imply it reviewed the whole section.
            lines.append(
                f"({unselected_changed} autre(s) sous-thematique(s) ont aussi change mais "
                f"n'ont pas ete retenues comme prioritaires. Ne te prononce pas dessus.)"
            )

    if report.mdna_diff.available:
        md = report.mdna_diff.value
        lines.append(
            f"MD&A (Item 7) -- changement : "
            f"{md.overall.similarity_ratio * 100:.1f}% similaire, "
            f"{md.overall.added_word_count} mots ajoutes, {md.overall.removed_word_count} mots supprimes"
        )
        mdna_excerpts = _segment_excerpt_lines(
            md.overall.segments, _MAX_EXCERPTS_PER_SECTION
        )
        if mdna_excerpts:
            lines.append("Phrases reellement modifiees dans le MD&A :")
            lines.extend(mdna_excerpts)

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
