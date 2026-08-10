# Equity Analyzer

Un ticker en entrée, **un** PDF de trois pages en sortie.

| | Contenu | D'où ça vient |
|---|---|---|
| **Page 1** | La lecture du dernier earnings call par Claude, écrite **contre le consensus** sur lequel le trimestre était attendu **et contre ce que la direction avait promis au trimestre précédent** | transcript Alpha Vantage + consensus BPA + engagements du trimestre précédent + API Claude |
| **Page 2** | Le même call disséqué : esquives, concessions, signaux prospectifs hors communiqué | seconde passe Claude sur la moitié Q&A |
| **Page 3** | Les red flags annuels (Altman, Beneish, Piotroski) et la tonalité Loughran-McDonald | SEC EDGAR (XBRL + 10-Q) |

L'ordre est l'argument : les pages 1 et 2 sont toutes deux l'actualité du
trimestre et vont ensemble, la seconde repassant sur la session que la
première ne fait qu'effleurer. La page 3 est ce qui bouge le plus
lentement, la toile de fond, donc elle vient en dernier plutôt que de
couper le call en deux. **Deux pages** quand il n'y a pas de Q&A à
disséquer : une page de titres vides serait pire que pas de page.

```bash
TICKER=AAOI ANTHROPIC_API_KEY=... ALPHAVANTAGE_API_KEY=... python scripts/rapport.py
# -> rapports/AAOI.pdf
```

Sans terminal : onglet **Actions** du dépôt → *Rapport (un ticker)* →
« Run workflow », saisir le ticker. Le PDF sort en artefact du run.

## Le principe

**On lit le call, on ne le diffe pas.** Une version antérieure de ce projet
comparait deux dépôts SEC consécutifs, parce qu'un filing est largement
copié-collé d'un trimestre sur l'autre et que les éditions sont le signal.
Un earnings call ne s'écrit pas comme ça : la première comparaison réelle
de deux calls Microsoft est revenue à **7 % de recouvrement de phrases**.
La direction réécrit son script chaque trimestre, donc un diff annonce que
tout a changé, ce qui est vrai et sans valeur.

**Et on ne le lit pas dans l'absolu.** « Le chiffre d'affaires progresse de
14 % » est un fait sans direction. « Le chiffre d'affaires progresse de 14 %
contre un consensus à 11 %, et la direction a passé le call à expliquer
pourquoi ça ne se reproduira pas » est une position. Le consensus de BPA du
trimestre lu, **plus le palmarès des trimestres précédents**, partent donc
dans le prompt avec le transcript. Une société qui bat de deux centimes pour
la huitième fois d'affilée a **tenu** les attentes, elle ne les a pas
dépassées, et sans le palmarès le modèle ne peut pas faire la différence.

**Deux repères, pas un.** Le consensus dit ce que le **trimestre** était
censé gagner. Il ne dit rien de ce que la **société** avait promis de
faire. Tant qu'il était seul, tout le reste du discours de la direction,
capex, marge visée, guidance de revenu, calendrier produit, arrivait sans
point de comparaison : le modèle pouvait rapporter un **niveau**, jamais
un **changement**. Trouvé sur un vrai rapport MSFT, qui citait deux fois
le capex et écrivait « désormais ajusté à *approximately $175 billion* »
sans jamais dire ajusté depuis quoi. Un programme d'investissement relevé
est souvent ce qu'un call contient de plus lourd, et il atterrissait comme
un fait parmi d'autres.

Le call du trimestre précédent est donc relu par une passe dédiée qui en
extrait les **engagements chiffrés** (métrique, valeur telle qu'annoncée,
période, citation), et cette liste part dans le prompt de lecture comme
seconde base de comparaison. Une passe séparée plutôt que le vieux
transcript recollé au prompt, pour deux raisons : envoyer huit mille mots
pour livrer une poignée de chiffres est la mauvaise forme, et surtout
extraire est un **travail d'une autre nature** que lire, avec une seule
bonne réponse, vérifiable dans le texte et sans jugement. La lecture
reçoit des faits, pas une botte de foin.

Cette passe n'interprète rien et ne classe rien : décider si un
changement compte se fait un étage plus haut, là où le trimestre courant
est aussi sous les yeux.

**Si le trimestre précédent manque, la recherche recule, mais elle dit de
combien.** Un seul trimestre jamais publié coûtait sinon toute la base.
Reculer sans le dire serait pire que de ne rien avoir : entre une base de
T-2 et le call lu, il y a un call que personne n'a vu, donc un chiffre
modifié à ce moment-là et simplement reconduit aujourd'hui ressemblerait
trait pour trait à l'annonce du jour. La distance voyage donc avec les
données, et au delà de 1 le bloc porte un avertissement qui interdit
explicitement d'attribuer l'écart au call lu. Le recul est borné à 3
trimestres : chaque pas coûte une requête sur les 25 du jour, et une base
vieille d'un an n'a plus grand rapport avec « est-ce que ça a changé
aujourd'hui ». Un refus de quota arrête la marche immédiatement, comme
pour la recherche du call principal.

Et quand il n'y a pas de base de comparaison
(pas de call précédent, quota épuisé, société qui n'a jamais chiffré
d'engagement), le bloc **le dit au modèle** au lieu de disparaître. C'est
le point le plus important du dispositif : un modèle sans repère ne
conclut pas que le repère est inconnu, il le remplit de mémoire, et
« en hausse par rapport au trimestre précédent » écrit de mémoire est
indistinguable sur la page de la même phrase écrite d'après le texte.

**Coût** : un appel Alpha Vantage de plus (3 sur les 25 quotidiens) et un
appel Claude de plus, sur un budget plus petit que celui de la lecture.

**Ce qui manque est écrit, jamais laissé en blanc.** Pas de consensus, pas
de 10-K, pas de MD&A : chaque absence apparaît sur la page sous forme d'une
phrase qui dit laquelle et pourquoi. Une case vide et une case incalculable
se ressemblent sur le papier, et un lecteur lira le blanc comme « rien à
signaler ».

**Une seule exception à ça : la lecture elle-même.** La page 1 *est* la
lecture. Un rapport dont la page 1 se dégrade en excuse, composée dans la
même typo qu'une vraie analyse, est pire que pas de rapport : le script
s'arrête sans écrire de PDF.

## La page 2 : la Q&A disséquée

Le transcript arrive déjà coupé en deux, parce que les remarques
préparées et la Q&A sont deux actes différents. Les premières sont
écrites, relues par les juristes et répétées : elles disent ce que la
direction a **choisi** de dire. La Q&A est le seul endroit où on lui
pose des questions qu'elle n'a pas choisies. Ce qui s'y esquive, et ce
qui s'y échappe, n'est ni dans le communiqué ni ailleurs.

Une seconde passe la lit seule et rend du **JSON**, pas de la prose. La
page 1 est en prose parce qu'on la lit ; ici, « quel analyste a demandé
quoi, ce qui a réellement été rendu, quelle est la gravité de l'écart »
a une forme de tableau, et un modèle à qui on demande de la prose
l'aplatit en un paragraphe qui se lit bien et ne se parcourt pas. La
page 2 le remet en forme :

| Section | Ce qu'elle contient |
|---|---|
| **Les esquives** | Analyste, question, ce qui était demandé, ce qui a été rendu, gravité. « Grave » = une information chiffrée précise demandée et refusée. |
| **Valeur prospective hors communiqué** | La partie la plus utile : ce qui engage l'avenir et ne figurait pas dans le communiqué, avec le contexte où c'était glissé. |
| **Les concessions** | Ce que la direction admet, citation courte à l'appui. |
| **Thèmes récurrents** | Combien d'analystes sont revenus dessus. Une mesure de ce que le marché n'a pas compris, ou n'a pas cru. |
| **Chiffres à vérifier** | Ceux que le modèle soupçonne d'être mal transcrits. |

**Une troisième page, pas un document séparé.** Une première version
livrait ça en PDF joint. Deux fichiers pour un trimestre, c'est deux
choses à retrouver, et la seconde se perd : le budget de pages est passé
de deux à trois pour faire de la place, plutôt que la section d'être
rognée pour tenir.

**Trois pages est désormais une cible avec un filet, pas une garantie**,
et c'est un compromis assumé. La longueur de la page 2 est une propriété
du call : une session qui esquive huit questions fait huit lignes.
L'ancienne promesse des deux pages tenait parce que tout y était borné ;
ça ne l'est plus. Mesuré : une session complète (5 esquives, 4
concessions, 5 signaux, plus thèmes récurrents et chiffres douteux) fait
quatre pages au naturel et revient à trois une fois compactée, donc **la
compaction est le chemin normal ici**, pas un cas d'urgence. La marge est
mince : quatorze constats tiennent, quinze non. Au delà, même la feuille
la plus dense déborde et le rapport passe à quatre pages plutôt que de
perdre une ligne, la compaction continuant malgré tout d'économiser une
feuille. Un rapport un peu long vaut mieux qu'un rapport auquel manque la
ligne qui comptait.

**Le compte annoncé est mesuré sur le PDF écrit**, pas déduit du budget
demandé, et ça a servi tout de suite : le premier vrai run TSLA du
document plié est sorti à quatre pages parce que l'échelle de compaction
s'arrêtait trop tôt, et il l'a dit. Les fixtures de mise en page
mesuraient le nombre de lignes et pas la prose qui les remplit, donc les
tests annonçaient trois pendant que la production en livrait quatre : ils
rendaient honnêtement deux documents différents. Les fixtures ont été
recalées sur la longueur réelle des réponses du modèle, et l'échelle a
gagné un dernier barreau à 7,5 pt, atteint seulement quand tout le reste
a échoué.

**Jamais fatal.** Le rapport principal est déjà calculable quand cette
passe démarre : un échec ici se dégrade en une ligne de log et les deux
pages sortent quand même.

**Trois vérifications indépendantes du trimestre.** EDGAR dit lequel
c'est, `verify_against_declared` lit l'ouverture du call, et cette passe
rend la période que le modèle croit avoir lue. La troisième ne prime
jamais sur les deux autres : elle ne parle que si elle est en désaccord,
parce que trois sources d'accord ne valent pas la peine d'être
imprimées et deux en désaccord valent beaucoup.

**Coût** : un second appel Claude, du même ordre que le premier.

## Ce qui est garanti dans le document

- **Deux pages garanties, une troisième bornée.** Sans Q&A à disséquer, le
  document fait exactement deux pages : la coupure est explicite, le texte
  de la page 1 est plafonné et tronqué à une frontière de phrase si le
  modèle déborde, et `save_pdf(..., max_pages=3)` **rend le PDF, compte ses
  vraies pages** et recompose avec une feuille de style de plus en plus
  dense jusqu'à ce que ça tienne. Avec une Q&A, ce mécanisme ramène la
  charge d'un vrai run à trois pages, et une session hors norme en fait
  quatre plutôt que de perdre une ligne : voir plus haut, c'est le seul
  endroit du document où le nombre de pages est une cible et non une
  garantie.
- **Aucune couleur.** Zone Altman en toutes lettres, Beneish en gras, mises
  en garde au filet : rien n'est perdu en noir et blanc ou à l'impression.
  Un test rejette toute couleur hexadécimale dont R, G et B ne sont pas
  égaux, donc un futur ajustement de style ne peut pas réintroduire du
  rouge et du vert en silence.
- **Aucun tiret cadratin.** Utilisés comme ponctuation, c'est un marqueur de
  texte généré. Bannis du gabarit **et** de la réponse du modèle, par deux
  tests séparés pour qu'assouplir l'un ne puisse pas assouplir l'autre. Les
  traits d'union dans un mot ou un nom propre (`10-K`, `Loughran-McDonald`,
  `Z-Score`) ne sont pas touchés.
- **Rien qui ressemble à une slide.** Pas d'encadré teinté, pas de badge,
  pas d'emoji. La page est composée comme une note d'analyste.
- **Police Lato**, embarquée dans le PDF via une vraie règle `@font-face`
  pointant sur un `.ttf`. `xhtml2pdf` **ne consulte pas la base de polices
  système** : un simple `font-family: Lato` retombe silencieusement sur
  Helvetica (vérifié avec `pdffonts`). Si le fichier est absent, la pile
  générique s'applique et le rapport se génère quand même.

## Ce que coûte un run

| | Coût |
|---|---|
| SEC EDGAR | 4 requêtes, gratuit, sans compte. Client throttlé à 5 req/s, sous la limite SEC de 10/s. |
| Alpha Vantage | 2 des 25 requêtes quotidiennes du tier gratuit (1 transcript, 1 consensus). 2 à 4 de plus seulement si les trimestres les plus récents ne sont pas encore publiés. Un transcript déjà récupéré sort du cache disque et ne coûte rien. Compter ~15 s d'attente entre deux requêtes, voir ci-dessous. |
| Anthropic | 1 appel, de l'ordre de 10 000 tokens en entrée et 900 en sortie. Compter 3 à 6 centimes sur Sonnet, 5 à 10 fois moins sur Haiku. |

**Le tier gratuit limite aussi le débit, pas seulement le volume**, et les
deux se ressemblent dans la réponse. Deux modules différents interrogent
Alpha Vantage (le transcript et le consensus) et un premier run réel les
a envoyés dans la même milliseconde, ce qui a valu un « please consider
spreading out your free API requests more sparingly » sur le second. La
limite appartient au **fournisseur**, pas à l'un ou l'autre appelant :
`data_layer/alpha_vantage.py` porte donc un throttle partagé que les deux
traversent, sur le même principe que le throttle SEC déjà présent dans
`EdgarClient`. Un refus est en outre **réessayé une fois** après une
pause, parce qu'une limite de débit et un quota journalier épuisé
arrivent avec le même HTTP 200 et une prose qui se recoupe : attendre et
redemander est la seule réponse honnête à cette ambiguïté.

**Le transcript est demandé avant le consensus**, et l'ordre porte. Le
budget du jour est partagé entre les deux et ils ne pèsent pas pareil :
sans transcript il n'y a pas de page 1 donc pas de rapport, alors qu'un
consensus manquant se dégrade en une phrase imprimée. La ressource rare
va d'abord à la requête critique. Et c'est le transcript qui dit **de
quel trimestre** le call est, donc demander le consensus ensuite revient
à le demander une fois pour le bon trimestre au lieu de demander le plus
récent puis de corriger.

Le cache est ce qui rend le tier gratuit viable : un transcript passé ne
change jamais, donc chaque société ne produit qu'**un** transcript neuf par
trimestre. Le coût courant est de 100 requêtes par trimestre pour cent
sociétés, contre un budget de 25 par jour. Seul un succès est mis en cache :
toutes les raisons d'échec (quota, réseau, panne fournisseur) sont
temporaires, et en cacher une transformerait un problème passager en trou
permanent qui ressemble exactement à une société sans transcript.

## Quand le fournisseur n'a pas le call

Alpha Vantage couvre bien les grandes capitalisations et mal les
petites, et publie le trimestre le plus récent avec un retard qui mord
justement en pleine earnings season. La route honnête est alors de
transcrire le webcast soi même : une heure d'anglais des affaires
scripté est largement à la portée d'un modèle de la famille Whisper,
pour quelques centimes.

### Sans terminal, depuis github.com

C'est la voie normale. Sur github.com, **Add file → Create new file**,
chemin :

```
equity-analyzer/transcripts/UBER_2026Q2.txt
```

Colle le texte produit par Whisper, committe, relance le workflow. Le
fichier est lu **avant** le fournisseur, donc le run ne coûte aucun
quota.

Le nom porte toute la métadonnée. `2026Q2` est le repère **fiscal** de
la société, celui qu'affiche le rapport, pas un trimestre civil : le
log de chaque run imprime le fichier attendu (`dépôt local attendu :
transcripts/UBER_2026Q2.txt`), et le message d'échec le redonne quand
aucun transcript n'a pu être récupéré. Nommer le fichier d'après le
trimestre le fait aussi **expirer tout seul** : au trimestre suivant le
pipeline résout `2026Q3`, aucun fichier ne correspond, et le fournisseur
reprend la main. Un fichier appelé `UBER.txt` aurait silencieusement
écrasé tous les runs futurs avec un call périmé.

### Avec un terminal

```bash
TICKER=UBER QUARTER=2026Q2 SOURCE_FILE=uber-q2.txt \
    SOURCE="Whisper large-v3" CALL_DATE=2026-08-05 \
    python scripts/importer_transcript.py
# -> transcripts/UBER_2026Q2.json
```

Même résultat, en JSON plutôt qu'en texte, ce qui permet de conserver la
date du call et un libellé de source précis.

Les deux routes refusent un fichier de moins de 1500 mots : un vrai call
en fait 6 000 à 12 000, et en dessous c'est un fichier tronqué, un résumé
ou le mauvais document. Mieux vaut refuser là qu'après que le modèle
l'ait lu.

**L'appariement reste vérifié.** Le trimestre dans le nom du fichier est
une affirmation de celui qui l'a nommé, et une erreur attacherait une
vraie lecture aux mauvais trois mois. `verify_against_declared` compare
ce repère à la période que la société **annonce à voix haute** dans
l'ouverture du call, donc un écart apparaît sur le rapport au lieu de
passer.

**Une transcription automatique n'est pas un verbatim.** C'est la seule
chose qui change vraiment, et elle est traitée comme telle. Toute la
page 1 repose sur la citation exacte, parce qu'un transcript de
fournisseur est un compte rendu écrit officiel qu'un lecteur peut
ouvrir et vérifier. Une transcription audio ne l'est pas : elle est
fidèle sur la prose et peu fiable sur exactement les mots qui portent
une conclusion ici, puisque « fifteen » et « fifty » ne diffèrent que
d'un phonème et qu'un chiffre de guidance est tout l'enjeu. Un
transcript importé est donc marqué non verbatim par défaut, le rapport
l'annonce **au dessus de la lecture**, et le prompt demande au modèle de
signaler quand un chiffre porte sa conclusion. Il faut un `VERBATIM=1`
explicite pour dire le contraire.

Ce que ce script ne fait **pas** : récupérer quoi que ce soit. La
distinction est juridique et pas technique. Transcrire un call public
auquel on vous a invité à assister est une chose ; recopier le texte
d'un site de transcripts en est une autre, et ce projet ne fournit pas
la seconde.

## Ce qui n'est pas deviné

**Quel trimestre est « le dernier »** est répondu par EDGAR. Le fournisseur
de transcripts indexe par le calendrier **fiscal de l'émetteur**, et EDGAR
porte déjà ce calendrier sur chaque dépôt (`fy` / `fp`). Le déduire du
calendrier grégorien serait juste pour un déposant à clôture décembre et
silencieusement faux pour Apple, NVIDIA, Micron ou Microsoft, c'est-à-dire
pour l'essentiel de la population visée. Le problème est concret : une
requête réelle pour `MSFT 2026Q2` a renvoyé le call du trimestre clos en
décembre 2025.

**Le quatrième trimestre n'est pas un 10-Q.** Un exercice compte quatre
trimestres mais seulement trois 10-Q : le quatrième est publié dans le
10-K, avec l'année complète. Chercher « le dernier 10-Q » saute donc un
trimestre sur quatre, pour toutes les sociétés, et le saute en silence
puisqu'un dépôt de T3 est un dépôt parfaitement valide. Trouvé sur un
vrai run MSFT : exercice clos en juin, T4 clos le 30 juin publié dans le
10-K de fin juillet, et l'outil est allé lire le call d'avril en le
présentant comme le dernier. La sélection porte donc sur le dernier
**10-Q ou 10-K**, ordonné par période de report.

**Et publier n'est pas déposer.** Le call a lieu le jour du communiqué ;
le 10-Q ou le 10-K qu'EDGAR indexe suit deux à six semaines plus tard.
Dans cette fenêtre le call le plus récent existe alors que le dernier
dépôt périodique porte sur le trimestre d'avant. Le **8-K de résultats
(Item 2.02)**, déposé le jour même du communiqué, est le signal le moins
cher qu'un trimestre plus récent a été publié : quand sa période dépasse
celle du dernier dépôt périodique, la recherche part d'un trimestre plus
loin. Avancer d'un cran depuis un repère fiscal **connu** est sûr là où
déduire un trimestre du calendrier ne l'est pas : le repère porte déjà
l'étiquetage de la société, donc un déposant à clôture juin passe de son
T3 à son T4 sans que personne ait codé en dur la fin de son exercice.

**Et dans l'autre sens, le trimestre déposé n'est pas toujours publié
chez le fournisseur.** Une société peut déposer quelques jours après
avoir publié, avant que le transcript ne soit en ligne (constaté sur
AAOI). La recherche remonte alors trimestre par trimestre, et le rapport
**dit de combien** il a fallu remonter plutôt que de faire passer un call
plus ancien pour le courant. Le consensus remonte avec lui : mesurer une
lecture du T1 contre les attentes du T2 serait pire que pas de consensus
du tout, parce que ça a l'air fondé.

**Et on vérifie quand même.** `verify_against_declared` compare le repère
demandé à la période que la société **annonce à voix haute** dans les
premières secondes du call. Un mauvais appariement est autrement invisible
dans la sortie : la lecture serait fluide, sourcée, et sur les mauvais trois
mois.

## Les modules

### `data_layer` — récupérer

- **`edgar_client.py`** : client HTTP SEC EDGAR (companyfacts XBRL,
  submissions, documents bruts). Impose un `User-Agent` valide, exigé par la
  SEC, qui rejette (403) toute requête sans.
- **`cik_lookup.py`** : ticker → CIK via le `company_tickers.json` officiel,
  et listing des dépôts par formulaire. L'ordre est établi sur la **période
  de report**, pas la date de dépôt : une société qui dépose en retard
  appartient quand même à son propre trimestre.
- **`xbrl_normalizer.py`** : XBRL brut → `FinancialPeriod` normalisé. Gère
  la variation des tags GAAP entre émetteurs (liste de candidats ordonnée
  par métrique), la sélection de la valeur **telle que déclarée dans le
  dépôt d'origine** (jamais mélangée à une restatement ultérieure), et
  surtout **la sélection de la bonne période à l'intérieur d'un même
  dépôt** : un numéro d'accession ne porte pas un chiffre par concept mais
  une demi-douzaine (un 10-K étiquette trois exercices comparatifs sous son
  propre accession ; un 10-Q déclare le trimestre seul **et** le cumulé
  depuis le début d'exercice, plus les deux équivalents de l'an dernier).
  Prendre la première entrée du tableau renvoyait une période arbitraire, en
  pratique la plus ancienne. C'est la propriété la plus dangereuse de
  `companyfacts`, parce que l'ignorer donne un nombre parfaitement plausible
  qui est simplement celui d'une autre période.
- **`text_sections.py`** : extrait Item 1A / Item 7 ou 2 / Item 9A du HTML
  brut. Quatre vrais bugs d'extraction trouvés contre de vrais filings et
  corrigés (voir « Ce qui a été trouvé sur de vraies données » plus bas).
- **`transcript_source.py`** : d'où vient un transcript. Quatre routes
  documentées avec leur coût réel ; deux implémentées (Alpha Vantage, et le
  8-K de résultats déposé chez SEC en repli). Le scraping d'un site de
  transcripts est **délibérément absent** : ces textes sont des œuvres
  protégées et leurs conditions l'interdisent.
- **`transcript_cache.py`** : un transcript ne change jamais. Fichiers JSON
  sur disque, un par call, lisibles à la main : le lecteur d'un rapport doit
  pouvoir vérifier une citation contre ce que le modèle a réellement lu.
- **`transcript_period.py`** : quel repère demander au fournisseur, et la
  vérification contre ce que la société annonce.
- **`earnings_expectations.py`** : le consensus de BPA (Alpha Vantage
  `EARNINGS`), apparié au trimestre **par date de fin de période** et non
  par étiquette fiscale, plus le palmarès des quatre trimestres précédents.

### `redflags` — trois scores annuels, jamais trimestriels

Altman Z-Score, Beneish M-Score, Piotroski F-Score, construits sur les
`FinancialPeriod` du data layer.

**Ce sont des modèles annuels, et ce n'est pas une préférence.** Tous ont
été estimés sur des comptes d'exercice complet, et plusieurs de leurs
entrées n'ont pas de sens sur un trimestre : les indices d'accruals et de
croissance de Beneish comparent une année à la précédente, les neuf critères
de Piotroski sont des tests d'une année sur l'autre, les coefficients
d'Altman ont été calibrés sur des bilans annuels. Leur donner un 10-Q ne
produit pas une estimation plus bruitée, ça produit une **erreur de
catégorie** qui ressemble exactement à un bon chiffre. Ils sont donc
calculés sur les deux derniers 10-K, la page 2 dit de quel exercice ils
viennent, et faute de 10-K exploitable ils sont marqués indisponibles.

Toutes les fonctions échouent explicitement plutôt que de deviner une
donnée manquante ou de comparer silencieusement des périodes incompatibles.

**Limite documentée, pas corrigée** : les institutions financières déposent
un bilan non classifié en courant / non courant, donc
`current_assets` / `current_liabilities` n'existe pas pour elles, sous aucun
tag. C'est aussi la pratique standard en finance de ne pas appliquer ces
trois modèles aux institutions financières, pour cette même raison. Ce n'est
pas un bug, c'est une limite de portée.

### `sentiment` — la tonalité, sur trois textes

Score Loughran-McDonald, à partir du **vrai** dictionnaire de l'université
Notre Dame (86 553 mots, fourni dans `data/`, publié gratuitement sur
<https://sraf.nd.edu/loughranmcdonald-master-dictionary/>). Ce dépôt ne le
recopie pas de mémoire : une version partielle sous-compterait certaines
catégories en silence, exactement le genre d'erreur que ce projet cherche à
éviter.

**Trois textes, pas un, parce que ce sont trois actes différents :**

- les **remarques préparées** sont écrites, relues par les juristes et
  répétées, donc leur ton est une décision que la direction a prise ;
- la **Q&A** est non scriptée, donc son ton est plus proche d'une
  observation que d'un choix ;
- le **MD&A du 10-Q** est la même direction écrivant pour le dossier
  quelques semaines plus tard, le registre le plus lent et le plus prudent
  des trois.

Le chiffre qui compte n'est aucun des trois isolément mais **l'écart entre
les deux premiers** : un script nettement plus chaud que les réponses qui le
suivent est un motif précis et fréquent, et il est invisible si le lecteur
doit soustraire deux nombres lui-même. Le rapport le calcule et le nomme.

**Limite connue, imprimée sur la page** : c'est du bag-of-words, la négation
n'est pas gérée (« not profitable » compte « profitable » comme positif).
Un chiffre sur une page se lit comme une mesure tant qu'il ne dit pas le
contraire.

### `report` — lire et composer

- **`call_analysis.py`** — le prompt. Trois propriétés y sont verrouillées
  par des tests séparés, parce que ce sont des instructions et pas des
  chemins de code : **il cite** verbatim (le transcript est un compte rendu
  officiel, donc la citation exacte est vérifiable et c'est ce qui rend
  l'analyse utilisable) ; **il sépare ce qui est dit de ce qui est déduit**
  (un gérant doit distinguer d'un coup d'œil ce qu'il doit vérifier de ce
  qu'il doit soupeser) ; **il tient en une page**.
  Il produit quatre sections : Verdict, Face aux attentes, Les déclarations
  clés, À surveiller. **Cinq quand il n'y a pas de page 2** : « Les
  esquives » revient alors, parce que la page 2 est le seul autre endroit
  du document qui les mentionne. Avec elle, les répéter en prose coûterait
  un cinquième d'une page plafonnée à redire ce que le lecteur va lire mis
  en forme, et ce cinquième est pris aux engagements chiffrés, qui
  n'apparaissent nulle part ailleurs. Mesuré sur un vrai run TSLA : la même
  question esquivée sortait en paragraphe page 1 et en ligne page 2.
  Corollaire d'ordonnancement : la passe Q&A tourne **avant** la lecture,
  puisque la lecture doit savoir si la page 2 existera.
  Ce qui reste interdit : importer un fait extérieur sur la société (autre
  publication, actualité, cours, mémoire d'entraînement), et donner une
  recommandation d'achat ou de vente. Trancher sur la balance de **ce
  call** est demandé ; dire d'acheter le titre est une affirmation d'une
  autre nature, que cet outil ne fait pas. En revanche **raisonner
  économiquement sur un fait fourni** (une hausse de prix annoncée implique
  plus de revenu) est explicitement attendu : la limite est de ne pas
  importer de faits, pas de s'interdire de réfléchir.
- **`claude_client.py`** — le transport, et rien d'autre. Un seul endroit
  sait parler au modèle.
- **`markdown.py`** — la réponse du modèle vers le HTML du rapport.
  Volontairement pas une bibliothèque markdown : l'entrée n'est pas du
  markdown arbitraire, c'est la réponse à un prompt qui impose sa propre
  structure. Tout est échappé **avant** qu'un tag soit ajouté : la page cite
  un earnings call, où « & » et « < » sont de l'anglais des affaires
  ordinaire (« R&D », « <10% »).
- **`report_data.py`** — assemble l'objet à rendre. Pur : pas de réseau,
  pas de clé. C'est ce qui rend le rapport testable hors ligne, ce qui
  compte plus ici qu'ailleurs (voir « Tests »).
- **`html_renderer.py` / `pdf_renderer.py`** — le document, puis le PDF via
  `xhtml2pdf` (pur Python, aucune dépendance système type Cairo, Pango ou
  wkhtmltopdf). Si `pip install xhtml2pdf` échoue avec *« Cannot uninstall
  cryptography..., RECORD file not found »*, utiliser
  `pip install --ignore-installed cryptography xhtml2pdf`.

## Tests

```bash
python -m pytest tests/ -q     # 247 tests, entièrement hors ligne
```

**Entièrement hors ligne, et c'est structurant.** L'environnement de
développement de ce projet n'atteint ni `data.sec.gov` ni le fournisseur de
transcripts, donc toute la suite tourne sur des fixtures locales qui
reproduisent fidèlement les schémas JSON et HTML réels, et le premier vrai
run se fait en CI. Le workflow lance la suite **avant** de dépenser du quota
et un appel payant.

**Les comptes de pages sont mesurés, pas déduits d'une feuille de style.**
Chaque test de mise en page rend un vrai PDF et compte ses vrais objets
`/Type /Page`. C'est le seul contrôle qui attrape la classe de bug que
cette contrainte existe pour empêcher : « la feuille de style a l'air de
tenir » et « le document tient » sont deux affirmations différentes.
Le plafond de la page 1 (`MAX_READING_WORDS`) est **encadré des deux
côtés** : un test vérifie qu'un texte au plafond laisse le rapport à deux
pages, un autre qu'un texte quarante mots plus long déborde vraiment. Les
deux seuils de la page Q&A sont gelés de la même façon, la charge d'un vrai
run d'un côté et la session hors norme de l'autre. Sans ces contrôles hauts,
le plafond pourrait être abaissé à 50 mots et tout resterait vert pendant
que la page 1 serait aux deux tiers vide.

## Ce qui a été trouvé sur de vraies données

Chaque correction ci-dessous vient d'un vrai document, pas d'un exemple
construit. Elles sont listées parce qu'elles disent quelque chose sur la
matière première, qui est plus sale que sa documentation.

**Extraction de sections (vrais 10-K)**

- Un mot coupé par une balise en plein milieu : un 10-K Microsoft contient
  littéralement `RIS<span>K</span> FACTORS`. La balise n'est effacée sans
  laisser d'espace que si elle est strictement entre deux caractères
  alphanumériques, pour ne pas recoller des cellules de tableau adjacentes.
- Les entités HTML numériques (`&#160;`, pas seulement `&nbsp;`) : un vrai
  10-K Coca-Cola les utilise entre « Item 7. » et « Management's
  Discussion », ce qui faisait échouer l'extraction en silence.
- Une référence croisée **au milieu d'une phrase** : quasiment tout MD&A
  commence par « ...should be read in conjunction with 'Item 1A. Risk
  Factors,' ... », qui a exactement la forme d'un titre de section pour un
  détecteur de frontière. Sans distinction, le MD&A de NVIDIA (~40 000 mots
  réels) était coupé à 27 mots. Une frontière n'est acceptée que précédée
  d'un saut de ligne : un vrai titre est seul sur sa ligne.
- Le bruit de pagination **à l'intérieur d'une phrase** : un numéro de page
  isolé et un « Table of Contents » répété, injectés par l'imprimeur
  financier à chaque saut de page, verbatim depuis un vrai 10-K NVIDIA.
- Le numéro d'item et son titre **sur deux lignes différentes** (Microsoft,
  Item 1A extrait à 22 mots) : les motifs utilisaient `\s`, qui traverse les
  sauts de ligne, si bien qu'une **ligne de table des matières** matchait
  comme un vrai titre et gagnait, et qu'un en-tête courant nu répété à
  chaque page servait de frontière de section.

**Découpage du call (vrais transcripts)**

- L'opérateur **annonce** la Q&A dans son préambule (« a question and
  answer session will follow the formal presentation ») longtemps avant d'y
  passer. Sur le premier call Microsoft réel, ça déplaçait la coupure à la
  deuxième ligne : trois mots de remarques préparées et tout le reste classé
  en Q&A. Le signe distinctif est le temps : une annonce pointe vers l'avant,
  un passage de relais a lieu maintenant. Second garde-fou indépendant de la
  formulation : les remarques préparées sont le gros d'un call, jamais sa
  première ligne.
- À l'inverse, le vrai passage de relais du call Microsoft n'utilisait
  **aucune** des formules attendues : la coupure ne trouvait rien et classait
  les 8 815 mots en remarques préparées. Trois marqueurs supplémentaires
  (`[Operator Instructions]`, « first question comes from », « press star
  one ») sont ce qu'un opérateur dit réellement.

**Rendu du PDF (trouvés en relisant le PDF, pas en lisant le code)**

- Les puces d'une `<ul>` sortaient en **glyphe manquant** : `xhtml2pdf` tire
  le marqueur par défaut d'un caractère que la police embarquée du rapport
  ne porte pas. Remplacées par un point médian explicite, présent dans toute
  police de repli, avec un retrait négatif qui rend le débord de ligne.
- La troncature de la page 1 **écrasait les sauts de ligne**
  (`" ".join(text.split()[:n])`), donc le `##` du premier titre avalait
  toute la réponse et la page se rendait comme un unique titre géant. La
  suite de tests était verte pendant ce temps ; c'est le comptage de pages
  d'un vrai PDF qui l'a fait tomber.
- Les raisons d'indisponibilité arrivaient en anglais technique
  (« FinancialPeriod (current, accession 0000-...) is missing 'receivables' »)
  au milieu d'un rapport français. Reformulées pour le cas qui arrive
  réellement, en gardant le nom de la métrique tel que le module l'a nommé ;
  un message non reconnu passe verbatim, parce qu'une raison que le rapport
  ne sait pas analyser reste une raison que le lecteur a le droit de voir.

**API (tranché par un appel réel, pas par la documentation)**

- Alpha Vantage répond à un quota épuisé, à un endpoint devenu payant et à
  un symbole inconnu par un **HTTP 200** portant une phrase sous
  `Information` ou `Note`, sans données. Lu comme de la donnée, ça ressemble
  à une société sans historique. Vérifié avant de chercher le champ attendu,
  parce que sinon les trois remontent comme « champ manquant » et pointent
  le lecteur vers les noms de champs quand le vrai problème est le quota.
- **L'endpoint `EARNINGS` est bien dans le tier gratuit.** C'était la
  dernière inconnue du projet, tranchée par un vrai run : MSFT T3 2026,
  attendu 4,09, publié 4,27.
- **Et ce tier limite le débit autant que le volume.** Le même run a vu
  la deuxième requête refusée pour cause de rafale, alors que le quota
  du jour était intact. Le message mentionne les deux limites à la fois,
  donc on ne peut pas les distinguer en le lisant : d'où le throttle
  partagé et la reprise unique décrits plus haut.
- La famille Claude 5 **refuse** le paramètre `temperature` (HTTP 400,
  « `temperature` is deprecated for this model »), donc l'envoyer
  inconditionnellement rend ces modèles inutilisables. Omis là où il n'est
  pas supporté plutôt que supprimé partout. Détecté sur un motif de famille
  et de génération et non sur une liste d'identifiants connus : les
  identifiants changent à chaque sortie, et le pire moment pour l'apprendre
  est après avoir dépensé le quota de transcript.

## Choix du modèle

Une clé API n'est liée à aucun modèle : le modèle se choisit **à chaque
appel**. Par défaut `claude-sonnet-5`, surchargeable par le paramètre
`model=`, par la variable d'environnement `ANTHROPIC_MODEL`, ou par la liste
déroulante du workflow.

Le défaut est un modèle de raisonnement et pas le moins cher, délibérément :
lire un call entier et le peser contre le consensus est une tâche de
jugement, pas de reformulation. Haiku reste disponible dans la liste et
divise le coût par cinq à dix ; ce compromis n'a **pas** été mesuré
comparativement sur de vrais rapports, donc c'est une piste raisonnée et pas
un résultat vérifié.

## Ce que ce projet ne fait pas

- **Pas de recommandation d'achat ou de vente**, ni d'objectif de cours.
- **Pas de comparaison sectorielle** : elle demanderait de récupérer
  plusieurs sociétés à la fois, ce qu'on ne peut pas valider contre de
  vraies données depuis cet environnement. Mieux vaut ne pas livrer un
  résultat non vérifié que de prétendre qu'il est fiable.
- **Pas de scraping de sites de transcripts** (voir `transcript_source.py`).
- **Pas de pagination au-delà de `filings.recent`** (~1000 dépôts les plus
  récents) : suffisant pour l'usage advisory, pas pour un historique sur
  plusieurs décennies.

## Secrets et configuration

| Variable | Rôle |
|---|---|
| `ANTHROPIC_API_KEY` | **Obligatoire.** C'est elle qui écrit la page 1. |
| `ALPHAVANTAGE_API_KEY` | Transcript et consensus. Sans elle, le run se rabat sur le 8-K de résultats déposé chez SEC, que seule une minorité d'émetteurs fournit. |
| `SEC_USER_AGENT` | Nom d'application + email de contact. Exigé par la SEC, qui rejette (403) sans. |
| `ANTHROPIC_MODEL` | Optionnel, surcharge le modèle par défaut. |

Depuis GitHub Actions : *Settings → Secrets and variables → Actions → New
repository secret*. Jamais en clair dans une case de saisie du formulaire.

## Installation

```bash
pip install --ignore-installed cryptography   # voir la note xhtml2pdf ci-dessus
pip install -r requirements.txt
pip install -e .
```

Le rapport embarque **Lato** : `sudo apt-get install fonts-lato` (le workflow
le fait). Sans le paquet, le rapport se génère quand même, dans la police de
repli.
