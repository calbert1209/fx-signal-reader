# P6-04: Backtest CLI & package integration

* **Phase:** Phase 6: Historical Backtesting & Evaluation
* **Status:** `DONE`
* **Parallelism:** Wave 2 (Depends on `P6-01`, `P6-02`, and `P6-03`)
* **Last Updated (UTC):** `2026-11-19T04:14:00Z`
* **Backtesting Plan:** [Backtesting Implementation Plan.md](https://drive.google.com/file/d/1WIkuIDYbEVfCaL7HK7IHO6w5WTnDpaBR/view)

---

## 1. Objective & Scope

Wire the point-in-time LangGraph workflow (`P6-01`), historical backfill (`P6-02`), and bracket simulator (`P6-03`) into `run_backtest(...)`, expose `fx_signal_reader/backtest/__init__.py`, and build the `fx-backtest` CLI (`fx_signal_reader/cli/backtest.py` + `pyproject.toml`).

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/backtest/simulator.py` (`run_backtest`):**
   - Loads `JPY=X` OHLC timeline from SQLite, samples evaluation timestamps at `step_hours` cadence, compiles `build_signal_graph(..., point_in_time=True, mof_lookback_hours=config.mof_lookback_hours, interrupt_on_actionable=False, write_ledger=False)` once, enforces `allow_overlapping_trades=False` concurrency, resolves trades, and returns `BacktestReport`.
   - Tracks `data_coverage` diagnostics (% of steps with yield spread, VIX, calendar, and news coverage).
2. **`fx_signal_reader/backtest/__init__.py`:**
   - Export `BacktestConfig`, `SimulatedTrade`, `BacktestMetrics`, `BacktestReport`, `run_backtest`, `simulate_trade_resolution`, `compute_backtest_metrics`, and `backfill_historical_data`.
3. **`fx_signal_reader/cli/backtest.py` & `pyproject.toml`:**
   - Implement `fx-backtest` supporting `--db-path` (defaulting to `data/backtest_signals.db` when `--backfill` is passed, else `FX_DB_PATH` / `data/usdjpy_signals.db`), `--backfill`, `--interval`, `--start`, `--end`, `--step-hours`, `--max-hold-hours`, `--spread-pips`, `--allow-overlap`, `--mof-lookback-hours`, `--json`, and `--export-trades` (`.csv` or `.jsonl`).
   - Register `fx-backtest = "fx_signal_reader.cli.backtest:main"` in `pyproject.toml`.

- [x] Implementation complete
- [x] `pip install -e . --no-deps` and `fx-backtest --help` verified

## 3. Execution Log & Findings

* **2026-11-19T04:14:00Z:** Implemented `run_backtest(...)` in `fx_signal_reader/backtest/simulator.py`, exported public API in `fx_signal_reader/backtest/__init__.py`, created `fx_signal_reader/cli/backtest.py` (`fx-backtest`), and registered `fx-backtest` in `pyproject.toml`.
* **Verification:** `pip install -e . --no-deps` & `.venv/bin/fx-backtest --help` exited `0`; live run `.venv/bin/fx-backtest --db-path data/backtest_signals.db --step-hours 4` evaluated 197 steps and executed 18 trades (`66.67%` win rate).
