# P6-03: Backtest schemas & bracket simulator

* **Phase:** Phase 6: Historical Backtesting & Evaluation
* **Status:** `DONE`
* **Parallelism:** Wave 1 (Independent — can run in parallel with `P6-01` and `P6-02`)
* **Last Updated (UTC):** `2026-11-19T03:43:00Z`
* **Backtesting Plan:** [Backtesting Implementation Plan.md](https://drive.google.com/file/d/1WIkuIDYbEVfCaL7HK7IHO6w5WTnDpaBR/view)

---

## 1. Objective & Scope

Define Pydantic v2 schemas (`fx_signal_reader/backtest/models.py`) and implement the deterministic bracket trade-resolution engine and portfolio analytics (`fx_signal_reader/backtest/simulator.py`).

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/backtest/models.py`:**
   - `BacktestConfig`: `start_utc`, `end_utc`, `step_hours` (`4`), `max_hold_hours` (`72`), `spread_pips` (`1.5`), `allow_overlapping_trades` (`False`), `conservative_tie_break` (`True`), `mof_lookback_hours` (`48`), `initial_capital_usd` (`10000.0`).
   - `SimulatedTrade`: `trade_id`, `proposal_id`, `entry_time_utc`, `exit_time_utc`, `action`, `position_size_units`, `entry_price`, `stop_loss`, `take_profit`, `exit_price`, `exit_reason` (`TAKE_PROFIT`, `STOP_LOSS`, `TIME_STOP`, `END_OF_DATA`), `pnl_pips`, `pnl_jpy`, `pnl_usd`, `r_multiple`, `macro_bias`, `risk_regime`, `intervention_risk_score`, `bars_held`.
   - `BacktestMetrics` & `BacktestReport`: aggregate performance metrics, regime/bias breakdowns, equity curve, and data-coverage diagnostics.
2. **`fx_signal_reader/backtest/simulator.py` (Core Resolution & Analytics):**
   - `simulate_trade_resolution(proposal, future_bars, config)`:
     - Applies round-trip spread/slippage (`spread_pips * 0.01` JPY) to `entry_price`.
     - Scans forward OHLC bars up to `max_hold_hours`; resolves `STOP_LOSS` vs `TAKE_PROFIT` (conservative SL-first tie-break by default when both trigger in the same bar).
     - Closes at bar `close` on `TIME_STOP` or `END_OF_DATA`.
     - Computes `pnl_pips`, `pnl_jpy`, `pnl_usd` (`pnl_jpy / exit_price`), and `r_multiple`.
   - `compute_backtest_metrics(...)`:
     - Computes Win Rate, Profit Factor, Expectancy (USD), Average R-Multiple, Peak-to-Trough Max Drawdown (USD & %), Annualized Sharpe Ratio, and breakdowns by `RiskRegime` and `YieldBias`.

- [x] Implementation complete
- [x] Unit tests for bracket SL/TP/collision/time-stop/spread/metrics passing

## 3. Execution Log & Findings

* **2026-11-19T03:43:00Z:** Implemented `fx_signal_reader/backtest/models.py` (`ExitReason`, `BacktestConfig`, `SimulatedTrade`, `BacktestMetrics`, `BacktestReport`) and `fx_signal_reader/backtest/simulator.py` (`simulate_trade_resolution`, `compute_backtest_metrics`) with `1.5` pip spread default, single-position concurrency default, conservative SL-first tie-breaking, 72h time-stop, and regime/bias breakdowns.
* **Verification:** `tests/test_backtest_simulator.py` (20/20 passing) and full regression suite (`166/166` passing).
