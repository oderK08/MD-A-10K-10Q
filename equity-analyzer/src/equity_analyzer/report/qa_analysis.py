"""
The question and answer session, read on its own and returned as data.

WHY A SECOND PASS AT ALL. The transcript already arrives split, because
prepared remarks and Q&A are different acts (see transcript_source), and
until now the Q&A half was used for one tone score and nothing else.
That wastes the best material in the call: the prepared remarks are
written, lawyered and rehearsed, so they say what management chose to
say, while the Q&A is the only place it is asked questions it did not
choose. What gets dodged there, and what slips out around the edges, is
not in the press release and not anywhere else.

WHY JSON RATHER THAN PROSE. Page 1's reading is prose because a reader
reads it. This is different: "which analyst asked what, what was
actually given back, how bad is the gap" is table shaped, and a model
asked for prose flattens it into a paragraph that reads well and cannot
be scanned. Structured output lets the report lay it out as what it is.

FOUR THINGS WERE ADAPTED from the prompt as first drafted:

  THE TRANSCRIPTION WARNING IS CONDITIONAL. The draft asserted the
  transcript was machine generated. In this project that is a property
  of the source, not a constant: a provider transcript is the company's
  own written record, and telling the model to doubt its numbers would
  make it hedge on figures that are exactly right. The warning now
  appears only when `verbatim` is False, the same way page 1's does.

  NO EXTERNAL FACTS. The project's standing rule, applied here too:
  reason economically about what is in front of you, import nothing
  about the company from anywhere else.

  THE PERIOD IS A CROSS CHECK, NOT DATA. The pipeline already knows the
  quarter authoritatively, from EDGAR. Asking the model what period it
  thinks it read is still worth doing, as a third independent check on
  the pairing, but its answer never overrides what EDGAR said.

  THE DASH RULE uses this project's wording, so the two prompts cannot
  drift into forbidding subtly different things.
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

# Roomier than page 1's budget: this answer is a list of findings whose
# length is a property of the call, not of a page, and a Q&A that dodged
# six questions should return six.
#
# THE CEILING COVERS THINKING PLUS RESPONSE, not the response alone, and
# that is what killed two real runs. Claude 5 family models emit a
# thinking block before writing, those tokens count against
# `max_tokens`, and on an eight thousand word call the thinking alone
# can exhaust a budget sized for the answer. The TSLA run came back with
# `stop_reason: max_tokens` and a single `thinking` block: the model had
# reasoned until the budget ran out without ever writing a line, after
# the transcript had been fetched and paid for.
#
# Raising the ceiling costs nothing. Billing is on tokens ACTUALLY
# produced, not on the ceiling, and the thinking happens either way: a
# generous ceiling only leaves room to write down what it prepared.
MAX_TOKENS = 12000

_MACHINE_TRANSCRIPT_RULE = """
La transcription est automatique : les noms propres et certains chiffres peuvent
etre errones. Signale le quand un chiffre te parait douteux plutot que de le
reprendre, et liste ces chiffres dans "uncertain_figures".
"""

_VERBATIM_RULE = """
Ce transcript est le compte rendu ecrit officiel, pas une transcription audio.
Les noms et les chiffres sont fiables : ne les mets pas en doute et laisse
"uncertain_figures" vide.
"""

_SCHEMA = """{
  "period": "le trimestre tel que la societe le nomme dans le call",
  "dodged_questions": [
    {"analyst": "...", "question": "reformulation courte",
     "what_was_asked": "l'information precise demandee",
     "what_was_given": "ce qui a reellement ete repondu",
     "severity": "high|medium|low"}
  ],
  "concessions": [
    {"topic": "...", "admission": "ce que le management concede",
     "verbatim": "citation courte"}
  ],
  "implicit_guidance": [
    {"topic": "...", "signal": "l'information a valeur prospective",
     "buried_in": "contexte ou c'etait glisse",
     "direction": "positive|negative|neutral"}
  ],
  "recurring_themes": [
    {"theme": "...", "analyst_count": 3, "summary": "..."}
  ],
  "tone_shift_markers": ["formulations notables, verbatim court"],
  "uncertain_figures": ["chiffres probablement mal transcrits"]
}"""


def system_prompt(verbatim: bool = True) -> str:
    """
    Built at call time because one rule depends on where the transcript
    came from. See the module docstring.
    """
    source_rule = _VERBATIM_RULE if verbatim else _MACHINE_TRANSCRIPT_RULE
    return f"""Tu analyses la session questions-reponses d'un earnings call.
{source_rule}
Reponds UNIQUEMENT avec un objet JSON valide, sans texte ni balises autour.

{_SCHEMA}

Regles :
- severity high : une information chiffree precise a ete demandee et refusee.
- implicit_guidance : cherche ce qui a une valeur prospective mais n'etait pas
  dans le communique de presse. C'est la partie la plus utile.
- Citations verbatim sous 15 mots.
- N'utilise jamais le tiret cadratin ni le tiret demi cadratin, dans aucun champ.
  Virgule, deux points ou parenthese.
- Tu n'importes AUCUN fait exterieur sur cette societe : ni cours de bourse, ni
  actualite, ni autre publication, ni souvenir d'entrainement. Ta matiere est
  cette session et rien d'autre. Raisonner economiquement sur ce qu'elle contient
  est en revanche attendu de toi.
- N'invente rien. Listes vides si rien a signaler."""


@dataclass(frozen=True)
class QaAnalysis:
    """The model's reading of the Q&A, as data the report can lay out."""

    ticker: str
    quarter: str
    model: str
    # What the model thinks it read. NEVER used as the report's quarter:
    # EDGAR already answered that. Kept as a third independent check on
    # the pairing, next to verify_against_declared.
    declared_period: Optional[str] = None
    dodged_questions: list = field(default_factory=list)
    concessions: list = field(default_factory=list)
    implicit_guidance: list = field(default_factory=list)
    recurring_themes: list = field(default_factory=list)
    tone_shift_markers: list = field(default_factory=list)
    uncertain_figures: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """
        True when the session yielded nothing on any axis.

        Worth knowing rather than rendering six empty headings: a Q&A
        where nothing was dodged and nothing slipped is itself a fact,
        and it reads better as one sentence than as an empty form.
        """
        return not any((
            self.dodged_questions, self.concessions, self.implicit_guidance,
            self.recurring_themes, self.tone_shift_markers,
        ))

    @property
    def hard_dodges(self) -> list:
        """The ones where a precise figure was asked for and refused."""
        return [d for d in self.dodged_questions
                if str(d.get("severity", "")).lower() == "high"]


# A model told to answer in bare JSON still wraps it in a fence often
# enough that not handling it would throw away a good answer, and the
# transcript is already fetched and paid for by then.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_response(text: str) -> dict:
    """
    The JSON object out of whatever the model returned.

    Tolerant about the wrapper, strict about the content: a fence or a
    stray sentence around the object is recovered, but anything that is
    not a JSON object raises, because a partially understood answer
    rendered as findings is worse than no section at all.
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


def _entries(payload: dict, key: str) -> list:
    """
    One list of dict entries, with anything malformed dropped.

    Dropped rather than raising: one bad entry in a list of six should
    cost that entry, not the whole section. Dropped rather than kept,
    because an entry the renderer cannot read becomes a blank row, and a
    blank row reads as a finding with nothing in it.
    """
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and any(item.values())]


def _strings(payload: dict, key: str) -> list:
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def build_prompt(ticker: str, quarter: str, qa_text: str,
                 company_name: Optional[str] = None) -> str:
    """The Q&A alone, whole. The prepared remarks are not this pass's job."""
    who = company_name or ticker
    return (
        f"Session questions-reponses de {who} ({ticker}), trimestre {quarter}.\n\n"
        f"---DEBUT DE LA SESSION---\n{qa_text}\n---FIN DE LA SESSION---"
    )


def analyse_qa(
    ticker: str,
    quarter: str,
    qa_text: str,
    *,
    api_key: str,
    company_name: Optional[str] = None,
    verbatim: bool = True,
    model: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> QaAnalysis:
    """
    Reads the Q&A and returns it as data. Raises ClaudeError on anything
    that would leave the caller with a half understood answer.

    Unlike page 1's reading, a failure here is NOT fatal to the report:
    the caller treats this as an optional companion, so the main two
    pages still come out. See scripts/rapport.py.
    """
    if not (qa_text or "").strip():
        raise ClaudeError("session questions-reponses vide : rien a analyser")

    payload = parse_response(call_claude(
        build_prompt(ticker, quarter, qa_text, company_name),
        api_key=api_key,
        system_prompt=system_prompt(verbatim),
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    ))

    declared = payload.get("period")
    return QaAnalysis(
        ticker=ticker,
        quarter=quarter,
        model=model,
        declared_period=str(declared).strip() if declared else None,
        dodged_questions=_entries(payload, "dodged_questions"),
        concessions=_entries(payload, "concessions"),
        implicit_guidance=_entries(payload, "implicit_guidance"),
        recurring_themes=_entries(payload, "recurring_themes"),
        tone_shift_markers=_strings(payload, "tone_shift_markers"),
        uncertain_figures=_strings(payload, "uncertain_figures"),
    )


__all__ = [
    "MAX_TOKENS",
    "QaAnalysis",
    "analyse_qa",
    "build_prompt",
    "parse_response",
    "system_prompt",
]
