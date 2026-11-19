# P7-01: Baseline edge optimization (120h & RISK_OFF veto)

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `DONE`
* **Complexity:** Single-Task Project
* **Last Updated (UTC):** `2026-11-19T06:10:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Codify the two highest-impact empirical findings from the 2-year backtest experiments into the core agent logic and backtest engine:
1. Extend the default holding period from `72h` to `120h` (5 business days) to give 150-pip USD/JPY moves adequate room to mature without premature time-stop exits.
2. Veto new trade entries in `RISK_OFF` (`VIX >= 25.0`) in `risk_committee_node` to avoid trading during erratic, high-friction market panics.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/agent/nodes.py`:**
   - In `risk_committee_node`, updated `veto_trade` evaluation to explicitly include `is_risk_off = (risk_regime == RiskRegime.RISK_OFF or (vix_level is not None and vix_level >= VIX_RISK_OFF_THRESHOLD))`, appending detailed veto reasons to `risk_notes`.
2. **`fx_signal_reader/backtest/models.py` & `cli/backtest.py`:**
   - Updated default `max_hold_hours` from `72` to `120` in `BacktestConfig` and `fx-backtest` CLI argument parser.
3. **Tests & Verification:**
   - Updated `tests/test_nodes.py`, `tests/test_graph_execution.py`, `tests/test_backtest.py`, and `tests/test_backtest_simulator.py`.
   - Verified 173/173 tests passing.
   - Run 2-year backtest (`2024-11-19` to `2026-11-19`, 1.5 pip spread): executed 209 trades, 241 vetoes, net PnL `+$445.51` (+4.46% return), PF `1.0361`, Sharpe `0.2087`.

- [x] Implementation complete
- [x] Unit tests updated and passing (173/173)
- [x] 2-year backtest positive edge verified (+$445.51 Net PnL)
