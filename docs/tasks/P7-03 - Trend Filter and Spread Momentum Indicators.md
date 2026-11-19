# P7-03: Trend filter and spread momentum indicators

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `DONE`
* **Complexity:** Multi-Task Project (Part 1 of 2: Core & MCP Rolling Analytics)
* **Last Updated (UTC):** `2026-11-19T07:17:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Add rolling time-series indicator calculators in `fx_signal_reader/core/` and expose them via `fx_signal_reader/mcp/server.py`:
1. **Trend Anchor:** Rolling 50-period Exponential Moving Average (EMA-50) and 200-period Simple Moving Average (SMA-200) for `JPY=X` spot prices.
2. **Spread Momentum:** 5-day and 20-day rate of change (RoC) on US-Japan 10Y yield spreads to detect whether carry differentials are actively expanding or contracting.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/core/indicators.py` & `fx_signal_reader/core/__init__.py`:**
   - Implemented SMA-seeded `compute_ema_series`, `compute_latest_ema`, O(N) `compute_sma_series`, `compute_latest_sma`, `compute_rate_of_change`, `classify_market_trend` (`BULLISH` / `BEARISH` / `SIDEWAYS`), and `classify_spread_momentum` (`CONTRACTING` when 5d < `-0.10` or 20d < `-0.20`, `EXPANDING` when 5d > `+0.10` or 20d > `+0.20`, else `STABLE`).
2. **`fx_signal_reader/mcp/server.py`:**
   - Added `get_market_trend_summary(symbol='JPY=X', db_path=None, as_of_utc=None, ema_period=50, sma_period=200)` and registered read-only MCP tool wrapper `_tool_get_market_trend_summary`.
   - Extended `get_latest_yield_spread` to return `spread_5d_change`, `spread_20d_change`, and `momentum` with strict `as_of_utc` point-in-time filtering and `None` preservation on empty spread rows.
3. **Tests & Verification:**
   - Added `tests/test_indicators.py` verifying EMA/SMA/RoC math against manual benchmarks, validation guards, classification thresholds, and strict PIT isolation.
   - Updated MCP tool-discovery assertions in `tests/test_mcp_server.py`, `tests/test_mcp_multi_server.py`, and `tests/test_backtest_pit.py`.
   - Full suite verified: 179/179 tests passing.

- [x] Implementation complete
- [x] Mathematical unit tests passing (179/179 total suite)
- [x] Point-in-time isolation verified
