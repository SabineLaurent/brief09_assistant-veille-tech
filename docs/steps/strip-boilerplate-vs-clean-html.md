# strip_boilerplate vs clean_html_to_markdown

Deux fonctions qui touchent du HTML, mais à des moments différents du pipeline.

## strip_boilerplate(soup) — Chirurgien

- Reçoit un objet `BeautifulSoup` (l'arbre HTML complet d'une page)
- Retire des sections entières par balise : `<nav>`, `<footer>`, `<script>`, `<style>`
- Retourne toujours un `BeautifulSoup` — le HTML est encore là, juste amputé du bruit
- Se fait **avant** d'extraire le contenu

```
HTML complet → [strip_boilerplate] → HTML sans menus/pieds de page
```

## clean_html_to_markdown(html) — Traducteur

- Reçoit une chaîne HTML (typiquement le fragment utile qu'on veut garder)
- Convertit toute la syntaxe HTML en Markdown : `<h1>` → `# `, `<b>` → `**`, `<p>` → saut de ligne, etc.
- On impose `heading_style="ATX"` à `markdownify` pour forcer le style de titres avec `#`
- Retourne une `str` Markdown — plus de HTML du tout
- Se fait **après** le découpage, sur le contenu qu'on veut indexer

```
HTML utile → [clean_html_to_markdown] → texte Markdown
```

## Dans le pipeline, les deux s'enchaînent

```
Page HTML brute
    ↓  strip_boilerplate()      → retire nav, footer, scripts
    ↓  extraction du contenu    → on isole le <main>, les blocs d'articles
    ↓  clean_html_to_markdown() → convertit le HTML restant en Markdown
    ↓  chunk()                  → découpe en morceaux pour Chroma
```

`strip_boilerplate` enlève ce qu'on ne veut pas, `clean_html_to_markdown` transforme ce qui reste.

## Aparté : style ATX vs Setext

`heading_style="ATX"` impose le style de titres Markdown le plus courant, avec des `#` :

```markdown
# Titre niveau 1
## Titre niveau 2
### Titre niveau 3
```

L'alternative est le style **Setext**, qui utilise des soulignements :

```markdown
Titre niveau 1
==============

Titre niveau 2
--------------
```

Les deux sont du Markdown valide. ATX est choisi ici pour sa lisibilité et sa cohérence
(fonctionne pour tous les niveaux, pas seulement h1 et h2).
