# P7-02: Fixed-dollar and volatility-parity risk sizing

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `DONE`
* **Complexity:** Single-Task Project
* **Last Updated (UTC):** `2026-11-19T06:10:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Replace the fixed regime unit tier scaling (`10,000` / `5,000` / `2,500` units) with a standard equal-dollar risk sizing model (e.g. risking a constant `1.0%` of account equity, or `$100` per trade), eliminating the payoff asymmetry where losses in `RISK_ON` (10k units) overpowered gains in `TRANSITIONAL` and `RISK_OFF`.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/agent/nodes.py`:**
   - Implemented `compute_position_units(spot_price, stop_loss_distance_jpy=0.75, account_capital_usd=10000.0, risk_fraction=0.01) -> int` with non-negative floor and zero guards:
     $$\text{Units} = \left\lfloor \frac{\text{Account Capital} \times \text{Risk \%}}{\text{Stop Loss Distance in JPY}} \times \text{Spot Price} \right\rfloor$$
   - Updated `_build_trade_proposal` and `risk_committee_node` to compute dynamic unit sizing from state configuration (`account_capital_usd`, `risk_fraction`).
2. **`fx_signal_reader/backtest/simulator.py`:**
   - Updated `simulator.py` to propagate `config.initial_capital_usd` into `create_initial_state`, maintaining uniform dollar risk across historical trade generation.
3. **Tests & Verification:**
   - Added unit test `test_compute_position_units_bounds_and_risk_sizing` covering edge cases, bounds, and variable risk percentages/stop loss distances.
   - Verified 173/173 tests passing.
   - Run 2-year backtest: `TRANSITIONAL` regime turned net profitable (`+$101.92` across 90 trades, PF `1.0181`), and `RISK_ON` delivered `+$343.59` across 119 trades (PF `1.0513`), removing the regime payoff asymmetry.

- [x] Implementation complete
- [x] Unit tests passing (173/173)
- [x] Position sizing verified across regimes with normalized PnL
