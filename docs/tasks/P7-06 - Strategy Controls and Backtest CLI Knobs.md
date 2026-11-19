# P7-06: Strategy controls and backtest CLI knobs

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `TODO`
* **Complexity:** Multi-Task Project (Part 2 of 2: Backtest CLI Knobs & Optimization Controls)
* **Last Updated (UTC):** `2026-11-19T05:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Expose the new strategy knobs as first-class CLI flags in `fx-backtest` (`fx_signal_reader/cli/backtest.py`) and `fx-pipeline` (`fx_signal_reader/cli/pipeline.py`), allowing rapid parameter exploration, regime isolation, and walk-forward testing.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/cli/backtest.py` & `models.py`:**
   - Add `--allowed-regimes` flag (e.g. `--allowed-regimes RISK_ON,TRANSITIONAL`).
   - Add `--breakeven-r` flag (e.g. `--breakeven-r 1.0` or `--no-breakeven`).
   - Add `--sl-pips` and `--tp-pips` override flags (defaults: 75.0 and 150.0).
   - Add `--use-atr` flag to toggle fixed vs ATR-based brackets.
2. **`fx_signal_reader/cli/pipeline.py`:**
   - Expose consistent regime filter and bracket configuration flags for live/paper runs.
3. **Tests & Verification:**
   - CLI argument parsing tests in `tests/test_backtest.py`.
   - Verify formatted report displays configured regime filters, breakeven trigger, and bracket mode.

- [ ] Implementation complete
- [ ] CLI flags and argument parser tests passing
- [ ] Integration report formatting verified
