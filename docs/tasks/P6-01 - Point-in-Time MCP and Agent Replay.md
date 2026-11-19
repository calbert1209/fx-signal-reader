# P6-01: Point-in-time MCP & agent replay

* **Phase:** Phase 6: Historical Backtesting & Evaluation
* **Status:** `DONE`
* **Parallelism:** Wave 1 (Independent — can run in parallel with `P6-02` and `P6-03`)
* **Last Updated (UTC):** `2026-11-19T03:43:00Z`
* **Backtesting Plan:** [Backtesting Implementation Plan.md](https://drive.google.com/file/d/1WIkuIDYbEVfCaL7HK7IHO6w5WTnDpaBR/view)

---

## 1. Objective & Scope

Add point-in-time (`as_of_utc`) filtering to the read-only SQLite MCP query layer and LangGraph agent workflow so historical replays read only market ticks, yield spreads, macro calendar events, and MoF/BoJ headlines available at or before the simulated timestamp—eliminating look-ahead bias while preserving 100% backwards compatibility for live runs.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/mcp/server.py`:**
   - `get_latest_yield_spread(db_path=None, as_of_utc=None)`: filter `yield_spreads` and `JPY=X` `market_ticks` by `timestamp_utc <= ?` when `as_of_utc` is set.
   - `get_upcoming_macro_events(currency=None, impact=None, limit=20, db_path=None, as_of_utc=None, past_hours=4, future_hours=48)`: when `as_of_utc` is set, filter events within `[as_of_utc - past_hours, as_of_utc + future_hours]`.
   - `get_recent_mof_alerts(min_tier=1, limit=20, db_path=None, as_of_utc=None, lookback_hours=None)`: filter `published_utc <= ?` (and `published_utc >= ?` when `lookback_hours` is provided).
   - `get_market_ticks_summary(symbols=None, db_path=None, as_of_utc=None)`: filter `timestamp_utc <= ?` before `ORDER BY timestamp_utc DESC LIMIT 2`.
2. **`fx_signal_reader/agent/nodes.py`:**
   - Add optional `point_in_time: bool = False` and `mof_lookback_hours: int | None = None` to `macro_analyst_node`, `risk_committee_node`, `make_macro_analyst_node`, and `make_risk_committee_node`.
   - When `point_in_time=True`, pass `as_of_utc=normalize_datetime_utc(state.get("timestamp_utc"), fallback_now=False)` into all MCP query calls.
3. **`fx_signal_reader/agent/graph.py`:**
   - Add optional `point_in_time: bool = False`, `mof_lookback_hours: int | None = None`, and `write_ledger: bool = True` to `build_signal_graph(...)`, `paper_execution_node(...)`, and `make_paper_execution_node(...)` so backtest runs do not append to live `data/paper_trades.jsonl`.

- [x] Implementation complete
- [x] Zero look-ahead bias verified
- [x] Existing live pipeline & test suite unaffected

## 3. Execution Log & Findings

* **2026-11-19T03:43:00Z:** Implemented `as_of_utc`, `past_hours`/`future_hours`, and `lookback_hours` filtering in `fx_signal_reader/mcp/server.py`, plus `point_in_time`, `mof_lookback_hours` (default 48h in replay), and `write_ledger` in `fx_signal_reader/agent/nodes.py` and `fx_signal_reader/agent/graph.py`.
* **Verification:** `tests/test_backtest_pit.py` (14/14 passing) and full regression suite (`166/166` passing).
