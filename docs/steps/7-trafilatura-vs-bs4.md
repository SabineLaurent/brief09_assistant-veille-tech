# Décision : trafilatura vs BeautifulSoup + markdownify

## Contexte

Pour le scraping de TLDR.tech, question posée : utiliser `trafilatura` (bibliothèque
spécialisée dans l'extraction de contenu web) ou rester sur la stack déjà présente
(`httpx` + `beautifulsoup4` + `markdownify`) ?

## Comparaison

| Critère | `trafilatura` | `httpx` + `bs4` + `markdownify` |
|---|---|---|
| Suppression du boilerplate | Automatique (très bon) | Manuel (`strip_boilerplate`) |
| Sortie Markdown | Oui (`output_format="markdown"`) | Via `markdownify` |
| Extraction structurée (blocs individuels) | Non — renvoie le texte de la page entière | Oui — on contrôle article par article |
| Dépendances à ajouter | 1 (`trafilatura`) | 0 (tout est déjà là) |
| Contrôle fin | Faible | Total |

## Problème spécifique à TLDR.tech

TLDR.tech n'est pas un article de blog — c'est une newsletter.
Chaque page contient **plusieurs articles distincts**, chacun avec :
- un titre
- un résumé (2-3 phrases)
- un lien vers la source originale
- une catégorie

`trafilatura` extrait le contenu principal d'une page **en un seul bloc de texte**.
C'est parfait pour scraper un article de blog individuel, mais pas pour découper
une newsletter en N articles indépendants avec leurs métadonnées.

## Décision retenue

**Rester sur `bs4` + `markdownify`** — pas de nouvelle dépendance, contrôle total
sur l'extraction des blocs d'articles.

## Quand trafilatura serait pertinent

Si on adoptait une approche "deep" : suivre chaque lien source depuis TLDR vers
l'article original et en extraire le contenu. Dans ce cas, `trafilatura` excellerait
(une URL → un bloc de texte propre en Markdown, boilerplate retiré automatiquement).

Ce n'est pas l'approche retenue (voir `docs/conception/tldr.tech-scrapping.md`).
