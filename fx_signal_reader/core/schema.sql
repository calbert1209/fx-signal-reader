PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- Market prices for USD/JPY, equities, VIX, Gold, and Yahoo yield proxies
CREATE TABLE IF NOT EXISTS market_ticks (
    symbol TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL, -- ISO-8601 UTC (e.g., 2026-11-18T06:00:00Z)
    price REAL NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    volume REAL,
    PRIMARY KEY (symbol, timestamp_utc)
);

-- Calculated US-Japan sovereign yield spreads
CREATE TABLE IF NOT EXISTS yield_spreads (
    timestamp_utc TEXT PRIMARY KEY,
    us_2y REAL,
    us_10y REAL,
    jp_10y REAL,
    spread_10y REAL, -- us_10y - jp_10y
    source TEXT NOT NULL
);

-- Scheduled macro releases (Forex Factory / FRED)
CREATE TABLE IF NOT EXISTS macro_events (
    event_hash TEXT PRIMARY KEY,
    timestamp_utc TEXT NOT NULL,
    currency TEXT NOT NULL,
    title TEXT NOT NULL,
    impact TEXT,     -- High, Medium, Low
    forecast TEXT,
    previous TEXT,
    actual TEXT
);

-- MoF / BoJ intervention headlines (RSS)
CREATE TABLE IF NOT EXISTS news_headlines (
    url_hash TEXT PRIMARY KEY,
    published_utc TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url NOT NULL,
    matched_keywords TEXT -- Comma-separated trigger words
);

CREATE INDEX IF NOT EXISTS idx_ticks_time ON market_ticks(timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_macro_time ON macro_events(timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_news_time ON news_headlines(published_utc DESC);
