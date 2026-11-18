# P2-02: Modular collector scripts

* **Phase:** Phase 2: Collection Architecture & Local Persistence
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T08:35:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Implement a clean, modular collection architecture in Python for USD/JPY market data, sovereign yields, economic calendar releases, and verbal intervention news feeds with resilient retry/backoff logic and SQLite WAL local persistence.

Key modules implemented under `collectors/`:
1. `collectors/base.py`: Abstract `BaseCollector` class featuring exponential backoff, jitter, HTTP 429 `Retry-After` header parsing, transient error handling, and WAL database initialization.
2. `collectors/market_ticks.py`: Ingests market tickers (`JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F`) via `yfinance`, fetches official JGB 10-Year yield via Ministry of Finance (MoF) Japan CSV (`mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv`), parses Reiwa era dates, calculates 10Y US-Japan sovereign yield spread, and inserts into `market_ticks` and `yield_spreads`.
3. `collectors/calendar.py`: Ingests Forex Factory weekly economic calendar releases (live JSON feed with graceful fallback to cached JSON `data/raw_calendar_thisweek.json` and XML), filters strictly for USD and JPY events, standardizes timestamps to ISO-8601 UTC, computes deterministic 32-character SHA-256 event hashes, and inserts into `macro_events`.
4. `collectors/news_rss.py`: Polls 10 curated feeds (Bank of Japan English/Japanese releases, Nikkei Asia, MoF Japan releases, FXStreet, Investing.com, MarketWatch, Google News alerts), runs the 4-tier verbal intervention regex engine with policymaker entity recognition (Kanda, Mimura, Kato, Suzuki, Ueda), normalizes timestamps, and inserts into `news_headlines` with SHA-256 URL hash deduplication.
5. `collector.py`: Production CLI runner supporting `--collector all|ticks|calendar|news`, `--db-path`, `--schema-path`, and `--retries`.

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete
- [x] Verified in local sandbox / environment
- [x] Linked artifacts and logs updated

### Acceptance Verification Summary:
- **Modular Directory Structure:** `collectors/` package created containing `__init__.py`, `base.py`, `market_ticks.py`, `calendar.py`, `news_rss.py`.
- **CLI Runner:** `collector.py` executable supporting all targeted collector flags and database paths.
- **SQLite WAL Mode:** Verified `PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; PRAGMA foreign_keys = ON;`.
- **Deduplication:** Repeated runs verify 100% idempotent deduplication via primary keys (`(symbol, timestamp_utc)`, `timestamp_utc`, `event_hash`, `url_hash`).

---

## 3. Execution Log & Findings

### 3.1 Initial Ingestion Test (`collector.py --collector all`)
```text
======================================================================
USD/JPY Signal Pipeline - Modular Collector Runner
Start Time (UTC): 2026-11-18T08:33:08Z
Target Database : /Users/albert/Projects/fx-signal-reader/usdjpy_signals.db
Collector Target: ALL
======================================================================

>>> [1/3] Running Market Ticks & Yield Spreads Collector...
[INFO] Fetching JGB 10Y yield from MoF: https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv
[INFO] Successfully parsed MoF JGB 10Y: 2.925% (date: R8.11.17 -> 2026-11-17T00:00:00Z)
[INFO] Market ticks collection complete: 444 total ticks in DB, 10Y Spread: 2.178% (US: 5.103%, JP: 2.925%)

>>> [2/3] Running Economic Calendar Collector...
[INFO] Requesting live JSON feed: https://nfs.faireconomy.media/ff_calendar_thisweek.json
[INFO] Calendar collection complete: 53 USD/JPY events written (53 total in DB)

>>> [3/3] Running News & Intervention RSS Collector...
[INFO] Polled 10 curated feeds: BoJ (EN/JA), FXStreet, Investing.com, MarketWatch, Nikkei Asia, Google News (USD/JPY, MoF/BoJ, Reuters), MoF HTML.
[INFO] News RSS collection complete: 469 processed, 459 total in DB, 150 intervention alerts flagged.

======================================================================
Collection Execution Summary (Completed in 4.02s)
======================================================================
Table Name           | Initial    | Final      | Net Change
----------------------------------------------------------------------
market_ticks         | 0          | 444        | +444      
yield_spreads        | 0          | 1          | +1        
macro_events         | 0          | 53         | +53       
news_headlines       | 0          | 459        | +459      
======================================================================
• Market Ticks: USD/JPY = 159.231, US 10Y = 5.103%, JGB 10Y = 2.925%, 10Y Spread = 2.178%
• Calendar    : 53 USD/JPY events (46 USD, 7 JPY)
• News RSS    : 469 items parsed across 10 feeds, 150 intervention signals flagged
```

### 3.2 Primary Key Deduplication Verification (Second Run)
```text
======================================================================
Collection Execution Summary (Completed in 3.30s)
======================================================================
Table Name           | Initial    | Final      | Net Change
----------------------------------------------------------------------
market_ticks         | 444        | 444        | 0         
yield_spreads        | 1          | 1          | 0         
macro_events         | 53         | 53         | 0         
news_headlines       | 459        | 459        | 0         
======================================================================
```
*Conclusion:* All four tables achieved net change 0 across repeated runs, confirming robust composite and unique primary key deduplication without record duplication.

### 3.3 Key Market Observations
- **USD/JPY:** Spot rate trading at ~159.23.
- **US 10Y Yield (^TNX):** 5.103%.
- **Japan MoF JGB 10Y:** 2.925% (latest publication R8.11.17).
- **10Y Sovereign Yield Spread:** 2.178% (217.8 bps).
- **Verbal Intervention Alerts:** 150 matching headlines identified across wire and central bank feeds spanning Tiers 1–4.
