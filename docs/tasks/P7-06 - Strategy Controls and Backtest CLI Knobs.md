# P7-06: Strategy controls and backtest CLI knobs

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `DONE`
* **Complexity:** Multi-Task Project (Part 2 of 2: Backtest CLI Knobs & Optimization Controls)
* **Last Updated (UTC):** `2026-11-19T07:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Expose the new strategy knobs as first-class CLI flags in `fx-backtest` (`fx_signal_reader/cli/backtest.py`) and `fx-pipeline` (`fx_signal_reader/cli/pipeline.py`), allowing rapid parameter exploration, regime isolation, and walk-forward testing.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/cli/backtest.py` & `backtest/models.py`:**
   - Added `--allowed-regimes` (default `RISK_ON,TRANSITIONAL`, accepts comma-separated string or sequence).
   - Added `--breakeven-r` (default `1.0`) and `--no-breakeven` toggle (`breakeven_trigger_r=None`).
   - Added `--sl-pips` (default `75.0`), `--tp-pips` (default `150.0`), and `--use-atr` toggle.
   - Updated `format_backtest_summary` to print the active `Strategy` configuration line (`regimes=... | brackets=... | breakeven=...`).
2. **`fx_signal_reader/cli/pipeline.py` & `agent/state.py` / `nodes.py`:**
   - Exposed `--allowed-regimes`, `--sl-pips`, `--tp-pips`, and `--use-atr` flags on `fx-pipeline` and wired them through `run_signal_pipeline(...)` -> `create_initial_state(...)` -> `risk_committee_node`.
3. **Tests & Verification:**
   - Added `test_cli_strategy_knobs_and_summary_formatting` in `tests/test_backtest.py` covering CLI flags, JSON config output, formatted `Strategy` report line, and `pipeline.py` argument parsing.
   - Verified full suite `182/182` passing and verified all CLI combinations via live 2-year `fx-backtest` runs.

- [x] Implementation complete
- [x] CLI flags and argument parser tests passing (`182/182` suite)
- [x] Integration report formatting and live CLI runs verified
