"""
Unit tests for multi-pair backtest simulator pip scaling, USD PnL accounting,
and pair configuration (Task P8-06).
"""

from __future__ import annotations

import unittest

from fx_signal_reader.agent.state import RiskRegime, TradeAction, TradeProposal, YieldBias
from fx_signal_reader.backtest.models import BacktestConfig, ExitReason, SimulatedTrade
from fx_signal_reader.backtest.simulator import simulate_trade_resolution


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


if __name__ == "__main__":
    unittest.main()
