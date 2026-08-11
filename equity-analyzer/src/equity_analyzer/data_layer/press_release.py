"""
The earnings press release, which the report was reasoning about
without ever having read.

WHY THIS EXISTS. The Q&A pass is told, in its own words, that the most
useful thing it can find is "ce qui a une valeur prospective mais
n'etait pas dans le communique de presse". It was never given the press
release. So the single section the prompt calls the most useful rested
on the model's assumption about what a document it had not seen
contained, and an assumption written in the same type as a finding is
indistinguishable from one on the page.

The material was there the whole time. A company announcing results
files an 8-K under Item 2.02 with the release attached as an exhibit,
on EDGAR, free, no key, no quota. `earnings_release` already lists
those filings and already classifies which exhibit is the release; the
only thing missing was fetching the document and handing it over.

WHAT IT BUYS BEYOND THAT ONE SECTION. The distinction between "already
public" and "said out loud only" applies to page 1 too. A quantified
commitment that was already in the release is not news; the same figure
volunteered only under questioning is. Without the release neither pass
could tell those apart, so both were quietly treating them the same.

THE PAIRING IS MEASURED, NOT ASSUMED. The obvious implementation takes
the newest earnings 8-K and hopes it belongs to the call being read.
That breaks in the two situations this project keeps running into: a
call the provider published late, so the report fell back a quarter,
and the window where results are announced before the periodic report
is filed. And an 8-K's `period_of_report` cannot arbitrate, because it
is the date of the EVENT rather than a period end, which has already
produced one wrong answer in this codebase.

So the release is paired the same way the transcript is: by reading the
quarter the company NAMES in its own headline and comparing it against
the label being reported. A release that does not match is not used.
Attaching the wrong quarter's release would make the model confidently
report as "new information" everything the actual release contained.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .cik_lookup import FilingNotFoundError
from .declared_period import read_declared_period
from .earnings_release import fetch_earnings_release, list_earnings_8ks
from .earnings_text import extract_earnings_text

# How many earnings 8-Ks to open before giving up. Two covers the cases
# that actually occur: the newest filing is the right one, or the report
# fell back one quarter because the provider had not published the
# newest call yet. Going deeper costs a document fetch per step to chase
# a pairing that, by then, is more likely to be a labelling problem than
# a missing filing.
MAX_FILINGS_EXAMINED = 2

# Below this, whatever was extracted is not a results release. A real
# one runs well over a thousand words; a few hundred means the exhibit
# was a cover page, an XBRL sidecar that slipped the classifier, or a
# document whose tables were stripped down to nothing.
MIN_WORDS = 400


class PressReleaseUnavailable(Exception):
    """
    No release could be paired to the quarter being reported.

    Raised rather than returning the nearest one: see the module
    docstring on what a mispaired release does to the reading.
    """


@dataclass(frozen=True)
class PressRelease:
    """The results announcement, as text, with its provenance."""

    quarter: str
    text: str
    document: str
    accession_number: str
    filed_date: object = None

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def fetch_press_release(client, cik: str, label: str,
                        max_filings: int = MAX_FILINGS_EXAMINED) -> PressRelease:
    """
    The results release for `label`, read off EDGAR.

    Raises `PressReleaseUnavailable` with the reasons it looked at and
    rejected, so the caller can print WHY rather than a blank.
    """
    # One exception type out of this module, whatever went wrong inside.
    # `list_earnings_8ks` raises when a filer reports results only inside
    # its 10-Q, which is a real answer rather than an error, and a caller
    # should not have to know two vocabularies to handle "no release".
    try:
        refs = list_earnings_8ks(client, cik, limit=max_filings)
    except FilingNotFoundError as exc:
        raise PressReleaseUnavailable(f"aucun 8-K de résultats déposé : {exc}") from exc
    if not refs:
        raise PressReleaseUnavailable("aucun 8-K de résultats déposé")

    misses = []
    for ref in refs:
        release = fetch_earnings_release(client, cik, ref)
        if release.press_release is None:
            misses.append(f"{ref.accession_number} (pas de communiqué joint)")
            continue

        html = client.fetch_filing_document(
            cik, ref.accession_number, release.press_release.document
        )
        text = extract_earnings_text(html).narrative.strip()
        words = len(text.split())
        if words < MIN_WORDS:
            misses.append(f"{release.press_release.document} ({words} mots seulement)")
            continue

        declared = read_declared_period(text)
        if declared is None:
            misses.append(f"{release.press_release.document} (trimestre non nommé)")
            continue
        if declared.label != label:
            misses.append(
                f"{release.press_release.document} (annonce {declared.label}, "
                f"pas {label})"
            )
            continue

        return PressRelease(
            quarter=label,
            text=text,
            document=release.press_release.document,
            accession_number=ref.accession_number,
            filed_date=ref.filed_date,
        )

    raise PressReleaseUnavailable(
        f"aucun communiqué appariable à {label} : " + " ; ".join(misses)
    )


def as_prompt_block(release: Optional[PressRelease], reason: str = "") -> str:
    """
    The release as both passes see it, or an explicit statement that
    there is none.

    NEVER SILENTLY OMITTED, the same discipline the consensus and the
    guidance baseline follow. A model asked what was NOT in the release,
    and handed no release, does not conclude that it cannot tell. It
    reconstructs a plausible release from what such documents usually
    contain and answers against that, and the answer reads exactly like
    one checked against the real thing.
    """
    if release is None:
        detail = f" ({reason})" if reason else ""
        return (
            f"COMMUNIQUE DE RESULTATS : NON DISPONIBLE{detail}.\n"
            "Tu ne peux donc PAS savoir ce qui etait deja public avant le call. "
            "N'affirme jamais qu'une information ne figurait pas dans le "
            "communique : dis, quand c'est pertinent, que tu n'as pas pu le "
            "verifier."
        )

    return (
        f"COMMUNIQUE DE RESULTATS du trimestre {release.quarter}, publie avant le "
        "call. C'est ce qui etait DEJA PUBLIC quand les analystes ont pose leurs "
        "questions.\n"
        "Tu ne l'analyses pas et tu n'en rends pas compte : il sert d'etalon. Ce "
        "qui y figure deja n'est pas une nouvelle information ; ce qui n'apparait "
        "qu'a l'oral en est une.\n\n"
        f"---DEBUT DU COMMUNIQUE---\n{release.text}\n---FIN DU COMMUNIQUE---"
    )


__all__ = [
    "MAX_FILINGS_EXAMINED",
    "MIN_WORDS",
    "PressRelease",
    "PressReleaseUnavailable",
    "as_prompt_block",
    "fetch_press_release",
]
