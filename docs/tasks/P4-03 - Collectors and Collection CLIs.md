# P4-03: Collectors & collection CLIs

* **Phase:** Phase 4: Package Reorganization & Cleanup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T11:42:40Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Migrate the modular data collectors (`base.py`, `calendar.py`, `market_ticks.py`, `news_rss.py`) and collection/scheduling CLI scripts (`collector.py`, `scheduler.py`) into `fx_signal_reader/collectors/` and `fx_signal_reader/cli/`.

## 2. Implementation Notes & Acceptance Criteria

1. Move `collectors/*` -> `fx_signal_reader/collectors/*` and update internal imports to `fx_signal_reader.collectors.*` and `fx_signal_reader.core.*`.
2. Update default `db_path` and `schema_path` in `BaseCollector`, `MarketTicksCollector`, and `CalendarCollector` to reference `DEFAULT_DB_PATH` (`data/usdjpy_signals.db`) and `DEFAULT_SCHEMA_PATH` (`fx_signal_reader/core/schema.sql`).
3. Move `collector.py` -> `fx_signal_reader/cli/collect.py` (`fx-collect` entrypoint).
4. Move `scheduler.py` -> `fx_signal_reader/cli/schedule.py` (`fx-schedule` entrypoint) and verify `--dry-run` and `--run-once` flags.

- [x] Implementation complete
- [x] Verified in local sandbox / environment
- [x] Linked artifacts and logs updated

## 3. Execution Log & Findings

- **2026-11-18T11:42:40Z**:
  - Moved `collectors/__init__.py`, `base.py`, `calendar.py`, `market_ticks.py`, `news_rss.py` into `fx_signal_reader/collectors/` and removed the empty root `collectors/` directory.
  - Moved `collector.py` -> `fx_signal_reader/cli/collect.py` and `scheduler.py` -> `fx_signal_reader/cli/schedule.py`.
  - Updated all internal imports to `fx_signal_reader.collectors.*` and `fx_signal_reader.core.*` (`DEFAULT_DB_PATH`, `DEFAULT_SCHEMA_PATH`, `PROJECT_ROOT`), including `CalendarCollector.DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "raw_calendar_thisweek.json"`.
  - Verified `.venv/bin/fx-collect --help` (exit 0, defaults pointing to `data/usdjpy_signals.db` and `fx_signal_reader/core/schema.sql`).
  - Verified `.venv/bin/fx-schedule --dry-run` (started cleanly, scheduled `news`, `ticks`, `calendar` jobs against `data/usdjpy_signals.db`) and `.venv/bin/fx-schedule --dry-run --run-once` (exit 0, completed 1 dry-run cycle in 0.18s).
