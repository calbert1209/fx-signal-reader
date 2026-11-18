# USD/JPY Automated Signal Pipeline: Architecture & Task Breakdown

## 1. Session Background & Objective

This document captures the design and implementation plan for an automated, local-first data pipeline to monitor and analyze **USD/JPY** trading signals using freely available data sources.

Among major FX pairs, USD/JPY offers high liquidity, sustained directional trends, and strong activity across both the Tokyo and New York sessions. Rather than relying purely on intraday price patterns, USD/JPY is heavily driven by sovereign bond yields, central bank divergence, risk sentiment, and official intervention risks.

### Core Signals Tracked
* **US–Japan Yield Spreads (Primary Engine):** US 2Y and 10Y Treasury yields relative to Japanese Government Bond (JGB) 10Y yields (`US 10Y - JGB 10Y`). Widening spreads drive carry-trade inflows (higher USD/JPY); narrowing spreads trigger carry unwinds.
* **Central Bank Policy & Tier-1 Macro Data:** FOMC decisions, US CPI, Non-Farm Payrolls (NFP), Bank of Japan (BoJ) meetings, Tokyo/National Core CPI, and annual Shunto wage data.
* **MoF / BoJ Intervention Risk:** Escalating verbal intervention from Japan's Ministry of Finance (MoF) or BoJ rate checks near major psychological levels (e.g., 160.00), which can trigger sudden, multi-hundred-pip reversals.
* **Global Risk Sentiment:** Correlations with US equities (S&P 500), Japanese equities (Nikkei 225), volatility (VIX), and Gold.
* **Session Dynamics & Technical Anchors:** Tokyo Fix (09:55 JST) order flows, London/NY overlap liquidity sweeps, 50/200-day moving averages, and round-number option barriers.

---

## 2. Data Source Mapping (Free Tier)

| Indicator | Free Data Source | Collection Method / Identifiers |
| :--- | :--- | :--- |
| **US 2Y & 10Y Yields** | FRED (Federal Reserve Economic Data) | FRED REST API (`DGS2`, `DGS10`) or Nasdaq Data Link API |
| **JGB 10Y Yield** | Japan Ministry of Finance (MoF) / Yahoo Finance | MoF daily CSV (`mof.go.jp/jgbs/.../jgbcm.csv`) benchmark (Yahoo `^JB10YR` delisted) |
| **USD/JPY & Risk Assets** | Yahoo Finance | `yfinance` (`JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F`) |
| **Macro Economic Calendar** | Forex Factory | Weekly JSON/XML calendar feed (scheduled times, currency, impact, forecast, actual) |
| **MoF / BoJ Intervention News** | Reuters / Nikkei Asia / BoJ / FT | RSS feeds parsed against verbal intervention keyword triggers |

---

## 3. Proposed System Architecture

The architecture is designed to run locally with minimal cloud dependencies, combining lightweight ingestion scripts, zero-config storage, and an agentic analysis layer.

```text
[Free APIs / RSS / yfinance]
             │
             ▼
┌────────────────────────────┐
│  Dockerized Collectors     │  (Python / Go workers on tiered schedules)
└────────────┬───────────────┘
             │ Normalized UTC Writes
             ▼
┌────────────────────────────┐
│  Local SQLite (WAL Mode)   │  (market_ticks, yield_spreads, macro_events, news_headlines)
└────────────┬───────────────┘
             │ Read-Only Queries
             ▼
┌────────────────────────────┐
│  MCP Tool Layer            │  (Custom SQLite MCP Server + Nasdaq Data Link MCP Server)
└────────────┬───────────────┘
             │ Structured Tool Calls
             ▼
┌────────────────────────────┐
│  LangGraph Multi-Agent     │  (Macro Analyst + Risk Committee -> Human Approval Gate)
└────────────────────────────┘
```

### Key Architectural Components
1. **Ingestion Layer:** Modular collector scripts (Python for `yfinance`, Go or Python for REST/RSS polling) with retry/backoff logic and UTC timestamp normalization.
2. **Local Persistence:** A local **SQLite** database configured in **WAL (Write-Ahead Logging)** mode to allow concurrent reads by AI agents while collectors write new data.
3. **Containerized Hosting:** Packaged via `Dockerfile` and `docker-compose.yml` with a bind-mounted host volume for the SQLite file, suitable for running continuously on a local desktop (e.g., Mac mini) or SBC (e.g., Banana Pi Pro).
4. **Agentic Integration (MCP & LangGraph):**
   * A custom, read-only **Model Context Protocol (MCP)** server exposing clean domain tools (e.g., `get_latest_yield_spread`, `get_recent_mof_alerts`) alongside the **Nasdaq Data Link MCP server**.
   * A **LangGraph** workflow separating responsibilities between a **Macro Analyst** node and a **Risk Committee** node, ending in a human-in-the-loop approval gate before paper-trade execution.

---

## 4. Phased Task Breakdown

### Phase 1: Data Source Mapping & API Setup
- [x] **FRED API provisioning:** Register a free FRED API key and verify REST calls for `DGS2` and `DGS10` (`2026-11-16` latest verified: `DGS2=4.83%`, `DGS10=5.12%`).
- [x] **Yahoo Finance ticker validation:** Verify availability, delay, and formatting for `JPY=X`, `^TNX`, `^JB10YR`, `^GSPC`, `^N225`, `^VIX`, and `GC=F` (`2026-11-18` verified: `JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F` active; `^JB10YR` delisted -> MoF daily CSV validated as canonical source).
- [x] **Economic calendar mapping:** Fetch the Forex Factory weekly JSON/XML feed and map fields for timestamp, currency (`USD`/`JPY`), impact, forecast, and actuals (`2026-11-18` verified: 53 USD/JPY events mapped, ISO-8601 UTC normalized, deterministic SHA256 hashed, and SQLite deduplication validated).
- [x] **Intervention feed curation:** Compile active RSS URLs (Reuters, Nikkei Asia, BoJ) and define a regex/keyword trigger list for verbal intervention language (`2026-11-18` verified: 10 active institutional/wire feeds verified with 100% HTTP 200, 4-tier verbal escalation ladder defined, 458 unique headlines ingested into `news_headlines`, 151 intervention alerts categorized, 12/12 benchmark cases passed).

### Phase 2: Collection Architecture & Local Persistence
- [x] **SQLite schema design:** Define `market_ticks`, `yield_spreads`, `macro_events`, and `news_headlines` with WAL mode and deduplication keys (`schema.sql` drafted).
- [x] **Modular collector scripts:** Implement workers for market ticks & yields (`collectors/market_ticks.py`), Forex Factory calendar events (`collectors/calendar.py`), and RSS intervention headlines (`collectors/news_rss.py`) with retry/backoff logic (`collectors/base.py`) and CLI entrypoint (`collector.py`).
- [x] **Normalization & spread calculation:** Enforce ISO-8601 UTC timestamps across all sources, deduplicate news via URL hashes, and compute `US10Y - JGB10Y` automatically on write (implemented in `normalizer.py` and `spread_calculator.py` with 28/28 unit tests passing).
- [x] **Containerization:** Create `Dockerfile` and `docker-compose.yml` with a persistent bind mount for `usdjpy_signals.db` (Python 3.11-slim, non-root user `appuser`, healthcheck, SIGTERM stopsignal).
- [x] **Tiered scheduling:** Configure polling cadences in `scheduler.py` (5m RSS headlines, 15m market ticks with Tokyo Fix expedited cadence, daily 00:05 UTC calendar refresh).

### Phase 3: Agent Integration (MCP & LangGraph)
- [ ] **Custom SQLite MCP server:** Build a local read-only MCP server exposing tools such as `get_latest_yield_spread`, `get_upcoming_macro_events`, and `get_recent_mof_alerts`.
- [ ] **Multi-server MCP setup:** Wire the custom SQLite MCP server alongside the Nasdaq Data Link MCP server and verify tool discovery.
- [ ] **LangGraph state definition:** Define the shared graph state schema (yield bias, risk-on/risk-off regime, intervention risk score, trade proposal).
- [ ] **Specialist agent nodes:** Implement the Macro Analyst node (yields + calendar) and Risk Committee node (VIX + MoF headlines) with structured outputs.
- [ ] **Approval gate & paper execution:** Connect nodes with a human-in-the-loop interrupt before routing approved signals to paper-trading execution.

---

## 5. Current Implementation Status

Two starter artifacts have been drafted to kick off Phase 2:
* **`schema.sql`:** Configures SQLite in WAL mode and creates the four core tables (`market_ticks`, `yield_spreads`, `macro_events`, `news_headlines`) with descending timestamp indexes.
* **`collector.py`:** Initializes the database, pulls core market and yield tickers via `yfinance` (with a daily fallback for `^JB10YR`), optionally pulls official US 2Y/10Y yields via FRED when `FRED_API_KEY` is provided, and writes the calculated 10Y spread to `yield_spreads`.