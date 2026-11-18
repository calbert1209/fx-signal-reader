# P4-06: Runtime data, scripts & docs cleanup

* **Phase:** Phase 4: Package Reorganization & Cleanup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T11:41:43Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Consolidate runtime SQLite storage into `data/`, remove stale Phase 1 database artifacts, and relocate Phase 1 validation scripts and JSON outputs out of the repository root.

## 2. Implementation Notes & Acceptance Criteria

1. Move `usdjpy_signals.db` (and any `-wal`/`-shm` sidecars) -> `data/usdjpy_signals.db`.
2. Delete stale `fx_signals.db` from the repository root.
3. Add `data/.gitkeep` to preserve the runtime data directory in git.
4. Move `validate_calendar.py`, `validate_intervention.py`, and `validate_yfinance.py` -> `scripts/validation/`, updating schema/output paths to reference `fx_signal_reader/core/schema.sql` and `docs/validation_results/`.
5. Move `calendar_validation_results.json`, `intervention_validation_results.json`, and `yfinance_validation_results.json` -> `docs/validation_results/`.

- [x] Implementation complete
- [x] Verified in local sandbox / environment
- [x] Linked artifacts and logs updated

## 3. Execution Log & Findings

* **`2026-11-18T11:41:43Z`**: Completed runtime data, scripts, and docs cleanup:
  * Moved `usdjpy_signals.db` to `data/usdjpy_signals.db` (no `-wal`/`-shm` sidecars were present).
  * Deleted stale `fx_signals.db` from the repository root.
  * Created `data/.gitkeep`.
  * Created `scripts/validation/` and moved `validate_calendar.py`, `validate_intervention.py`, and `validate_yfinance.py` into it, updating DB (`data/usdjpy_signals.db`), schema (`fx_signal_reader/core/schema.sql`), and output (`docs/validation_results/`) paths.
  * Created `docs/validation_results/` and moved `calendar_validation_results.json`, `intervention_validation_results.json`, and `yfinance_validation_results.json` into it.
  * Verified directory contents (`data/`, `scripts/validation/`, `docs/validation_results/`), `py_compile` on all 3 validation scripts, and SQLite read-only integrity check on `data/usdjpy_signals.db`: `integrity: ('ok',)`.
