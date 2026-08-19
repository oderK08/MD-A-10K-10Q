"""
Several companies' calls, read together, for what only the comparison
shows.

WHY THIS IS A DIFFERENT PASS, not a longer report. A single report
answers "what did this company say". A sector view answers a question no
individual report can: where do peers, on the SAME driver, say different
things, and where has every management converged on one narrative. The
value for a professional is precisely in the divergence and in the
consensus, because what the whole sector agrees on tends to be in the
price already, and where it splits is where the reading has an edge.

WHAT IT IS BUILT ON. Only the archived report records: each carries the
reading, the verdict, the consensus outcome and the Q&A findings for one
company and one quarter. Nothing else is imported. The synthesis is a
reading of what was SAID across the group, not a valuation and not a
recommendation, and the prompt is held to that as hard as the single
report is.

QUARTERS MAY DIFFER, and that is handled honestly rather than hidden.
Earnings season spreads calls over weeks, so the latest available
quarter is taken for each company and the quarter is printed next to its
name. The caller warns when they diverge; the prompt is told each one so
it never compares a company's Q2 against a peer's Q4 as if they were the
same moment.
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

# Roomier than a single report: the answer covers several companies on
# several axes, and the thinking that precedes it scales with the number
# of readings fed in. The ceiling covers thinking plus response (see
# qa_analysis.MAX_TOKENS for the run this rule was learned on).
MAX_TOKENS = 16000

_SCHEMA = """{
  "fil_directeur": "un paragraphe etoffe (6 a 10 phrases) : le narratif sur lequel les directions du groupe convergent ce trimestre, et donc ce qui est probablement deja dans les cours",
  "divergences": [
    {"driver": "le sujet precis, par exemple pricing, capex, demande sur tel marche",
     "positions": [{"ticker": "...", "position": "ce que dit cette societe, court"}],
     "qui_diverge": "la ou l'ecart est le plus net, en une phrase"}
  ],
  "questions_marche": [
    {"sujet": "ce que les analystes ont presse",
     "presse_chez": ["TICKER", "TICKER"],
     "repondu_par": ["TICKER"],
     "esquive_par": ["TICKER"],
     "lecture": "ce que ce partage dit, une phrase"}
  ],
  "guidance": [
    {"metrique": "capex, revenu, marge...",
     "par_societe": [{"ticker": "...", "mouvement": "releve|tenu|abaisse|nc",
                      "detail": "le chiffre ou l'engagement, court"}]}
  ],
  "read_throughs": [
    {"de": "TICKER ou entite nommee", "vers": "TICKER",
     "implication": "ce que la declaration de l'un implique pour l'autre",
     "base": "la phrase ou le fait du call qui le fonde"}
  ],
  "ton": "un paragraphe : qui est confiant, qui hedge, sur les themes communs, et l'ecart script contre Q&A la ou il est notable"
}"""

_SYSTEM_PROMPT = f"""Tu es un analyste equity chevronne. On te donne les lectures de plusieurs earnings calls de societes d'un meme secteur, chacune deja resumee (verdict, lecture, attentes, session questions-reponses). Tu produis la SYNTHESE SECTORIELLE : ce que seule la comparaison revele.

REGLES ABSOLUES

1. Ta matiere est UNIQUEMENT ce qui t'est fourni. Aucun cours, aucun multiple, aucune valorisation, aucune actualite, aucun souvenir d'entrainement. Tu ne compares pas des valorisations : tu compares ce qui a ete DIT dans les calls.

2. Tu ne donnes aucune recommandation d'achat ou de vente, ni objectif de cours. Eclairer la divergence est demande ; trancher sur un titre ne l'est pas.

3. Chaque affirmation est ATTRIBUEE a une societe nommee par son ticker. Quand une affirmation vient d'une lecture, appuie la sur une citation courte entre guillemets si la lecture en contient une.

4. Tu ne compares que le comparable. Chaque societe t'est donnee avec SON trimestre. Si deux societes sont sur des trimestres differents, tu peux quand meme les rapprocher, mais tu ne presentes pas un ecart comme une nouveaute du meme moment : dis le trimestre quand ca compte.

5. La VALEUR est dans la divergence et dans le consensus. Ce sur quoi toutes les directions s'accordent est probablement deja dans les cours : nomme le (fil directeur). La ou elles s'ecartent est l'angle : c'est le coeur de ta synthese, donne lui le plus de place.

6. Les read-throughs (ce que la declaration d'une societe implique pour une autre nommee) sont ce qu'un gerant ne voit pas en lisant les rapports separement. Marque les comme des deductions, appuyees sur un fait du call.

7. Tu n'utilises jamais le tiret cadratin ni le tiret demi cadratin, dans aucun champ. Virgule, deux points ou parenthese.

8. N'invente rien. Une liste vide si le materiau ne dit rien sur un axe. Ne remplis pas pour remplir.

Reponds UNIQUEMENT avec un objet JSON valide, sans texte ni balises autour :

{_SCHEMA}"""


@dataclass(frozen=True)
class SectorSynthesis:
    """The cross-company reading, as data the report lays out."""

    tickers: list
    quarters: dict            # ticker -> quarter, for the header
    model: str
    fil_directeur: str = ""
    divergences: list = field(default_factory=list)
    questions_marche: list = field(default_factory=list)
    guidance: list = field(default_factory=list)
    read_throughs: list = field(default_factory=list)
    ton: str = ""

    @property
    def mixed_quarters(self) -> bool:
        """True when the companies are not all on the same quarter."""
        return len(set(self.quarters.values())) > 1


def system_prompt() -> str:
    return _SYSTEM_PROMPT


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_response(text: str) -> dict:
    """The JSON object out of the model's answer. See qa_analysis for the
    same fence-tolerant, content-strict reasoning."""
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
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and any(item.values())]


def _one_company_block(record: dict) -> str:
    """One company's material, as the prompt sees it."""
    ticker = record.get("ticker", "?")
    quarter = record.get("trimestre", "?")
    lines = [f"=== {ticker} ({record.get('societe', ticker)}), trimestre {quarter} ==="]

    verdict = (record.get("verdict") or "").strip()
    if verdict:
        lines.append(f"Verdict : {verdict}")

    attentes = record.get("attentes")
    if isinstance(attentes, dict) and attentes.get("disponible"):
        lines.append(
            f"Consensus BPA : attendu {attentes.get('bpa_attendu')}, "
            f"publie {attentes.get('bpa_publie')} "
            f"(surprise {attentes.get('surprise_pct')}%)"
        )

    reading = (record.get("lecture") or "").strip()
    if reading:
        lines.append("Lecture :")
        lines.append(reading)

    qa = record.get("qa")
    if isinstance(qa, dict):
        dodges = qa.get("esquives") or []
        if dodges:
            lines.append("Esquives :")
            for d in dodges:
                lines.append(
                    f"  - {d.get('analyst', '?')} : demande {d.get('what_was_asked', '?')}, "
                    f"obtient {d.get('what_was_given', '?')} (gravite {d.get('severity', '?')})"
                )
        signals = qa.get("signaux_prospectifs") or []
        if signals:
            lines.append("Signaux prospectifs hors communique :")
            for s in signals:
                lines.append(f"  - {s.get('signal', '?')} ({s.get('direction', '?')})")
    return "\n".join(lines)


def build_prompt(records: list) -> str:
    """The whole group, one company after another."""
    blocks = "\n\n".join(_one_company_block(r) for r in records)
    tickers = ", ".join(r.get("ticker", "?") for r in records)
    return (
        f"Secteur : {tickers}. Produis la synthese sectorielle.\n\n"
        f"{blocks}"
    )


def analyse_sector(
    records: list,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> SectorSynthesis:
    """
    Reads the group and returns the synthesis. Raises ClaudeError on
    anything that would leave a half understood answer.
    """
    if len(records) < 2:
        raise ClaudeError("il faut au moins deux societes pour une synthese sectorielle")

    payload = parse_response(call_claude(
        build_prompt(records),
        api_key=api_key,
        system_prompt=system_prompt(),
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    ))

    return SectorSynthesis(
        tickers=[r.get("ticker", "?") for r in records],
        quarters={r.get("ticker", "?"): r.get("trimestre", "?") for r in records},
        model=model,
        fil_directeur=str(payload.get("fil_directeur", "")).strip(),
        divergences=_entries(payload, "divergences"),
        questions_marche=_entries(payload, "questions_marche"),
        guidance=_entries(payload, "guidance"),
        read_throughs=_entries(payload, "read_throughs"),
        ton=str(payload.get("ton", "")).strip(),
    )


__all__ = [
    "MAX_TOKENS",
    "SectorSynthesis",
    "analyse_sector",
    "build_prompt",
    "parse_response",
    "system_prompt",
]
