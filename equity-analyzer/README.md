# Equity Analyzer

Outil d'analyse equity pour advisory : diff de filings SEC (10-K/10-Q),
red flags comptables quantitatifs, et analyse de tonalité (bullish/bearish),
compilés dans un rapport PDF structuré.

## Statut du projet

| Module | Statut | Description |
|---|---|---|
| 1. Data Layer | ✅ Terminé (29 tests) | Client SEC EDGAR, normalisation XBRL, extraction de sections textuelles |
| 2. Red Flags | ✅ Terminé (23 tests) | Altman Z-Score, Beneish M-Score, Piotroski F-Score |
| 3. Diff textuel | ✅ Terminé (16 tests) | Comparaison Item 1A / Item 7 entre deux filings |
| 4. Sentiment | ✅ Terminé (24 tests) | Score de tonalité Loughran-McDonald |
| 5. Report Builder | ✅ Terminé (18 tests) | Génération du rapport PDF |

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
  - **(trouvé via le test de fiabilité multi-tickers sur la vraie API)**
    les mots coupés par une balise en plein milieu (ex: un vrai 10-K
    Microsoft contient littéralement "RIS\<span>K\</span> FACTORS") — la
    balise n'est effacée sans laisser d'espace que si elle est strictement
    entre deux caractères alphanumériques, pour ne pas recoller des
    éléments distincts (ex: cellules de tableau adjacentes)
  - **(idem)** le décodage complet des entités HTML numériques (ex:
    `&#160;`, pas seulement `&nbsp;`) via `html.unescape` — un vrai 10-K
    Coca-Cola utilise la forme numérique entre "Item 7." et "Management's
    Discussion", ce qui faisait échouer silencieusement l'extraction tant
    que ce n'était pas décodé

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

## Module 3 — Diff textuel

### Ce qu'il fait

Diff au niveau phrase (via `difflib.SequenceMatcher`), construit sur les
`FilingTextSections` du Module 1 :

- **`text_diff.py`** — le diff générique : sépare d'abord le texte sur les
  vraies lignes vides (frontières de bloc réelles), puis, **à l'intérieur**
  de chaque bloc, découpe en phrases plutôt qu'en gardant tout le bloc
  d'un seul tenant.
  **Correction post-validation** : la première version découpait
  uniquement sur les lignes vides ("paragraphe"). En testant contre les
  vraies fixtures du Module 1 (pas des exemples synthétiques), on a
  découvert que beaucoup de filings SEC réels mettent tout un MD&A ou tout
  un risk factor dans **une seule balise `<p>`**, avec de simples retours
  à la ligne internes pour le formatage source — pas une balise par
  phrase. Résultat avec l'ancien découpage : si une seule phrase changeait
  (ex: "Revenue increased 12%" → "5%"), tout le bloc de 4 phrases était
  signalé comme entièrement supprimé + entièrement réécrit — un faux
  signal "gros changement". Le découpage par phrase à l'intérieur des
  blocs corrige ça (test de régression :
  `test_single_p_tag_with_line_wrapped_sentences_diffs_at_sentence_level`,
  + `tests/diff/test_integration_real_fixtures.py` qui fait tourner le
  pipeline complet Module 1 → Module 3 sur `sample_10k.html`).
- **`risk_factors.py`** — diff d'Item 1A, avec le cas boilerplate des 10-Q
  géré explicitement : si la section actuelle est la formule standard
  "aucun changement matériel", le module **refuse de calculer un diff**
  (`skipped=True` + `skip_reason` explicite) plutôt que de renvoyer un
  faux signal "0% de changement". Si seule la période *précédente* était
  du boilerplate (ex: le 10-K annuel vient de réécrire la section), le
  diff est calculé normalement — c'est un vrai changement, pas un artefact.
- **`mdna.py`** — diff d'Item 7/2 (MD&A), sans notion de boilerplate : le
  MD&A est toujours réécrit chaque trimestre, donc diff direct avec juste
  la garde "section manquante".

Toutes les fonctions lèvent `MissingSectionError` si une section n'a pas
été extraite par le Module 1, plutôt que de comparer `None` silencieusement.

### Lancer les tests

```bash
python -m pytest tests/diff -v
```

## Module 4 — Sentiment

### Ce qu'il fait

Score de tonalité Loughran-McDonald, construit sur `FilingTextSections`
du Module 1 :

- **`lm_dictionary.py`** — charge le **vrai** dictionnaire Loughran-McDonald
  depuis son export CSV officiel. **Ce repo ne l'embarque volontairement
  pas dans le code** : c'est une liste académique de plusieurs dizaines de
  milliers de mots, maintenue et versionnée par l'université Notre Dame,
  publiée gratuitement ici :
  👉 https://sraf.nd.edu/loughranmcdonald-master-dictionary/
  En recopier une version partielle de mémoire aurait sous-compté certaines
  catégories silencieusement — exactement le genre d'erreur que ce projet
  cherche à éviter. Le vrai fichier (86 553 mots) est fourni dans
  `data/Loughran-McDonald_MasterDictionary_1993-2025.csv` — voir
  `data/README.md`.
- **`scorer.py`** — comptage de mots par catégorie (Negative, Positive,
  Uncertainty, Litigious, Strong/Weak Modal, Constraining), proportions
  normalisées par le nombre de mots, et un score de tonalité nette
  `net_tone = (positive - negative) / (positive + negative)`. Approche
  "bag-of-words" standard (celle de l'article original Loughran & McDonald
  2011) — pas de gestion de la négation ("not profitable" compte quand
  même "profitable" comme positif), documenté comme limite connue plutôt
  que caché.
- **`sections.py`** — wrappers spécifiques à Item 7 (MD&A, toujours scoré)
  et Item 1A (Risk Factors), avec le même traitement explicite du
  boilerplate 10-Q que le Module 3 : scorer une phrase de disclaimer
  donnerait un résultat valide mais dénué de sens, donc c'est skip
  explicite plutôt que faux score neutre.
- `compare_sentiment()` — variation de tonalité entre deux textes déjà
  scorés (`tone_shift`), utile pour suivre l'évolution trimestre sur
  trimestre du MD&A.

### Tests

Trois niveaux :
- **Unitaires** sur un petit fixture CSV fait main (15 mots,
  `tests/fixtures/sample_lm_dictionary.csv`) — valide juste la logique du
  loader et du scorer.
- **Intégration Module 1 → Module 4** sur `sample_10k.html`, suivant la
  discipline adoptée après le bug du Module 3 (valider contre du texte
  réellement extrait, pas seulement des exemples synthétiques).
- **Intégration contre le vrai dictionnaire de production**
  (`test_integration_production_dictionary.py`) : charge le vrai fichier
  de 86 553 mots, vérifie que les tailles de catégories correspondent aux
  chiffres publiés (négatif: 2355, positif: 354, etc.), et score le
  fixture 10-K avec — première validation du projet contre de la vraie
  donnée plutôt qu'un fixture qui la reproduit.

```bash
python -m pytest tests/sentiment -v
```

### Utiliser le module (avec le vrai dictionnaire)

```python
from equity_analyzer.sentiment import load_lm_dictionary, score_mdna_sentiment

dictionary = load_lm_dictionary("data/Loughran-McDonald_MasterDictionary_1993-2025.csv")
result = score_mdna_sentiment(filing.text_sections, dictionary)
print(result.net_tone, result.proportions)
```

## Module 5 — Report Builder

### Ce qu'il fait

Assemble les sorties des Modules 1 à 4 en un rapport HTML → PDF.

- **`report_data.py`** — construit un `ReportData` à partir d'un `Filing`
  courant, d'un `Filing` précédent (optionnel), et d'un dictionnaire
  Loughran-McDonald (optionnel). **Changement de philosophie assumé par
  rapport aux Modules 2-4** : ceux-ci échouent bruyamment sur la moindre
  donnée manquante (`InsufficientDataError`, etc.) — c'est le bon
  comportement pour un calcul isolé. Mais un rapport qui agrège 7
  analyses ne doit pas planter entièrement parce qu'une seule (ex: Beneish
  M, faute de période précédente) n'est pas calculable. Chaque section est
  donc enveloppée dans un `SectionResult` : soit la valeur calculée, soit
  une raison en clair ("indisponible — aucune donnée financière extraite
  pour ce filing"). Seules les erreurs *attendues* de chaque module sont
  interceptées (`RedFlagError`, `DiffError`, `SentimentError`) — un vrai
  bug de programmation (`AttributeError` etc.) continue de remonter
  normalement, pour ne jamais se faire passer pour un simple manque de
  donnée.
- **`html_renderer.py`** — génère un document HTML autonome (CSS inline,
  aucune ressource externe — le renderer PDF n'a pas d'accès réseau).
  Tout texte issu d'un filing (nom de société, segments de diff...) passe
  par `html.escape()`.
- **`pdf_renderer.py`** — convertit le HTML en PDF via **`xhtml2pdf`**
  (pur Python, aucune dépendance système type Cairo/Pango/wkhtmltopdf —
  portable sur n'importe quel environnement). Note d'installation : si
  `pip install xhtml2pdf` échoue avec *"Cannot uninstall cryptography...,
  RECORD file not found"* (paquet système sans métadonnées pip), utilise
  `pip install --ignore-installed cryptography xhtml2pdf`.

### Tests

18 tests, dont un test d'intégration bout-en-bout qui fait tourner
**Module 1 → Module 5** sur les vraies fixtures (XBRL + HTML), jusqu'à un
vrai PDF généré (vérifie les octets magiques `%PDF-`). Avec cette fixture
précise, les Red Flags ressortent "indisponibles" (tags XBRL manquants,
données trimestrielles) — comportement voulu, pas un échec de test.

```bash
python -m pytest tests/report -v
```

### Utiliser le module

```python
from equity_analyzer.report import build_report_data, render_html, save_pdf

report = build_report_data(filing, prior_filing, lm_dictionary)
html = render_html(report)
save_pdf(html, "rapport.pdf")
```

## Statut : projet complet, validé contre de vraies données

Les 5 modules sont terminés et testés (110 tests). Le pipeline a été validé
contre la vraie API SEC EDGAR (voir `.github/workflows/test-real-sec-api.yml`
et `scripts/test_real_sec_pipeline.py`) sur 15 grandes capitalisations de
secteurs variés (tech, finance, énergie, santé, biens de consommation,
industriel). Deux vrais bugs d'extraction HTML ont été trouvés et corrigés
grâce à ce test (voir Module 1 ci-dessus : mots coupés par une balise,
entités HTML numériques non décodées). D'autres écarts réels et attendus
subsistent (tags XBRL non couverts selon l'émetteur, ex: `sga_expense`
manquant chez Microsoft/Amazon) — le Module 5 les affiche explicitement
comme "indisponible" plutôt que de planter ou de deviner.
