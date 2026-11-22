# FX Signal Reader: Multi-Pair Directional Trading System (USD/JPY & EUR/USD)

This software collects market data, sovereign yields, macroeconomic calendar releases, and policy news for major currency pairs—currently supporting **USD/JPY** and **EUR/USD**. It stores records in pair-segregated local SQLite databases in WAL mode, computes directional sovereign yield spreads, and runs a LangGraph multi-agent workflow that proposes risk-managed paper trades with human approval gates and historical backtesting.

---

## System Overview

The system consists of four primary components:

1. **Multi-Pair Data Collectors:** Modular Python workers ingest market ticks, sovereign bond yields (US Treasury, Japanese Government Bonds, German Bunds), economic calendar schedules, and central bank/intervention news feeds.
2. **Pair-Segregated SQLite Storage:** Local SQLite databases configured in WAL mode provide high-concurrency read/write access. Storage is cleanly segregated by currency pair:
   - `data/usdjpy_signals.db`: Production database for USD/JPY.
   - `data/eurusd_signals.db`: Production database for EUR/USD.
   - `data/backtest_signals.db` / `data/backtest_signals_eurusd.db`: Isolated backtest and replay databases.
3. **Specialist Agent Workflow:** A LangGraph multi-agent pipeline reads the active database through MCP tools, calculates directional yield bias, evaluates pair-specific risk regimes, and requests human approval before paper execution.
   - **Advanced USD/JPY Multi-Signal Engine (Phase 9):**
     - **P1 — 2-Year Sovereign Yield Spread (`US 2Y - JGB 2Y`):** Front-end monetary policy divergence (US 2Y Treasury via FRED `DGS2` / Yahoo `2YY=F` vs. Japan 2Y JGB `2年` from MoF `jgbcm.csv`). Confirms 10Y directional bias (`+0.05` confidence boost) or dampens confidence (`-0.05`) and flags curve divergence when the 2Y and 10Y spreads conflict.
     - **P2 — Crude Oil (`CL=F`) Terms-of-Trade Momentum:** Symmetric energy terms-of-trade indicator evaluating 5-day and 20-day rate of change (RoC) and 50-day EMA on WTI Crude Oil Futures (`CL=F`). Classifies oil momentum into `SURGING` (energy import cost drag / JPY headwind / `BULLISH_USD`), `PLUNGING` (terms-of-trade relief / JPY supportive / `BEARISH_USD`), or `STABLE` (`NEUTRAL`).
     - **P3 — Tokyo Gotobi & Nakane (9:55 AM JST) Fixing Session Engine:** Deterministic calendar and intraday session engine detecting Gotobi corporate settlement dates (days 5, 10, 15, 20, 25, 30, and month-end, with weekend/Japanese holiday rollback to the preceding business day). Applies a Pre-Fix (08:30–09:55 JST / 23:30–00:55 UTC) `BUY` conviction boost (`+0.05` confidence) and soft `SELL` advisory warning, plus Post-Fix (09:55–11:00 JST / 00:55–02:00 UTC) exhaustion notes.
   - **USD/JPY Risk Policy (`VERBAL_LADDER`):** Active 4-tier Japan Ministry of Finance (MoF) verbal intervention ladder, spot barrier proximity checks (157.0/158.5/160.0), and VIX filters.
   - **EUR/USD Risk Policy (`CENTRAL_BANK`):** $\pm 4$-hour Tier-1 Central Bank Event Blackout window (ECB rate decisions, FOMC, US NFP, US CPI), falling-knife momentum protection, and VIX $\ge 25.0$ risk-off veto.
   - **Position Sizing:** Equal-dollar risk sizing tailored to base-in-USD (USD/JPY) and quote-in-USD (EUR/USD) quote conventions.
4. **Point-in-Time Backtest Simulator (`fx-backtest`):** Replays historical ticks with bracket resolution (fixed or dynamic ATR-14), breakeven stop loss ratcheting, USD PnL accounting, and historical coverage diagnostics for 2Y yield spreads, crude oil, and Gotobi sessions.

### Tracked Data Sources

| Data Type | Pair Coverage | Primary Source | Identifier / Symbols |
| :--- | :--- | :--- | :--- |
| **Spot, Risk & Energy Assets** | USD/JPY | Yahoo Finance | `JPY=X`, `^TNX`, `2YY=F`, `^GSPC`, `^N225`, `^VIX`, `GC=F`, `CL=F` (Crude Oil WTI Futures) |
| **Spot & Risk Assets** | EUR/USD | Yahoo Finance | `EURUSD=X`, `^TNX`, `^STOXX50E`, `^GSPC`, `^VIX`, `GC=F` |
| **US 2Y & 10Y Yields** | Both | FRED API / Yahoo Finance | `DGS2`, `DGS10`, `2YY=F`, `^TNX` |
| **Japan 2Y & 10Y JGB Yields** | USD/JPY | Ministry of Finance Japan | `jgbcm.csv` (`2年` 2Y JGB & `10年` 10Y JGB) |
| **German 10Y Bund Yield** | EUR/USD | Deutsche Bundesbank / ECB / FRED | SDMX REST API, ECB Data Portal, FRED `IRLTLT01DEM156N` |
| **Economic Calendar** | Both | Forex Factory | Weekly JSON feeds (USD, JPY, EUR events) |
| **Policy News & Alerts** | Both | BoJ, MoF, ECB, FX RSS feeds | 10+ curated central bank and macro news feeds |

### Database Schema (`schema.sql`)

Each pair-segregated SQLite database (WAL mode) maintains four normalized tables:
- `market_ticks`: Intraday and daily OHLCV bars (`symbol`, `timestamp_utc`, `price`, `open`, `high`, `low`, `volume`) covering spot FX, equity indices, VIX, Gold (`GC=F`), and WTI Crude Oil Futures (`CL=F`).
- `yield_spreads`: Multi-tenor sovereign bond yields and differentials (`timestamp_utc`, `us_2y`, `jp_2y`, `spread_2y`, `us_10y`, `jp_10y`, `spread_10y`, `source`). Existing databases automatically migrate `jp_2y` and `spread_2y` columns via idempotent `ALTER TABLE` checks.
- `macro_events`: Scheduled economic calendar releases with SHA-256 deduplication (`event_hash`, `timestamp_utc`, `currency`, `title`, `impact`, `forecast`, `previous`, `actual`).
- `news_headlines`: Central bank and MoF/BoJ verbal intervention headlines (`url_hash`, `published_utc`, `source`, `title`, `url`, `matched_keywords`).

---

## Requirements

- Python 3.11 or newer.
- Git (for version control).
- Docker and Docker Compose (optional, for containerized background collectors).

---

## Installation

1. Open a terminal in the project root directory.
2. Create and activate a Python virtual environment:
   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies and the package in editable mode:
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```

Installation registers six production CLI entrypoints in `.venv/bin/`:
- `fx-collect`: One-shot data collection.
- `fx-schedule`: Production tiered scheduler.
- `fx-pipeline`: LangGraph multi-agent signal pipeline.
- `fx-backtest`: Point-in-time historical backtest engine.
- `fx-mcp`: Read-only stdio SQLite MCP server.
- `fx-mcp-client`: Multi-server MCP testing client.

---

## Configuration

Copy the example configuration file to set up your environment:

```bash
cp .env.example .env
```

Key environment variables in `.env`:

- `FX_PAIR`: Default currency pair to evaluate (default: `USD/JPY`; options: `USD/JPY`, `EUR/USD`).
- `FX_DB_PATH`: Explicit SQLite database path. When omitted, automatically routes to `data/usdjpy_signals.db` or `data/eurusd_signals.db` based on `FX_PAIR`.
- `FRED_API_KEY`: Optional free FRED API key for official US 2Y (`DGS2`) and 10Y (`DGS10`) Treasury yields. If omitted, falls back to Yahoo Finance `^TNX`.
- `POLL_INTERVAL_NEWS`: News RSS polling interval in seconds (default: `300`).
- `POLL_INTERVAL_TICKS`: Market tick polling interval in seconds (default: `900`).
- `POLL_INTERVAL_CALENDAR`: Economic calendar polling interval in seconds (default: `86400`).
- `TELEGRAM_BOT_TOKEN`: Optional Telegram Bot HTTP API token from `@BotFather` for remote mobile trade approvals and daily summaries.
- `TELEGRAM_CHAT_ID`: Authorized Telegram chat ID permitted to receive notifications, approve/reject trades, and run `/summary` commands.
- `TELEGRAM_APPROVAL_TIMEOUT`: Seconds to wait for a Telegram button tap before auto-rejecting an actionable trade proposal (default: `300`).
- `TELEGRAM_SUMMARY_TIME_JST`: Default daily summary schedule in 24-hour JST format (default: `09:00`; persisted in `data/telegram_settings.json`).

---

## Usage

### 1. One-Shot Data Collection (`fx-collect`)

Poll all data sources once for a pair:

```bash
# Collect default USD/JPY
fx-collect --collector all

# Collect EUR/USD (German Bunds, EUR/USD spot, Euro Stoxx 50, ECB news, EUR calendar)
fx-collect --pair EUR/USD --collector all
```

You can also run a single collector component (`ticks`, `calendar`, or `news`):

```bash
fx-collect --pair EUR/USD --collector ticks
```

### 2. Tiered Scheduler (`fx-schedule`)

Run continuous background polling adhering to session awareness (Tokyo Fix, London/NY overlap):

```bash
# Run scheduler for USD/JPY
fx-schedule

# Run scheduler for EUR/USD
fx-schedule --pair EUR/USD
```

To test scheduling without writing to the database, use `--dry-run`:

```bash
fx-schedule --pair EUR/USD --dry-run --run-once
```

### 3. Agent Signal Pipeline (`fx-pipeline`)

Execute the LangGraph multi-agent workflow:

```bash
# Evaluate USD/JPY (default)
fx-pipeline

# Evaluate EUR/USD
fx-pipeline --pair EUR/USD
```

Approval gate controls:
- **Interactive TTY:** Prompts for reviewer approval or rejection on actionable signals.
- `--telegram`: Sends actionable `BUY`/`SELL` proposals to your Telegram app with inline **[ ✅ Approve ]** and **[ ❌ Reject ]** buttons, long-polls for your decision (up to `--telegram-timeout`), resumes the LangGraph checkpoint, records approved trades to `data/paper_trades.jsonl`, and updates the Telegram message with the final execution receipt.
- `--telegram-test`: Verifies `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` connectivity (auto-discovering `chat_id` if you recently messaged the bot) and exits.
- `--telegram-summary`: Immediately evaluates the market & paper ledger and sends a summary digest (including current summary settings) to Telegram.
- `--auto-approve`: Automatically approves actionable trade proposals and records execution to `data/paper_trades.jsonl`.
- `--reject`: Rejects actionable proposals without execution.
- `--json`: Outputs structured JSON results for programmatic consumption.
- `--sl-pips` / `--tp-pips`: Custom fixed Stop Loss and Take Profit distances in pips.
- `--use-atr`: Dynamic ATR-14 volatility-scaled brackets.

#### Remote Telegram Setup & `/summary` Commands
1. Message **`@BotFather`** on Telegram with `/newbot` to obtain your `TELEGRAM_BOT_TOKEN`.
2. Open a chat with your new bot, send `hello`, and set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` (run `fx-pipeline --telegram-test` to verify connectivity).
3. Run continuous scheduling with Telegram remote approvals and daily summaries:
   ```bash
   fx-schedule --pair USD/JPY --telegram --run-pipeline
   ```
4. Control your daily market & portfolio summary anytime from your phone by messaging your bot:
   - `/summary on` — Enables the daily summary and schedules it for the default **`09:00` JST** (9:00 AM JST).
   - `/summary on 7:30` or `/summary on 14:30` — Enables and schedules the daily summary for **`07:30` JST** (7:30 AM) or **`14:30` JST** (2:30 PM) in 24-hour JST time.
   - `/summary off` — Disables the daily scheduled summary (persisted in `data/telegram_settings.json`).
   - `/summary now` — Immediately evaluates and sends an on-demand summary at any time (even when the daily schedule is `off`). Every scheduled or on-demand summary includes a **Summary Settings** footer displaying current toggle status (`ON`/`OFF`) and JST delivery time.

### 4. Historical Backtesting (`fx-backtest`)

Run point-in-time backtesting across multi-year historical datasets with automated backfill:

#### 2-Year High-Resolution Backtest (Hourly `1h` Bars, Recommended)
Yahoo Finance retains intraday hourly bars for up to 730 days (~2 years). Run a 2-year backtest with 4-hour evaluation steps:

```bash
# 2-Year EUR/USD Backtest with fixed 50p SL / 100p TP and breakeven ratchet
fx-backtest --pair EUR/USD \
  --backfill \
  --start 2024-11-20 \
  --end 2026-11-19 \
  --interval 1h \
  --step-hours 4 \
  --allowed-regimes RISK_ON \
  --breakeven-r 1.25

# 2-Year USD/JPY Backtest with fixed 75p SL / 150p TP
fx-backtest --pair USD/JPY \
  --backfill \
  --start 2024-11-20 \
  --end 2026-11-19 \
  --interval 1h \
  --step-hours 4 \
  --breakeven-r 1.25
```

#### 5-Year Macro Backtest (Daily `1d` Bars)
For multi-year macro trends across full central bank rate cycles:

```bash
fx-backtest --pair EUR/USD \
  --db-path data/backtest_signals_eurusd_5y.db \
  --backfill \
  --start 2021-11-20 \
  --end 2026-11-19 \
  --interval 1d \
  --step-hours 24 \
  --sl-pips 100 \
  --tp-pips 200 \
  --breakeven-r 1.25
```

Key backtest knobs:
- `--use-atr`: Enable dynamic ATR-14 Stop Loss (1.0x ATR) and Take Profit (2.0x ATR) brackets.
- `--breakeven-r <float>`: Ratchet Stop Loss to breakeven when trade reaches specified R-multiple (e.g. `1.25`).
- `--export-trades <path>`: Export simulated trade records to `.csv` or `.jsonl`.
- `--json`: Dump complete backtest report payload as JSON.

### 5. Read-Only MCP Server (`fx-mcp`)

Start the local MCP server over `stdio` to provide tools for external AI clients:

```bash
fx-mcp
```

Inspect available tools with the client CLI:

```bash
fx-mcp-client --list-tools
```

For detailed agent integration examples and tool schemas, read [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md).

### 6. Container Deployment (Docker)

1. Copy `.env.example` to `.env` and set `FX_PAIR` and optional API keys.
2. Start the background collector container:
   ```bash
   docker compose up -d
   ```
3. Tail container logs:
   ```bash
   docker compose logs -f
   ```

The local `./data` directory is bind-mounted to `/app/data` to ensure persistence across container restarts.

---

## Project Structure

```text
fx-signal-reader/
├── fx_signal_reader/
│   ├── core/          # PairProfile registry, normalization, spread math, schema.sql
│   ├── collectors/    # Collectors for ticks, yields, calendar, and RSS feeds
│   ├── agent/         # LangGraph state, specialist nodes, and approval gate
│   ├── backtest/      # Replay engine, bracket simulator, models, and backfill
│   ├── mcp/           # Read-only SQLite MCP server and multi-server client
│   └── cli/           # Entrypoints for fx-collect, fx-schedule, fx-pipeline, fx-backtest
├── data/              # Pair-segregated SQLite databases, fixtures, and trade ledgers
├── docs/              # Multi-pair expansion plan, task logs, and architecture specs
├── tests/             # Comprehensive 355-test unit and integration test suite
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## Tests

Execute the full regression test suite:

```bash
.venv/bin/pytest tests/
```

Test coverage encompasses 355 tests covering pair profiles, 10Y/2Y yield ingestion and spread calculation, crude oil terms-of-trade momentum, Tokyo Gotobi & Nakane fixing session evaluation, calendar parsing, agent nodes, simulator bracket resolution, CLI parameterization, and end-to-end replay backtests.
