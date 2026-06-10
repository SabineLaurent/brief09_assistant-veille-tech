# Étape 11 — Fix « Synthèse indisponible (erreur LLM) » - 2026-06-10

## Symptôme

Le frontend affichait pour chaque question :

> SYNTHÈSE — Synthèse indisponible (erreur LLM). 8 article(s) référencé(s).

Le retrieval fonctionnait (8 articles trouvés dans Chroma), mais l'appel Azure AI
échouait à chaque fois et `compose_answer` tombait dans son repli d'erreur
(`app/rag/llm.py`, bloc `try/except` autour de `llm.ainvoke`).

Les logs backend (`docker compose logs backend`) donnaient la cause exacte :

```
LLM call failed: (NoModelName) No model specified in request.
Please provide a model name in the request body or as a
x-ms-model-mesh-model-name header.
```

---

## Point 1 — Le kwarg `model_name=` silencieusement ignoré

### Diagnostic

La requête arrivait chez Azure **sans nom de modèle**, alors que `get_llm()`
passait bien `model_name=settings.azure_ai_inference_model` (vérifié dans le
conteneur : variable définie, 9 caractères).

Le piège est dans `langchain-azure-ai` 0.1.4 : le champ est déclaré avec un
**alias Pydantic** :

```python
model_name: Optional[str] = Field(default=None, alias="model")
```

Avec un alias et sans `populate_by_name`, Pydantic n'accepte que l'alias au
constructeur : le kwarg `model_name=` est **silencieusement ignoré** et le champ
reste `None`. La bibliothèque tente alors de deviner le modèle en interrogeant
l'endpoint (`get_model_info()`), échoue sans lever, et envoie ensuite chaque
requête sans `model` → erreur `NoModelName` à chaque appel.

Preuve (reproduite dans le conteneur) :

```python
AzureAIChatCompletionsModel(..., model_name="test-model").model_name  # → None
AzureAIChatCompletionsModel(..., model="test-model").model_name      # → 'test-model'
```

### Correctif

Dans `app/rag/llm.py` (`get_llm`) : passer le modèle via l'alias.

```python
# avant
model_name=settings.azure_ai_inference_model,
# après
model=settings.azure_ai_inference_model,
```

---

## Point 2 — Le JSON du LLM enveloppé dans des barrières Markdown

### Diagnostic

Une fois le LLM joignable, second bug masqué derrière le premier : malgré la
consigne « JSON strict » du system prompt, le modèle enveloppe sa réponse dans
des barrières Markdown :

````
```json
{ "answer": "...", "cards": [...] }
```
````

`_extract_answer` faisait `json.loads(raw)` directement → `JSONDecodeError` →
repli sur le texte brut : le frontend aurait affiché **tout le blob JSON** au
lieu de la synthèse.

### Correctif

Dans `_extract_answer` (`app/rag/llm.py`) : retirer les barrières ` ```json ` /
` ``` ` avant le parsing (`removeprefix`/`removesuffix`), puis parser le JSON
comme avant.

---

## Validation

`POST /chat` (question d'exemple de `make chat-test`) renvoie désormais
`status="ok"`, 8 cartes, et une synthèse propre en français avec références
`[1]`, `[6]`… au lieu du message de repli.

---

## Problèmes repérés au passage (non corrigés à ce stade)

1. **`make chat-test` cassé sur l'hôte** : la cible pipe vers
   `python -m json.tool`, mais seul `python3` existe sur la machine
   (`/bin/sh: python: command not found`). Correction envisagée : remplacer
   `python` par `python3` dans le Makefile.

2. **Tags affichés en un seul gros tag sur les cartes** : l'indexeur joint les
   tags avec `"|"` (`"cs.CR|prompt injection|jailbreak…"`, cf.
   `app/indexing/indexer.py` et `Article.to_chroma_metadata`), mais
   `_split_tags` (`app/rag/llm.py`) ne découpe que sur `","`. Résultat : chaque
   carte porte un unique tag géant. Correction envisagée : faire découper
   `_split_tags` aussi sur `"|"`, ou harmoniser le séparateur (`", "`) côté
   indexeur.
