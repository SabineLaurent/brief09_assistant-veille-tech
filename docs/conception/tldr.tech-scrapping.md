# Scraping TLDR.tech — Note de conception

## Pourquoi TLDR.tech

Site retenu pour le scraping des sources "froides" (core data).
Avantage : préfiltres thématiques déjà en place côté site, contenu structuré et stable.

## Aucune nouvelle dépendance nécessaire

Tout ce qu'il faut est déjà dans `pyproject.toml` :

| Dépendance | Rôle |
|---|---|
| `httpx` | Télécharger les pages HTML |
| `beautifulsoup4` + `lxml` | Parser le HTML |
| `markdownify` | Convertir HTML → Markdown |

TLDR.tech est un site **rendu côté serveur** — le HTML renvoyé contient directement le contenu. Pas besoin de Playwright, Selenium, ou navigateur headless.

## Structure des URLs

TLDR publie des newsletters datées, avec des sous-sections thématiques :

```
https://tldr.tech/tech/2025-06-05     ← newsletter générale
https://tldr.tech/ai/2025-06-05       ← newsletter IA
https://tldr.tech/webdev/2025-06-05   ← newsletter Web Dev
https://tldr.tech/devops/2025-06-05   ← newsletter DevOps
```

## Contenu extrait par page

Chaque page newsletter contient une liste d'articles, chacun avec :
- un **titre**
- un **résumé** (2-3 phrases rédigées par TLDR)
- un **lien** vers la source originale
- une **catégorie** (ex: "Big Tech & Startups", "Programming")

## Approche retenue : scraping superficiel (shallow)

On extrait les **résumés TLDR** tels quels, sans suivre les liens vers les sources originales.

Avantages :
- Simple, rapide, sans appels HTTP supplémentaires
- Contenu déjà nettoyé et résumé par TLDR
- Moins fragile (pas dépendant de la structure HTML de dizaines de sites externes)

## Flux d'implémentation dans `scraper.py`

`Scraper.run(urls)` reçoit une liste d'URLs de newsletters TLDR :

1. Pour chaque URL : `httpx.Client.get(url)` avec `User-Agent` et `timeout`
2. Parser le HTML : `BeautifulSoup(resp.text, "lxml")`
3. `strip_boilerplate(soup)` — retire `<nav>`, `<footer>`, etc.
4. Extraire les blocs d'articles (titre, résumé, lien source, catégorie)
5. Pour chaque article : construire un dict `{id, title, url, source, date, content, tags}`
   - `id` : `hashlib.sha1(url.encode()).hexdigest()` (déterministe)
   - `source` : `"tldr.tech"`
   - `date` : extraite de l'URL (format `YYYY-MM-DD`)
   - `tags` : dérivés de la catégorie TLDR ou de la section (`ai`, `webdev`, etc.)
6. Retourner la liste — chaque URL en échec est logguée et ignorée (pas d'exception levée)

## Ordre d'implémentation

1. `cleaning.py` — prérequis (fonctions pures, pas de réseau)
2. `scraper.py` — utilise `strip_boilerplate` + `clean_html_to_markdown` de `cleaning.py`
