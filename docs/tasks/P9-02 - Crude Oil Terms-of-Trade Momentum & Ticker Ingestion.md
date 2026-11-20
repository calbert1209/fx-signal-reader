# P9-02: Crude Oil Terms-of-Trade Momentum & Ticker Ingestion

* **Phase:** Phase 9: Advanced USD/JPY Signals (Yield Curve, Terms-of-Trade & Intraday Fix)
* **Status:** `COMPLETED`
* **Complexity:** Single-Task Foundation
* **Last Updated (UTC):** `2026-11-20T13:35:00Z`
* **Implementation Plan:** [plan_p1_p2_p3_signals.md](file:///Users/albert/.gemini/antigravity/brain/df88ef12-66c7-48bc-a6ad-f98f47ed301e/plan_p1_p2_p3_signals.md)

---

## 1. Objective & Scope

Incorporate **WTI Crude Oil Futures (`CL=F`)** into market ticks collection and compute energy terms-of-trade momentum indicators to model Japan's energy import balance shocks:

1. **Watchlist Addition:** Add `"CL=F"` to `MarketTicksCollector.TICKERS` so that oil candles (15m intraday and 1d daily) are automatically ingested and persisted in `market_ticks`.
2. **Commodity Momentum Indicators:** In `fx_signal_reader/core/indicators.py`, implement `compute_commodity_momentum(prices, period=5)`:
   - Compute 5-day and 20-day percentage rate-of-change (RoC).
   - Evaluate price relative to 50-day EMA.
   - Classify symmetric terms-of-trade regime:
     - `SURGING` (5d RoC $> +3.0\%$ or price $>$ EMA-50): Negative terms-of-trade shock for Japan -> USD/JPY bullish tailwind.
     - `PLUNGING` (5d RoC $< -3.0\%$): Positive terms-of-trade improvement for Japan -> JPY supportive / USD/JPY headwind.
     - `STABLE`: Neutral terms of trade.

## 2. Implementation Notes & Acceptance Criteria

1. **Files Affected:**
   - `fx_signal_reader/collectors/market_ticks.py`
   - `fx_signal_reader/core/indicators.py`
   - `tests/test_indicators.py`
   - `tests/test_market_ticks_pairs.py`
2. **Acceptance Criteria:**
   - `CL=F` is fetched via yfinance and stored with standard OHLCV volume records in `market_ticks`.
   - `compute_commodity_momentum` handles insufficient data, edge cases (zeros, infinities), and computes correct rolling returns and classifications.
   - Unit tests verify indicator math, edge cases, and collector execution.

- [x] `CL=F` added to `MarketTicksCollector` watchlist
- [x] `compute_commodity_momentum` implemented in `indicators.py`
- [x] Symmetric terms-of-trade regime classification tested
- [x] Unit tests written and passing
