# Bug : endpoint Azure AI Foundry incompatible avec langchain-azure-ai

> **✅ RÉSOLU (2026-06-14)** — migration de `AzureAIChatCompletionsModel`
> (`langchain-azure-ai`, SDK `azure-ai-inference` déprécié, retrait 2026-08-26) vers
> **`ChatOpenAI`** (`langchain-openai`) pointé sur l'endpoint `/openai/v1`. Conforme à
> la préconisation Microsoft 2026 (SDK OpenAI + `/openai/v1`). `langchain-azure-ai` a
> été retirée des dépendances. `make chat-test` renvoie une synthèse correcte (Kimi).
> Voir `app/rag/llm.py:get_llm()`. La « Solution recommandée » ci-dessous est celle qui
> a été appliquée.

## Symptôme

`make chat-test` retourne `"Synthèse indisponible (erreur LLM)"` avec `status: "ok"`.
Les logs backend affichent :

```
LLM call failed: (BadRequest) API version not supported
```

## Cause

`AzureAIChatCompletionsModel` (de `langchain-azure-ai`) utilise le SDK `azure-ai-inference`
qui envoie les requêtes à `{endpoint}/chat/completions?api-version=2024-05-01-preview`.

L'endpoint Azure AI Foundry disponible est de la forme :
`https://eagwu-0283-resource.services.ai.azure.com/openai/v1`

Ce suffixe `/openai/v1` indique une interface **compatible OpenAI**, pas l'interface
Azure AI Inference — les deux protocoles sont incompatibles.

## Pistes testées

| Endpoint | Résultat |
|---|---|
| `https://.../openai/v1` | `API version not supported` |
| `https://.../` | probablement idem (chemin `/chat/completions` incorrect) |
| `https://.../models` | à tester — chemin attendu par azure-ai-inference pour AI Foundry |

## Solution recommandée

Remplacer `AzureAIChatCompletionsModel` de `langchain-azure-ai` par `ChatOpenAI` de
`langchain-openai`, conçu pour les endpoints compatibles OpenAI :

```python
# llm.py
from langchain_openai import ChatOpenAI

ChatOpenAI(
    base_url=settings.azure_ai_inference_endpoint,  # https://.../openai/v1
    api_key=settings.azure_ai_inference_api_key,
    model=settings.azure_ai_inference_model,        # Kimi-K2.6
    temperature=0.2,
)
```

Dépendance à ajouter dans `pyproject.toml` : `langchain-openai>=0.3,<0.4`.
