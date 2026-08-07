# Equity Analyzer

Outil d'analyse equity pour advisory : diff de filings SEC (10-K/10-Q),
red flags comptables quantitatifs, et analyse de tonalité (bullish/bearish),
compilés dans un rapport PDF structuré.

## Statut du projet

| Module | Statut | Description |
|---|---|---|
| 1. Data Layer | ✅ Terminé (42 tests) | Client SEC EDGAR, normalisation XBRL, extraction de sections textuelles |
| 2. Red Flags | ✅ Terminé (23 tests) | Altman Z-Score, Beneish M-Score, Piotroski F-Score |
| 3. Diff textuel | ✅ Terminé (30 tests) | Comparaison Item 1A / Item 7 entre deux filings, regroupée par sous-thème |
| 4. Sentiment | ✅ Terminé (24 tests) | Score de tonalité Loughran-McDonald |
| 5. Report Builder | ✅ Terminé (59 tests) | Rapport principal 2 pages + rapport « détail » séparé, en niveaux de gris, police Lato intégrée |
| 6. Sélection + lecture IA (opt-in) | ✅ Terminé (23 tests) | Deux passes : l'IA choisit les sous-thématiques suivies par les analystes, Python les diffe, l'IA rédige le résumé exécutif dessus |

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
    par métrique, avec fallback ordonné) — étendue après le test multi-tickers
    sur la vraie API (`sga_expense`, `cogs`, `ppe_net`, `receivables`,
    `long_term_debt`, `depreciation_amortization`, `shares_outstanding` ont
    chacun reçu des tags alternatifs supplémentaires). **Ces ajouts sont du
    "best-effort"** basé sur la connaissance générale de la taxonomie
    US-GAAP, pas vérifiés contre les vrais filings qui avaient le manque
    (accès réseau toujours bloqué depuis mon environnement) — à valider en
    relançant le workflow GitHub Actions.
  - la sélection de la valeur **telle que déclarée dans le filing d'origine**
    (par `accession_number`), pour ne jamais mélanger une valeur avec une
    restatement ultérieure
  - la classification de durée (instant / 3M / 6M / 9M / 12M) pour ne
    jamais comparer un trimestre seul à un cumul year-to-date
  - **deux manques structurels identifiés avec certitude** (pas de simples
    tags alternatifs à ajouter) :
    - `shares_outstanding` est très souvent disponible **uniquement** en
      tant que fait de première page (`dei:EntityCommonStockSharesOutstanding`,
      espace de nommage `dei`, pas `us-gaap`) — fallback ajouté, utilisé
      seulement si aucun tag `us-gaap` ne matche
    - certains émetteurs (ex: Amazon) ne déclarent jamais de ligne "Total
      liabilities" à part entière — le bilan va directement à "Total
      liabilities and stockholders' equity". `total_liabilities` est alors
      **calculé** (`LiabilitiesAndStockholdersEquity - StockholdersEquity`),
      marqué explicitement `derived:...` dans `resolved_tags` pour rester
      traçable et distinct d'une valeur réellement déclarée
  - **limite documentée, pas corrigée** : les institutions financières
    (banques, assureurs) déposent un bilan non classifié en courant/non
    courant — `current_assets`/`current_liabilities` n'existe simplement
    pas pour elles, sous aucun tag. C'est aussi la pratique standard en
    finance de ne pas appliquer Altman Z / Beneish M / Piotroski F aux
    institutions financières pour cette même raison — ce n'est pas un bug
    à corriger, c'est une limite de portée de ces modèles.
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
  - **(trouvé sur un vrai 10-K NVIDIA, via l'artefact de diagnostic
    `debug/<TICKER>_item7_plaintext.txt`)** les références internes à un
    autre item **au milieu d'une phrase** : quasiment tout MD&A commence
    par "...should be read in conjunction with 'Item 1A. Risk Factors,'
    our Consolidated Financial Statements..." — un texte qui a exactement
    la forme d'un titre de section pour le détecteur de frontière, mais
    qui est en réalité au milieu d'un paragraphe. Sans distinction, le
    MD&A de NVIDIA (~40 000 mots réels) était coupé à 27 mots.
    `_find_next_real_header` n'accepte une frontière que si elle est
    précédée d'un saut de ligne (un vrai titre est seul sur sa ligne).
  - **(idem)** le bruit de pagination **à l'intérieur d'une phrase** : un
    numéro de page isolé et un renvoi "Table of Contents" répété,
    injectés par l'imprimeur financier à chaque saut de page, verbatim
    depuis un vrai 10-K NVIDIA : *"...cause our stock \n 13 \n\n Table of
    Contents \n\n price to decline."* Non filtré, ça pollue le diff et le
    sentiment de tokens parasites, et la ligne vide introduite trompe le
    découpage en paragraphes du Module 3 (une phrase réelle coupée en
    deux blocs). Supprimé avant tout traitement en aval.

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
  **Correction #2, trouvée sur un vrai 10-K Micron** : `SequenceMatcher`
  est basé sur la plus longue sous-séquence commune (LCS), qui n'aligne
  deux unités que si leur ordre relatif est préservé des deux côtés. Une
  section réorganisée (mêmes phrases, ordre différent — ce qui arrive
  quand un émetteur réécrit un sous-thème sans changer son contenu)
  déjoue ça : chaque phrase déplacée ressortait comme une suppression +
  un ajout, alors que rien n'avait réellement changé. Pire, ce n'est même
  pas toujours un seul opcode "replace" : un simple échange de deux
  phrases produit une séquence `insert` / `equal` / `delete` chez
  `difflib`, la paire réordonnée encadrant un bloc `equal` sans rapport.
  Fix : `_recover_reordered_matches()` fait une passe globale, après
  construction du diff, sur *tous* les segments "removed"/"added" du
  document — une intersection multiset (`collections.Counter`) retrouve
  le texte identique des deux côtés indépendamment de sa position ; les
  occurrences appariées deviennent "equal", seul le texte réellement
  différent (au-delà des doublons) reste marqué removed/added. Tests de
  régression : `test_reordered_sentences_within_a_block_are_recognized_as_equal`,
  `test_reordered_pair_straddling_an_equal_block_is_recognized`,
  `test_reordering_does_not_hide_a_genuine_change_alongside_it`.
  **Correction #3, trouvée sur un vrai rapport Netflix** : une phrase
  ressortait à la fois en "removed" et en "added", en lisant exactement
  le même texte. Deux filings publiés à des années d'écart peuvent
  différer sur quelque chose d'invisible à la lecture (espace
  insécable U+00A0, guillemet typographique vs droit, entité HTML
  décodée légèrement différemment) sans que le contenu ait réellement
  changé — le cas testé (`test_non_breaking_space_artifact_does_not_
  show_as_a_change`) reproduit exactement ça : l'espace insécable
  n'est pas touché par la normalisation `[ \t]+` existante dans
  `_split_paragraphs`, contrairement à un espace normal. Plutôt que de
  traquer chaque variante d'encodage une par une, `_recover_near_
  duplicate_matches()` ajoute une tolérance : après la passe exacte
  ci-dessus, les paires "removed"/"added" restantes sont comparées par
  un ratio `difflib` calculé **sur les mots** (pas les caractères —
  plus proche de "combien de mots diffèrent réellement" qu'un ratio par
  caractère, que l'ajout/suppression d'un seul mot peut fausser
  disproportionnellement). Au-delà de 95% de similarité (marge
  d'erreur ~5%, remontée depuis 99% initial à la demande explicite de
  l'utilisateur — un artefact d'encodage isolé ne suffisait pas à
  expliquer tous les cas rencontrés en pratique), la paire est
  considérée comme non-changée ; les appariements sont faits par ordre
  de similarité décroissante, chaque phrase utilisée au plus une fois
  (évite qu'une phrase supprimée avec deux candidats "added" proches
  s'apparie au mauvais). **Compromis assumé** : un changement factuel
  d'un ou deux mots (ex: un chiffre) au milieu d'une phrase assez
  longue peut en théorie repasser sous le seuil de 95% et disparaître
  — risque inhérent à toute tolérance en pourcentage fixe, plus large
  qu'à 99% mais toujours documenté et testé, pas caché. Tests de
  régression : `test_near_duplicate_sentence_is_recognized_as_equal`,
  `test_non_breaking_space_artifact_does_not_show_as_a_change`,
  `test_5_percent_threshold_tolerates_more_than_a_single_word_difference`,
  `test_genuinely_different_sentences_still_show_as_a_real_change`
  (garde-fou : un vrai changement de contenu reste affiché).
  **Correction #4, demande explicite de l'utilisateur** : même une
  phrase *réellement* réécrite (pas un artefact de reformatage) se
  lisait mal en superposant tout l'ancien texte barré puis tout le
  nouveau texte en vert — plus long à lire, et il fallait comparer les
  deux blocs à l'œil pour repérer ce qui avait vraiment changé.
  Demande : garder le texte tel quel, et montrer juste le mot
  supprimé, avec à côté le mot qui l'a remplacé entre parenthèses.
  `word_level_diff()` (fonction publique, réutilise le même mécanisme
  `SequenceMatcher`, appliqué un niveau plus bas — les mots d'une
  phrase, pas les phrases d'un document) calcule ce diff mot-à-mot.
  `_reconcile_replace_block()` l'utilise pour transformer un opcode
  "replace" local (une ou plusieurs phrases supprimées ET ajoutées au
  même endroit) en un seul segment `DiffSegment(kind="modified", text=
  ancien, replacement=nouveau)` quand les deux phrases sont assez
  proches pour être "la même phrase, réécrite" — un ratio `difflib` sur
  les mots ≥ 40% (`_MODIFIED_PAIR_RATIO_THRESHOLD`), volontairement
  plus bas que le seuil "quasi-identique" (95%, correction #3 :
  au-delà, c'est un non-changement, pas une réécriture) mais
  confortablement au-dessus du chevauchement de mots-outils ("le",
  "notre", "et"...) entre deux phrases sans rapport, mesuré en pratique
  autour de 20-30%. **Volontairement local à un seul opcode**, pas
  global comme les corrections #2/#3 : "cette phrase est devenue
  celle-là" n'a de sens qu'entre deux phrases adjacentes du même
  endroit du document — l'appliquer globalement risquerait d'apparier
  deux phrases sans rapport, situées à des endroits différents du
  texte, simplement parce qu'elles partagent quelques mots.
  Les décomptes "mots ajoutés/supprimés" d'un segment "modified" ne
  comptent que les mots réellement différents (via `word_level_diff`),
  pas la phrase entière de chaque côté. Tests de régression :
  `test_replaced_paragraph_becomes_a_single_modified_segment`,
  `test_modified_word_counts_reflect_only_the_changed_words`,
  `test_reordering_does_not_hide_a_genuine_change_alongside_it`
  (mis à jour), `test_single_p_tag_with_line_wrapped_sentences_diffs_
  at_sentence_level` (mis à jour). Le rendu (`html_renderer.py`, plus
  bas) affiche ce segment sous la forme `mot supprimé (mot ajouté)`,
  barré en rouge / vert entre parenthèses, dans la phrase d'origine
  plutôt qu'en deux blocs dupliqués.
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
- **`grouped_diff.py`** — regroupe le diff par sous-thème détecté au lieu
  d'une seule longue séquence de phrases. Constaté sur un vrai 10-K
  NVIDIA : l'Item 1A est organisé sous des sous-titres nommés ("Risks
  Related to Demand, Supply, and Manufacturing", etc.) ; quand la section
  est fortement réécrite (65,7% de similarité chez NVIDIA), le diff plat
  produisait des dizaines de pages de rouge-puis-vert ininterrompu, sans
  moyen de voir quel changement se rattache à quel sujet. `grouped_diff`
  détecte les lignes qui ressemblent à des titres (courtes, sans
  ponctuation finale, sur leur propre bloc — pas une phrase de prose ni
  une puce), aligne les deux séquences de titres (courant vs précédent)
  avec `difflib.SequenceMatcher` pour gérer sous-thèmes ajoutés/retirés/
  réordonnés, puis diffe le contenu de chaque sous-thème indépendamment.
  Résultat sur un cas réel NVIDIA reconstruit : **34 pages → 6 pages**
  pour un volume de changement comparable, avec un compteur
  "N sous-thème(s) sans changement" explicite pour ce qui n'est pas
  détaillé (jamais caché silencieusement).
  **Dégrade proprement** : un filing sans sous-titres internes (le cas le
  plus courant) produit un seul groupe non-titré dont le diff est
  strictement identique au diff plat d'avant — donc pas de chemin de code
  séparé à maintenir pour ce cas.

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
- **Traçabilité** — chaque rapport contient un lien direct vers la vraie
  page SEC EDGAR du filing source (`filing_index_url()` dans
  `data_layer/edgar_client.py`, pure construction d'URL, pas d'appel
  réseau), pour vérifier n'importe quel chiffre à la source.
- **Indicateur de complétude des données** — % des métriques XBRL
  attendues effectivement résolues pour la période, affiché explicitement
  dans le rapport plutôt que de laisser deviner en comptant les sections
  "indisponible" une par une.
- **`trend.py`** — analyse multi-année : au lieu de comparer juste
  N vs N-1, prend une liste de filings (ex: 5 ans de 10-K, du plus ancien
  au plus récent) et calcule un `ReportData` par exercice, chacun comparé
  à celui **immédiatement précédent dans la liste** (jamais une année
  sautée). Ne réimplémente rien : réutilise directement
  `build_report_data` en fenêtre glissante. Un score de 7/9 au Piotroski
  ne veut rien dire seul — savoir que c'était 4/9 deux ans plus tôt, si.
  **Comparaison sectorielle volontairement absente** de cette itération :
  elle demanderait un nouveau mode de récupération de données (plusieurs
  entreprises à la fois) qu'on ne peut pas valider contre de vraies
  données depuis cet environnement de dev — mieux vaut ne pas livrer un
  résultat non vérifié que de prétendre que c'est fiable.

### Mise en forme du rapport

Passe dédiée à la lisibilité du PDF final, faite en générant de vrais
rapports (fixtures réelles AAPL + dictionnaire Loughran-McDonald complet)
et en **relisant visuellement les pages rendues** (outil `Read` sur le
PDF, pas seulement la suite de tests) — c'est cette relecture, et non la
suite de tests qui restait verte, qui a trouvé deux vrais bugs de rendu
(détaillés plus bas).

- **`charts.py`** — mini-générateur de graphiques en barres. `xhtml2pdf`
  ignore silencieusement les techniques CSS habituelles pour des barres
  (div imbriquées en largeur %, largeur fixe en pt — testé et confirmé
  cassé avant d'écrire quoi que ce soit dessus) ; la seule technique
  fiable trouvée est un **SVG unique encodé en `data:image/svg+xml;base64,...`**
  dans une balise `<img>` (svglib, embarqué par `xhtml2pdf` justement pour
  ce cas). Les libellés, barres et valeurs sont des éléments `<text>`/`<rect>`
  du même SVG, dans le même repère — ça évite les problèmes d'alignement
  qu'on aurait entre du texte HTML et une image générée séparément.
- **Page de garde + résumé exécutif** — chaque rapport (single-période et
  tendance) commence par une page de garde (`page-break-before: always`)
  puis un résumé exécutif de 3-4 puces qui synthétise revenue/résultat
  net, nombre de red flags, direction du diff MD&A et de la tonalité — les
  avertissements (zone de détresse Altman, Beneish suspect) sont mis en
  évidence visuellement.
- **Ordre des sections, demande explicite de l'utilisateur** : la
  Sentiment (Loughran-McDonald), auparavant isolée tout à la fin du
  rapport, est remontée juste après le résumé du diff Risk Factors/MD&A
  ("Changements textuels vs période précédente") — les deux sont des
  analyses des deux mêmes sections textuelles, ça se lit mieux ensemble
  qu'après tout le reste. À l'inverse, le détail complet du diff (le
  texte réellement ajouté/supprimé/reformulé — la partie la plus longue
  et la plus dense à lire du rapport) est descendu tout en bas, juste
  avant le pied de page, dans une nouvelle section "Détail des
  changements textuels". Résultat : tous les chiffres à lecture rapide
  (financiers, red flags, résumé du diff, tonalité) regroupés en premier,
  le texte détaillé en dernier. `_render_text_diff()` a été scindée en
  `_text_diff_summary_html()` (juste la ligne similarité/compteurs) et
  `_text_diff_detail_html()` (le corps complet, avec le classement des
  sous-thèmes déjà décrit ci-dessus) pour permettre ce découpage sans dupliquer
  la logique. Test de régression :
  `test_report_section_order_moves_sentiment_up_and_diff_detail_to_the_end`.
- **Pagination** — pied de page "Page X / Y" répété sur chaque page, via
  la syntaxe propriétaire `@page { @frame footer_frame { ... } }` +
  `<pdf:pagenumber/>`/`<pdf:pagecount/>` de `xhtml2pdf` (pas du CSS
  standard).
- **Deux vrais bugs trouvés uniquement par relecture visuelle du PDF**
  (la suite de tests était verte pendant que ces deux bugs existaient — ce
  sont des bugs de rendu, pas de contenu de chaîne) :
  1. **Double échappement de "MD&A"** — le PDF affichait littéralement
     "MD&amp;A" au lieu de "MD&A". Cause : le titre `"MD&amp;A (Item 7)"`
     (déjà échappé) était passé à une fonction qui appelle `html.escape()`
     une seconde fois. Corrigé en passant un titre en clair aux points
     d'appel concernés.
  2. **Débordement des noms de tags XBRL** — un identifiant camelCase
     brut (ex: `RevenueFromContractWithCustomerExcludingAssessedTax`) sort
     de la page. Essayé et confirmé **non fonctionnel** dans `xhtml2pdf` :
     `word-break: break-all`, `<wbr>`, espace de largeur nulle U+200B
     (s'affiche comme un glyphe "tofu" visible, pas un point de coupure
     invisible). Seule technique confirmée qui fonctionne : insérer de
     vrais espaces aux frontières camelCase (`_humanize_xbrl_tag()`), ce
     qui laisse au moteur de mise en page de vrais points de coupure de
     mots.

### Refonte : deux documents, deux pages, sans couleur

Refonte demandée après lecture d'un vrai rapport NVDA. Le rapport ne
sortait plus un seul PDF long mais **deux documents** :

- **`<TICKER>.pdf` — le rapport principal, exactement 2 pages.**
  Page 1 : le résumé exécutif seul (la lecture de l'IA sur les
  sous-thématiques retenues, plus la liste de ces sous-thématiques et
  la façon dont elles ont été choisies). Page 2 : tous les chiffres —
  red flags, % de changement par sous-thématique, chiffres clés,
  tonalité.
- **`<TICKER>_detail.pdf` — le rapport « détail », non plafonné.** Tout
  le texte réellement ajouté/supprimé/reformulé, sous-thématique par
  sous-thématique, les retenues d'abord. Volontairement sans limite de
  pages : la plafonner reviendrait à supprimer du texte modifié, ce que
  ce projet ne fait jamais silencieusement.

**Les 2 pages sont une garantie, pas une cible.** La feuille de style
seule ne suffit pas : un filing avec 10 sous-thématiques modifiées et
plusieurs critères Piotroski en échec déborde naturellement sur une 3e
page. `render_pdf_fitted(html, max_pages=2)` rend le PDF, **compte les
vraies pages du PDF produit**, et re-rend avec une feuille de style de
plus en plus compacte jusqu'à ce que ça tienne. Le choix de l'utilisateur
entre « compacter, jamais déborder » et « laisser filer » était explicite.
Test de régression : `test_main_report_fits_two_pages_even_in_the_worst_case`
construit le pire cas réaliste (le plafond de 10 sous-thématiques, des
intitulés longs, un résumé trop long), **vérifie d'abord qu'il déborde
vraiment** au rendu naturel — sinon le test ne prouverait rien — puis que
la version ajustée fait exactement 2 pages.

Si même l'étape la plus compacte déborde, la fonction renvoie ce rendu-là
plutôt que de lever : un rapport un peu long est plus utile au lecteur
que pas de rapport, et `page_count()` reste disponible pour vérifier.

**Aucune couleur.** Tout ce que la feuille de style distinguait par la
couleur passe par la graisse, un filet, un retrait ou un marqueur
typographique : zone Altman en toutes lettres (« zone détresse »),
Beneish signalé en gras, texte supprimé barré, texte ajouté souligné.
Rien n'est perdu à la lecture en noir et blanc ou à l'impression. Test :
`test_report_uses_no_colour_anywhere` scanne le HTML rendu des deux
documents et rejette toute couleur hexadécimale dont R, G et B ne sont
pas égaux — un futur ajustement de style ne peut pas réintroduire du
vert/rouge sans faire échouer la suite.

**Police.** L'utilisateur demandait Seravek. C'est une police
propriétaire Apple, livrée avec macOS : absente des runners GitHub
Actions, et sa licence n'autorise pas à l'embarquer dans ce dépôt. Le
choix a été posé explicitement ; **Lato** a été retenue, la plus proche
en esprit parmi les humanistes libres, installable via `apt`
(`fonts-lato`, ajouté au workflow) donc identique en CI et en local.
Détail qui compte : `xhtml2pdf` **ne consulte pas la base de polices
système** — un simple `font-family: Lato` retombe silencieusement sur
Helvetica dans le PDF (vérifié avec `pdffonts`). Seule une règle
`@font-face` pointant sur un vrai `.ttf` embarque la police
(`report/fonts.py`), ce que `pdffonts` confirme ensuite avec `emb yes`.
Si le fichier est absent, `font_face_css()` renvoie `""` et la pile
générique s'applique : le rapport se génère quand même, simplement dans
la police par défaut.

### Rendu du diff par sous-thème : condenser sans jamais cacher

Retour utilisateur sur un vrai rapport Micron généré en conditions
réelles : le rendu par sous-thème (voir `grouped_diff.py` ci-dessus)
restait trop long. Deux causes, corrigées dans `_render_text_diff()`
(`html_renderer.py`), toutes deux en préservant le principe déjà en place
dans le projet — condenser, jamais supprimer silencieusement une info :

1. **Sous-thème entièrement ajouté ou supprimé** — son "diff" est par
   construction 100% d'un seul sens (tout removed, ou tout added) ; en
   reproduire le texte intégral n'apporte rien que le titre + la note de
   statut ("nouvelle sous-thématique" / "sous-thématique supprimée") ne
   disent déjà, et gonfle le rapport pour rien. Ces groupes n'affichent
   plus que leur ligne titre + compteur, jamais leur corps.
2. **Trop de sous-thèmes réellement réécrits ("matched") à la fois** —
   un seul filing peut réorganiser une douzaine de sous-thèmes en même
   temps. Seuls les `_MAX_DETAILED_GROUPS` (5) les plus modifiés — classés
   par nombre de mots changés (`added_word_count + removed_word_count`,
   déjà calculé, même proxy d'"importance" qu'ailleurs dans le projet) —
   sont montrés en détail avec leur texte complet. Les autres gardent une
   ligne compacte (titre + compteur), et une note en bas du rapport
   ("N sous-thème(s) résumé(s) sans le texte complet") explique pourquoi,
   plutôt que de les faire disparaître sans trace.

Tests de régression :
`test_wholesale_removed_subtheme_does_not_reproduce_its_text`,
`test_wholesale_added_subtheme_does_not_reproduce_its_text`,
`test_only_the_most_changed_matched_subthemes_show_full_text`.

### Rendu d'une phrase réécrite : diff mot-à-mot en ligne, pas deux blocs dupliqués

Suite explicite à la demande utilisateur ci-dessus (Module 3, correction
#4) : un segment `DiffSegment(kind="modified", ...)` (une phrase
reconnue comme réécrite, pas wholesale remplacée) ne s'affiche plus en
deux blocs empilés (tout l'ancien texte barré en rouge, puis tout le
nouveau en vert). `_modified_segment_html()` construit à la place un
diff mot-à-mot en ligne, dans la phrase d'origine : les mots inchangés
restent en texte normal à leur place, les mots supprimés sont barrés en
rouge, et les mots ajoutés apparaissent juste à côté en vert **entre
parenthèses** — exactement le format demandé. Exemple concret (test
`test_renders_modified_segment_as_inline_word_diff`) :

> Revenue ~~fell~~ (grew) due to strong growth this year.

Les compteurs "ajout(s)" / "suppression(s)" / "reformulation(s)" (dans
l'en-tête et par sous-thème) comptent désormais les trois séparément —
un segment "modified" n'est ni un pur ajout ni une pure suppression, et
le confondre avec l'un ou l'autre aurait sous-compté les changements
réels ou, pire, fait passer un sous-thème entièrement composé de
reformulations pour "sans changement" (bug attrapé et corrigé pendant
le développement : la condition de saut d'un groupe "inchangé" ne
regardait à l'origine que `g_added`/`g_removed`, jamais `g_modified`).

Tests de régression :
`test_renders_modified_segment_as_inline_word_diff`,
`test_renders_diff_segments_for_mdna` (garde-fou : deux phrases sans
rapport, en dehors du seuil d'appariement, restent bien deux blocs
distincts removed/added, pas un "modified" forcé).

### Tests

59 tests, dont deux tests d'intégration bout-en-bout qui font tourner
**Module 1 → Module 5** sur les vraies fixtures (XBRL + HTML) jusqu'à un
vrai PDF généré (vérifie les octets magiques `%PDF-`) — un pour un rapport
single-période, un pour une tendance sur 2 exercices. Avec cette fixture
précise, les Red Flags ressortent "indisponibles" (tags XBRL manquants,
données trimestrielles) — comportement voulu, pas un échec de test. Les
deux bugs de rendu ci-dessus ont chacun leur test de régression
(`test_mdna_heading_is_not_double_escaped`,
`test_long_xbrl_tag_names_are_humanized_for_wrapping`), écrits après coup
pour figer le comportement corrigé.

```bash
python -m pytest tests/report -v
```

### Utiliser le module

```python
from equity_analyzer.report import (
    build_report_data, render_html, save_pdf,
    build_trend_analysis, render_trend_html,
)

# Rapport sur une période
report = build_report_data(filing, prior_filing, lm_dictionary)
save_pdf(render_html(report), "rapport.pdf")

# Tendance multi-année (filings triés du plus ancien au plus récent)
trend = build_trend_analysis([filing_2021, filing_2022, filing_2023], lm_dictionary)
save_pdf(render_trend_html(trend), "tendance.pdf")
```

## Module 6 — Sélection + lecture IA (`theme_selection.py` + `ai_summary.py`, opt-in)

### Le flux en trois temps

Refonte demandée explicitement : l'IA ne doit plus seulement commenter
après coup ce que Python a diffé, elle doit d'abord **choisir sur quoi
travailler**.

1. **Python liste** les sous-thématiques réellement présentes dans le
   filing (`list_subthemes`, pur, sans réseau — réutilise la détection
   de titres du diff lui-même, donc les deux ne peuvent pas diverger sur
   ce qu'est une sous-thématique).
2. **L'IA choisit** jusqu'à `MAX_SELECTED_THEMES` (**10**) de ces
   intitulés : ceux qu'un analyste couvrant cette société surveillerait
   réellement — prix, volumes, marges, demande, concentration client,
   contraintes de capacité, risques réglementaires bloquants — en
   dépriorisant le boilerplate juridique générique
   (`select_key_subthemes`).
3. **Python diffe**, puis **l'IA revient** écrire le résumé exécutif sur
   cette sélection (`ai_summary.py`), en reprenant explicitement les
   « points attendus » sous-thématique par sous-thématique.

Ce que ça remplace : le classement par nombre de mots changés. Un mauvais
proxy de la pertinence analyste — une section juridique fortement
réécrite passait devant un changement de trois mots sur le guidance de
prix.

**Le modèle ne peut que CHOISIR dans la liste que Python lui donne.**
Tout intitulé renvoyé qui n'existe pas dans la liste d'entrée est écarté
(`_parse_selection`) : une sous-thématique hallucinée ou reformulée ne
peut pas entrer dans le pipeline — au pire la sélection revient plus
courte, jamais fausse. Test :
`test_a_hallucinated_heading_is_dropped_not_passed_through`.

**Les non-retenues ne sont pas jetées.** `apply_theme_selection` *marque*
les groupes, il n'en supprime aucun : le total Item 1A continue de les
compter, le rapport principal indique combien il y en a, et le rapport
« détail » les contient toutes. Une sélection vide sélectionne zéro
sous-thématique et non « toutes » — c'est une vraie réponse du sélecteur,
pas un cas à inverser silencieusement.

**Dégrade proprement sans clé API** : pas de clé, réseau en panne,
réponse illisible → repli documenté sur les sous-thématiques les plus
volumineuses, et le rapport **écrit noir sur blanc** comment la sélection
a été faite (`ThemeSelection.reason`, `ai_selected`) plutôt que de
laisser croire qu'une IA a tranché. Le rapport reste générable
gratuitement et hors-ligne.

**Coût** : deux appels au lieu d'un. La passe de sélection est courte
(elle ne reçoit que des intitulés et des tailles, pas de texte) — compter
de l'ordre de 0,4 à 0,8 centime par rapport au total sur Haiku 4.5.

### Ce qu'il fait

Ajoute, en tête du rapport, un **verdict directionnel** (plutôt bullish /
plutôt bearish / mitigé / neutre) sur la balance des changements du
filing, généré par l'API Claude à partir des seules données **déjà
calculées** par les modules 1-5 — pas une nouvelle analyse indépendante.
La réponse attendue tient en trois temps : le verdict en une phrase, puis
2-3 phrases du filing **citées verbatim** comme preuves (avec pourquoi
chacune penche d'un côté), puis une nuance honnête sur ce qui va en sens
inverse.

Le cadrage a bougé **trois fois**, à chaque fois sur demande explicite de
l'utilisateur après lecture d'un vrai rapport généré, jamais supposé :

1. **v1 — synthèse factuelle stricte**, zéro interprétation, zéro
   direction. Choisie explicitement parmi trois options proposées.
2. **v2 — interprétation ancrée** : relier les signaux entre eux et en
   tirer une lecture, mais toujours sans verdict directionnel.
3. **v3 (actuelle) — verdict bullish/bearish concret.** Après lecture
   d'un vrai rapport NVDA, l'utilisateur a constaté que la synthèse
   restait descriptive (des compteurs et des scores de tonalité) là où
   l'attente réelle était : lire ce qui a été ajouté et supprimé, le
   remettre en contexte, et **trancher**. Exemple donné par l'utilisateur
   pour cadrer l'attendu : si un émetteur annonce que les prix DRAM
   seront ajustés à la hausse de 5%, c'est **fortement bullish** compte
   tenu de ce que ça implique sur le revenu et la marge — et ce type de
   phrase doit ressortir citée, pas noyée dans un compteur. Ça lève
   explicitement l'interdiction d'avis directionnel des v1/v2.

**Ce qui n'a PAS bougé** sur les trois versions, reformulé à chaque fois
plutôt que reconduit ou abandonné silencieusement :

- **Ancré uniquement sur les données du rapport, jamais sur la
  connaissance que le modèle a de cette société.** Le prompt interdit
  toujours d'importer un fait sur l'émetteur venu d'ailleurs (autres
  filings, actualité, cours de bourse, mémoire d'entraînement) —
  invérifiable et non rattachable à ce filing précis. En revanche
  **raisonner économiquement sur un fait fourni** (une hausse de prix
  annoncée implique plus de revenu) est désormais explicitement demandé :
  la limite est de ne pas importer de faits nouveaux, pas de s'interdire
  de réfléchir.
- **Jamais de recommandation d'achat/vente ni d'objectif de cours.** Un
  verdict sur *la balance des changements de ce filing* est demandé ;
  dire d'acheter ou vendre le titre reste une affirmation d'une autre
  nature, que l'outil ne fait pas. Cette limite a été reconfirmée au
  moment même où l'interdiction d'avis directionnel était levée, pas
  emportée avec elle.

Deux tests distincts verrouillent ça dans le texte du prompt système —
séparés exprès, pour qu'assouplir l'un ne puisse jamais assouplir l'autre
en silence : `test_system_prompt_asks_for_a_bullish_bearish_verdict_with_verbatim_evidence`
et `test_system_prompt_still_forbids_buy_sell_advice_and_external_company_knowledge`.

### Le vrai blocage corrigé en v3 : le MD&A n'était pas envoyé

Demander au modèle de **citer** les phrases les plus significatives était
impossible à satisfaire en l'état : `build_prompt_context` envoyait le
MD&A sous forme de **compteurs de mots uniquement, sans aucun texte** —
or c'est précisément là que vit l'information concrète (prix, marges,
demande). Le modèle ne pouvait pas citer ce qu'il ne recevait pas, d'où
des synthèses qui ne parlaient que de compteurs et de scores. Corrigé :

- Les **deux** sections (Risk Factors et MD&A) envoient maintenant de
  vraies phrases modifiées, verbatim, étiquetées `AJOUTE` / `SUPPRIME` /
  `REFORMULE` (avec l'ancienne et la nouvelle version pour une
  reformulation). Une **suppression** compte autant qu'un ajout : une
  société qui retire un risque qu'elle mentionnait l'an dernier envoie un
  signal, et le prompt demande de le peser comme tel.
- La troncature des extraits passe de 160 à **400 caractères** : un
  extrait coupé en plein milieu est activement nuisible quand on demande
  une citation verbatim (soit elle est citée incomplète, soit le modèle
  reconstruit la fin, c'est-à-dire l'invente).
- Le budget d'extraits est plafonné (14 par section, 3 par sous-thème)
  et **priorisé** : une phrase portant une **grandeur chiffrée**
  (pourcentage, montant, points de base, multiple) passe avant du
  boilerplate non chiffré — c'est exactement la forme de l'exemple DRAM.
  Première version de cette heuristique : n'importe quel chiffre (`\d`),
  ce qui marchait mal sur du vrai texte (une année « fiscal 2026 », un
  numéro de section ou d'item sont des chiffres sans grandeur, donc tout
  se retrouvait à égalité et le départage par longueur donnait le budget
  au boilerplate le plus long — attrapé par un test, corrigé en ne
  matchant que les vraies grandeurs).
  L'heuristique décide **quoi envoyer**, jamais quel doit être le verdict :
  un mauvais classement coûte au modèle une citation candidate, il ne peut
  pas lui en faire inventer une.

**Délibérément séparé de `build_report_data()`**, jamais appelé
automatiquement : contrairement à tous les autres modules, un appel réel
est facturé, nécessite le réseau et une clé API, et n'est pas
déterministe (la suite `pytest` reste donc 100% hors-ligne). Un appelant
doit explicitement activer cette section :

```python
from equity_analyzer.report import (
    attach_ai_summary, attach_theme_selection,
    build_report_data, render_detail_html, render_html, save_pdf,
)

report = build_report_data(filing, prior_filing, lm_dictionary)

# Passe 1 : quelles sous-thématiques valent le coup (repli documenté sans clé)
report = attach_theme_selection(report, api_key=os.environ.get("ANTHROPIC_API_KEY"))
# Passe 2 : le résumé exécutif, écrit sur cette sélection
report = attach_ai_summary(report, api_key=os.environ["ANTHROPIC_API_KEY"])

save_pdf(render_html(report), "rapport.pdf", max_pages=2)   # 2 pages garanties
save_pdf(render_detail_html(report), "rapport_detail.pdf")  # non plafonné
```

Si la clé est absente, ou si l'appel échoue pour n'importe quelle raison
(réseau, quota, réponse inattendue), la section revient `unavailable` avec
la raison — comme toutes les autres sections optionnelles du projet — sans
jamais faire échouer la génération du rapport. Si `attach_ai_summary`
n'est jamais appelé, la section n'apparaît tout simplement pas dans le
rapport (pas de placeholder "indisponible" affiché par défaut).

### Choix du modèle

**Une clé API n'est liée à aucun modèle** : le modèle se choisit à
**chaque appel**, pas au moment de créer la clé. La même clé peut donc
servir indifféremment Haiku ou Sonnet.

Modèle par défaut : `claude-haiku-4-5-20251001` (rapide et économique).
Surchargeable de trois façons, par ordre de priorité : le paramètre
`model=` de `attach_ai_summary`, la variable d'environnement
`ANTHROPIC_MODEL`, sinon le défaut. Depuis le workflow GitHub Actions,
c'est une **liste déroulante `ai_model`** dans le formulaire "Run
workflow" — rien à éditer dans le code.

À noter honnêtement : le défaut Haiku a été choisi pour la tâche de la
**v1** (reformuler des faits déjà fournis, sans raisonnement poussé). La
v3 demande nettement plus — peser des phrases les unes contre les autres
et trancher. Si les verdicts paraissent superficiels ou mal calibrés,
c'est le premier levier à essayer : basculer sur Sonnet dans la liste
déroulante. Ce point n'a pas été mesuré comparativement sur de vrais
rapports (ça demanderait de lancer les deux modèles sur les mêmes filings
et de juger les sorties), donc c'est une **piste raisonnée, pas un
résultat vérifié**.

**Coût** : de l'ordre de 0,3 à 0,6 centime de dollar par rapport sur
Haiku 4.5 (prompt ~1000-2500 tokens selon le volume d'extraits, réponse
~300-600 tokens, tarifs $1/$5 par million entrée/sortie). En hausse par
rapport aux ~0,1-0,2 centime des v1/v2 : le prompt envoie maintenant de
vraies phrases (et non des compteurs), et la réponse contient des
citations verbatim. Pour les 15 tickers du workflow, de l'ordre de 5 à 10
centimes par run. Sur Sonnet 4.5, compter environ 3x plus.

**Activable à la demande depuis le workflow GitHub Actions**
(`.github/workflows/test-real-sec-api.yml`) : une case à cocher
`use_ai_summary` (décochée par défaut) contrôle si `ANTHROPIC_API_KEY`
est même transmise au script — même si le secret est configuré dans le
repo, un run où la case reste décochée ne déclenche jamais d'appel
payant. La clé doit être ajoutée comme secret du repo (Settings →
Secrets and variables → Actions → New repository secret,
`ANTHROPIC_API_KEY`), pas saisie en clair dans une case de saisie. Le
log du run indique quel modèle a réellement produit les synthèses.

### Tests

23 tests, tous hors-ligne : `build_prompt_context` et `select_key_subthemes` (purs, sans réseau) et
le traitement des réponses succès/échec via des réponses HTTP simulées.
Le chemin d'erreur HTTP réel (401 avec une fausse clé) a été vérifié
manuellement contre la vraie API pendant le développement — network
access à `api.anthropic.com` fonctionne depuis cet environnement de dev
(contrairement à `data.sec.gov`), mais par cohérence avec le reste du
projet (`EdgarClient` n'a aucun test réseau non plus), ce n'est pas
gardé comme test automatisé.

```bash
python -m pytest tests/report/test_ai_summary.py -v
```

## Statut : projet complet, validé contre de vraies données

Les 5 modules principaux (+ le module 6 optionnel) sont terminés et
testés (201 tests). Le pipeline a été validé
contre la vraie API SEC EDGAR (voir `.github/workflows/test-real-sec-api.yml`
et `scripts/test_real_sec_pipeline.py`) sur 15 grandes capitalisations de
secteurs variés (tech, finance, énergie, santé, biens de consommation,
industriel). Quatre vrais bugs d'extraction HTML ont été trouvés et
corrigés grâce à ce test (voir Module 1 ci-dessus : mots coupés par une
balise, entités HTML numériques non décodées, référence croisée à un
autre item au milieu d'une phrase, bruit de pagination au milieu d'une
phrase — les deux derniers trouvés sur un vrai 10-K NVIDIA). D'autres écarts réels et attendus
subsistent (tags XBRL non couverts selon l'émetteur, ex: `sga_expense`
manquant chez Microsoft/Amazon) — le Module 5 les affiche explicitement
comme "indisponible" plutôt que de planter ou de deviner.
