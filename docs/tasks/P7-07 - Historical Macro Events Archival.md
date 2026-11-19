# P7-07: Historical macro events archival

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `DONE`
* **Complexity:** Multi-Task Project (Part 1 of 2: Multi-Year Economic Calendar Backfill)
* **Last Updated (UTC):** `2026-11-19T08:24:00Z`
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

- [x] Implementation complete
- [x] Calendar backfill unit tests passing
- [x] Backtest reports verified non-zero macro event coverage

## Implementation & Verification Summary

- **Modules & Exports:**
  - `fx_signal_reader/backtest/backfill_calendar.py`: implemented `generate_historical_macro_events`, `parse_calendar_archive_file` (JSON & CSV archive ingestion), and `backfill_calendar` (filters to `USD`/`JPY` and `High`/`Medium` impact using deterministic `CalendarCollector.generate_event_hash` IDs).
  - `fx_signal_reader/backtest/backfill.py` & `fx_signal_reader/backtest/__init__.py`: wired `backfill_calendar` into `backfill_historical_data` (returning `"macro_events"`) and exported `backfill_calendar`.
- **Database Population (`data/backtest_signals.db`, `2022-01-01` -> `2026-11-19`):**
  - Inserted `509` historical macro event rows (`393` USD, `116` JPY; `431` High impact, `78` Medium impact).
- **Verification:**
  - `.venv/bin/python -m unittest discover -s tests -v`: `Ran 187 tests in 40.820s` -> `OK` (including `tests/test_backtest_crisis.py` and `tests/test_backtest_backfill.py`).
  - 2-year historical backtest (`.venv/bin/fx-backtest --db-path data/backtest_signals.db --start 2024-11-19 --end 2026-11-19 --spread-pips 1.5`):
    - `Macro Events=56.95%` coverage (`JPY Bars=12241 | Yield Spread=100.0% | VIX=99.94% | MoF Alerts=5.73%`).
    - Portfolio (`114` trades, `46.49%` win rate): `Net PnL: $1,768.49 (+17.68%)`, `Profit Factor=1.2978`, `Sharpe=0.8228`, `Max Drawdown=$986.68 (9.87%)`.
