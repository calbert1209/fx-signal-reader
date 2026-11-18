# P3-01: Custom SQLite MCP server

* **Phase:** Phase 3: Agent Integration (MCP & LangGraph)
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T09:41:28Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Build a local, strictly read-only Model Context Protocol (MCP) server (`mcp_server.py`) using `mcp.server.mcpserver.MCPServer` (`mcp 2.3.0`) over stdio transport (`mcp_server = MCPServer("usdjpy-sqlite-signals")`).

### Key Capabilities Implemented:
1. **Strict Read-Only SQLite URI Enforcement (`get_readonly_connection`):**
   - Resolves target database via explicit `db_path` argument, `FX_DB_PATH` environment variable, or default `usdjpy_signals.db`.
   - Connects strictly via `sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5.0)` with `PRAGMA query_only = ON;` and `sqlite3.Row` factory.
   - Blocks all mutations (`INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`) at the SQLite URI/pager level (`sqlite3.OperationalError: attempt to write a readonly database`).
2. **Domain Query Functions & Zero-Arg Compatible MCP Tools:**
   - `get_latest_yield_spread(db_path=None)`: Queries latest `yield_spreads` row (`timestamp_utc`, `us_2y`, `us_10y`, `jp_10y`, `spread_10y`, `source`), computes `spread_bps` (`compute_spread_bps`) and `us_2s10s_slope` (`compute_us_2s10s_slope`) via `spread_calculator`, and attaches latest `JPY=X` spot price and `timestamp_utc` from `market_ticks`.
   - `get_upcoming_macro_events(currency=None, impact=None, limit=20, db_path=None)`: Queries `macro_events` with optional case-insensitive `currency` (`USD`/`JPY`) and `impact` (`High`/`Medium`/`Low`) filters, ordered by `timestamp_utc ASC`.
   - `get_recent_mof_alerts(min_tier=1, limit=20, db_path=None)`: Queries `news_headlines` where `matched_keywords != ''`, evaluates escalation tiers (1–4) and entity matches by reusing `NewsRSSCollector.evaluate_headline` (`collectors/news_rss.py` — no duplicated regex definitions), filters `tier >= min_tier`, and returns newest alerts first (`published_utc DESC`).
   - `get_market_ticks_summary(symbols=None, db_path=None)`: Queries latest 2 OHLCV ticks per symbol for `MarketTicksCollector.TICKERS` (`JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F`) or requested `symbols`, returning `close`, `open`, `high`, `low`, `volume`, `timestamp_utc`, `prev_close`, `bar_change`, and `bar_change_pct`.
3. **Clean MCP Tool Signatures & Read-Only Annotations:**
   - Registered MCP wrappers omit internal `db_path` arguments (reading `FX_DB_PATH` dynamically so stdio clients can call tools with zero arguments without leaking filesystem defaults) and attach `ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)`.

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete (`mcp_server.py` and `tests/test_mcp_server.py`)
- [x] Verified read-only SQLite URI mode (`file:<path>?mode=ro`) blocks writes with `sqlite3.OperationalError`
- [x] Reused `NewsRSSCollector` taxonomy (`collectors/news_rss.py`), `MarketTicksCollector.TICKERS`, and `spread_calculator` helpers
- [x] Verified in local sandbox / environment (15/15 tests in `tests/test_mcp_server.py`, 91/91 project-wide tests passing, live MCP stdio roundtrip validated)
- [x] Linked artifacts and trackers updated

---

## 3. Execution Log & Findings

### 3.1 Registered MCP Tool Schemas (`mcp_server.list_tools()` over stdio)

```json
[
  {
    "name": "get_latest_yield_spread",
    "title": "Get Latest US-Japan Yield Spread",
    "description": "Return the latest US-Japan sovereign yield spread (US 2Y, US 10Y, JP 10Y, 10Y spread in % and bps, source) plus the latest JPY=X spot price and timestamp.",
    "input_schema": {
      "properties": {},
      "type": "object",
      "title": "_tool_get_latest_yield_spreadArguments"
    },
    "output_schema": {
      "type": "object",
      "additionalProperties": true,
      "title": "_tool_get_latest_yield_spreadDictOutput"
    },
    "annotations": {
      "title": null,
      "read_only_hint": true,
      "destructive_hint": false,
      "idempotent_hint": true,
      "open_world_hint": false
    }
  },
  {
    "name": "get_upcoming_macro_events",
    "title": "Get Upcoming Macro Events",
    "description": "Query scheduled USD and JPY economic calendar events from macro_events, optionally filtered by currency (USD/JPY) and impact (High/Medium/Low), ordered by timestamp_utc ascending.",
    "input_schema": {
      "properties": {
        "currency": {
          "anyOf": [{"type": "string"}, {"type": "null"}],
          "default": null,
          "title": "Currency"
        },
        "impact": {
          "anyOf": [{"type": "string"}, {"type": "null"}],
          "default": null,
          "title": "Impact"
        },
        "limit": {
          "default": 20,
          "title": "Limit",
          "type": "integer"
        }
      },
      "type": "object",
      "title": "_tool_get_upcoming_macro_eventsArguments"
    },
    "output_schema": {
      "properties": {
        "result": {
          "items": {"additionalProperties": true, "type": "object"},
          "title": "Result",
          "type": "array"
        }
      },
      "required": ["result"],
      "type": "object",
      "title": "_tool_get_upcoming_macro_eventsOutput"
    },
    "annotations": {
      "title": null,
      "read_only_hint": true,
      "destructive_hint": false,
      "idempotent_hint": true,
      "open_world_hint": false
    }
  },
  {
    "name": "get_recent_mof_alerts",
    "title": "Get Recent MoF / BoJ Intervention Alerts",
    "description": "Query recent news headlines with intervention or policymaker keyword matches, score each headline on the 4-tier verbal intervention escalation ladder (1-4), filter by tier >= min_tier, and return newest alerts first.",
    "input_schema": {
      "properties": {
        "min_tier": {
          "default": 1,
          "title": "Min Tier",
          "type": "integer"
        },
        "limit": {
          "default": 20,
          "title": "Limit",
          "type": "integer"
        }
      },
      "type": "object",
      "title": "_tool_get_recent_mof_alertsArguments"
    },
    "output_schema": {
      "properties": {
        "result": {
          "items": {"additionalProperties": true, "type": "object"},
          "title": "Result",
          "type": "array"
        }
      },
      "required": ["result"],
      "type": "object",
      "title": "_tool_get_recent_mof_alertsOutput"
    },
    "annotations": {
      "title": null,
      "read_only_hint": true,
      "destructive_hint": false,
      "idempotent_hint": true,
      "open_world_hint": false
    }
  },
  {
    "name": "get_market_ticks_summary",
    "title": "Get Market Ticks Summary",
    "description": "Return latest close, open, high, low, volume, timestamp_utc, and bar change for core USD/JPY cross-asset symbols (JPY=X, ^TNX, ^GSPC, ^N225, ^VIX, GC=F) or requested ticker symbols.",
    "input_schema": {
      "properties": {
        "symbols": {
          "anyOf": [
            {"items": {"type": "string"}, "type": "array"},
            {"type": "null"}
          ],
          "default": null,
          "title": "Symbols"
        }
      },
      "type": "object",
      "title": "_tool_get_market_ticks_summaryArguments"
    },
    "output_schema": {
      "type": "object",
      "additionalProperties": true,
      "title": "_tool_get_market_ticks_summaryDictOutput"
    },
    "annotations": {
      "title": null,
      "read_only_hint": true,
      "destructive_hint": false,
      "idempotent_hint": true,
      "open_world_hint": false
    }
  }
]
```

### 3.2 Automated Unit & Stdio Test Execution (`.venv/bin/python -m unittest -v tests/test_mcp_server.py`)

```text
test_live_macro_events (tests.test_mcp_server.TestDomainToolsWithLiveDB.test_live_macro_events) ... ok
test_live_market_ticks_summary (tests.test_mcp_server.TestDomainToolsWithLiveDB.test_live_market_ticks_summary) ... ok
test_live_mof_alerts (tests.test_mcp_server.TestDomainToolsWithLiveDB.test_live_mof_alerts) ... ok
test_live_yield_spread (tests.test_mcp_server.TestDomainToolsWithLiveDB.test_live_yield_spread) ... ok
test_get_latest_yield_spread (tests.test_mcp_server.TestDomainToolsWithTempDB.test_get_latest_yield_spread) ... ok
test_get_latest_yield_spread_empty_db (tests.test_mcp_server.TestDomainToolsWithTempDB.test_get_latest_yield_spread_empty_db) ... ok
test_get_market_ticks_summary_core_and_custom (tests.test_mcp_server.TestDomainToolsWithTempDB.test_get_market_ticks_summary_core_and_custom) ... ok
test_get_recent_mof_alerts_tiers_and_ordering (tests.test_mcp_server.TestDomainToolsWithTempDB.test_get_recent_mof_alerts_tiers_and_ordering) ... ok
test_get_upcoming_macro_events_filters_and_order (tests.test_mcp_server.TestDomainToolsWithTempDB.test_get_upcoming_macro_events_filters_and_order) ... ok
test_live_stdio_client_default_live_db (tests.test_mcp_server.TestMCPServerRegistrationAndStdio.test_live_stdio_client_default_live_db) ... ok
test_live_stdio_client_with_fixture_db (tests.test_mcp_server.TestMCPServerRegistrationAndStdio.test_live_stdio_client_with_fixture_db) ... ok
test_mcp_server_tool_registration_and_annotations (tests.test_mcp_server.TestMCPServerRegistrationAndStdio.test_mcp_server_tool_registration_and_annotations) ... ok
test_missing_database_raises_operational_error (tests.test_mcp_server.TestReadOnlyURIEnforcement.test_missing_database_raises_operational_error) ... ok
test_resolve_db_path_precedence (tests.test_mcp_server.TestReadOnlyURIEnforcement.test_resolve_db_path_precedence) ... ok
test_write_operations_raise_operational_error (tests.test_mcp_server.TestReadOnlyURIEnforcement.test_write_operations_raise_operational_error) ... ok

----------------------------------------------------------------------
Ran 15 tests in 4.847s

OK
```
*Full suite check (`.venv/bin/python -m unittest discover -s tests -v`):* `Ran 91 tests in 15.342s — OK`.

### 3.3 Live MCP Stdio Query Sample (`usdjpy_signals.db` at `2026-11-18T09:41:14Z`)

#### `get_latest_yield_spread()`
```json
{
  "timestamp_utc": "2026-11-17T19:50:00Z",
  "us_2y": null,
  "us_10y": 5.102999687194824,
  "jp_10y": 2.925,
  "spread_10y": 2.178,
  "spread_bps": 217.8,
  "us_2s10s_slope": null,
  "source": "yfinance+mof_japan",
  "usdjpy_spot": 159.13800048828125,
  "usdjpy_timestamp_utc": "2026-11-18T09:00:00Z"
}
```

#### `get_upcoming_macro_events(currency="USD", impact="High", limit=3)`
```json
[
  {
    "event_hash": "113d58d096fc94f71b5c7aae9695045c",
    "timestamp_utc": "2026-11-16T17:00:00Z",
    "currency": "USD",
    "title": "Fed Chairman Warsh Speaks",
    "impact": "High",
    "forecast": null,
    "previous": null,
    "actual": null
  },
  {
    "event_hash": "b9db017c0093b202bda37b7d4b4534ab",
    "timestamp_utc": "2026-11-17T13:30:00Z",
    "currency": "USD",
    "title": "Retail Sales m/m",
    "impact": "High",
    "forecast": "0.1%",
    "previous": "0.8%",
    "actual": null
  },
  {
    "event_hash": "5ffb1b3465405a57aa750e037611f623",
    "timestamp_utc": "2026-11-17T13:30:00Z",
    "currency": "USD",
    "title": "Core Retail Sales m/m",
    "impact": "High",
    "forecast": "0.4%",
    "previous": "0.9%",
    "actual": null
  }
]
```

#### `get_recent_mof_alerts(min_tier=4, limit=3)`
```json
[
  {
    "url_hash": "00c250e0526043bd338f0fd225a043c3f0a3143081d54644c4ad54573c843659",
    "published_utc": "2026-11-18T00:00:00Z",
    "source": "Ministry of Finance Japan",
    "title": "ITN\nForeign Exchange Intervention Operations (July - September 2026)",
    "url": "https://www.mof.go.jp/english/policy/international_policy/reference/feio/quarter/2026_3Qe.html",
    "matched_keywords": "intervention operations, intervention",
    "tier": 4,
    "tier_name": "Tier 4: Imminent Physical Intervention / Rate Check",
    "severity": "CRITICAL",
    "matched_entities": [],
    "matched_tier_keywords": ["intervention operations", "intervention"]
  },
  {
    "url_hash": "648194101126cd6b76dfc89fb3cc7a1ccce2509d510ac050f7cbcd1b53e8c674",
    "published_utc": "2026-11-13T18:13:14Z",
    "source": "Forex Factory",
    "title": "Suspected Yen Intervention - Forex Factory",
    "url": "https://news.google.com/rss/articles/CBMid0FVX3lxTE9Ucl9WNXNjcUZnZEQxbGFKZV8wLXlZNkhLUWVnM2EwNjZzZ2Z6ZnE3QjhkR25INGs0eE5RRTZ4T0JwVUlvblZxMHBsaTk5YVNiMHpLbzRqN01vTTJrZFNZY2ZVN3hYZE1rdy1QenBIY1V2SVZiNm9F0gF8QVVfeXFMUG03TVBVczJ5aktLUVNtTjBoRnNaOW9nRVpvYmhlY0taTmNDNWdDR2tmZ0t2UXJXQUN5R1NiMWVUbGFxWGEzcHRuZVJJSXhnTXBzbllGdzNIM2FvbVo1YXVxM2xXMi1kTE1hbFllMDMwQk5WbWE5S2lOSE9rbw?oc=5",
    "matched_keywords": "physical/suspected intervention, intervention",
    "tier": 4,
    "tier_name": "Tier 4: Imminent Physical Intervention / Rate Check",
    "severity": "CRITICAL",
    "matched_entities": [],
    "matched_tier_keywords": ["physical/suspected intervention", "intervention"]
  },
  {
    "url_hash": "2ca0970646a0f1fb3f0a495c2d39f51bcf126739d487884cc69e4429cb6ba201",
    "published_utc": "2026-11-12T17:59:47Z",
    "source": "XTB.com",
    "title": "USDJPY Pulls Back After Reaching 160 \ud83d\udea9 Is Tokyo Intervening in the Yen Again? - XTB.com",
    "url": "https://news.google.com/rss/articles/CBMizAFBVV95cUxPY1R6bXI1VWhJNmtkRWlTaXNaLW4xZ3ZRTVY2cFBTQ294NFdMc1FEMThoZklRNWFST2xGYUxtd3NjX2JMYzItUk9EVEIxWVMwYlRPQS1LYVNoMUlZZ19lS05TUzdZa2tfRjA1YUs1VGZTV3pZZDcyN1FIZmlfV2VlT09tU2tfVy0xUUN1Qk0xMW9fQWY3X29wanJRczVTdW85U1pGM3pBOUUwcV83YkZwaF9sSEs1Z3RSOGd5aHg5T1pfQUFCTGtpNlVTUU0?oc=5",
    "matched_keywords": "intervened",
    "tier": 4,
    "tier_name": "Tier 4: Imminent Physical Intervention / Rate Check",
    "severity": "CRITICAL",
    "matched_entities": [],
    "matched_tier_keywords": ["intervened"]
  }
]
```

#### `get_market_ticks_summary()`
```json
{
  "as_of_utc": "2026-11-18T09:00:00Z",
  "requested_symbols": ["JPY=X", "^TNX", "^GSPC", "^N225", "^VIX", "GC=F"],
  "symbol_count": 6,
  "symbols": {
    "JPY=X": {
      "symbol": "JPY=X",
      "timestamp_utc": "2026-11-18T09:00:00Z",
      "close": 159.13800048828125,
      "open": 159.23399353027344,
      "high": 159.23500061035156,
      "low": 159.13800048828125,
      "volume": 0.0,
      "prev_close": 159.22999572753906,
      "bar_change": -0.091995,
      "bar_change_pct": -0.0578
    },
    "^TNX": {
      "symbol": "^TNX",
      "timestamp_utc": "2026-11-17T19:50:00Z",
      "close": 5.102999687194824,
      "open": 5.098999977111816,
      "high": 5.105000019073486,
      "low": 5.098999977111816,
      "volume": 0.0,
      "prev_close": 5.0970001220703125,
      "bar_change": 0.006,
      "bar_change_pct": 0.1177
    },
    "^GSPC": {
      "symbol": "^GSPC",
      "timestamp_utc": "2026-11-17T20:45:00Z",
      "close": 7340.6298828125,
      "open": 7328.7001953125,
      "high": 7340.6298828125,
      "low": 7312.990234375,
      "volume": 355785000.0,
      "prev_close": 7328.77978515625,
      "bar_change": 11.850098,
      "bar_change_pct": 0.1617
    },
    "^N225": {
      "symbol": "^N225",
      "timestamp_utc": "2026-11-18T06:15:00Z",
      "close": 59165.12890625,
      "open": 59120.69921875,
      "high": 59207.7890625,
      "low": 59059.03125,
      "volume": 0.0,
      "prev_close": 59122.3515625,
      "bar_change": 42.777344,
      "bar_change_pct": 0.0724
    },
    "^VIX": {
      "symbol": "^VIX",
      "timestamp_utc": "2026-11-18T08:45:00Z",
      "close": 23.850000381469727,
      "open": 23.8799991607666,
      "high": 23.889999389648438,
      "low": 23.850000381469727,
      "volume": 0.0,
      "prev_close": 23.889999389648438,
      "bar_change": -0.039999,
      "bar_change_pct": -0.1674
    },
    "GC=F": {
      "symbol": "GC=F",
      "timestamp_utc": "2026-11-18T08:45:00Z",
      "close": 3769.60009765625,
      "open": 3769.10009765625,
      "high": 3775.10009765625,
      "low": 3768.39990234375,
      "volume": 2027.0,
      "prev_close": 3769.5,
      "bar_change": 0.100098,
      "bar_change_pct": 0.0027
    }
  },
  "missing_symbols": []
}
```
