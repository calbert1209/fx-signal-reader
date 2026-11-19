"""
Unit and Integration Tests for the Custom Read-Only SQLite MCP Server (Task P3-01).

Covers:
  1. Strict SQLite read-only URI enforcement (`?mode=ro`) across INSERT/UPDATE/DELETE/CREATE/DROP.
  2. Path resolution precedence (`db_path` argument > `FX_DB_PATH` env > default `usdjpy_signals.db`).
  3. All 4 domain query tools against an isolated fixture database (including edge cases).
  4. All 4 domain query tools against the live `usdjpy_signals.db` database.
  5. MCPServer tool registration, schemas, read-only annotations, and live stdio client-server calls.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from fx_signal_reader.core import (
    DEFAULT_SCHEMA_PATH as SCHEMA_PATH,
    PROJECT_ROOT,
)
import fx_signal_reader.mcp.server as mcp_server
from fx_signal_reader.mcp.server import (
    CORE_TICKER_SYMBOLS,
    DEFAULT_DB_PATH,
    get_latest_yield_spread,
    get_market_ticks_summary,
    get_readonly_connection,
    get_recent_mof_alerts,
    get_upcoming_macro_events,
    resolve_db_path,
)


def _create_fixture_db(db_file: Path) -> None:
    """Create and populate a deterministic WAL-mode SQLite test database."""
    conn = sqlite3.connect(db_file)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    # yield_spreads: insert older row after newer row to ensure ORDER BY timestamp_utc DESC
    conn.executemany(
        """
        INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-11-18T08:00:00Z", 4.83, 5.12, 2.925, 2.195, "yfinance+mof_japan+fred"),
            ("2026-11-17T08:00:00Z", 4.80, 5.05, 2.900, 2.150, "yfinance+mof_japan+fred"),
        ],
    )

    # market_ticks: two bars for JPY=X and ^TNX, one bar for remaining core symbols
    conn.executemany(
        """
        INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("JPY=X", "2026-11-18T08:45:00Z", 159.20, 159.10, 159.25, 159.05, 0.0),
            ("JPY=X", "2026-11-18T09:00:00Z", 159.45, 159.20, 159.50, 159.18, 0.0),
            ("^TNX", "2026-11-18T08:45:00Z", 5.10, 5.08, 5.11, 5.07, 0.0),
            ("^TNX", "2026-11-18T09:00:00Z", 5.12, 5.10, 5.13, 5.09, 0.0),
            ("^GSPC", "2026-11-18T09:00:00Z", 7350.0, 7330.0, 7360.0, 7325.0, 1250000.0),
            ("^N225", "2026-11-18T06:00:00Z", 59150.0, 59000.0, 59200.0, 58950.0, 500000.0),
            ("^VIX", "2026-11-18T09:00:00Z", 23.5, 24.0, 24.2, 23.4, 0.0),
            ("GC=F", "2026-11-18T09:00:00Z", 3770.0, 3765.0, 3775.0, 3760.0, 2000.0),
        ],
    )

    # macro_events: mixed currencies and impact levels out of chronological order
    conn.executemany(
        """
        INSERT INTO macro_events (event_hash, timestamp_utc, currency, title, impact, forecast, previous, actual)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("ev3", "2026-11-20T13:30:00Z", "USD", "US Core CPI m/m", "High", "0.3%", "0.3%", None),
            ("ev1", "2026-11-18T23:50:00Z", "JPY", "BoJ Monetary Policy Statement", "High", None, None, None),
            ("ev2", "2026-11-19T05:00:00Z", "JPY", "All Industry Activity Index", "Low", "0.2%", "0.1%", None),
            ("ev4", "2026-11-19T14:00:00Z", "USD", "Existing Home Sales", "Medium", "4.10M", "4.05M", None),
        ],
    )

    # news_headlines: spanning Tiers 0-4 plus unmatched empty keyword row
    conn.executemany(
        """
        INSERT INTO news_headlines (url_hash, published_utc, source, title, url, matched_keywords)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "h_unmatched",
                "2026-11-18T09:30:00Z",
                "MarketWatch",
                "S&P 500 opens slightly higher on tech earnings",
                "https://example.com/unmatched",
                "",
            ),
            (
                "h_tier0",
                "2026-11-18T09:20:00Z",
                "Bank of Japan",
                "Bank of Japan releases October account balances",
                "https://example.com/tier0",
                "Bank of Japan",
            ),
            (
                "h_tier1",
                "2026-11-18T09:10:00Z",
                "Reuters",
                "Mimura says closely monitoring FX developments",
                "https://example.com/tier1",
                "Mimura, monitoring closely",
            ),
            (
                "h_tier2",
                "2026-11-18T08:50:00Z",
                "Nikkei Asia",
                "MoF warns against rapid moves and one-sided yen depreciation",
                "https://example.com/tier2",
                "MoF, rapid moves, one-sided",
            ),
            (
                "h_tier3",
                "2026-11-18T08:30:00Z",
                "FXStreet",
                "Tokyo stands ready to respond to excessive volatility",
                "https://example.com/tier3",
                "stand ready, excessive volatility",
            ),
            (
                "h_tier4_new",
                "2026-11-18T08:15:00Z",
                "Reuters",
                "Japan conducts yen rate check as USD/JPY tests 160",
                "https://example.com/tier4_new",
                "rate check",
            ),
            (
                "h_tier4_old",
                "2026-11-18T07:00:00Z",
                "Ministry of Finance Japan",
                "Foreign Exchange Intervention Operations Report",
                "https://example.com/tier4_old",
                "intervention operations",
            ),
        ],
    )

    conn.commit()
    conn.close()


class TestReadOnlyURIEnforcement(unittest.TestCase):
    """Verify strict SQLite read-only URI mode prevents all mutations."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "readonly_test.db"
        _create_fixture_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_write_operations_raise_operational_error(self) -> None:
        conn = get_readonly_connection(self.db_path)
        try:
            write_statements = [
                "INSERT INTO yield_spreads (timestamp_utc, source) VALUES ('2026-11-19T00:00:00Z', 'rogue')",
                "UPDATE yield_spreads SET spread_10y = 9.99",
                "DELETE FROM macro_events",
                "CREATE TABLE rogue_table (id INTEGER PRIMARY KEY)",
                "DROP TABLE news_headlines",
            ]
            for stmt in write_statements:
                with self.subTest(stmt=stmt):
                    with self.assertRaises(sqlite3.OperationalError) as ctx:
                        conn.execute(stmt)
                    self.assertIn("readonly", str(ctx.exception).lower())
        finally:
            conn.close()

    def test_missing_database_raises_operational_error(self) -> None:
        missing = Path(self._tmpdir.name) / "does_not_exist.db"
        with self.assertRaises(sqlite3.OperationalError):
            get_readonly_connection(missing)

    def test_resolve_db_path_precedence(self) -> None:
        explicit = Path(self._tmpdir.name) / "explicit.db"
        env_db = Path(self._tmpdir.name) / "env.db"
        with mock.patch.dict(os.environ, {"FX_DB_PATH": str(env_db)}):
            self.assertEqual(resolve_db_path(str(explicit)), explicit.resolve())
            self.assertEqual(resolve_db_path(), env_db.resolve())
        with mock.patch.dict(os.environ, {"FX_DB_PATH": ""}):
            self.assertEqual(resolve_db_path(), DEFAULT_DB_PATH.resolve())


class TestDomainToolsWithTempDB(unittest.TestCase):
    """Validate all 4 SQLite MCP domain functions against deterministic fixture data."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "fixture.db"
        _create_fixture_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_get_latest_yield_spread(self) -> None:
        res = get_latest_yield_spread(db_path=str(self.db_path))
        self.assertEqual(res["timestamp_utc"], "2026-11-18T08:00:00Z")
        self.assertAlmostEqual(res["us_2y"], 4.83)
        self.assertAlmostEqual(res["us_10y"], 5.12)
        self.assertAlmostEqual(res["jp_10y"], 2.925)
        self.assertAlmostEqual(res["spread_10y"], 2.195)
        self.assertAlmostEqual(res["spread_bps"], 219.5)
        self.assertAlmostEqual(res["us_2s10s_slope"], 0.29)
        self.assertEqual(res["source"], "yfinance+mof_japan+fred")
        self.assertAlmostEqual(res["usdjpy_spot"], 159.45)
        self.assertEqual(res["usdjpy_timestamp_utc"], "2026-11-18T09:00:00Z")

    def test_get_latest_yield_spread_empty_db(self) -> None:
        empty_db = Path(self._tmpdir.name) / "empty.db"
        conn = sqlite3.connect(empty_db)
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.close()

        res = get_latest_yield_spread(db_path=str(empty_db))
        self.assertIsNone(res["timestamp_utc"])
        self.assertIsNone(res["spread_10y"])
        self.assertIsNone(res["spread_bps"])
        self.assertIsNone(res["usdjpy_spot"])

    def test_get_upcoming_macro_events_filters_and_order(self) -> None:
        all_events = get_upcoming_macro_events(db_path=str(self.db_path))
        self.assertEqual([e["event_hash"] for e in all_events], ["ev1", "ev2", "ev4", "ev3"])

        jpy_events = get_upcoming_macro_events(currency="jpy", db_path=str(self.db_path))
        self.assertEqual([e["event_hash"] for e in jpy_events], ["ev1", "ev2"])

        high_usd = get_upcoming_macro_events(currency="USD", impact="high", db_path=str(self.db_path))
        self.assertEqual(len(high_usd), 1)
        self.assertEqual(high_usd[0]["title"], "US Core CPI m/m")

        limited = get_upcoming_macro_events(limit=2, db_path=str(self.db_path))
        self.assertEqual(len(limited), 2)
        self.assertEqual(get_upcoming_macro_events(limit=0, db_path=str(self.db_path)), [])

    def test_get_recent_mof_alerts_tiers_and_ordering(self) -> None:
        # min_tier=1 excludes empty matched_keywords and Tier 0 entity-only matches
        tier1_plus = get_recent_mof_alerts(min_tier=1, limit=20, db_path=str(self.db_path))
        self.assertEqual(
            [a["url_hash"] for a in tier1_plus],
            ["h_tier1", "h_tier2", "h_tier3", "h_tier4_new", "h_tier4_old"],
        )
        self.assertEqual([a["tier"] for a in tier1_plus], [1, 2, 3, 4, 4])

        # min_tier=3 filters strictly to Tiers 3-4
        tier3_plus = get_recent_mof_alerts(min_tier=3, limit=20, db_path=str(self.db_path))
        self.assertEqual([a["url_hash"] for a in tier3_plus], ["h_tier3", "h_tier4_new", "h_tier4_old"])

        # min_tier=4 with limit=1 picks newest Tier 4 even though Tier 1-3 rows are newer
        tier4_top1 = get_recent_mof_alerts(min_tier=4, limit=1, db_path=str(self.db_path))
        self.assertEqual(len(tier4_top1), 1)
        self.assertEqual(tier4_top1[0]["url_hash"], "h_tier4_new")
        self.assertEqual(tier4_top1[0]["severity"], "CRITICAL")

    def test_get_market_ticks_summary_core_and_custom(self) -> None:
        summary = get_market_ticks_summary(db_path=str(self.db_path))
        self.assertEqual(summary["symbol_count"], 6)
        self.assertEqual(summary["missing_symbols"], [])
        self.assertEqual(summary["as_of_utc"], "2026-11-18T09:00:00Z")

        for sym in CORE_TICKER_SYMBOLS:
            self.assertIn(sym, summary["symbols"])

        # Two bars -> bar_change is vs prev_close (159.45 - 159.20 = 0.25)
        jpy = summary["symbols"]["JPY=X"]
        self.assertAlmostEqual(jpy["close"], 159.45)
        self.assertAlmostEqual(jpy["open"], 159.20)
        self.assertAlmostEqual(jpy["prev_close"], 159.20)
        self.assertAlmostEqual(jpy["bar_change"], 0.25)

        # Single bar -> bar_change falls back to close - open (23.5 - 24.0 = -0.5)
        vix = summary["symbols"]["^VIX"]
        self.assertIsNone(vix["prev_close"])
        self.assertAlmostEqual(vix["bar_change"], -0.5)

        # Subset + missing symbol
        subset = get_market_ticks_summary(symbols=["^TNX", "DOES_NOT_EXIST"], db_path=str(self.db_path))
        self.assertEqual(subset["symbol_count"], 1)
        self.assertEqual(subset["missing_symbols"], ["DOES_NOT_EXIST"])
        self.assertAlmostEqual(subset["symbols"]["^TNX"]["bar_change"], 0.02)


class TestDomainToolsWithLiveDB(unittest.TestCase):
    """Validate all 4 tools against the live production SQLite DB (`usdjpy_signals.db`)."""

    @classmethod
    def setUpClass(cls) -> None:
        if not DEFAULT_DB_PATH.exists():
            raise unittest.SkipTest(f"Live DB not found at {DEFAULT_DB_PATH}")

    def test_live_yield_spread(self) -> None:
        res = get_latest_yield_spread()
        self.assertIsNotNone(res["timestamp_utc"])
        self.assertIsNotNone(res["us_10y"])
        self.assertIsNotNone(res["jp_10y"])
        self.assertIsNotNone(res["spread_10y"])
        self.assertAlmostEqual(res["spread_bps"], round(res["spread_10y"] * 100.0, 2))
        self.assertIsNotNone(res["usdjpy_spot"])

    def test_live_macro_events(self) -> None:
        events = get_upcoming_macro_events(limit=10)
        self.assertGreater(len(events), 0)
        timestamps = [e["timestamp_utc"] for e in events]
        self.assertEqual(timestamps, sorted(timestamps))

        high_events = get_upcoming_macro_events(impact="High", limit=10)
        self.assertGreater(len(high_events), 0)
        self.assertTrue(all(e["impact"] == "High" for e in high_events))

    def test_live_mof_alerts(self) -> None:
        alerts = get_recent_mof_alerts(min_tier=1, limit=20)
        self.assertGreater(len(alerts), 0)
        self.assertTrue(all(1 <= a["tier"] <= 4 for a in alerts))
        pub_dates = [a["published_utc"] for a in alerts]
        self.assertEqual(pub_dates, sorted(pub_dates, reverse=True))

        tier4 = get_recent_mof_alerts(min_tier=4, limit=10)
        self.assertGreater(len(tier4), 0)
        self.assertTrue(all(a["tier"] == 4 for a in tier4))

    def test_live_market_ticks_summary(self) -> None:
        summary = get_market_ticks_summary()
        self.assertEqual(summary["symbol_count"], len(CORE_TICKER_SYMBOLS))
        self.assertEqual(summary["missing_symbols"], [])
        for sym in CORE_TICKER_SYMBOLS:
            entry = summary["symbols"][sym]
            self.assertIsNotNone(entry["close"])
            self.assertIsNotNone(entry["timestamp_utc"])


class TestMCPServerRegistrationAndStdio(unittest.TestCase):
    """Verify MCPServer registration and end-to-end stdio protocol tool execution."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "stdio_fixture.db"
        _create_fixture_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_mcp_server_tool_registration_and_annotations(self) -> None:
        self.assertEqual(mcp_server.mcp_server.name, "usdjpy-sqlite-signals")

        async def _check() -> None:
            tools = await mcp_server.mcp_server.list_tools()
            tool_map = {t.name: t for t in tools}
            expected_names = {
                "get_latest_yield_spread",
                "get_upcoming_macro_events",
                "get_recent_mof_alerts",
                "get_market_ticks_summary",
                "get_market_trend_summary",
            }
            self.assertEqual(set(tool_map.keys()), expected_names)

            for name, tool in tool_map.items():
                props = tool.input_schema.get("properties", {})
                self.assertNotIn("db_path", props, f"{name} should not leak db_path")
                self.assertIsNotNone(tool.annotations)
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)

        anyio.run(_check)

    def test_live_stdio_client_with_fixture_db(self) -> None:
        async def _run_stdio() -> None:
            env = dict(os.environ)
            env["FX_DB_PATH"] = str(self.db_path)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "fx_signal_reader.mcp.server"],
                env=env,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    init_res = await session.initialize()
                    self.assertEqual(init_res.server_info.name, "usdjpy-sqlite-signals")

                    listed = await session.list_tools()
                    self.assertEqual(
                        {t.name for t in listed.tools},
                        {
                            "get_latest_yield_spread",
                            "get_upcoming_macro_events",
                            "get_recent_mof_alerts",
                            "get_market_ticks_summary",
                            "get_market_trend_summary",
                        },
                    )

                    # 1. Zero-argument call: get_latest_yield_spread
                    spread_res = await session.call_tool("get_latest_yield_spread", {})
                    self.assertFalse(spread_res.is_error)
                    spread_data = json.loads(spread_res.content[0].text)
                    self.assertAlmostEqual(spread_data["spread_bps"], 219.5)
                    self.assertAlmostEqual(spread_data["usdjpy_spot"], 159.45)

                    # 2. Filtered call: get_upcoming_macro_events
                    ev_res = await session.call_tool(
                        "get_upcoming_macro_events",
                        {"currency": "JPY", "impact": "High", "limit": 5},
                    )
                    self.assertFalse(ev_res.is_error)
                    ev_data = ev_res.structured_content["result"]
                    self.assertEqual(len(ev_data), 1)
                    self.assertEqual(ev_data[0]["title"], "BoJ Monetary Policy Statement")

                    # 3. Tiered alerts call: get_recent_mof_alerts
                    alert_res = await session.call_tool(
                        "get_recent_mof_alerts",
                        {"min_tier": 3, "limit": 10},
                    )
                    self.assertFalse(alert_res.is_error)
                    alert_data = alert_res.structured_content["result"]
                    self.assertEqual(len(alert_data), 3)
                    self.assertTrue(all(a["tier"] >= 3 for a in alert_data))

                    # 4. Market ticks summary call: get_market_ticks_summary
                    ticks_res = await session.call_tool(
                        "get_market_ticks_summary",
                        {"symbols": ["JPY=X", "^VIX"]},
                    )
                    self.assertFalse(ticks_res.is_error)
                    ticks_data = json.loads(ticks_res.content[0].text)
                    self.assertEqual(ticks_data["symbol_count"], 2)
                    self.assertAlmostEqual(ticks_data["symbols"]["JPY=X"]["bar_change"], 0.25)

        anyio.run(_run_stdio)

    def test_live_stdio_client_default_live_db(self) -> None:
        if not DEFAULT_DB_PATH.exists():
            self.skipTest(f"Live DB not found at {DEFAULT_DB_PATH}")

        async def _run_live_stdio() -> None:
            env = {k: v for k, v in os.environ.items() if k != "FX_DB_PATH"}
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "fx_signal_reader.mcp.server"],
                env=env,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    res = await session.call_tool("get_latest_yield_spread", {})
                    self.assertFalse(res.is_error)
                    payload = json.loads(res.content[0].text)
                    self.assertIsNotNone(payload["spread_10y"])
                    self.assertIsNotNone(payload["usdjpy_spot"])

        anyio.run(_run_live_stdio)


if __name__ == "__main__":
    unittest.main(verbosity=2)
