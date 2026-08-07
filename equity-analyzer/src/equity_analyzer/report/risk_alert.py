"""
The rare-signal alert on a quarter's Item 1A.

Almost every 10-Q's Part II Item 1A is the SEC-sanctioned shortcut:
"there have been no material changes to the risk factors disclosed in
our Annual Report on Form 10-K". That is the normal case, it carries no
information, and the report says so in one line and moves on -- which is
exactly what the user asked for ("MD&A, plus une alerte si un risque est
ajoute").

The exception is the whole point of this module: when a company DOES
write risk-factor text into a 10-Q, it is because something changed
badly enough that counsel would not let the boilerplate stand. That is
one of the rarest and highest-signal events in quarterly disclosure, and
it must not be buried in a percentage table.

Two asymmetries drive the design, both structural rather than
stylistic:

1. ONLY ADDITIONS ARE INTERPRETED. A 10-Q that writes its own Item 1A
   restates only the risk factors it is UPDATING, not the full set from
   the 10-K -- the rest remain on file and in force. So diffing it
   against the prior filing shows most of the prior text as "removed",
   which looks like a company withdrawing its risk disclosures and is
   nothing of the sort. Removals are counted (the reader is told they
   exist) but never reported as a signal, and the reason is printed
   alongside rather than left to be guessed at.

2. AGAINST AN ANNUAL PRIOR, EVEN THE ADDITIONS ARE NOISIER. At a Q1
   boundary the previous filing is the 10-K, whose Item 1A is the
   complete set; a Q1 that restates one risk in its own words produces
   "added" text that is really a rewording of something already on file.
   The alert still fires (a company bothering to rewrite a risk in a
   10-Q is still a signal) but says which comparison it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# How many added sentences the alert carries. This is a flag, not the
# detail report: enough to see what the company actually wrote, with a
# pointer to the full text when there is more.
_MAX_QUOTED_ADDITIONS = 5


@dataclass(frozen=True)
class RiskFactorsAlert:
    """
    `triggered` is the thing the reader acts on: did this quarter add
    risk-factor language at all.

    `headline` is always set, including when nothing fired -- "the
    boilerplate stands" is itself the answer to the question the reader
    is asking, and printing it costs one line while printing nothing
    leaves them wondering whether the check ran.
    """
    triggered: bool
    headline: str
    added_headings: list       # list[str], new risk sub-themes by name
    added_sentences: list      # list[str], verbatim, capped
    total_added_sentences: int
    removed_sentence_count: int
    prior_is_annual: bool


def _added_material(grouped) -> tuple:
    """
    Returns (added_headings, added_sentences, added_count, removed_count)
    across every sub-theme of a grouped diff, selected or not.

    Deliberately NOT restricted to the analyst-relevant selection: the
    selection is made on the MD&A for this report, and a risk factor
    nobody thought to watch for is precisely the one worth flagging.
    """
    added_headings = []
    added_sentences = []
    added_count = 0
    removed_count = 0
    for group in grouped.groups:
        group_additions = [s.text for s in group.diff.segments if s.kind == "added"]
        # A reworded sentence carries new wording too, and in risk
        # language the rewording IS the disclosure ("could affect"
        # becoming "has affected"), so the replacement counts as added
        # material rather than being skipped as a cosmetic edit.
        group_additions += [
            s.replacement for s in group.diff.segments
            if s.kind == "modified" and s.replacement
        ]
        removed_count += sum(1 for s in group.diff.segments if s.kind == "removed")
        if not group_additions:
            continue
        added_count += len(group_additions)
        if group.status == "added" and group.heading:
            added_headings.append(group.heading)
        added_sentences.extend(group_additions)
    return added_headings, added_sentences, added_count, removed_count


def evaluate_risk_alert(
    filing,
    risk_factors_diff,
    prior_filing=None,
) -> Optional[RiskFactorsAlert]:
    """
    Builds the alert from an already-computed Risk Factors diff section
    (a `SectionResult` wrapping a `RiskFactorsDiffResult`).

    Returns None when the question doesn't apply: an annual filing, whose
    Item 1A is the full risk disclosure rewritten as a matter of course
    (nothing rare about it), or a quarter whose diff wasn't computable,
    where "did this quarter add a risk" has no answer and inventing a
    reassuring one would be worse than silence. Every other path returns
    an alert, fired or not.
    """
    if getattr(filing.form_type, "value", filing.form_type) != "10-Q":
        return None
    if risk_factors_diff is None or not risk_factors_diff.available:
        return None

    prior_is_annual = bool(
        prior_filing is not None
        and getattr(prior_filing.form_type, "value", prior_filing.form_type) == "10-K"
    )
    result = risk_factors_diff.value

    if result.skipped:
        return RiskFactorsAlert(
            triggered=False,
            headline=(
                "Aucun risque nouveau déclaré ce trimestre : l'Item 1A reprend la "
                "clause « aucun changement significatif » et renvoie au dernier 10-K."
            ),
            added_headings=[],
            added_sentences=[],
            total_added_sentences=0,
            removed_sentence_count=0,
            prior_is_annual=prior_is_annual,
        )

    headings, sentences, added_count, removed_count = _added_material(result.diff)

    if not added_count:
        return RiskFactorsAlert(
            triggered=False,
            headline=(
                "L'Item 1A a été rédigé en propre ce trimestre mais n'ajoute aucune "
                "formulation nouvelle par rapport au dépôt précédent."
            ),
            added_headings=[],
            added_sentences=[],
            total_added_sentences=0,
            removed_sentence_count=removed_count,
            prior_is_annual=prior_is_annual,
        )

    if headings:
        what = (
            f"{len(headings)} facteur(s) de risque nouveau(x) portant un intitulé inédit, "
            f"et {added_count} phrase(s) ajoutée(s) au total"
        )
    else:
        what = f"{added_count} phrase(s) de risque ajoutée(s)"
    against = (
        "par rapport au dernier 10-K (comparaison de début d'exercice)"
        if prior_is_annual else "par rapport au trimestre précédent"
    )
    return RiskFactorsAlert(
        triggered=True,
        headline=(
            f"Alerte : la société a écrit des facteurs de risque dans ce 10-Q, "
            f"au lieu de la clause habituelle. {what.capitalize()} {against}."
        ),
        added_headings=headings,
        added_sentences=sentences[:_MAX_QUOTED_ADDITIONS],
        total_added_sentences=added_count,
        removed_sentence_count=removed_count,
        prior_is_annual=prior_is_annual,
    )


__all__ = ["RiskFactorsAlert", "evaluate_risk_alert"]
