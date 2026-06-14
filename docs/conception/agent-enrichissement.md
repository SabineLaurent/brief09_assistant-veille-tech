# Conception — Agent d'enrichissement des articles

> Note de cadrage (échange du 2026-06-14). Rien d'implémenté ici : c'est une
> consolidation des décisions et pistes pour un futur agent. Recoupe les points
> 3 et 6 (piste 4) de `docs/TODO.md`.

## 1. Objectif

Un agent LLM, **activable manuellement** dans un premier temps, qui pour **chaque
article** de la base produit trois choses :

1. **`content` (résumé)** — uniquement pour les entrées **sans contenu** (voir §2).
2. **`keywords`** — mots-clés de contenu, dérivés du sujet réel de l'article.
3. **`tags` / catégorie(s)** — choisie(s) dans une **liste imposée** (voir §3).

Ces champs existent déjà dans le modèle `Article` (`tags`, `keywords`) et la table
SQLite ; l'agent vient les **peupler**, pas changer le schéma.

### Pourquoi un agent : les sources ne fournissent pas de mots-clés

Constat (2026-06-14) : **aucune source actuelle ne livre de vrais mots-clés de
contenu**. Il y a donc un vide réel à combler, sans signal « gratuit » exploitable
tel quel.

| Source | `keywords` aujourd'hui | `tags` |
|---|---|---|
| **arXiv** | **rempli mais trompeur** : ce sont les **termes de la requête** qui ont trouvé l'article (mêmes valeurs pour tous les résultats d'une même requête), pas une description de l'article | `[catégorie arXiv]` (ex. `cs.AI`) |
| **TLDR** | **vide** (`[]` par défaut) | `[edition, catégorie]` |
| **RSS** | **vide** (`keywords=[]` explicite) | `[topic]` de la config (ou `[]`) |

- La séparation `tags` (provenance/requête) vs `keywords` (contenu) a déjà été faite
  pour préparer ce terrain (TODO point 2, cf. `docs/steps/13-attribut-keywords.md`).
- **Piste « gratuite » mineure** : certains flux RSS exposent des `<category>`
  (accessibles via `feedparser` → `entry.tags`) qu'on pourrait pré-remplir **sans
  LLM** ; mais c'est inégal d'un flux à l'autre → non prioritaire, l'agent reste la
  solution générale.

## 2. Le problème déclencheur : les articles sans `content`

Constat à l'indexation : un article dont `content` est vide donne `chunk("") == []`,
donc `index_articles` le **saute** (`continue`) sans changer son status → il reste
coincé en `status='ingested'` et est relu/re-sauté à chaque `make index`.

Cas observé : **12 articles** (11 du blog Hugging Face, 1 TLDR) dont le flux RSS ne
fournit que **titre + lien**, sans corps.

Pistes pour les débloquer (par effort croissant) — cf. TODO point 6 :

1. **(quick win)** repli sur le titre comme texte indexable (`text = content or title`)
   — gratuit, immédiat, rend l'article retrouvable (signal faible mais > rien).
2. status distinct `skipped` quand `chunks` est vide (sortir de la file `ingested`).
3. scraper la page source (`url`) pour récupérer le vrai texte.
4. **faire générer le résumé par l'agent**, *dans la même passe que les mots-clés*
   (le coût marginal est faible : l'agent lit déjà l'article pour les keywords).

→ Décision pragmatique : la piste 1 débloque tout de suite ; l'**agent (piste 4)
enrichit la qualité plus tard**, mutualisé avec les mots-clés.

## 3. Catégories imposées

Pour arXiv (à étendre au besoin), l'agent choisit **uniquement** parmi :

- **AI**
- **Sécurité**
- **Agentique**
- **Embarqué**

La/les catégorie(s) retenue(s) vont dans `tags` ; les mots-clés générés vont dans
`keywords` (champ déjà prévu, cf. `docs/steps/13-attribut-keywords.md`).

## 4. Choix du modèle (décisions)

Principes communs aux deux pistes ci-dessous :

- **Tâche facile** (résumé court + extraction de mots-clés + classification fermée) →
  un **petit modèle** suffit ; la qualité brute n'est pas le facteur discriminant.
  Surtout **pas** un gros modèle ni un *reasoning model* (overkill, lent, cher).
- **Critères qui comptent** : bon en **français** (catégories en FR), **sortie
  structurée fiable** (JSON), petit/rapide/pas cher (passe à froid volumineuse).
- **Un seul modèle**, pour l'**uniformité** (format/qualité homogènes, un seul prompt,
  un seul SDK, une seule logique retry, reproductibilité). On ne diversifie que si un
  plafond bloque réellement.
- **Structured output** (JSON schema / function calling) plutôt que parser du texte
  libre : on contraint `tags` à un **enum des 4 catégories** → l'agent ne peut pas
  inventer de catégorie hors liste (NTUI). Géré nativement par GPT-mini et Mistral.

### Piste A — Azure AI Foundry (privilégiée si on reste sur Azure)

Réutilise l'infra existante : déploiement **serverless (MaaS)** appelé via **Azure AI
Inference** — **même SDK que le chat** (`langchain-azure-ai`). Unification au prix du
**crédit Azure** (vs gratuit).

- **Défaut conseillé : `GPT-5.4-mini` en mode NON-reasoning** (dispo sur le Foundry de
  l'utilisateur). Récent (sortie 2026-03-17), function calling (→ structured output /
  enum des catégories), prompt caching, grand contexte. Mode non-reasoning impératif
  pour cette tâche facile : sinon on paie des tokens de raisonnement + latence inutiles.
  - Prix Azure : **$0.75 / 1M entrée, $4.50 / 1M sortie**. La sortie est minuscule
    (résumé + mots-clés ≈ 150-200 tokens) → coût dominé par l'entrée. Ordre de grandeur
    ~$0.003/article → passe à froid (~555 articles) **< 2 $** ; incrémental négligeable.
  - **Prompt caching** : garder un system prompt stable (instructions + 4 catégories +
    JSON schema) → facturé moins cher d'un article à l'autre.
- Alternative **non-OpenAI / EU : `Mistral Small`** (dernière version) — français natif,
  pas cher, bon JSON.
- Repli moins récent : `GPT-4.1-mini` (équivalent, un peu moins cher au token).
  `Phi-4-mini` = le moins cher mais français plus faible → à tester avant.
- Démarrage zéro-setup possible : prototyper le prompt sur le **déploiement Kimi
  existant**, puis basculer sur le *mini* pour le coût.

### Piste B — Clés gratuites (Google / Mistral / Groq)

L'utilisateur a des clés **gratuites, limitées, auto-renouvelables** chez **Google
(Gemini)**, **Mistral**, **Groq**. Gratuit, mais infra séparée du chat + plafonds.

- **Défaut conseillé : Gemini Flash** — gros quota gratuit (~1500 req/jour), bon en
  français, grand contexte. Groq = le plus **rapide** mais français un cran en dessous
  (modèles ouverts type Llama) ; Mistral = bonne option FR/EU mais quota plus modeste.

### Arbitrage

Foundry = **unification** (un seul SDK, infra Azure) au prix du crédit ; free tiers =
**gratuit** au prix d'une infra séparée et de plafonds. Dans les deux cas le chat reste
sur Azure AI / Kimi-K2.6 — l'agent est **découplé**.

## 5. Contrainte de débit (rate limits)

Le quota gratuit est **par compte / organisation / projet, PAS par clé** : plusieurs
clés dans le même projet partagent le même pool. Créer plusieurs comptes/projets pour
contourner = **violation des CGU** (Groq, Google), risque de suspension.

Conséquence sur l'implémentation :

- **Passe à froid** (centaines d'articles) : **throttle + retry sur 429**, étaler dans
  le temps — le quota se renouvelle, un batch lent mais patient finit par tout traiter.
- **Incrémental** quotidien : ne traite qu'une poignée d'articles (les nouveaux du
  jour, via le watermark) → ne touche plus les limites.

## 6. Esquisse de flux (non figé)

```
article (SQLite)
   │  contenu = content si présent, sinon page scrapée / titre
   ▼
agent LLM (1 passe / article)
   │  → résumé (si content vide) → content
   │  → mots-clés                → keywords
   │  → catégorie(s) ∈ liste     → tags
   ▼
upsert SQLite (idempotent) → puis indexation Chroma habituelle
```

À trancher au moment de coder : repli résumé à l'indexation vs à l'ingestion ;
déclenchement (commande CLU dédiée) ; gestion des échecs agent (laisser l'article
en l'état vs status d'erreur).
