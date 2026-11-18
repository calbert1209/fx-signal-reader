# P4-05: Agent layer & pipeline CLI

* **Phase:** Phase 4: Package Reorganization & Cleanup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T11:41:52Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Migrate the LangGraph state schema, specialist nodes, and approval/paper-execution graph into `fx_signal_reader/agent/`, and move `run_pipeline.py` to `fx_signal_reader/cli/pipeline.py`.

## 2. Implementation Notes & Acceptance Criteria

1. Move `agent/*` -> `fx_signal_reader/agent/*` (`__init__.py`, `state.py`, `nodes.py`, `graph.py`).
2. Remove all `try/except ImportError` and `sys.path.insert(...)` fallback blocks, replacing them with clean `fx_signal_reader.core.*`, `fx_signal_reader.mcp.*`, and `fx_signal_reader.agent.*` imports.
3. Move `run_pipeline.py` -> `fx_signal_reader/cli/pipeline.py` (`fx-pipeline` entrypoint) with updated package imports.

- [x] Implementation complete
- [x] Verified in local sandbox / environment
- [x] Linked artifacts and logs updated

## 3. Execution Log & Findings

* **2026-11-18T11:41:52Z:** Migrated `agent/` (`__init__.py`, `state.py`, `nodes.py`, `graph.py`) into `fx_signal_reader/agent/` and `run_pipeline.py` into `fx_signal_reader/cli/pipeline.py`, removing `try/except ImportError` and `sys.path.insert(...)` fallbacks in favor of `fx_signal_reader.core.*`, `fx_signal_reader.mcp.*`, and `fx_signal_reader.agent.*` imports (`DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "paper_trades.jsonl"`).
* **Verification:**
  * `.venv/bin/fx-pipeline --help` exited `0` and printed CLI usage/options.
  * `.venv/bin/python -c "import fx_signal_reader.agent as a; print(len(a.__all__), 'exports ok')"` printed `25 exports ok`.
