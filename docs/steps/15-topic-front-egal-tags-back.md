# Décision : `topics` du front = `tags` du back

> **Statut : décision tranchée** (2026-06-13). Décision de **normalisation du modèle
> `Article`** — pas encore d'implémentation. Trace la sémantique retenue pour `tags`
> avant d'y toucher dans le code.

## La décision

Les **`topics` sélectionnés dans le front** (chips + saisie libre, ex. `AI`, `Sécurité`,
`Agentique`, `Embarqué`) correspondent **directement au champ `tags`** des articles côté
back. On **réutilise `tags`** comme axe thématique contrôlé — **on n'ajoute pas** de
nouveau champ `topic` au modèle `Article`.

## Pourquoi (raisonnement)

Le modèle a 3 champs candidats pour porter le « thème » ; il fallait éviter les doublons :

| Champ | Rôle retenu | Vocabulaire | Exemple |
|---|---|---|---|
| `source` | plateforme d'origine (provenance) | imposé par la source | `"arXiv"`, `"tldr.tech"` |
| **`tags`** ← *repensé* | **catégorie thématique = topics du front** | **fermé / contrôlé** | `["AI", "Sécurité"]` |
| `keywords` | contenu précis de l'article | ouvert / libre | `["RAG", "prompt injection"]` |

- Le sens initial de `tags` (« provenance ») **faisait doublon avec `source`**, qui contient
  déjà la plateforme (`source="arXiv"` / `"tldr.tech"`). La sous-taxonomie native qui restait
  dans `tags` (code arXiv `cs.AI`, édition/rubrique TLDR) a peu de valeur d'affichage.
- En libérant `tags` de la provenance (déjà couverte par `source`), il devient l'axe
  **thématique** naturel — exactement ce que le front veut filtrer.
- Le chevauchement à surveiller n'est PAS `tags`↔`source` mais `tags`↔`keywords` : tous deux
  décrivent le contenu, à deux granularités → `tags` = **liste fermée** (filtre),
  `keywords` = **liste ouverte** (richesse sémantique). Tant que cette distinction tient,
  les deux se justifient.

## Conséquences / reste à faire (pas encore fait)

- **Pas de migration** (base dev recréée à neuf) — éditer `article.sql` directement si besoin
  (a priori `tags` existe déjà, seul son **sens** change).
- `app/ingest/models.py` — réécrire le commentaire de `tags` (provenance → catégorie
  thématique contrôlée).
- **Ingesters arXiv / TLDR** — remplir `tags` via un **mapping catégorie** vers le
  vocabulaire fermé (`cs.AI → "AI"`, `cs.CR → "Sécurité"`…) au lieu du code brut. Un simple
  dico suffit pour l'instant ; l'agent de catégorisation (TODO « Amélioration » pt.3)
  raffinera plus tard.
- **Front** — le **filtrage de la requête vers Chroma** (les `tags` sélectionnés deviennent
  un filtre `where` sur les métadonnées) est **reporté** : à faire « côté front plus tard ».
  Aujourd'hui les topics ne font que de l'expansion de requête sémantique
  (`chat.py:_expand_query`), pas un filtre strict.

## Décision encore ouverte

- **Liste fermée exacte des catégories** : le TODO pt.3 impose *uniquement* `AI`, `Sécurité`,
  `Agentique`, `Embarqué`. L'exemple du front incluait « Python » → à arbitrer (garder les 4,
  ou étendre).
