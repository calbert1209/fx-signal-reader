# P5-02: Agent usage guide (ASD-STE100)

* **Phase:** Phase 5: Documentation & Onboarding
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T12:24:38Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Create `docs/AGENT_GUIDE.md` expanding on `README.md` with runnable examples for connecting an external MCP host, calling MCP tools from Python, running the LangGraph approval workflow, and invoking `fx-pipeline --json`, composed in ASD-STE100 (`simple-tech-eng`).

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete ([`docs/AGENT_GUIDE.md`](file:///Users/albert/Projects/fx-signal-reader/docs/AGENT_GUIDE.md) created and linked from [`README.md`](file:///Users/albert/Projects/fx-signal-reader/README.md))
- [x] Verified in local sandbox / environment (both Python examples executed cleanly against `data/usdjpy_signals.db` with exit code `0`)
- [x] Linked artifacts and logs updated (recorded in Google Sheet row 25)

---

## 3. Execution Log & Findings

- **MCP Python Example (`fx_signal_reader.mcp.client`):**
  - Connected to `['usdjpy-sqlite', 'nasdaq-data-link']` over `stdio`.
  - Returned `Spread (bps): 217.8`, `Spot USD/JPY: 159.13800048828125`, and `High-tier alerts: 3`.
- **LangGraph Python Example (`fx_signal_reader.agent`):**
  - Executed `build_signal_graph(db_path="data/usdjpy_signals.db")` end-to-end.
  - Returned `Final action: HOLD` and `Receipt status: SKIPPED_HOLD`.
