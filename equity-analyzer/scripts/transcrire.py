"""
D'un fichier audio OU d'une URL YouTube à un rapport, en une commande.

    python scripts/transcrire.py SOURCE TICKER TRIMESTRE

    SOURCE peut être :
      un fichier audio local   sap_q2.mp3
      une URL YouTube          https://www.youtube.com/watch?v=XXXX

    exemples :
    python scripts/transcrire.py sap_q2.mp3 SAP 2026Q2
    python scripts/transcrire.py "https://youtube.com/watch?v=XXXX" SAP 2026Q2

CE QUE ÇA FAIT, dans l'ordre :

  1. si SOURCE est une URL, extrait l'audio avec yt-dlp,
  2. transcrit l'audio avec Whisper,
  3. écrit le texte dans transcripts/TICKER_TRIMESTRE.txt (le nom exact
     que le rapport attend, pour que tu n'aies rien à renommer),
  4. si ANTHROPIC_API_KEY est présent, enchaîne et génère le rapport.

Tu n'as donc qu'UNE chose à retenir : cette commande. Le nom du fichier,
son emplacement, le format du trimestre, tout est géré ici.

UN MOT SUR YOUTUBE. Les conditions de YouTube restreignent le
téléchargement. Pour un earnings call posté par la SOCIÉTÉ sur sa chaîne
officielle, en extraire l'audio pour ton analyse interne est dans le même
esprit que transcrire le webcast public auquel tu assistes. Une réupload
par un tiers est plus discutable : préfère la source officielle. Ce choix
est le tien, l'outil ne le fait pas à ta place.

CHOISIR LE MODÈLE. Par défaut large-v3, le plus fiable, mais lourd (~3 Go)
et lent sur un Mac sans GPU. Pour un test rapide : --model small (~250 Mo)
ou --model medium. Pour ne pas le retaper à chaque fois, règle-le une fois
dans ton terminal :
    export WHISPER_MODEL=small
Le défaut reste large-v3 pour la prod, où un chiffre mal entendu compte.

INSTALLATION (une fois) :
    pip install openai-whisper       # transcription
    pip install yt-dlp               # seulement si tu pars d'une URL
    puis ffmpeg :  brew install ffmpeg   (macOS)
                   sudo apt-get install ffmpeg   (Ubuntu)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "transcripts"

# Même seuil que le pipeline : un vrai call fait 6 000 à 12 000 mots, donc
# quelques centaines signifient un audio tronqué ou la mauvaise piste.
MIN_WORDS = 1500


def _valid_quarter(quarter: str) -> bool:
    return (len(quarter) == 6 and quarter[4] == "Q"
            and quarter[:4].isdigit() and quarter[5] in "1234")


def _is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _download_audio(url: str, into: Path) -> Path:
    """
    The call's audio, pulled from a video URL with yt-dlp.

    A subprocess rather than the library, because `pip install yt-dlp`
    puts the command on the PATH and shelling out keeps this script from
    depending on yt-dlp's Python API, which changes more often than its
    CLI. Errors clearly when the tool is missing.
    """
    if shutil.which("yt-dlp") is None:
        sys.exit(
            "yt-dlp n'est pas installé, il extrait l'audio d'une URL. Fais :\n"
            "    pip install yt-dlp\n"
            "(et ffmpeg, comme pour Whisper). Ou pars d'un fichier audio local."
        )
    target = into / "audio.%(ext)s"
    print(f"Extraction de l'audio depuis {url} ...")
    result = subprocess.call([
        "yt-dlp", "-x", "--audio-format", "mp3",
        "-o", str(target), url,
    ])
    if result != 0:
        sys.exit(
            "yt-dlp n'a pas pu récupérer l'audio. Vérifie l'URL, et que la vidéo "
            "est bien accessible (ni privée, ni géo-bloquée)."
        )
    files = sorted(into.glob("audio.*"))
    if not files:
        sys.exit("yt-dlp n'a produit aucun fichier audio.")
    return files[0]


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
    print("(sur un Mac sans GPU, un call d'une heure peut prendre 30 à 60 min)")
    model = whisper.load_model(model_name)
    # verbose=False AFFICHE une barre de progression sur la durée de
    # l'audio. Le défaut (None) ne montre rien, et une transcription
    # silencieuse de 40 minutes est indistinguable d'un plantage : c'est
    # exactement ce qui a fait croire à un gel. verbose=True imprimerait
    # tout le texte au fur et à mesure, trop bavard ; False donne le juste
    # milieu, une barre et rien d'autre.
    #
    # language=None laisse Whisper détecter; on passe la valeur seulement
    # si l'utilisateur l'a donnée, pour ne pas forcer l'anglais sur un
    # call tenu dans une autre langue.
    result = model.transcribe(
        str(audio), language=language or None, verbose=False
    )
    return (result.get("text") or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audio -> transcript nommé -> rapport, en une commande.",
    )
    parser.add_argument("source",
                        help="fichier audio local (mp3, m4a...) OU URL YouTube")
    parser.add_argument("ticker", help="le ticker, ex: SAP")
    parser.add_argument("trimestre", help="le repère fiscal, ex: 2026Q2")
    # The default stays the most accurate model, because on a financial
    # call the model is what decides whether a figure was heard right,
    # and a misheard number in a report is a real error. But WHISPER_MODEL
    # lets someone set a lighter one ONCE in their shell for fast
    # iteration, without retyping --model every run and without lowering
    # the default that production relies on. The flag still wins when
    # given, so a one-off override is `--model ...`.
    parser.add_argument(
        "--model",
        default=os.environ.get("WHISPER_MODEL", "large-v3"),
        help="modèle Whisper. Défaut: large-v3 (le plus fiable), ou la valeur "
             "de WHISPER_MODEL si définie. Plus léger et rapide: small, medium.",
    )
    parser.add_argument("--langue", default="",
                        help="langue du call (ex: en, fr). Vide = détection auto.")
    parser.add_argument("--pas-de-rapport", action="store_true",
                        help="s'arrêter après le transcript, ne pas générer le PDF")
    args = parser.parse_args()

    ticker = args.ticker.strip().upper()
    quarter = args.trimestre.strip().upper()
    if not _valid_quarter(quarter):
        sys.exit(f"Le trimestre doit ressembler à 2026Q2, reçu : {quarter!r}\n"
                 "C'est le repère FISCAL de la société, celui qu'elle annonce "
                 "dans le call.")

    # A URL is downloaded into a scratch directory that lives only for
    # the run; a local path is used where it sits, untouched. The
    # quarter is validated FIRST, so a typo does not cost a download.
    tmp = None
    if _is_url(args.source):
        tmp = tempfile.mkdtemp(prefix="transcrire_")
        audio = _download_audio(args.source, Path(tmp))
    else:
        audio = Path(args.source)
        if not audio.is_file():
            sys.exit(f"Fichier audio introuvable : {audio}\n"
                     "(ou, si c'était une URL, elle doit commencer par http)")

    try:
        text = _transcribe(audio, args.model, args.langue.strip())
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
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
