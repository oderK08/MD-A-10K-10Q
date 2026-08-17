"""
D'un fichier audio à un rapport, en une commande.

    python scripts/transcrire.py CHEMIN_AUDIO TICKER TRIMESTRE

    exemple :
    python scripts/transcrire.py sap_q2.mp3 SAP 2026Q2

CE QUE ÇA FAIT, dans l'ordre :

  1. transcrit l'audio avec Whisper,
  2. écrit le texte dans transcripts/TICKER_TRIMESTRE.txt (le nom exact
     que le rapport attend, pour que tu n'aies rien à renommer),
  3. si ANTHROPIC_API_KEY est présent, enchaîne et génère le rapport.

Tu n'as donc qu'UNE chose à retenir : cette commande. Le nom du fichier,
son emplacement, le format du trimestre, tout est géré ici.

CE QUE ÇA NE FAIT PAS : récupérer l'audio. Whisper transcrit un fichier
que tu fournis. Enregistrer le webcast auquel tu assistes est ta partie,
et c'est la seule route honnête (copier un site de transcripts ne l'est
pas). Voir transcript_source.py.

INSTALLATION (une fois) :
    pip install openai-whisper
    puis ffmpeg :  brew install ffmpeg   (macOS)
                   sudo apt-get install ffmpeg   (Ubuntu)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "transcripts"

# Même seuil que le pipeline : un vrai call fait 6 000 à 12 000 mots, donc
# quelques centaines signifient un audio tronqué ou la mauvaise piste.
MIN_WORDS = 1500


def _valid_quarter(quarter: str) -> bool:
    return (len(quarter) == 6 and quarter[4] == "Q"
            and quarter[:4].isdigit() and quarter[5] in "1234")


def _transcribe(audio: Path, model_name: str, language: str) -> str:
    """Le texte du call. Erreur claire si Whisper n'est pas installé."""
    try:
        import whisper  # noqa: PLC0415 -- dépendance lourde et optionnelle
    except ImportError:
        sys.exit(
            "Whisper n'est pas installé. Fais :\n"
            "    pip install openai-whisper\n"
            "puis installe ffmpeg (brew install ffmpeg, ou "
            "sudo apt-get install ffmpeg)."
        )

    print(f"Transcription de {audio.name} avec le modèle {model_name}...")
    print("(un call d'une heure prend quelques minutes sur GPU, plus sur CPU)")
    model = whisper.load_model(model_name)
    # language=None laisse Whisper détecter; on passe la valeur seulement
    # si l'utilisateur l'a donnée, pour ne pas forcer l'anglais sur un
    # call tenu dans une autre langue.
    result = model.transcribe(str(audio), language=language or None)
    return (result.get("text") or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audio -> transcript nommé -> rapport, en une commande.",
    )
    parser.add_argument("audio", help="le fichier audio du call (mp3, m4a, wav...)")
    parser.add_argument("ticker", help="le ticker, ex: SAP")
    parser.add_argument("trimestre", help="le repère fiscal, ex: 2026Q2")
    parser.add_argument("--model", default="large-v3",
                        help="modèle Whisper (défaut: large-v3, le plus fiable)")
    parser.add_argument("--langue", default="",
                        help="langue du call (ex: en, fr). Vide = détection auto.")
    parser.add_argument("--pas-de-rapport", action="store_true",
                        help="s'arrêter après le transcript, ne pas générer le PDF")
    args = parser.parse_args()

    audio = Path(args.audio)
    if not audio.is_file():
        sys.exit(f"Fichier audio introuvable : {audio}")

    ticker = args.ticker.strip().upper()
    quarter = args.trimestre.strip().upper()
    if not _valid_quarter(quarter):
        sys.exit(f"Le trimestre doit ressembler à 2026Q2, reçu : {quarter!r}\n"
                 "C'est le repère FISCAL de la société, celui qu'elle annonce "
                 "dans le call.")

    text = _transcribe(audio, args.model, args.langue.strip())
    words = len(text.split())
    if words < MIN_WORDS:
        sys.exit(
            f"\nSeulement {words} mots transcrits. Un vrai call en fait 6 000 à "
            f"12 000, donc l'audio est tronqué, ou ce n'est pas le bon fichier.\n"
            f"Rien n'a été écrit (seuil : {MIN_WORDS})."
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{ticker}_{quarter}.txt"
    out.write_text(text, encoding="utf-8")

    print()
    print(f"OK. Transcript écrit ({words} mots) dans :")
    print(f"    {out}")
    print()

    if args.pas_de_rapport:
        print("Pour générer le rapport ensuite :")
        print(f"    REGION=International TICKER={ticker} QUARTER={quarter} \\")
        print("        ANTHROPIC_API_KEY=... python scripts/rapport.py")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("ANTHROPIC_API_KEY n'est pas défini, donc je m'arrête au transcript.")
        print("Définis la clé puis génère le rapport :")
        print(f"    export ANTHROPIC_API_KEY=sk-ant-...")
        print(f"    REGION=International TICKER={ticker} QUARTER={quarter} \\")
        print("        python scripts/rapport.py")
        return 0

    print("Génération du rapport...")
    env = dict(os.environ, REGION="International", TICKER=ticker, QUARTER=quarter)
    return subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "rapport.py")], env=env
    )


if __name__ == "__main__":
    sys.exit(main())
