# P1-02: Yahoo Finance ticker validation

* **Phase:** Phase 1: Data Source Mapping & API Setup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T07:45:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Verify availability, delay, and formatting for `JPY=X`, `^TNX`, `^JB10YR`, `^GSPC`, `^N225`, `^VIX`, and `GC=F`.

## 2. Implementation Notes & Acceptance Criteria

Test `yfinance` 15m intraday intervals and verify daily (`1d`) candle fallback behavior for `^JB10YR` (JGB 10Y yield).

- [x] Implementation complete (`validate_yfinance.py` executed in isolated `.venv` environment)
- [x] Verified availability, exchange delays, and candle formatting across all target tickers
- [x] Tested 15m intraday and 1d fallback behavior; investigated `^JB10YR` delisting and validated official MoF Japan alternative
- [x] Linked artifacts and logs updated

---

## 3. Execution Log & Findings

### Validation Summary Matrix (Tested `2026-11-18T07:44:38Z`)

| Symbol | Asset Name | Quote Type | Exchange | Delay | 15m Bars | 1d Bars | Latest Price / Level | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`JPY=X`** | USD / JPY | CURRENCY | CCY | Real-time (`0m`) | 127 bars | 5 bars | `159.242` | **ACTIVE** |
| **`^TNX`** | 10-Year US Treasury Yield | INDEX | CGI | Delayed (`15m`) | 54 bars | 5 bars | `5.103%` | **ACTIVE** |
| **`^JB10YR`** | Japan 10Y Govt Bond Yield | INDEX | None | N/A | 0 bars | 0 bars | N/A (HTTP 404) | **DELISTED** |
| **`^GSPC`** | S&P 500 Index | INDEX | SNP | Real-time (`0m`) | 52 bars | 5 bars | `7,340.53` | **ACTIVE** |
| **`^N225`** | Nikkei 225 Index | INDEX | OSA | Delayed (`20m`) | 46 bars | 5 bars | `59,165.13` | **ACTIVE** |
| **`^VIX`** | CBOE Volatility Index | INDEX | CXI | Delayed (`15m`) | 104 bars | 5 bars | `25.46` | **ACTIVE** |
| **`GC=F`** | COMEX Gold Futures | FUTURE | CMX | Delayed (`10m`) | 103 bars | 4 bars | `3,772.40` | **ACTIVE** |

---

### Detailed Findings & Analysis

1. **`JPY=X` (USD/JPY Spot):**
   * High-liquidity 15m bars returned cleanly (127 intervals over 2 days).
   * Quotes stream without exchange delay (`delayedBy=0`).
   * Exchange timezone reported as `Europe/London` (aligned with FX market convention).
   * Volume is `0.0` (standard for OTC spot FX feeds in Yahoo Finance).

2. **`^TNX` (US 10-Year Treasury Yield Index):**
   * Quoted in percentage terms directly (`5.103%`).
   * 15-minute delay on CBOE index ticks.
   * Matches official FRED benchmark (`DGS10 = 5.12%` on `2026-11-16`), confirming suitability as an intraday proxy for US 10Y yields.

3. **`^JB10YR` (Japan 10Y Government Bond Yield) & Fallback Analysis:**
   * **Result:** Yahoo Finance returns `HTTP Error 404: Quote not found for symbol: ^JB10YR` and `No data found, symbol may be delisted` on both 15m and 1d queries.
   * **Root Cause:** Yahoo Finance does not provide a native index ticker for the Japan 10-Year Government Bond yield.
   * **Alternative Solution Validated:** The **Japan Ministry of Finance (MoF)** publishes an official, unauthenticated daily benchmark yield curve CSV at:
     `https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv`
   * **MoF Verification:**
     * Successfully fetched and parsed the live MoF feed.
     * Latest benchmark 10Y yield on `2026-11-17` (`R8.11.17`): **`2.925%`** (prior: `2.927%` on `2026-11-16`).
     * **Calculated Live 10Y Spread (`US 10Y - JGB 10Y`):** `5.103% (^TNX) - 2.925% (MoF JGB 10Y) = 2.178%` (**+217.8 bps**).
     * **Architectural Decision:** Implement MoF CSV ingestion in `collector.py` as the canonical source for JGB 10Y yields to ensure robust, resilient spread calculations without broken ticker dependencies.

4. **Equity & Risk Sentiment Assets (`^GSPC`, `^N225`, `^VIX`, `GC=F`):**
   * `^GSPC` and `^N225` return full 15m intraday OHLC bars.
   * `^VIX` provides 15m intervals with standard 15-minute CBOE delay.
   * `GC=F` (Gold futures) provides active 15m contracts with 10-minute COMEX delay and reported volume.

---

### Verification Script

Local verification script located at [`validate_yfinance.py`](file:///Users/albert/Projects/fx-signal-reader/validate_yfinance.py), with structured outputs saved to [`yfinance_validation_results.json`](file:///Users/albert/Projects/fx-signal-reader/yfinance_validation_results.json).
