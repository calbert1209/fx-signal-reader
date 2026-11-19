"""
Offline unit tests for P6-03 backtest Pydantic v2 schemas, deterministic bracket
simulator, and portfolio/regime/bias analytics.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import unittest

from pydantic import ValidationError

import fx_signal_reader.backtest as backtest_pkg
from fx_signal_reader.agent.state import RiskRegime, TradeAction, TradeProposal, YieldBias
from fx_signal_reader.backtest import (
    BacktestConfig,
    BacktestMetrics,
    BacktestReport,
    ExitReason,
    SimulatedTrade,
    compute_backtest_metrics,
    simulate_trade_resolution,
)


def _sample_proposal(**overrides) -> TradeProposal:
    payload = {
        "proposal_id": "prop-20261118-001",
        "timestamp_utc": "2026-11-18T00:00:00Z",
        "pair": "USD/JPY",
        "action": TradeAction.BUY,
        "entry_price": 150.000,
        "stop_loss": 149.250,
        "take_profit": 151.500,
        "risk_reward_ratio": 2.0,
        "position_size_units": 10000,
        "rationale": "Test BUY bracket setup.",
        "macro_bias": YieldBias.BULLISH_USD,
        "risk_regime": RiskRegime.RISK_ON,
        "intervention_risk_score": 20.0,
    }
    payload.update(overrides)
    return TradeProposal.model_validate(payload)


def _sample_simulated_trade(**overrides) -> SimulatedTrade:
    payload = {
        "trade_id": "sim-prop-001",
        "proposal_id": "prop-001",
        "entry_time_utc": "2026-11-18T00:00:00Z",
        "exit_time_utc": "2026-11-18T04:00:00Z",
        "action": TradeAction.BUY,
        "position_size_units": 10000,
        "entry_price": 150.015,
        "stop_loss": 149.25,
        "take_profit": 151.5,
        "exit_price": 151.5,
        "exit_reason": ExitReason.TAKE_PROFIT,
        "pnl_pips": 148.5,
        "pnl_jpy": 14850.0,
        "pnl_usd": 98.0198,
        "r_multiple": 1.98,
        "macro_bias": YieldBias.BULLISH_USD,
        "risk_regime": RiskRegime.RISK_ON,
        "intervention_risk_score": 20.0,
        "bars_held": 4,
    }
    payload.update(overrides)
    return SimulatedTrade.model_validate(payload)


class TestBacktestSchemas(unittest.TestCase):
    """Test Pydantic v2 schemas, defaults, validation bounds, JSON safety, and exports."""

    def test_package_exports(self):
        expected = {
            "BacktestConfig",
            "BacktestMetrics",
            "BacktestReport",
            "ExitReason",
            "SimulatedTrade",
            "compute_backtest_metrics",
            "simulate_trade_resolution",
        }
        for name in expected:
            with self.subTest(symbol=name):
                self.assertTrue(hasattr(backtest_pkg, name))
                self.assertIn(name, backtest_pkg.__all__)

    def test_exit_reason_enum(self):
        self.assertEqual(
            [m.value for m in ExitReason],
            ["TAKE_PROFIT", "STOP_LOSS", "TIME_STOP", "END_OF_DATA"],
        )
        self.assertIsInstance(ExitReason.TAKE_PROFIT, str)
        self.assertEqual(ExitReason.STOP_LOSS, "STOP_LOSS")

    def test_backtest_config_defaults_and_normalization(self):
        cfg = BacktestConfig()
        self.assertIsNone(cfg.start_utc)
        self.assertIsNone(cfg.end_utc)
        self.assertEqual(cfg.step_hours, 4)
        self.assertEqual(cfg.max_hold_hours, 120)
        self.assertEqual(cfg.spread_pips, 1.5)
        self.assertFalse(cfg.allow_overlapping_trades)
        self.assertTrue(cfg.conservative_tie_break)
        self.assertEqual(cfg.mof_lookback_hours, 48)
        self.assertEqual(cfg.initial_capital_usd, 10000.0)

        cfg_norm = BacktestConfig(
            start_utc="2026-11-18T09:00:00+09:00",
            end_utc=datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc),
            step_hours=0,
        )
        self.assertEqual(cfg_norm.start_utc, "2026-11-18T00:00:00Z")
        self.assertEqual(cfg_norm.end_utc, "2026-11-19T00:00:00Z")
        self.assertEqual(cfg_norm.step_hours, 0)

        equal_bounds = BacktestConfig(
            start_utc="2026-11-18T00:00:00Z",
            end_utc="2026-11-18T00:00:00Z",
        )
        self.assertEqual(equal_bounds.start_utc, equal_bounds.end_utc)

    def test_backtest_config_rejects_invalid_values(self):
        invalid_cases = [
            {
                "start_utc": "2026-11-19T00:00:00Z",
                "end_utc": "2026-11-18T00:00:00Z",
            },
            {"start_utc": True},
            {"end_utc": 1700000000},
            {"start_utc": "not-a-timestamp"},
            {"step_hours": -1},
            {"step_hours": True},
            {"max_hold_hours": 0},
            {"max_hold_hours": -5},
            {"spread_pips": -0.1},
            {"spread_pips": float("nan")},
            {"spread_pips": float("inf")},
            {"spread_pips": False},
            {"mof_lookback_hours": -1},
            {"initial_capital_usd": 0.0},
            {"initial_capital_usd": -100.0},
            {"initial_capital_usd": float("inf")},
        ]
        for kwargs in invalid_cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    BacktestConfig(**kwargs)

    def test_simulated_trade_validation(self):
        valid = _sample_simulated_trade()
        self.assertEqual(valid.action, TradeAction.BUY)
        self.assertEqual(valid.exit_reason, ExitReason.TAKE_PROFIT)

        invalid_overrides = [
            {"trade_id": ""},
            {"trade_id": "   "},
            {"proposal_id": "  "},
            {"action": TradeAction.HOLD},
            {"action": "HOLD"},
            {"exit_reason": "UNKNOWN"},
            {"entry_time_utc": 1700000000},
            {"exit_time_utc": True},
            {
                "entry_time_utc": "2026-11-18T05:00:00Z",
                "exit_time_utc": "2026-11-18T04:00:00Z",
            },
            {"position_size_units": 0},
            {"position_size_units": True},
            {"entry_price": 0.0},
            {"stop_loss": -1.0},
            {"take_profit": float("nan")},
            {"exit_price": float("inf")},
            {"pnl_usd": float("nan")},
            {"r_multiple": float("-inf")},
            {"intervention_risk_score": -0.1},
            {"intervention_risk_score": 100.1},
            {"bars_held": -1},
            {"bars_held": False},
        ]
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    _sample_simulated_trade(**overrides)

    def test_metrics_and_report_reject_nested_non_finite_and_round_trip_json(self):
        with self.assertRaises(ValidationError):
            BacktestMetrics(data_coverage={"ticks": float("nan")})

        with self.assertRaises(ValidationError):
            BacktestMetrics(breakdown_by_regime={"RISK_ON": {"net_pnl_usd": float("inf")}})

        with self.assertRaises(ValidationError):
            BacktestMetrics(breakdown_by_bias={"BULLISH_USD": {"vals": [1.0, float("-inf")]}})

        cfg = BacktestConfig(start_utc="2026-11-18T00:00:00Z", end_utc="2026-11-19T00:00:00Z")
        metrics = BacktestMetrics()
        with self.assertRaises(ValidationError):
            BacktestReport(
                config=cfg,
                metrics=metrics,
                equity_curve=[{"timestamp_utc": "2026-11-18T00:00:00Z", "equity_usd": float("nan")}],
            )

        trade = _sample_simulated_trade()
        metrics, curve = compute_backtest_metrics([trade], cfg)
        report = BacktestReport(config=cfg, metrics=metrics, equity_curve=curve, trades=[trade])
        dumped = report.model_dump(mode="json")
        serialized = json.dumps(dumped, allow_nan=False)
        reloaded = BacktestReport.model_validate_json(serialized)
        self.assertEqual(reloaded.trades[0].trade_id, trade.trade_id)
        self.assertIsNone(reloaded.metrics.profit_factor)
        self.assertIsNone(reloaded.metrics.sharpe_ratio)


class TestSimulateTradeResolutionValidation(unittest.TestCase):
    """Verify strict upfront validation of proposals and forward bars."""

    def setUp(self):
        self.cfg = BacktestConfig()
        self.valid_bars = [
            {
                "timestamp_utc": "2026-11-18T01:00:00Z",
                "high": 150.2,
                "low": 149.8,
                "close": 150.1,
            }
        ]

    def test_rejects_invalid_proposals(self):
        invalid_proposals = [
            _sample_proposal(
                action=TradeAction.HOLD,
                entry_price=None,
                stop_loss=None,
                take_profit=None,
                risk_reward_ratio=None,
                position_size_units=0,
            ),
            _sample_proposal(entry_price=None),
            _sample_proposal(stop_loss=None),
            _sample_proposal(take_profit=None),
            _sample_proposal(entry_price=0.0),
            _sample_proposal(stop_loss=-149.0),
            _sample_proposal(take_profit=float("nan")),
            _sample_proposal(entry_price=float("inf")),
            _sample_proposal(position_size_units=0),
            _sample_proposal(proposal_id="   "),
            # Inverted BUY brackets
            _sample_proposal(action=TradeAction.BUY, entry_price=150.0, stop_loss=150.0, take_profit=151.5),
            _sample_proposal(action=TradeAction.BUY, entry_price=150.0, stop_loss=150.5, take_profit=151.5),
            _sample_proposal(action=TradeAction.BUY, entry_price=150.0, stop_loss=149.25, take_profit=149.9),
            # Inverted SELL brackets
            _sample_proposal(action=TradeAction.SELL, entry_price=150.0, stop_loss=150.0, take_profit=148.5),
            _sample_proposal(action=TradeAction.SELL, entry_price=150.0, stop_loss=149.5, take_profit=148.5),
            _sample_proposal(action=TradeAction.SELL, entry_price=150.0, stop_loss=150.75, take_profit=150.1),
        ]
        for prop in invalid_proposals:
            with self.subTest(prop=prop.model_dump()):
                with self.assertRaises(ValueError):
                    simulate_trade_resolution(prop, self.valid_bars, self.cfg)

    def test_rejects_invalid_bars_and_upfront_corruption(self):
        prop = _sample_proposal()
        invalid_bar_lists = [
            [],
            [{"timestamp_utc": "2026-11-18T00:00:00Z", "close": 150.0}],  # only entry bar
            [{"timestamp_utc": "2026-11-17T23:00:00Z", "close": 150.0}],  # only prior bar
            [
                {"timestamp_utc": "2026-11-18T02:00:00Z", "close": 150.1},
                {"timestamp_utc": "2026-11-18T01:00:00Z", "close": 150.2},  # unsorted
            ],
            [
                {"timestamp_utc": "2026-11-18T01:00:00Z", "close": 150.1},
                {"timestamp_utc": "2026-11-18T01:00:00Z", "close": 150.2},  # duplicate ts
            ],
            [{"timestamp_utc": None, "close": 150.0}],
            [{"timestamp_utc": 1700000000, "close": 150.0}],
            [{"timestamp_utc": True, "close": 150.0}],
            [{"timestamp_utc": "2026-11-18T01:00:00Z"}],  # missing close/price
            [{"timestamp_utc": "2026-11-18T01:00:00Z", "close": 0.0}],
            [{"timestamp_utc": "2026-11-18T01:00:00Z", "close": float("nan")}],
            [{"timestamp_utc": "2026-11-18T01:00:00Z", "close": 150.0, "high": 149.9, "low": 150.1}],
            # Upfront validation: bar 1 hits TP, bar 2 is out-of-order -> must still raise
            [
                {"timestamp_utc": "2026-11-18T02:00:00Z", "high": 152.0, "low": 150.0, "close": 151.8},
                {"timestamp_utc": "2026-11-18T01:00:00Z", "high": 150.5, "low": 150.0, "close": 150.2},
            ],
        ]
        for bars in invalid_bar_lists:
            with self.subTest(bars=bars):
                with self.assertRaises(ValueError):
                    simulate_trade_resolution(prop, bars, self.cfg)

    def test_accepts_dict_proposal_config_and_price_key(self):
        prop_dict = _sample_proposal().model_dump(mode="json")
        bars = [
            {"timestamp_utc": "2026-11-18T01:00:00Z", "price": 151.600, "high": 151.600, "low": 150.000}
        ]
        trade = simulate_trade_resolution(prop_dict, bars, {"spread_pips": 1.5})
        self.assertEqual(trade.exit_reason, ExitReason.TAKE_PROFIT)
        self.assertEqual(trade.exit_price, 151.500)


class TestSimulateTradeResolutionMechanics(unittest.TestCase):
    """Verify BUY/SELL resolution, spread, tie-breaking, TIME_STOP, END_OF_DATA, and PnL math."""

    def test_buy_take_profit_and_stop_loss_math(self):
        cfg = BacktestConfig(spread_pips=1.5, max_hold_hours=72)
        prop = _sample_proposal(
            proposal_id="buy-001",
            action=TradeAction.BUY,
            entry_price=150.000,
            stop_loss=149.250,
            take_profit=151.500,
            position_size_units=10000,
        )

        # Bar 1 neutral, Bar 2 hits TP exactly
        tp_bars = [
            {"timestamp_utc": "2026-11-18T01:00:00Z", "high": 150.4, "low": 149.9, "close": 150.2},
            {"timestamp_utc": "2026-11-18T02:00:00Z", "high": 151.500, "low": 150.1, "close": 151.4},
        ]
        win_trade = simulate_trade_resolution(prop, tp_bars, cfg)
        self.assertEqual(win_trade.trade_id, "sim-buy-001")
        self.assertEqual(win_trade.entry_price, 150.015)
        self.assertEqual(win_trade.exit_price, 151.500)
        self.assertEqual(win_trade.exit_reason, ExitReason.TAKE_PROFIT)
        self.assertEqual(win_trade.exit_time_utc, "2026-11-18T02:00:00Z")
        self.assertEqual(win_trade.bars_held, 2)
        self.assertEqual(win_trade.pnl_pips, 148.5)
        self.assertEqual(win_trade.pnl_jpy, 14850.0)
        self.assertEqual(win_trade.pnl_usd, round(14850.0 / 151.500, 4))
        self.assertEqual(win_trade.r_multiple, 1.98)

        # Exact SL boundary touch
        sl_bars = [
            {"timestamp_utc": "2026-11-18T01:00:00Z", "high": 150.2, "low": 149.250, "close": 149.4},
        ]
        loss_trade = simulate_trade_resolution(prop, sl_bars, cfg)
        self.assertEqual(loss_trade.exit_reason, ExitReason.STOP_LOSS)
        self.assertEqual(loss_trade.exit_price, 149.250)
        self.assertEqual(loss_trade.bars_held, 1)
        self.assertEqual(loss_trade.pnl_pips, -76.5)
        self.assertEqual(loss_trade.pnl_jpy, -7650.0)
        self.assertEqual(loss_trade.pnl_usd, round(-7650.0 / 149.250, 4))
        self.assertEqual(loss_trade.r_multiple, -1.02)

    def test_sell_take_profit_and_stop_loss_math(self):
        cfg = BacktestConfig(spread_pips=1.5, max_hold_hours=72)
        prop = _sample_proposal(
            proposal_id="sell-001",
            action=TradeAction.SELL,
            entry_price=150.000,
            stop_loss=150.750,
            take_profit=148.500,
            position_size_units=5000,
            macro_bias=YieldBias.BEARISH_USD,
            risk_regime=RiskRegime.TRANSITIONAL,
        )

        tp_bars = [
            {"timestamp_utc": "2026-11-18T01:00:00Z", "high": 150.2, "low": 148.500, "close": 148.7},
        ]
        win_trade = simulate_trade_resolution(prop, tp_bars, cfg)
        self.assertEqual(win_trade.entry_price, 149.985)
        self.assertEqual(win_trade.exit_reason, ExitReason.TAKE_PROFIT)
        self.assertEqual(win_trade.exit_price, 148.500)
        self.assertEqual(win_trade.pnl_pips, 148.5)
        self.assertEqual(win_trade.pnl_jpy, 7425.0)
        self.assertEqual(win_trade.pnl_usd, round(7425.0 / 148.500, 4))
        self.assertEqual(win_trade.r_multiple, 1.98)

        sl_bars = [
            {"timestamp_utc": "2026-11-18T01:00:00Z", "high": 150.750, "low": 149.8, "close": 150.6},
        ]
        loss_trade = simulate_trade_resolution(prop, sl_bars, cfg)
        self.assertEqual(loss_trade.exit_reason, ExitReason.STOP_LOSS)
        self.assertEqual(loss_trade.exit_price, 150.750)
        self.assertEqual(loss_trade.pnl_pips, -76.5)
        self.assertEqual(loss_trade.pnl_jpy, -3825.0)
        self.assertEqual(loss_trade.pnl_usd, round(-3825.0 / 150.750, 4))
        self.assertEqual(loss_trade.r_multiple, -1.02)

    def test_zero_spread_yields_exact_2r_and_minus_1r(self):
        cfg = BacktestConfig(spread_pips=0.0)
        prop = _sample_proposal(entry_price=150.0, stop_loss=149.25, take_profit=151.5)
        win = simulate_trade_resolution(
            prop,
            [{"timestamp_utc": "2026-11-18T01:00:00Z", "high": 151.6, "low": 150.0, "close": 151.5}],
            cfg,
        )
        loss = simulate_trade_resolution(
            prop,
            [{"timestamp_utc": "2026-11-18T01:00:00Z", "high": 150.0, "low": 149.2, "close": 149.25}],
            cfg,
        )
        self.assertEqual(win.r_multiple, 2.0)
        self.assertEqual(loss.r_multiple, -1.0)

    def test_same_bar_collision_tie_break_buy_and_sell(self):
        buy_prop = _sample_proposal(
            action=TradeAction.BUY, entry_price=150.0, stop_loss=149.25, take_profit=151.5
        )
        sell_prop = _sample_proposal(
            action=TradeAction.SELL, entry_price=150.0, stop_loss=150.75, take_profit=148.5
        )
        wide_bar = [
            {"timestamp_utc": "2026-11-18T01:00:00Z", "high": 152.0, "low": 148.0, "close": 150.0}
        ]

        buy_cons = simulate_trade_resolution(buy_prop, wide_bar, BacktestConfig(conservative_tie_break=True))
        buy_aggr = simulate_trade_resolution(buy_prop, wide_bar, BacktestConfig(conservative_tie_break=False))
        self.assertEqual(buy_cons.exit_reason, ExitReason.STOP_LOSS)
        self.assertEqual(buy_cons.exit_price, 149.25)
        self.assertEqual(buy_aggr.exit_reason, ExitReason.TAKE_PROFIT)
        self.assertEqual(buy_aggr.exit_price, 151.5)

        sell_cons = simulate_trade_resolution(sell_prop, wide_bar, BacktestConfig(conservative_tie_break=True))
        sell_aggr = simulate_trade_resolution(sell_prop, wide_bar, BacktestConfig(conservative_tie_break=False))
        self.assertEqual(sell_cons.exit_reason, ExitReason.STOP_LOSS)
        self.assertEqual(sell_cons.exit_price, 150.75)
        self.assertEqual(sell_aggr.exit_reason, ExitReason.TAKE_PROFIT)
        self.assertEqual(sell_aggr.exit_price, 148.5)

    def test_entry_bar_skipped_and_only_post_entry_bars_counted(self):
        prop = _sample_proposal(timestamp_utc="2026-11-18T02:00:00Z")
        bars = [
            # Prior & entry bars hit SL — must be ignored!
            {"timestamp_utc": "2026-11-18T01:00:00Z", "high": 150.0, "low": 148.0, "close": 149.0},
            {"timestamp_utc": "2026-11-18T02:00:00Z", "high": 152.0, "low": 148.0, "close": 150.0},
            {"timestamp_utc": "2026-11-18T03:00:00Z", "high": 150.5, "low": 149.8, "close": 150.3},
            {"timestamp_utc": "2026-11-18T04:00:00Z", "high": 151.6, "low": 150.2, "close": 151.5},
        ]
        trade = simulate_trade_resolution(prop, bars, BacktestConfig())
        self.assertEqual(trade.exit_reason, ExitReason.TAKE_PROFIT)
        self.assertEqual(trade.exit_time_utc, "2026-11-18T04:00:00Z")
        self.assertEqual(trade.bars_held, 2)

    def test_time_stop_exact_deadline_crossing_and_gap(self):
        prop = _sample_proposal(timestamp_utc="2026-11-18T00:00:00Z")

        # 1. Exact deadline bar (even as last bar) -> TIME_STOP, not END_OF_DATA
        cfg_4h = BacktestConfig(max_hold_hours=4)
        exact_bars = [
            {"timestamp_utc": "2026-11-18T02:00:00Z", "high": 150.4, "low": 149.8, "close": 150.1},
            {"timestamp_utc": "2026-11-18T04:00:00Z", "high": 150.6, "low": 149.9, "close": 150.45},
        ]
        t_exact = simulate_trade_resolution(prop, exact_bars, cfg_4h)
        self.assertEqual(t_exact.exit_reason, ExitReason.TIME_STOP)
        self.assertEqual(t_exact.exit_time_utc, "2026-11-18T04:00:00Z")
        self.assertEqual(t_exact.exit_price, 150.45)
        self.assertEqual(t_exact.bars_held, 2)

        # 2. Next bar crosses deadline -> exits at last in-window bar close, ignoring out-of-window TP/SL
        crossing_bars = [
            {"timestamp_utc": "2026-11-18T02:00:00Z", "high": 150.4, "low": 149.8, "close": 150.25},
            {"timestamp_utc": "2026-11-18T05:00:00Z", "high": 155.0, "low": 145.0, "close": 152.0},
        ]
        t_cross = simulate_trade_resolution(prop, crossing_bars, cfg_4h)
        self.assertEqual(t_cross.exit_reason, ExitReason.TIME_STOP)
        self.assertEqual(t_cross.exit_time_utc, "2026-11-18T02:00:00Z")
        self.assertEqual(t_cross.exit_price, 150.25)
        self.assertEqual(t_cross.bars_held, 1)

        # 3. Weekend gap: first post-entry bar is already > deadline -> TIME_STOP on first post-entry bar
        gap_bars = [
            {"timestamp_utc": "2026-11-18T10:00:00Z", "high": 155.0, "low": 145.0, "close": 150.30},
        ]
        t_gap = simulate_trade_resolution(prop, gap_bars, cfg_4h)
        self.assertEqual(t_gap.exit_reason, ExitReason.TIME_STOP)
        self.assertEqual(t_gap.exit_time_utc, "2026-11-18T10:00:00Z")
        self.assertEqual(t_gap.exit_price, 150.30)
        self.assertEqual(t_gap.bars_held, 1)

    def test_end_of_data_and_nullable_high_low_fallback(self):
        prop = _sample_proposal(timestamp_utc="2026-11-18T00:00:00Z")
        cfg = BacktestConfig(max_hold_hours=72)
        bars_eod = [
            {"timestamp_utc": "2026-11-18T04:00:00Z", "price": 150.30, "high": None, "low": None},
            {"timestamp_utc": "2026-11-18T08:00:00Z", "price": 150.60, "high": None, "low": None},
        ]
        eod_trade = simulate_trade_resolution(prop, bars_eod, cfg)
        self.assertEqual(eod_trade.exit_reason, ExitReason.END_OF_DATA)
        self.assertEqual(eod_trade.exit_time_utc, "2026-11-18T08:00:00Z")
        self.assertEqual(eod_trade.exit_price, 150.60)
        self.assertEqual(eod_trade.bars_held, 2)

        # Nullable high/low falling back to close can still trigger TP
        bars_tp_null = [
            {"timestamp_utc": "2026-11-18T04:00:00Z", "close": 151.60, "high": None, "low": None},
        ]
        tp_trade = simulate_trade_resolution(prop, bars_tp_null, cfg)
        self.assertEqual(tp_trade.exit_reason, ExitReason.TAKE_PROFIT)
        self.assertEqual(tp_trade.exit_price, 151.50)

        # Determinism check
        tp_trade_2 = simulate_trade_resolution(prop, bars_tp_null, cfg)
        self.assertEqual(tp_trade, tp_trade_2)


class TestComputeBacktestMetrics(unittest.TestCase):
    """Verify aggregate portfolio metrics, equity curve, drawdown, Sharpe, and breakdowns."""

    def test_zero_trades_bounded_and_unbounded(self):
        cfg_bounded = BacktestConfig(start_utc="2026-11-18T00:00:00Z", end_utc="2026-11-19T00:00:00Z")
        metrics_b, curve_b = compute_backtest_metrics(
            [],
            cfg_bounded,
            total_steps=10,
            total_signals_actionable=2,
            total_holds=8,
            total_vetoes=3,
            trades_skipped_overlap=1,
            data_coverage={"yield_spread_pct": 100.0},
        )
        self.assertEqual(metrics_b.trades_executed, 0)
        self.assertEqual(metrics_b.total_steps, 10)
        self.assertEqual(metrics_b.total_signals_actionable, 2)
        self.assertEqual(metrics_b.total_holds, 8)
        self.assertEqual(metrics_b.total_vetoes, 3)
        self.assertEqual(metrics_b.trades_skipped_overlap, 1)
        self.assertEqual(metrics_b.win_rate_pct, 0.0)
        self.assertIsNone(metrics_b.profit_factor)
        self.assertIsNone(metrics_b.sharpe_ratio)
        self.assertEqual(metrics_b.max_drawdown_usd, 0.0)
        self.assertEqual(metrics_b.max_drawdown_pct, 0.0)
        self.assertEqual(len(curve_b), 1)
        self.assertEqual(curve_b[0]["equity_usd"], 10000.0)
        self.assertEqual(curve_b[0]["timestamp_utc"], "2026-11-18T00:00:00Z")

        self.assertEqual(set(metrics_b.breakdown_by_regime.keys()), {r.value for r in RiskRegime})
        self.assertEqual(set(metrics_b.breakdown_by_bias.keys()), {b.value for b in YieldBias})
        self.assertEqual(metrics_b.breakdown_by_regime["RISK_ON"]["count"], 0)
        self.assertIsNone(metrics_b.breakdown_by_regime["RISK_ON"]["profit_factor"])

        cfg_unbounded = BacktestConfig()
        metrics_u, curve_u = compute_backtest_metrics([], cfg_unbounded)
        self.assertEqual(metrics_u.trades_executed, 0)
        self.assertEqual(curve_u, [])

    def test_mixed_wins_losses_breakeven_all_wins_all_losses_and_drawdown(self):
        cfg = BacktestConfig(initial_capital_usd=10000.0)

        # Out-of-order trades forming equity: 10000 -> 10500 -> 9800 -> 10600 -> 10000 (+0 breakeven)
        t1 = _sample_simulated_trade(
            trade_id="sim-1",
            proposal_id="1",
            entry_time_utc="2026-11-10T00:00:00Z",
            exit_time_utc="2026-11-10T04:00:00Z",
            pnl_usd=500.0,
            r_multiple=2.0,
            risk_regime=RiskRegime.RISK_ON,
            macro_bias=YieldBias.BULLISH_USD,
        )
        t2 = _sample_simulated_trade(
            trade_id="sim-2",
            proposal_id="2",
            entry_time_utc="2026-11-11T00:00:00Z",
            exit_time_utc="2026-11-11T04:00:00Z",
            pnl_usd=-700.0,
            r_multiple=-1.0,
            exit_reason=ExitReason.STOP_LOSS,
            risk_regime=RiskRegime.TRANSITIONAL,
            macro_bias=YieldBias.BEARISH_USD,
        )
        t3 = _sample_simulated_trade(
            trade_id="sim-3",
            proposal_id="3",
            entry_time_utc="2026-11-12T00:00:00Z",
            exit_time_utc="2026-11-12T04:00:00Z",
            pnl_usd=800.0,
            r_multiple=2.0,
            risk_regime=RiskRegime.RISK_ON,
            macro_bias=YieldBias.BULLISH_USD,
        )
        t4 = _sample_simulated_trade(
            trade_id="sim-4",
            proposal_id="4",
            entry_time_utc="2026-11-13T00:00:00Z",
            exit_time_utc="2026-11-13T04:00:00Z",
            pnl_usd=-600.0,
            r_multiple=-1.0,
            exit_reason=ExitReason.STOP_LOSS,
            risk_regime=RiskRegime.RISK_OFF,
            macro_bias=YieldBias.BEARISH_USD,
        )
        t5 = _sample_simulated_trade(
            trade_id="sim-5",
            proposal_id="5",
            entry_time_utc="2026-11-14T00:00:00Z",
            exit_time_utc="2026-11-14T04:00:00Z",
            pnl_usd=0.0,
            r_multiple=0.0,
            exit_reason=ExitReason.TIME_STOP,
            risk_regime=RiskRegime.RISK_ON,
            macro_bias=YieldBias.BULLISH_USD,
        )

        # Pass out-of-order to verify sorting by exit_time_utc
        metrics, curve = compute_backtest_metrics([t4, t2, t5, t1, t3], cfg)

        self.assertEqual([pt["trade_id"] for pt in curve], [None, "sim-1", "sim-2", "sim-3", "sim-4", "sim-5"])
        self.assertEqual([pt["equity_usd"] for pt in curve], [10000.0, 10500.0, 9800.0, 10600.0, 10000.0, 10000.0])

        self.assertEqual(metrics.trades_executed, 5)
        self.assertEqual(metrics.wins, 2)
        self.assertEqual(metrics.losses, 3)  # breakeven 0.0 counts as non-win
        self.assertEqual(metrics.win_rate_pct, 40.0)
        self.assertEqual(metrics.gross_profit_usd, 1300.0)
        self.assertEqual(metrics.gross_loss_usd, 1300.0)
        self.assertEqual(metrics.net_pnl_usd, 0.0)
        self.assertEqual(metrics.return_pct, 0.0)
        self.assertEqual(metrics.profit_factor, 1.0)
        self.assertEqual(metrics.expectancy_usd, 0.0)
        self.assertEqual(metrics.avg_r_multiple, 0.4)
        self.assertEqual(metrics.max_drawdown_usd, 700.0)
        self.assertEqual(metrics.max_drawdown_pct, round((700.0 / 10500.0) * 100.0, 4))
        self.assertIsNotNone(metrics.sharpe_ratio)
        self.assertTrue(math.isfinite(metrics.sharpe_ratio))

        # Regime breakdown checks
        self.assertEqual(metrics.breakdown_by_regime["RISK_ON"]["count"], 3)
        self.assertEqual(metrics.breakdown_by_regime["RISK_ON"]["wins"], 2)
        self.assertEqual(metrics.breakdown_by_regime["RISK_ON"]["losses"], 1)
        self.assertIsNone(metrics.breakdown_by_regime["RISK_ON"]["profit_factor"])
        self.assertEqual(metrics.breakdown_by_regime["RISK_OFF"]["profit_factor"], 0.0)

        # Bias breakdown checks
        self.assertEqual(metrics.breakdown_by_bias["BULLISH_USD"]["net_pnl_usd"], 1300.0)
        self.assertEqual(metrics.breakdown_by_bias["BEARISH_USD"]["net_pnl_usd"], -1300.0)
        self.assertEqual(metrics.breakdown_by_bias["NEUTRAL"]["count"], 0)

        # All-wins -> profit_factor is None
        all_wins_m, _ = compute_backtest_metrics([t1, t3], cfg)
        self.assertIsNone(all_wins_m.profit_factor)

        # All-losses -> profit_factor is 0.0
        all_losses_m, _ = compute_backtest_metrics([t2, t4], cfg)
        self.assertEqual(all_losses_m.profit_factor, 0.0)

    def test_sharpe_ratio_edge_cases_and_json_serialization(self):
        # <2 trades -> None
        one_trade = _sample_simulated_trade(pnl_usd=100.0)
        m1, _ = compute_backtest_metrics([one_trade], BacktestConfig())
        self.assertIsNone(m1.sharpe_ratio)

        # Equal-PnL unbounded trades -> std == 0 -> 0.0
        t_eq_1 = _sample_simulated_trade(
            trade_id="sim-eq-1",
            proposal_id="eq-1",
            entry_time_utc="2026-11-10T00:00:00Z",
            exit_time_utc="2026-11-10T04:00:00Z",
            pnl_usd=100.0,
        )
        t_eq_2 = _sample_simulated_trade(
            trade_id="sim-eq-2",
            proposal_id="eq-2",
            entry_time_utc="2026-11-11T00:00:00Z",
            exit_time_utc="2026-11-11T04:00:00Z",
            pnl_usd=100.0,
        )
        m_eq, _ = compute_backtest_metrics([t_eq_1, t_eq_2], BacktestConfig())
        self.assertEqual(m_eq.sharpe_ratio, 0.0)

        # Bounded multi-day positive sequence -> finite positive float
        cfg_bounded = BacktestConfig(
            start_utc="2026-11-10T00:00:00Z",
            end_utc="2026-11-13T00:00:00Z",
        )
        t_b2 = _sample_simulated_trade(
            trade_id="sim-b2",
            proposal_id="b2",
            entry_time_utc="2026-11-11T00:00:00Z",
            exit_time_utc="2026-11-11T04:00:00Z",
            pnl_usd=200.0,
        )
        m_bounded, curve_bounded = compute_backtest_metrics([t_eq_1, t_b2], cfg_bounded)
        self.assertIsNotNone(m_bounded.sharpe_ratio)
        self.assertGreater(m_bounded.sharpe_ratio, 0.0)
        self.assertTrue(math.isfinite(m_bounded.sharpe_ratio))

        report = BacktestReport(
            config=cfg_bounded,
            metrics=m_bounded,
            equity_curve=curve_bounded,
            trades=[t_eq_1, t_b2],
        )
        encoded = json.dumps(report.model_dump(mode="json"), allow_nan=False)
        self.assertIn("sim-b2", encoded)

    def test_rejects_invalid_counters(self):
        with self.assertRaises(ValueError):
            compute_backtest_metrics([], BacktestConfig(), total_steps=-1)
        with self.assertRaises(ValueError):
            compute_backtest_metrics([], BacktestConfig(), total_vetoes=True)


if __name__ == "__main__":
    unittest.main()
