# P8-07: CLI Entrypoints & Multi-Pair Parameterization

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** CLI & Integration
* **Last Updated (UTC):** `2026-11-19T12:08:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Expose multi-pair configuration knobs across all command-line interfaces:

1. Add `--pair` option (defaulting to `USD/JPY`) to:
   - `fx-collect` (`fx_signal_reader/cli/collect.py`)
   - `fx-pipeline` (`fx_signal_reader/cli/pipeline.py`)
   - `fx-schedule` (`fx_signal_reader/cli/schedule.py`)
   - `fx-backtest` (`fx_signal_reader/cli/backtest.py`)
2. Route commands automatically to the pair-specific database path (`data/{symbol_slug}_signals.db`) unless an explicit `--db-path` override is passed.
3. Support environment variable `FX_PAIR` as a default for `--pair`.
4. Update display telemetry and summaries to render the active currency pair name and appropriate price formatting.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/cli/collect.py`:**
   - Add `--pair` flag; instantiate collectors with the resolved pair profile.
2. **`fx_signal_reader/cli/pipeline.py`:**
   - Pass `pair` into `create_initial_state(..., pair=pair)`.
3. **`fx_signal_reader/cli/backtest.py`:**
   - Default DB path to `data/backtest_signals_{symbol_slug}.db` when `--backfill` is used.
4. **Acceptance Criteria:**
   - `fx-pipeline --pair EURUSD --run-once` executes against EUR/USD data.
   - `fx-collect --pair EURUSD --collector all` polls EUR/USD data sources.
   - Default behavior without `--pair` remains strictly USD/JPY.

- [x] `--pair` option added to `fx-collect`, `fx-pipeline`, `fx-schedule`, and `fx-backtest`
- [x] Automatic database path resolution per pair implemented
- [x] CLI help text and telemetry updated
- [x] Integration smoke tests passing
