"""
Settles the one question everything else depends on: is the transcript
endpoint actually usable on the free tier?

Everything built around Alpha Vantage rests on a claim read
second-hand -- that EARNINGS_CALL_TRANSCRIPT is available at 25 requests
a day. The vendor's site is unreachable from the environment that code
was written in, so the field names, the URL shape and the tier are all
unconfirmed guesses. One real call resolves all three at once.

WHY THIS IS A SEPARATE SCRIPT and not a line in the pipeline: Alpha
Vantage does not signal refusal with an HTTP status. A request for a
premium endpoint on a free key, an exhausted quota, and an unknown
symbol ALL come back as HTTP 200 with a JSON body carrying an
"Information" or "Note" key and no data. Code that checks
`response.ok` sees success in every one of those cases. So the first
thing this prints is the raw shape of what came back, before any
interpretation -- if the guesses in `alpha_vantage_source()` are wrong,
the output says exactly what the right ones are.

Costs 1-2 of the day's 25 requests. Never prints the key.
"""

from __future__ import annotations

import json
import os
import sys

import requests

API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
# IBM is Alpha Vantage's own documentation example, so a failure here is
# a problem with the key or the tier, never with the symbol.
TICKER = os.environ.get("VERIFY_TICKER", "IBM").strip().upper()
QUARTER = os.environ.get("VERIFY_QUARTER", "2024Q1").strip()

ENDPOINT = "https://www.alphavantage.co/query"

# Alpha Vantage's soft-failure keys. These arrive with HTTP 200 and no
# data, which is why status codes cannot be trusted here.
_SOFT_ERROR_KEYS = ("Information", "Note", "Error Message")


def _redact(text: str) -> str:
    return text.replace(API_KEY, "***") if API_KEY else text


def main() -> int:
    if not API_KEY:
        print("ALPHAVANTAGE_API_KEY absent. Ajoute-le en secret du dépôt.")
        return 1

    print("=== Vérification Alpha Vantage : le transcript est-il dans le gratuit ? ===")
    print(f"Symbole {TICKER}, trimestre {QUARTER}, clé de {len(API_KEY)} caractères")
    print()

    params = {
        "function": "EARNINGS_CALL_TRANSCRIPT",
        "symbol": TICKER,
        "quarter": QUARTER,
        "apikey": API_KEY,
    }
    try:
        response = requests.get(ENDPOINT, params=params, timeout=30)
    except requests.RequestException as exc:
        print(f"ÉCHEC RÉSEAU: {_redact(str(exc))}")
        return 1

    print(f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        print("Réponse non-JSON (200 chars):")
        print(_redact(response.text[:200]))
        return 1

    # Before any interpretation: what actually came back. If the field
    # guesses baked into alpha_vantage_source() are wrong, this is where
    # the correct ones become visible.
    print(f"Clés de premier niveau: {sorted(payload)}")
    print()

    for key in _SOFT_ERROR_KEYS:
        if key in payload:
            print(f"REFUS (HTTP 200, clé '{key}') :")
            print(f"  {_redact(str(payload[key]))[:400]}")
            print()
            message = str(payload[key]).lower()
            if "premium" in message or "subscribe" in message:
                print("VERDICT : endpoint premium. Le palier gratuit ne le couvre pas.")
                print("  → La voie Alpha Vantage tombe. Reste la reconnaissance")
                print("    webcast, ou un abonnement.")
            elif "rate limit" in message or "requests per day" in message:
                print("VERDICT : quota épuisé aujourd'hui, pas un refus de tier.")
                print("  → Relancer demain. Ça ne dit rien sur la disponibilité.")
            else:
                print("VERDICT : refusé, motif inhabituel. Lire le message ci-dessus.")
            return 2

    transcript = payload.get("transcript")
    if not transcript:
        print("VERDICT : réponse acceptée mais sans transcript.")
        print(f"  Charge utile complète : {_redact(json.dumps(payload)[:600])}")
        print("  → Soit ce trimestre n'existe pas, soit le champ porte un autre nom.")
        print("    Les clés de premier niveau ci-dessus le disent.")
        return 2

    print("VERDICT : ÇA MARCHE. Le transcript est accessible avec cette clé.")
    print()
    print(f"  {len(transcript)} tours de parole")
    if isinstance(transcript, list) and isinstance(transcript[0], dict):
        first = transcript[0]
        print(f"  Champs d'un tour : {sorted(first)}")
        print()
        # Confirms or refutes the field names hardcoded in the preset.
        expected = {"speaker", "title", "content"}
        missing = expected - set(first)
        if missing:
            print(f"  ATTENTION : champs attendus absents {sorted(missing)}.")
            print("  → Corriger alpha_vantage_source() avec les noms ci-dessus.")
        else:
            print("  Les noms de champs du préréglage sont corrects.")
        print()
        print("  Extrait du premier tour :")
        speaker = first.get("speaker", "?")
        content = str(first.get("content", ""))[:300]
        print(f"    [{speaker}] {content}")

    print()
    print("--- Vérification de bout en bout via l'adaptateur du projet ---")
    try:
        from equity_analyzer.data_layer.transcript_source import (
            _join_turns,
            alpha_vantage_source,
            split_prepared_from_qa,
        )

        # Deliberately NOT a second HTTP call. The first version of this
        # script re-fetched the same quarter through the adapter, and
        # the two requests -- identical, in the same second -- earned a
        # rate-limit refusal that read as an adapter bug. Reusing the
        # payload already in hand tests exactly the same parsing code,
        # costs nothing more from a 25-a-day budget, and cannot fail for
        # a reason unrelated to what is being checked.
        source = alpha_vantage_source()
        text = _join_turns(transcript, source)
        prepared, qa = split_prepared_from_qa(text)

        class _Call:
            word_count = len(text.split())
            prepared_remarks = prepared

        call = _Call()
        call.qa = qa
        print(f"  {call.word_count} mots au total")
        print(f"  Prepared remarks : {len(call.prepared_remarks.split())} mots")
        print(f"  Q&A : {len(call.qa.split()) if call.qa else 0} mots")
        if call.qa is None:
            print("  NOTE : découpage prepared/Q&A introuvable — la formule de")
            print("  passage à l'opérateur diffère chez ce fournisseur. À ajuster.")
    except Exception as exc:  # noqa: BLE001 -- this is a diagnostic, it reports rather than raises
        print(f"  L'adaptateur a échoué sur une charge utile que l'API a bien renvoyée :")
        print(f"    {_redact(str(exc))}")
        print("  → Le préréglage est à corriger, pas l'API.")
        return 2

    print()
    print("Tout est bon. Le transcript peut être branché dans le rapport.")
    print(f"Coût de cette vérification : 1 requête sur les 25 du jour.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
