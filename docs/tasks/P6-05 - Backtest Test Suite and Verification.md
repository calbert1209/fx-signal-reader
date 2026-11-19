# P6-05: Backtest test suite & verification

* **Phase:** Phase 6: Historical Backtesting & Evaluation
* **Status:** `DONE`
* **Parallelism:** Wave 2 (Depends on `P6-01`–`P6-04`)
* **Last Updated (UTC):** `2026-11-19T04:14:00Z`
* **Backtesting Plan:** [Backtesting Implementation Plan.md](https://drive.google.com/file/d/1WIkuIDYbEVfCaL7HK7IHO6w5WTnDpaBR/view)

---

## 1. Objective & Scope

Create a comprehensive automated test suite (`tests/test_backtest.py`) covering point-in-time isolation, bracket simulation, concurrency rules, end-to-end synthetic replay, mocked backfill parsing, and CLI execution, and verify zero regressions across the entire project test suite.

## 2. Implementation Notes & Acceptance Criteria

1. **`tests/test_backtest.py`:**
   - Point-in-time isolation: verify `mcp/server.py` with `as_of_utc=T` never returns future ticks, spreads, events, or headlines.
   - Bracket trade resolution: test `BUY`/`SELL` SL & TP hits, conservative vs optimistic same-bar collision, `TIME_STOP`, `END_OF_DATA`, and spread/PnL math.
   - Concurrency: verify `allow_overlapping_trades=False` skips overlapping signals and increments `trades_skipped_overlap`.
   - End-to-end synthetic replay: multi-day DB with shifting yield spreads (`BULLISH_USD` → `NEUTRAL` → `BEARISH_USD`), VIX regime transitions, and a Tier-4 MoF headline (vetoed within 48h, cleared after 48h).
   - Backfill parsers: mocked MoF CSV (Reiwa/Heisei dates) and FRED/yfinance spread alignment tests.
   - CLI tests: `fx_signal_reader.cli.backtest.main()` with `--json` and `--export-trades`.
2. **Full Regression & Manual Run:**
   - `python3 -m unittest discover -s tests -v` passes 100%.
   - Manual `fx-backtest --backfill --interval 1h` run verified against `data/backtest_signals.db`.

- [x] Implementation complete
- [x] `tests/test_backtest.py` passing (`6/6`)
- [x] Full regression suite passing (`python3 -m unittest discover -s tests -v`: `172/172`)

## 3. Execution Log & Findings

* **2026-11-19T04:14:00Z:** Created `tests/test_backtest.py` covering end-to-end synthetic multi-day replay with 48h MoF decay, concurrency overlap rules, empty DB handling, and `fx-backtest` CLI (`--json`, `--export-trades` CSV/JSONL, `--backfill`).
* **Verification:** `.venv/bin/python -m unittest discover -s tests -v` ran **172 tests** (`118` existing + `48` Wave 1 + `6` Wave 2) in `38.572s` — **OK**.
