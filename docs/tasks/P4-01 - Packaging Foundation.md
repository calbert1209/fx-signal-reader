# P4-01: Packaging foundation (`pyproject.toml`)

* **Phase:** Phase 4: Package Reorganization & Cleanup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T11:39:50Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Establish the installable Python package skeleton (`fx_signal_reader/`) and `pyproject.toml` configuration with `setuptools` build metadata, package data rules, and first-class `fx-*` CLI entrypoints.

## 2. Implementation Notes & Acceptance Criteria

1. Create `pyproject.toml` using `setuptools>=68.0` (`setuptools.build_meta`), Python `>=3.11`, and dependencies matching `requirements.txt`.
2. Configure `[tool.setuptools.package-data]` to bundle `fx_signal_reader/core/schema.sql` and `fx_signal_reader/mcp/mcp_servers.json`.
3. Register `[project.scripts]` console entrypoints:
   - `fx-collect = "fx_signal_reader.cli.collect:main"`
   - `fx-schedule = "fx_signal_reader.cli.schedule:main"`
   - `fx-mcp = "fx_signal_reader.mcp.server:main"`
   - `fx-mcp-client = "fx_signal_reader.mcp.client:main"`
   - `fx-pipeline = "fx_signal_reader.cli.pipeline:main"`
4. Create package initializers: `fx_signal_reader/__init__.py`, `fx_signal_reader/core/__init__.py`, `fx_signal_reader/mcp/__init__.py`, `fx_signal_reader/cli/__init__.py`.

- [x] Implementation complete (`pyproject.toml` and `fx_signal_reader/` initializers created)
- [x] Verified in local sandbox / environment (`.venv/bin/pip install --no-build-isolation --no-deps -e .` installed `fx-signal-reader 0.1.0` and generated all 5 `fx-*` scripts in `.venv/bin/`)
- [x] Linked artifacts and logs updated

## 3. Execution Log & Findings

- Created `pyproject.toml` with `setuptools.build_meta`, `requires-python >= 3.11`, package-data rules for `schema.sql` & `mcp_servers.json`, and `fx-collect`, `fx-schedule`, `fx-mcp`, `fx-mcp-client`, `fx-pipeline` console scripts.
- Editable install succeeded cleanly; verified `fx_signal_reader.__version__ == "0.1.0"` and `.venv/bin/fx-*` binaries created.
