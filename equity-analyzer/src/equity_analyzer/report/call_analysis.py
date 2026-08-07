"""
Reading an earnings call the way an analyst does: what was said that
matters, and what to watch next.

Deliberately NOT a diff. The rest of this project compares a filing
against the previous quarter's, because filings are largely copy-pasted
and the edits are the signal. An earnings call is not written that way:
the first live comparison of two Microsoft calls came back at 7%
sentence overlap. Management writes a fresh script every quarter, so a
diff reports that almost everything changed -- true, and worthless.

What is worth doing instead is reading it. A call is 8,000 words of
prepared narrative and unscripted answers, and the useful output is the
handful of points that move a thesis: what management committed to, what
they hedged, what an analyst pushed on and did not get answered.

TWO PROPERTIES THIS MUST HAVE, both of which come from the prompt rather
than from code:

  IT QUOTES. The transcript is a real verbatim record, not a machine
  transcription, so quoting is legitimate and it is the only way a
  reader can check the model's reading against the source. Every claim
  carries the sentence it rests on.

  IT SEPARATES SAID FROM MEANT. "Revenue grew 22%" is a quote. "Growth
  is decelerating" is an inference. A summary that blends them is worse
  than useless for advisory work, because the reader cannot tell which
  part to verify.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .ai_summary import (
    AISummaryError,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    _call_claude_api,
)

# A call runs 6,000-12,000 words, comfortably inside any current model's
# context, so the transcript goes in whole. Excerpting it would mean
# choosing what matters before the model has read it -- which is the
# job being delegated.
MAX_TOKENS = 2000

_SYSTEM_PROMPT = """Tu es un analyste equity chevronne. Tu viens d'ecouter un earnings call et tu dois en rendre compte a un gerant qui n'a pas eu le temps de l'ecouter et qui doit decider s'il bouge sa ligne.

REGLES ABSOLUES

1. Tu ne cites que des phrases REELLEMENT presentes dans le transcript, mot pour mot, entre guillemets. C'est un verbatim officiel : la citation exacte est verifiable et c'est ce qui rend ton analyse utilisable. Si tu ne peux pas citer, tu n'affirmes pas.

2. Tu separes toujours ce qui est DIT de ce que tu en DEDUIS. Une citation est un fait ; ton interpretation est une lecture. Un gerant doit pouvoir distinguer d'un coup d'oeil ce qu'il doit verifier de ce qu'il doit soupeser.

3. Tu ne remplis pas. Si le call ne dit rien d'interessant sur un point, tu ecris qu'il n'en dit rien. Un rapport court et vrai vaut mieux qu'un rapport complet et delaye.

4. Tu ne repetes pas les chiffres du communique. Le gerant les a deja. Ce qui t'interesse, c'est ce qui n'est PAS dans les chiffres : le ton, les engagements, les esquives, ce que la direction a choisi de mettre en avant et ce qu'elle a laisse de cote.

CE QUE TU PRODUIS, dans cet ordre

## L'essentiel
Trois a cinq points maximum. Ce qu'un gerant doit retenir s'il ne lit que ca. Chacun appuye sur une citation.

## Ce que la direction s'engage a faire
Les engagements chiffres et datables : guidance, marges visees, capex, calendrier produit. Citation exacte a chaque fois. Si la guidance a change de formulation par rapport a ce qui etait dit avant, dis-le.

## Les esquives
Les questions d'analystes ou la reponse ne repond pas. C'est souvent l'endroit le plus informatif d'un call. Cite la question, puis la reponse, puis dis en une phrase ce qui manque. S'il n'y a pas d'esquive nette, ecris-le.

## Ce qu'il faut surveiller au prochain trimestre
Les points precis qui trancheront. Formule-les comme des questions verifiables, pas comme des generalites.

STYLE
Francais. Direct, sans jargon inutile, sans formules de politesse. Tu ecris pour quelqu'un de presse et competent. Pas de conclusion qui resume ce que tu viens d'ecrire."""


@dataclass(frozen=True)
class CallAnalysis:
    """The model's reading, with the provenance needed to check it."""
    ticker: str
    quarter: str
    text: str
    model: str
    transcript_words: int


def build_prompt(ticker: str, quarter: str, transcript_text: str,
                 company_name: Optional[str] = None) -> str:
    """
    The transcript, whole, with just enough framing to anchor it.

    Nothing is summarised or pre-selected on the way in. Trimming the
    call before the model reads it would mean deciding what matters
    first, which is exactly the judgement being asked for.
    """
    who = company_name or ticker
    return (
        f"Earnings call de {who} ({ticker}), trimestre {quarter}.\n"
        f"Transcript integral ci-dessous, tel que publie.\n\n"
        f"---DEBUT DU TRANSCRIPT---\n{transcript_text}\n---FIN DU TRANSCRIPT---"
    )


def analyse_call(
    ticker: str,
    quarter: str,
    transcript_text: str,
    *,
    api_key: str,
    company_name: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> CallAnalysis:
    """
    Sends the call to Claude and returns its reading.

    Raises AISummaryError rather than returning a placeholder when the
    call cannot be made: a report that silently degrades to "no analysis
    available" text, printed in the same style as a real one, is how a
    reader ends up trusting an empty page.
    """
    if not transcript_text.strip():
        raise AISummaryError("transcript vide : rien a analyser")
    if not api_key:
        raise AISummaryError("ANTHROPIC_API_KEY absent")

    text = _call_claude_api(
        build_prompt(ticker, quarter, transcript_text, company_name),
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        system_prompt=_SYSTEM_PROMPT,
    )
    return CallAnalysis(
        ticker=ticker,
        quarter=quarter,
        text=text,
        model=model,
        transcript_words=len(transcript_text.split()),
    )


__all__ = ["CallAnalysis", "MAX_TOKENS", "analyse_call", "build_prompt"]
