# Equity Analyzer

Outil d'analyse equity pour advisory : diff de filings SEC (10-K/10-Q),
red flags comptables quantitatifs, et analyse de tonalité (bullish/bearish),
compilés dans un rapport PDF structuré.

## Statut du projet

| Module | Statut | Description |
|---|---|---|
| 1. Data Layer | ✅ Terminé (26 tests) | Client SEC EDGAR, normalisation XBRL, extraction de sections textuelles |
| 2. Red Flags | ✅ Terminé (23 tests) | Altman Z-Score, Beneish M-Score, Piotroski F-Score |
| 3. Diff textuel | ⏳ À venir | Comparaison Item 1A / Item 7 entre deux filings |
| 4. Sentiment | ⏳ À venir | Score de tonalité Loughran-McDonald |
| 5. Report Builder | ⏳ À venir | Génération du rapport PDF |

## Module 1 — Data Layer

### Ce qu'il fait

- **`edgar_client.py`** : client HTTP pour SEC EDGAR (companyfacts XBRL,
  submissions, documents bruts). Impose un `User-Agent` valide (obligatoire
  côté SEC), throttle les requêtes (5 req/s par défaut, sous la limite SEC
  de 10/s).
- **`cik_lookup.py`** : résolution ticker → CIK via le fichier officiel SEC
  `company_tickers.json`, et listing des filings par type de formulaire
  (10-K, 10-Q) à partir de `filings.recent`.
- **`xbrl_normalizer.py`** : transforme les données XBRL brutes en
  `FinancialPeriod` normalisé. Gère explicitement :
  - la variation des tags GAAP selon les émetteurs (liste de tags candidats
    par métrique, avec fallback ordonné)
  - la sélection de la valeur **telle que déclarée dans le filing d'origine**
    (par `accession_number`), pour ne jamais mélanger une valeur avec une
    restatement ultérieure
  - la classification de durée (instant / 3M / 6M / 9M / 12M) pour ne
    jamais comparer un trimestre seul à un cumul year-to-date
- **`text_sections.py`** : extrait Item 1A (Risk Factors), Item 7/Item 2
  (MD&A), Item 9A/Item 4 (Controls) du HTML brut d'un filing. Gère :
  - la désambiguïsation table des matières vs section réelle (la TOC
    contient les mêmes intitulés mais très peu de texte après)
  - la détection du boilerplate "no material changes" utilisé dans les
    10-Q pour Item 1A, afin de ne pas le traiter comme un vrai changement
    (ou une vraie absence de changement) de fond

### Ce qu'il ne fait PAS encore

- Pagination des filings antérieurs à `filings.recent` (~1000 filings les
  plus récents ; suffisant pour la plupart des usages advisory mais pas
  pour un historique complet sur des décennies)
- Cache disque des réponses EDGAR (à ajouter si le volume d'appels devient
  significatif)

### Installation

```bash
pip install -r requirements.txt
```

### Utiliser le module

```python
from equity_analyzer.data_layer import (
    EdgarClient, EdgarClientConfig, CikLookup, list_filings,
    build_financial_period, extract_sections,
)

config = EdgarClientConfig(user_agent="MyAdvisoryTool/1.0 you@example.com")
client = EdgarClient(config)

cik = CikLookup(client).resolve("AAPL")
filings = list_filings(client, cik, form_type="10-Q", limit=2)

company_facts = client.fetch_company_facts(cik)
period = build_financial_period(
    company_facts,
    accession_number=filings[0].accession_number,
    fiscal_year=2025,
    fiscal_period="Q2",
)

html = client.fetch_filing_document(
    cik, filings[0].accession_number, filings[0].primary_document
)
sections = extract_sections(html)
```

**Important** : le `user_agent` doit être une chaîne réelle avec un email
de contact valide — SEC EDGAR rejette (403) toute requête sans ça.

### Lancer les tests

```bash
python -m pytest tests/ -v
```

Tous les tests du Data Layer tournent sur des fixtures locales
(`tests/fixtures/`) qui reproduisent fidèlement les schémas JSON/HTML réels
de SEC EDGAR — aucun appel réseau réel n'est nécessaire pour les tests.

## Module 2 — Red Flags

### Ce qu'il fait

Trois scores quantitatifs, construits directement sur les `FinancialPeriod`
du Module 1 (jamais sur du XBRL brut) :

- **`altman_z.py`** — risque de faillite/détresse financière. Deux variantes
  explicites, jamais mélangées silencieusement :
  - **`original`** (Altman 1968, entreprises cotées) : utilise la valeur de
    **marché** des capitaux propres. SEC XBRL ne fournit pas de cours de
    bourse — il faut passer `market_value_of_equity` explicitement pour
    déclencher cette variante.
  - **`book_value`** (variante entreprises privées) : utilisée par défaut,
    coefficients et seuils de zone différents (pas juste la même formule
    avec une autre entrée).
  - Exige un `FinancialPeriod` annuel (12M) : appliqué tel quel sur un
    trimestre, le ratio Sales/Total Assets serait sous-estimé d'un facteur
    ~4, donc la fonction rejette explicitement les périodes trimestrielles.
- **`beneish_m.py`** — probabilité de manipulation comptable (modèle à 8
  variables). Nécessite une paire (current, prior) comparable YoY.
- **`piotroski_f.py`** — solidité fondamentale (9 signaux binaires,
  score 0-9). Nécessite également une paire (current, prior) comparable YoY.
- **`comparability.py`** — garde partagée (`require_comparable_yoy`) qui
  impose : même `duration`, année fiscale strictement postérieure, et même
  trimestre fiscal si la période n'est pas annuelle (Q2 vs Q2, jamais Q2 vs
  Q1) — pour ne jamais confondre saisonnalité et vrai signal.

Toutes les fonctions échouent explicitement (`InsufficientDataError`,
`IncomparablePeriodsError`) plutôt que de deviner une valeur manquante ou de
comparer silencieusement des périodes incompatibles.

### Lancer les tests

```bash
python -m pytest tests/redflags -v
```

23 tests, vérifiant notamment les formules à la main (valeurs attendues
calculées indépendamment, pas juste "ça tourne sans erreur").

## Prochaine étape

Module 3 : Diff textuel (comparaison Item 1A / Item 7 entre deux filings),
construit sur `FilingTextSections` du Module 1.
