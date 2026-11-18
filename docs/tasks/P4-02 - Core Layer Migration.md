# P4-02: Core layer migration

* **Phase:** Phase 4: Package Reorganization & Cleanup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T11:39:50Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Migrate shared normalization utilities, yield-spread math, and the SQLite DDL schema into `fx_signal_reader/core/`, establishing canonical path defaults.

## 2. Implementation Notes & Acceptance Criteria

1. Move `normalizer.py` -> `fx_signal_reader/core/normalizer.py`.
2. Move `spread_calculator.py` -> `fx_signal_reader/core/spread_calculator.py` and update imports to `fx_signal_reader.core.normalizer`.
3. Move `schema.sql` -> `fx_signal_reader/core/schema.sql`.
4. Define canonical path constants (`PROJECT_ROOT`, `DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "usdjpy_signals.db"`, `DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"`) in `fx_signal_reader/core/__init__.py`.

- [x] Implementation complete (`normalizer.py`, `spread_calculator.py`, `schema.sql` moved to `fx_signal_reader/core/`)
- [x] Verified in local sandbox / environment (`fx_signal_reader.core.DEFAULT_DB_PATH` -> `data/usdjpy_signals.db`, `DEFAULT_SCHEMA_PATH.exists() == True`, `compute_yield_spread(5.1, 2.9) == 2.2`)
- [x] Linked artifacts and logs updated

## 3. Execution Log & Findings

- Migrated `normalizer.py`, `spread_calculator.py`, and `schema.sql` to `fx_signal_reader/core/` via `git mv`.
- Updated `spread_calculator.py` import to `fx_signal_reader.core.normalizer`.
- Defined `PROJECT_ROOT`, `DEFAULT_DB_PATH`, and `DEFAULT_SCHEMA_PATH` in `fx_signal_reader/core/__init__.py`.
