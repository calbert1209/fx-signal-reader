# P7-03: Trend filter and spread momentum indicators

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `TODO`
* **Complexity:** Multi-Task Project (Part 1 of 2: Core & MCP Rolling Analytics)
* **Last Updated (UTC):** `2026-11-19T05:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Add rolling time-series indicator calculators in `fx_signal_reader/core/` and expose them via `fx_signal_reader/mcp/server.py`:
1. **Trend Anchor:** Rolling 50-period Exponential Moving Average (EMA-50) and 200-period Simple Moving Average (SMA-200) for `JPY=X` spot prices.
2. **Spread Momentum:** 5-day and 20-day rate of change (RoC) and moving average on US-Japan 10Y yield spreads to detect whether carry differentials are actively expanding or contracting.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/core/indicators.py`:**
   - Implement vectorized/efficient rolling EMA, SMA, and RoC functions operating on chronological SQLite bar/spread sequences with `as_of_utc` point-in-time filtering.
2. **`fx_signal_reader/mcp/server.py`:**
   - Add `get_market_trend_summary(symbol='JPY=X', as_of_utc=None)` returning spot price, EMA-50, SMA-200, and trend classification (`BULLISH`, `BEARISH`, `SIDEWAYS`).
   - Extend `get_latest_yield_spread` to return `spread_5d_change`, `spread_20d_change`, and `momentum` (`EXPANDING`, `CONTRACTING`, `STABLE`).
3. **Tests & Verification:**
   - Unit tests covering EMA/SMA/RoC mathematical correctness against known pandas/numpy benchmarks.
   - Point-in-time isolation verified for all rolling indicator calculations.

- [ ] Implementation complete
- [ ] Mathematical unit tests passing
- [ ] Point-in-time isolation verified
