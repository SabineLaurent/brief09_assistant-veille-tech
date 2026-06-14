CREATE TABLE IF NOT EXISTS article (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reference      TEXT UNIQUE NOT NULL,
    title          TEXT NOT NULL,
    source         TEXT NOT NULL,
    published_date TEXT,
    updated_date   TEXT,  -- date <updated> arXiv (watermark Option A) ; NULL pour TLDR
    content        TEXT NOT NULL,
    url            TEXT NOT NULL,
    tags           TEXT NOT NULL,                -- provenance/requête (JSON liste) : ex. catégorie arXiv
    keywords       TEXT NOT NULL DEFAULT '[]',   -- mots-clés de contenu (JSON liste) ; remplis par l'agent (TODO pt.3)
    authors        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'ingested' CHECK (status IN ('ingested', 'indexed', 'error')),
    -- SQLite n'a pas de type DATETIME natif : les dates sont stockées en TEXT
    -- au format ISO 8601 (ex: 2026-06-09T14:32:00). CURRENT_TIMESTAMP produit
    -- ce format automatiquement à l'insert.
    ingested_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    indexed_at     TEXT,  -- NULL jusqu'à l'indexation dans Chroma
    -- NULL tant que l'agent LLM d'enrichissement (résumé/keywords/tags, TODO pt.3)
    -- n'a pas traité l'article. Sert de signal de lecture : on enrichit ceux dont
    -- llm_reviewed_at IS NULL. Orthogonal à `status` (un article peut être enrichi
    -- indépendamment d'être indexé). Ajouté en dernière colonne pour coïncider avec
    -- l'ALTER additif de migrate.py sur les bases existantes.
    llm_reviewed_at TEXT
);
