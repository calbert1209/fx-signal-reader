"""Tests for 2Y Sovereign Yield Spread, multi-tenor MoF CSV parser, and YieldSpreadRecord.

Validates Task P9-01:
- compute_2y_yield_spread with rounding and NaN/None edge cases
- parse_mof_csv multi-tenor extraction (2年, 10年) and Reiwa era parsing
- parse_mof_multi_tenor_series
- YieldSpreadRecord dataclass evolution (jp_2y, spread_2y)
- save_yield_spread persistence for both modern and legacy schemas
- align_yield_series and calculate_live_spread 2Y integration
"""

import math
import sqlite3
import unittest
from datetime import datetime

from fx_signal_reader.core.spread_calculator import (
    YieldSpreadRecord,
    align_yield_series,
    calculate_live_spread,
    compute_2y_yield_spread,
    compute_pair_spread,
    compute_yield_spread,
    parse_mof_csv,
    parse_mof_multi_tenor_series,
    save_yield_spread,
)

SAMPLE_MOF_CSV = (
    "国債金利情報\n"
    "基準日,1年,2年,3年,5年,10年,20年,30年\n"
    "R8.11.13,0.850,1.100,1.350,1.850,2.920,3.400,3.650\n"
    "R8.11.16,0.855,1.105,1.355,1.855,2.927,3.410,3.660\n"
    "R8.11.17,0.852,1.102,1.351,1.851,2.925,3.405,3.655\n"
)


class TestCompute2YYieldSpread(unittest.TestCase):
    def test_standard_spread(self):
        # US 2Y = 4.35%, JP 2Y = 0.85% -> 3.50%
        spread = compute_2y_yield_spread(4.35, 0.85)
        self.assertEqual(spread, 3.5)

    def test_precision_rounding(self):
        # 4.3524 - 0.8511 = 3.5013
        spread = compute_2y_yield_spread(4.3524, 0.8511, precision=4)
        self.assertEqual(spread, 3.5013)
        spread_2dp = compute_2y_yield_spread(4.3524, 0.8511, precision=2)
        self.assertEqual(spread_2dp, 3.5)

    def test_inverted_spread(self):
        # US 2Y lower than JP 2Y -> negative spread
        spread = compute_2y_yield_spread(1.10, 1.35)
        self.assertEqual(spread, -0.25)

    def test_none_inputs(self):
        self.assertIsNone(compute_2y_yield_spread(None, 0.85))
        self.assertIsNone(compute_2y_yield_spread(4.35, None))
        self.assertIsNone(compute_2y_yield_spread(None, None))

    def test_nan_and_inf_inputs(self):
        self.assertIsNone(compute_2y_yield_spread(float("nan"), 0.85))
        self.assertIsNone(compute_2y_yield_spread(4.35, float("nan")))
        self.assertIsNone(compute_2y_yield_spread(float("inf"), 0.85))
        self.assertIsNone(compute_2y_yield_spread(4.35, float("-inf")))


class TestMoFMultiTenorParsing(unittest.TestCase):
    def test_single_tenor_default_10y(self):
        res = parse_mof_csv(SAMPLE_MOF_CSV)
        self.assertEqual(len(res), 3)
        self.assertEqual(res["2026-11-13"], 2.920)
        self.assertEqual(res["2026-11-16"], 2.927)
        self.assertEqual(res["2026-11-17"], 2.925)

    def test_single_tenor_2y(self):
        res = parse_mof_csv(SAMPLE_MOF_CSV, target_tenor="2年")
        self.assertEqual(len(res), 3)
        self.assertEqual(res["2026-11-13"], 1.100)
        self.assertEqual(res["2026-11-16"], 1.105)
        self.assertEqual(res["2026-11-17"], 1.102)

    def test_multi_tenor_via_list(self):
        res = parse_mof_csv(SAMPLE_MOF_CSV, target_tenor=["2年", "10年"])
        self.assertEqual(len(res), 3)
        self.assertIn("2026-11-17", res)
        self.assertEqual(res["2026-11-17"]["2年"], 1.102)
        self.assertEqual(res["2026-11-17"]["10年"], 2.925)

    def test_multi_tenor_via_target_tenors_param(self):
        res = parse_mof_csv(SAMPLE_MOF_CSV, target_tenors=("2年", "10年"))
        self.assertEqual(len(res), 3)
        self.assertEqual(res["2026-11-13"]["2年"], 1.100)
        self.assertEqual(res["2026-11-13"]["10年"], 2.920)

    def test_parse_mof_multi_tenor_series(self):
        series = parse_mof_multi_tenor_series(SAMPLE_MOF_CSV, tenors=("2年", "10年"))
        self.assertIn("2年", series)
        self.assertIn("10年", series)
        self.assertEqual(series["2年"]["2026-11-16"], 1.105)
        self.assertEqual(series["10年"]["2026-11-16"], 2.927)

    def test_malformed_csv_returns_empty(self):
        self.assertEqual(parse_mof_csv(""), {})
        self.assertEqual(parse_mof_csv("Not,CSV,Data"), {})


class TestYieldSpreadRecordAndPersistence(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_record_dataclass_fields(self):
        rec = YieldSpreadRecord(
            timestamp_utc="2026-11-18T00:00:00Z",
            us_2y=4.35,
            us_10y=4.50,
            jp_10y=1.60,
            spread_10y=2.90,
            source="FRED+MoF",
            jp_2y=0.85,
            spread_2y=3.50,
        )
        self.assertEqual(rec.jp_2y, 0.85)
        self.assertEqual(rec.spread_2y, 3.50)

        # 8-element tuple for modern schema
        db_tuple = rec.to_db_tuple()
        self.assertEqual(
            db_tuple,
            ("2026-11-18T00:00:00Z", 4.35, 0.85, 3.50, 4.50, 1.60, 2.90, "FRED+MoF"),
        )

        # 6-element tuple for legacy schema
        legacy_tuple = rec.to_db_tuple(legacy=True)
        self.assertEqual(
            legacy_tuple,
            ("2026-11-18T00:00:00Z", 4.35, 4.50, 1.60, 2.90, "FRED+MoF"),
        )

        d = rec.to_dict()
        self.assertEqual(d["jp_2y"], 0.85)
        self.assertEqual(d["spread_2y"], 3.50)

    def test_save_to_modern_schema(self):
        self.conn.execute(
            """
            CREATE TABLE yield_spreads (
                timestamp_utc TEXT PRIMARY KEY,
                us_2y REAL,
                jp_2y REAL,
                spread_2y REAL,
                us_10y REAL,
                jp_10y REAL,
                spread_10y REAL,
                source TEXT NOT NULL
            );
            """
        )
        rec = YieldSpreadRecord(
            timestamp_utc="2026-11-18T00:00:00Z",
            us_2y=4.35,
            us_10y=4.50,
            jp_10y=1.60,
            spread_10y=2.90,
            source="FRED+MoF",
            jp_2y=0.85,
            spread_2y=3.50,
        )
        save_yield_spread(self.conn, rec)

        cur = self.conn.cursor()
        cur.execute("SELECT * FROM yield_spreads WHERE timestamp_utc = ?", ("2026-11-18T00:00:00Z",))
        row = cur.fetchone()
        self.assertEqual(row, ("2026-11-18T00:00:00Z", 4.35, 0.85, 3.50, 4.50, 1.60, 2.90, "FRED+MoF"))

    def test_save_to_legacy_schema(self):
        self.conn.execute(
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
        rec = YieldSpreadRecord(
            timestamp_utc="2026-11-18T00:00:00Z",
            us_2y=4.35,
            us_10y=4.50,
            jp_10y=1.60,
            spread_10y=2.90,
            source="FRED+MoF",
            jp_2y=0.85,
            spread_2y=3.50,
        )
        # Must not raise an error even though table has only 6 columns
        save_yield_spread(self.conn, rec)

        cur = self.conn.cursor()
        cur.execute("SELECT * FROM yield_spreads WHERE timestamp_utc = ?", ("2026-11-18T00:00:00Z",))
        row = cur.fetchone()
        self.assertEqual(row, ("2026-11-18T00:00:00Z", 4.35, 4.50, 1.60, 2.90, "FRED+MoF"))


class TestAlignmentAndLiveSpread2Y(unittest.TestCase):
    def test_align_yield_series_with_jp_2y(self):
        us_10y = {"2026-11-16": 4.50, "2026-11-17": 4.52}
        jp_10y = {"2026-11-16": 1.60, "2026-11-17": 1.62}
        us_2y = {"2026-11-16": 4.30, "2026-11-17": 4.32}
        jp_2y = {"2026-11-16": 0.80, "2026-11-17": 0.82}

        records = align_yield_series(
            us_10y_series=us_10y,
            jp_10y_series=jp_10y,
            us_2y_series=us_2y,
            jp_2y_series=jp_2y,
        )
        self.assertEqual(len(records), 2)
        rec0 = records[0]
        self.assertEqual(rec0.timestamp_utc, "2026-11-16T00:00:00Z")
        self.assertEqual(rec0.us_2y, 4.30)
        self.assertEqual(rec0.jp_2y, 0.80)
        self.assertEqual(rec0.spread_2y, 3.50)
        self.assertEqual(rec0.spread_10y, 2.90)

    def test_calculate_live_spread_with_2y(self):
        rec = calculate_live_spread(
            us_10y=4.50,
            jp_10y=1.60,
            us_2y=4.35,
            jp_2y=0.85,
            evaluation_timestamp_utc="2026-11-18T10:00:00Z",
        )
        self.assertEqual(rec.us_2y, 4.35)
        self.assertEqual(rec.jp_2y, 0.85)
        self.assertEqual(rec.spread_2y, 3.50)
        self.assertEqual(rec.spread_10y, 2.90)


if __name__ == "__main__":
    unittest.main()
