# data/

Ce dossier accueille les fichiers de données externes que le projet
utilise mais n'embarque pas dans le dépôt Git (licence tierce, ou fichier
trop volumineux pour être versionné).

## Loughran-McDonald Master Dictionary

Requis par le Module 4 (`equity_analyzer.sentiment`).

1. Télécharge le CSV le plus récent ici :
   👉 https://sraf.nd.edu/loughranmcdonald-master-dictionary/
   (section "Master Dictionary", fichier `.csv`, gratuit — juste un
   formulaire à remplir sur le site de l'université Notre Dame)
2. Place le fichier téléchargé dans ce dossier, par exemple :
   `data/Loughran-McDonald_MasterDictionary.csv`
3. Charge-le dans ton code :
   ```python
   from equity_analyzer.sentiment import load_lm_dictionary
   dictionary = load_lm_dictionary("data/Loughran-McDonald_MasterDictionary.csv")
   ```

Tout le contenu de ce dossier (sauf ce README) est ignoré par Git — voir
`.gitignore` à la racine du projet.
