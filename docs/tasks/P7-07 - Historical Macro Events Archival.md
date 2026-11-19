# P7-07: Historical macro events archival

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `TODO`
* **Complexity:** Multi-Task Project (Part 1 of 2: Multi-Year Economic Calendar Backfill)
* **Last Updated (UTC):** `2026-11-19T05:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Build an archival ingestion script to populate multi-year historical economic calendar releases (US CPI, NFP, FOMC rate decisions, BoJ meetings) into `macro_events` so that the Macro Analyst's catalyst proximity risk penalty can be backtested over multi-year datasets (raising `macro_events_pct` from `0.0%`).

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/backtest/backfill_calendar.py`:**
   - Ingest historical Forex Factory weekly JSON archives or open-source high-impact FX calendar CSVs covering 2022–2026.
   - Filter to USD and JPY events with `impact in ('High', 'Medium')`.
   - Normalize timestamps to ISO-8601 UTC and compute deterministic `event_hash` matching `CalendarCollector` schema.
   - Insert into target SQLite database (`data/backtest_signals.db`) with deduplication.
2. **Backfill Integration:**
   - Wire `backfill_calendar` into `backfill_historical_data` in `fx_signal_reader/backtest/backfill.py`.
3. **Tests & Verification:**
   - Unit tests for date parsing and event hashing.
   - Backtest run showing non-zero `macro_events_pct` in data coverage diagnostics.

- [ ] Implementation complete
- [ ] Calendar backfill unit tests passing
- [ ] Backtest reports verified non-zero macro event coverage
