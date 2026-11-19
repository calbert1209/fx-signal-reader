# P7-05: Dynamic brackets and breakeven stop ratchet

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `TODO`
* **Complexity:** Multi-Task Project (Part 1 of 2: Simulator Engine & Profit Protection)
* **Last Updated (UTC):** `2026-11-19T05:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Enhance `fx_signal_reader/backtest/simulator.py` to support profit-locking trade mechanisms:
1. **Breakeven Stop Ratchet:** When unrealized trade profit reaches `+1.0R` (`+75 pips` or configurable threshold), automatically move the effective Stop Loss to entry price (plus spread buffer) to prevent large floating gains from turning into full `-1R` losses.
2. **Volatility-Adaptive Brackets (ATR):** Implement optional Average True Range (ATR-14) dynamic brackets (`SL = 1.0 * ATR`, `TP = 2.0 * ATR`) in place of fixed 75/150 pip levels to adapt to shifting session volatility.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/backtest/models.py`:**
   - Add `BREAKEVEN` to `ExitReason` enum.
   - Add `breakeven_trigger_r: float | None = 1.0` to `BacktestConfig`.
2. **`fx_signal_reader/backtest/simulator.py`:**
   - In `simulate_trade_resolution`, scan forward bars and track maximum favorable excursion (MFE). If MFE reaches the breakeven threshold, raise the active stop loss floor to `effective_entry`.
   - Record `ExitReason.BREAKEVEN` when price pulls back to the ratcheted entry level.
3. **Tests & Verification:**
   - Unit tests covering breakeven ratchet triggering, partial profit locking, and ATR calculation.
   - Assert zero regressions on existing bracket resolution tests.

- [ ] Implementation complete
- [ ] Breakeven ratchet unit tests passing
- [ ] Positive expectancy impact verified
