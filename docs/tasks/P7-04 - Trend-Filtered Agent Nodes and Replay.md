# P7-04: Trend-filtered agent nodes and replay

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `DONE`
* **Complexity:** Multi-Task Project (Part 2 of 2: Agent Node Integration & Falling Knife Protection)
* **Last Updated (UTC):** `2026-11-19T07:17:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Integrate the rolling trend and spread-momentum indicators into `macro_analyst_node` and `risk_committee_node` in `fx_signal_reader/agent/nodes.py` to eliminate "falling knife" clustered Stop Losses during severe multi-hundred pip USD/JPY corrections (e.g. November 2024 and April 2025 sell-offs).

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/agent/state.py` & `fx_signal_reader/agent/nodes.py`:**
   - Extended `MacroAssessment` with optional `spread_5d_change`, `spread_20d_change`, and `spread_momentum`; updated `macro_analyst_node` to downgrade `BULLISH_USD` to `NEUTRAL` when `spread_momentum == "CONTRACTING"` (carry unwinds underway).
   - Extended `RiskAssessment` with optional `ema_50`, `sma_200`, and `market_trend`; updated `risk_committee_node` to call `get_market_trend_summary` and set `veto_trade = True` (`HOLD`) whenever `yield_bias == BULLISH_USD` and `spot_price < ema_50` (falling-knife protection).
2. **Backtest Verification (`2024-11-19` to `2026-11-19`, 1.5 pip spread):**
   - **Executed Trades:** 118 (766 skipped overlap, 807 vetoes, 2,222 holds across 3,106 steps).
   - **Win Rate:** `44.07%` (52 wins / 66 losses) vs `39.71%` Wave 1 baseline.
   - **Net PnL:** `+$1,563.83` (`+15.64%` return) vs `+$445.51` Wave 1 baseline.
   - **Profit Factor / Sharpe:** `1.2518` / `0.7285` (up from `1.0361` / `0.2087`).
   - **Max Drawdown:** `$1,089.16` (`10.89%`, down from `$2,814.33` / `28.14%`).
   - **Sell-off Suppression:** April 2025 sell-off reduced to 1 trade (1 loss) and max consecutive stop-losses capped at 6.
   - **Regimes:** `RISK_ON` (`+$844.91`, 76 trades, +0.1197R) and `TRANSITIONAL` (`+$718.92`, 42 trades, +0.1801R) both strongly profitable.

- [x] Implementation complete (179/179 unit/integration tests passing)
- [x] Agent state and node schemas updated
- [x] Clustered Stop Loss suppression verified on historical replay (`+$1,563.83`, PF `1.2518`, Max DD `10.89%`)
