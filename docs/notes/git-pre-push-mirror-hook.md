# Hook git `pre-push` — mirroring automatique vers un second remote

Hook réutilisable : à chaque `git push` vers **origin**, réplique automatiquement
tout l'état du dépôt (branches + tags) vers un second remote nommé **`mirror`**.

## Le script

```sh
#!/bin/sh
# pre-push : quand on pousse vers origin, répliquer tout l'état (branches + tags)
# vers le remote 'mirror' via --mirror.
#
# Notes :
# - Garde-fou anti-récursion : le push interne vers 'mirror' re-déclenche ce hook,
#   mais avec remote_name='mirror' → le bloc ne s'exécute pas. On n'agit QUE pour origin.
# - --mirror est DESTRUCTIF côté miroir : il rend 'mirror' identique au local
#   (supprime sur le miroir ce qui n'existe plus chez toi).
# - exit 0 final : un échec du mirroring ne bloque jamais le push principal vers origin.

remote_name="$1"

if [ "$remote_name" = "origin" ]; then
    # Ne rien tenter si le remote 'mirror' n'est pas (encore) configuré.
    if git remote get-url mirror >/dev/null 2>&1; then
        echo "↪︎  pre-push : mirroring vers 'mirror'…"
        if git push mirror --mirror; then
            echo "✅  mirroring succeeded"
        else
            echo "⚠️  mirroring failed (push to origin kept)"
        fi
    fi
fi

exit 0
```

## Installation dans un autre repo

```bash
# 1. Coller le script dans le hook (depuis la racine du repo)
#    -> créer/éditer le fichier .git/hooks/pre-push avec le contenu ci-dessus

# 2. Le rendre exécutable
chmod +x .git/hooks/pre-push

# 3. Déclarer le second remote (dépôt cible créé FROM SCRATCH, pas un fork)
git remote add mirror <URL-DU-MIROIR>
```

## Comportement

| Situation | Sortie |
|---|---|
| Push vers origin, miroir répliqué | `↪︎ pre-push : mirroring vers 'mirror'…` puis `✅ mirroring succeeded` |
| Push vers origin, échec du miroir | `↪︎ pre-push…` puis `⚠️ mirroring failed (push to origin kept)` |
| Pas de remote `mirror` configuré | (rien — silencieux) |

- Déclenché par `git push` tout court dès que la branche courante suit `origin`
  (le hook reçoit le nom du remote résolu = `origin`).
- **Ne se déclenche pas** si on pousse une branche sans upstream ou en visant
  explicitement un autre remote.

## Points de vigilance

- **Non versionné** : `.git/hooks/` ne part pas avec le repo → à recréer sur
  chaque clone/machine.
- **`--mirror` est autoritaire côté miroir** : ce qui n'existe plus en local est
  **supprimé** du miroir. Le miroir doit rester un clone jetable, jamais une
  source de vérité.
- **`exit 0` final** : un échec du mirroring n'interrompt jamais le push principal.
- Pour sauter le hook ponctuellement : `git push --no-verify`.
```
