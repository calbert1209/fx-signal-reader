# P4-08: Test suite migration & verification

* **Phase:** Phase 4: Package Reorganization & Cleanup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T11:46:40Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Migrate all 8 test modules under `tests/` to import from `fx_signal_reader.*`, install the package in editable mode, and verify 100% pass rate across all unit, integration, and CLI tests.

## 2. Implementation Notes & Acceptance Criteria

1. Remove `sys.path.insert(0, str(PROJECT_ROOT))` boilerplate across all test files in `tests/`.
2. Update imports and path constants (`SCHEMA_PATH`, `LIVE_DB_PATH`, `DEFAULT_CONFIG_PATH`) in `tests/test_*.py` to target `fx_signal_reader.*` and `data/usdjpy_signals.db`.
3. Update `tests/test_containerization.py` assertions to match the new `Dockerfile` layout (`pyproject.toml`, `fx_signal_reader/`, `fx-schedule`).
4. Run `.venv/bin/pip install -e .` and execute `.venv/bin/python -m unittest discover -s tests -v` (confirming 118/118 tests pass).
5. Smoke-test all `fx-*` console scripts (`fx-collect --help`, `fx-schedule --dry-run`, `fx-mcp-client --list-tools`, `fx-pipeline --db-path data/usdjpy_signals.db`).

- [x] Implementation complete (migrated all 8 test modules in `tests/` to `fx_signal_reader.*`, removed `sys.path.insert` hacks, updated `test_containerization.py`)
- [x] Verified in local sandbox / environment (`.venv/bin/python -m unittest discover -s tests -v`: 118/118 passed in 34.314s; all 5 `fx-*` CLI entrypoints verified)
- [x] Linked artifacts and logs updated

## 3. Execution Log & Findings

- Removed `sys.path.insert(0, str(PROJECT_ROOT))` boilerplate from `test_mcp_server.py`, `test_mcp_multi_server.py`, `test_nodes.py`, and `test_graph_execution.py`.
- Updated imports across all 8 test modules to reference `fx_signal_reader.core`, `fx_signal_reader.collectors`, `fx_signal_reader.mcp`, `fx_signal_reader.agent`, and `fx_signal_reader.cli`.
- Updated `tests/test_containerization.py` to assert `pyproject.toml`, `fx_signal_reader/`, and `CMD ["fx-schedule", "--db-path", "/app/data/usdjpy_signals.db"]`.
- **Full Test Suite:** `Ran 118 tests in 34.314s — OK` (`118/118` passed).
- **CLI Smoke Tests:**
  - `.venv/bin/fx-collect --help` -> exit 0
  - `.venv/bin/fx-schedule --dry-run --run-once` -> exit 0
  - `.venv/bin/fx-mcp --help` -> exit 0
  - `.venv/bin/fx-mcp-client --list-tools` -> connected to both `usdjpy-sqlite` and `nasdaq-data-link` (9 namespaced tools)
  - `.venv/bin/fx-pipeline --db-path data/usdjpy_signals.db` -> exit 0 (`SKIPPED_HOLD`, `PAPER-FE6D20D6DA1B`)
