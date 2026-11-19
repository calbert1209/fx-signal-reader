"""Offline mocked unit tests for historical data backfill (Task P6-02).

All network and Yahoo Finance calls are mocked; zero live network calls are made,
and live databases (`data/usdjpy_signals.db`) are never touched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

from fx_signal_reader.backtest import backfill as backfill_mod
from fx_signal_reader.backtest.backfill import (
    DEFAULT_BACKTEST_DB_PATH,
    FRED_OBSERVATIONS_URL,
    MOF_JGB_ALL_URL,
    MOF_JGB_CURRENT_URL,
    backfill_historical_data,
    backfill_jgb_yields,
    backfill_market_ticks,
    backfill_yield_spreads,
)
from fx_signal_reader.collectors.market_ticks import MarketTicksCollector
from fx_signal_reader.core import DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH, PROJECT_ROOT
from fx_signal_reader.mcp.server import (
    get_latest_yield_spread,
    get_market_ticks_summary,
)


def _make_mock_response(
    *,
    content_bytes: bytes | None = None,
    text: str | None = None,
    json_data: dict | None = None,
    status_code: int = 200,
) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    if content_bytes is not None:
        resp.content = content_bytes
        resp.text = content_bytes.decode("cp932")
    elif text is not None:
        resp.text = text
        resp.content = text.encode("utf-8")
    if json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status = mock.MagicMock()
    if status_code >= 400:
        import requests

        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    return resp


def _sample_mof_current_csv() -> bytes:
    lines = [
        "国債金利情報 (令和8年11月),,,,,,,,,,,,,,,",
        "基準日,1年,2年,3年,4年,5年,6年,7年,8年,9年,10年,15年,20年,25年,30年,40年",
        "R8.11.2,0.50,0.60,0.70,0.80,0.90,0.95,1.00,1.05,1.10,1.150,1.50,1.80,2.00,2.20,2.40",
        "R8.11.4,0.50,0.60,0.70,0.80,0.90,0.95,1.00,1.05,1.10,-,1.50,1.80,2.00,2.20,2.40",
        "R8.11.17,0.55,0.65,0.75,0.85,0.95,1.00,1.05,1.10,1.15,2.925,1.55,1.85,2.05,2.25,2.45",
        ",,,,,,,,,,,,,,,",
        "※最新のcsvデータがダウンロードできない場合はブラウザのキャッシュを削除してください,,,,,,,,,,,,,,,",
    ]
    return "\n".join(lines).encode("cp932")


def _sample_mof_all_csv() -> bytes:
    lines = [
        "国債金利情報 (全期間),,,,,,,,,,,,,,,",
        "基準日,1年,2年,3年,4年,5年,6年,7年,8年,9年,10年,15年,20年,25年,30年,40年",
        "S49.9.24,-,-,-,-,-,-,-,-,-,-,-,-,-,-,-,-",
        "S60.1.10,5.0,5.1,5.2,5.3,5.4,5.5,5.6,5.7,5.8,6.500,6.8,7.0,7.1,7.2,-",
        "H30.12.31,0.01,0.02,0.03,0.04,0.05,0.06,0.07,0.08,0.09,0.105,0.3,0.5,0.7,0.8,0.9",
        "2024/5/1,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,0.890,1.2,1.6,1.9,2.0,2.2",
        "2026-10-30,0.4,0.5,0.6,0.7,0.8,0.85,0.9,0.95,1.0,1.080,1.4,1.7,1.9,2.1,2.3",
        # Deliberate overlap with jgbcm.csv (1.150 should win over 9.999)
        "R8.11.2,0.5,0.6,0.7,0.8,0.9,0.95,1.0,1.05,1.1,9.999,1.5,1.8,2.0,2.2,2.4",
    ]
    return "\n".join(lines).encode("cp932")


class TestBacktestBackfill(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fx_backfill_test_")
        self.tmp_dir = Path(self._tmpdir.name)
        self.db_path = self.tmp_dir / "backtest_test.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _init_empty_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.row_factory = sqlite3.Row
        return conn

    def test_default_db_path_and_live_db_isolation(self) -> None:
        self.assertEqual(
            DEFAULT_BACKTEST_DB_PATH,
            PROJECT_ROOT / "data" / "backtest_signals.db",
        )
        self.assertNotEqual(DEFAULT_BACKTEST_DB_PATH, DEFAULT_DB_PATH)

        live_stat_before = (
            DEFAULT_DB_PATH.stat() if DEFAULT_DB_PATH.exists() else None
        )

        # Run full backfill against an isolated temp db_path and verify live DB is untouched
        df = pd.DataFrame(
            {
                "Open": [155.0],
                "High": [155.5],
                "Low": [154.8],
                "Close": [155.2],
                "Volume": [0.0],
            },
            index=pd.DatetimeIndex(["2026-11-17 12:00:00"], tz="UTC"),
        )
        mock_ticker = mock.MagicMock()
        mock_ticker.history.return_value = df

        def _fake_get(url: str, **kwargs):
            if url == MOF_JGB_CURRENT_URL:
                return _make_mock_response(content_bytes=_sample_mof_current_csv())
            if url == MOF_JGB_ALL_URL:
                return _make_mock_response(content_bytes=_sample_mof_all_csv())
            raise AssertionError(f"Unexpected URL: {url}")

        with (
            mock.patch.object(backfill_mod.yf, "Ticker", return_value=mock_ticker),
            mock.patch(
                "fx_signal_reader.collectors.base.requests.get",
                side_effect=_fake_get,
            ),
            mock.patch.dict(os.environ, {"FRED_API_KEY": ""}, clear=False),
        ):
            summary = backfill_historical_data(
                db_path=self.db_path,
                start_date="2026-11-16",
                end_date="2026-11-18",
                interval="1h",
            )

        self.assertEqual(summary["db_path"], str(self.db_path))
        self.assertGreater(summary["db_counts"]["market_ticks"], 0)
        self.assertGreater(summary["db_counts"]["macro_events"], 0)
        self.assertGreater(summary["db_counts"]["news_headlines"], 0)

        if live_stat_before is not None:
            live_stat_after = DEFAULT_DB_PATH.stat()
            self.assertEqual(live_stat_before.st_mtime_ns, live_stat_after.st_mtime_ns)
            self.assertEqual(live_stat_before.st_size, live_stat_after.st_size)

    def test_validation_errors_on_bad_dates_or_interval(self) -> None:
        with self.assertRaises(ValueError):
            backfill_historical_data(
                db_path=self.db_path,
                start_date="2026-11-18",
                end_date="2026-11-10",
            )
        with self.assertRaises(ValueError):
            backfill_market_ticks(
                db_path=self.db_path,
                start_date="not-a-date",
                end_date="2026-11-10",
            )
        with self.assertRaises(ValueError):
            backfill_market_ticks(
                db_path=self.db_path,
                start_date="2026-11-01",
                end_date="2026-11-10",
                interval="4h",
            )
        self.assertFalse(self.db_path.exists())

    def test_mof_csv_date_formats_and_merge_fallback(self) -> None:
        calls: list[str] = []

        def _fake_get(url: str, **kwargs):
            calls.append(url)
            if url == MOF_JGB_CURRENT_URL:
                return _make_mock_response(content_bytes=_sample_mof_current_csv())
            if url == MOF_JGB_ALL_URL:
                return _make_mock_response(content_bytes=_sample_mof_all_csv())
            raise AssertionError(f"Unexpected URL: {url}")

        with mock.patch(
            "fx_signal_reader.collectors.base.requests.get",
            side_effect=_fake_get,
        ):
            # 1. Range entirely within jgbcm.csv -> should NOT fetch jgbcm_all.csv
            res_current = backfill_jgb_yields(
                db_path=self.db_path,
                start_date="2026-11-02",
                end_date="2026-11-18",
            )
            self.assertFalse(res_current["used_historical_csv"])
            self.assertEqual(calls, [MOF_JGB_CURRENT_URL])
            self.assertEqual(res_current["dates_covered"], 2)  # 11-02 and 11-17 (11-04 has '-')
            self.assertEqual(res_current["rows_inserted"], 4)

            # 2. Range starting in Showa era -> fetches jgbcm_all.csv & merges
            calls.clear()
            res_all = backfill_jgb_yields(
                db_path=self.db_path,
                start_date="1974-09-01",
                end_date="2026-11-18",
            )
            self.assertTrue(res_all["used_historical_csv"])
            self.assertEqual(calls, [MOF_JGB_CURRENT_URL, MOF_JGB_ALL_URL])
            # Dates: S60.1.10 (1985-01-10), H30.12.31 (2018-12-31), 2024/5/1, 2026-10-30, R8.11.2, R8.11.17
            self.assertEqual(res_all["dates_covered"], 6)
            self.assertEqual(res_all["rows_inserted"], 12)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            jgb_rows = conn.execute(
                "SELECT symbol, timestamp_utc, price, open, high, low, volume "
                "FROM market_ticks WHERE symbol = 'JGB10Y' ORDER BY timestamp_utc ASC"
            ).fetchall()
            jb_rows = conn.execute(
                "SELECT symbol, timestamp_utc, price FROM market_ticks "
                "WHERE symbol = '^JB10YR' ORDER BY timestamp_utc ASC"
            ).fetchall()
        finally:
            conn.close()

        by_ts = {r["timestamp_utc"]: r for r in jgb_rows}
        self.assertIn("1985-01-10T00:00:00Z", by_ts)
        self.assertIn("2018-12-31T00:00:00Z", by_ts)
        self.assertIn("2024-05-01T00:00:00Z", by_ts)
        self.assertIn("2026-10-30T00:00:00Z", by_ts)
        self.assertIn("2026-11-02T00:00:00Z", by_ts)
        self.assertIn("2026-11-17T00:00:00Z", by_ts)
        self.assertNotIn("1974-09-24T00:00:00Z", by_ts)  # '-' cell skipped
        self.assertNotIn("2026-11-04T00:00:00Z", by_ts)  # '-' cell skipped

        # Precedence check: jgbcm.csv (1.150) wins over jgbcm_all.csv (9.999)
        nov2 = by_ts["2026-11-02T00:00:00Z"]
        self.assertAlmostEqual(nov2["price"], 1.150)
        self.assertAlmostEqual(nov2["open"], 1.150)
        self.assertAlmostEqual(nov2["high"], 1.150)
        self.assertAlmostEqual(nov2["low"], 1.150)
        self.assertEqual(nov2["volume"], 0.0)

        self.assertEqual(len(jb_rows), len(jgb_rows))
        self.assertEqual(
            [(r["timestamp_utc"], r["price"]) for r in jb_rows],
            [(r["timestamp_utc"], r["price"]) for r in jgb_rows],
        )

    def test_backfill_market_ticks_tz_nan_inclusive_end_and_idempotency(self) -> None:
        # Multi-timezone DataFrames + MultiIndex + NaN Close / NaN Volume / NaN Open
        df_jpy = pd.DataFrame(
            {
                "Open": [154.0, float("nan"), 154.5, 155.0],
                "High": [154.2, 154.6, 154.7, 155.1],
                "Low": [153.9, 154.1, 154.4, 154.9],
                "Close": [154.1, 154.4, float("nan"), 155.05],
                "Volume": [float("nan"), 100.0, 50.0, 200.0],
            },
            index=pd.DatetimeIndex(
                [
                    "2026-11-17 08:00:00",
                    "2026-11-18 22:00:00",
                    "2026-11-18 23:00:00",  # NaN Close -> skipped
                    "2026-11-19 01:00:00",  # > 2026-11-18T23:59:59Z -> excluded
                ],
                tz="Europe/London",
            ),
        )
        # MultiIndex DataFrame for ^N225 in Asia/Tokyo (+09:00)
        cols_mi = pd.MultiIndex.from_tuples(
            [("Open", "^N225"), ("High", "^N225"), ("Low", "^N225"), ("Close", "^N225"), ("Volume", "^N225")]
        )
        df_n225 = pd.DataFrame(
            [[39000.0, 39100.0, 38900.0, 39050.0, float("nan")]],
            columns=cols_mi,
            index=pd.DatetimeIndex(["2026-11-18 09:00:00"], tz="Asia/Tokyo"),  # 2026-11-18T00:00:00Z
        )

        history_calls: list[tuple[str, str, str, str]] = []

        def _ticker_factory(symbol: str):
            t = mock.MagicMock()

            def _hist(start: str, end: str, interval: str):
                history_calls.append((symbol, start, end, interval))
                return df_jpy if symbol == "JPY=X" else df_n225

            t.history.side_effect = _hist
            return t

        with mock.patch.object(backfill_mod.yf, "Ticker", side_effect=_ticker_factory):
            res1 = backfill_market_ticks(
                db_path=self.db_path,
                start_date="2026-11-17",
                end_date="2026-11-18",
                interval="1h",
                symbols=["JPY=X", "^N225"],
            )
            res2 = backfill_market_ticks(
                db_path=self.db_path,
                start_date="2026-11-17",
                end_date="2026-11-18",
                interval="1h",
                symbols=["JPY=X", "^N225"],
            )

        # Exclusive Yahoo end date must receive end="2026-11-19" for inclusive "2026-11-18"
        self.assertIn(("JPY=X", "2026-11-17", "2026-11-19", "1h"), history_calls)
        self.assertEqual(res1["symbols"]["JPY=X"]["rows_inserted"], 2)
        self.assertEqual(res1["symbols"]["^N225"]["rows_inserted"], 1)
        self.assertEqual(res2["total_rows_inserted"], 3)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT symbol, timestamp_utc, price, open, volume "
                "FROM market_ticks ORDER BY symbol, timestamp_utc ASC"
            ).fetchall()
        finally:
            conn.close()

        # Idempotent: still exactly 3 rows total
        self.assertEqual(len(rows), 3)
        jpy_rows = [r for r in rows if r["symbol"] == "JPY=X"]
        self.assertEqual(
            [r["timestamp_utc"] for r in jpy_rows],
            ["2026-11-17T08:00:00Z", "2026-11-18T22:00:00Z"],
        )
        self.assertEqual(jpy_rows[0]["volume"], 0.0)
        self.assertAlmostEqual(jpy_rows[1]["open"], 154.4)  # fallback to Close

        n225_rows = [r for r in rows if r["symbol"] == "^N225"]
        self.assertEqual(n225_rows[0]["timestamp_utc"], "2026-11-18T00:00:00Z")
        self.assertEqual(n225_rows[0]["volume"], 0.0)

    def test_backfill_market_ticks_interval_clamping(self) -> None:
        now = datetime.now(timezone.utc).date()
        recorded_starts: list[tuple[str, str]] = []

        def _ticker_factory(symbol: str):
            t = mock.MagicMock()

            def _hist(start: str, end: str, interval: str):
                recorded_starts.append((interval, start))
                return pd.DataFrame()

            t.history.side_effect = _hist
            return t

        with mock.patch.object(backfill_mod.yf, "Ticker", side_effect=_ticker_factory):
            # 15m > 59d -> clamped with WARNING
            with self.assertLogs("BacktestBackfill", level="WARNING") as cm15:
                res_15m = backfill_market_ticks(
                    db_path=self.db_path,
                    start_date=(now - timedelta(days=120)).isoformat(),
                    end_date=(now - timedelta(days=2)).isoformat(),
                    interval="15m",
                    symbols=["JPY=X"],
                )
            self.assertTrue(res_15m["clamped"])
            self.assertTrue(any("15m" in msg for msg in cm15.output))
            expected_min_15m = (now - timedelta(days=59)).isoformat()
            self.assertGreaterEqual(res_15m["effective_start_date"], expected_min_15m)
            self.assertIn(("15m", res_15m["effective_start_date"]), recorded_starts)

            # 1h > 729d -> clamped with WARNING
            with self.assertLogs("BacktestBackfill", level="WARNING") as cm1h:
                res_1h = backfill_market_ticks(
                    db_path=self.db_path,
                    start_date=(now - timedelta(days=900)).isoformat(),
                    end_date=now.isoformat(),
                    interval="1h",
                    symbols=["JPY=X"],
                )
            self.assertTrue(res_1h["clamped"])
            self.assertTrue(any("1h" in msg for msg in cm1h.output))
            expected_min_1h = (now - timedelta(days=729)).isoformat()
            self.assertGreaterEqual(res_1h["effective_start_date"], expected_min_1h)

            # Entire window predating retention -> 0 rows cleanly without calling yf
            recorded_starts.clear()
            with self.assertLogs("BacktestBackfill", level="WARNING"):
                res_old = backfill_market_ticks(
                    db_path=self.db_path,
                    start_date="2020-01-01",
                    end_date="2020-01-10",
                    interval="1h",
                    symbols=["JPY=X"],
                )
            self.assertTrue(res_old["clamped"])
            self.assertEqual(res_old["total_rows_inserted"], 0)
            self.assertEqual(recorded_starts, [])

            # 1d multi-year -> NOT clamped
            res_1d = backfill_market_ticks(
                db_path=self.db_path,
                start_date="2020-01-01",
                end_date="2024-01-10",
                interval="1d",
                symbols=["JPY=X"],
            )
            self.assertFalse(res_1d["clamped"])
            self.assertEqual(res_1d["effective_start_date"], "2020-01-01")

    def test_backfill_yield_spreads_fred_dgs2_dgs10_alignment(self) -> None:
        conn = self._init_empty_db()
        try:
            # Pre-seed JGB10Y including lookback day before start_date (2026-11-02)
            # and skip 2026-11-03 (JP Culture Day holiday)
            conn.executemany(
                """
                INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("JGB10Y", "2026-11-02T00:00:00Z", 1.10, 1.10, 1.10, 1.10, 0.0),
                    ("JGB10Y", "2026-11-04T00:00:00Z", 1.15, 1.15, 1.15, 1.15, 0.0),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        def _fake_fred_get(url: str, params=None, **kwargs):
            self.assertEqual(url, FRED_OBSERVATIONS_URL)
            series_id = (params or {}).get("series_id")
            # Ensure lookback buffer was requested
            self.assertLessEqual(params["observation_start"], "2026-10-27")
            if series_id == "DGS10":
                return _make_mock_response(
                    json_data={
                        "observations": [
                            {"date": "2026-11-02", "value": "4.30"},
                            {"date": "2026-11-03", "value": "4.35"},
                            {"date": "2026-11-04", "value": "."},  # US holiday -> ffill
                        ]
                    }
                )
            if series_id == "DGS2":
                return _make_mock_response(
                    json_data={
                        "observations": [
                            {"date": "2026-11-02", "value": "4.10"},
                            {"date": "2026-11-03", "value": "4.12"},
                            {"date": "2026-11-04", "value": "."},
                        ]
                    }
                )
            raise AssertionError(f"Unexpected series_id: {series_id}")

        with (
            mock.patch.dict(os.environ, {"FRED_API_KEY": "test-fred-key"}),
            mock.patch(
                "fx_signal_reader.collectors.base.requests.get",
                side_effect=_fake_fred_get,
            ),
        ):
            summary = backfill_yield_spreads(
                db_path=self.db_path,
                start_date="2026-11-03",
                end_date="2026-11-04",
            )

        self.assertEqual(summary["status"], "SUCCESS")
        self.assertTrue(summary["used_fred"])
        self.assertFalse(summary["fallback_used"])
        self.assertEqual(summary["source"], "FRED+MoF")
        self.assertEqual(summary["rows_inserted"], 2)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM yield_spreads ORDER BY timestamp_utc ASC"
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(len(rows), 2)
        # 2026-11-03: US 4.35 - JP carried from 2026-11-02 (1.10) = 3.25
        self.assertEqual(rows[0]["timestamp_utc"], "2026-11-03T00:00:00Z")
        self.assertAlmostEqual(rows[0]["us_10y"], 4.35)
        self.assertAlmostEqual(rows[0]["us_2y"], 4.12)
        self.assertAlmostEqual(rows[0]["jp_10y"], 1.10)
        self.assertAlmostEqual(rows[0]["spread_10y"], 3.25)
        self.assertEqual(rows[0]["source"], "FRED+MoF")

        # 2026-11-04: US carried from 2026-11-03 (4.35) - JP 1.15 = 3.20
        self.assertEqual(rows[1]["timestamp_utc"], "2026-11-04T00:00:00Z")
        self.assertAlmostEqual(rows[1]["us_10y"], 4.35)
        self.assertAlmostEqual(rows[1]["us_2y"], 4.12)
        self.assertAlmostEqual(rows[1]["jp_10y"], 1.15)
        self.assertAlmostEqual(rows[1]["spread_10y"], 3.20)

    def test_backfill_yield_spreads_tnx_db_and_yfinance_fallbacks(self) -> None:
        # Case A: Intraday ^TNX rows in DB -> pick latest bar per UTC day
        conn = self._init_empty_db()
        try:
            conn.executemany(
                """
                INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("^TNX", "2026-11-17T14:00:00Z", 4.10, 4.10, 4.10, 4.10, 0.0),
                    ("^TNX", "2026-11-17T20:00:00Z", 4.25, 4.25, 4.25, 4.25, 0.0),
                    ("JGB10Y", "2026-11-17T00:00:00Z", 1.05, 1.05, 1.05, 1.05, 0.0),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        with mock.patch.dict(os.environ, {"FRED_API_KEY": ""}):
            res_db = backfill_yield_spreads(
                db_path=self.db_path,
                start_date="2026-11-17",
                end_date="2026-11-17",
            )

        self.assertEqual(res_db["status"], "SUCCESS")
        self.assertFalse(res_db["used_fred"])
        self.assertTrue(res_db["fallback_used"])
        self.assertEqual(res_db["source"], "yfinance+mof_japan")

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM yield_spreads").fetchone()
        finally:
            conn.close()

        self.assertIsNone(row["us_2y"])
        self.assertAlmostEqual(row["us_10y"], 4.25)
        self.assertAlmostEqual(row["jp_10y"], 1.05)
        self.assertAlmostEqual(row["spread_10y"], 3.20)

        # Case B: No ^TNX rows in DB -> fallback to yf.Ticker("^TNX").history(..., interval="1d")
        db_b = self.tmp_dir / "backtest_tnx_yf.db"
        conn_b = sqlite3.connect(db_b)
        conn_b.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn_b.execute(
            """
            INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
            VALUES ('JGB10Y', '2026-11-17T00:00:00Z', 1.10, 1.10, 1.10, 1.10, 0.0)
            """
        )
        conn_b.commit()
        conn_b.close()

        tnx_df = pd.DataFrame(
            {"Close": [4.40]},
            index=pd.DatetimeIndex(["2026-11-17 00:00:00"], tz="America/Chicago"),
        )
        mock_ticker = mock.MagicMock()
        mock_ticker.history.return_value = tnx_df

        with (
            mock.patch.dict(os.environ, {"FRED_API_KEY": ""}),
            mock.patch.object(backfill_mod.yf, "Ticker", return_value=mock_ticker),
        ):
            res_yf = backfill_yield_spreads(
                db_path=db_b,
                start_date="2026-11-17",
                end_date="2026-11-17",
            )

        self.assertEqual(res_yf["rows_inserted"], 1)
        mock_ticker.history.assert_called_once()
        self.assertEqual(mock_ticker.history.call_args.kwargs["interval"], "1d")

    def test_backfill_yield_spreads_fred_error_falls_back_to_tnx_and_explicit_key(self) -> None:
        conn = self._init_empty_db()
        try:
            conn.executemany(
                """
                INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("^TNX", "2026-11-17T20:00:00Z", 4.30, 4.30, 4.30, 4.30, 0.0),
                    ("JGB10Y", "2026-11-17T00:00:00Z", 1.10, 1.10, 1.10, 1.10, 0.0),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        with (
            mock.patch.dict(os.environ, {"FRED_API_KEY": ""}),
            mock.patch(
                "fx_signal_reader.collectors.base.requests.get",
                return_value=_make_mock_response(status_code=403, text="Forbidden"),
            ),
            self.assertLogs("BacktestBackfill", level="WARNING") as cm,
        ):
            summary = backfill_yield_spreads(
                db_path=self.db_path,
                start_date="2026-11-17",
                end_date="2026-11-17",
                fred_api_key="explicit-override-key",
            )

        self.assertFalse(summary["used_fred"])
        self.assertTrue(summary["fallback_used"])
        self.assertEqual(summary["source"], "yfinance+mof_japan")
        self.assertEqual(summary["rows_inserted"], 1)
        self.assertTrue(any("falling back to ^TNX" in line for line in cm.output))

    def test_backfill_historical_data_none_dates_defaults(self) -> None:
        today_utc = datetime.now(timezone.utc).date()
        mock_ticker = mock.MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()

        with (
            mock.patch.object(backfill_mod.yf, "Ticker", return_value=mock_ticker),
            mock.patch(
                "fx_signal_reader.collectors.base.requests.get",
                return_value=_make_mock_response(content_bytes=_sample_mof_current_csv()),
            ),
            mock.patch.dict(os.environ, {"FRED_API_KEY": ""}, clear=False),
        ):
            res_15m = backfill_historical_data(
                db_path=self.db_path,
                interval="15m",
                symbols=["JPY=X"],
            )

        self.assertEqual(res_15m["end_date"], today_utc.isoformat())
        self.assertEqual(
            res_15m["start_date"],
            (today_utc - timedelta(days=59)).isoformat(),
        )
        self.assertFalse(res_15m["market_ticks"]["clamped"])

    def test_backfill_historical_data_summary_and_mcp_compatibility(self) -> None:
        base_prices = {
            "JPY=X": 155.25,
            "^TNX": 4.42,
            "^GSPC": 5980.0,
            "^N225": 39100.0,
            "^VIX": 16.5,
            "GC=F": 2650.0,
        }

        def _ticker_factory(symbol: str):
            t = mock.MagicMock()
            p = base_prices.get(symbol, 100.0)
            df = pd.DataFrame(
                {
                    "Open": [p - 0.1, p],
                    "High": [p + 0.2, p + 0.3],
                    "Low": [p - 0.2, p - 0.1],
                    "Close": [p, p + 0.1],
                    "Volume": [0.0, 1000.0],
                },
                index=pd.DatetimeIndex(
                    ["2026-11-17 10:00:00", "2026-11-17 11:00:00"],
                    tz="UTC",
                ),
            )
            t.history.return_value = df
            return t

        def _fake_get(url: str, **kwargs):
            if url == MOF_JGB_CURRENT_URL:
                return _make_mock_response(content_bytes=_sample_mof_current_csv())
            if url == MOF_JGB_ALL_URL:
                return _make_mock_response(content_bytes=_sample_mof_all_csv())
            raise AssertionError(f"Unexpected URL: {url}")

        with (
            mock.patch.object(backfill_mod.yf, "Ticker", side_effect=_ticker_factory),
            mock.patch(
                "fx_signal_reader.collectors.base.requests.get",
                side_effect=_fake_get,
            ),
            mock.patch.dict(os.environ, {"FRED_API_KEY": ""}, clear=False),
        ):
            summary = backfill_historical_data(
                db_path=self.db_path,
                start_date="2026-10-30",
                end_date="2026-11-18",
                interval="1h",
            )

        for key in (
            "db_path",
            "start_date",
            "end_date",
            "interval",
            "market_ticks",
            "jgb_yields",
            "yield_spreads",
            "db_counts",
        ):
            self.assertIn(key, summary)

        # Must be cleanly JSON-serializable
        json.dumps(summary)

        # Verify MCP read-only query functions work seamlessly against backfilled DB
        latest_spread = get_latest_yield_spread(db_path=str(self.db_path))
        self.assertIsNotNone(latest_spread["spread_10y"])
        self.assertIsNotNone(latest_spread["usdjpy_spot"])
        self.assertEqual(latest_spread["source"], "yfinance+mof_japan")

        ticks_summary = get_market_ticks_summary(db_path=str(self.db_path))
        self.assertEqual(ticks_summary["missing_symbols"], [])
        self.assertEqual(
            ticks_summary["symbol_count"],
            len(MarketTicksCollector.TICKERS),
        )
        self.assertIsNotNone(ticks_summary["symbols"]["JPY=X"]["bar_change"])

    def test_numpy_and_nullable_int_dtypes_preserved(self) -> None:
        # 1. All-integer (np.int64) DataFrame
        df_int = pd.DataFrame(
            {
                "Open": pd.array([154, 155], dtype="int64"),
                "High": pd.array([156, 157], dtype="int64"),
                "Low": pd.array([153, 154], dtype="int64"),
                "Close": pd.array([155, 156], dtype="int64"),
                "Volume": pd.array([1000, 2000], dtype="int64"),
            },
            index=pd.DatetimeIndex(
                ["2026-11-17 10:00:00", "2026-11-17 11:00:00"], tz="UTC"
            ),
        )
        # 2. Mixed object + int64 Volume (prevents float64 upcasting in df.iterrows())
        df_mixed = pd.DataFrame(
            {
                "Close": [155.25],
                "Volume": pd.array([12345], dtype="int64"),
                "Note": ["ok"],
            },
            index=pd.DatetimeIndex(["2026-11-17 12:00:00"], tz="UTC"),
        )
        # 3. Nullable pd.Int64 Volume ([500, pd.NA]) + np.float32 Close
        df_nullable = pd.DataFrame(
            {
                "Close": pd.array([155.25, 155.50], dtype="float32"),
                "Volume": pd.array([500, pd.NA], dtype="Int64"),
            },
            index=pd.DatetimeIndex(
                ["2026-11-17 13:00:00", "2026-11-17 14:00:00"], tz="UTC"
            ),
        )

        dfs = {"INTDF": df_int, "OBJVOL": df_mixed, "NULLINT": df_nullable}

        def _ticker_factory(symbol: str):
            t = mock.MagicMock()
            t.history.return_value = dfs[symbol]
            return t

        with mock.patch.object(backfill_mod.yf, "Ticker", side_effect=_ticker_factory):
            summary = backfill_market_ticks(
                db_path=self.db_path,
                start_date="2026-11-17",
                end_date="2026-11-17",
                interval="1h",
                symbols=["INTDF", "OBJVOL", "NULLINT"],
            )

        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["symbols"]["INTDF"]["rows_inserted"], 2)
        self.assertEqual(summary["symbols"]["OBJVOL"]["rows_inserted"], 1)
        self.assertEqual(summary["symbols"]["NULLINT"]["rows_inserted"], 2)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT symbol, timestamp_utc, price, open, high, low, volume "
                "FROM market_ticks ORDER BY symbol, timestamp_utc ASC"
            ).fetchall()
        finally:
            conn.close()

        by_sym: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            by_sym.setdefault(r["symbol"], []).append(r)

        self.assertEqual([r["price"] for r in by_sym["INTDF"]], [155.0, 156.0])
        self.assertEqual([r["volume"] for r in by_sym["INTDF"]], [1000.0, 2000.0])

        self.assertAlmostEqual(by_sym["OBJVOL"][0]["price"], 155.25)
        self.assertEqual(by_sym["OBJVOL"][0]["volume"], 12345.0)

        self.assertAlmostEqual(by_sym["NULLINT"][0]["price"], 155.25, places=5)
        self.assertEqual(by_sym["NULLINT"][0]["volume"], 500.0)
        self.assertAlmostEqual(by_sym["NULLINT"][1]["price"], 155.50, places=5)
        self.assertEqual(by_sym["NULLINT"][1]["volume"], 0.0)

    def test_nat_in_datetime_index_skips_bad_row_only(self) -> None:
        df_nat = pd.DataFrame(
            {
                "Open": [154.0, 154.5, 155.0],
                "High": [154.2, 154.7, 155.2],
                "Low": [153.8, 154.3, 154.8],
                "Close": [154.1, 154.6, 155.1],
                "Volume": [10.0, 20.0, 30.0],
            },
            index=pd.DatetimeIndex(
                ["2026-11-10 00:00:00", pd.NaT, "2026-11-10 02:00:00"],
                tz="UTC",
            ),
        )
        mock_ticker = mock.MagicMock()
        mock_ticker.history.return_value = df_nat

        with mock.patch.object(backfill_mod.yf, "Ticker", return_value=mock_ticker):
            summary = backfill_market_ticks(
                db_path=self.db_path,
                start_date="2026-11-10",
                end_date="2026-11-10",
                interval="1h",
                symbols=["NATSYM"],
            )

        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["symbols"]["NATSYM"]["status"], "SUCCESS")
        self.assertEqual(summary["symbols"]["NATSYM"]["rows_inserted"], 2)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT timestamp_utc, price FROM market_ticks "
                "WHERE symbol = 'NATSYM' ORDER BY timestamp_utc ASC"
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(
            [(r["timestamp_utc"], r["price"]) for r in rows],
            [
                ("2026-11-10T00:00:00Z", 154.1),
                ("2026-11-10T02:00:00Z", 155.1),
            ],
        )

        # Also verify _extract_yf_rows_to_daily_close skips pd.NaT and accepts np.int64 Close
        df_daily_nat = pd.DataFrame(
            {"Close": pd.array([4, 5], dtype="int64")},
            index=pd.DatetimeIndex([pd.NaT, "2026-11-10 00:00:00"], tz="UTC"),
        )
        daily = backfill_mod._extract_yf_rows_to_daily_close(
            df_daily_nat, "2026-11-09", "2026-11-11"
        )
        self.assertEqual(daily, {"2026-11-10": 5.0})

    def test_boolean_date_bounds_raise_value_error(self) -> None:
        for kwargs in (
            {"start_date": True, "end_date": "2026-11-10"},
            {"start_date": "2026-11-01", "end_date": False},
        ):
            with self.assertRaises(ValueError):
                backfill_market_ticks(db_path=self.db_path, **kwargs)
            with self.assertRaises(ValueError):
                backfill_jgb_yields(db_path=self.db_path, **kwargs)
            with self.assertRaises(ValueError):
                backfill_yield_spreads(db_path=self.db_path, **kwargs)
            with self.assertRaises(ValueError):
                backfill_historical_data(db_path=self.db_path, **kwargs)
        self.assertFalse(self.db_path.exists())

    def test_mof_explicit_dash_in_current_masks_historical_overlap(self) -> None:
        current_csv = "\n".join(
            [
                "国債金利情報 (令和8年1月)",
                "基準日,1年,10年",
                "2026/1/5,0.50,1.200",
                "2026/1/6,0.50,-",
            ]
        ).encode("cp932")
        all_csv = "\n".join(
            [
                "国債金利情報 (全期間)",
                "基準日,1年,10年",
                "2026/1/4,0.45,1.100",
                "2026/1/5,0.50,9.999",
                "2026/1/6,0.50,8.888",
            ]
        ).encode("cp932")

        def _fake_get(url: str, **kwargs):
            if url == MOF_JGB_CURRENT_URL:
                return _make_mock_response(content_bytes=current_csv)
            if url == MOF_JGB_ALL_URL:
                return _make_mock_response(content_bytes=all_csv)
            raise AssertionError(f"Unexpected URL: {url}")

        with mock.patch(
            "fx_signal_reader.collectors.base.requests.get",
            side_effect=_fake_get,
        ):
            res = backfill_jgb_yields(
                db_path=self.db_path,
                start_date="2026-01-04",
                end_date="2026-01-06",
            )

        self.assertTrue(res["used_historical_csv"])
        self.assertEqual(res["dates_covered"], 2)  # 2026-01-04 and 2026-01-05 only

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT timestamp_utc, price FROM market_ticks "
                "WHERE symbol = 'JGB10Y' ORDER BY timestamp_utc ASC"
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(
            [(r["timestamp_utc"], r["price"]) for r in rows],
            [
                ("2026-01-04T00:00:00Z", 1.100),
                ("2026-01-05T00:00:00Z", 1.200),
            ],
        )
