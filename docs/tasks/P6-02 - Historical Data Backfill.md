# P6-02: Historical data backfill

* **Phase:** Phase 6: Historical Backtesting & Evaluation
* **Status:** `DONE`
* **Parallelism:** Wave 1 (Independent — can run in parallel with `P6-01` and `P6-03`)
* **Last Updated (UTC):** `2026-11-19T03:43:00Z`
* **Backtesting Plan:** [Backtesting Implementation Plan.md](https://drive.google.com/file/d/1WIkuIDYbEVfCaL7HK7IHO6w5WTnDpaBR/view)

---

## 1. Objective & Scope

Create a historical data ingestion module (`fx_signal_reader/backtest/backfill.py`) to populate a SQLite database (defaulting to `data/backtest_signals.db` for isolation from live collector data) with historical OHLCV bars, US Treasury yields, Japan MoF JGB 10Y yields, and aligned `yield_spreads`.

## 2. Implementation Notes & Acceptance Criteria

1. **`backfill_market_ticks(db_path, start_date, end_date, interval="1h", symbols=None)`:**
   - Downloads historical OHLCV bars for `MarketTicksCollector.TICKERS` (`JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F`) via `yfinance`.
   - Enforces Yahoo Finance interval limits gracefully (warns/clamps if `15m` > 60d or `1h` > 730d).
   - Normalizes timestamps to ISO-8601 UTC via `fx_signal_reader.core.normalizer` and batch-inserts into `market_ticks` (`INSERT OR REPLACE`).
2. **`backfill_jgb_yields(db_path, start_date, end_date)`:**
   - Fetches Japan MoF historical JGB CSV (`jgbcm.csv` and `data/jgbcm_all.csv` fallback when `start_date` predates the current month).
   - Parses Reiwa (`R`), Heisei (`H`), and `YYYY/M/D` dates into `JGB10Y` / `^JB10YR` ticks.
3. **`backfill_yield_spreads(db_path, start_date, end_date, fred_api_key=None)`:**
   - Fetches historical `DGS2` and `DGS10` from FRED when `FRED_API_KEY` is configured; otherwise falls back to daily `^TNX` closes in `market_ticks`.
   - Forward-fills business-day JGB 10Y yields to US trading dates, computes `spread_10y = compute_yield_spread(us_10y, jp_10y)`, and writes to `yield_spreads`.
4. **`backfill_historical_data(...)`:**
   - Orchestrates schema initialization (`schema.sql`) and all three backfill steps, returning a structured summary dict.

- [x] Implementation complete
- [x] Mocked unit tests for MoF CSV & FRED/yfinance alignment passing
- [x] Live backfill verified against `data/backtest_signals.db`

## 3. Execution Log & Findings

* **2026-11-19T03:43:00Z:** Implemented `fx_signal_reader/backtest/backfill.py` (`backfill_market_ticks`, `backfill_jgb_yields`, `backfill_yield_spreads`, `backfill_historical_data`) with UTC normalization, yfinance interval clamping, MoF Reiwa/Heisei/Showa/`YYYY/M/D` CSV parsing, FRED `DGS2`/`DGS10` + `^TNX` fallback alignment, and isolated `data/backtest_signals.db` default.
* **Verification:** `tests/test_backtest_backfill.py` (14/14 passing) and live backfill into `data/backtest_signals.db` (`2,733` `market_ticks`, `33` `yield_spreads`, live `data/usdjpy_signals.db` untouched).
