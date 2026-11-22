"""Unit and integration tests for P11-01 per-regime sizing and bracket overrides."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fx_signal_reader.agent.nodes import make_risk_committee_node
from fx_signal_reader.agent.state import (
    RiskRegime,
    TradeAction,
    YieldBias,
    create_initial_state,
    normalize_regime_overrides,
    parse_regime_overrides_arg,
)
from fx_signal_reader.backtest.models import BacktestConfig
from fx_signal_reader.backtest.simulator import run_backtest
from fx_signal_reader.cli.backtest import build_arg_parser as build_backtest_parser
from fx_signal_reader.cli.backtest import format_backtest_summary
from fx_signal_reader.cli.pipeline import build_arg_parser as build_pipeline_parser
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH


def _create_test_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return path


def _seed_two_regime_db(db_path: Path) -> None:
    """Seed an isolated SQLite DB with a RISK_ON day followed by a TRANSITIONAL day."""
    conn = sqlite3.connect(db_path)
    try:
        # 50+ hourly bars around 150.000 trending slightly upward (above EMA50) so bullish signals fire
        ticks = []
        base_price = 149.500
        # 60 prior hours + 120 forward hours
        from datetime import datetime, timedelta, timezone

        start_dt = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        for idx in range(180):
            ts = (start_dt + timedelta(hours=idx)).strftime("%Y-%m-%dT%H:%M:%SZ")
            price = round(base_price + idx * 0.015, 3)
            ticks.append(("JPY=X", ts, price, price - 0.01, price + 0.05, price - 0.02, 1000.0))
            # VIX: < 18 for first 72 hours (RISK_ON), 21.0 thereafter (TRANSITIONAL)
            vix = 15.0 if idx < 72 else 21.0
            ticks.append(("^VIX", ts, vix, vix, vix + 0.2, vix - 0.2, 100.0))

        conn.executemany(
            "INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ticks,
        )
        # Bullish yield spread (> 2.0) covering the whole window
        conn.execute(
            "INSERT INTO yield_spreads (timestamp_utc, us_10y, jp_10y, spread_10y, source) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-01-01T00:00:00Z", 4.5, 1.5, 3.0, "test"),
        )
        conn.commit()
    finally:
        conn.close()


class TestRegimeOverridesValidation(unittest.TestCase):
    def test_normalize_and_parse_json_and_file(self) -> None:
        raw = {
            "risk_on": {"risk_pct": 1.0, "sl_pips": 80, "tp_pips": 160},
            "TRANSITIONAL": {"risk_fraction": 0.04, "sl_pips": 120, "tp_pips": 240, "use_atr": False},
        }
        norm = normalize_regime_overrides(raw)
        self.assertIsNotNone(norm)
        assert norm is not None
        self.assertAlmostEqual(norm["RISK_ON"]["risk_fraction"], 0.01)
        self.assertEqual(norm["RISK_ON"]["sl_pips"], 80.0)
        self.assertEqual(norm["RISK_ON"]["tp_pips"], 160.0)
        self.assertAlmostEqual(norm["TRANSITIONAL"]["risk_fraction"], 0.04)
        self.assertFalse(norm["TRANSITIONAL"]["use_atr"])

        # Parse JSON string
        parsed_str = parse_regime_overrides_arg(json.dumps(raw))
        self.assertEqual(parsed_str, norm)

        # Parse .json file path
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "regimes.json"
            cfg_path.write_text(json.dumps(raw), encoding="utf-8")
            parsed_file = parse_regime_overrides_arg(str(cfg_path))
            self.assertEqual(parsed_file, norm)

    def test_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            normalize_regime_overrides({"UNKNOWN_REGIME": {"risk_pct": 1.0}})
        with self.assertRaises(ValueError):
            normalize_regime_overrides({"RISK_ON": {"unknown_key": 1.0}})
        with self.assertRaises(ValueError):
            normalize_regime_overrides({"RISK_ON": {"risk_pct": True}})
        with self.assertRaises(ValueError):
            normalize_regime_overrides({"RISK_ON": {"sl_pips": -10}})
        with self.assertRaises(ValueError):
            normalize_regime_overrides({"RISK_ON": {"risk_fraction": 1.5}})
        with self.assertRaises(ValueError):
            parse_regime_overrides_arg("{not-valid-json}")


class TestRiskCommitteeRegimeResolution(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fx_regime_node_")
        self.db_path = _create_test_db(Path(self._tmp.name) / "test.db")
        _seed_two_regime_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_node_applies_per_regime_sizing_and_brackets(self) -> None:
        node = make_risk_committee_node(db_path=self.db_path, point_in_time=True)
        overrides = {
            "RISK_ON": {"risk_pct": 1.0, "sl_pips": 80, "tp_pips": 160},
            "TRANSITIONAL": {"risk_pct": 4.0, "sl_pips": 120, "tp_pips": 240},
        }

        # Step at hour 60 -> VIX=15.0 -> RISK_ON
        s_ro = create_initial_state(
            run_id="ro-test",
            timestamp_utc="2026-01-03T12:00:00Z",
            account_capital_usd=10000.0,
            risk_fraction=0.03,
            sl_pips=100.0,
            tp_pips=200.0,
            regime_overrides=overrides,
        )
        s_ro["yield_bias"] = YieldBias.BULLISH.value
        out_ro = node(s_ro)
        prop_ro = out_ro["trade_proposal"]
        self.assertEqual(out_ro["risk_regime"], RiskRegime.RISK_ON.value)
        self.assertEqual(prop_ro["action"], TradeAction.BUY.value)
        self.assertAlmostEqual(prop_ro["entry_price"] - prop_ro["stop_loss"], 0.80, places=3)
        self.assertAlmostEqual(prop_ro["take_profit"] - prop_ro["entry_price"], 1.60, places=3)
        # 1.0% of $10k = $100 risk over 80p (0.80 JPY) -> ~18,800 units
        self.assertIn("risking $100.00 / 1.0%", prop_ro["rationale"])

        # Step at hour 80 -> VIX=21.0 -> TRANSITIONAL
        s_tr = create_initial_state(
            run_id="tr-test",
            timestamp_utc="2026-01-04T08:00:00Z",
            account_capital_usd=10000.0,
            risk_fraction=0.03,
            sl_pips=100.0,
            tp_pips=200.0,
            regime_overrides=overrides,
        )
        s_tr["yield_bias"] = YieldBias.BULLISH.value
        out_tr = node(s_tr)
        prop_tr = out_tr["trade_proposal"]
        self.assertEqual(out_tr["risk_regime"], RiskRegime.TRANSITIONAL.value)
        self.assertEqual(prop_tr["action"], TradeAction.BUY.value)
        self.assertAlmostEqual(prop_tr["entry_price"] - prop_tr["stop_loss"], 1.20, places=3)
        self.assertAlmostEqual(prop_tr["take_profit"] - prop_tr["entry_price"], 2.40, places=3)
        # 4.0% of $10k = $400 risk over 120p (1.20 JPY) -> ~50,233 units
        self.assertIn("risking $400.00 / 4.0%", prop_tr["rationale"])


class TestUnifiedBacktestAndCLI(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fx_regime_bt_")
        self.db_path = _create_test_db(Path(self._tmp.name) / "test.db")
        _seed_two_regime_db(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unified_backtest_with_regime_overrides_and_cli_parsing(self) -> None:
        overrides = {
            "RISK_ON": {"risk_pct": 1.0, "sl_pips": 80, "tp_pips": 160},
            "TRANSITIONAL": {"risk_pct": 4.0, "sl_pips": 120, "tp_pips": 240},
        }
        cfg = BacktestConfig(
            start_utc="2026-01-03T12:00:00Z",
            end_utc="2026-01-08T11:00:00Z",
            step_hours=4,
            max_hold_hours=48,
            regime_overrides=overrides,
        )
        report = run_backtest(db_path=self.db_path, config=cfg)
        self.assertGreaterEqual(report.metrics.trades_executed, 1)
        first = report.trades[0]
        self.assertEqual(first.risk_regime, RiskRegime.RISK_ON)
        self.assertAlmostEqual(first.entry_price - first.stop_loss, 0.80 + 0.015, places=3)

        summary_text = format_backtest_summary(report, db_path=self.db_path)
        self.assertIn("Regime Overrides", summary_text)

        bt_args = build_backtest_parser().parse_args(
            ["--regime-overrides", json.dumps(overrides)]
        )
        self.assertEqual(bt_args.regime_overrides, json.dumps(overrides))

        pl_args = build_pipeline_parser().parse_args(
            ["--regime-overrides", json.dumps(overrides)]
        )
        self.assertEqual(pl_args.regime_overrides, json.dumps(overrides))


if __name__ == "__main__":
    unittest.main()
