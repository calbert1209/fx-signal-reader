"""
Unit and crisis stress tests for historical calendar archival (P7-07) and
curated MoF/BoJ FX intervention crisis headlines (P7-08).
"""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fx_signal_reader.agent.nodes import risk_committee_node
from fx_signal_reader.agent.state import (
    InterventionAlertLevel,
    TradeAction,
    YieldBias,
    create_initial_state,
)
from fx_signal_reader.backtest.backfill_calendar import (
    backfill_calendar,
    generate_historical_macro_events,
    parse_calendar_archive_file,
)
from fx_signal_reader.backtest.backfill_intervention import (
    CURATED_INTERVENTION_HEADLINES,
    backfill_intervention_headlines,
)
from fx_signal_reader.collectors.calendar import CalendarCollector
from fx_signal_reader.collectors.news_rss import NewsRSSCollector
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH
from fx_signal_reader.mcp.server import (
    get_recent_mof_alerts,
    get_upcoming_macro_events,
)


def _create_empty_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return path


class TestHistoricalCalendarArchival(unittest.TestCase):
    """Tests for P7-07 multi-year macro calendar schedule generation and archive ingestion."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fx_cal_arch_")
        self.tmp = Path(self._tmpdir.name)
        self.db_path = _create_empty_db(self.tmp / "cal.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_generate_schedule_and_hash_compatibility(self) -> None:
        events = generate_historical_macro_events("2024-01-01", "2024-12-31")
        self.assertGreater(len(events), 90)
        currencies = {e["currency"] for e in events}
        self.assertEqual(currencies, {"USD", "JPY"})
        self.assertEqual({e["impact"] for e in events}, {"High", "Medium"})

        # Verify event_hash matches CalendarCollector.generate_event_hash
        sample = events[0]
        expected_hash = CalendarCollector.generate_event_hash(
            sample["timestamp_utc"], sample["currency"], sample["title"]
        )
        self.assertEqual(sample["event_hash"], expected_hash)
        self.assertRegex(sample["timestamp_utc"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_parse_json_and_csv_archives_filters_and_normalizes(self) -> None:
        json_path = self.tmp / "ff_archive.json"
        json_path.write_text(
            json.dumps(
                [
                    {
                        "title": "Archive FOMC Decision",
                        "country": "USD",
                        "date": "2024-05-01T14:00:00-04:00",
                        "impact": "High",
                        "forecast": "5.50%",
                        "previous": "5.50%",
                    },
                    {
                        "title": "Ignored Low Event",
                        "country": "USD",
                        "date": "2024-05-01T10:00:00Z",
                        "impact": "Low",
                    },
                    {
                        "title": "Ignored EUR Event",
                        "country": "EUR",
                        "date": "2024-05-01T10:00:00Z",
                        "impact": "High",
                    },
                ]
            ),
            encoding="utf-8",
        )
        parsed_json = parse_calendar_archive_file(json_path)
        self.assertEqual(len(parsed_json), 1)
        self.assertEqual(parsed_json[0]["timestamp_utc"], "2024-05-01T18:00:00Z")
        self.assertEqual(parsed_json[0]["currency"], "USD")
        self.assertEqual(parsed_json[0]["title"], "Archive FOMC Decision")

        csv_path = self.tmp / "cal_archive.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["timestamp_utc", "currency", "title", "impact", "forecast", "previous", "actual"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "timestamp_utc": "2024-07-11T12:30:00Z",
                    "currency": "USD",
                    "title": "Archive US CPI y/y",
                    "impact": "High",
                    "forecast": "3.1%",
                    "previous": "3.3%",
                    "actual": "3.0%",
                }
            )
            writer.writerow(
                {
                    "timestamp_utc": "2024-07-11T12:30:00Z",
                    "currency": "GBP",
                    "title": "Archive UK GDP",
                    "impact": "High",
                    "forecast": "0.1%",
                    "previous": "0.0%",
                    "actual": "0.2%",
                }
            )
        parsed_csv = parse_calendar_archive_file(csv_path)
        self.assertEqual(len(parsed_csv), 1)
        self.assertEqual(parsed_csv[0]["title"], "Archive US CPI y/y")

    def test_backfill_calendar_idempotency_and_mcp_pit_query(self) -> None:
        res1 = backfill_calendar(
            db_path=self.db_path,
            start_date="2024-11-01",
            end_date="2024-11-30",
        )
        res2 = backfill_calendar(
            db_path=self.db_path,
            start_date="2024-11-01",
            end_date="2024-11-30",
        )
        self.assertEqual(res1["status"], "SUCCESS")
        self.assertEqual(res1["rows_inserted"], res2["rows_inserted"])

        conn = sqlite3.connect(self.db_path)
        try:
            db_rows = conn.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(db_rows, res1["rows_inserted"])

        # Query around NFP (First Friday of Nov 2024 = 2024-11-01T13:30:00Z)
        upcoming = get_upcoming_macro_events(
            currency="USD",
            impact="High",
            db_path=str(self.db_path),
            as_of_utc="2024-11-01T12:00:00Z",
            past_hours=4,
            future_hours=24,
        )
        self.assertTrue(any("Non-Farm Employment Change" in ev["title"] for ev in upcoming))


class TestHistoricalInterventionCrisisStress(unittest.TestCase):
    """Tests for P7-08 curated MoF crisis headline backfill and RiskCommittee veto activation."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fx_iv_crisis_")
        self.tmp = Path(self._tmpdir.name)
        self.db_path = _create_empty_db(self.tmp / "crisis.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_curated_headlines_match_taxonomy_and_ingest_cleanly(self) -> None:
        evaluator = NewsRSSCollector()
        self.assertGreaterEqual(len(CURATED_INTERVENTION_HEADLINES), 20)
        for item in CURATED_INTERVENTION_HEADLINES:
            eval_res = evaluator.evaluate_headline(item["title"])
            self.assertGreaterEqual(
                eval_res["highest_tier_level"],
                2,
                f"Curated headline scored < Tier 2: {item['title']}",
            )
            self.assertGreater(
                len(eval_res["matched_entities"]),
                0,
                f"Curated headline matched no entities: {item['title']}",
            )

        summary = backfill_intervention_headlines(db_path=self.db_path)
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["rows_inserted"], len(CURATED_INTERVENTION_HEADLINES))
        self.assertGreater(summary["tier_counts"]["4"], 0)
        self.assertIn("2024-04/05 Intervention", summary["episodes_covered"])
        self.assertIn("2024-07 Intervention", summary["episodes_covered"])

        # Filtered PIT lookup inside April 2024 episode
        alerts_apr24 = get_recent_mof_alerts(
            min_tier=4,
            db_path=str(self.db_path),
            as_of_utc="2024-04-29T12:00:00Z",
            lookback_hours=48,
        )
        self.assertGreaterEqual(len(alerts_apr24), 1)
        self.assertEqual(alerts_apr24[0]["tier"], 4)

    def test_crisis_stress_test_vetoes_entries_during_historical_shocks(self) -> None:
        backfill_intervention_headlines(db_path=self.db_path)

        crisis_cases = [
            # (label, bar_timestamp_utc, pre_crash_spot, vix)
            ("Apr 2024 160.24 Intervention", "2024-04-29T08:00:00Z", 159.95, 15.0),
            ("Jul 2024 161.50 Intervention", "2024-07-11T16:00:00Z", 161.40, 13.5),
            ("Jan 2026 Flash Crash", "2026-01-22T00:00:00Z", 158.75, 16.0),
            ("Apr 2026 Flash Crash", "2026-04-29T12:00:00Z", 160.35, 15.5),
            ("Jul 2026 Surge Intervention", "2026-07-28T12:00:00Z", 163.80, 14.5),
            ("Nov 2026 160 Barrier", "2026-11-11T12:00:00Z", 159.85, 15.0),
        ]

        conn = sqlite3.connect(self.db_path)
        try:
            for _, ts, spot, vix in crisis_cases:
                conn.executemany(
                    "INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("JPY=X", ts, spot, spot, spot + 0.1, spot - 0.1, 0.0),
                        ("^VIX", ts, vix, vix, vix + 0.1, vix - 0.1, 0.0),
                        ("^GSPC", ts, 5900.0, 5890.0, 5910.0, 5885.0, 1000.0),
                        ("^N225", ts, 39000.0, 38900.0, 39100.0, 38850.0, 0.0),
                        ("GC=F", ts, 2600.0, 2595.0, 2605.0, 2590.0, 100.0),
                        ("^TNX", ts, 4.5, 4.5, 4.51, 4.49, 0.0),
                    ],
                )
            conn.commit()
        finally:
            conn.close()

        for label, ts, _, _ in crisis_cases:
            with self.subTest(episode=label):
                state = create_initial_state(run_id=f"crisis-{ts[:10]}", timestamp_utc=ts)
                state["yield_bias"] = YieldBias.BULLISH_USD.value
                out = risk_committee_node(
                    state,
                    db_path=str(self.db_path),
                    point_in_time=True,
                    mof_lookback_hours=48,
                )
                risk = out["risk_assessment"]
                self.assertEqual(risk["highest_mof_tier"], 4)
                self.assertEqual(risk["intervention_alert_level"], InterventionAlertLevel.CRITICAL.value)
                self.assertGreaterEqual(risk["intervention_risk_score"], 70.0)
                self.assertTrue(risk["veto_trade"])
                self.assertEqual(out["trade_proposal"]["action"], TradeAction.HOLD.value)
                self.assertEqual(out["trade_proposal"]["position_size_units"], 0)


if __name__ == "__main__":
    unittest.main()
