"""
Offline Unit & Integration Tests for Point-in-Time MCP & Agent Replay (Task P6-01).

Covers:
  1. MCP SQLite query isolation (`as_of_utc`, `past_hours`/`future_hours`, `lookback_hours`),
     strict UTC normalization (`fallback_now=False`), bool/NaN/negative/early-return
     validation, partial/empty DB handling, SQL parameterization, and `@mcp_server.tool`
     wrapper schemas & forwarding.
  2. Specialist agent node replay (`macro_analyst_node`, `risk_committee_node`) with
     `point_in_time=True`, strict `state['timestamp_utc']` validation, 48-hour default
     MoF decay (preserving explicit `mof_lookback_hours=0`), and live-mode contrast.
  3. Compiled LangGraph replay (`build_signal_graph`, `paper_execution_node`, factories)
     across multi-step time evolution and `write_ledger=False` ledger isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import anyio

import fx_signal_reader.agent.graph as agent_graph
from fx_signal_reader.agent.graph import (
    ExecutionReceipt,
    build_signal_graph,
    make_paper_execution_node,
    paper_execution_node,
    resume_with_decision,
)
from fx_signal_reader.agent.nodes import (
    DEFAULT_PIT_MOF_LOOKBACK_HOURS,
    macro_analyst_node,
    make_macro_analyst_node,
    make_risk_committee_node,
    risk_committee_node,
)
from fx_signal_reader.agent.state import (
    ApprovalStatus,
    RiskRegime,
    TradeAction,
    YieldBias,
    create_initial_state,
    validate_state_models,
)
from fx_signal_reader.backtest.models import BacktestConfig
from fx_signal_reader.backtest.simulator import run_backtest
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH as SCHEMA_PATH, PROJECT_ROOT
import fx_signal_reader.mcp.server as mcp_server
from fx_signal_reader.mcp.server import (
    get_latest_yield_spread,
    get_market_ticks_summary,
    get_recent_mof_alerts,
    get_upcoming_macro_events,
)


def _ts(base_utc: str, *, hours: float = 0.0, seconds: int = 0) -> str:
    """Shift an ISO-8601 `YYYY-MM-DDTHH:MM:SSZ` timestamp by hours and seconds."""
    dt = datetime.strptime(base_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    shifted = dt + timedelta(hours=hours, seconds=seconds)
    return shifted.strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_pit_db(
    db_file: Path,
    *,
    yield_rows: list[tuple[str, float | None, float | None, float | None, float | None, str]] | None = None,
    ticks: list[tuple[str, str, float, float | None, float | None, float | None, float | None]] | None = None,
    events: list[tuple[str, str, str, str, str, str | None, str | None, str | None]] | None = None,
    headlines: list[tuple[str, str, str, str, str, str]] | None = None,
) -> Path:
    """Create a schema-initialized SQLite fixture DB with arbitrary multi-timestamp rows."""
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        if yield_rows:
            if yield_rows and len(yield_rows[0]) == 8:
                conn.executemany(
                    """
                    INSERT INTO yield_spreads (timestamp_utc, us_2y, jp_2y, spread_2y, us_10y, jp_10y, spread_10y, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    yield_rows,
                )
            else:
                conn.executemany(
                    """
                    INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    yield_rows,
                )
        if ticks:
            conn.executemany(
                """
                INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ticks,
            )
        if events:
            conn.executemany(
                """
                INSERT INTO macro_events (event_hash, timestamp_utc, currency, title, impact, forecast, previous, actual)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                events,
            )
        if headlines:
            conn.executemany(
                """
                INSERT INTO news_headlines (url_hash, published_utc, source, title, url, matched_keywords)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                headlines,
            )
        conn.commit()
    finally:
        conn.close()
    return db_file


def _core_ticks_at(
    ts_utc: str,
    *,
    spot: float,
    vix: float,
    sp500: float = 6100.0,
    nikkei: float = 39500.0,
    gold: float = 2700.0,
    tnx: float = 4.5,
    oil: float | None = None,
) -> list[tuple[str, str, float, float | None, float | None, float | None, float | None]]:
    """Build a complete set of core market ticks at a given timestamp."""
    ticks = [
        ("JPY=X", ts_utc, spot, spot - 0.1, spot + 0.1, spot - 0.2, 0.0),
        ("^TNX", ts_utc, tnx, tnx - 0.02, tnx + 0.02, tnx - 0.03, 0.0),
        ("^VIX", ts_utc, vix, vix - 0.2, vix + 0.3, vix - 0.3, 0.0),
        ("^GSPC", ts_utc, sp500, sp500 - 10.0, sp500 + 15.0, sp500 - 12.0, 1000000.0),
        ("^N225", ts_utc, nikkei, nikkei - 50.0, nikkei + 100.0, nikkei - 80.0, 500000.0),
        ("GC=F", ts_utc, gold, gold - 2.0, gold + 5.0, gold - 4.0, 1500.0),
    ]
    if oil is not None:
        ticks.append(("CL=F", ts_utc, oil, oil - 0.5, oil + 0.5, oil - 0.5, 50000.0))
    return ticks


class TestMCPPointInTimeIsolation(unittest.TestCase):
    """Verify zero look-ahead bias and validation across all 4 MCP query functions."""

    BASE_T = "2026-11-18T09:00:00Z"

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="fx_pit_mcp_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_get_latest_yield_spread_excludes_future_rows(self) -> None:
        t = self.BASE_T
        db_path = _create_pit_db(
            self._tmpdir / "spreads.db",
            yield_rows=[
                (_ts(t, hours=-24), 4.00, 4.40, 2.30, 2.10, "hist_old"),
                (t, 4.10, 4.60, 2.35, 2.25, "hist_exact"),
                (_ts(t, hours=1), 3.10, 3.20, 2.90, 0.30, "future_row"),
            ],
            ticks=[
                ("JPY=X", _ts(t, hours=-2), 151.25, 151.00, 151.40, 150.90, 0.0),
                ("JPY=X", t, 152.50, 152.20, 152.60, 152.10, 0.0),
                ("JPY=X", _ts(t, hours=1), 159.95, 159.80, 160.10, 159.70, 0.0),
            ],
        )

        at_t = get_latest_yield_spread(db_path=str(db_path), as_of_utc=t)
        self.assertEqual(at_t["timestamp_utc"], t)
        self.assertAlmostEqual(at_t["spread_10y"], 2.25)
        self.assertEqual(at_t["source"], "hist_exact")
        self.assertAlmostEqual(at_t["usdjpy_spot"], 152.50)
        self.assertEqual(at_t["usdjpy_timestamp_utc"], t)

        before_t = get_latest_yield_spread(db_path=str(db_path), as_of_utc=_ts(t, hours=-1))
        self.assertEqual(before_t["timestamp_utc"], _ts(t, hours=-24))
        self.assertAlmostEqual(before_t["spread_10y"], 2.10)
        self.assertAlmostEqual(before_t["usdjpy_spot"], 151.25)
        self.assertEqual(before_t["usdjpy_timestamp_utc"], _ts(t, hours=-2))

        live = get_latest_yield_spread(db_path=str(db_path), as_of_utc=None)
        self.assertEqual(live["timestamp_utc"], _ts(t, hours=1))
        self.assertAlmostEqual(live["usdjpy_spot"], 159.95)

    def test_get_latest_yield_spread_partial_and_empty_coverage(self) -> None:
        t = self.BASE_T
        only_spot_db = _create_pit_db(
            self._tmpdir / "only_spot.db",
            yield_rows=[(_ts(t, hours=2), 4.5, 4.9, 2.5, 2.4, "future_spread")],
            ticks=[("JPY=X", _ts(t, hours=-1), 153.10, 153.00, 153.20, 152.90, 0.0)],
        )
        res_spot = get_latest_yield_spread(db_path=str(only_spot_db), as_of_utc=t)
        self.assertIsNone(res_spot["timestamp_utc"])
        self.assertIsNone(res_spot["spread_10y"])
        self.assertIsNone(res_spot["spread_bps"])
        self.assertAlmostEqual(res_spot["usdjpy_spot"], 153.10)
        self.assertEqual(res_spot["usdjpy_timestamp_utc"], _ts(t, hours=-1))

        only_spread_db = _create_pit_db(
            self._tmpdir / "only_spread.db",
            yield_rows=[(_ts(t, hours=-1), 4.5, 4.9, 2.5, 2.4, "past_spread")],
            ticks=[("JPY=X", _ts(t, hours=2), 158.00, 157.90, 158.10, 157.80, 0.0)],
        )
        res_spread = get_latest_yield_spread(db_path=str(only_spread_db), as_of_utc=t)
        self.assertEqual(res_spread["timestamp_utc"], _ts(t, hours=-1))
        self.assertAlmostEqual(res_spread["spread_10y"], 2.4)
        self.assertIsNone(res_spread["usdjpy_spot"])
        self.assertIsNone(res_spread["usdjpy_timestamp_utc"])

        res_empty = get_latest_yield_spread(db_path=str(only_spot_db), as_of_utc=_ts(t, hours=-5))
        self.assertTrue(all(v is None for v in res_empty.values()))

    def test_get_market_ticks_summary_excludes_future_bars_and_preserves_bar_change(self) -> None:
        t = self.BASE_T
        db_path = _create_pit_db(
            self._tmpdir / "ticks.db",
            ticks=[
                ("JPY=X", _ts(t, hours=-2), 150.00, 149.90, 150.10, 149.80, 0.0),
                ("JPY=X", _ts(t, hours=-1), 150.50, 150.00, 150.60, 149.95, 0.0),
                ("JPY=X", t, 151.00, 150.50, 151.10, 150.45, 0.0),
                ("JPY=X", _ts(t, hours=1), 160.00, 159.00, 160.20, 158.90, 0.0),
                ("^VIX", t, 14.5, 15.0, 15.2, 14.3, 0.0),
                ("^VIX", _ts(t, hours=1), 32.0, 30.0, 33.0, 29.5, 0.0),
                ("^N225", _ts(t, hours=1), 40000.0, 39800.0, 40100.0, 39750.0, 0.0),
            ],
        )

        summary = get_market_ticks_summary(
            symbols=["JPY=X", "^VIX", "^N225"],
            db_path=str(db_path),
            as_of_utc=t,
        )
        self.assertEqual(summary["as_of_utc"], t)
        self.assertEqual(summary["symbol_count"], 2)
        self.assertEqual(summary["missing_symbols"], ["^N225"])

        jpy = summary["symbols"]["JPY=X"]
        self.assertAlmostEqual(jpy["close"], 151.00)
        self.assertAlmostEqual(jpy["prev_close"], 150.50)
        self.assertAlmostEqual(jpy["bar_change"], 0.50)
        self.assertEqual(jpy["timestamp_utc"], t)

        vix = summary["symbols"]["^VIX"]
        self.assertIsNone(vix["prev_close"])
        self.assertAlmostEqual(vix["close"], 14.5)
        self.assertAlmostEqual(vix["bar_change"], -0.5)

        empty_summary = get_market_ticks_summary(
            symbols=["JPY=X", "^VIX"],
            db_path=str(db_path),
            as_of_utc=_ts(t, hours=-5),
        )
        self.assertEqual(empty_summary["symbol_count"], 0)
        self.assertIsNone(empty_summary["as_of_utc"])
        self.assertEqual(empty_summary["missing_symbols"], ["JPY=X", "^VIX"])

        # Validation runs upfront even when symbols=[]
        with self.assertRaises(ValueError):
            get_market_ticks_summary(symbols=[], db_path=str(db_path), as_of_utc="bad-ts")

    def test_get_market_ticks_summary_cl_f_isolation(self) -> None:
        """Verify CL=F crude oil ticks respect as_of_utc and block future prices."""
        t = self.BASE_T
        db_path = _create_pit_db(
            self._tmpdir / "cl_f.db",
            ticks=[
                ("CL=F", _ts(t, hours=-2), 70.0, 69.5, 70.5, 69.0, 50000.0),
                ("CL=F", t, 71.5, 70.0, 72.0, 69.8, 60000.0),
                ("CL=F", _ts(t, hours=1), 75.0, 74.0, 75.5, 73.5, 70000.0),
            ],
        )
        res = get_market_ticks_summary(
            symbols=["CL=F"],
            db_path=str(db_path),
            as_of_utc=t,
        )
        self.assertIn("CL=F", res["symbols"])
        self.assertAlmostEqual(res["symbols"]["CL=F"]["close"], 71.5)
        self.assertEqual(res["symbols"]["CL=F"]["timestamp_utc"], t)
        self.assertNotEqual(res["symbols"]["CL=F"]["close"], 75.0)

    def test_get_latest_yield_spread_2y_isolation_and_fallback(self) -> None:
        """Verify 2Y yield spread respects as_of_utc and handles legacy/older DB gracefully."""
        t = self.BASE_T
        db_path = _create_pit_db(
            self._tmpdir / "spreads_2y.db",
            yield_rows=[
                (t, 4.10, 0.60, 3.50, 4.60, 2.35, 2.25, "FRED+MoF"),
                (_ts(t, hours=1), 3.00, 0.50, 2.50, 3.20, 2.90, 0.30, "future_row"),
            ],
            ticks=[
                ("JPY=X", t, 152.50, 152.20, 152.60, 152.10, 0.0),
            ],
        )
        res = get_latest_yield_spread(db_path=str(db_path), as_of_utc=t)
        self.assertEqual(res["timestamp_utc"], t)
        self.assertAlmostEqual(res["spread_10y"], 2.25)

        # Legacy DB without 2Y data falls back gracefully
        legacy_db = _create_pit_db(
            self._tmpdir / "spreads_legacy.db",
            yield_rows=[
                (t, None, 4.40, 2.20, 2.20, "Legacy"),
            ],
            ticks=[
                ("JPY=X", t, 152.50, 152.20, 152.60, 152.10, 0.0),
            ],
        )
        res_legacy = get_latest_yield_spread(db_path=str(legacy_db), as_of_utc=t)
        self.assertEqual(res_legacy["timestamp_utc"], t)
        self.assertAlmostEqual(res_legacy["spread_10y"], 2.20)

    def test_get_recent_mof_alerts_as_of_utc_and_lookback_window(self) -> None:
        t = self.BASE_T
        db_path = _create_pit_db(
            self._tmpdir / "mof.db",
            headlines=[
                (
                    "h_m72",
                    _ts(t, hours=-72),
                    "Reuters",
                    "Japan conducts yen rate check at 72h prior",
                    "https://example.com/m72",
                    "rate check",
                ),
                (
                    "h_m48_1s",
                    _ts(t, hours=-48, seconds=-1),
                    "Reuters",
                    "Suspected yen intervention 1s outside 48h window",
                    "https://example.com/m48_1s",
                    "suspected intervention",
                ),
                (
                    "h_m48",
                    _ts(t, hours=-48),
                    "Nikkei",
                    "MoF warns against rapid moves and one-sided yen weakness",
                    "https://example.com/m48",
                    "rapid moves, one-sided",
                ),
                (
                    "h_m12",
                    _ts(t, hours=-12),
                    "Reuters",
                    "Mimura says closely monitoring FX developments",
                    "https://example.com/m12",
                    "monitoring closely",
                ),
                (
                    "h_exact",
                    t,
                    "FXStreet",
                    "Tokyo stands ready to respond to excessive volatility",
                    "https://example.com/exact",
                    "stand ready, excessive volatility",
                ),
                (
                    "h_p1s",
                    _ts(t, seconds=1),
                    "Reuters",
                    "Japan conducts rate check 1s after T",
                    "https://example.com/p1s",
                    "rate check",
                ),
                (
                    "h_p2h",
                    _ts(t, hours=2),
                    "Reuters",
                    "Actual FX intervention confirmed 2h after T",
                    "https://example.com/p2h",
                    "intervention operations",
                ),
            ],
        )

        # as_of_utc without lookback_hours excludes > T only
        no_lookback = get_recent_mof_alerts(min_tier=1, limit=20, db_path=str(db_path), as_of_utc=t)
        self.assertEqual(
            [a["url_hash"] for a in no_lookback],
            ["h_exact", "h_m12", "h_m48", "h_m48_1s", "h_m72"],
        )

        # as_of_utc with lookback_hours=48 applies inclusive [T-48h, T]
        within_48 = get_recent_mof_alerts(
            min_tier=1, limit=20, db_path=str(db_path), as_of_utc=t, lookback_hours=48
        )
        self.assertEqual([a["url_hash"] for a in within_48], ["h_exact", "h_m12", "h_m48"])

        # lookback_hours=0 matches exact boundary == T
        exact_zero = get_recent_mof_alerts(
            min_tier=1, limit=20, db_path=str(db_path), as_of_utc=t, lookback_hours=0
        )
        self.assertEqual([a["url_hash"] for a in exact_zero], ["h_exact"])

        # Live mode (as_of_utc=None, lookback_hours=None) returns all including future
        live_all = get_recent_mof_alerts(min_tier=1, limit=20, db_path=str(db_path))
        self.assertEqual(
            [a["url_hash"] for a in live_all],
            ["h_p2h", "h_p1s", "h_exact", "h_m12", "h_m48", "h_m48_1s", "h_m72"],
        )

        # Unanchored lookback_hours relative to UTC now
        now_utc = datetime.now(timezone.utc)
        recent_ts = (now_utc - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old_ts = (now_utc - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
        now_db = _create_pit_db(
            self._tmpdir / "mof_now.db",
            headlines=[
                (
                    "h_now_recent",
                    recent_ts,
                    "Reuters",
                    "Japan conducts rate check recently",
                    "https://example.com/recent",
                    "rate check",
                ),
                (
                    "h_now_old",
                    old_ts,
                    "Reuters",
                    "Japan conducts rate check 72h ago",
                    "https://example.com/old",
                    "rate check",
                ),
            ],
        )
        unanchored = get_recent_mof_alerts(
            min_tier=1, limit=20, db_path=str(now_db), as_of_utc=None, lookback_hours=48
        )
        self.assertEqual([a["url_hash"] for a in unanchored], ["h_now_recent"])

        # Upfront validation even with limit=0
        for bad_lb in (-1, True, False, float("nan"), float("inf")):
            with self.subTest(bad_lookback=bad_lb):
                with self.assertRaises(ValueError):
                    get_recent_mof_alerts(
                        limit=0,
                        db_path=str(db_path),
                        as_of_utc=t,
                        lookback_hours=bad_lb,  # type: ignore[arg-type]
                    )

    def test_get_upcoming_macro_events_window_and_limit(self) -> None:
        t = self.BASE_T
        old_events = [
            (
                f"old_{i}",
                _ts(t, hours=-24 * (30 - i)),
                "USD",
                f"Historical USD Release {i}",
                "High",
                None,
                None,
                "1.0%",
            )
            for i in range(15)
        ]
        window_events = [
            ("ev_m4_1s", _ts(t, hours=-4, seconds=-1), "USD", "Too Early", "High", None, None, None),
            ("ev_m4", _ts(t, hours=-4), "USD", "Window Start", "High", None, None, None),
            ("ev_p12", _ts(t, hours=12), "USD", "FOMC Decision", "High", None, None, None),
            ("ev_p48", _ts(t, hours=48), "JPY", "BoJ Policy Rate", "High", None, None, None),
            ("ev_p48_1s", _ts(t, hours=48, seconds=1), "USD", "Too Late", "High", None, None, None),
        ]
        db_path = _create_pit_db(
            self._tmpdir / "macro.db",
            events=old_events + window_events,
        )

        # Windowed query ignores the 15 older rows and respects exact boundaries
        windowed = get_upcoming_macro_events(
            impact="High",
            limit=10,
            db_path=str(db_path),
            as_of_utc=t,
            past_hours=4,
            future_hours=48,
        )
        self.assertEqual([e["event_hash"] for e in windowed], ["ev_m4", "ev_p12", "ev_p48"])

        # future_hours=0 excludes upcoming releases after T
        past_only = get_upcoming_macro_events(
            impact="High",
            limit=10,
            db_path=str(db_path),
            as_of_utc=t,
            past_hours=4,
            future_hours=0,
        )
        self.assertEqual([e["event_hash"] for e in past_only], ["ev_m4"])

        # as_of_utc=None preserves unwindowed chronological ASC behavior
        unwindowed = get_upcoming_macro_events(impact="High", limit=3, db_path=str(db_path))
        self.assertEqual([e["event_hash"] for e in unwindowed], ["old_0", "old_1", "old_2"])

        # Upfront validation of past_hours / future_hours even with limit=0 and as_of_utc=None
        for bad_val in (-1, True, False, float("nan"), float("inf")):
            with self.subTest(bad_hour=bad_val):
                with self.assertRaises(ValueError):
                    get_upcoming_macro_events(
                        limit=0,
                        db_path=str(db_path),
                        as_of_utc=None,
                        past_hours=bad_val,  # type: ignore[arg-type]
                    )
                with self.assertRaises(ValueError):
                    get_upcoming_macro_events(
                        limit=0,
                        db_path=str(db_path),
                        as_of_utc=None,
                        future_hours=bad_val,  # type: ignore[arg-type]
                    )

    def test_as_of_utc_timezone_normalization_and_invalid_input(self) -> None:
        t_utc = "2026-11-18T09:00:00Z"
        t_jst = "2026-11-18T18:00:00+09:00"
        db_path = _create_pit_db(
            self._tmpdir / "tz.db",
            yield_rows=[
                (t_utc, 4.1, 4.6, 2.3, 2.3, "jst_exact"),
                (_ts(t_utc, seconds=1), 3.0, 3.1, 2.9, 0.2, "jst_future"),
            ],
            ticks=_core_ticks_at(t_utc, spot=152.0, vix=15.0)
            + _core_ticks_at(_ts(t_utc, seconds=1), spot=160.0, vix=30.0),
            events=[
                ("ev_at", t_utc, "USD", "US CPI", "High", None, None, None),
                ("ev_far", _ts(t_utc, hours=72), "USD", "Far Future", "High", None, None, None),
            ],
            headlines=[
                (
                    "h_at",
                    t_utc,
                    "Reuters",
                    "Mimura says monitoring FX closely",
                    "https://example.com/at",
                    "monitoring closely",
                ),
                (
                    "h_after",
                    _ts(t_utc, seconds=1),
                    "Reuters",
                    "Japan conducts rate check",
                    "https://example.com/after",
                    "rate check",
                ),
            ],
        )

        self.assertEqual(
            get_latest_yield_spread(db_path=str(db_path), as_of_utc=t_jst),
            get_latest_yield_spread(db_path=str(db_path), as_of_utc=t_utc),
        )
        self.assertEqual(
            get_upcoming_macro_events(db_path=str(db_path), as_of_utc=t_jst),
            get_upcoming_macro_events(db_path=str(db_path), as_of_utc=t_utc),
        )
        self.assertEqual(
            get_recent_mof_alerts(db_path=str(db_path), as_of_utc=t_jst, lookback_hours=24),
            get_recent_mof_alerts(db_path=str(db_path), as_of_utc=t_utc, lookback_hours=24),
        )
        self.assertEqual(
            get_market_ticks_summary(db_path=str(db_path), as_of_utc=t_jst),
            get_market_ticks_summary(db_path=str(db_path), as_of_utc=t_utc),
        )

        for bad_ts in ("", "   ", "not-a-date", "2026-02-31T06:00:00Z", True, False):
            with self.subTest(bad_ts=bad_ts):
                with self.assertRaises(ValueError):
                    get_latest_yield_spread(db_path=str(db_path), as_of_utc=bad_ts)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    get_upcoming_macro_events(
                        limit=0, db_path=str(db_path), as_of_utc=bad_ts  # type: ignore[arg-type]
                    )
                with self.assertRaises(ValueError):
                    get_recent_mof_alerts(
                        limit=0, db_path=str(db_path), as_of_utc=bad_ts  # type: ignore[arg-type]
                    )
                with self.assertRaises(ValueError):
                    get_market_ticks_summary(
                        symbols=[], db_path=str(db_path), as_of_utc=bad_ts  # type: ignore[arg-type]
                    )

        # SQL injection payloads in string filters are safely parameterized / rejected
        sqli = "' OR '1'='1'; DROP TABLE macro_events;--"
        self.assertEqual(
            get_upcoming_macro_events(currency=sqli, impact=sqli, db_path=str(db_path), as_of_utc=t_utc),
            [],
        )
        tick_sqli = get_market_ticks_summary(symbols=[sqli], db_path=str(db_path), as_of_utc=t_utc)
        self.assertEqual(tick_sqli["missing_symbols"], [sqli])
        with self.assertRaises(ValueError):
            get_latest_yield_spread(db_path=str(db_path), as_of_utc=sqli)

    def test_mcp_tool_wrappers_schema_and_forwarding(self) -> None:
        async def _inspect() -> None:
            tools = await mcp_server.mcp_server.list_tools()
            tool_map = {t.name: t for t in tools}
            self.assertEqual(
                set(tool_map.keys()),
                {
                    "get_latest_yield_spread",
                    "get_upcoming_macro_events",
                    "get_recent_mof_alerts",
                    "get_market_ticks_summary",
                    "get_market_trend_summary",
                },
            )
            for name, tool in tool_map.items():
                props = tool.input_schema.get("properties", {})
                self.assertNotIn("db_path", props, f"{name} must not expose db_path")
                self.assertIn("as_of_utc", props, f"{name} must accept as_of_utc")
                self.assertTrue(tool.annotations and tool.annotations.read_only_hint)

            macro_props = tool_map["get_upcoming_macro_events"].input_schema["properties"]
            self.assertIn("past_hours", macro_props)
            self.assertIn("future_hours", macro_props)

            mof_props = tool_map["get_recent_mof_alerts"].input_schema["properties"]
            self.assertIn("lookback_hours", mof_props)

        anyio.run(_inspect)

        t = self.BASE_T
        db_path = _create_pit_db(
            self._tmpdir / "wrapper.db",
            yield_rows=[
                (t, 4.1, 4.6, 2.3, 2.3, "at_t"),
                (_ts(t, hours=2), 3.0, 3.1, 2.9, 0.2, "after_t"),
            ],
            ticks=_core_ticks_at(t, spot=152.0, vix=15.0)
            + _core_ticks_at(_ts(t, hours=2), spot=160.0, vix=30.0),
            events=[
                ("ev_in", _ts(t, hours=6), "USD", "US Retail Sales", "High", None, None, None),
                ("ev_out", _ts(t, hours=72), "USD", "US GDP", "High", None, None, None),
            ],
            headlines=[
                (
                    "h_in",
                    _ts(t, hours=-1),
                    "Reuters",
                    "Mimura says closely monitoring FX developments",
                    "https://example.com/in",
                    "monitoring closely",
                ),
                (
                    "h_old",
                    _ts(t, hours=-72),
                    "Reuters",
                    "Japan rate check old",
                    "https://example.com/old",
                    "rate check",
                ),
                (
                    "h_future",
                    _ts(t, hours=2),
                    "Reuters",
                    "Japan rate check future",
                    "https://example.com/future",
                    "rate check",
                ),
            ],
        )

        with mock.patch.dict(os.environ, {"FX_DB_PATH": str(db_path)}):
            spread = mcp_server._tool_get_latest_yield_spread(as_of_utc=t)
            self.assertEqual(spread["timestamp_utc"], t)
            self.assertAlmostEqual(spread["usdjpy_spot"], 152.0)

            events = mcp_server._tool_get_upcoming_macro_events(
                as_of_utc=t, past_hours=2, future_hours=12
            )
            self.assertEqual([e["event_hash"] for e in events], ["ev_in"])

            alerts = mcp_server._tool_get_recent_mof_alerts(
                min_tier=1, limit=10, as_of_utc=t, lookback_hours=24
            )
            self.assertEqual([a["url_hash"] for a in alerts], ["h_in"])

            ticks = mcp_server._tool_get_market_ticks_summary(symbols=["JPY=X"], as_of_utc=t)
            self.assertEqual(ticks["as_of_utc"], t)
            self.assertAlmostEqual(ticks["symbols"]["JPY=X"]["close"], 152.0)


class TestAgentNodesPointInTimeReplay(unittest.TestCase):
    """Verify specialist agent nodes strictly honor `point_in_time` and `mof_lookback_hours`."""

    T1 = "2026-11-10T08:00:00Z"

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="fx_pit_nodes_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_nodes_point_in_time_blocks_future_data_and_contrasts_live_mode(self) -> None:
        t1 = self.T1
        db_path = _create_pit_db(
            self._tmpdir / "contrast.db",
            yield_rows=[
                (_ts(t1, hours=-1), 4.40, 4.80, 2.40, 2.40, "hist_bull"),
                (_ts(t1, hours=1), 3.10, 3.30, 2.80, 0.50, "future_bear"),
            ],
            ticks=_core_ticks_at(_ts(t1, hours=-1), spot=152.00, vix=14.5)
            + _core_ticks_at(_ts(t1, hours=1), spot=159.80, vix=28.0),
            headlines=[
                (
                    "h_past_t1",
                    _ts(t1, hours=-2),
                    "Reuters",
                    "Mimura says closely monitoring FX moves",
                    "https://example.com/past_t1",
                    "monitoring closely",
                ),
                (
                    "h_future_t4",
                    _ts(t1, hours=1),
                    "Reuters",
                    "Bank of Japan conducts rate check with dealers",
                    "https://example.com/future_t4",
                    "rate check",
                ),
            ],
        )

        # PIT mode at T1 sees only T1-1h / T1-2h data -> BUY
        pit_state = create_initial_state(run_id="pit-contrast", timestamp_utc=t1)
        pit_macro = macro_analyst_node(pit_state, db_path=str(db_path), point_in_time=True)
        pit_merged = {**pit_state, **pit_macro, "audit_log": list(pit_macro["audit_log"])}
        pit_risk = risk_committee_node(pit_merged, db_path=str(db_path), point_in_time=True)

        self.assertEqual(pit_macro["yield_bias"], YieldBias.BULLISH_USD.value)
        self.assertAlmostEqual(pit_risk["spot_price"], 152.00)
        self.assertEqual(pit_risk["risk_regime"], RiskRegime.RISK_ON.value)
        self.assertFalse(pit_risk["risk_assessment"]["veto_trade"])
        self.assertEqual(pit_risk["trade_proposal"]["action"], TradeAction.BUY.value)
        self.assertEqual(pit_risk["trade_proposal"]["timestamp_utc"], t1)

        # Live mode (point_in_time=False) sees T1+1h future rows -> BEARISH_USD + Tier 4 veto -> HOLD
        live_state = create_initial_state(run_id="live-contrast", timestamp_utc=t1)
        live_macro = macro_analyst_node(live_state, db_path=str(db_path), point_in_time=False)
        live_merged = {**live_state, **live_macro, "audit_log": list(live_macro["audit_log"])}
        live_risk = risk_committee_node(live_merged, db_path=str(db_path), point_in_time=False)

        self.assertEqual(live_macro["yield_bias"], YieldBias.BEARISH_USD.value)
        self.assertAlmostEqual(live_risk["spot_price"], 159.80)
        self.assertTrue(live_risk["risk_assessment"]["veto_trade"])
        self.assertEqual(live_risk["trade_proposal"]["action"], TradeAction.HOLD.value)

    def test_risk_committee_defaults_mof_lookback_to_48h_when_pit_enabled(self) -> None:
        t1 = self.T1
        self.assertEqual(DEFAULT_PIT_MOF_LOOKBACK_HOURS, 48)
        db_path = _create_pit_db(
            self._tmpdir / "mof_default.db",
            ticks=_core_ticks_at(t1, spot=152.00, vix=15.0),
            headlines=[
                (
                    "h_t4_49h",
                    _ts(t1, hours=-49),
                    "Reuters",
                    "Bank of Japan conducts rate check 49h earlier",
                    "https://example.com/49h",
                    "rate check",
                ),
                (
                    "h_t1_5h",
                    _ts(t1, hours=-5),
                    "Reuters",
                    "Mimura says closely monitoring FX developments",
                    "https://example.com/5h",
                    "monitoring closely",
                ),
            ],
        )

        base_state = create_initial_state(run_id="mof-def", timestamp_utc=t1)
        base_state["yield_bias"] = YieldBias.BULLISH_USD.value

        # point_in_time=True, mof_lookback_hours=None -> defaults to 48h -> excludes -49h Tier 4
        pit_default = risk_committee_node(base_state, db_path=str(db_path), point_in_time=True)
        self.assertEqual(pit_default["risk_assessment"]["highest_mof_tier"], 1)
        self.assertFalse(pit_default["risk_assessment"]["veto_trade"])
        self.assertEqual(pit_default["trade_proposal"]["action"], TradeAction.BUY.value)

        # Explicit 72h lookback includes -49h Tier 4 -> veto & HOLD
        pit_72 = risk_committee_node(
            base_state, db_path=str(db_path), point_in_time=True, mof_lookback_hours=72
        )
        self.assertEqual(pit_72["risk_assessment"]["highest_mof_tier"], 4)
        self.assertTrue(pit_72["risk_assessment"]["veto_trade"])
        self.assertEqual(pit_72["trade_proposal"]["action"], TradeAction.HOLD.value)

        # Explicit mof_lookback_hours=0 must NOT be treated as falsy -> excludes -5h Tier 1 -> tier 0
        pit_zero = risk_committee_node(
            base_state, db_path=str(db_path), point_in_time=True, mof_lookback_hours=0
        )
        self.assertEqual(pit_zero["risk_assessment"]["highest_mof_tier"], 0)
        self.assertEqual(pit_zero["trade_proposal"]["action"], TradeAction.BUY.value)

        # Live mode (point_in_time=False) keeps unbounded default -> sees -49h Tier 4 -> veto & HOLD
        live_out = risk_committee_node(base_state, db_path=str(db_path), point_in_time=False)
        self.assertEqual(live_out["risk_assessment"]["highest_mof_tier"], 4)
        self.assertTrue(live_out["risk_assessment"]["veto_trade"])
        self.assertEqual(live_out["trade_proposal"]["action"], TradeAction.HOLD.value)

    def test_nodes_point_in_time_strict_timestamp_validation(self) -> None:
        t1 = self.T1
        db_path = _create_pit_db(
            self._tmpdir / "strict_ts.db",
            yield_rows=[(t1, 4.4, 4.8, 2.4, 2.4, "ok")],
            ticks=_core_ticks_at(t1, spot=152.0, vix=15.0)
            + _core_ticks_at(_ts(t1, hours=1), spot=160.0, vix=30.0),
        )

        # Missing / None / empty / invalid / bool timestamp_utc must raise BEFORE any MCP query
        bad_states = [
            {"run_id": "bad-missing", "audit_log": []},
            {"run_id": "bad-none", "timestamp_utc": None, "audit_log": []},
            {"run_id": "bad-empty", "timestamp_utc": "", "audit_log": []},
            {"run_id": "bad-invalid", "timestamp_utc": "not-a-date", "audit_log": []},
            {"run_id": "bad-bool-true", "timestamp_utc": True, "audit_log": []},
            {"run_id": "bad-bool-false", "timestamp_utc": False, "audit_log": []},
        ]
        for bad_state in bad_states:
            with self.subTest(run_id=bad_state["run_id"]):
                with mock.patch("fx_signal_reader.agent.nodes.get_latest_yield_spread") as m_spread:
                    with self.assertRaises(ValueError):
                        macro_analyst_node(bad_state, db_path=str(db_path), point_in_time=True)  # type: ignore[arg-type]
                    m_spread.assert_not_called()

                with mock.patch("fx_signal_reader.agent.nodes.get_market_ticks_summary") as m_ticks:
                    with self.assertRaises(ValueError):
                        risk_committee_node(bad_state, db_path=str(db_path), point_in_time=True)  # type: ignore[arg-type]
                    m_ticks.assert_not_called()

        # Non-Z JST timestamp normalizes to UTC Z and excludes T1+1h
        jst_state = {"run_id": "jst-run", "timestamp_utc": "2026-11-10T17:00:00+09:00", "audit_log": []}
        macro_out = macro_analyst_node(jst_state, db_path=str(db_path), point_in_time=True)  # type: ignore[arg-type]
        risk_out = risk_committee_node(
            {**jst_state, **macro_out}, db_path=str(db_path), point_in_time=True  # type: ignore[arg-type]
        )
        self.assertEqual(risk_out["trade_proposal"]["timestamp_utc"], t1)
        self.assertAlmostEqual(risk_out["spot_price"], 152.0)

        # Live mode (point_in_time=False) still falls back to UTC now when timestamp_utc is empty
        live_empty = {"run_id": "live-empty", "timestamp_utc": "", "audit_log": []}
        live_macro = macro_analyst_node(live_empty, db_path=str(db_path), point_in_time=False)  # type: ignore[arg-type]
        live_risk = risk_committee_node(
            {**live_empty, **live_macro}, db_path=str(db_path), point_in_time=False  # type: ignore[arg-type]
        )
        self.assertRegex(live_risk["trade_proposal"]["timestamp_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_macro_analyst_pit_catalyst_window_isolation(self) -> None:
        t1 = self.T1
        old_events = [
            (
                f"hist_ev_{i}",
                _ts(t1, hours=-24 * (25 - i)),
                "USD",
                f"Old High Event {i}",
                "High",
                None,
                None,
                "0.2%",
            )
            for i in range(12)
        ]
        db_path = _create_pit_db(
            self._tmpdir / "catalysts.db",
            yield_rows=[(t1, 4.4, 4.8, 2.4, 2.4, "pit")],
            ticks=_core_ticks_at(t1, spot=152.0, vix=15.0),
            events=old_events
            + [
                ("ev_fomc", _ts(t1, hours=12), "USD", "FOMC Policy Statement", "High", None, None, None),
                ("ev_cpi_far", _ts(t1, hours=72), "USD", "US CPI Far Future", "High", None, None, None),
            ],
        )

        quiet_db = _create_pit_db(
            self._tmpdir / "quiet.db",
            yield_rows=[(t1, 4.4, 4.8, 2.4, 2.4, "pit")],
            ticks=_core_ticks_at(t1, spot=152.0, vix=15.0),
        )

        state = create_initial_state(run_id="cat-pit", timestamp_utc=t1)
        pit_out = macro_analyst_node(state, db_path=str(db_path), point_in_time=True)
        quiet_out = macro_analyst_node(state, db_path=str(quiet_db), point_in_time=True)

        events_returned = pit_out["macro_assessment"]["upcoming_high_impact_events"]
        self.assertEqual([e["event_hash"] for e in events_returned], ["ev_fomc"])
        self.assertLess(
            pit_out["macro_assessment"]["confidence"],
            quiet_out["macro_assessment"]["confidence"],
        )
        self.assertIn("FOMC Policy Statement", pit_out["macro_assessment"]["policy_divergence_summary"])


class TestGraphPointInTimeAndLedgerIsolation(unittest.TestCase):
    """Verify multi-step graph replay, MoF 48h decay, factory forwarding, and ledger isolation."""

    T1 = "2026-11-10T08:00:00Z"

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="fx_pit_graph_"))
        self.live_ledger = PROJECT_ROOT / "data" / "paper_trades.jsonl"
        self._live_ledger_before = (
            self.live_ledger.read_bytes() if self.live_ledger.exists() else None
        )

    def tearDown(self) -> None:
        live_after = self.live_ledger.read_bytes() if self.live_ledger.exists() else None
        self.assertEqual(live_after, self._live_ledger_before, "Live paper_trades.jsonl was modified!")
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_multi_step_pit_graph_replay_with_mof_veto_and_48h_decay(self) -> None:
        t1 = self.T1
        t2 = _ts(t1, hours=12)
        t3 = _ts(t2, hours=49)
        mof_t4_ts = _ts(t2, hours=-1)  # Inside T2's 48h window, outside T3's 48h window

        db_path = _create_pit_db(
            self._tmpdir / "replay.db",
            yield_rows=[
                (t1, 4.4, 4.8, 2.4, 2.4, "step1"),
                (t2, 4.4, 4.8, 2.4, 2.4, "step2"),
                (t3, 4.4, 4.8, 2.4, 2.4, "step3"),
            ],
            ticks=_core_ticks_at(t1, spot=152.00, vix=14.5)
            + _core_ticks_at(t2, spot=152.30, vix=15.0)
            + _core_ticks_at(t3, spot=152.10, vix=14.8),
            headlines=[
                (
                    "h_t4_t2",
                    mof_t4_ts,
                    "Reuters",
                    "Bank of Japan conducts rate check with major dealers",
                    "https://example.com/t4_t2",
                    "rate check",
                )
            ],
        )
        temp_ledger = self._tmpdir / "replay_ledger.jsonl"

        graph = build_signal_graph(
            db_path=str(db_path),
            ledger_path=temp_ledger,
            interrupt_on_actionable=False,
            point_in_time=True,
            mof_lookback_hours=48,
            write_ledger=False,
        )

        # Step 1 (T1): clean -> BUY
        s1 = graph.invoke(
            create_initial_state(run_id="bt-1", timestamp_utc=t1),
            config={"configurable": {"thread_id": "bt-1"}},
        )
        validate_state_models(s1)
        self.assertEqual(s1["trade_proposal"]["action"], TradeAction.BUY.value)
        self.assertEqual(s1["trade_proposal"]["timestamp_utc"], t1)

        # Step 2 (T2): Tier-4 headline at T2-1h -> vetoed HOLD (SKIPPED_HOLD)
        s2 = graph.invoke(
            create_initial_state(run_id="bt-2", timestamp_utc=t2),
            config={"configurable": {"thread_id": "bt-2"}},
        )
        validate_state_models(s2)
        self.assertTrue(s2["risk_assessment"]["veto_trade"])
        self.assertEqual(s2["trade_proposal"]["action"], TradeAction.HOLD.value)
        self.assertEqual(s2["execution_receipt"]["status"], "SKIPPED_HOLD")

        # Step 3 (T3 = T2+49h): T2-1h headline is now 50h old -> decayed out -> BUY again
        s3 = graph.invoke(
            create_initial_state(run_id="bt-3", timestamp_utc=t3),
            config={"configurable": {"thread_id": "bt-3"}},
        )
        validate_state_models(s3)
        self.assertFalse(s3["risk_assessment"]["veto_trade"])
        self.assertEqual(s3["trade_proposal"]["action"], TradeAction.BUY.value)
        self.assertEqual(s3["trade_proposal"]["timestamp_utc"], t3)
        self.assertFalse(temp_ledger.exists())

    def test_write_ledger_false_blocks_executed_trade_persistence(self) -> None:
        t1 = self.T1
        db_path = _create_pit_db(
            self._tmpdir / "exec.db",
            yield_rows=[(t1, 4.4, 4.8, 2.4, 2.4, "exec")],
            ticks=_core_ticks_at(t1, spot=152.0, vix=14.5),
        )
        temp_default_ledger = self._tmpdir / "patched_default_paper_trades.jsonl"
        explicit_ledger = self._tmpdir / "explicit_paper_trades.jsonl"

        with mock.patch.object(agent_graph, "DEFAULT_LEDGER_PATH", temp_default_ledger):
            # (a) Direct paper_execution_node with write_ledger=False on APPROVED state
            approved_state = create_initial_state(run_id="direct-exec", timestamp_utc=t1)
            approved_state["approval_status"] = ApprovalStatus.APPROVED.value
            approved_state["spot_price"] = 152.0
            approved_state["trade_proposal"] = {
                "proposal_id": "prop-direct-exec",
                "timestamp_utc": t1,
                "pair": "USD/JPY",
                "action": TradeAction.BUY.value,
                "entry_price": 152.0,
                "stop_loss": 151.25,
                "take_profit": 153.50,
                "risk_reward_ratio": 2.0,
                "position_size_units": 10000,
                "rationale": "Test approved BUY proposal",
                "macro_bias": YieldBias.BULLISH_USD.value,
                "risk_regime": RiskRegime.RISK_ON.value,
                "intervention_risk_score": 5.0,
            }
            out_no_write = paper_execution_node(
                approved_state, ledger_path=explicit_ledger, write_ledger=False
            )
            receipt_no_write = ExecutionReceipt.model_validate(out_no_write["execution_receipt"])
            self.assertEqual(receipt_no_write.status, "EXECUTED")
            self.assertEqual(receipt_no_write.position_size_units, 10000)
            self.assertFalse(explicit_ledger.exists())

            # (b) Full graph with ledger_path=None, interrupt_on_actionable=True, write_ledger=False
            graph = build_signal_graph(
                db_path=str(db_path),
                ledger_path=None,
                interrupt_on_actionable=True,
                point_in_time=True,
                write_ledger=False,
            )
            cfg = {"configurable": {"thread_id": "pit-exec-no-ledger"}}
            paused = graph.invoke(create_initial_state(run_id="pit-exec-1", timestamp_utc=t1), config=cfg)
            self.assertEqual(paused["trade_proposal"]["action"], TradeAction.BUY.value)
            resumed = resume_with_decision(graph, cfg, approved=True, reviewer_notes="Backtest approve")
            receipt_resumed = ExecutionReceipt.model_validate(resumed["execution_receipt"])
            self.assertEqual(receipt_resumed.status, "EXECUTED")
            self.assertEqual(receipt_resumed.approval_status, ApprovalStatus.APPROVED)
            self.assertFalse(temp_default_ledger.exists())

            # (c) Contrast with write_ledger=True -> writes 1 JSONL line
            out_write = paper_execution_node(
                approved_state, ledger_path=explicit_ledger, write_ledger=True
            )
            self.assertEqual(out_write["execution_receipt"]["status"], "EXECUTED")
            self.assertTrue(explicit_ledger.exists())
            lines = explicit_ledger.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)

    def test_factory_helpers_preserve_defaults_and_propagate_flags(self) -> None:
        t1 = self.T1
        db_path = _create_pit_db(
            self._tmpdir / "factory.db",
            yield_rows=[
                (t1, 4.4, 4.8, 2.4, 2.4, "at_t1"),
                (_ts(t1, hours=2), 3.1, 3.2, 2.8, 0.4, "future"),
            ],
            ticks=_core_ticks_at(t1, spot=152.0, vix=15.0)
            + _core_ticks_at(_ts(t1, hours=2), spot=160.0, vix=30.0),
            headlines=[
                (
                    "h_60h",
                    _ts(t1, hours=-60),
                    "Reuters",
                    "Japan conducts rate check 60h prior",
                    "https://example.com/60h",
                    "rate check",
                )
            ],
        )

        # Default factory preserves live behavior (sees future row + unbounded MoF alert)
        live_macro_fn = make_macro_analyst_node(db_path=str(db_path))
        live_risk_fn = make_risk_committee_node(db_path=str(db_path))
        state = create_initial_state(run_id="fac-live", timestamp_utc=t1)
        m_live = live_macro_fn(state)
        r_live = live_risk_fn({**state, **m_live})
        self.assertEqual(m_live["yield_bias"], YieldBias.BEARISH_USD.value)
        self.assertTrue(r_live["risk_assessment"]["veto_trade"])

        # PIT factories propagate point_in_time=True and mof_lookback_hours=48
        pit_macro_fn = make_macro_analyst_node(
            db_path=str(db_path), point_in_time=True, mof_lookback_hours=48
        )
        pit_risk_fn = make_risk_committee_node(
            db_path=str(db_path), point_in_time=True, mof_lookback_hours=48
        )
        pit_state = create_initial_state(run_id="fac-pit", timestamp_utc=t1)
        m_pit = pit_macro_fn(pit_state)
        r_pit = pit_risk_fn({**pit_state, **m_pit})
        self.assertEqual(m_pit["yield_bias"], YieldBias.BULLISH_USD.value)
        self.assertFalse(r_pit["risk_assessment"]["veto_trade"])
        self.assertEqual(r_pit["trade_proposal"]["action"], TradeAction.BUY.value)

        # Paper execution factory propagates write_ledger=False
        ledger = self._tmpdir / "factory_ledger.jsonl"
        exec_fn = make_paper_execution_node(ledger_path=ledger, write_ledger=False)
        approved_state = {
            **pit_state,
            **m_pit,
            **r_pit,
            "approval_status": ApprovalStatus.APPROVED.value,
        }
        exec_out = exec_fn(approved_state)
        self.assertEqual(exec_out["execution_receipt"]["status"], "EXECUTED")
        self.assertFalse(ledger.exists())


class TestBacktestSimulatorPointInTimeIntegration(unittest.TestCase):
    """Verify run_backtest integrates CL=F and 2Y data in point-in-time mode, and falls back gracefully."""

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="fx_pit_sim_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_run_backtest_with_cl_f_and_2y_data(self) -> None:
        """Verify backtest simulation executes cleanly when CL=F and 2Y data are present."""
        t0 = "2026-11-17T00:00:00Z"
        ticks: list[tuple[str, str, float, float | None, float | None, float | None, float | None]] = []
        for i in range(12):
            ts_bar = _ts(t0, hours=i)
            ticks.extend(_core_ticks_at(ts_bar, spot=152.0 + (i * 0.05), vix=15.0, oil=71.0 + (i * 0.1)))

        yield_rows = [
            (t0, 4.10, 0.60, 3.50, 4.50, 1.10, 3.40, "FRED+MoF"),
            (_ts(t0, hours=6), 4.12, 0.61, 3.51, 4.52, 1.11, 3.41, "FRED+MoF"),
        ]
        db_path = _create_pit_db(self._tmpdir / "sim_with_data.db", yield_rows=yield_rows, ticks=ticks)
        cfg = BacktestConfig(pair="USD/JPY", step_hours=2, spread_pips=0.0)
        report = run_backtest(db_path=str(db_path), config=cfg)

        self.assertIsNotNone(report)
        self.assertEqual(report.config.pair, "USD/JPY")
        self.assertGreater(report.metrics.data_coverage["bars"], 0)
        self.assertGreaterEqual(report.metrics.data_coverage.get("oil_pct", 0.0), 0.0)
        self.assertGreaterEqual(report.metrics.data_coverage.get("spread_2y_pct", 0.0), 0.0)

    def test_run_backtest_graceful_fallback_when_cl_f_and_2y_absent(self) -> None:
        """Verify backtest simulation completes cleanly and gracefully when CL=F and 2Y data are absent."""
        t0 = "2026-11-17T00:00:00Z"
        ticks: list[tuple[str, str, float, float | None, float | None, float | None, float | None]] = []
        for i in range(8):
            ts_bar = _ts(t0, hours=i)
            # No oil ticks added
            ticks.extend(_core_ticks_at(ts_bar, spot=152.0 + (i * 0.05), vix=16.0))

        # Legacy yield row without 2Y data
        yield_rows = [
            (t0, None, 4.50, 1.10, 3.40, "Legacy"),
        ]
        db_path = _create_pit_db(self._tmpdir / "sim_legacy.db", yield_rows=yield_rows, ticks=ticks)
        cfg = BacktestConfig(pair="USD/JPY", step_hours=2, spread_pips=0.0)
        report = run_backtest(db_path=str(db_path), config=cfg)

        self.assertIsNotNone(report)
        self.assertEqual(report.metrics.data_coverage["bars"], 8)
        self.assertEqual(report.metrics.data_coverage.get("oil_pct", 0.0), 0.0)
        self.assertEqual(report.metrics.data_coverage.get("spread_2y_pct", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
