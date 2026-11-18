"""
Unit and integration tests for USD/JPY Production Tiered Scheduler.

Covers:
  1. Interval and configuration parsing from environment variables and CLI arguments.
  2. Tokyo Fix window detection (09:45–10:05 JST / 00:45–01:05 UTC) and exact 09:55 JST snapshot.
  3. London / NY overlap session detection (13:00–17:00 UTC) and session regime classification.
  4. Macro economic calendar scheduling (daily 00:05 UTC anchor vs configurable intervals).
  5. Dynamic expedited tick cadence in Tokyo Fix session.
  6. Dry-run execution mode and error containment.
  7. Graceful shutdown lifecycle.
  8. End-to-end --run-once execution against SQLite database.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from fx_signal_reader.core import DEFAULT_SCHEMA_PATH
from fx_signal_reader.cli.schedule import (
    JST,
    UTC,
    JobSchedule,
    SchedulerConfig,
    SignalScheduler,
    build_arg_parser,
    calculate_next_calendar_run,
    get_market_session_regime,
    is_exact_tokyo_fix,
    is_london_ny_overlap,
    is_tokyo_fix_window,
    normalize_to_utc,
    parse_interval,
)


class TestIntervalAndConfigParsing(unittest.TestCase):
    """Tests interval validation, environment parsing, and CLI flag precedence."""

    def test_parse_interval_valid(self):
        self.assertEqual(parse_interval(300, 60), 300)
        self.assertEqual(parse_interval("900", 60), 900)
        self.assertEqual(parse_interval("86400", 60), 86400)

    def test_parse_interval_invalid(self):
        self.assertEqual(parse_interval(None, 300), 300)
        self.assertEqual(parse_interval("", 300), 300)
        self.assertEqual(parse_interval("invalid", 300), 300)
        self.assertEqual(parse_interval(-10, 300), 300)
        self.assertEqual(parse_interval(0, 300), 300)

    def test_default_config(self):
        cfg = SchedulerConfig()
        self.assertEqual(cfg.news_interval, 300)
        self.assertEqual(cfg.ticks_interval, 900)
        self.assertEqual(cfg.calendar_interval, 86400)
        self.assertEqual(cfg.tokyo_fix_interval, 60)
        self.assertEqual(cfg.calendar_utc_hour, 0)
        self.assertEqual(cfg.calendar_utc_minute, 5)
        self.assertTrue(cfg.enable_tokyo_fix_expedited)
        self.assertFalse(cfg.dry_run)
        self.assertFalse(cfg.run_once)

    @patch.dict(os.environ, {
        "INTERVAL_NEWS": "120",
        "INTERVAL_TICKS": "600",
        "INTERVAL_CALENDAR": "14400",
        "INTERVAL_TOKYO_FIX": "30",
        "CALENDAR_TIME_UTC": "04:15",
        "FX_DB_PATH": "custom_fx.db",
    })
    def test_env_variable_overrides(self):
        cfg = SchedulerConfig.from_env_and_args()
        self.assertEqual(cfg.news_interval, 120)
        self.assertEqual(cfg.ticks_interval, 600)
        self.assertEqual(cfg.calendar_interval, 14400)
        self.assertEqual(cfg.tokyo_fix_interval, 30)
        self.assertEqual(cfg.calendar_utc_hour, 4)
        self.assertEqual(cfg.calendar_utc_minute, 15)
        self.assertEqual(cfg.db_path, Path("custom_fx.db"))

    @patch.dict(os.environ, {
        "INTERVAL_NEWS": "120",
        "INTERVAL_TICKS": "600",
    })
    def test_cli_overrides_env_variables(self):
        parser = build_arg_parser()
        args = parser.parse_args([
            "--interval-news", "45",
            "--interval-ticks", "180",
            "--db-path", "cli_test.db",
            "--run-once",
            "--dry-run",
            "--no-expedited-fix",
        ])
        cfg = SchedulerConfig.from_env_and_args(args)
        self.assertEqual(cfg.news_interval, 45)
        self.assertEqual(cfg.ticks_interval, 180)
        self.assertEqual(cfg.db_path, Path("cli_test.db"))
        self.assertTrue(cfg.run_once)
        self.assertTrue(cfg.dry_run)
        self.assertFalse(cfg.enable_tokyo_fix_expedited)


class TestTokyoFixWindowDetection(unittest.TestCase):
    """
    Tests Tokyo Fix window detection: 09:45–10:05 JST (00:45–01:05 UTC).
    Benchmark snapshot: 09:55 JST (00:55 UTC).
    """

    def test_tokyo_fix_window_boundary_utc(self):
        # 00:45:00 UTC -> 09:45:00 JST (Window Start)
        dt_start = datetime(2026, 11, 18, 0, 45, 0, tzinfo=UTC)
        self.assertTrue(is_tokyo_fix_window(dt_start))

        # 00:55:00 UTC -> 09:55:00 JST (Tokyo Fix Benchmark)
        dt_fix = datetime(2026, 11, 18, 0, 55, 0, tzinfo=UTC)
        self.assertTrue(is_tokyo_fix_window(dt_fix))

        # 01:05:00 UTC -> 10:05:00 JST (Window End)
        dt_end = datetime(2026, 11, 18, 1, 5, 0, tzinfo=UTC)
        self.assertTrue(is_tokyo_fix_window(dt_end))

    def test_outside_tokyo_fix_window_utc(self):
        # 00:44:59 UTC (1 second before window)
        dt_before = datetime(2026, 11, 18, 0, 44, 59, tzinfo=UTC)
        self.assertFalse(is_tokyo_fix_window(dt_before))

        # 01:05:01 UTC (1 second after window)
        dt_after = datetime(2026, 11, 18, 1, 5, 1, tzinfo=UTC)
        self.assertFalse(is_tokyo_fix_window(dt_after))

        # Midday London / NY (14:30 UTC)
        dt_noon = datetime(2026, 11, 18, 14, 30, 0, tzinfo=UTC)
        self.assertFalse(is_tokyo_fix_window(dt_noon))

    def test_tokyo_fix_window_jst_timezone(self):
        # Aware datetime in JST
        dt_jst_in = datetime(2026, 11, 18, 9, 50, 0, tzinfo=JST)
        self.assertTrue(is_tokyo_fix_window(dt_jst_in))

        dt_jst_out = datetime(2026, 11, 18, 10, 10, 0, tzinfo=JST)
        self.assertFalse(is_tokyo_fix_window(dt_jst_out))

    def test_naive_datetime_handling(self):
        # Naive datetime is treated as UTC
        dt_naive_fix = datetime(2026, 11, 18, 0, 50, 0)
        self.assertTrue(is_tokyo_fix_window(dt_naive_fix))

        dt_naive_out = datetime(2026, 11, 18, 8, 0, 0)
        self.assertFalse(is_tokyo_fix_window(dt_naive_out))

    def test_exact_tokyo_fix_snapshot(self):
        # Exact 09:55:00 JST (00:55:00 UTC)
        dt_exact = datetime(2026, 11, 18, 0, 55, 0, tzinfo=UTC)
        self.assertTrue(is_exact_tokyo_fix(dt_exact, tolerance_sec=30))

        # 00:55:25 UTC (Within 30s tolerance)
        dt_near = datetime(2026, 11, 18, 0, 55, 25, tzinfo=UTC)
        self.assertTrue(is_exact_tokyo_fix(dt_near, tolerance_sec=30))

        # 00:56:05 UTC (Outside 30s tolerance)
        dt_outside = datetime(2026, 11, 18, 0, 56, 5, tzinfo=UTC)
        self.assertFalse(is_exact_tokyo_fix(dt_outside, tolerance_sec=30))


class TestLondonNYOverlapDetection(unittest.TestCase):
    """
    Tests London / NY overlap session detection: 13:00–17:00 UTC.
    """

    def test_london_ny_overlap_boundaries(self):
        # 13:00:00 UTC (Start)
        dt_start = datetime(2026, 11, 18, 13, 0, 0, tzinfo=UTC)
        self.assertTrue(is_london_ny_overlap(dt_start))

        # 15:30:00 UTC (Midpoint peak)
        dt_mid = datetime(2026, 11, 18, 15, 30, 0, tzinfo=UTC)
        self.assertTrue(is_london_ny_overlap(dt_mid))

        # 17:00:00 UTC (End)
        dt_end = datetime(2026, 11, 18, 17, 0, 0, tzinfo=UTC)
        self.assertTrue(is_london_ny_overlap(dt_end))

    def test_outside_london_ny_overlap(self):
        # 12:59:59 UTC
        dt_before = datetime(2026, 11, 18, 12, 59, 59, tzinfo=UTC)
        self.assertFalse(is_london_ny_overlap(dt_before))

        # 17:00:01 UTC
        dt_after = datetime(2026, 11, 18, 17, 0, 1, tzinfo=UTC)
        self.assertFalse(is_london_ny_overlap(dt_after))

        # 03:00:00 UTC (Tokyo session)
        dt_tokyo = datetime(2026, 11, 18, 3, 0, 0, tzinfo=UTC)
        self.assertFalse(is_london_ny_overlap(dt_tokyo))


class TestSessionRegimeClassification(unittest.TestCase):
    """Tests session regime mapping and liquidity classification."""

    def test_tokyo_fix_regime(self):
        dt = datetime(2026, 11, 18, 0, 55, 0, tzinfo=UTC)  # Wednesday
        regime = get_market_session_regime(dt)
        self.assertTrue(regime["is_tokyo_fix_window"])
        self.assertTrue(regime["is_exact_tokyo_fix"])
        self.assertFalse(regime["is_london_ny_overlap"])
        self.assertTrue(regime["expedited_ticks"])
        self.assertEqual(regime["liquidity_level"], "HIGH")
        self.assertIn("Tokyo Fix Window", regime["regime_name"])

    def test_london_ny_overlap_regime(self):
        dt = datetime(2026, 11, 18, 14, 0, 0, tzinfo=UTC)  # Wednesday
        regime = get_market_session_regime(dt)
        self.assertFalse(regime["is_tokyo_fix_window"])
        self.assertTrue(regime["is_london_ny_overlap"])
        self.assertEqual(regime["liquidity_level"], "VERY_HIGH")
        self.assertIn("London/New York Overlap", regime["regime_name"])

    def test_weekend_regime(self):
        # Saturday 22:00 UTC
        dt_sat = datetime(2026, 11, 21, 22, 0, 0, tzinfo=UTC)
        regime = get_market_session_regime(dt_sat)
        self.assertTrue(regime["is_weekend"])
        self.assertEqual(regime["liquidity_level"], "LOW")
        self.assertFalse(regime["expedited_ticks"])


class TestCalendarScheduling(unittest.TestCase):
    """Tests daily 00:05 UTC anchor calculation and configurable interval scheduling."""

    def test_calendar_daily_anchor_before_target(self):
        # If current time is 00:00 UTC, target 00:05 is today
        now = datetime(2026, 11, 18, 0, 0, 0, tzinfo=UTC)
        next_run = calculate_next_calendar_run(now, target_hour=0, target_minute=5, interval_sec=86400)
        expected = datetime(2026, 11, 18, 0, 5, 0, tzinfo=UTC)
        self.assertEqual(next_run, expected)

    def test_calendar_daily_anchor_after_target(self):
        # If current time is 00:05 UTC (or 10:00 UTC), target is tomorrow at 00:05 UTC
        now = datetime(2026, 11, 18, 10, 0, 0, tzinfo=UTC)
        next_run = calculate_next_calendar_run(now, target_hour=0, target_minute=5, interval_sec=86400)
        expected = datetime(2026, 11, 19, 0, 5, 0, tzinfo=UTC)
        self.assertEqual(next_run, expected)

    def test_calendar_custom_interval(self):
        # 4-hour cycle (14400s)
        now = datetime(2026, 11, 18, 8, 0, 0, tzinfo=UTC)
        next_run = calculate_next_calendar_run(now, interval_sec=14400)
        expected = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
        self.assertEqual(next_run, expected)


class TestSchedulerDynamicsAndExpeditedTicks(unittest.TestCase):
    """Tests scheduler tick polling adaptations during Tokyo Fix."""

    def test_effective_ticks_interval_normal(self):
        cfg = SchedulerConfig(ticks_interval=900, tokyo_fix_interval=60)
        sched = SignalScheduler(cfg)

        # Standard afternoon UTC
        now_normal = datetime(2026, 11, 18, 14, 0, 0, tzinfo=UTC)
        self.assertEqual(sched.get_effective_ticks_interval(now_normal), 900)

    def test_effective_ticks_interval_tokyo_fix(self):
        cfg = SchedulerConfig(ticks_interval=900, tokyo_fix_interval=60, enable_tokyo_fix_expedited=True)
        sched = SignalScheduler(cfg)

        # Inside Tokyo Fix (00:50 UTC)
        now_fix = datetime(2026, 11, 18, 0, 50, 0, tzinfo=UTC)
        self.assertEqual(sched.get_effective_ticks_interval(now_fix), 60)

    def test_effective_ticks_interval_disabled_expedited(self):
        cfg = SchedulerConfig(ticks_interval=900, tokyo_fix_interval=60, enable_tokyo_fix_expedited=False)
        sched = SignalScheduler(cfg)

        now_fix = datetime(2026, 11, 18, 0, 50, 0, tzinfo=UTC)
        self.assertEqual(sched.get_effective_ticks_interval(now_fix), 900)


class TestSchedulerDryRunAndLifecycle(unittest.TestCase):
    """Tests dry-run execution, error resilience, and graceful termination."""

    def test_dry_run_collectors(self):
        cfg = SchedulerConfig(dry_run=True)
        sched = SignalScheduler(cfg)

        res_news = sched.run_collector("news")
        self.assertEqual(res_news["status"], "dry_run")
        self.assertEqual(sched.jobs["news"].last_status, "SUCCESS (DRY-RUN)")

        res_ticks = sched.run_collector("ticks")
        self.assertEqual(res_ticks["status"], "dry_run")
        self.assertEqual(sched.jobs["ticks"].last_status, "SUCCESS (DRY-RUN)")

        res_cal = sched.run_collector("calendar")
        self.assertEqual(res_cal["status"], "dry_run")
        self.assertEqual(sched.jobs["calendar"].last_status, "SUCCESS (DRY-RUN)")

    def test_dry_run_run_once(self):
        cfg = SchedulerConfig(dry_run=True, run_once=True)
        sched = SignalScheduler(cfg)
        res = sched.run_once()
        self.assertIn("news", res)
        self.assertIn("ticks", res)
        self.assertIn("calendar", res)
        self.assertEqual(res["news"]["status"], "dry_run")

    def test_collector_error_resilience(self):
        cfg = SchedulerConfig(dry_run=False)
        sched = SignalScheduler(cfg)

        # Mock NewsRSSCollector to throw an exception
        with patch("fx_signal_reader.cli.schedule.NewsRSSCollector") as mock_news:
            mock_inst = MagicMock()
            mock_inst.collect.side_effect = RuntimeError("Simulated transient network drop")
            mock_news.return_value = mock_inst

            res = sched.run_collector("news")
            self.assertEqual(res["status"], "FAILED")
            self.assertEqual(sched.jobs["news"].last_status, "FAILED")
            self.assertIn("Simulated transient network drop", res["error"])
            # Ensure next_run was still scheduled
            self.assertIsNotNone(sched.jobs["news"].next_run)

    def test_graceful_stop(self):
        sched = SignalScheduler(SchedulerConfig())
        self.assertTrue(sched.is_running)
        sched.stop()
        self.assertFalse(sched.is_running)

    def test_start_max_cycles_clean_exit(self):
        cfg = SchedulerConfig(dry_run=True)
        sched = SignalScheduler(cfg)
        exit_code = sched.start(max_cycles=1)
        self.assertEqual(exit_code, 0)


class TestSchedulerIntegrationAgainstDB(unittest.TestCase):
    """End-to-end integration tests verifying --run-once mode against SQLite DB."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_signals.db"
        self.schema_path = DEFAULT_SCHEMA_PATH

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_once_database_roundtrip(self):
        cfg = SchedulerConfig(
            db_path=self.db_path,
            schema_path=self.schema_path,
            dry_run=False,
            run_once=True,
            retries=2,
        )
        sched = SignalScheduler(cfg)

        # Check initial counts on fresh db
        init_counts = sched.get_table_counts()
        self.assertEqual(init_counts["market_ticks"], 0)
        self.assertEqual(init_counts["yield_spreads"], 0)
        self.assertEqual(init_counts["macro_events"], 0)
        self.assertEqual(init_counts["news_headlines"], 0)

        # Run cycle
        results = sched.run_once()
        self.assertIn("news", results)
        self.assertIn("ticks", results)
        self.assertIn("calendar", results)

        # Verify DB rows were persisted and tables created in WAL mode
        final_counts = sched.get_table_counts()
        self.assertGreater(final_counts["market_ticks"], 0)
        self.assertGreater(final_counts["yield_spreads"], 0)
        self.assertGreater(final_counts["macro_events"], 0)
        self.assertGreater(final_counts["news_headlines"], 0)

        # Confirm WAL journal mode
        conn = sqlite3.connect(self.db_path)
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        conn.close()
        self.assertEqual(mode.lower(), "wal")


if __name__ == "__main__":
    unittest.main()
