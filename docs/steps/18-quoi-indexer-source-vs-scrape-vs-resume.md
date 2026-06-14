# 18 — Quoi indexer dans Chroma : source vs article scrapé entier vs résumé LLM

> Daté du 2026-06-14. Décision de stratégie d'indexation, issue d'un échange sur
> l'agent de review (cf. `docs/steps/17` et `docs/conception/agent-enrichissement.md`).
> Tranche : *quel texte feed-on au chunking/embedding pour chaque article ?*

## La question

Pour chaque article, **quel texte indexer** dans Chroma ? Trois candidats :

1. **Contenu source** — le `content` récupéré à la source (abstract arXiv, blurb TLDR,
   corps RSS), écrit par un auteur tiers.
2. **Article entier scrapé** — le texte brut de la page, récupéré en scrapant l'`url`.
3. **Résumé LLM de l'article entier scrapé** — on scrape, puis le LLM condense.

## Comparaison

Ce qu'on embed = la **surface de recherche** : une requête matche les termes présents
dans le texte indexé. D'où le compromis recall (retrouver large) vs bruit (faux matchs).

| Critère | 1. Contenu source | 2. Article entier scrapé | 3. Résumé LLM du scrape |
|---|---|---|---|
| Fidélité | auteur, factuel | brut, fidèle | risque d'hallucination (atténué si fait *du texte scrapé*) |
| Recall | bon (dense, termes-clés) | **max** (tout le texte) | réduit (un résumé jette des détails) |
| Bruit / précision | propre | **bruité** (refs, boilerplate, pub) → faux matchs | propre (nettoyé) |
| Coût | gratuit | gratuit (juste HTTP) | **LLM par article** |
| Grain de l'index | abstract-like | gros dumps hétérogènes | abstract-like (uniforme) |
| Snippet de card | bon | mauvais (dump) | bon |

Points saillants :

- **Résumer réduit la surface** (option 3) : termes/entités absents du résumé →
  article introuvable sur ces mots. Plus coût LLM, hallucination, variance.
- **L'article entier** (option 2) maximise le recall **mais ajoute du bruit** : chunks
  de références/boilerplate qui matchent à tort et gonflent l'index — le « lost in the
  middle » de `docs/notes/retrieval-k.md`.
- Le **contenu source** (option 1) est souvent **déjà un bon résumé fidèle** : l'abstract
  arXiv est dense et écrit pour ça, le blurb TLDR est déjà éditorialisé.

## Décision

Indexer **la meilleure représentation fidèle DISPONIBLE**, qui dépend de la source —
ni « tout résumer », ni « tout l'article entier » :

| Cas | Option retenue | Pourquoi |
|---|---|---|
| `content` source présent et correct (arXiv, TLDR, RSS avec corps) | **1. contenu source** | déjà fidèle et dense ; le re-scraper/résumer = perte + coût pour rien |
| `content` **vide ou très court** (seuil à définir) | **3. résumé LLM du scrape** | pas d'abstract d'origine → on en fabrique un |

→ On **écarte l'option 2 (article entier scrapé brut)** même quand `content` est vide :
le dump brut est bruité, hétérogène en grain, et mauvais à l'affichage.

## Pourquoi l'option 3 pour le cas vide/court (et pas l'option 2)

- Le résumé joue le rôle de **l'abstract que l'article n'a jamais eu** → tout l'index
  reste au **même grain** (abstract-like comme arXiv/TLDR), pas un mélange dumps + abstracts.
- Il **nettoie le bruit web** et fait un meilleur snippet de card (le `content` sert
  aussi à l'affichage).
- L'objection « le résumé est lossy » ne vaut **que** face à un bon abstract existant
  (option 1) ; ici l'alternative réaliste est un scrape brut bruité (option 2) → le
  résumé gagne.
- Garde-fou hallucination : résumé fait **à partir du texte scrapé réel** (jamais du
  seul titre), température basse, prompt « fidèle, n'invente rien ».

## Lien avec l'agent de review (3 cas)

L'agent **scrape toujours** l'`url` (déterministe, via `app/ingest/scraper.py`) et lit le
texte pour produire `keywords` + `categories`. En plus, selon `content` :

1. `content` présent (assez long) → gardé verbatim (option 1), indexé tel quel.
2. `content` absent → résumé LLM du scrape (option 3) → écrit dans `content` → indexé.
3. `content` court (seuil à définir) → idem absent.

Le résumé LLM **ne sert qu'à combler** un `content` absent/court — jamais à remplacer
un contenu source fidèle.

> Raffinement possible plus tard (YAGNI) : indexer **deux** grains — abstract pour la
> précision + corps pour le recall (retrieval multi-vecteur). Hors périmètre.
