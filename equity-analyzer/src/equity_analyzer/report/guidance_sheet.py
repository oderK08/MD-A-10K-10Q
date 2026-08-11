"""
What the company committed to LAST quarter, as a baseline for reading
this one.

WHY THIS EXISTS. The reading was measured against exactly one
expectation: the EPS consensus. Everything else management says, the
capex envelope, the revenue guide, the margin target, the product
calendar, arrived with no reference point, so the model could report a
LEVEL and never a CHANGE.

That gap was found on a real MSFT report. The reading carried the capex
twice and wrote "desormais ajuste a approximately $175 billion" without
ever saying adjusted from what. A reader looking for the one number that
moved found a number, correctly quoted, that did not tell them it had
moved. A raised capex programme is often the single most consequential
thing in a call, and it was landing as a fact among facts.

WHY A SEPARATE PASS RATHER THAN JUST APPENDING THE OLD TRANSCRIPT. Two
reasons, and the second is the one that decided it.

  Handing the model two full calls doubles the input and asks it to do
  two jobs at once, hunting last quarter's numbers while writing this
  quarter's reading. The commitments are a handful of figures; sending
  eight thousand words to deliver them is the wrong shape.

  And extraction is a DIFFERENT KIND OF WORK from reading. It has one
  right answer, it is checkable against the text, and it needs no
  judgement. Keeping it apart means the reading prompt receives facts
  rather than a haystack, and the facts are the same facts every time
  regardless of what the reading happens to be looking at.

WHAT IT DELIBERATELY DOES NOT DO. It does not interpret, rank, or
comment. A commitment is a metric, a value, the period it covers and the
sentence it was said in. Whether a change matters is the reading's job,
one layer up, where the current quarter is also in view.

THE COMPARISON IS STILL NOT GUARANTEED. A company that never restated a
figure leaves nothing to compare, and the sheet then says so rather than
inventing a baseline. Same discipline as the consensus: an absence is
printed, never silently filled in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .claude_client import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    ClaudeError,
    call_claude,
)

# Smaller than the reading's and the Q&A's budgets, because the answer
# is a short list of figures rather than prose. Still generous next to
# the answer itself: Claude 5 emits a thinking block before writing and
# those tokens count against this ceiling, which killed two real runs
# when it was learned the hard way (see qa_analysis.MAX_TOKENS).
MAX_TOKENS = 4000

_MACHINE_TRANSCRIPT_RULE = """
La transcription est automatique : certains chiffres peuvent etre errones. Ne
retiens un engagement que si le chiffre est clairement lisible dans la phrase.
"""

_VERBATIM_RULE = """
Ce transcript est le compte rendu ecrit officiel. Les chiffres sont fiables.
"""

_SCHEMA = """{
  "commitments": [
    {"metric": "ce sur quoi porte l'engagement, par exemple capex, marge brute",
     "value": "la valeur telle qu'annoncee, avec son unite",
     "period": "la periode couverte, par exemple T1, exercice 2026, annee civile 2026",
     "verbatim": "la phrase exacte, sous 25 mots"}
  ]
}"""

_RULES = """Regles :
- Tu retiens ce qui ENGAGE L'AVENIR et porte un chiffre : guidance de revenu, de
  marge, capex, taux de croissance attendu, calendrier produit date, prix
  annonces. Une performance passee n'est pas un engagement.
- Tu ne retiens rien sans chiffre. "Nous restons confiants" n'est pas un
  engagement, "une marge autour de 20%" en est un.
- La valeur est reprise telle quelle, avec son unite et sa forme (fourchette,
  "plus de", "environ"). Tu ne convertis pas, tu n'arrondis pas, tu ne calcules
  rien.
- Tu n'interpretes pas et tu ne commentes pas. Ni consequence, ni jugement, ni
  classement par importance. Cette liste est de la matiere premiere.
- Tu n'importes AUCUN fait exterieur : ni autre publication, ni actualite, ni
  souvenir d'entrainement. Ta matiere est ce transcript et rien d'autre.
- N'utilise jamais le tiret cadratin ni le tiret demi cadratin, dans aucun champ.
- N'invente rien. Liste vide si le call ne contient aucun engagement chiffre."""


def system_prompt(verbatim: bool = True) -> str:
    """
    Built at call time because one rule depends on the source.

    Assembled by concatenation rather than `str.format`, because the
    schema is itself full of braces and would have to be escaped to
    survive a formatting pass. Escaping a prompt to protect a templating
    mechanism is how a prompt quietly stops saying what it reads as.
    """
    source_rule = _VERBATIM_RULE if verbatim else _MACHINE_TRANSCRIPT_RULE
    return (
        "Tu extrais d'un earnings call les engagements CHIFFRES et tournes vers "
        "l'avenir, pour servir de base de comparaison au trimestre suivant.\n"
        f"{source_rule}\n"
        "Reponds UNIQUEMENT avec un objet JSON valide, sans texte ni balises "
        f"autour.\n\n{_SCHEMA}\n\n{_RULES}"
    )


@dataclass(frozen=True)
class GuidanceSheet:
    """The quantified commitments of one call, as a comparison baseline."""

    ticker: str
    quarter: str
    model: str
    commitments: list = field(default_factory=list)
    # How many quarters before the call being read this baseline is. 1
    # is the normal case, the quarter immediately before. Anything above
    # 1 means a call in between could not be had, and it CHANGES WHAT A
    # DIFFERENCE MEANS: see as_prompt_block.
    quarters_before: int = 1

    @property
    def is_empty(self) -> bool:
        return not self.commitments

    @property
    def is_adjacent(self) -> bool:
        """True when this is the quarter immediately before the one read."""
        return self.quarters_before <= 1


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_response(text: str) -> dict:
    """
    The JSON object out of whatever came back.

    Tolerant about a fence or a stray sentence around the object, strict
    about the content: see qa_analysis.parse_response, same reasoning.
    """
    stripped = _FENCE_RE.sub("", (text or "").strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ClaudeError(
            f"la reponse ne contient pas d'objet JSON : {stripped[:200]!r}"
        )
    try:
        payload = json.loads(stripped[start:end + 1])
    except ValueError as exc:
        raise ClaudeError(f"JSON invalide dans la reponse : {exc}") from exc
    if not isinstance(payload, dict):
        raise ClaudeError("la reponse JSON n'est pas un objet")
    return payload


def _commitments(payload: dict) -> list:
    """
    The entries that carry both a metric and a value.

    Anything missing one of the two is dropped rather than kept: an
    entry without a figure is exactly what the prompt was told not to
    return, and letting it through would put a baseline row with nothing
    to compare in front of the reading.
    """
    raw = payload.get("commitments")
    if not isinstance(raw, list):
        return []
    kept = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        metric = str(item.get("metric", "")).strip()
        value = str(item.get("value", "")).strip()
        if metric and value:
            kept.append({
                "metric": metric,
                "value": value,
                "period": str(item.get("period", "")).strip(),
                "verbatim": str(item.get("verbatim", "")).strip(),
            })
    return kept


def build_prompt(ticker: str, quarter: str, transcript_text: str,
                 company_name: Optional[str] = None) -> str:
    who = company_name or ticker
    return (
        f"Earnings call de {who} ({ticker}), trimestre {quarter}.\n\n"
        f"---DEBUT DU TRANSCRIPT---\n{transcript_text}\n---FIN DU TRANSCRIPT---"
    )


def extract_guidance(
    ticker: str,
    quarter: str,
    transcript_text: str,
    *,
    api_key: str,
    company_name: Optional[str] = None,
    verbatim: bool = True,
    quarters_before: int = 1,
    model: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> GuidanceSheet:
    """
    Reads one call and returns its quantified commitments.

    Raises ClaudeError on anything that would leave a half understood
    answer. The caller treats a failure as a missing baseline, not as a
    failed report: see scripts/rapport.py.
    """
    if not (transcript_text or "").strip():
        raise ClaudeError("transcript vide : rien a extraire")

    payload = parse_response(call_claude(
        build_prompt(ticker, quarter, transcript_text, company_name),
        api_key=api_key,
        system_prompt=system_prompt(verbatim),
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    ))
    return GuidanceSheet(
        ticker=ticker,
        quarter=quarter,
        model=model,
        commitments=_commitments(payload),
        quarters_before=quarters_before,
    )


def as_prompt_block(sheet: Optional[GuidanceSheet], reason: str = "") -> str:
    """
    The baseline as the reading prompt sees it, or an explicit statement
    that there is none.

    NEVER SILENTLY OMITTED, for the reason expectations_block gives: a
    model handed no baseline does not conclude the baseline is unknown,
    it reaches for whatever it remembers about the company, and
    "en hausse par rapport au trimestre precedent" written from memory
    is indistinguishable on the page from one written from the text.
    """
    if sheet is None or sheet.is_empty:
        detail = f" ({reason})" if reason else ""
        return (
            "ENGAGEMENTS DU TRIMESTRE PRECEDENT : NON DISPONIBLES"
            f"{detail}.\n"
            "Tu n'as aucune base de comparaison sur la guidance. Tu peux toujours "
            "signaler une revision SI LA DIRECTION LA CHIFFRE ELLE MEME dans ce "
            "call, mais tu n'affirmes aucune hausse ni aucune baisse par rapport "
            "au trimestre precedent : tu n'as pas le chiffre precedent."
        )

    if sheet.is_adjacent:
        header = (
            f"ENGAGEMENTS CHIFFRES PRIS AU TRIMESTRE PRECEDENT ({sheet.quarter}), "
            "extraits de ce call la :"
        )
    else:
        header = (
            f"ENGAGEMENTS CHIFFRES PRIS IL Y A {sheet.quarters_before} TRIMESTRES "
            f"({sheet.quarter}), extraits de ce call la :"
        )

    lines = [header]
    for item in sheet.commitments:
        # READ WITH `.get`, because these do not all come from
        # `_commitments` any more. A sheet can be rebuilt from the
        # history, whose records are plain JSON meant to be readable and
        # editable on github.com, so a hand written entry missing a key
        # is a normal event rather than a corrupt one. Indexing would
        # turn that into a crash in the middle of a paid run.
        period = f" [{item.get('period')}]" if item.get("period") else ""
        lines.append(f"  {item.get('metric', '')}{period} : {item.get('value', '')}")
    lines.append("")
    lines.append(
        "Compare ce que la direction annonce AUJOURD'HUI a cette liste. Un chiffre "
        "qui a bouge est une information de premier ordre : dis l'ancien, le "
        "nouveau et l'ampleur. Un chiffre reconduit a l'identique se mentionne "
        "brievement, voire pas du tout. Cette liste est une base de comparaison, "
        "pas une matiere a analyser : ne commente pas le trimestre precedent."
    )
    if not sheet.is_adjacent:
        # THE GUARD THAT MAKES WALKING BACK HONEST. Between this
        # baseline and the call being read there are calls nobody could
        # get. A figure that moved in one of them and was merely
        # restated today would look like today's news, which is a wrong
        # answer stated confidently, and that is worse than no baseline.
        lines.append(
            f"ATTENTION : ce ne sont PAS les engagements du trimestre precedent. "
            f"{sheet.quarters_before - 1} call(s) n'ont pas pu etre recuperes entre "
            f"les deux. Un ecart avec cette liste s'est donc produit QUELQUE PART "
            f"sur {sheet.quarters_before} trimestres, et pas forcement dans le call "
            f"que tu lis. Ne presente jamais une difference comme l'annonce du jour "
            f"si la direction ne la presente pas elle meme comme un changement : "
            f"dis que la comparaison porte sur {sheet.quarters_before} trimestres."
        )
    return "\n".join(lines)


__all__ = [
    "GuidanceSheet",
    "MAX_TOKENS",
    "as_prompt_block",
    "build_prompt",
    "extract_guidance",
    "parse_response",
    "system_prompt",
]
