"""Tests for P8-03: multi-pair MarketTicksCollector, German Bund 10Y and pair spreads.

All network access (requests / yfinance) is mocked.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from fx_signal_reader.collectors.market_ticks import MarketTicksCollector
from fx_signal_reader.core.spread_calculator import (
    compute_pair_spread,
    compute_spread_for_pair,
    compute_yield_spread,
    parse_de_bund_csv,
)

BUNDESBANK_CSV = (
    '"BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A","","Unit"\n'
    "2026-11-16,2.61,\n"
    "2026-11-17,.,\n"
    "2026-11-18,2.65,\n"
)
BUNDESBANK_CSV_SEMI = "\ufeff;BBSIS.D\n2026-11-17;2,60;\n2026-11-18;2,64;\n"
ECB_CSV = (
    "KEY,FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
    "YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y,B,2026-11-17,2.55,A\n"
    "YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y,B,2026-11-18,2.58,A\n"
)


def _resp(text="", json_data=None):
    r = MagicMock()
    r.text = text
    r.json.return_value = json_data or {}
    r.raise_for_status.return_value = None
    return r


def _candles(symbol, price, ts="2026-11-18T15:00:00Z"):
    c = [
        {
            "symbol": symbol,
            "timestamp_utc": ts,
            "price": price,
            "open": price,
            "high": price,
            "low": price,
            "volume": 0.0,
        }
    ]
    return c, {"symbol": symbol, "latest_price": price}


class TestPairSpreadMath(unittest.TestCase):
    def test_compute_pair_spread_base_minus_quote(self):
        self.assertEqual(compute_pair_spread(2.65, 4.10), -1.45)  # EUR/USD
        self.assertEqual(compute_pair_spread(4.10, 2.65), 1.45)  # USD/JPY style

    def test_compute_pair_spread_invalid(self):
        self.assertIsNone(compute_pair_spread(None, 1.0))
        self.assertIsNone(compute_pair_spread(1.0, None))
        self.assertIsNone(compute_pair_spread(float("nan"), 1.0))
        self.assertIsNone(compute_pair_spread(1.0, float("inf")))
        self.assertEqual(compute_pair_spread(1.23456, 0, precision=2), 1.23)

    def test_compute_yield_spread_backward_compatible(self):
        self.assertEqual(compute_yield_spread(5.103, 2.925), 2.178)
        self.assertEqual(
            compute_yield_spread(5.123456, 2.987654, precision=2),
            compute_pair_spread(5.123456, 2.987654, precision=2),
        )
        self.assertIsNone(compute_yield_spread(None, 2.9))

    def test_compute_spread_for_pair_orientation(self):
        self.assertEqual(compute_spread_for_pair("USD/JPY", 4.10, 1.60), 2.5)
        self.assertEqual(compute_spread_for_pair("EUR/USD", 4.10, 2.65), -1.45)
        self.assertEqual(compute_spread_for_pair("eurusd", 3.0, 2.5), -0.5)


class TestParseDeBundCsv(unittest.TestCase):
    def test_bundesbank_classic_csv_skips_missing(self):
        s = parse_de_bund_csv(BUNDESBANK_CSV)
        self.assertEqual(s, {"2026-11-16": 2.61, "2026-11-18": 2.65})

    def test_semicolon_decimal_comma_with_bom(self):
        s = parse_de_bund_csv(BUNDESBANK_CSV_SEMI)
        self.assertEqual(s, {"2026-11-17": 2.60, "2026-11-18": 2.64})

    def test_ecb_sdmx_csv(self):
        s = parse_de_bund_csv(ECB_CSV)
        self.assertEqual(s, {"2026-11-17": 2.55, "2026-11-18": 2.58})

    def test_empty_and_garbage(self):
        self.assertEqual(parse_de_bund_csv(""), {})
        self.assertEqual(parse_de_bund_csv("<html>error</html>"), {})


class TestCollectorConfig(unittest.TestCase):
    def test_default_is_usdjpy_and_unchanged(self):
        c = MarketTicksCollector()
        self.assertEqual(c.pair, "USD/JPY")
        self.assertEqual(c.tickers, MarketTicksCollector.TICKERS)
        self.assertEqual(
            MarketTicksCollector.TICKERS,
            ["JPY=X", "^TNX", "^GSPC", "^N225", "^VIX", "GC=F"],
        )
        self.assertTrue(c.db_path.name == "usdjpy_signals.db")
        self.assertTrue(MarketTicksCollector.MOF_JGB_CSV_URL.endswith("jgbcm.csv"))
        self.assertEqual(c.foreign_yield_symbol, "JGB10Y")

    def test_eurusd_watchlist_and_db(self):
        c = MarketTicksCollector(pair="eurusd")
        self.assertEqual(c.pair, "EUR/USD")
        self.assertEqual(
            c.tickers,
            ["EURUSD=X", "^TNX", "^STOXX50E", "^GSPC", "^VIX", "GC=F"],
        )
        self.assertEqual(c.db_path.name, "eurusd_signals.db")
        self.assertEqual(c.foreign_yield_symbol, "DE10Y")

    def test_explicit_db_path_respected(self):
        c = MarketTicksCollector(db_path="/tmp/custom.db", pair="EUR/USD")
        self.assertEqual(str(c.db_path), "/tmp/custom.db")

    def test_unknown_pair_raises(self):
        with self.assertRaises(ValueError):
            MarketTicksCollector(pair="GBP/CHF")


class TestFetchDe10yBund(unittest.TestCase):
    def setUp(self):
        self.c = MarketTicksCollector(pair="EUR/USD", fred_api_key="")
        self.c.max_retries = 0

    def test_bundesbank_primary(self):
        with patch.object(
            self.c, "fetch_url", return_value=_resp(BUNDESBANK_CSV)
        ) as fu:
            res = self.c.fetch_de_10y_bund()
        self.assertEqual(res["rate_10y"], 2.65)
        self.assertEqual(res["timestamp_utc"], "2026-11-18T00:00:00Z")
        self.assertEqual(res["raw_date"], "2026-11-18")
        self.assertIn("Bundesbank", res["source"])
        self.assertEqual(fu.call_count, 1)
        url = fu.call_args.args[0]
        self.assertEqual(url, MarketTicksCollector.BUNDESBANK_DE10Y_URL)
        self.assertEqual(fu.call_args.kwargs["params"]["format"], "csv")
        self.assertIn("startPeriod", fu.call_args.kwargs["params"])

    def test_falls_back_to_ecb(self):
        def fake(url, **kw):
            if "bundesbank" in url:
                raise ConnectionError("down")
            return _resp(ECB_CSV)

        with patch.object(self.c, "fetch_url", side_effect=fake):
            res = self.c.fetch_de_10y_bund()
        self.assertEqual(res["rate_10y"], 2.58)
        self.assertIn("ECB", res["source"])

    def test_falls_back_to_fred_with_key(self):
        self.c.fred_api_key = "k"

        def fake(url, **kw):
            if "stlouisfed" in url:
                self.assertEqual(
                    kw["params"]["series_id"], "IRLTLT01DEM156N"
                )
                return _resp(
                    json_data={
                        "observations": [
                            {"date": "2026-10-01", "value": "."},
                            {"date": "2026-09-01", "value": "2.71"},
                        ]
                    }
                )
            raise ConnectionError("down")

        with patch.object(self.c, "fetch_url", side_effect=fake):
            res = self.c.fetch_de_10y_bund()
        self.assertEqual(res["rate_10y"], 2.71)
        self.assertEqual(res["timestamp_utc"], "2026-09-01T00:00:00Z")
        self.assertIn("FRED", res["source"])

    def test_falls_back_to_yahoo_proxy(self):
        with patch.object(
            self.c, "fetch_url", side_effect=ConnectionError("down")
        ), patch.object(
            self.c, "fetch_ticker_data", return_value=_candles("DE10YT=RR", 2.7)
        ):
            res = self.c.fetch_de_10y_bund()
        self.assertEqual(res["rate_10y"], 2.7)
        self.assertEqual(res["timestamp_utc"], "2026-11-18T00:00:00Z")
        self.assertIn("Yahoo", res["source"])

    def test_all_sources_fail_raises(self):
        with patch.object(
            self.c, "fetch_url", side_effect=ConnectionError("down")
        ), patch.object(self.c, "fetch_ticker_data", return_value=([], {})):
            with self.assertRaises(ValueError):
                self.c.fetch_de_10y_bund()

    def test_empty_bundesbank_body_triggers_fallback(self):
        def fake(url, **kw):
            return _resp("" if "bundesbank" in url else ECB_CSV)

        with patch.object(self.c, "fetch_url", side_effect=fake):
            res = self.c.fetch_de_10y_bund()
        self.assertIn("ECB", res["source"])


class TestEurUsdCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "eurusd_signals.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, de_result):
        c = MarketTicksCollector(db_path=self.db, pair="EUR/USD", fred_api_key="")
        prices = {
            "EURUSD=X": 1.0850,
            "^TNX": 4.10,
            "^STOXX50E": 5000.0,
            "^GSPC": 6000.0,
            "^VIX": 15.0,
            "GC=F": 2500.0,
        }
        fetched = []

        def fake_ticker(symbol):
            fetched.append(symbol)
            return _candles(symbol, prices[symbol])

        with patch.object(c, "fetch_ticker_data", side_effect=fake_ticker), patch.object(
            c, "fetch_de_10y_bund", **de_result
        ), patch.object(c, "fetch_jgb_10y_from_mof") as mof:
            summary = c.collect()
        mof.assert_not_called()
        return summary, fetched

    def test_collect_records_de10y_and_spread(self):
        info = {
            "return_value": {
                "rate_10y": 2.65,
                "raw_date": "2026-11-18",
                "timestamp_utc": "2026-11-18T00:00:00Z",
                "source": "Deutsche Bundesbank",
            }
        }
        summary, fetched = self._run(info)
        self.assertEqual(
            fetched, ["EURUSD=X", "^TNX", "^STOXX50E", "^GSPC", "^VIX", "GC=F"]
        )
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["pair"], "EUR/USD")
        self.assertEqual(summary["de_10y"], 2.65)
        self.assertEqual(summary["us_10y"], 4.10)
        self.assertEqual(summary["spread_10y"], -1.45)
        self.assertNotIn("jgb_10y", summary)

        conn = sqlite3.connect(self.db)
        syms = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM market_ticks")}
        self.assertTrue({"EURUSD=X", "DE10Y", "^TNX"} <= syms)
        self.assertNotIn("JGB10Y", syms)
        row = conn.execute(
            "SELECT us_10y, jp_10y, spread_10y, source FROM yield_spreads"
        ).fetchall()
        self.assertEqual(len(row), 1)
        # jp_10y is the legacy 'foreign yield' column -> DE10Y for EUR/USD.
        self.assertEqual(tuple(row[0][:3]), (4.10, 2.65, -1.45))
        self.assertIn("bundesbank", row[0][3])
        conn.close()

    def test_collect_survives_de10y_outage(self):
        summary, _ = self._run({"side_effect": ValueError("all failed")})
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertIsNone(summary["de_10y"])
        self.assertIsNone(summary["spread_10y"])
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT count(*) FROM market_ticks WHERE symbol='DE10Y'").fetchone()[0],
            0,
        )
        self.assertEqual(
            conn.execute("SELECT count(*) FROM yield_spreads").fetchone()[0], 1
        )
        conn.close()


class TestUsdJpyCollectUnchanged(unittest.TestCase):
    def test_default_collect_keys_and_spread(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "usdjpy_signals.db"
            c = MarketTicksCollector(db_path=db, fred_api_key="")
            prices = {s: 100.0 for s in MarketTicksCollector.TICKERS}
            prices["^TNX"] = 4.5
            with patch.object(
                c,
                "fetch_ticker_data",
                side_effect=lambda s: _candles(s, prices[s]),
            ), patch.object(
                c,
                "fetch_jgb_10y_from_mof",
                return_value={
                    "rate_10y": 1.6,
                    "raw_date": "R8.11.18",
                    "timestamp_utc": "2026-11-18T00:00:00Z",
                    "source": "mof",
                },
            ), patch.object(c, "fetch_de_10y_bund") as de:
                summary = c.collect()
            de.assert_not_called()
            self.assertEqual(
                list(summary),
                [
                    "status",
                    "total_ticks_inserted",
                    "db_market_ticks_rows",
                    "db_yield_spreads_rows",
                    "latest_prices",
                    "jgb_10y",
                    "us_10y",
                    "spread_10y",
                    "spread_timestamp",
                    "ticker_summaries",
                ],
            )
            self.assertEqual(summary["spread_10y"], 2.9)
            conn = sqlite3.connect(db)
            syms = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM market_ticks")}
            self.assertTrue({"JGB10Y", "^JB10YR", "JPY=X"} <= syms)
            self.assertEqual(
                tuple(conn.execute("SELECT us_10y, jp_10y, spread_10y, source FROM yield_spreads").fetchone()),
                (4.5, 1.6, 2.9, "yfinance+mof_japan"),
            )
            conn.close()


class TestYieldSpread2YAndSchemaMigration(unittest.TestCase):
    def test_schema_migration_adds_2y_columns(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test_legacy.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE yield_spreads (
                    timestamp_utc TEXT PRIMARY KEY,
                    us_2y REAL,
                    us_10y REAL,
                    jp_10y REAL,
                    spread_10y REAL,
                    source TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
                VALUES ('2026-11-17T00:00:00Z', 4.30, 4.50, 1.60, 2.90, 'legacy')
                """
            )
            conn.commit()
            conn.close()

            # Now instantiate collector and get_connection (which triggers init_db)
            collector = MarketTicksCollector(db_path=db_path)
            conn = collector.get_connection()

            cur = conn.cursor()
            cur.execute("PRAGMA table_info(yield_spreads)")
            cols = {r["name"] for r in cur.fetchall()}
            self.assertIn("jp_2y", cols)
            self.assertIn("spread_2y", cols)

            # Preexisting row should be intact
            row = conn.execute("SELECT * FROM yield_spreads WHERE timestamp_utc = '2026-11-17T00:00:00Z'").fetchone()
            self.assertEqual(row["us_10y"], 4.50)
            self.assertEqual(row["spread_10y"], 2.90)
            self.assertIsNone(row["jp_2y"])
            self.assertIsNone(row["spread_2y"])

            # Idempotency: calling init_db again should not raise
            collector.init_db(conn)
            conn.close()

    def test_fetch_jgb_10y_and_2y_from_mof(self):
        mof_csv = (
            "国債金利情報\n"
            "基準日,1年,2年,5年,10年\n"
            "R8.11.18,0.850,1.100,1.850,2.920\n"
        )
        c = MarketTicksCollector()
        with patch.object(c, "fetch_url", return_value=_resp(mof_csv)):
            res = c.fetch_jgb_10y_from_mof()
        self.assertEqual(res["rate_10y"], 2.920)
        self.assertEqual(res["rate_2y"], 1.100)
        self.assertEqual(res["timestamp_utc"], "2026-11-18T00:00:00Z")

    def test_fetch_us_2y_fred(self):
        c = MarketTicksCollector(fred_api_key="mock_key")
        with patch.object(c, "fetch_fred_latest", return_value=4.35):
            val, src = c.fetch_us_2y()
        self.assertEqual(val, 4.35)
        self.assertEqual(src, "fred")

    def test_fetch_us_2y_yahoo_fallback_unconfigured_fred(self):
        c = MarketTicksCollector(fred_api_key="")
        candles = [{"price": 4.25, "timestamp_utc": "2026-11-18T15:00:00Z"}]
        with patch.object(c, "fetch_ticker_data", return_value=(candles, {})) as ft:
            val, src = c.fetch_us_2y()
        ft.assert_called_once_with("2YY=F")
        self.assertEqual(val, 4.25)
        self.assertEqual(src, "yahoo_2yy")

    def test_fetch_us_2y_yahoo_fallback_fred_none(self):
        c = MarketTicksCollector(fred_api_key="mock_key")
        candles = [{"price": 4.28, "timestamp_utc": "2026-11-18T15:00:00Z"}]
        with patch.object(c, "fetch_fred_latest", return_value=None), patch.object(
            c, "fetch_ticker_data", return_value=(candles, {})
        ) as ft:
            val, src = c.fetch_us_2y()
        ft.assert_called_once_with("2YY=F")
        self.assertEqual(val, 4.28)
        self.assertEqual(src, "yahoo_2yy")

    def test_collect_with_2y_yields_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test_collect_2y.db"
            c = MarketTicksCollector(db_path=db, fred_api_key="")
            prices = {s: 100.0 for s in MarketTicksCollector.TICKERS}
            prices["^TNX"] = 4.50
            prices["2YY=F"] = 4.35

            def fake_ticker(s):
                return _candles(s, prices.get(s, 100.0))

            with patch.object(c, "fetch_ticker_data", side_effect=fake_ticker), patch.object(
                c,
                "fetch_jgb_10y_from_mof",
                return_value={
                    "rate_10y": 1.60,
                    "rate_2y": 0.85,
                    "raw_date": "R8.11.18",
                    "timestamp_utc": "2026-11-18T00:00:00Z",
                    "source": "mof",
                },
            ), patch.object(c, "fetch_de_10y_bund") as de:
                summary = c.collect()

            de.assert_not_called()
            self.assertEqual(summary["status"], "SUCCESS")
            self.assertEqual(summary["jgb_10y"], 1.60)
            self.assertEqual(summary["jgb_2y"], 0.85)
            self.assertEqual(summary["us_10y"], 4.50)
            self.assertEqual(summary["us_2y"], 4.35)
            self.assertEqual(summary["spread_10y"], 2.90)
            self.assertEqual(summary["spread_2y"], 3.50)

            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM yield_spreads").fetchone()
            self.assertAlmostEqual(row["us_2y"], 4.35)
            self.assertAlmostEqual(row["jp_2y"], 0.85)
            self.assertAlmostEqual(row["spread_2y"], 3.50)
            self.assertAlmostEqual(row["us_10y"], 4.50)
            self.assertAlmostEqual(row["jp_10y"], 1.60)
            self.assertAlmostEqual(row["spread_10y"], 2.90)
            self.assertIn("yahoo_2y", row["source"])

            syms = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM market_ticks")}
            self.assertIn("JGB2Y", syms)
            self.assertIn("2YY=F", syms)
            conn.close()


if __name__ == "__main__":
    unittest.main()
