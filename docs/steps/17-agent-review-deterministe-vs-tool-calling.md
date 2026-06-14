# 17 — Agent de review : déterministe (structured output) plutôt que tool-calling

> Daté du 2026-06-14. Décision d'architecture pour l'agent LLM qui annote les
> articles (résumé + mots-clés + catégories). Prolonge `docs/conception/agent-enrichissement.md`
> et TODO pt.3 / pt.6. Le code vivra sous `app/review/` (terme « review », cohérent
> avec la colonne `llm_reviewed_at` ajoutée à l'étape 16 ; on évite « enrich » déjà
> pris par le RAG : `ingest/enrich.py` et `runtime/fresh_news.py`).

## Décision

L'agent de review est implémenté en **structured output one-shot**, **pas** en agent
*tool-calling* :

- **1 seul appel LLM par article**, contraint à répondre au format `ArticleReview`
  (JSON schema dérivé d'un modèle pydantic, `categories` = enum imposé → NTUI).
- Le **repli de contenu est déterministe** (décidé par le code, pas par le LLM) :
  `texte = content or (page scrapée si content vide) or title`. Le LLM ne décide
  pas *quand* aller chercher la page ; nous, oui.

On écarte l'agent tool-calling (LLM qui boucle et choisit lui-même ses outils).

## L'enjeu (pourquoi ce choix)

La tâche est « lire un texte → produire des champs » : c'est la **définition du
structured output**, pas d'une tâche agentique (qui suppose un raisonnement
multi-étapes avec décisions d'outils). Mettre un agent tool-calling dessus coûte cher
pour un gain quasi nul :

| Critère | One-shot déterministe | Agent tool-calling |
|---|---|---|
| Appels LLM / article | **1** | 2 à N (boucle) |
| Coût passe à froid (~555 art.) | référence | **× 2-3** (l'historique est renvoyé à chaque tour) |
| Latence | référence | **× 2-3** (allers-retours séquentiels) |
| Rate limits (déjà un risque, cf. conception §5) | minimal | **aggravé** par le nb d'appels |
| Déterminisme / testabilité | flux fixe, facile à mocker | flux variable d'un article à l'autre |
| Pièces mobiles | un appel | executor, `max_iterations`, erreurs par outil |

**Le piège décisif** : un agent tool-calling finit sur un **message libre**, pas un
JSON garanti. Pour obtenir un `ArticleReview` structuré en sortie, il faut de toute
façon **un outil final `submit_review(...)` dont les arguments sont le schéma**, ou un
appel structured output après la boucle. → On ne supprime pas le structured output, on
l'emballe dans une boucle plus chère. Autant le garder seul.

**Le seul argument pour l'agentique** ici serait le *scraping conditionnel* (« si
`content` est vide, va chercher la page »). Or ça se fait sans LLM, en une ligne
déterministe (`if not content: content = scrape(url)`), gratuitement et sans
non-déterminisme.

## Conséquences

- Plus simple, moins cher, plus testable (on peut mocker un appel unique avec `respx`
  / un faux client) → cohérent avec KISS/YAGNI et la « tolérance aux pannes » du
  pipeline (un échec d'article ne casse pas la passe).
- Structure de fichiers visée sous `app/review/` : `reviewer.py` (agent + schéma +
  client LLM), persistance dans `app/data/article_store.py`, boucle dans
  `app/review/runner.py`, déclenchement par une commande CLI manuelle.

## Quand reconsidérer (signal de bascule)

Passer à un vrai agent tool-calling **seulement si** la tâche devient réellement
multi-étapes et non scriptable à l'avance — par ex. : décider dynamiquement de
chercher sur le web, de recouper plusieurs sources, de dédupliquer contre l'existant
avant de trancher. Tant que c'est « texte → champs », le déterministe gagne.

> Note pédagogique : pratiquer le pattern agent+tools reste utile — mieux vaut le faire
> comme **exercice séparé** que de le plaquer sur cette feature où il ne paie pas.
