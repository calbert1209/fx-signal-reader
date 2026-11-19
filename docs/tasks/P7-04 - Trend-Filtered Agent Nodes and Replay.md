# P7-04: Trend-filtered agent nodes and replay

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `TODO`
* **Complexity:** Multi-Task Project (Part 2 of 2: Agent Node Integration & Falling Knife Protection)
* **Last Updated (UTC):** `2026-11-19T05:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Integrate the rolling trend and spread-momentum indicators into `macro_analyst_node` and `risk_committee_node` in `fx_signal_reader/agent/nodes.py` to eliminate "falling knife" clustered Stop Losses during severe multi-hundred pip USD/JPY corrections (e.g. November 2024 and April 2025 sell-offs).

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/agent/state.py` & `nodes.py`:**
   - Update `MacroAssessment` to include `spread_momentum` (`EXPANDING` | `CONTRACTING` | `STABLE`). If the 10Y yield spread is narrowing significantly (carry unwinds underway), downgrade `YieldBias` from `BULLISH_USD` to `NEUTRAL`.
   - Update `RiskAssessment` and `risk_committee_node` to check the trend filter: require `spot_price > ema_50` before issuing a `BUY` proposal. If price is below EMA-50, recommend `HOLD` to avoid buying falling knives until technical stabilization occurs.
2. **Backtest Verification:**
   - Run 2-year backtest and compare against baseline.
   - Confirm clustered consecutive Stop Losses (e.g., 5–8 in a row during April 2025) are suppressed.
   - Verify improvement in Profit Factor, Win Rate, and Maximum Drawdown.

- [ ] Implementation complete
- [ ] Agent state and node schemas updated
- [ ] Clustered Stop Loss suppression verified on historical replay
