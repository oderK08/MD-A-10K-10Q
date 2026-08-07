"""
One ticker in, the latest earnings call out -- and what changed in it
since the quarter before.

This is the thing the whole transcript detour was for. Type a ticker,
get the most recent call, compared against the previous one.

WHICH QUARTER IS "THE LATEST" is answered by EDGAR, not guessed. The
provider labels transcripts by the issuer's fiscal calendar, and EDGAR
already carries that calendar on every filing (`fy`/`fp`). So the newest
10-Q or 10-K on file tells us both which quarter just happened AND how
the company labels it -- no calendar arithmetic, no fiscal-year table,
and it is right for a June-year filer and a January-year filer without
either being special-cased.

WHAT IS COMPARED. Prepared remarks against prepared remarks. Those are
scripted and written to a template quarter over quarter, so a diff
surfaces the sentences management chose to change -- which is the
signal. The Q&A is reported but NOT diffed: it is unscripted, its
wording never repeats, and a diff of it would mark everything as changed
while meaning nothing. What matters there is which topics analysts
pushed on, which is a different question and honestly labelled as
not-yet-answered rather than faked with a diff.

Costs 2 requests: this quarter's call and last quarter's.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from equity_analyzer.data_layer import (
    CikLookup,
    EdgarClient,
    EdgarClientConfig,
    EdgarClientError,
    FilingNotFoundError,
)
from equity_analyzer.data_layer.transcript_cache import CachedTranscriptSource, TranscriptCache
from equity_analyzer.data_layer.transcript_period import (
    PeriodLabelUnavailable,
    alpha_vantage_label,
    previous_label,
    verify_against_declared,
)
from equity_analyzer.data_layer.transcript_source import (
    TranscriptUnavailable,
    alpha_vantage_source,
)
from equity_analyzer.diff.grouped_diff import split_into_groups
from equity_analyzer.diff.text_diff import diff_text

TICKER = os.environ.get("TICKER", "").strip().upper()
USER_AGENT = os.environ.get("SEC_USER_AGENT", "EquityAnalyzer/1.0 contact@example.com").strip()

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "transcripts"
OUTPUT = ROOT / "dernier_call.md"

# Five requests a minute on the free tier. Two calls back to back is
# within that, but a pause costs nothing and a rate-limit refusal here
# would read as "no transcript for that quarter".
PAUSE_SECONDS = 13.0


def _resolve_latest_period(client, cik: str) -> tuple:
    """(label, previous_label, human description) from the newest filing."""
    from equity_analyzer.data_layer.cik_lookup import latest_quarterly_pair
    from equity_analyzer.data_layer.xbrl_normalizer import build_financial_period

    pair = latest_quarterly_pair(client, cik)
    facts = client.fetch_company_facts(cik)
    period = build_financial_period(facts, pair.current.accession_number)
    label = alpha_vantage_label(period.fiscal_year, period.fiscal_period)
    return label, previous_label(label), (
        f"{period.fiscal_period} {period.fiscal_year} (clos le {period.period_end})"
    )


def main() -> int:
    if not TICKER:
        print("TICKER est vide. Indique le ticker à analyser.")
        return 1
    if not os.environ.get("ALPHAVANTAGE_API_KEY", "").strip():
        print("ALPHAVANTAGE_API_KEY absent. Ajoute-le en secret du dépôt.")
        return 1

    print(f"=== {TICKER} : dernier earnings call ===")
    client = EdgarClient(EdgarClientConfig(user_agent=USER_AGENT, timeout_seconds=30.0))

    try:
        cik = CikLookup(client).resolve(TICKER)
    except (EdgarClientError, FilingNotFoundError) as exc:
        print(f"CIK introuvable pour {TICKER} : {exc}")
        return 1

    try:
        label, prev, described = _resolve_latest_period(client, cik)
    except (EdgarClientError, FilingNotFoundError, PeriodLabelUnavailable, ValueError) as exc:
        print(f"Impossible de déterminer le dernier trimestre : {exc}")
        return 1

    print(f"Dernier trimestre déposé : {described}")
    print(f"Transcript demandé : {label}, comparé à {prev}")
    print()

    source = CachedTranscriptSource(alpha_vantage_source(), TranscriptCache(CACHE_DIR))

    try:
        current = source.fetch(TICKER, cik, quarter=label)
    except TranscriptUnavailable as exc:
        print(f"Pas de transcript pour {label} : {exc}")
        return 2

    warning = verify_against_declared(label, current.full_text)
    if warning:
        # Printed before anything else is shown. A mismatched pairing
        # produces output that looks entirely normal, so the reader has
        # to be told before they start believing it.
        print(f"!! ATTENTION : {warning}")
        print()

    print(f"{label} : {current.word_count} mots "
          f"({len(current.prepared_remarks.split())} préparés, "
          f"{len(current.qa.split()) if current.qa else 0} en Q&A)")

    previous_call = None
    try:
        time.sleep(PAUSE_SECONDS)
        previous_call = source.fetch(TICKER, cik, quarter=prev)
        print(f"{prev} : {previous_call.word_count} mots")
    except TranscriptUnavailable as exc:
        print(f"{prev} indisponible : {exc}")

    lines = [
        f"# {TICKER} — earnings call {label}",
        "",
        f"Dernier trimestre déposé chez SEC : {described}.",
        f"Transcript : {current.word_count} mots, source {current.source}.",
        "",
    ]
    if warning:
        lines += [f"> **Attention** — {warning}", ""]

    if previous_call is None:
        lines += [
            f"## Pas de comparaison possible",
            "",
            f"Le call {prev} n'a pas pu être récupéré, donc rien n'est comparé.",
            "Ce qui suit est le contenu du seul trimestre disponible.",
            "",
        ]
    else:
        result = diff_text(previous_call.prepared_remarks, current.prepared_remarks)
        modified = [s for s in result.segments if s.kind == "modified"]
        added = [s for s in result.segments if s.kind == "added"]
        removed = [s for s in result.segments if s.kind == "removed"]

        print()
        print(f"Remarques préparées : {len(modified)} reformulation(s), "
              f"{len(added)} ajout(s), {len(removed)} retrait(s) — "
              f"{result.similarity_ratio:.0%} de recouvrement")

        lines += [
            f"## Ce qui a changé dans les remarques préparées, contre {prev}",
            "",
            "Les remarques préparées sont écrites sur un gabarit d'un trimestre à",
            "l'autre. Ce qui change est donc ce que la direction a choisi de",
            "changer.",
            "",
            f"Recouvrement avec {prev} : **{result.similarity_ratio:.0%}**. "
            f"{len(modified)} reformulation(s), {len(added)} ajout(s), "
            f"{len(removed)} retrait(s).",
            "",
            "### Reformulé",
            "",
            "La même phrase, dite autrement. C'est le signal le plus fort d'un",
            "diff trimestriel : la direction a gardé le point mais changé les",
            "mots, ce qu'elle ne fait jamais par hasard.",
            "",
        ]
        lines += [
            f"- ~~{s.text.strip()}~~\n  → **{(s.replacement or '').strip()}**"
            for s in modified[:30]
        ] or ["_Rien._"]
        lines += ["", "### Ajouté ce trimestre", ""]
        lines += [f"- {s.text.strip()}" for s in added[:30]] or ["_Rien._"]
        lines += ["", "### Retiré depuis le trimestre précédent", ""]
        lines += [f"- {s.text.strip()}" for s in removed[:30]] or ["_Rien._"]

    if current.qa:
        # Reported, deliberately not diffed: unscripted wording never
        # repeats, so a diff would mark all of it changed and mean
        # nothing. Saying so is more useful than a table of noise.
        speakers = [h for h, _ in split_into_groups(current.qa)]
        lines += [
            "",
            "## Q&A",
            "",
            f"{len(current.qa.split())} mots, {len(speakers)} prises de parole.",
            "",
            "La Q&A n'est pas comparée au trimestre précédent : elle n'est pas",
            "scriptée, sa formulation ne se répète jamais, et un diff la",
            "marquerait intégralement comme changée sans rien vouloir dire. Ce",
            "qui compte ici est quels sujets les analystes ont creusés — une",
            "autre question, pas encore traitée.",
            "",
            "Intervenants :",
            "",
        ]
        lines += [f"- {s}" for s in speakers[:30]]

    OUTPUT.write_text("\n".join(lines) + "\n")
    print()
    print(f"Écrit dans {OUTPUT.name}")
    print(f"Transcripts mis en cache dans {CACHE_DIR.name}/ "
          f"({source.hits} depuis le cache, {source.fetches} appel(s) API)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
