# USD/JPY Signal Reader

This software collects market data, macro events, and news headlines for the USD/JPY currency pair. It stores all records in a local SQLite database. It runs a multi-agent workflow that proposes paper trades with human approval.

---

## System Overview

The software has three main parts:

1. **Data Collectors:** Python workers fetch market ticks, sovereign bond yields, economic calendars, and news feeds.
2. **Local Database:** A SQLite database in WAL mode stores all data in `data/usdjpy_signals.db`.
3. **Agent Workflow:** A LangGraph workflow reads the database through MCP tools, checks intervention risk, and requests approval before paper execution.

### Tracked Data Sources

| Data Type | Source | Identifier |
| :--- | :--- | :--- |
| **USD/JPY & Risk Assets** | Yahoo Finance | `JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F` |
| **US 2Y & 10Y Yields** | FRED API (optional) | `DGS2`, `DGS10` |
| **Japan 10Y Yield** | Ministry of Finance Japan | `jgbcm.csv` |
| **Economic Calendar** | Forex Factory | Weekly JSON feed (USD and JPY events) |
| **Intervention News** | BoJ, MoF, and FX RSS feeds | 10 curated news feeds |

---

## Requirements

- Python 3.11 or newer.
- Git (optional, for version control).
- Docker and Docker Compose (optional, for container deployment).

---

## Installation

Follow these steps to install the package on your machine:

1. Open a terminal in the project root directory.
2. Create a Python virtual environment:
   ```bash
   python3.11 -m venv .venv
   ```
3. Activate the virtual environment:
   ```bash
   source .venv/bin/activate
   ```
4. Install the dependencies and the package in editable mode:
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```

The installation creates five CLI commands in `.venv/bin/`: `fx-collect`, `fx-schedule`, `fx-mcp`, `fx-mcp-client`, and `fx-pipeline`.

---

## Configuration

Copy the example file to create your local environment configuration:

```bash
cp .env.example .env
```

Review these optional keys in `.env`:

- `FX_DB_PATH`: Set the path to the SQLite database file (default: `data/usdjpy_signals.db`, or `/app/data/usdjpy_signals.db` in Docker).
- `FRED_API_KEY`: Add a free FRED API key to fetch official US 2Y and 10Y Treasury yields. If you omit this key, the collector uses Yahoo Finance (`^TNX`) as a fallback.
- `NASDAQ_DATA_LINK_API_KEY`: Add a key if you use the external `nasdaq-data-link` MCP server.
- `POLL_INTERVAL_NEWS`: Set the interval in seconds for news polling (default: `300`).
- `POLL_INTERVAL_TICKS`: Set the interval in seconds for market tick polling (default: `900`).
- `POLL_INTERVAL_CALENDAR`: Set the interval in seconds for calendar polling (default: `86400`).

---

## Usage

### 1. Run One-Shot Data Collection

Run all collectors once to populate the local database:

```bash
fx-collect --collector all
```

You can also run a single collector (`ticks`, `calendar`, or `news`):

```bash
fx-collect --collector ticks
```

### 2. Run the Scheduled Collector

Start the scheduler to poll data sources on tiered intervals:

```bash
fx-schedule
```

To test the schedule without writing to the database, add `--dry-run`:

```bash
fx-schedule --dry-run --run-once
```

### 3. Run the Agent Signal Pipeline

Execute the LangGraph workflow to generate a USD/JPY trade proposal:

```bash
fx-pipeline
```

Use these flags to control the approval gate:

- Run in an interactive terminal to approve or reject actionable proposals at the prompt.
- Add `--auto-approve` to approve actionable proposals automatically and write to `data/paper_trades.jsonl`.
- Add `--reject` to cancel actionable proposals without writing to the ledger.
- Add `--json` to print the full output in JSON format.

### 4. Start the Read-Only MCP Server

Start the local MCP server over `stdio` for external AI clients:

```bash
fx-mcp
```

List tools across all configured servers in `fx_signal_reader/mcp/mcp_servers.json`:

```bash
fx-mcp-client --list-tools
```

### 5. Run with Docker

1. Copy `.env.example` to `.env` and add your optional API keys.
2. Start the collector container in the background:
   ```bash
   docker compose up -d
   ```
3. View container logs:
   ```bash
   docker compose logs -f
   ```

Docker mounts the local `./data` directory to `/app/data` so your database persists across restarts.

---

## Project Structure

```text
fx-signal-reader/
├── fx_signal_reader/
│   ├── core/          # Normalization, yield-spread math, and schema.sql
│   ├── collectors/    # Workers for ticks, calendar, and RSS news
│   ├── mcp/           # Read-only SQLite MCP server and client
│   ├── agent/         # LangGraph state, specialist nodes, and approval graph
│   └── cli/           # Entrypoints for fx-collect, fx-schedule, and fx-pipeline
├── data/              # SQLite database, calendar cache, and paper-trade ledger
├── docs/              # Architecture plan, task logs, and validation snapshots
├── scripts/           # Standalone Phase 1 validation scripts
├── tests/             # Unit and integration tests
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## Tests

Run the full test suite from the project root directory:

```bash
.venv/bin/python -m unittest discover -s tests -v
```
