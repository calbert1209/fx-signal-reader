"""
End-to-End Integration Tests for Historical Backtest Replay & `fx-backtest` CLI.
Task P6-05: Backtest Test Suite & Verification
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fx_signal_reader.backtest import (
    DEFAULT_BACKTEST_DB_PATH,
    BacktestConfig,
    BacktestReport,
    ExitReason,
    run_backtest,
)
from fx_signal_reader.cli import backtest as backtest_cli
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH


def _build_replay_db(db_path: Path) -> Path:
    """Create a multi-day synthetic SQLite DB with shifting spreads, VIX, and MoF headlines."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))

        # Yield spreads:
        # Day 1 (Nov 01): spread=2.50 -> BULLISH_USD
        # Day 4 (Nov 04): spread=1.50 -> NEUTRAL
        # Day 6 (Nov 06): spread=0.50 -> BEARISH_USD
        spreads = [
            ("2026-11-01T00:00:00Z", 4.60, 4.80, 2.30, 2.50, "fixture"),
            ("2026-11-04T00:00:00Z", 3.60, 3.80, 2.30, 1.50, "fixture"),
            ("2026-11-06T00:00:00Z", 2.60, 2.80, 2.30, 0.50, "fixture"),
        ]
        conn.executemany(
            """
            INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            spreads,
        )

        # Tier-4 MoF headline at Nov 01 03:00Z (vetoes Nov 01 04:00Z & Nov 02 00:00Z inside 48h lookback, clears by Nov 06)
        conn.execute(
            """
            INSERT INTO news_headlines (url_hash, published_utc, source, title, url, matched_keywords)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "mof-tier4-nov01",
                "2026-11-01T03:00:00Z",
                "Reuters",
                "Japan MoF ready to take decisive action against excessive yen moves",
                "https://example.com/mof-t4",
                "MoF,decisive action",
            ),
        )

        # 4-hour JPY=X bars from Nov 01 00:00Z through Nov 08 00:00Z
        # - Nov 01 00:00Z: entry ~150.00 (BUY, TP=151.50, SL=149.25)
        # - Nov 01 04:00Z: high=151.70 -> TP hit!
        # - Nov 02 00:00Z: Tier-4 MoF veto -> HOLD
        # - Nov 04 00:00Z: NEUTRAL spread -> HOLD
        # - Nov 06 00:00Z: MoF headline >48h old, spread=0.50 (BEARISH_USD), VIX=21.0 (TRANSITIONAL -> equal-dollar sizing)
        #   entry ~151.50 (SELL, TP=150.00, SL=152.25)
        # - Nov 06 08:00Z: low=149.80 -> TP hit!
        jpy_bars = [
            ("2026-11-01T00:00:00Z", 150.00, 149.90, 150.10, 149.85),
            ("2026-11-01T04:00:00Z", 151.60, 150.05, 151.70, 150.00),
            ("2026-11-02T00:00:00Z", 151.40, 151.50, 151.55, 151.30),
            ("2026-11-04T00:00:00Z", 151.20, 151.30, 151.35, 151.10),
            ("2026-11-06T00:00:00Z", 151.50, 151.40, 151.55, 151.35),
            ("2026-11-06T04:00:00Z", 150.80, 151.45, 151.50, 150.70),
            ("2026-11-06T08:00:00Z", 150.10, 150.80, 150.85, 149.80),
        ]
        ticks: list[tuple[str, str, float, float, float, float, float]] = []
        for ts, close, op, hi, lo in jpy_bars:
            ticks.append(("JPY=X", ts, close, op, hi, lo, 0.0))
            vix = 21.0 if ts >= "2026-11-06T00:00:00Z" else 16.0
            ticks.append(("^VIX", ts, vix, vix, vix + 0.1, vix - 0.1, 0.0))
            ticks.append(("^GSPC", ts, 7000.0, 6990.0, 7010.0, 6985.0, 1000.0))
            ticks.append(("^N225", ts, 50000.0, 49900.0, 50100.0, 49850.0, 0.0))
            ticks.append(("GC=F", ts, 3500.0, 3495.0, 3505.0, 3490.0, 100.0))
            ticks.append(("^TNX", ts, 4.5, 4.5, 4.51, 4.49, 0.0))

        conn.executemany(
            """
            INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ticks,
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


class TestRunBacktestIntegration(unittest.TestCase):
    """End-to-end integration tests for `run_backtest`."""

    def test_synthetic_multi_day_replay_and_mof_decay(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _build_replay_db(Path(tmpdir) / "replay.db")
            ledger_probe = Path(tmpdir) / "paper_trades.jsonl"

            with patch("fx_signal_reader.agent.graph.DEFAULT_LEDGER_PATH", ledger_probe):
                report = run_backtest(
                    db_path=db_path,
                    config=BacktestConfig(step_hours=4, spread_pips=1.5, mof_lookback_hours=48),
                )

            self.assertFalse(ledger_probe.exists(), "Backtest must never write to paper_trades.jsonl.")
            self.assertEqual(report.metrics.total_steps, 6)
            self.assertEqual(report.metrics.trades_executed, 2)
            self.assertGreaterEqual(report.metrics.total_vetoes, 1)
            self.assertEqual(report.metrics.wins, 2)
            self.assertEqual(report.metrics.losses, 0)
            self.assertEqual(report.metrics.win_rate_pct, 100.0)
            self.assertGreater(report.metrics.net_pnl_usd, 0.0)
            self.assertEqual(report.trades[0].action.value, "BUY")
            self.assertEqual(report.trades[0].position_size_units, 20000)
            self.assertEqual(report.trades[0].exit_reason, ExitReason.TAKE_PROFIT)
            self.assertEqual(report.trades[1].action.value, "SELL")
            self.assertEqual(report.trades[1].position_size_units, 20200)
            self.assertEqual(report.trades[1].exit_reason, ExitReason.TAKE_PROFIT)
            self.assertEqual(report.metrics.data_coverage["yield_spread_pct"], 100.0)
            self.assertEqual(report.metrics.data_coverage["vix_pct"], 100.0)

    def test_concurrency_overlap_skip_vs_allow_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "overlap.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
                conn.execute(
                    """
                    INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("2026-11-01T00:00:00Z", 4.6, 4.8, 2.3, 2.5, "fixture"),
                )
                # 3 bars 4h apart that stay flat (neither SL nor TP hit until bar 3)
                bars = [
                    ("2026-11-01T00:00:00Z", 150.00, 150.00, 150.20, 149.80),
                    ("2026-11-01T04:00:00Z", 150.10, 150.00, 150.25, 149.90),
                    ("2026-11-01T08:00:00Z", 151.60, 150.10, 151.70, 150.05),
                ]
                ticks = []
                for ts, c, o, h, l in bars:
                    ticks.append(("JPY=X", ts, c, o, h, l, 0.0))
                    ticks.append(("^VIX", ts, 16.0, 16.0, 16.1, 15.9, 0.0))
                conn.executemany(
                    "INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ticks,
                )
                conn.commit()
            finally:
                conn.close()

            no_overlap = run_backtest(
                db_path=db_path,
                config=BacktestConfig(step_hours=4, allow_overlapping_trades=False),
            )
            self.assertEqual(no_overlap.metrics.total_signals_actionable, 2)
            self.assertEqual(no_overlap.metrics.trades_executed, 1)
            self.assertEqual(no_overlap.metrics.trades_skipped_overlap, 1)

            with_overlap = run_backtest(
                db_path=db_path,
                config=BacktestConfig(step_hours=4, allow_overlapping_trades=True),
            )
            self.assertEqual(with_overlap.metrics.total_signals_actionable, 2)
            self.assertEqual(with_overlap.metrics.trades_executed, 2)
            self.assertEqual(with_overlap.metrics.trades_skipped_overlap, 0)

    def test_empty_db_returns_clean_zero_step_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
                conn.commit()
            finally:
                conn.close()

            report = run_backtest(db_path=db_path, config=BacktestConfig())
            self.assertEqual(report.metrics.total_steps, 0)
            self.assertEqual(report.metrics.trades_executed, 0)
            self.assertEqual(report.trades, [])
            self.assertEqual(report.metrics.data_coverage["jpy_bars"], 0)


class TestBacktestCLI(unittest.TestCase):
    """Tests for `fx_signal_reader.cli.backtest` (`fx-backtest`)."""

    def test_resolve_target_db_isolates_backfill_by_default(self) -> None:
        self.assertEqual(
            backtest_cli._resolve_target_db(None, backfill=True),
            DEFAULT_BACKTEST_DB_PATH.resolve(),
        )
        explicit = Path("/tmp/custom_bt.db")
        self.assertEqual(
            backtest_cli._resolve_target_db(str(explicit), backfill=True),
            explicit.resolve(),
        )

    def test_cli_json_and_export_csv_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = _build_replay_db(tmp / "cli.db")
            csv_path = tmp / "trades.csv"
            jsonl_path = tmp / "trades.jsonl"

            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                rc = backtest_cli.main(
                    [
                        "--db-path",
                        str(db_path),
                        "--step-hours",
                        "4",
                        "--json",
                        "--export-trades",
                        str(csv_path),
                    ]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(stdout_buf.getvalue())
            validated = BacktestReport.model_validate(payload)
            self.assertEqual(validated.metrics.trades_executed, 2)

            with csv_path.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["action"], "BUY")

            stdout_buf2 = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf2):
                rc2 = backtest_cli.main(
                    [
                        "--db-path",
                        str(db_path),
                        "--step-hours",
                        "4",
                        "--export-trades",
                        str(jsonl_path),
                    ]
                )
            self.assertEqual(rc2, 0)
            self.assertIn("Historical Backtest Report", stdout_buf2.getvalue())
            jsonl_lines = [line for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(len(jsonl_lines), 2)

    def test_cli_backfill_flag_invokes_backfill_and_outputs_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = _build_replay_db(tmp / "cli_bf.db")
            mock_bf_summary = {
                "db_path": str(db_path),
                "interval": "1h",
                "market_ticks": {"rows_inserted": 42},
                "yield_spreads": {"rows_inserted": 3},
            }

            stdout_buf = io.StringIO()
            with (
                patch("fx_signal_reader.cli.backtest.backfill_historical_data", return_value=mock_bf_summary) as mock_bf,
                contextlib.redirect_stdout(stdout_buf),
            ):
                rc = backtest_cli.main(
                    [
                        "--db-path",
                        str(db_path),
                        "--backfill",
                        "--interval",
                        "1h",
                        "--json",
                    ]
                )

            self.assertEqual(rc, 0)
            mock_bf.assert_called_once()
            payload = json.loads(stdout_buf.getvalue())
            self.assertEqual(payload["backfill_summary"]["market_ticks"]["rows_inserted"], 42)


if __name__ == "__main__":
    unittest.main()
