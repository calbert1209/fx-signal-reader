# P4-04: MCP layer migration

* **Phase:** Phase 4: Package Reorganization & Cleanup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T11:41:48Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Migrate the custom read-only SQLite MCP server, multi-server MCP client, and server configuration into `fx_signal_reader/mcp/`, removing `sys.path` workarounds.

## 2. Implementation Notes & Acceptance Criteria

1. Move `mcp_server.py` -> `fx_signal_reader/mcp/server.py` (`fx-mcp` entrypoint), removing `sys.path.insert(...)`, updating imports to `fx_signal_reader.*`, and setting `DEFAULT_DB_PATH` to `data/usdjpy_signals.db`.
2. Move `mcp_client.py` -> `fx_signal_reader/mcp/client.py` (`fx-mcp-client` entrypoint), removing `sys.path.insert(...)`, and updating `DEFAULT_CONFIG_PATH` to `Path(__file__).resolve().parent / "mcp_servers.json"`.
3. Move `mcp_servers.json` -> `fx_signal_reader/mcp/mcp_servers.json`, updating `usdjpy-sqlite` to invoke `"-m", "fx_signal_reader.mcp.server"` with `FX_DB_PATH` pointing to `data/usdjpy_signals.db` (retaining `${NASDAQ_DATA_LINK_API_KEY}` placeholder).

- [x] Implementation complete
- [x] Verified in local sandbox / environment
- [x] Linked artifacts and logs updated

## 3. Execution Log & Findings

* **2026-11-18T11:41:48Z — Migration & Verification:**
  * Moved `mcp_server.py` -> `fx_signal_reader/mcp/server.py`, `mcp_client.py` -> `fx_signal_reader/mcp/client.py`, and `mcp_servers.json` -> `fx_signal_reader/mcp/mcp_servers.json`.
  * `fx_signal_reader/mcp/server.py`: Removed `sys.path.insert(...)` and unused `import sys`; updated imports to `fx_signal_reader.core` (`DEFAULT_DB_PATH`, `PROJECT_ROOT`), `fx_signal_reader.core.spread_calculator`, and `fx_signal_reader.collectors.*` (default DB path is now `data/usdjpy_signals.db`).
  * `fx_signal_reader/mcp/client.py`: Updated `PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent` and `DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "mcp_servers.json"`.
  * `fx_signal_reader/mcp/mcp_servers.json`: Updated `usdjpy-sqlite` to invoke `"-m", "fx_signal_reader.mcp.server"` with `FX_DB_PATH=/Users/albert/Projects/fx-signal-reader/data/usdjpy_signals.db`; retained `${NASDAQ_DATA_LINK_API_KEY}` placeholder for `nasdaq-data-link`.
  * **Verification:**
    * `.venv/bin/fx-mcp-client --help`: Passed (exit 0).
    * `.venv/bin/python -c "import fx_signal_reader.mcp.client as c; print('config:', c.DEFAULT_CONFIG_PATH)"`: Passed (`config: /Users/albert/Projects/fx-signal-reader/fx_signal_reader/mcp/mcp_servers.json`).
    * `.venv/bin/python -m py_compile fx_signal_reader/mcp/server.py fx_signal_reader/mcp/client.py`: Passed (exit 0).
    * `.venv/bin/fx-mcp --help` / `import fx_signal_reader.mcp.server`: Currently fails at `fx_signal_reader/collectors/__init__.py` line 5 (`ModuleNotFoundError: No module named 'collectors'`) because `fx_signal_reader/collectors/__init__.py` is pending update by the parallel collectors migration task.
