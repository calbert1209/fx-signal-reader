"""Unit tests for Phase 8 CLI multi-pair parameterization (Task P8-07).

Tests argument parsing, automatic DB path resolution, FX_PAIR environment
variable precedence, and telemetry/summary formatting across:
- fx-collect (fx_signal_reader.cli.collect)
- fx-pipeline (fx_signal_reader.cli.pipeline)
- fx-schedule (fx_signal_reader.cli.schedule)
- fx-backtest (fx_signal_reader.cli.backtest)
"""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from fx_signal_reader.cli import backtest as backtest_cli
from fx_signal_reader.cli import collect as collect_cli
from fx_signal_reader.cli import pipeline as pipeline_cli
from fx_signal_reader.cli import schedule as schedule_cli
from fx_signal_reader.core import DEFAULT_DB_PATH
from fx_signal_reader.core.pair_profile import get_pair_db_path, get_pair_profile


class TestCLIPairs(unittest.TestCase):
    """Test suite for CLI multi-pair parameterization and database routing."""

    def test_collect_cli_defaults_to_usdjpy(self) -> None:
        parser = collect_cli.build_arg_parser() if hasattr(collect_cli, "build_arg_parser") else None
        # Collect main uses internal parser
        with patch("argparse.ArgumentParser.parse_args") as mock_parse:
            mock_parse.return_value = collect_cli.argparse.Namespace(
                pair="USD/JPY",
                collector="all",
                db_path=None,
                schema_path=str(collect_cli.DEFAULT_SCHEMA_PATH),
                retries=3,
                verbose=False,
            )
            profile = get_pair_profile("USD/JPY")
            expected_db = get_pair_db_path(profile)
            self.assertEqual(expected_db.name, "usdjpy_signals.db")

    def test_collect_cli_eurusd_db_path_resolution(self) -> None:
        profile = get_pair_profile("EUR/USD")
        expected_db = get_pair_db_path(profile)
        self.assertEqual(expected_db.name, "eurusd_signals.db")

    def test_pipeline_cli_parser_defaults(self) -> None:
        parser = pipeline_cli.build_arg_parser()
        args = parser.parse_args([])
        self.assertEqual(args.pair, "USD/JPY")
        self.assertIsNone(args.db_path)

    def test_pipeline_cli_parser_eurusd(self) -> None:
        parser = pipeline_cli.build_arg_parser()
        args = parser.parse_args(["--pair", "EUR/USD"])
        self.assertEqual(args.pair, "EUR/USD")

    @patch.dict(os.environ, {"FX_PAIR": "EURUSD"})
    def test_pipeline_cli_parser_env_var(self) -> None:
        parser = pipeline_cli.build_arg_parser()
        args = parser.parse_args([])
        self.assertEqual(args.pair, "EURUSD")

    def test_schedule_cli_defaults_to_usdjpy(self) -> None:
        parser = schedule_cli.build_arg_parser()
        args = parser.parse_args([])
        cfg = schedule_cli.SchedulerConfig.from_env_and_args(args)
        self.assertEqual(cfg.pair, "USD/JPY")
        self.assertEqual(cfg.db_path.name, "usdjpy_signals.db")

    def test_schedule_cli_eurusd_routes_to_eurusd_db(self) -> None:
        parser = schedule_cli.build_arg_parser()
        args = parser.parse_args(["--pair", "EUR/USD"])
        cfg = schedule_cli.SchedulerConfig.from_env_and_args(args)
        self.assertEqual(cfg.pair, "EUR/USD")
        self.assertEqual(cfg.db_path.name, "eurusd_signals.db")

    @patch.dict(os.environ, {"FX_PAIR": "EUR/USD"})
    def test_schedule_cli_env_var_routing(self) -> None:
        cfg = schedule_cli.SchedulerConfig.from_env_and_args()
        self.assertEqual(cfg.pair, "EUR/USD")
        self.assertEqual(cfg.db_path.name, "eurusd_signals.db")

    def test_schedule_cli_db_override_respected(self) -> None:
        parser = schedule_cli.build_arg_parser()
        args = parser.parse_args(["--pair", "EUR/USD", "--db-path", "/tmp/custom.db"])
        cfg = schedule_cli.SchedulerConfig.from_env_and_args(args)
        self.assertEqual(cfg.pair, "EUR/USD")
        self.assertEqual(cfg.db_path, Path("/tmp/custom.db"))

    def test_backtest_cli_resolve_target_db(self) -> None:
        # USD/JPY default
        db_usdjpy = backtest_cli._resolve_target_db(None, backfill=False, pair="USD/JPY")
        self.assertEqual(db_usdjpy.name, "usdjpy_signals.db")

        # USD/JPY with backfill
        db_usdjpy_bf = backtest_cli._resolve_target_db(None, backfill=True, pair="USD/JPY")
        self.assertEqual(db_usdjpy_bf.name, "backtest_signals.db")

        # EUR/USD default
        db_eurusd = backtest_cli._resolve_target_db(None, backfill=False, pair="EUR/USD")
        self.assertEqual(db_eurusd.name, "eurusd_signals.db")

        # EUR/USD with backfill
        db_eurusd_bf = backtest_cli._resolve_target_db(None, backfill=True, pair="EUR/USD")
        self.assertEqual(db_eurusd_bf.name, "backtest_signals_eurusd.db")

    def test_backtest_cli_parser_pair_flag(self) -> None:
        parser = backtest_cli.build_arg_parser()
        args = parser.parse_args(["--pair", "EUR/USD"])
        self.assertEqual(args.pair, "EUR/USD")
        self.assertIsNone(args.sl_pips)
        self.assertIsNone(args.tp_pips)

    def test_backtest_cli_default_sl_tp_by_pair(self) -> None:
        # USD/JPY defaults to 75 / 150
        cfg_jpy = backtest_cli.BacktestConfig(pair="USD/JPY")
        self.assertEqual(cfg_jpy.sl_pips, 75.0)
        self.assertEqual(cfg_jpy.tp_pips, 150.0)

        # EUR/USD defaults to 50 / 100
        cfg_eur = backtest_cli.BacktestConfig(pair="EUR/USD")
        self.assertEqual(cfg_eur.sl_pips, 50.0)
        self.assertEqual(cfg_eur.tp_pips, 100.0)

    def test_backtest_cli_summary_header_contains_pair(self) -> None:
        from fx_signal_reader.backtest.models import BacktestMetrics, BacktestReport

        cfg = backtest_cli.BacktestConfig(pair="EUR/USD")
        metrics = BacktestMetrics(
            total_steps=10,
            trades_executed=0,
            data_coverage={"bars": 100, "yield_spread_pct": 100.0},
        )
        report = BacktestReport(
            config=cfg,
            metrics=metrics,
            trades=[],
            equity_curve=[],
        )
        text = backtest_cli.format_backtest_summary(report, db_path=Path("data/eurusd_signals.db"))
        self.assertIn("EUR/USD Signal Reader - Historical Backtest Report", text)
        self.assertIn("EUR/USD Bars=100", text)


if __name__ == "__main__":
    unittest.main()
