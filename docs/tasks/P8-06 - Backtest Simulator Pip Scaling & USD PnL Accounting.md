# P8-06: Backtest Simulator Pip Scaling & USD PnL Accounting

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** Backtest Engine Generalization
* **Last Updated (UTC):** `2026-11-19T10:47:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Generalize the bracket simulator and historical backfill engine to support multi-pair backtesting:

1. In `simulate_trade_resolution`:
   - Scale spread and slippage deductions by the pair's pip size (`0.0001` for EUR/USD vs `0.01` for USD/JPY).
   - Calculate pip PnL using the pair's pip scale: $\text{pnl\_pips} = \frac{\text{price\_delta}}{\text{pip\_size}}$.
   - Calculate dollar PnL according to whether the quote currency is USD:
     - Quote is USD (EUR/USD): $\text{pnl\_usd} = \text{price\_delta} \times \text{units}$.
     - Base is USD (USD/JPY): $\text{pnl\_usd} = \frac{\text{price\_delta} \times \text{units}}{\text{exit\_price}}$.
2. In `BacktestConfig`:
   - Add `pair: str = "USD/JPY"` field.
   - Adjust default SL/TP when pair is EUR/USD (default 50 pips SL / 100 pips TP).
3. In `fx_signal_reader/backtest/backfill.py`:
   - Support backfilling historical `EURUSD=X` daily/hourly OHLCV from Yahoo Finance.
   - Support backfilling German 10Y Bund yields into `data/backtest_signals_eurusd.db`.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/backtest/models.py`:**
   - Add `pair: str = Field(default="USD/JPY")` to `BacktestConfig` and `SimulatedTrade`.
2. **`fx_signal_reader/backtest/simulator.py`:**
   - Resolve `profile = get_pair_profile(proposal.pair)`.
   - Compute `spread_cost = config.spread_pips * profile.pip_size`.
   - Compute `pnl_usd` using quote-currency logic.
3. **`fx_signal_reader/backtest/backfill.py`:**
   - Parameterize backfill with `pair: str = "USD/JPY"`.
4. **Acceptance Criteria:**
   - Simulating an EUR/USD trade with 50-pip SL hit deducts exactly target dollar risk ($100).
   - Replay loop produces valid `BacktestReport` for EUR/USD.

- [x] Pip scaling and USD PnL logic updated in `simulate_trade_resolution`
- [x] `BacktestConfig` and `SimulatedTrade` parameterized with `pair`
- [x] Historical backfill updated for `EURUSD=X` and German Bund yields
- [x] Unit tests written and passing
