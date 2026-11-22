# USD/JPY Automated Signal Pipeline: Architecture & Task Breakdown

> [!NOTE]
> **Archival Architecture Reference**: This document captures the foundational Phase 1–7 architecture originally designed specifically for **USD/JPY**. In Phase 8, the system generalized to a multi-pair platform supporting both **USD/JPY** and **EUR/USD**. For current multi-pair architecture, registry profiles, and EUR/USD specifications, see [Multi-Pair Expansion Plan.md](Multi-Pair%20Expansion%20Plan.md) and [README.md](../README.md).

## 1. Session Background & Objective

This document captures the design and implementation plan for an automated, local-first data pipeline to monitor and analyze **USD/JPY** trading signals using freely available data sources.

Among major FX pairs, USD/JPY offers high liquidity, sustained directional trends, and strong activity across both the Tokyo and New York sessions. Rather than relying purely on intraday price patterns, USD/JPY is heavily driven by sovereign bond yields, central bank divergence, risk sentiment, and official intervention risks.

### Core Signals Tracked
* **US–Japan Yield Spreads (Primary Engine — 10Y & 2Y):** US 2Y and 10Y Treasury yields relative to Japanese Government Bond (JGB) 2Y (`2年`) and 10Y (`10年`) yields (`US 10Y - JGB 10Y` and `US 2Y - JGB 2Y`). Widening spreads drive carry-trade inflows (higher USD/JPY); narrowing spreads trigger carry unwinds. The 2-Year sovereign spread provides front-end policy confirmation (`+0.05` confidence boost) or curve divergence dampening (`-0.05` confidence).
* **Crude Oil (`CL=F`) Terms-of-Trade Momentum:** WTI Crude Oil Futures (`CL=F`) 5-day/20-day RoC and 50-day EMA modeling Japan's energy import balance (`SURGING` oil acts as an import cost drag / JPY headwind; `PLUNGING` oil acts as terms-of-trade relief / JPY supportive).
* **Tokyo Gotobi & Nakane (09:55 JST) Fixing Session Engine:** Deterministic calendar detection of corporate settlement dates (days 5, 10, 15, 20, 25, 30, and month-end with weekend/holiday rollback to the preceding business day) and intraday Pre-Fix (08:30–09:55 JST) `BUY` conviction boosts and Post-Fix (09:55–11:00 JST) exhaustion alerts.
* **Central Bank Policy & Tier-1 Macro Data:** FOMC decisions, US CPI, Non-Farm Payrolls (NFP), Bank of Japan (BoJ) meetings, Tokyo/National Core CPI, and annual Shunto wage data.
* **MoF / BoJ Intervention Risk:** Escalating verbal intervention from Japan's Ministry of Finance (MoF) or BoJ rate checks near major psychological levels (e.g., 160.00), which can trigger sudden, multi-hundred-pip reversals.
* **Global Risk Sentiment:** Correlations with US equities (S&P 500), Japanese equities (Nikkei 225), volatility (VIX), and Gold.
* **Session Dynamics & Technical Anchors:** Tokyo Fix (09:55 JST) order flows, London/NY overlap liquidity sweeps, 50/200-day moving averages, and round-number option barriers.

---

## 2. Data Source Mapping (Free Tier)

| Indicator | Free Data Source | Collection Method / Identifiers |
| :--- | :--- | :--- |
| **US 2Y & 10Y Yields** | FRED (Federal Reserve Economic Data) / Yahoo Finance | FRED REST API (`DGS2`, `DGS10`) with Yahoo Finance (`2YY=F`, `^TNX`) fallback |
| **JGB 2Y & 10Y Yields** | Japan Ministry of Finance (MoF) | MoF daily CSV (`mof.go.jp/jgbs/.../jgbcm.csv`) multi-tenor benchmark (`2年` & `10年`) |
| **USD/JPY, Risk & Energy Assets** | Yahoo Finance | `yfinance` (`JPY=X`, `^TNX`, `2YY=F`, `^GSPC`, `^N225`, `^VIX`, `GC=F`, `CL=F`) |
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
- [x] **Custom SQLite MCP server:** Built read-only SQLite MCP server (`mcp_server.py`, `?mode=ro` + `PRAGMA query_only=ON`) exposing `get_latest_yield_spread`, `get_upcoming_macro_events`, `get_recent_mof_alerts`, and `get_market_ticks_summary` over stdio (`15/15` tests passing).
- [x] **Multi-server MCP setup:** Wired `usdjpy-sqlite` alongside `nasdaq-data-link` via `mcp_servers.json` and `MultiServerMCPClient` (`mcp_client.py`), verifying simultaneous stdio tool discovery (9 namespaced tools + `nasdaq://databases` resource; `11/11` tests passing).
- [x] **LangGraph state definition:** Defined Pydantic v2 structured models (`MacroAssessment`, `RiskAssessment`, `TradeProposal`, `ExecutionReceipt`) and shared `AgentState` `TypedDict` with `operator.add` reducer on `audit_log` (`agent/state.py`, `15/15` tests passing).
- [x] **Specialist agent nodes:** Implemented `macro_analyst_node` and `risk_committee_node` (`agent/nodes.py`) consuming read-only SQLite tools and enforcing 4-tier MoF intervention veto logic (`9/9` tests passing).
- [x] **Approval gate & paper execution:** Built full `StateGraph(AgentState)` with `InMemorySaver` checkpointer, `interrupt_before=["paper_execution"]` human gate, JSONL paper ledger (`data/paper_trades.jsonl`), and CLI runner `run_pipeline.py` (`7/7` tests passing).

### Phase 9: Advanced USD/JPY Signals (Yield Curve, Terms-of-Trade & Intraday Fix)
- [x] **P9-01 (2Y Sovereign Yield Spread & Schema Migration):** Add `jp_2y` and `spread_2y` columns to `yield_spreads` with safe migration, extract 2Y JGB from MoF CSV, add Yahoo `2YY=F` fallback for US 2Y, and implement `compute_2y_yield_spread`. *(Parallel Track A)*
- [x] **P9-02 (Crude Oil Terms-of-Trade Momentum & Ticker Ingestion):** Add `CL=F` (WTI crude) to `MarketTicksCollector` watchlist, implement `compute_commodity_momentum` (5d/20d RoC and EMA-50) in `core/indicators.py`, and store candles in `market_ticks`. *(Parallel Track B)*
- [x] **P9-03 (Tokyo Gotobi & Nakane Fixing Intraday Session Engine):** Implement `core/gotobi.py` with Japanese holiday/weekend business day rollback, Gotobi settlement detection (5/10/15/20/25/30/EOM), and Pre-Fix/Post-Fix window evaluation. *(Parallel Track C)*
- [x] **P9-04 (Specialist Agent Nodes & MCP Layer Integration):** Integrate P1 (2Y spread confirmation/curve divergence), P2 (symmetric terms-of-trade oil bias), and P3 (Gotobi pre-fix conviction boost) into `MacroAssessment` and `TradeProposal` in `agent/nodes.py`, and expose via MCP server. *(Downstream Track D)*
- [x] **P9-05 (Historical Backfill & Backtest Simulator Integration):** Extend `fx-backtest --backfill` to fetch historical `CL=F` candles and 2Y sovereign yields (MoF 2Y and FRED/Yahoo 2Y), ensuring all 3 signals operate during historical replays. *(Downstream Track E)*
- [x] **P9-06 (Multi-Signal Documentation Overhaul & Verification):** Synchronize `README.md`, `AGENTS.md`, `docs/AGENT_GUIDE.md`, and `USD-JPY Signal Pipeline Plan.md` with the 3 new signals, verify full regression test suite (355 tests), and validate CLI entrypoints. *(Final Track F)*

### Phase 10: Telegram Remote Approval Gate & Daily Summary Engine
- [x] **P10-01 (Telegram Settings Persistence & Bot API Client):** Implement `TelegramConfig`, 24-hour JST time parser (`parse_jst_time` for `H:MM`/`HH:MM`), persistent `TelegramSettings` (`data/telegram_settings.json`, enabled by default at `09:00` JST), and synchronous `TelegramBotClient` (`sendMessage`, `editMessageText`, `answerCallbackQuery`, `getUpdates`). *(Parallel Track A)*
- [x] **P10-02 (Trade Proposal & Daily Summary Message Formatters):** Implement mobile-friendly formatters for actionable trade approval alerts, post-decision execution receipts, and daily/on-demand market & portfolio digests including the mandatory Summary Settings Block. *(Parallel Track B)*
- [x] **P10-03 (Interactive Approval Loop & `/summary` Command Controller):** Implement `TelegramNotifier` orchestrating 2-way inline-button trade approval (`[Approve]` / `[Reject]`) with timeout auto-rejection and interactive `/summary on [HH:MM]`, `/summary off`, and `/summary now` command handling. *(Downstream Track C)*
- [x] **P10-04 (Pipeline CLI & Tiered Scheduler Telegram Integration):** Wire `--telegram`, `--telegram-timeout`, `--telegram-test`, and `--telegram-summary` into `fx-pipeline`, and integrate command polling, dynamic JST daily summary scheduling, and `--run-pipeline` into `fx-schedule`. *(Downstream Track D)*
- [x] **P10-05 (Telegram Test Suite, Configuration & Documentation):** Build `tests/test_telegram_approval.py`, update `.env.example`, `README.md`, `AGENTS.md`, `docs/AGENT_GUIDE.md`, and `USD-JPY Signal Pipeline Plan.md`, and verify the full `pytest` regression suite. *(Final Track E)*

---

## 5. Current Implementation Status

* **Phase 1 (Data Sources):** FRED (`DGS2`/`DGS10`), Yahoo Finance (`JPY=X`, `^TNX`, `2YY=F`, `^GSPC`, `^N225`, `^VIX`, `GC=F`, `CL=F`) + Japan MoF CSV JGB 2Y (`2年`) & 10Y (`10年`) benchmarks (`mof.go.jp`), Forex Factory calendar JSON/cache, and 10 curated intervention RSS wires.
* **Phase 2 (Collection & Containerization):** WAL-mode SQLite schema (`schema.sql`), modular collectors (`collectors/`), ISO-8601 UTC normalization & spread engine (`normalizer.py`, `spread_calculator.py`), tiered session-aware scheduler (`scheduler.py`), and `Dockerfile` / `docker-compose.yml`.
* **Phase 3 (MCP & LangGraph Agents):** Read-only SQLite MCP server (`mcp_server.py`), dual-server stdio MCP client (`mcp_client.py`, `mcp_servers.json`), typed LangGraph state & specialist nodes (`agent/state.py`, `agent/nodes.py`), and `interrupt_before` human approval gate + paper execution (`agent/graph.py`, `run_pipeline.py`).
* **Phase 9 (Advanced USD/JPY Signals):** Completed across tasks `P9-01` through `P9-06` — integrating 2Y Sovereign Yield Spread (`spread_2y`), Crude Oil (`CL=F`) Terms-of-Trade Momentum, and the Tokyo Gotobi & Nakane (9:55 AM JST) Fixing Intraday Session Engine across live collection, specialist agent nodes, MCP server tools, historical backtesting, and documentation (355/355 tests passing).
* **Phase 10 (Telegram Remote Approval Gate & Daily Summary Engine):** Completed across tasks `P10-01` through `P10-05` — integrating 2-way Telegram inline-button trade approvals, timeout auto-rejection, persistent 24-hour JST daily summary scheduling (`data/telegram_settings.json`, default `09:00` JST), interactive `/summary on [HH:MM]`, `/summary off`, and `/summary now` commands, and mandatory Summary Settings footers across all scheduled and on-demand summaries.
