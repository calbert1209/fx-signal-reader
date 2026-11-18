# P3-02: Multi-server MCP setup

* **Phase:** Phase 3: Agent Integration (MCP & LangGraph)
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T09:50:41Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Wire the custom read-only SQLite MCP server (`usdjpy-sqlite` via `mcp_server.py`) alongside the external Nasdaq Data Link MCP server (`nasdaq-data-link` via `uv` and `/Users/albert/Projects/nasdaq-data-link-mcp`) in a workspace-local configuration and multi-server client, verifying simultaneous `stdio` connection, tool discovery, namespaced invocation, and graceful error isolation.

### Deliverables Implemented
1. **Workspace MCP Server Registry (`mcp_servers.json` & `.env.example`):**
   - Configured `usdjpy-sqlite` pointing to `/Users/albert/Projects/fx-signal-reader/.venv/bin/python` `/Users/albert/Projects/fx-signal-reader/mcp_server.py` with `FX_DB_PATH` targeting `/Users/albert/Projects/fx-signal-reader/usdjpy_signals.db`.
   - Configured `nasdaq-data-link` using `uv run --directory /Users/albert/Projects/nasdaq-data-link-mcp nasdaq_data_link_mcp_os/server.py` with `PYTHONPATH` and `${NASDAQ_DATA_LINK_API_KEY}` placeholder (no hardcoded secrets committed; `.env.example` updated with `NASDAQ_DATA_LINK_API_KEY=`).
   - Left global `/Users/albert/.gemini/config/mcp_config.json` untouched.
2. **Multi-Server MCP Client (`mcp_client.py`):**
   - Implemented `MultiServerMCPClient` using `contextlib.AsyncExitStack`, `mcp.client.stdio.stdio_client`, `mcp.StdioServerParameters`, and `mcp.ClientSession` (`mcp 2.3.0`).
   - Added `${VAR}` and `${VAR:-fallback}` expansion (`expand_env_vars`, `load_server_specs`) from `os.environ`, with optional `.env` and read-only runtime MCP config fallback when `env` is not explicitly overridden, plus secret redaction in `MCPServerSpec.to_safe_dict()`.
   - Added per-server isolated `AsyncExitStack` startup in `_connect_single_server` so broken, exiting, missing, or timing-out servers are recorded in `failed_servers` / `connection_status` without crashing healthy server sessions.
   - Implemented `discover_tools() -> dict[str, list[dict]]` alongside unified namespaced tool registry (`namespaced_tools` / `get_namespaced_tools()`, e.g. `usdjpy-sqlite:get_latest_yield_spread`, `nasdaq-data-link:list_databases`), plus `discover_resources()` / `read_resource()`.
   - Implemented `call_tool()`, `call_namespaced_tool("server:tool", arguments)`, `extract_tool_result()` (unwrapping `{"result": ...}` wrappers and parsing JSON text content), synchronous convenience wrappers (`discover_tools_sync`, `call_tool_sync`, `call_namespaced_tool_sync`), and CLI modes (`--list-tools`, `--smoke-test`, `--call`).
3. **Automated Multi-Server Test Suite (`tests/test_mcp_multi_server.py`):**
   - Config schema, secret-placeholder hygiene, and `${VAR}` expansion tests.
   - Helper tests for namespaced parsing, result extraction, and secret redaction.
   - Graceful failure-isolation tests against missing binaries, immediate-exit subprocesses, and hanging startup timeouts alongside a healthy `usdjpy-sqlite` fixture server.
   - Live simultaneous `stdio` integration tests against both `usdjpy-sqlite` and `nasdaq-data-link` (tool discovery, resource reads, live tool execution, sync wrappers, and CLI `--smoke-test`).

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Workspace-local `mcp_servers.json` created with both `usdjpy-sqlite` and `nasdaq-data-link` (and `${NASDAQ_DATA_LINK_API_KEY}` placeholder; global `mcp_config.json` unmodified)
- [x] `MultiServerMCPClient` implemented in `mcp_client.py` (`AsyncExitStack`, `stdio_client`, `StdioServerParameters`, `ClientSession`)
- [x] Simultaneous `stdio` connection, tool discovery (`discover_tools` + `<server>:<tool>` registry), namespaced `call_tool` / `call_namespaced_tool`, sync wrappers, and CLI `--list-tools` / `--smoke-test` verified
- [x] Graceful isolation verified for missing, failing, and timing-out servers
- [x] Verified in local `.venv` environment (11/11 tests in `tests/test_mcp_multi_server.py` passing; 6/6 tests in `tests/test_containerization.py` passing)
- [x] Linked artifacts and trackers updated (Drive task doc `1jX6bFPvmjwUjE56zdSrPAJv9CFnGx3Pl` and Google Sheet tracker Row 12)

---

## 3. Execution Log & Findings

### 3.1 Discovered Tools Across Both MCP Servers (`--list-tools`)

Simultaneous connection initialized:
- `usdjpy-sqlite` -> `usdjpy-sqlite-signals` (`v1.0.0`, 4 tools)
- `nasdaq-data-link` -> `NASDAQ Data Link MCP` (`v1.30.0`, 5 tools, 1 resource)

| Namespaced Tool ID | Server | Tool Name | Summary |
| :--- | :--- | :--- | :--- |
| `usdjpy-sqlite:get_latest_yield_spread` | `usdjpy-sqlite` | `get_latest_yield_spread` | Latest US 2Y/10Y, JP 10Y, spread (% & bps), and `JPY=X` spot snapshot |
| `usdjpy-sqlite:get_upcoming_macro_events` | `usdjpy-sqlite` | `get_upcoming_macro_events` | Query `macro_events` filtered by optional `currency`, `impact`, `limit` |
| `usdjpy-sqlite:get_recent_mof_alerts` | `usdjpy-sqlite` | `get_recent_mof_alerts` | Query `news_headlines` scored on 4-tier MoF/BoJ verbal intervention ladder |
| `usdjpy-sqlite:get_market_ticks_summary` | `usdjpy-sqlite` | `get_market_ticks_summary` | Latest OHLCV and bar-over-bar change for core FX/macro tickers |
| `nasdaq-data-link:search_datasets` | `nasdaq-data-link` | `search_datasets` | Search Nasdaq Data Link datasets by `query` |
| `nasdaq-data-link:get_dataset` | `nasdaq-data-link` | `get_dataset` | Retrieve dataset rows for `dataset_code` (`DATABASE/DATASET`) |
| `nasdaq-data-link:get_dataset_metadata` | `nasdaq-data-link` | `get_dataset_metadata` | Retrieve dataset metadata without downloading full series |
| `nasdaq-data-link:list_databases` | `nasdaq-data-link` | `list_databases` | List available Nasdaq Data Link databases |
| `nasdaq-data-link:export_dataset` | `nasdaq-data-link` | `export_dataset` | Export dataset in `csv`, `json`, or `xml` format |

Discovered MCP Resources:
- `nasdaq-data-link`: `nasdaq://databases` (verified readable live via `read_resource`, returning `# Available Nasdaq Data Link Databases` catalog markdown).

### 3.2 Live Multi-Server Smoke Check (`.venv/bin/python mcp_client.py --smoke-test`)

```json
{
  "connections": {
    "usdjpy-sqlite": {
      "name": "usdjpy-sqlite",
      "connected": true,
      "server_name": "usdjpy-sqlite-signals",
      "server_version": "1.0.0",
      "error": null
    },
    "nasdaq-data-link": {
      "name": "nasdaq-data-link",
      "connected": true,
      "server_name": "NASDAQ Data Link MCP",
      "server_version": "1.30.0",
      "error": null
    }
  },
  "tools_per_server": {
    "usdjpy-sqlite": [
      "get_latest_yield_spread",
      "get_upcoming_macro_events",
      "get_recent_mof_alerts",
      "get_market_ticks_summary"
    ],
    "nasdaq-data-link": [
      "search_datasets",
      "get_dataset",
      "get_dataset_metadata",
      "list_databases",
      "export_dataset"
    ]
  },
  "namespaced_tools": [
    "nasdaq-data-link:export_dataset",
    "nasdaq-data-link:get_dataset",
    "nasdaq-data-link:get_dataset_metadata",
    "nasdaq-data-link:list_databases",
    "nasdaq-data-link:search_datasets",
    "usdjpy-sqlite:get_latest_yield_spread",
    "usdjpy-sqlite:get_market_ticks_summary",
    "usdjpy-sqlite:get_recent_mof_alerts",
    "usdjpy-sqlite:get_upcoming_macro_events"
  ],
  "resources_per_server": {
    "usdjpy-sqlite": [],
    "nasdaq-data-link": [
      "nasdaq://databases"
    ]
  },
  "checks": {
    "usdjpy-sqlite": {
      "ok": true,
      "spread_10y": 2.178,
      "spread_bps": 217.8,
      "usdjpy_spot": 159.13800048828125,
      "ticks_symbol_count": 2,
      "upcoming_events_count": 3,
      "mof_alerts_count": 3
    },
    "nasdaq-data-link": {
      "discovered_tools_count": 5,
      "resource_nasdaq_databases_ok": true,
      "resource_preview": "# Available Nasdaq Data Link Databases\n\n## WORLDBANK - World Bank Indicators\nGlobal development indicators from World Ba",
      "list_databases_roundtrip": true,
      "list_databases_is_error": true,
      "list_databases_preview": "Error executing tool list_databases: Session.request() got an unexpected keyword argument 'per_page'"
    }
  }
}
```

**Note on external `nasdaq-data-link` runtime calls:** The external `nasdaq-data-link-mcp` (`v1.30.0`) subprocess connects, lists all 5 tools, serves `nasdaq://databases`, and completes `call_tool` JSON-RPC roundtrips cleanly over `stdio`. Calling `list_databases` returns a tool-level error from the upstream `nasdaqdatalink` library (`Session.request() got an unexpected keyword argument 'per_page'`), which `MultiServerMCPClient` surfaces cleanly via `MCPToolExecutionError` (or `raw=True`) without affecting active sessions.

### 3.3 Automated Test Suite Output (`.venv/bin/python -m unittest -v tests/test_mcp_multi_server.py`)

```text
test_broken_and_exiting_servers_isolated_from_healthy_sqlite_server (tests.test_mcp_multi_server.TestGracefulServerIsolation.test_broken_and_exiting_servers_isolated_from_healthy_sqlite_server) ... ok
test_hanging_server_timeout_isolated_from_healthy_server (tests.test_mcp_multi_server.TestGracefulServerIsolation.test_hanging_server_timeout_isolated_from_healthy_server) ... ok
test_live_simultaneous_connection_discovery_and_execution (tests.test_mcp_multi_server.TestLiveMultiServerStdio.test_live_simultaneous_connection_discovery_and_execution) ... ok
test_sync_wrappers_and_cli_list_tools_and_smoke_test (tests.test_mcp_multi_server.TestLiveMultiServerStdio.test_sync_wrappers_and_cli_list_tools_and_smoke_test) ... ok
test_extract_tool_result_variants (tests.test_mcp_multi_server.TestMCPClientHelpers.test_extract_tool_result_variants) ... ok
test_parse_namespaced_tool_valid_and_invalid (tests.test_mcp_multi_server.TestMCPClientHelpers.test_parse_namespaced_tool_valid_and_invalid) ... ok
test_server_spec_to_safe_dict_redacts_sensitive_values (tests.test_mcp_multi_server.TestMCPClientHelpers.test_server_spec_to_safe_dict_redacts_sensitive_values) ... ok
test_expand_env_vars_supports_placeholders_defaults_and_strict_mode (tests.test_mcp_multi_server.TestMCPConfigLoadingAndEnvExpansion.test_expand_env_vars_supports_placeholders_defaults_and_strict_mode) ... ok
test_load_server_specs_error_cases (tests.test_mcp_multi_server.TestMCPConfigLoadingAndEnvExpansion.test_load_server_specs_error_cases) ... ok
test_load_server_specs_expands_env_and_filters_disabled (tests.test_mcp_multi_server.TestMCPConfigLoadingAndEnvExpansion.test_load_server_specs_expands_env_and_filters_disabled) ... ok
test_workspace_mcp_servers_json_structure_and_no_hardcoded_secret (tests.test_mcp_multi_server.TestMCPConfigLoadingAndEnvExpansion.test_workspace_mcp_servers_json_structure_and_no_hardcoded_secret) ... ok

----------------------------------------------------------------------
Ran 11 tests in 26.321s

OK
```
