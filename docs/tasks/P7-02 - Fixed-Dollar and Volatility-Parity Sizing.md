# P7-02: Fixed-dollar and volatility-parity risk sizing

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `TODO`
* **Complexity:** Single-Task Project
* **Last Updated (UTC):** `2026-11-19T05:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Replace the fixed regime unit tier scaling (`10,000` / `5,000` / `2,500` units) with a standard equal-dollar risk sizing model (e.g. risking a constant `1.0%` of account equity, or `$100` per trade), eliminating the payoff asymmetry where losses in `RISK_ON` (10k units) overpowered gains in `TRANSITIONAL` and `RISK_OFF`.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/agent/nodes.py`:**
   - Update `_build_trade_proposal` to calculate `position_size_units` using the planned Stop Loss distance in JPY:
     $$\text{Units} = \left\lfloor \frac{\text{Account Capital} \times \text{Risk \%}}{\text{Stop Loss Distance in JPY}} \times \text{Spot Price} \right\rfloor$$
   - Support optional capital and risk fraction parameters (defaulting to e.g. `$10,000` capital and `1.0%` = `$100` fixed risk per trade).
2. **`fx_signal_reader/backtest/simulator.py`:**
   - Ensure `simulate_trade_resolution` and `compute_backtest_metrics` preserve dollar-based position sizing and compute normalized R-multiples consistently.
3. **Tests & Verification:**
   - Unit tests verifying unit calculation bounds across varying spot prices and stop loss distances.
   - Run backtest and verify regime PnL is normalized.

- [ ] Implementation complete
- [ ] Unit tests passing
- [ ] Position sizing verified across regimes
