# P2-05: Tiered scheduling

* **Phase:** Phase 2: Collection Architecture & Local Persistence
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T09:07:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Implement a production-grade, tiered scheduling engine in `scheduler.py` for the USD/JPY signal reader pipeline. The runner manages distinct collection frequencies, dynamic session regime tracking (Tokyo Fix, London/New York overlap), graceful shutdown, and CLI interfaces for automated or one-off collection cycles.

### Key Capabilities Implemented:
1. **Tiered Polling Cadences:**
   - **News & Verbal Intervention RSS:** Every 5 minutes (`300s`, configurable via `--interval-news` / `INTERVAL_NEWS`).
   - **Market Ticks & Sovereign Yield Spreads:** Every 15 minutes (`900s`, configurable via `--interval-ticks` / `INTERVAL_TICKS`).
   - **Macro Economic Calendar (Forex Factory):** Daily anchor at 00:05 UTC (configurable via `--interval-calendar` / `INTERVAL_CALENDAR` or `--calendar-time-utc` / `CALENDAR_TIME_UTC`, supporting 4-hour cycles or custom intervals).
2. **Session Dynamics & Market Anchor Awareness:**
   - **Tokyo Fix Window (09:45–10:05 JST / 00:45–01:05 UTC):** Detects benchmark window with expedited tick polling (e.g., 60s cadence) and exact 09:55 JST (00:55 UTC) snapshot verification.
   - **London / New York Overlap (13:00–17:00 UTC):** Flags peak daily liquidity regime and volatility alerts.
   - **Session Regime Classifier:** Real-time state classification (`Tokyo Fix Window`, `London/NY Overlap`, `European Session`, `Asian/Tokyo Session`, `New York Afternoon`, `Weekend/Closed`) with liquidity ratings (`VERY_HIGH`, `HIGH`, `NORMAL`, `LOW`).
3. **Lifecycle & Resiliency:**
   - **Graceful Termination:** Registers POSIX `SIGINT` and `SIGTERM` signal handlers to finish active collector cycles before exiting cleanly.
   - **CLI Controls:** Full support for `--run-once` (executes single cycle of all collectors and exits cleanly), `--dry-run` (simulates collection with zero DB/network mutation), `--db-path`, `--schema-path`, `--retries`, and environment variable configuration with strict precedence.
   - **Telemetry Dashboard:** Live formatted console status table tracking active session regimes, liquidity levels, last-run and next-run UTC timestamps, intervals, and execution outcomes.

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete (`scheduler.py` and `tests/test_scheduler.py`)
- [x] Verified in local sandbox / environment
- [x] Linked artifacts and logs updated

### Acceptance Verification Summary:
- **Module Delivery:** `scheduler.py` written and verified as executable CLI runner and importable class library (`SignalScheduler`, `SchedulerConfig`, `JobSchedule`).
- **Test Suite Coverage:** Created `tests/test_scheduler.py` covering interval parsing, session boundary edge cases, Tokyo Fix window detection, London/NY overlap classification, calendar anchor calculations, dry-run simulations, graceful shutdown, and end-to-end database writes.
- **Test Results:** 27/27 tests in `tests/test_scheduler.py` passed cleanly (and 61/61 project-wide tests passing).
- **End-to-End Database Test:** Verified `python scheduler.py --run-once` against `usdjpy_signals.db`, confirming successful insertion into `market_ticks`, `yield_spreads`, `macro_events`, and `news_headlines`.

---

## 3. Execution Log & Findings

### 3.1 Production CLI Execution (`python scheduler.py --run-once`)
```text
2026-11-18 18:04:07 [INFO] [Scheduler] Executing --run-once mode at 2026-11-18T09:04:07Z

================================================================================
USD/JPY Signal Pipeline - Production Tiered Scheduler Telemetry
================================================================================
Current Time (UTC): 2026-11-18T09:04:07Z | JST: 2026-11-18T18:04:07+09:00
Active Regime     : European Session
Liquidity Level   : HIGH | Active Sessions: London/European
Tokyo Fix Window  : Inactive
London/NY Overlap : Inactive
Target Database   : /Users/albert/Projects/fx-signal-reader/usdjpy_signals.db
--------------------------------------------------------------------------------
Job Name     | Interval       | Last Run (UTC)       | Next Run (UTC)       | Status    
--------------------------------------------------------------------------------
news         | 300s (5m)      | Never                | 2026-11-18T09:04:07Z | PENDING   
ticks        | 900s (15m)     | Never                | 2026-11-18T09:04:07Z | PENDING   
calendar     | Daily 00:05    | Never                | 2026-11-18T09:04:07Z | PENDING   
================================================================================

>>> Running collector job: NEWS...
2026-11-18 18:04:07 [INFO] [Scheduler] Executing collector job 'news' at 2026-11-18T09:04:07Z
2026-11-18 18:04:07 [INFO] [NewsRSSCollector] Polling feed: Bank of Japan Releases (English) (boj_en_whatsnew)
2026-11-18 18:04:07 [INFO] [NewsRSSCollector] Polling feed: Bank of Japan Releases (Japanese) (boj_ja_whatsnew)
2026-11-18 18:04:07 [INFO] [NewsRSSCollector] Polling feed: FXStreet Breaking News (fxstreet_news)
2026-11-18 18:04:08 [INFO] [NewsRSSCollector] Polling feed: Investing.com Forex News (investing_forex)
2026-11-18 18:04:09 [INFO] [NewsRSSCollector] Polling feed: MarketWatch Real-Time MarketPulse (marketwatch_realtime)
2026-11-18 18:04:09 [INFO] [NewsRSSCollector] Polling feed: Nikkei Asia Primary Wire (nikkei_asia_nar)
2026-11-18 18:04:09 [INFO] [NewsRSSCollector] Polling feed: Google News USD/JPY & FX Wire (google_news_usdjpy)
2026-11-18 18:04:10 [INFO] [NewsRSSCollector] Polling feed: Google News MoF / BoJ Intervention Alert (google_news_mof_boj)
2026-11-18 18:04:10 [INFO] [NewsRSSCollector] Polling feed: Reuters Yen & FX Wire (via Google News) (google_news_reuters_yen)
2026-11-18 18:04:11 [INFO] [NewsRSSCollector] Polling feed: Ministry of Finance Official Releases (mof_whatsnew_html)
2026-11-18 18:04:12 [INFO] [NewsRSSCollector] News RSS collection complete: 469 processed, 462 total in DB, 151 intervention alerts flagged.
2026-11-18 18:04:12 [INFO] [Scheduler] Finished job 'news' in 5.33s [Status: SUCCESS]. Next run at 2026-11-18T09:09:12Z

>>> Running collector job: TICKS...
2026-11-18 18:04:12 [INFO] [Scheduler] Executing collector job 'ticks' at 2026-11-18T09:04:12Z
2026-11-18 18:04:14 [INFO] [MarketTicksCollector] Fetching JGB 10Y yield from MoF: https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv
2026-11-18 18:04:14 [INFO] [MarketTicksCollector] Successfully parsed MoF JGB 10Y: 2.925% (date: R8.11.17 -> 2026-11-17T00:00:00Z)
2026-11-18 18:04:14 [INFO] [MarketTicksCollector] Market ticks collection complete: 450 total ticks in DB, 10Y Spread: 2.178% (US: 5.103%, JP: 2.925%)
2026-11-18 18:04:14 [INFO] [Scheduler] Finished job 'ticks' in 1.71s [Status: SUCCESS]. Next run at 2026-11-18T09:19:14Z

>>> Running collector job: CALENDAR...
2026-11-18 18:04:14 [INFO] [Scheduler] Executing collector job 'calendar' at 2026-11-18T09:04:14Z
2026-11-18 18:04:14 [INFO] [CalendarCollector] Requesting live JSON feed: https://nfs.faireconomy.media/ff_calendar_thisweek.json
2026-11-18 18:04:14 [INFO] [CalendarCollector] Live JSON feed retrieved: 125 items
2026-11-18 18:04:14 [INFO] [CalendarCollector] Calendar collection complete: 53 USD/JPY events written (53 total in DB, source: live_json)
2026-11-18 18:04:14 [INFO] [Scheduler] Finished job 'calendar' in 0.14s [Status: SUCCESS]. Next run at 2026-11-19T00:05:00Z

================================================================================
Cycle Execution Summary (Completed in 7.19s)
================================================================================
Table Name             | Initial    | Final      | Net Change
--------------------------------------------------------------------------------
market_ticks           | 444        | 450        | +6        
yield_spreads          | 1          | 1          | 0         
macro_events           | 53         | 53         | 0         
news_headlines         | 459        | 462        | +3        
================================================================================
• Market Ticks: USD/JPY = 159.138, US 10Y = 5.103%, JGB 10Y = 2.925%, 10Y Spread = 2.178%
• Calendar    : 53 events via live_json
• News RSS    : 469 items parsed across 10 feeds, 151 intervention signals flagged
================================================================================
```

### 3.2 Automated Test Suite Execution (`python -m unittest tests/test_scheduler.py`)
```text
Ran 27 tests in 4.929s

OK

Test Breakdown:
- TestIntervalAndConfigParsing: 5 tests (defaults, environment variables, CLI override precedence, invalid input guards)
- TestTokyoFixWindowDetection: 5 tests (window boundaries 00:45–01:05 UTC, 1s outside guards, JST timezone handling, naive datetime normalization, exact 09:55 JST snapshot)
- TestLondonNYOverlapDetection: 2 tests (window boundaries 13:00–17:00 UTC, edge points, off-session rejection)
- TestSessionRegimeClassification: 3 tests (Tokyo Fix high volatility regime, London/NY peak liquidity, weekend interbank closed detection)
- TestCalendarScheduling: 3 tests (00:05 UTC anchor same day, next day rollover, custom 4-hour cycle)
- TestSchedulerDynamicsAndExpeditedTicks: 3 tests (normal 900s interval, expedited 60s during Tokyo Fix, expedited disabled override)
- TestSchedulerDryRunAndLifecycle: 5 tests (dry run collector dispatch, dry run cycle summary, collector exception isolation & resilience, graceful stop flag, loop max_cycles termination)
- TestSchedulerIntegrationAgainstDB: 1 test (fresh SQLite DB roundtrip, WAL mode verification, multi-table delta counts)
```

### 3.3 Next-Run Telemetry & Session Cadence Specifications

| Component | Default Interval | Tokyo Fix Interval | Target Anchor / Formula | Destination Table |
| :--- | :--- | :--- | :--- | :--- |
| **News & Verbal RSS** | 300s (5 min) | 300s (5 min) | `now + 300s` | `news_headlines` |
| **Market Ticks & Spreads** | 900s (15 min) | 60s (Expedited) | `now + (60s if in Tokyo Fix else 900s)` | `market_ticks`, `yield_spreads` |
| **Forex Factory Calendar** | Daily (86400s) | Daily (86400s) | Target 00:05 UTC daily | `macro_events` |
