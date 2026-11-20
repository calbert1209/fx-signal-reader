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
   - **USD/JPY Risk Policy (`VERBAL_LADDER`):** Active 4-tier Japan Ministry of Finance (MoF) verbal intervention ladder, spot barrier proximity checks (157.0/158.5/160.0), and VIX filters.
   - **EUR/USD Risk Policy (`CENTRAL_BANK`):** $\pm 4$-hour Tier-1 Central Bank Event Blackout window (ECB rate decisions, FOMC, US NFP, US CPI), falling-knife momentum protection, and VIX $\ge 25.0$ risk-off veto.
   - **Position Sizing:** Equal-dollar risk sizing tailored to base-in-USD (USD/JPY) and quote-in-USD (EUR/USD) quote conventions.
4. **Point-in-Time Backtest Simulator (`fx-backtest`):** Replays historical ticks with bracket resolution (fixed or dynamic ATR-14), breakeven stop loss ratcheting, and USD PnL accounting.

### Tracked Data Sources

| Data Type | Pair Coverage | Primary Source | Identifier / Symbols |
| :--- | :--- | :--- | :--- |
| **Spot & Risk Assets** | USD/JPY | Yahoo Finance | `JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F` |
| **Spot & Risk Assets** | EUR/USD | Yahoo Finance | `EURUSD=X`, `^TNX`, `^STOXX50E`, `^GSPC`, `^VIX`, `GC=F` |
| **US 2Y & 10Y Yields** | Both | FRED API / Yahoo Finance | `DGS2`, `DGS10`, `^TNX` |
| **Japan 10Y JGB Yield** | USD/JPY | Ministry of Finance Japan | `jgbcm.csv` |
| **German 10Y Bund Yield** | EUR/USD | Deutsche Bundesbank / ECB / FRED | SDMX REST API, ECB Data Portal, FRED `IRLTLT01DEM156N` |
| **Economic Calendar** | Both | Forex Factory | Weekly JSON feeds (USD, JPY, EUR events) |
| **Policy News & Alerts** | Both | BoJ, MoF, ECB, FX RSS feeds | 10+ curated central bank and macro news feeds |

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
- `--auto-approve`: Automatically approves actionable trade proposals and records execution to `data/paper_trades.jsonl`.
- `--reject`: Rejects actionable proposals without execution.
- `--json`: Outputs structured JSON results for programmatic consumption.
- `--sl-pips` / `--tp-pips`: Custom fixed Stop Loss and Take Profit distances in pips.
- `--use-atr`: Dynamic ATR-14 volatility-scaled brackets.

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
├── tests/             # Comprehensive 288-test unit and integration test suite
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

Test coverage encompasses 288 tests covering pair profiles, yield ingestion, calendar parsing, agent nodes, simulator bracket resolution, CLI parameterization, and end-to-end replay backtests.
