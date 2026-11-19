# P7-01: Baseline edge optimization (120h & RISK_OFF veto)

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `TODO`
* **Complexity:** Single-Task Project
* **Last Updated (UTC):** `2026-11-19T05:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Codify the two highest-impact empirical findings from the 2-year backtest experiments into the core agent logic and backtest engine:
1. Extend the default holding period from `72h` to `120h` (5 business days) to give 150-pip USD/JPY moves adequate room to mature without premature time-stop exits.
2. Veto new trade entries in `RISK_OFF` (`VIX >= 25.0`) in `risk_committee_node` to avoid trading during erratic, high-friction market panics.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/agent/nodes.py`:**
   - In `risk_committee_node`, update `veto_trade` evaluation to explicitly include `risk_regime == RiskRegime.RISK_OFF` (or when `vix_level >= VIX_RISK_OFF_THRESHOLD`), documenting the rationale in `risk_notes`.
2. **`fx_signal_reader/backtest/models.py` & `cli/backtest.py`:**
   - Update default `max_hold_hours` from `72` to `120` in `BacktestConfig` and `fx-backtest` CLI argument parser.
3. **Tests & Verification:**
   - Update `tests/test_nodes.py`, `tests/test_graph_execution.py`, and `tests/test_backtest_simulator.py` to reflect the updated default veto conditions and 120h hold window.
   - Run 2-year backtest and assert net PnL is positive (`+$218.25` or higher with 1.5 pip spread).

- [ ] Implementation complete
- [ ] Unit tests updated and passing
- [ ] 2-year backtest positive edge verified
