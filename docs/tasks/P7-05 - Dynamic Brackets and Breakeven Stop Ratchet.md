# P7-05: Dynamic brackets and breakeven stop ratchet

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `DONE`
* **Complexity:** Multi-Task Project (Part 1 of 2: Simulator Engine & Profit Protection)
* **Last Updated (UTC):** `2026-11-19T07:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Enhance `fx_signal_reader/backtest/simulator.py` to support profit-locking trade mechanisms:
1. **Breakeven Stop Ratchet:** When unrealized trade profit reaches `+1.0R` (`+75 pips` or configurable threshold), automatically move the effective Stop Loss to entry price (`effective_entry` including spread buffer) to prevent large floating gains from turning into full `-1R` losses.
2. **Volatility-Adaptive Brackets (ATR):** Implement optional Average True Range (ATR-14) dynamic brackets (`SL = 1.0 * ATR`, `TP = 2.0 * ATR`, clamped `[0.25, 2.50]` JPY) alongside configurable fixed levels.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/core/indicators.py`, `core/__init__.py`, & `mcp/server.py`:**
   - Implemented `DEFAULT_ATR_PERIOD = 14`, `compute_true_ranges`, `compute_atr_series`, and `compute_latest_atr` with strict length/finiteness/high-low guards.
   - Extended `get_market_trend_summary` to query `high`/`low` ticks and include `"atr_14"` in the snapshot.
2. **`fx_signal_reader/backtest/models.py` & `fx_signal_reader/agent/state.py` / `nodes.py`:**
   - Added `ExitReason.BREAKEVEN` and `BacktestConfig.breakeven_trigger_r: float | None = 1.0`, `sl_pips`, `tp_pips`, `use_atr`, `allowed_regimes`.
   - Added `RiskAssessment.atr_14`, `compute_bracket_distances_jpy(...)`, and strategy fields on `AgentState` / `create_initial_state`.
3. **`fx_signal_reader/backtest/simulator.py`:**
   - Updated `simulate_trade_resolution` to arm the breakeven floor (`active_sl = effective_entry`) once intra-bar MFE reaches `breakeven_trigger_r * risk_per_unit`, recording `ExitReason.BREAKEVEN` on subsequent pullbacks.
4. **Tests & 2-Year Historical Verification (`2024-11-19` -> `2026-11-19`, 1.5 pip spread):**
   - Full suite: `182/182` tests passing (added breakeven BUY/SELL/disabled tests in `tests/test_backtest_simulator.py` and ATR/node bracket tests in `tests/test_indicators.py`).
   - **Fixed `75/150` + `+1.0R` Breakeven:** `131` trades, Net PnL `+$1,003.09` (`+10.03%`), Profit Factor `1.1617`, Expectancy `+$7.66` (`+0.0841R`), Sharpe `0.5053`, Max Drawdown `$1,058.79` (`10.59%`).
   - **Fixed `75/150` + `--no-breakeven`:** `118` trades, Net PnL `+$1,563.83` (`+15.64%`), Profit Factor `1.2518`, Expectancy `+$13.25` (`+0.1412R`), Sharpe `0.7285`, Max Drawdown `$1,089.16` (`10.89%`).
   - **Finding on `1.0x` 1h ATR (`--use-atr`):** On 1-hour bars, `1.0x ATR-14` frequently clamps to the `0.25` JPY (25-pip) floor, increasing turnover (`358`–`429` trades) and spread friction (`-$2,234.11` with BE off, `-$2,820.82` with BE `+1.0R`); keeping fixed `75/150` brackets (or higher ATR multiples on higher timeframes) remains superior for 120h holds.

- [x] Implementation complete (`182/182` tests passing)
- [x] Breakeven ratchet & ATR-14 unit tests passing
- [x] Positive expectancy verified (`+$1,003.09` with BE `+1.0R`, `+$1,563.83` with BE disabled)
