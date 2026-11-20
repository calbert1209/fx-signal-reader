# P8-10: EUR/USD Sovereign Yield Spread Threshold Recalibration

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `PENDING`
* **Complexity:** Core Model Calibration & Verification
* **Last Updated (UTC):** `2026-11-20T08:52:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Recalibrate the 10Y sovereign yield spread directional thresholds (`spread_bullish_threshold` and `spread_bearish_threshold`) for `EUR/USD` in `PairProfile` ([pair_profile.py](../../fx_signal_reader/core/pair_profile.py)) to reflect empirical Bundesbank vs. US Treasury spread distributions:

1. **Problem Analysis:**
   - The current baseline thresholds (`-1.00%` bullish, `-2.00%` bearish) are excessively wide and one-sided.
   - In empirical history (`2024-2026`), the 10Y German Bund vs. US Treasury yield spread (`DE10Y - US10Y`) fluctuated between `-2.25%` and `-1.13%` (mean `-1.53%`).
   - Consequently, **0% of days** crossed above `-1.00%` (0 long `BUY` signals in 2 years), while **89.3% of observations** fell into the `[-2.0%, -1.0%]` neutral dead zone.
2. **Empirical Optimization:**
   - Recalibrate thresholds symmetrically around the historical median ($-1.50\%$) with a $\pm20\text{ bps}$ buffer to prevent whipsaw noise:
     - `spread_bullish_threshold = -1.30` (Spread narrowing above $-1.30\%$ triggers `BULLISH` / `BUY` EUR/USD).
     - `spread_bearish_threshold = -1.70` (Spread widening below $-1.70\%$ triggers `BEARISH` / `SELL` EUR/USD).
     - Neutral zone $[-1.70\%, -1.30\%]$ filters out marginal fluctuations.
   - Preliminary simulation indicates this lifts 5-year daily returns from **+5.12% to +17.66%** and 2-year fixed returns from **+3.42% to +5.01% / +9.03%** by successfully unlocking profitable long positions.
3. **Regression & Safety:**
   - Ensure USD/JPY profile parameters (`+2.0%` bullish, `+1.0%` bearish) remain strictly unaffected.
   - Synchronize unit tests and assert 100% test pass rate across the full 288-test regression suite.

---

## 2. Deliverables & Acceptance Criteria

- [ ] Update `PAIR_PROFILES["EUR/USD"]` in `fx_signal_reader/core/pair_profile.py` with `spread_bullish_threshold = -1.30` and `spread_bearish_threshold = -1.70`
- [ ] Update unit tests in `tests/test_pair_profile.py` and `tests/test_nodes_pairs.py` to reflect updated EUR/USD threshold values
- [ ] Verify 2-year hourly backtest (`fx-backtest --pair EUR/USD --db-path data/backtest_signals_eurusd.db`) produces balanced BUY and SELL execution
- [ ] Verify 5-year daily backtest (`fx-backtest --pair EUR/USD --db-path data/backtest_signals_eurusd_5y.db`) generates validated reports
- [ ] Full regression test suite passing with 0 failures (`pytest tests/`)
- [ ] Update `docs/Multi-Pair Expansion Plan.md` with new calibration parameters and benchmark metrics
