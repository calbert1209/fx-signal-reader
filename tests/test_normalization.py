"""
Unit tests for Normalization and Yield Spread Calculation modules.
Covers:
  1. ISO-8601 UTC timestamp parsing, conversion, and validation across various formats.
  2. URL and event hash repeatability, format, and collision resistance.
  3. Yield spread calculation accuracy, rounding, slope, and None-handling.
  4. Multi-source parsing (FRED, Yahoo Finance, Japan MoF).
  5. Holiday / publication misalignment resolution.
  6. Database persistence round-trip.
"""

from datetime import date, datetime, timedelta, timezone
import hashlib
import math
import sqlite3
import unittest

from normalizer import (
    clean_text,
    generate_event_hash,
    generate_url_hash,
    is_valid_iso8601_utc,
    normalize_datetime_utc,
    parse_event_datetime,
    parse_float_or_none,
    parse_japanese_era_date,
)
from spread_calculator import (
    YieldSpreadRecord,
    align_yield_series,
    calculate_live_spread,
    compute_curve_slope,
    compute_spread_bps,
    compute_us_2s10s_slope,
    compute_yield_spread,
    extract_fred_series,
    parse_fred_yield,
    parse_mof_csv,
    parse_yahoo_tnx,
    save_yield_spread,
)


class TestTimestampNormalization(unittest.TestCase):
    """Test ISO-8601 UTC timestamp normalization and standardization."""

    def test_strict_iso8601_utc_valid(self):
        valid_ts = "2026-11-18T06:00:00Z"
        self.assertTrue(is_valid_iso8601_utc(valid_ts))
        self.assertEqual(normalize_datetime_utc(valid_ts), valid_ts)

    def test_strict_iso8601_utc_invalid(self):
        # Missing Z
        self.assertFalse(is_valid_iso8601_utc("2026-11-18T06:00:00"))
        # Offset instead of Z
        self.assertFalse(is_valid_iso8601_utc("2026-11-18T06:00:00+09:00"))
        # Invalid date components
        self.assertFalse(is_valid_iso8601_utc("2026-02-31T06:00:00Z"))
        self.assertFalse(is_valid_iso8601_utc("2026-11-18 06:00:00"))
        self.assertFalse(is_valid_iso8601_utc("not-a-timestamp"))

    def test_datetime_object_conversion(self):
        # Naive datetime defaults to UTC
        dt_naive = datetime(2026, 11, 18, 14, 30, 15)
        self.assertEqual(normalize_datetime_utc(dt_naive), "2026-11-18T14:30:15Z")

        # Aware datetime with JST (+09:00) converted to UTC
        jst = timezone(timedelta(hours=9))
        dt_jst = datetime(2026, 11, 18, 15, 0, 0, tzinfo=jst)
        self.assertEqual(normalize_datetime_utc(dt_jst), "2026-11-18T06:00:00Z")

        # Aware datetime with EST (-05:00) converted to UTC
        est = timezone(timedelta(hours=-5))
        dt_est = datetime(2026, 11, 18, 1, 0, 0, tzinfo=est)
        self.assertEqual(normalize_datetime_utc(dt_est), "2026-11-18T06:00:00Z")

    def test_date_object_conversion(self):
        d = date(2026, 11, 18)
        self.assertEqual(normalize_datetime_utc(d), "2026-11-18T00:00:00Z")

    def test_epoch_timestamp_conversion(self):
        # Seconds epoch for 2026-11-18T06:00:00Z
        # 2026-11-18T06:00:00Z is 1794981600 seconds
        dt_target = datetime(2026, 11, 18, 6, 0, 0, tzinfo=timezone.utc)
        epoch_sec = dt_target.timestamp()
        self.assertEqual(normalize_datetime_utc(epoch_sec), "2026-11-18T06:00:00Z")
        self.assertEqual(normalize_datetime_utc(int(epoch_sec)), "2026-11-18T06:00:00Z")

        # Milliseconds epoch
        epoch_ms = epoch_sec * 1000.0
        self.assertEqual(normalize_datetime_utc(epoch_ms), "2026-11-18T06:00:00Z")
        self.assertEqual(normalize_datetime_utc(int(epoch_ms)), "2026-11-18T06:00:00Z")

    def test_string_timezone_offsets(self):
        # +09:00 (Tokyo)
        self.assertEqual(
            normalize_datetime_utc("2026-11-18T15:00:00+09:00"),
            "2026-11-18T06:00:00Z",
        )
        # -05:00 (New York EST)
        self.assertEqual(
            normalize_datetime_utc("2026-11-18T01:00:00-05:00"),
            "2026-11-18T06:00:00Z",
        )
        # Fractional seconds with Z
        self.assertEqual(
            normalize_datetime_utc("2026-11-18T06:00:00.123456Z"),
            "2026-11-18T06:00:00Z",
        )

    def test_rfc2822_strings(self):
        # Standard RSS pubDate format
        rfc_str = "Wed, 18 Nov 2026 15:00:00 +0900"
        self.assertEqual(normalize_datetime_utc(rfc_str), "2026-11-18T06:00:00Z")

        rfc_gmt = "Wed, 18 Nov 2026 06:00:00 GMT"
        self.assertEqual(normalize_datetime_utc(rfc_gmt), "2026-11-18T06:00:00Z")

    def test_japanese_era_dates(self):
        # Reiwa 8 (2026)
        self.assertEqual(parse_japanese_era_date("R8.11.17"), "2026-11-17")
        self.assertEqual(normalize_datetime_utc("R8.11.17"), "2026-11-17T00:00:00Z")

        # Reiwa 6 (2024)
        self.assertEqual(parse_japanese_era_date("R6.5.1"), "2024-05-01")
        self.assertEqual(normalize_datetime_utc("R6.05.01"), "2024-05-01T00:00:00Z")

        # Heisei 30 (2018)
        self.assertEqual(parse_japanese_era_date("H30.12.31"), "2018-12-31")

        # Invalid era string
        with self.assertRaises(ValueError):
            parse_japanese_era_date("X1.1.1")

    def test_forex_factory_event_datetime(self):
        # Specific time
        self.assertEqual(
            parse_event_datetime("11-15-2026", "11:50pm"),
            "2026-11-15T23:50:00Z",
        )
        # Morning time
        self.assertEqual(
            parse_event_datetime("2026-11-18", "2:00am"),
            "2026-11-18T02:00:00Z",
        )
        # All day event
        self.assertEqual(
            parse_event_datetime("2026-11-18", "All Day"),
            "2026-11-18T00:00:00Z",
        )
        # Tentative event
        self.assertEqual(
            parse_event_datetime("2026-11-18", "Tentative"),
            "2026-11-18T00:00:00Z",
        )

    def test_none_and_invalid_timestamp_handling(self):
        # Raises ValueError by default
        with self.assertRaises(ValueError):
            normalize_datetime_utc(None)

        with self.assertRaises(ValueError):
            normalize_datetime_utc("")

        with self.assertRaises(ValueError):
            normalize_datetime_utc("invalid-timestamp-xyz")

        # Fallback now returns valid ISO-8601 UTC
        now_fb = normalize_datetime_utc(None, fallback_now=True)
        self.assertTrue(is_valid_iso8601_utc(now_fb))

        now_fb_invalid = normalize_datetime_utc("invalid", fallback_now=True)
        self.assertTrue(is_valid_iso8601_utc(now_fb_invalid))


class TestDeduplicationHashing(unittest.TestCase):
    """Test URL and Event deduplication hashing."""

    def test_url_hash_format_and_repeatability(self):
        url = "https://www.reuters.com/markets/currencies/japan-fx-intervention-watch-2026-11-18"
        h1 = generate_url_hash(url)
        h2 = generate_url_hash(url)

        # Repeatability
        self.assertEqual(h1, h2)
        # Length must be exactly 64 hex characters (SHA-256)
        self.assertEqual(len(h1), 64)
        # Must be valid hexadecimal
        int(h1, 16)
        # Whitespace trimming check
        self.assertEqual(generate_url_hash(f"  {url}  \n"), h1)

    def test_url_hash_collision_resistance(self):
        url_a = "https://reuters.com/article/1"
        url_b = "https://reuters.com/article/2"
        self.assertNotEqual(generate_url_hash(url_a), generate_url_hash(url_b))

    def test_url_hash_validation(self):
        with self.assertRaises(ValueError):
            generate_url_hash("")
        with self.assertRaises(ValueError):
            generate_url_hash("   ")

    def test_event_hash_format_and_repeatability(self):
        ts = "2026-11-18T13:30:00Z"
        curr = "USD"
        title = "Core CPI m/m"

        h1 = generate_event_hash(ts, curr, title)
        h2 = generate_event_hash(ts, curr, title)

        # Repeatability
        self.assertEqual(h1, h2)
        # Length must be exactly 32 hex characters
        self.assertEqual(len(h1), 32)
        int(h1, 16)

        # Case-insensitivity for currency & whitespace tolerance
        h_variant = generate_event_hash(
            "2026-11-18T13:30:00Z",
            "  usd  ",
            " Core CPI m/m ",
        )
        self.assertEqual(h1, h_variant)

        # Verify against reference SHA-256 calculation
        expected_payload = f"{ts}:USD:{title}".encode("utf-8")
        expected_hash = hashlib.sha256(expected_payload).hexdigest()[:32].lower()
        self.assertEqual(h1, expected_hash)

    def test_event_hash_collision_resistance(self):
        ts = "2026-11-18T13:30:00Z"
        h_usd = generate_event_hash(ts, "USD", "Retail Sales")
        h_jpy = generate_event_hash(ts, "JPY", "Retail Sales")
        h_diff_time = generate_event_hash("2026-11-18T14:00:00Z", "USD", "Retail Sales")
        h_diff_title = generate_event_hash(ts, "USD", "Retail Sales Core")

        hashes = {h_usd, h_jpy, h_diff_time, h_diff_title}
        self.assertEqual(len(hashes), 4, "All event hashes must be distinct")

    def test_event_hash_validation(self):
        ts = "2026-11-18T13:30:00Z"
        with self.assertRaises(ValueError):
            generate_event_hash(ts, "", "Title")
        with self.assertRaises(ValueError):
            generate_event_hash(ts, "USD", "")


class TestSpreadCalculation(unittest.TestCase):
    """Test US10Y - JGB10Y spread calculation, rounding, slope, and edge cases."""

    def test_standard_spread_calculation(self):
        us_10y = 5.1030
        jp_10y = 2.9250
        spread = compute_yield_spread(us_10y, jp_10y)
        # 5.1030 - 2.9250 = 2.1780
        self.assertEqual(spread, 2.1780)

        # Basis points
        bps = compute_spread_bps(spread)
        self.assertEqual(bps, 217.80)

    def test_precision_and_rounding(self):
        # 5.123456 - 2.987654 = 2.135802 -> 2.1358
        spread = compute_yield_spread(5.123456, 2.987654, precision=4)
        self.assertEqual(spread, 2.1358)

        # Custom precision
        spread_2 = compute_yield_spread(5.123456, 2.987654, precision=2)
        self.assertEqual(spread_2, 2.14)

    def test_none_and_nan_handling(self):
        self.assertIsNone(compute_yield_spread(None, 2.925))
        self.assertIsNone(compute_yield_spread(5.103, None))
        self.assertIsNone(compute_yield_spread(None, None))
        self.assertIsNone(compute_yield_spread(float("nan"), 2.925))
        self.assertIsNone(compute_yield_spread(5.103, float("inf")))

        self.assertIsNone(compute_spread_bps(None))
        self.assertIsNone(compute_spread_bps(float("nan")))

    def test_curve_slope_calculation(self):
        # US 2s10s slope = US10Y - US2Y
        # Normal upward-sloping curve
        slope_normal = compute_us_2s10s_slope(5.12, 4.83)
        self.assertEqual(slope_normal, 0.29)

        # Inverted curve
        slope_inverted = compute_us_2s10s_slope(4.50, 4.80)
        self.assertEqual(slope_inverted, -0.30)

        # Flat curve
        slope_flat = compute_us_2s10s_slope(5.00, 5.00)
        self.assertEqual(slope_flat, 0.00)

        # None handling
        self.assertIsNone(compute_us_2s10s_slope(None, 4.83))
        self.assertIsNone(compute_us_2s10s_slope(5.12, None))


class TestMultiSourceParsers(unittest.TestCase):
    """Test parser functions for FRED, Yahoo Finance, and Japan MoF inputs."""

    def test_fred_yield_parser(self):
        self.assertEqual(parse_fred_yield("5.12"), 5.12)
        self.assertEqual(parse_fred_yield({"value": "4.83", "date": "2026-11-16"}), 4.83)

        # FRED holiday / missing values
        self.assertIsNone(parse_fred_yield("."))
        self.assertIsNone(parse_fred_yield({"value": "."}))
        self.assertIsNone(parse_fred_yield(None))

    def test_extract_fred_series(self):
        observations = [
            {"date": "2026-11-13", "value": "5.10"},
            {"date": "2026-11-14", "value": "."},  # Weekend / Holiday
            {"date": "2026-11-15", "value": "."},
            {"date": "2026-11-16", "value": "5.12"},
        ]
        series = extract_fred_series(observations)
        self.assertEqual(len(series), 2)
        self.assertEqual(series["2026-11-13"], 5.10)
        self.assertEqual(series["2026-11-16"], 5.12)
        self.assertNotIn("2026-11-14", series)

    def test_yahoo_tnx_parser(self):
        # Direct float
        self.assertEqual(parse_yahoo_tnx(5.103), 5.103)
        # String with percentage
        self.assertEqual(parse_yahoo_tnx("5.103%"), 5.103)
        # Candle dict
        candle = {"Open": 5.09, "Close": 5.103, "High": 5.11, "Low": 5.08}
        self.assertEqual(parse_yahoo_tnx(candle), 5.103)
        self.assertIsNone(parse_yahoo_tnx(None))

    def test_mof_csv_parser(self):
        sample_mof_csv = (
            "国債金利情報\n"
            "基準日,1年,2年,5年,10年,20年,30年\n"
            "R8.11.13,0.850,1.100,1.850,2.920,3.400,3.650\n"
            "R8.11.16,0.855,1.105,1.855,2.927,3.410,3.660\n"
            "R8.11.17,0.852,1.102,1.851,2.925,3.405,3.655\n"
        )
        parsed = parse_mof_csv(sample_mof_csv, target_tenor="10年")
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed["2026-11-13"], 2.920)
        self.assertEqual(parsed["2026-11-16"], 2.927)
        self.assertEqual(parsed["2026-11-17"], 2.925)


class TestHolidayAndMisalignmentHandling(unittest.TestCase):
    """Test bond market holiday alignment, forward-fill, and stale data limits."""

    def test_forward_fill_on_holiday_misalignment(self):
        # Scenario:
        # 2026-11-16 (Mon): Both open
        # 2026-11-17 (Tue): US is open, JP is closed (Japanese holiday)
        # 2026-11-18 (Wed): US is closed (US holiday), JP is open
        us_series = {
            "2026-11-16": 5.12,
            "2026-11-17": 5.15,
        }
        jp_series = {
            "2026-11-16": 2.92,
            "2026-11-18": 2.95,
        }
        us_2y_series = {
            "2026-11-16": 4.83,
            "2026-11-17": 4.85,
        }

        records = align_yield_series(
            us_10y_series=us_series,
            jp_10y_series=jp_series,
            us_2y_series=us_2y_series,
            fill_method="ffill",
            max_stale_days=5,
        )

        self.assertEqual(len(records), 3)

        # Day 1: Both markets open
        r1 = records[0]
        self.assertEqual(r1.timestamp_utc, "2026-11-16T00:00:00Z")
        self.assertEqual(r1.us_10y, 5.12)
        self.assertEqual(r1.jp_10y, 2.92)
        self.assertEqual(r1.spread_10y, round(5.12 - 2.92, 4))
        self.assertFalse(r1.is_us_stale)
        self.assertFalse(r1.is_jp_stale)

        # Day 2: JP holiday (carried forward from 2026-11-16)
        r2 = records[1]
        self.assertEqual(r2.timestamp_utc, "2026-11-17T00:00:00Z")
        self.assertEqual(r2.us_10y, 5.15)
        self.assertEqual(r2.jp_10y, 2.92)  # Carried forward
        self.assertEqual(r2.spread_10y, round(5.15 - 2.92, 4))
        self.assertFalse(r2.is_us_stale)
        self.assertTrue(r2.is_jp_stale)
        self.assertEqual(r2.jp_observation_date, "2026-11-16")
        self.assertIn("JP holiday/closed", r2.notes or "")

        # Day 3: US holiday (carried forward from 2026-11-17)
        r3 = records[2]
        self.assertEqual(r3.timestamp_utc, "2026-11-18T00:00:00Z")
        self.assertEqual(r3.us_10y, 5.15)  # Carried forward
        self.assertEqual(r3.jp_10y, 2.95)
        self.assertEqual(r3.spread_10y, round(5.15 - 2.95, 4))
        self.assertTrue(r3.is_us_stale)
        self.assertFalse(r3.is_jp_stale)
        self.assertEqual(r3.us_observation_date, "2026-11-17")
        self.assertIn("US holiday/closed", r3.notes or "")

    def test_max_stale_days_cutoff(self):
        # US data from 2026-11-01, JP data on 2026-11-10 (9 days later)
        us_series = {"2026-11-01": 5.00}
        jp_series = {"2026-11-10": 2.90}

        records = align_yield_series(
            us_10y_series=us_series,
            jp_10y_series=jp_series,
            fill_method="ffill",
            max_stale_days=5,
        )
        self.assertEqual(len(records), 2)
        r_old = records[0]
        self.assertEqual(r_old.us_10y, 5.00)
        self.assertIsNone(r_old.jp_10y)
        self.assertIsNone(r_old.spread_10y)

        r_new = records[1]
        self.assertEqual(r_new.jp_10y, 2.90)
        # US is 9 days stale (> 5 days), so it should be dropped/None
        self.assertIsNone(r_new.us_10y)
        self.assertIsNone(r_new.spread_10y)

    def test_live_spread_calculation(self):
        now_ts = "2026-11-18T10:00:00Z"
        us_ts = "2026-11-18T09:45:00Z"
        jp_ts = "2026-11-18T08:00:00Z"

        rec = calculate_live_spread(
            us_10y=5.103,
            jp_10y=2.925,
            us_2y=4.825,
            us_timestamp_utc=us_ts,
            jp_timestamp_utc=jp_ts,
            evaluation_timestamp_utc=now_ts,
            max_misalignment_hours=24.0,
        )

        self.assertEqual(rec.timestamp_utc, now_ts)
        self.assertEqual(rec.spread_10y, 2.178)
        self.assertEqual(rec.spread_bps, 217.8)
        self.assertEqual(rec.us_2s10s_slope, 0.278)
        self.assertFalse(rec.is_us_stale)
        self.assertFalse(rec.is_jp_stale)


class TestDatabasePersistence(unittest.TestCase):
    """Test saving yield spread records into SQLite database."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
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

    def tearDown(self):
        self.conn.close()

    def test_save_and_retrieve_spread_record(self):
        rec = YieldSpreadRecord(
            timestamp_utc="2026-11-18T08:00:00Z",
            us_2y=4.83,
            us_10y=5.12,
            jp_10y=2.925,
            spread_10y=2.195,
            source="FRED+MoF",
        )
        save_yield_spread(self.conn, rec)

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM yield_spreads WHERE timestamp_utc = ?", ("2026-11-18T08:00:00Z",))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "2026-11-18T08:00:00Z")
        self.assertEqual(row[1], 4.83)
        self.assertEqual(row[2], 5.12)
        self.assertEqual(row[3], 2.925)
        self.assertEqual(row[4], 2.195)
        self.assertEqual(row[5], "FRED+MoF")

        # Test duplicate key replacement
        updated_rec = YieldSpreadRecord(
            timestamp_utc="2026-11-18T08:00:00Z",
            us_2y=4.85,
            us_10y=5.15,
            jp_10y=2.925,
            spread_10y=2.225,
            source="FRED+MoF-Updated",
        )
        save_yield_spread(self.conn, updated_rec)
        cursor.execute("SELECT count(*), us_10y, spread_10y FROM yield_spreads")
        count, us10, sp = cursor.fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(us10, 5.15)
        self.assertEqual(sp, 2.225)


if __name__ == "__main__":
    unittest.main()
