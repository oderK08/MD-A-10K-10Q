"""
Une synthese sectorielle a partir des rapports deja archives.

    python scripts/secteur.py NVDA AMD AVGO

Charge la fiche JSON de chaque societe (le jumeau machine de son PDF,
ecrit par rapport.py), prend pour chacune son DERNIER trimestre
disponible, et demande a Claude ce que seule la comparaison revele :
divergences, ce que le marche redoute vu par les questions, revisions de
guidance cote a cote, read-throughs, ton.

Sortie : un PDF de deux a trois pages dans rapports/, plus sa fiche JSON.

OU IL LIT. Dans rapports/, le meme dossier ou rapport.py ecrit. En CI, le
workflow y depose d'abord l'archive de la branche donnees. Les trimestres
peuvent differer d'une societe a l'autre (l'earnings season les etale) :
c'est assume, chaque trimestre est imprime a cote du nom et un
avertissement le signale.

CE QU'ELLE NE FAIT PAS : de la valorisation, ni une recommandation. La
matiere est ce qui a ete dit dans les calls, rien d'autre.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from equity_analyzer.report.claude_client import ClaudeError  # noqa: E402
from equity_analyzer.report.sector import analyse_sector  # noqa: E402
from equity_analyzer.report.sector_render import render_sector_html  # noqa: E402
from equity_analyzer.report.pdf_renderer import save_pdf  # noqa: E402

ROOT = Path(__file__).parent.parent
REPORTS_DIR = ROOT / "rapports"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip()

# Two to three pages, compacted to fit, like the single report.
MAX_PAGES = 3


def _ok(msg): print(f"  [OK]   {msg}")
def _warn(msg): print(f"  [SKIP] {msg}")
def _fail(msg): print(f"  [FAIL] {msg}")


def _load_record(ticker: str):
    """The archived report record for one ticker, or None with a reason."""
    path = REPORTS_DIR / f"{ticker}.json"
    if not path.is_file():
        _warn(f"{ticker} : pas de fiche archivee ({path.name}). Lance d'abord "
              f"le rapport sur {ticker}.")
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        _warn(f"{ticker} : fiche illisible ({exc})")
        return None
    if not isinstance(record, dict) or not record.get("lecture"):
        _warn(f"{ticker} : fiche sans lecture, inexploitable")
        return None
    return record


def main() -> int:
    tickers = [t.strip().upper() for t in sys.argv[1:] if t.strip()]
    if len(tickers) < 2:
        print("Donne au moins deux tickers : python scripts/secteur.py NVDA AMD AVGO")
        return 1
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY absent. C'est cette cle qui ecrit la synthese.")
        return 1

    print(f"=== Synthese sectorielle : {', '.join(tickers)} ===")

    records = []
    for ticker in tickers:
        record = _load_record(ticker)
        if record is not None:
            records.append(record)
            _ok(f"{ticker} : {record.get('trimestre', '?')} chargee")

    if len(records) < 2:
        _fail("Moins de deux societes exploitables : rien a comparer.")
        return 2

    quarters = {r.get("ticker"): r.get("trimestre") for r in records}
    if len(set(quarters.values())) > 1:
        _warn("Trimestres non alignes entre societes : "
              + ", ".join(f"{k} {v}" for k, v in quarters.items())
              + ". La synthese le signale sur la page.")

    print(f"  ...   Synthese par Claude ({ANTHROPIC_MODEL or 'modele par defaut'})")
    try:
        synthesis = analyse_sector(
            records,
            api_key=ANTHROPIC_API_KEY,
            **({"model": ANTHROPIC_MODEL} if ANTHROPIC_MODEL else {}),
        )
    except ClaudeError as exc:
        _fail(f"La synthese a echoue : {exc}")
        return 2
    _ok(f"Synthese ecrite : {len(synthesis.divergences)} divergence(s), "
        f"{len(synthesis.read_throughs)} read-through(s)")

    REPORTS_DIR.mkdir(exist_ok=True)
    slug = "-".join(t.get("ticker", "?") for t in records)
    output = REPORTS_DIR / f"secteur_{slug}.pdf"
    pages = save_pdf(render_sector_html(synthesis), output, max_pages=MAX_PAGES)
    try:
        shown = output.relative_to(ROOT)
    except ValueError:
        shown = output
    _ok(f"Synthese ecrite : {shown} ({pages} pages)")

    # Machine-readable twin, same idea as the single report: a synthesis
    # is itself a thing a later pass might load.
    try:
        (REPORTS_DIR / f"secteur_{slug}.json").write_text(
            json.dumps({
                "tickers": synthesis.tickers,
                "trimestres": synthesis.quarters,
                "modele": synthesis.model,
                "fil_directeur": synthesis.fil_directeur,
                "divergences": synthesis.divergences,
                "questions_marche": synthesis.questions_marche,
                "guidance": synthesis.guidance,
                "read_throughs": synthesis.read_throughs,
                "ton": synthesis.ton,
            }, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        _warn(f"Fiche JSON de la synthese non ecrite : {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
