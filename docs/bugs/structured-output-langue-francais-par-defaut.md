# Bug : l'agent de review sort en français par défaut en structured output

> **✅ RÉSOLU (2026-06-14)** — persona anglophone + directive explicite « STRICTLY IN
> ENGLISH » dans le system prompt de `app/review/reviewer.py`. Le modèle ne peut pas
> être laissé en auto-détection de langue en structured output. Voir `_build_system_prompt`.

## Symptôme

L'agent de review (`gpt-5.4-mini`, Foundry, `app/review/reviewer.py`) rédige le résumé
et les mots-clés **en français**, alors que les articles traités sont **en anglais**
(arXiv, TLDR, RSS). Exemple : pour un article Engadget anglais, le résumé sortait
« Meta travaillerait sur un pendentif IA… » au lieu de l'anglais.

Conséquence métier : incohérence linguistique dans la base vectorielle. Le `content`
résumé étant **embedé**, mélanger français et anglais dégrade la métrique sémantique et
la qualité du retrieval.

## Cause

En **structured output** (`ChatOpenAI.with_structured_output(schema)`), le modèle
**n'honore pas** une consigne de prompt du type « écris dans la langue de l'article » :
il retombe sur le **français par défaut**, même quand tout le contenu fourni est anglais.

Ce n'est **pas** un bridage du déploiement, ni un problème de localisation : c'est un
comportement propre au mode function-calling/structured output de ce modèle, qui
n'applique pas l'instruction d'« auto-détection de langue ».

## Investigation (tests successifs)

| Test | Entrée | Sortie | Conclusion |
|---|---|---|---|
| Appel **brut** (sans schéma), `reply in same language as user` | contenu EN | **EN** ✓ | le modèle sait suivre la langue → pas un bridage du déploiement |
| Appel **brut**, contrôle | contenu FR | **FR** ✓ | miroir de langue correct en mode brut |
| **Structured output**, prompt + schéma EN, « match the article's language » | contenu EN propre | **FR** ✗ | le structured output ignore l'auto-détection |
| **Structured output**, ordre explicite « STRICTLY IN ENGLISH » | contenu EN | **EN** ✓ | une directive **explicite** est respectée |

Fausses pistes écartées en chemin :
- **Descriptions `Field(...)` du schéma en français** : suspectées (elles sont injectées
  dans le JSON schema), traduites en anglais — **n'a pas suffi** à elles seules.
- **Déploiement bridé FR** : réfuté par les appels bruts (EN→EN, FR→FR).
- **Texte scrapé non anglais** : réfuté en rejouant avec un contenu anglais écrit à la main.

## Solution appliquée

Ne pas compter sur l'auto-détection de langue en structured output. Dans
`_build_system_prompt` :

1. **persona anglophone** : « You are an English-speaking … annotation agent ».
2. **directive explicite** : « Write the summary and the keywords STRICTLY IN ENGLISH ».

Légitime car le corpus est **entièrement anglophone** (arXiv, TLDR, RSS
HuggingFace/OpenAI) : « fidèle à la langue de l'article » ≡ « anglais » en pratique.

## Quand reconsidérer

Si une **source francophone** est ajoutée à l'ingestion, l'anglais forcé deviendrait
faux. Bascule prévue : **détecter la langue de l'article côté code** et injecter une
directive explicite « write in {lang} » — l'ordre explicite, lui, est fiable (prouvé
ci-dessus). On garde le mécanisme, on rend juste la langue cible dynamique.
