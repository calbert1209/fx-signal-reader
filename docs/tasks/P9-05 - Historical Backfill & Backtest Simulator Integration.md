# P9-05: Historical Backfill & Backtest Simulator Integration

* **Phase:** Phase 9: Advanced USD/JPY Signals (Yield Curve, Terms-of-Trade & Intraday Fix)
* **Status:** `PENDING`
* **Complexity:** Integration Task (Depends on P9-01, P9-02, P9-03)
* **Last Updated (UTC):** `2026-11-20T13:35:00Z`
* **Implementation Plan:** [plan_p1_p2_p3_signals.md](file:///Users/albert/.gemini/antigravity/brain/df88ef12-66c7-48bc-a6ad-f98f47ed301e/plan_p1_p2_p3_signals.md)

---

## 1. Objective & Scope

Ensure historical replay and point-in-time backtesting (`fx-backtest`) leverage the 3 new signals:

1. **Historical Backfill Script (`backtest/backfill.py`):**
   - Extend `backfill_market_data()` to download historical `CL=F` (Crude Oil) candles alongside `JPY=X`, `^TNX`, `^GSPC`, etc.
   - Extend historical sovereign yield backfill to extract historical 2Y JGB yields from MoF `jgbcm.csv` and historical US 2Y Treasury yields (`DGS2` or `2YY=F`).
   - Populate `yield_spreads` with `jp_2y`, `us_2y`, and `spread_2y`.
2. **Point-in-Time Simulator Integration:**
   - Verify `point_in_time=True` agent replays query historical `CL=F` ticks, historical 2Y spreads, and point-in-time Gotobi sessions without future data leakage.
   - Ensure graceful fallback: if historical 2Y or oil data is missing for earlier historical intervals, the agent smoothly defaults without raising unhandled exceptions.

## 2. Implementation Notes & Acceptance Criteria

1. **Files Affected:**
   - `fx_signal_reader/backtest/backfill.py`
   - `fx_signal_reader/cli/backtest.py`
   - `tests/test_backtest_backfill.py`
   - `tests/test_backtest_pit.py`
2. **Acceptance Criteria:**
   - `fx-backtest --backfill` populates `market_ticks` with `CL=F` and `yield_spreads` with 2Y data.
   - Point-in-time backtests execute cleanly with dynamic Gotobi session checks matching historical bar timestamps.
   - Backtest test suite passes with zero regressions.

- [ ] `backtest/backfill.py` updated for historical `CL=F` and 2Y sovereign yields
- [ ] Point-in-time replay verified for all 3 signals
- [ ] Backtest unit tests written and passing
