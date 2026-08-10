"""
Reading an earnings call the way an analyst does: what was said, whether
it beat what was expected, and what to watch next.

DELIBERATELY NOT A DIFF. Everything else this project ever did to a
document was compare it against the previous quarter's, because filings
are largely copy-pasted and the edits are the signal. An earnings call
is not written that way: the first live comparison of two Microsoft
calls came back at 7% sentence overlap. Management writes a fresh script
every quarter, so a diff reports that almost everything changed, which
is true and worthless. Reading the call is the right tool for a call.

WHAT MAKES THE READING WORTH ANYTHING is that it is measured against
something. "Revenue grew 14%" is a fact with no direction; "revenue grew
14% against a consensus that had them at 11%, and management spent the
call explaining why that will not repeat" is a position. So the
consensus EPS for the exact quarter being read, plus the beat/miss
record of the quarters before it, go into the prompt alongside the
transcript. When they are unavailable the prompt SAYS SO explicitly
rather than omitting the section, because a model given no expectations
and no notice of their absence will supply expectations from memory.

THREE PROPERTIES THIS MUST HAVE, all of which come from the prompt
rather than from code:

  IT QUOTES. The transcript is a real verbatim record, not a machine
  transcription, so quoting is legitimate and it is the only way a
  reader can check the model's reading against the source. Every claim
  carries the sentence it rests on.

  IT SEPARATES SAID FROM MEANT. "Revenue grew 22%" is a quote. "Growth
  is decelerating" is an inference. A summary that blends them is worse
  than useless for advisory work, because the reader cannot tell which
  part to verify.

  IT FITS ON ONE PAGE. Not a stylistic preference: page 1 of the report
  is this reading and nothing else, and the page budget is enforced
  downstream by measuring the rendered PDF. A model that overruns gets
  truncated at a sentence boundary, which loses its conclusion. Asking
  for the right length up front is how that is avoided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .claude_client import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    ClaudeError,
    call_claude,
)

# A call runs 6,000-12,000 words, comfortably inside any current model's
# context, so the transcript goes in whole. Excerpting it would mean
# choosing what matters before the model has read it, which is the
# judgement being delegated.
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
# The reading itself is 400 to 540 words, so roughly 900 tokens. The
# rest of this budget is headroom for the thinking that precedes it.
MAX_TOKENS = 8000

# Measured against the real page, then given room. The page holds 610
# words (report.html_renderer.MAX_READING_WORDS); asking for up to 600
# left ten words of slack, and the first real run came back at 615, so
# the tail of the last section was cut. A model asked for a length
# lands near it, not on it, so the ask sits far enough below the cap
# that an ordinary overrun still fits whole.
TARGET_WORDS_LOW = 400
TARGET_WORDS_HIGH = 540

# WHY THIS SECTION IS CONDITIONAL. Page 2 inventories the dodges
# properly: analyst, what was asked, what came back, how bad the gap is.
# When it is there, asking page 1 for the same thing in prose spends
# roughly a fifth of a hard capped page saying twice what the reader is
# about to read laid out. The real TSLA run showed exactly that: the
# Colin Langan question on a SpaceX merger appeared as a paragraph on
# page 1 and as a row on page 2.
#
# So the section is dropped when page 2 will carry it, and the freed
# words go where the prompt already says the value is, the datable
# commitments. It is KEPT when there is no Q&A page, because then
# nothing else in the document would mention a dodge at all.
_DODGES_SECTION = """
## Les esquives
Les questions d'analystes ou la reponse ne repond pas. C'est souvent l'endroit le plus informatif d'un call. Cite la question, puis la reponse, puis dis en une phrase ce qui manque. S'il n'y a pas d'esquive nette, ecris le en une ligne et passe.
"""

_DODGES_ELSEWHERE = """
Ne consacre AUCUNE section aux questions esquivees : une analyse separee de la session questions-reponses est jointe au meme document et les recense une par une. Repeter ici ce qu'elle detaille couterait la place des engagements chiffres, qui n'apparaissent qu'ici. Si une esquive change ton verdict, une incise d'une ligne suffit.
"""


def system_prompt(qa_page: bool = False) -> str:
    """
    Page 1's instructions, which depend on whether page 2 exists.

    `qa_page` says the Q&A pass succeeded and its findings will be
    rendered. See `_DODGES_SECTION`.
    """
    sections = "quatre" if qa_page else "cinq"
    dodges = _DODGES_ELSEWHERE if qa_page else _DODGES_SECTION
    return _SYSTEM_PROMPT_TEMPLATE.format(sections=sections, dodges=dodges)


_SYSTEM_PROMPT_TEMPLATE = f"""Tu es un analyste equity chevronne. Tu viens de lire le transcript integral d'un earnings call et tu dois en rendre compte a un gerant qui n'a pas eu le temps de l'ecouter et qui doit decider s'il bouge sa ligne.

REGLES ABSOLUES

1. Tu ne cites que des phrases REELLEMENT presentes dans le transcript, mot pour mot, entre guillemets. C'est un verbatim officiel : la citation exacte est verifiable et c'est ce qui rend ton analyse utilisable. Si tu ne peux pas citer, tu n'affirmes pas.

2. Tu separes toujours ce qui est DIT de ce que tu en DEDUIS. Une citation est un fait ; ton interpretation est une lecture. Un gerant doit pouvoir distinguer d'un coup d'oeil ce qu'il doit verifier de ce qu'il doit soupeser.

3. TU LIS LE CALL CONTRE LES ATTENTES, pas dans l'absolu. On te donne le consensus de BPA du trimestre et le palmares des trimestres precedents. Un chiffre n'a pas de direction tout seul : une croissance de 14% est bonne ou mauvaise selon ce qui etait attendu, et une societe qui bat de deux centimes pour la huitieme fois d'affilee a tenu les attentes, elle ne les a pas depassees. Si ces donnees ne te sont pas fournies, la section te le dira explicitement : dans ce cas tu ecris que tu ne peux pas situer le trimestre par rapport au consensus, et tu n'inventes surtout pas un chiffre attendu de memoire.

4. Tu n'importes AUCUN fait exterieur sur cette societe : ni cours de bourse, ni actualite, ni autre publication, ni souvenir d'entrainement. Ta matiere est le transcript et les attentes fournies, rien d'autre. En revanche raisonner economiquement sur un fait fourni (une hausse de prix annoncee implique plus de revenu) est attendu de toi : la limite est de ne pas importer de faits, pas de t'interdire de reflechir.

5. Tu ne donnes jamais de recommandation d'achat ou de vente, ni d'objectif de cours. Trancher sur la balance de ce call est demande ; dire d'acheter le titre est une affirmation d'une autre nature, que cet outil ne fait pas.

6. Tu ne remplis pas. Si le call ne dit rien sur un point, tu ecris qu'il n'en dit rien. Un rapport court et vrai vaut mieux qu'un rapport complet et delaye.

7. Tu n'utilises jamais le tiret cadratin ni le tiret demi cadratin comme ponctuation. Virgule, deux points ou parenthese. Les traits d'union a l'interieur d'un mot ou d'un nom propre sont normaux et ne sont pas concernes.

CE QUE TU PRODUIS, exactement ces {{sections}} sections, dans cet ordre, en markdown avec des titres de niveau 2 (##)

## Verdict
Une a deux phrases. Commence par "Plutot bullish", "Plutot bearish", "Mitige" ou "Neutre", suivi de la raison principale. C'est la premiere chose que le lecteur voit, ne la noie pas.

## Face aux attentes
Le trimestre a-t-il battu, manque ou tenu le consensus, et surtout : qu'est-ce que la direction en dit. Un beat que le management passe sous silence et un beat qu'il met en avant ne se lisent pas pareil. Situe aussi ce trimestre dans le palmares des precedents. Citation a l'appui.

## Les declarations cles
Les engagements chiffres et datables : guidance, marges visees, capex, calendrier produit, prix. Citation exacte a chaque fois, puis en une phrase ce que ca implique. C'est le coeur de ton analyse, donne lui le plus de place.

{{dodges}}
## A surveiller
Deux a quatre points precis qui trancheront au prochain trimestre. Formule les comme des questions verifiables, pas comme des generalites.

LONGUEUR ET STYLE
Entre {TARGET_WORDS_LOW} et {TARGET_WORDS_HIGH} mots au total, titres compris. Cette limite est dure : ton texte occupe une page et une seule, et ce qui deborde est coupe. Francais. Direct, sans jargon inutile, sans formule de politesse. Tu ecris pour quelqu'un de presse et competent. Pas de conclusion qui resume ce que tu viens d'ecrire."""


@dataclass(frozen=True)
class CallAnalysis:
    """The model's reading, with the provenance needed to check it."""

    ticker: str
    quarter: str
    text: str
    model: str
    transcript_words: int
    # False when the consensus figures could not be fetched: the report
    # prints the caveat rather than letting a reading that had nothing
    # to measure against look like one that did.
    had_expectations: bool = False


def _eps(value: Optional[float]) -> str:
    return "non publie" if value is None else f"{value:.2f}"


def expectations_block(expectation, history=()) -> str:
    """
    The consensus section of the prompt, or an explicit statement that
    there is none.

    Never silently omitted. A model handed a transcript with no mention
    of expectations does not conclude that expectations are unknown, it
    fills them in from whatever it remembers about the company, and the
    resulting sentence ("slightly below what the street was looking for")
    is indistinguishable from a grounded one.
    """
    if expectation is None:
        return (
            "ATTENTES DU MARCHE : NON DISPONIBLES pour ce trimestre. "
            "Tu n'as aucun consensus a ta disposition. Dis le explicitement dans "
            "la section \"Face aux attentes\" et n'avance aucun chiffre attendu."
        )

    lines = [
        "ATTENTES DU MARCHE, pour le trimestre lu ci-dessous :",
        f"  BPA attendu (consensus)  : {_eps(expectation.estimated_eps)}",
        f"  BPA publie               : {_eps(expectation.reported_eps)}",
    ]
    if expectation.surprise_pct is not None:
        lines.append(
            f"  Ecart                    : {expectation.surprise_pct:+.1f}% "
            f"({expectation.verdict})"
        )
    if not expectation.comparable:
        lines.append("")
        lines.append(
            "  ATTENTION : cet ecart est trop large pour etre une vraie surprise. "
            "Le BPA publie ici est le chiffre GAAP, alors qu'un consensus d'analystes "
            "est presque toujours etabli sur une base AJUSTEE. Les deux ne mesurent "
            "donc pas la meme chose et l'ecart ci-dessus ne dit rien du trimestre. "
            "NE CONSTRUIS PAS de verdict dessus : dis dans la section \"Face aux "
            "attentes\" que la comparaison n'est pas exploitable et pourquoi, puis "
            "appuie toi sur ce que la direction dit elle meme de sa performance."
        )
    if expectation.reported_date is not None:
        lines.append(f"  Publie le                : {expectation.reported_date.isoformat()}")

    past = [q for q in history if q.estimated_eps is not None or q.reported_eps is not None]
    if past:
        lines.append("")
        lines.append("PALMARES DES TRIMESTRES PRECEDENTS (du plus recent au plus ancien) :")
        for quarter in past:
            surprise = (
                f"{quarter.surprise_pct:+.1f}%" if quarter.surprise_pct is not None else "n/a"
            )
            lines.append(
                f"  {quarter.fiscal_date_ending.isoformat()} : "
                f"attendu {_eps(quarter.estimated_eps)}, "
                f"publie {_eps(quarter.reported_eps)}, "
                f"ecart {surprise} ({quarter.verdict})"
            )
    return "\n".join(lines)


_MACHINE_TRANSCRIPT_NOTE = (
    "AVERTISSEMENT SUR LA SOURCE : ce transcript est une transcription "
    "AUTOMATIQUE de l'audio, pas le compte rendu ecrit de la societe. Le "
    "propos est fidele, mais un mot a pu etre mal entendu, et c'est le plus "
    "probable sur les CHIFFRES (\"fifteen\" et \"fifty\" se ressemblent). Tu "
    "cites normalement, mais quand un chiffre porte ta conclusion, dis qu'il "
    "vient d'une transcription automatique et reste a verifier."
)


def build_prompt(
    ticker: str,
    quarter: str,
    transcript_text: str,
    company_name: Optional[str] = None,
    expectation=None,
    history=(),
    verbatim: bool = True,
) -> str:
    """
    The transcript, whole, with the expectations in front of it.

    Nothing in the call is summarised or pre-selected on the way in.
    Trimming it before the model reads it would mean deciding what
    matters first, which is exactly the judgement being asked for.

    The expectations come FIRST and the transcript second, so the model
    reads the call already knowing what it has to be measured against
    rather than forming a view and then checking it.
    """
    who = company_name or ticker
    warning = "" if verbatim else f"{_MACHINE_TRANSCRIPT_NOTE}\n\n"
    return (
        f"Earnings call de {who} ({ticker}), trimestre {quarter}.\n\n"
        f"{warning}"
        f"{expectations_block(expectation, history)}\n\n"
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
    expectation=None,
    history=(),
    verbatim: bool = True,
    qa_page: bool = False,
    model: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> CallAnalysis:
    """
    Sends the call to Claude and returns its reading.

    `qa_page` says the document will carry the Q&A page, which changes
    what this reading is asked for: see `_DODGES_SECTION`. It has to be
    known BEFORE this call, which is why the caller runs the Q&A pass
    first even though this one is the report's spine.

    Raises rather than returning a placeholder when the call cannot be
    made: page 1 of this report IS the reading, so a report whose page 1
    degrades to an apology set in the same type as a real analysis is
    worse than no report at all.
    """
    if not transcript_text.strip():
        raise ClaudeError("transcript vide : rien a analyser")

    text = call_claude(
        build_prompt(
            ticker, quarter, transcript_text, company_name, expectation, history,
            verbatim=verbatim,
        ),
        api_key=api_key,
        system_prompt=system_prompt(qa_page),
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    return CallAnalysis(
        ticker=ticker,
        quarter=quarter,
        text=text,
        model=model,
        transcript_words=len(transcript_text.split()),
        had_expectations=expectation is not None,
    )


__all__ = [
    "CallAnalysis",
    "MAX_TOKENS",
    "TARGET_WORDS_HIGH",
    "TARGET_WORDS_LOW",
    "analyse_call",
    "build_prompt",
    "expectations_block",
    "system_prompt",
]
