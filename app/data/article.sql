CREATE TABLE IF NOT EXISTS article (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reference      TEXT UNIQUE NOT NULL,
    title          TEXT NOT NULL,
    source         TEXT NOT NULL,
    published_date TEXT,
    updated_date   TEXT,  -- date <updated> arXiv (watermark Option A) ; NULL pour TLDR
    content        TEXT NOT NULL,
    url            TEXT NOT NULL,
    tags           TEXT NOT NULL,
    authors        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'ingested' CHECK (status IN ('ingested', 'indexed', 'error')),
    -- SQLite n'a pas de type DATETIME natif : les dates sont stockées en TEXT
    -- au format ISO 8601 (ex: 2026-06-09T14:32:00). CURRENT_TIMESTAMP produit
    -- ce format automatiquement à l'insert.
    ingested_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    indexed_at     TEXT  -- NULL jusqu'à l'indexation dans Chroma
);
