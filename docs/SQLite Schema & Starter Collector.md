# USD/JPY Signal Pipeline: SQLite Schema & Starter Collector

This document contains the initial local persistence schema (`schema.sql`) and starter Python ingestion script (`collector.py`) for collecting USD/JPY market tickers and US–Japan sovereign yield spreads.

---

## 1. SQLite Database Schema (`schema.sql`)

Enable **WAL (Write-Ahead Logging)** mode so your MCP server and trading agents can read from the database concurrently while collectors are writing. Composite primary keys and unique hashes prevent duplicate rows on repeated runs.

```sql
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
    url TEXT NOT NULL,
    matched_keywords TEXT -- Comma-separated trigger words
);

CREATE INDEX IF NOT EXISTS idx_ticks_time ON market_ticks(timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_macro_time ON macro_events(timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_news_time ON news_headlines(published_utc DESC);
```

---

## 2. Starter Python Collector (`collector.py`)

This script initializes the SQLite database, pulls the core market tickers via `yfinance`, fetches official US 2Y/10Y yields from FRED (if `FRED_API_KEY` is set, otherwise falling back to `^TNX`), and logs the `US10Y - JGB10Y` spread.

### Dependencies

```bash
pip install yfinance requests
```

### Script Implementation

```python
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import requests
import yfinance as yf

DB_PATH = Path(os.getenv("FX_DB_PATH", "usdjpy_signals.db"))
SCHEMA_PATH = Path("schema.sql")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

TICKERS = ["JPY=X", "^TNX", "^JB10YR", "^GSPC", "^N225", "^VIX", "GC=F"]


def init_db(conn: sqlite3.Connection) -> None:
    if SCHEMA_PATH.exists():
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    else:
        conn.execute("PRAGMA journal_mode = WAL;")


def fetch_fred_latest(series_id: str) -> float | None:
    if not FRED_API_KEY:
        return None
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 5,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        for obs in resp.json().get("observations", []):
            if obs["value"] != ".":
                return float(obs["value"])
    except Exception as exc:
        print(f"[WARN] FRED fetch failed for {series_id}: {exc}")
    return None


def collect_market_ticks(conn: sqlite3.Connection) -> dict[str, float]:
    latest_prices: dict[str, float] = {}
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for symbol in TICKERS:
        try:
            hist = yf.Ticker(symbol).history(period="2d", interval="15m")
            if hist.empty:
                hist = yf.Ticker(symbol).history(period="5d", interval="1d")
            if hist.empty:
                print(f"[WARN] No data returned for {symbol}")
                continue

            row = hist.iloc[-1]
            price = float(row["Close"])
            latest_prices[symbol] = price

            conn.execute(
                """
                INSERT OR REPLACE INTO market_ticks
                (symbol, timestamp_utc, price, open, high, low, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    now_utc,
                    price,
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Volume"]) if row["Volume"] == row["Volume"] else 0.0,
                ),
            )
        except Exception as exc:
            print(f"[WARN] Failed to collect {symbol}: {exc}")

    return latest_prices


def collect_yield_spread(conn: sqlite3.Connection, latest_prices: dict[str, float]) -> None:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    us_2y = fetch_fred_latest("DGS2")
    us_10y = fetch_fred_latest("DGS10") or latest_prices.get("^TNX")
    jp_10y = latest_prices.get("^JB10YR")

    spread_10y = round(us_10y - jp_10y, 4) if (us_10y is not None and jp_10y is not None) else None
    source = "FRED+yfinance" if FRED_API_KEY else "yfinance"

    conn.execute(
        """
        INSERT OR REPLACE INTO yield_spreads
        (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (now_utc, us_2y, us_10y, jp_10y, spread_10y, source),
    )


def main() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        latest_prices = collect_market_ticks(conn)
        collect_yield_spread(conn, latest_prices)
        conn.commit()
    print(f"[OK] Collected {len(latest_prices)} tickers into {DB_PATH}")


if __name__ == "__main__":
    main()
```

---

## 3. Running Notes

* **Environment Variables:** Set `FRED_API_KEY` to pull official US 2Y/10Y Treasury yields from FRED (`DGS2`, `DGS10`). Optionally override the database file location with `FX_DB_PATH`.
* **Yahoo Finance JGB Ticker:** `^JB10YR` occasionally has delayed or missing intraday bars; the script automatically falls back to daily candles (`interval="1d"`) if the 15-minute window returns empty.
* **Scheduling:** Run `python collector.py` on a 15-minute `cron` interval during active Tokyo/London/NY sessions, or wrap it in a loop inside a Docker container.