# P9-01: 2Y Sovereign Yield Spread & Schema Migration

* **Phase:** Phase 9: Advanced USD/JPY Signals (Yield Curve, Terms-of-Trade & Intraday Fix)
* **Status:** `PENDING`
* **Complexity:** Single-Task Foundation
* **Last Updated (UTC):** `2026-11-20T13:35:00Z`
* **Implementation Plan:** [plan_p1_p2_p3_signals.md](file:///Users/albert/.gemini/antigravity/brain/df88ef12-66c7-48bc-a6ad-f98f47ed301e/plan_p1_p2_p3_signals.md)

---

## 1. Objective & Scope

Integrate the **2-Year Sovereign Yield Spread** (US 2Y Treasury minus Japan 2Y JGB) to capture front-end central bank policy expectations (Fed rate paths vs. BoJ rate hike expectations):

1. **Database Schema Evolution:** Update `yield_spreads` schema in `schema.sql` to include `jp_2y REAL` and `spread_2y REAL`. Implement safe, idempotent `ALTER TABLE` checks in `collectors/base.py` (`init_db`) so existing databases (`usdjpy_signals.db`, backtest databases) upgrade cleanly.
2. **MoF JGB 2Y Parsing:** Update `spread_calculator.parse_mof_csv` to support multi-tenor extraction (both `10年` and `2年`) from Japan Ministry of Finance `jgbcm.csv`.
3. **US 2Y Yield Ingestion & Fallback:** Ingest official US 2Y Treasury yield via FRED `DGS2` when configured, and automatically fall back to Yahoo Finance `2YY=F` when FRED is unconfigured or returns None.
4. **Spread Calculation:** Implement `compute_2y_yield_spread(us_2y, jp_2y)` and update `YieldSpreadRecord` in `core/spread_calculator.py`.

## 2. Implementation Notes & Acceptance Criteria

1. **Files Affected:**
   - `fx_signal_reader/core/schema.sql`
   - `fx_signal_reader/collectors/base.py`
   - `fx_signal_reader/core/spread_calculator.py`
   - `fx_signal_reader/collectors/market_ticks.py`
   - `tests/test_spread_calculator.py`
   - `tests/test_market_ticks_pairs.py`
2. **Acceptance Criteria:**
   - Existing databases automatically acquire `jp_2y` and `spread_2y` columns without schema corruption or index drops.
   - `parse_mof_csv` successfully extracts Reiwa-era dates and both `2年` and `10年` yields.
   - `compute_2y_yield_spread` computes accurate rounded spreads in percentage points and bps.
   - `MarketTicksCollector.collect()` writes `jp_2y`, `us_2y`, and `spread_2y` into `yield_spreads`.
   - Comprehensive unit tests verify calculation, migration, and storage.

- [ ] `yield_spreads` schema updated and safe migration implemented
- [ ] MoF `jgbcm.csv` 2Y parser and Yahoo `2YY=F` fallback implemented
- [ ] 2Y yield spread calculation and dataclass updated
- [ ] Unit tests written and passing
