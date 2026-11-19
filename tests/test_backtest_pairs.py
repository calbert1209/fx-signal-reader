"""
Unit tests for multi-pair backtest simulator pip scaling, USD PnL accounting,
and pair configuration (Task P8-06).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from fx_signal_reader.agent.state import RiskRegime, TradeAction, TradeProposal, YieldBias
from fx_signal_reader.backtest import run_backtest
from fx_signal_reader.backtest.models import BacktestConfig, ExitReason, SimulatedTrade
from fx_signal_reader.backtest.simulator import simulate_trade_resolution
from fx_signal_reader.cli.backtest import format_backtest_summary
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH


def _build_eurusd_replay_db(db_path: Path, *, add_blackout_event: bool = False) -> Path:
    """Create a synthetic SQLite DB seeded with EUR/USD ticks, DE-US spreads, and events."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
        spreads = [
            ("2026-11-01T00:00:00Z", 4.0, 4.2, 3.5, -0.70, "fixture"),
        ]
        conn.executemany(
            """
            INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            spreads,
        )
        ticks = [
            # VIX ticks to establish RISK_ON regime (<20.0)
            ("^VIX", "2026-11-01T00:00:00Z", 16.0, 16.0, 16.0, 16.0, 0.0),
            # Hourly EUR/USD bars
            ("EURUSD=X", "2026-11-01T00:00:00Z", 1.08500, 1.08480, 1.08520, 1.08450, 100.0),
            ("EURUSD=X", "2026-11-01T01:00:00Z", 1.08600, 1.08500, 1.08650, 1.08490, 100.0),
            ("EURUSD=X", "2026-11-01T02:00:00Z", 1.08700, 1.08600, 1.08750, 1.08590, 100.0),
            ("EURUSD=X", "2026-11-01T03:00:00Z", 1.08900, 1.08700, 1.08950, 1.08690, 100.0),
            ("EURUSD=X", "2026-11-01T04:00:00Z", 1.09600, 1.08900, 1.09700, 1.08850, 100.0),
            ("EURUSD=X", "2026-11-01T05:00:00Z", 1.09500, 1.09600, 1.09650, 1.09450, 100.0),
        ]
        conn.executemany(
            """
            INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ticks,
        )

        if add_blackout_event:
            # Tier-1 ECB event within 4 hours (at 02:00Z) triggering Event Blackout veto
            conn.execute(
                """
                INSERT INTO macro_events (event_hash, timestamp_utc, currency, title, impact, forecast, previous, actual)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("ecb-t1-001", "2026-11-01T02:00:00Z", "EUR", "ECB Main Refinancing Rate Decision", "High", "3.50%", "3.75%", None),
            )

        conn.commit()
    finally:
        conn.close()
    return db_path


def _sample_eurusd_proposal(**overrides) -> TradeProposal:
    payload = {
        "proposal_id": "prop-eurusd-001",
        "timestamp_utc": "2026-03-01T10:00:00Z",
        "pair": "EUR/USD",
        "action": TradeAction.BUY,
        "entry_price": 1.08500,
        "stop_loss": 1.08000,
        "take_profit": 1.09500,
        "risk_reward_ratio": 2.0,
        "position_size_units": 20000,
        "rationale": "Test EUR/USD BUY setup.",
        "macro_bias": YieldBias.BULLISH,
        "risk_regime": RiskRegime.RISK_ON,
        "intervention_risk_score": 0.0,
    }
    payload.update(overrides)
    return TradeProposal.model_validate(payload)


class TestBacktestPairGeneralization(unittest.TestCase):
    def test_backtest_config_eurusd_defaults(self) -> None:
        cfg = BacktestConfig(pair="EUR/USD")
        self.assertEqual(cfg.pair, "EUR/USD")
        self.assertEqual(cfg.sl_pips, 50.0)
        self.assertEqual(cfg.tp_pips, 100.0)

    def test_backtest_config_usdjpy_defaults(self) -> None:
        cfg = BacktestConfig(pair="USD/JPY")
        self.assertEqual(cfg.pair, "USD/JPY")
        self.assertEqual(cfg.sl_pips, 75.0)
        self.assertEqual(cfg.tp_pips, 150.0)

    def test_eurusd_tp_hit_resolution(self) -> None:
        # Zero spread to test baseline math
        config = BacktestConfig(pair="EUR/USD", spread_pips=0.0)
        proposal = _sample_eurusd_proposal()
        bars = [
            {"timestamp_utc": "2026-03-01T10:00:00Z", "close": 1.08500},
            {"timestamp_utc": "2026-03-01T11:00:00Z", "open": 1.08520, "high": 1.09550, "low": 1.08480, "close": 1.09520},
        ]

        trade = simulate_trade_resolution(proposal, bars, config)
        self.assertEqual(trade.pair, "EUR/USD")
        self.assertEqual(trade.exit_reason, ExitReason.TAKE_PROFIT)
        self.assertAlmostEqual(trade.exit_price, 1.09500, places=5)
        # Price delta = 1.09500 - 1.08500 = +0.0100 -> 100.0 pips
        self.assertAlmostEqual(trade.pnl_pips, 100.0)
        # PnL USD = 0.0100 * 20,000 units = $200.00
        self.assertAlmostEqual(trade.pnl_usd, 200.00, places=2)
        self.assertEqual(trade.pnl_jpy, 0.0)
        self.assertAlmostEqual(trade.r_multiple, 2.0, places=2)

    def test_eurusd_sl_hit_resolution(self) -> None:
        # Zero spread
        config = BacktestConfig(pair="EUR/USD", spread_pips=0.0)
        proposal = _sample_eurusd_proposal()
        bars = [
            {"timestamp_utc": "2026-03-01T10:00:00Z", "close": 1.08500},
            {"timestamp_utc": "2026-03-01T11:00:00Z", "open": 1.08480, "high": 1.08510, "low": 1.07950, "close": 1.07980},
        ]

        trade = simulate_trade_resolution(proposal, bars, config)
        self.assertEqual(trade.pair, "EUR/USD")
        self.assertEqual(trade.exit_reason, ExitReason.STOP_LOSS)
        self.assertAlmostEqual(trade.exit_price, 1.08000, places=5)
        # Price delta = 1.08000 - 1.08500 = -0.0050 -> -50.0 pips
        self.assertAlmostEqual(trade.pnl_pips, -50.0)
        # PnL USD = -0.0050 * 20,000 units = -$100.00 (exact target risk)
        self.assertAlmostEqual(trade.pnl_usd, -100.00, places=2)
        self.assertEqual(trade.pnl_jpy, 0.0)
        self.assertAlmostEqual(trade.r_multiple, -1.0, places=2)

    def test_eurusd_spread_drag(self) -> None:
        # 1.5 pips spread = 0.00015 price drag on BUY
        config = BacktestConfig(pair="EUR/USD", spread_pips=1.5)
        proposal = _sample_eurusd_proposal()
        bars = [
            {"timestamp_utc": "2026-03-01T10:00:00Z", "close": 1.08500},
            {"timestamp_utc": "2026-03-01T11:00:00Z", "open": 1.08480, "high": 1.08510, "low": 1.07950, "close": 1.07980},
        ]

        trade = simulate_trade_resolution(proposal, bars, config)
        # Effective entry = 1.08500 + 0.00015 = 1.08515
        self.assertAlmostEqual(trade.entry_price, 1.08515, places=5)
        self.assertEqual(trade.exit_reason, ExitReason.STOP_LOSS)
        # Price delta = 1.08000 - 1.08515 = -0.00515 -> -51.5 pips
        self.assertAlmostEqual(trade.pnl_pips, -51.5, places=1)
        # PnL USD = -0.00515 * 20,000 = -$103.00
        self.assertAlmostEqual(trade.pnl_usd, -103.00, places=2)
        self.assertAlmostEqual(trade.r_multiple, -1.03, places=2)

    def test_eurusd_end_to_end_replay_backtest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _build_eurusd_replay_db(Path(tmpdir) / "replay_eurusd.db")
            cfg = BacktestConfig(pair="EUR/USD", step_hours=4, spread_pips=0.0)
            report = run_backtest(db_path=db_path, config=cfg)

            self.assertEqual(report.config.pair, "EUR/USD")
            self.assertGreaterEqual(report.metrics.trades_executed, 1)
            self.assertGreaterEqual(report.metrics.wins, 1)
            self.assertGreater(report.metrics.net_pnl_usd, 0.0)

            trade = report.trades[0]
            self.assertEqual(trade.pair, "EUR/USD")
            self.assertEqual(trade.action, TradeAction.BUY)
            self.assertEqual(trade.exit_reason, ExitReason.TAKE_PROFIT)
            self.assertEqual(trade.pnl_jpy, 0.0)
            self.assertAlmostEqual(trade.pnl_usd, 200.0, places=2)

            summary_text = format_backtest_summary(report, db_path=db_path)
            self.assertIn("EUR/USD Signal Reader - Historical Backtest Report", summary_text)
            self.assertIn("EUR/USD Bars=6", summary_text)

    def test_eurusd_replay_event_blackout_veto(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = _build_eurusd_replay_db(Path(tmpdir) / "replay_eurusd_blackout.db", add_blackout_event=True)
            cfg = BacktestConfig(pair="EUR/USD", step_hours=4, spread_pips=0.0)
            report = run_backtest(db_path=db_path, config=cfg)

            self.assertEqual(report.config.pair, "EUR/USD")
            self.assertEqual(report.metrics.trades_executed, 0)
            self.assertGreaterEqual(report.metrics.total_vetoes, 1)


if __name__ == "__main__":
    unittest.main()
