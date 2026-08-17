# Où déposer un transcript

Ce dossier reçoit les transcripts que tu obtiens toi-même, pour analyser
un call que le fournisseur n'a pas (petite capi, trimestre trop récent)
ou une **société hors US**, qui n'a pas de dépôt SEC du tout.

## La règle du nom

Un fichier par call, nommé d'après le ticker et le **repère fiscal** de
la société (celui qu'elle annonce dans le call, pas le trimestre civil) :

```
transcripts/SAP_2026Q2.txt
```

Le nom porte toute l'information. Il dit de quel call il s'agit, et il
fait **expirer le fichier tout seul** : au trimestre suivant le pipeline
cherche `SAP_2026Q3`, ne le trouve pas, et repart normalement. Un fichier
nommé `SAP.txt` écraserait silencieusement chaque futur call.

Format : texte brut (`.txt`), au moins 1500 mots (un vrai call en fait
6 000 à 12 000 ; en dessous, le run refuse, c'est un collage tronqué).

## Le plus simple : une commande

Depuis un terminal, à la racine de `equity-analyzer/` :

```bash
python scripts/transcrire.py sap_q2.mp3 SAP 2026Q2
```

Ça transcrit l'audio avec Whisper, écrit le fichier ici au bon nom, et
enchaîne sur le rapport si `ANTHROPIC_API_KEY` est défini. Tu n'as rien à
renommer ni à te rappeler d'autre. Voir `scripts/transcrire.py`.

## Sans terminal

Transcris le webcast de ton côté, puis sur github.com : **Add file →
Create new file**, chemin `equity-analyzer/transcripts/SAP_2026Q2.txt`,
colle le texte, commit. Lance ensuite le workflow (region = International,
quarter = 2026Q2).

## Ce qui n'est PAS ici

L'audio. Whisper transcrit un fichier que tu fournis ; récupérer l'audio
(enregistrer le webcast auquel tu assistes) est ta partie. Ce projet ne
copie pas les sites de transcripts : c'est une question de droits, pas de
technique.
