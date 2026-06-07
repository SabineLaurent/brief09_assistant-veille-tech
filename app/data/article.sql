CREATE TABLE IF NOT EXISTS article (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reference      TEXT UNIQUE NOT NULL,
    title          TEXT,
    source         TEXT,
    published_date TEXT,
    content        TEXT,
    url            TEXT,
    tags           TEXT,
    authors        TEXT
);
