"""
Unit and integration tests for rolling indicators, MCP trend/momentum queries,
and trend-filtered specialist agent nodes (P7-03 & P7-04).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fx_signal_reader.agent.nodes import macro_analyst_node, risk_committee_node
from fx_signal_reader.agent.state import (
    ApprovalStatus,
    TradeAction,
    YieldBias,
    create_initial_state,
)
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH
from fx_signal_reader.core.indicators import (
    classify_market_trend,
    classify_spread_momentum,
    compute_atr_series,
    compute_ema_series,
    compute_latest_atr,
    compute_latest_ema,
    compute_latest_sma,
    compute_rate_of_change,
    compute_sma_series,
    compute_true_ranges,
)
from fx_signal_reader.mcp import server as mcp_server
from fx_signal_reader.mcp.server import (
    get_latest_yield_spread,
    get_market_trend_summary,
)


def _init_temp_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def _utc_iso(base_dt: datetime, *, hours: int = 0, days: int = 0) -> str:
    return (base_dt + timedelta(hours=hours, days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestIndicatorMath(unittest.TestCase):
    """Verify mathematical correctness and validation guards for `core/indicators.py`."""

    def test_ema_and_sma_match_manual_benchmarks(self) -> None:
        prices = [10.0, 12.0, 14.0, 16.0, 20.0]

        # Period=3: SMA seed at index 2 is (10+12+14)/3 = 12.0
        # alpha = 2/(3+1) = 0.5
        # index 3: 0.5*16 + 0.5*12 = 14.0
        # index 4: 0.5*20 + 0.5*14 = 17.0
        ema_series = compute_ema_series(prices, period=3)
        self.assertEqual(ema_series, [None, None, 12.0, 14.0, 17.0])
        self.assertEqual(compute_latest_ema(prices, period=3), 17.0)

        sma_series = compute_sma_series(prices, period=3)
        self.assertEqual(sma_series, [None, None, 12.0, 14.0, round((14.0 + 16.0 + 20.0) / 3.0, 6)])
        self.assertAlmostEqual(compute_latest_sma(prices, period=3) or 0.0, 16.666667, places=6)

        # Insufficient bars return None without error
        self.assertEqual(compute_ema_series([100.0, 101.0], period=5), [None, None])
        self.assertIsNone(compute_latest_ema([100.0, 101.0], period=5))
        self.assertIsNone(compute_latest_sma([100.0, 101.0], period=5))

    def test_rate_of_change_and_validation_guards(self) -> None:
        spreads = [2.40, 2.38, 2.35, 2.30, 2.28, 2.20]
        # periods_back=5 -> 2.20 - 2.40 = -0.20
        self.assertAlmostEqual(compute_rate_of_change(spreads, periods_back=5) or 0.0, -0.20, places=6)
        self.assertIsNone(compute_rate_of_change(spreads, periods_back=6))

        for bad_period in (0, -5, True, False, 2.5):
            with self.subTest(bad_period=bad_period):
                with self.assertRaises(ValueError):
                    compute_ema_series(spreads, period=bad_period)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    compute_sma_series(spreads, period=bad_period)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    compute_rate_of_change(spreads, periods_back=bad_period)  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            compute_ema_series([1.0, float("nan"), 2.0], period=2)

    def test_classifications(self) -> None:
        self.assertEqual(classify_market_trend(155.0, 152.0, 150.0), "BULLISH")
        self.assertEqual(classify_market_trend(155.0, 152.0, None), "BULLISH")
        self.assertEqual(classify_market_trend(148.0, 152.0, 150.0), "BEARISH")
        self.assertEqual(classify_market_trend(151.0, 152.0, 150.0), "SIDEWAYS")
        self.assertEqual(classify_market_trend(151.0, None, None), "SIDEWAYS")

        self.assertEqual(classify_spread_momentum(-0.11, -0.05), "CONTRACTING")
        self.assertEqual(classify_spread_momentum(-0.02, -0.21), "CONTRACTING")
        self.assertEqual(classify_spread_momentum(0.11, 0.05), "EXPANDING")
        self.assertEqual(classify_spread_momentum(0.02, 0.21), "EXPANDING")
        self.assertEqual(classify_spread_momentum(-0.05, 0.05), "STABLE")
        self.assertEqual(classify_spread_momentum(None, None), "STABLE")


class TestMCPIndicatorsPointInTime(unittest.TestCase):
    """Verify PIT isolation and schema correctness for MCP trend & spread momentum tools."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fx_ind_mcp_")
        self.db_path = Path(self._tmpdir.name) / "indicators_pit.db"
        self.base_dt = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_spread_momentum_and_market_trend_point_in_time_isolation(self) -> None:
        conn = _init_temp_db(self.db_path)
        try:
            # 25 daily yield spreads: flat 2.40% through day 19, expanding on days 20..24,
            # then sharp future plunge on day 25 (must be invisible as_of day 24).
            spread_rows = []
            for d in range(20):
                spread_rows.append((_utc_iso(self.base_dt, days=d), 4.4, 4.8, 2.4, 2.40, "hist"))
            for d in range(20, 25):
                val = 2.40 + 0.03 * (d - 19)  # day 24 = 2.55 (5d change = 2.55 - 2.40 = +0.15)
                spread_rows.append((_utc_iso(self.base_dt, days=d), 4.4, 2.4 + val, 2.4, val, "hist"))
            # Future plunge at day 25
            spread_rows.append((_utc_iso(self.base_dt, days=25), 3.5, 3.6, 2.4, 1.20, "future"))

            conn.executemany(
                "INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source) VALUES (?, ?, ?, ?, ?, ?)",
                spread_rows,
            )

            # 60 hourly JPY=X bars rising gently from 150.00 -> 155.90, then future crash to 140.00
            tick_rows = []
            for h in range(60):
                px = 150.0 + 0.1 * h
                tick_rows.append(("JPY=X", _utc_iso(self.base_dt, hours=h), px, px, px + 0.05, px - 0.05, 0.0))
            tick_rows.append(("JPY=X", _utc_iso(self.base_dt, hours=60), 140.0, 140.0, 140.1, 139.9, 0.0))

            conn.executemany(
                "INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                tick_rows,
            )
            conn.commit()
        finally:
            conn.close()

        as_of_day24 = _utc_iso(self.base_dt, days=24)
        spread_res = get_latest_yield_spread(db_path=str(self.db_path), as_of_utc=as_of_day24)
        self.assertAlmostEqual(spread_res["spread_10y"], 2.55)
        self.assertAlmostEqual(spread_res["spread_5d_change"], 0.15)
        self.assertAlmostEqual(spread_res["spread_20d_change"], 0.15)
        self.assertEqual(spread_res["momentum"], "EXPANDING")

        as_of_bar59 = _utc_iso(self.base_dt, hours=59)
        trend_pit = get_market_trend_summary(symbol="JPY=X", db_path=str(self.db_path), as_of_utc=as_of_bar59)
        self.assertEqual(trend_pit["bars_count"], 60)
        self.assertAlmostEqual(trend_pit["spot_price"], 155.90)
        self.assertIsNotNone(trend_pit["ema_50"])
        self.assertGreater(trend_pit["spot_price"], trend_pit["ema_50"])
        self.assertIsNone(trend_pit["sma_200"])
        self.assertEqual(trend_pit["trend"], "BULLISH")

        # Live mode includes future crash bar (hour 60) -> spot 140.0 < ema_50 -> BEARISH
        trend_live = get_market_trend_summary(symbol="JPY=X", db_path=str(self.db_path), as_of_utc=None)
        self.assertEqual(trend_live["bars_count"], 61)
        self.assertAlmostEqual(trend_live["spot_price"], 140.0)
        self.assertLess(trend_live["spot_price"], trend_live["ema_50"])
        self.assertEqual(trend_live["trend"], "BEARISH")

        # Check tool wrapper forwarding with FX_DB_PATH
        old_env = mcp_server.os.environ.get("FX_DB_PATH")
        mcp_server.os.environ["FX_DB_PATH"] = str(self.db_path)
        try:
            wrapped = mcp_server._tool_get_market_trend_summary(symbol="JPY=X", as_of_utc=as_of_bar59)
            self.assertEqual(wrapped["trend"], "BULLISH")
        finally:
            if old_env is None:
                mcp_server.os.environ.pop("FX_DB_PATH", None)
            else:
                mcp_server.os.environ["FX_DB_PATH"] = old_env

        with self.assertRaises(ValueError):
            get_market_trend_summary(db_path=str(self.db_path), as_of_utc="bad-date")
        with self.assertRaises(ValueError):
            get_market_trend_summary(db_path=str(self.db_path), ema_period=0)


class TestNodeTrendAndMomentumIntegration(unittest.TestCase):
    """Verify MacroAnalyst carry-compression downgrade and RiskCommittee EMA-50 falling-knife veto."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fx_ind_nodes_")
        self.base_dt = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_macro_analyst_downgrades_bullish_usd_when_spread_contracting(self) -> None:
        db_path = Path(self._tmpdir.name) / "contracting.db"
        conn = _init_temp_db(db_path)
        try:
            # Spread still > 2.0% (2.10%), but fell -0.25% from 2.35% over 5 days -> CONTRACTING
            spread_rows = [
                (_utc_iso(self.base_dt, days=0), 4.4, 4.75, 2.40, 2.35, "fixture"),
                (_utc_iso(self.base_dt, days=1), 4.4, 4.72, 2.40, 2.32, "fixture"),
                (_utc_iso(self.base_dt, days=2), 4.4, 4.68, 2.40, 2.28, "fixture"),
                (_utc_iso(self.base_dt, days=3), 4.4, 4.62, 2.40, 2.22, "fixture"),
                (_utc_iso(self.base_dt, days=4), 4.4, 4.55, 2.40, 2.15, "fixture"),
                (_utc_iso(self.base_dt, days=5), 4.4, 4.50, 2.40, 2.10, "fixture"),
            ]
            conn.executemany(
                "INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source) VALUES (?, ?, ?, ?, ?, ?)",
                spread_rows,
            )
            conn.execute(
                "INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("JPY=X", _utc_iso(self.base_dt, days=5), 151.0, 151.0, 151.1, 150.9, 0.0),
            )
            conn.commit()
        finally:
            conn.close()

        as_of_ts = _utc_iso(self.base_dt, days=5)
        state = create_initial_state(run_id="macro-contract", timestamp_utc=as_of_ts)
        out = macro_analyst_node(state, db_path=str(db_path), point_in_time=True)

        self.assertEqual(out["yield_bias"], YieldBias.NEUTRAL.value)
        macro = out["macro_assessment"]
        self.assertEqual(macro["spread_momentum"], "CONTRACTING")
        self.assertAlmostEqual(macro["spread_5d_change"], -0.25)
        self.assertIn("downgrades BULLISH_USD to NEUTRAL", macro["policy_divergence_summary"])

    def test_risk_committee_ema50_falling_knife_veto_vs_bullish_allowance(self) -> None:
        # Case 1: Spot drops below EMA-50 under BULLISH_USD -> veto_trade=True, HOLD
        knife_db = Path(self._tmpdir.name) / "knife.db"
        conn = _init_temp_db(knife_db)
        try:
            ticks = []
            # 50 bars at 155.0, then final bar crashes to 150.0 (below EMA-50 ~154.8)
            for h in range(55):
                px = 155.0 if h < 54 else 150.0
                ts = _utc_iso(self.base_dt, hours=h)
                ticks.append(("JPY=X", ts, px, px, px + 0.05, px - 0.05, 0.0))
                ticks.append(("^VIX", ts, 15.0, 15.0, 15.1, 14.9, 0.0))
            conn.executemany(
                "INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ticks,
            )
            conn.commit()
        finally:
            conn.close()

        as_of_ts = _utc_iso(self.base_dt, hours=54)
        state = create_initial_state(run_id="knife-test", timestamp_utc=as_of_ts)
        state["yield_bias"] = YieldBias.BULLISH_USD.value
        knife_out = risk_committee_node(state, db_path=str(knife_db), point_in_time=True)

        self.assertTrue(knife_out["risk_assessment"]["veto_trade"])
        self.assertEqual(knife_out["risk_assessment"]["market_trend"], "BEARISH")
        self.assertLess(knife_out["spot_price"], knife_out["risk_assessment"]["ema_50"])
        self.assertIn("trend filter veto", knife_out["risk_assessment"]["risk_notes"])
        self.assertEqual(knife_out["trade_proposal"]["action"], TradeAction.HOLD.value)
        self.assertEqual(knife_out["approval_status"], ApprovalStatus.NOT_REQUIRED.value)

        # Case 2: Spot rallies above EMA-50 under BULLISH_USD & RISK_ON -> BUY PENDING
        bull_db = Path(self._tmpdir.name) / "bull.db"
        conn2 = _init_temp_db(bull_db)
        try:
            ticks2 = []
            for h in range(55):
                px = 150.0 + 0.1 * h
                ts = _utc_iso(self.base_dt, hours=h)
                ticks2.append(("JPY=X", ts, px, px, px + 0.05, px - 0.05, 0.0))
                ticks2.append(("^VIX", ts, 15.0, 15.0, 15.1, 14.9, 0.0))
            conn2.executemany(
                "INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ticks2,
            )
            conn2.commit()
        finally:
            conn2.close()

        bull_out = risk_committee_node(state, db_path=str(bull_db), point_in_time=True)
        self.assertFalse(bull_out["risk_assessment"]["veto_trade"])
        self.assertEqual(bull_out["risk_assessment"]["market_trend"], "BULLISH")
        self.assertGreater(bull_out["spot_price"], bull_out["risk_assessment"]["ema_50"])
        self.assertIsNotNone(bull_out["risk_assessment"]["atr_14"])
        self.assertAlmostEqual(bull_out["risk_assessment"]["atr_14"], 0.15, places=6)
        self.assertEqual(bull_out["trade_proposal"]["action"], TradeAction.BUY.value)
        self.assertEqual(bull_out["approval_status"], ApprovalStatus.PENDING.value)

    def test_atr_calculation_and_dynamic_brackets_plus_allowed_regimes(self) -> None:
        """P7-05 & P7-06: verify ATR-14 computation, dynamic brackets, and custom allowed_regimes."""
        # Constant bar range H-L=0.50 with +0.50 step closes -> TR = [0.50, 0.75, 0.75, ...]
        highs = [100.5 + 0.5 * i for i in range(15)]
        lows = [100.0 + 0.5 * i for i in range(15)]
        closes = [100.25 + 0.5 * i for i in range(15)]
        trs = compute_true_ranges(highs, lows, closes)
        self.assertEqual(len(trs), 15)
        self.assertAlmostEqual(trs[0], 0.50, places=6)
        self.assertAlmostEqual(trs[1], 0.75, places=6)
        atr_series = compute_atr_series(highs, lows, closes, period=14)
        self.assertIsNone(atr_series[12])
        self.assertIsNotNone(atr_series[13])
        self.assertAlmostEqual(compute_latest_atr(highs, lows, closes, period=14) or 0.0, 0.75, places=6)
        self.assertIsNone(compute_latest_atr(highs[:5], lows[:5], closes[:5], period=14))

        with self.assertRaises(ValueError):
            compute_true_ranges([100.0], [101.0], [100.5])

        # Verify use_atr=True scales SL/TP and units inside risk_committee_node,
        # and custom allowed_regimes=["RISK_ON"] vetoes TRANSITIONAL VIX=20.0.
        db_path = Path(self._tmpdir.name) / "atr_regimes.db"
        conn = _init_temp_db(db_path)
        try:
            ticks = []
            for h in range(55):
                px = 150.0 + 0.05 * h
                hi = px + 0.40  # wide 0.80 JPY bars -> ATR-14 ~0.80 JPY
                lo = px - 0.40
                ts = _utc_iso(self.base_dt, hours=h)
                ticks.append(("JPY=X", ts, px, px, hi, lo, 0.0))
                vix = 15.0 if h < 54 else 20.0  # Final bar TRANSITIONAL
                ticks.append(("^VIX", ts, vix, vix, vix + 0.1, vix - 0.1, 0.0))
            conn.executemany(
                "INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ticks,
            )
            conn.commit()
        finally:
            conn.close()

        as_of_ts = _utc_iso(self.base_dt, hours=53)  # RISK_ON bar
        atr_state = create_initial_state(
            run_id="atr-on",
            timestamp_utc=as_of_ts,
            use_atr=True,
        )
        atr_state["yield_bias"] = YieldBias.BULLISH_USD.value
        atr_out = risk_committee_node(atr_state, db_path=str(db_path), point_in_time=True)
        prop = atr_out["trade_proposal"]
        self.assertEqual(prop["action"], TradeAction.BUY.value)
        self.assertGreater(abs(prop["entry_price"] - prop["stop_loss"]), 0.75)

        # TRANSITIONAL bar rejected when allowed_regimes=["RISK_ON"]
        trans_state = create_initial_state(
            run_id="reg-filter",
            timestamp_utc=_utc_iso(self.base_dt, hours=54),
            allowed_regimes=["RISK_ON"],
        )
        trans_state["yield_bias"] = YieldBias.BULLISH_USD.value
        trans_out = risk_committee_node(trans_state, db_path=str(db_path), point_in_time=True)
        self.assertTrue(trans_out["risk_assessment"]["veto_trade"])
        self.assertIn("excluded by allowed_regimes", trans_out["risk_assessment"]["risk_notes"])
        self.assertEqual(trans_out["trade_proposal"]["action"], TradeAction.HOLD.value)


if __name__ == "__main__":
    unittest.main()
