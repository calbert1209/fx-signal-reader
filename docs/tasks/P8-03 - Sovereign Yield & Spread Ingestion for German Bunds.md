# P8-03: Sovereign Yield & Spread Ingestion for German Bunds

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** Collector & Core Integration
* **Last Updated (UTC):** `2026-11-19T10:47:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Integrate European sovereign bond yield data ingestion for EUR/USD macro spread analysis:

1. Ingest daily German 10-Year Government Bund yield (DE10Y) via Deutsche Bundesbank / ECB public time series or daily CSV feeds.
2. Provide fallback ingestion using FRED series `IRLTLT01DEM156N` (via existing `FRED_API_KEY`) and Yahoo Finance European bond proxy tickers.
3. Compute the primary sovereign yield spread for EUR/USD: $\text{Spread}_{10Y} = \text{DE 10Y} - \text{US 10Y}$.
4. Generalize `spread_calculator.py` to calculate spread as $\text{Base Yield} - \text{Quote Yield}$, ensuring positive widening spread reflects base currency strength.
5. Save records into `yield_spreads` in `data/eurusd_signals.db`.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/collectors/market_ticks.py`:**
   - Parameterize `MarketTicksCollector` with `pair: str = "USD/JPY"`.
   - For `EUR/USD`, watchlist includes `EURUSD=X`, `^TNX` (US 10Y), `^STOXX50E` (Euro Stoxx 50), `^GSPC` (S&P 500), `^VIX`, and `GC=F`.
   - Add method `fetch_de_10y_bund()` with Bundesbank/ECB HTTP parsing and FRED fallback.
2. **`fx_signal_reader/core/spread_calculator.py`:**
   - Refactor `compute_yield_spread(base_10y, quote_10y)` to be pair-agnostic.
   - Maintain backward-compatible aliases for `us_10y` and `jp_10y`.
3. **Acceptance Criteria:**
   - Ingesting EUR/USD ticks records `EURUSD=X`, `DE10Y`, and `^TNX` in `market_ticks`.
   - Spread table records aligned daily spreads for EUR/USD.

- [x] German 10Y Bund yield ingestion implemented
- [x] Spread calculation generalized to Base Yield - Quote Yield
- [x] Multi-pair collector parameterization added
- [x] Unit tests written and passing
