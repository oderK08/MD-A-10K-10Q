# data/

Ce dossier accueille les fichiers de données externes du projet.

## Loughran-McDonald Master Dictionary

Requis par le Module 4 (`equity_analyzer.sentiment`).

`Loughran-McDonald_MasterDictionary_1993-2025.csv` est déjà présent dans
ce dossier et **suivi par Git** (choix délibéré, voir `.gitignore`) —
téléchargé depuis la source officielle :
👉 https://sraf.nd.edu/loughranmcdonald-master-dictionary/

Utilisation :
```python
from equity_analyzer.sentiment import load_lm_dictionary
dictionary = load_lm_dictionary(
    "data/Loughran-McDonald_MasterDictionary_1993-2025.csv"
)
```

Validé contre 86 553 mots réels — les comptages par catégorie
correspondent aux chiffres publiés (négatif: 2355, positif: 354,
incertitude: 297, litigieux: 905, modal fort: 19, modal faible: 27,
contraignant: 184). Voir
`tests/sentiment/test_integration_production_dictionary.py`.

Si tu remplaces ce fichier par une version plus récente, garde le même
nom (ou mets à jour la référence dans le code et cette doc) — n'importe
quel autre fichier déposé ici reste ignoré par Git par défaut.
