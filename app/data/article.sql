CREATE TABLE IF NOT EXISTS article (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reference      TEXT UNIQUE NOT NULL,
    title          TEXT NOT NULL,
    source         TEXT NOT NULL,
    published_date TEXT,
    content        TEXT NOT NULL,
    url            TEXT NOT NULL,
    tags           TEXT NOT NULL,
    authors        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'ingested' CHECK (status IN ('ingested', 'indexed', 'error'))
);
