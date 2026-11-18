"""
Economic Calendar Collector for USD/JPY Signal Reader.
Ingests weekly Forex Factory calendar events:
  - Fetches live JSON endpoint with exponential retry
  - Falls back to local cached JSON (data/raw_calendar_thisweek.json) or live XML
  - Filters strictly for USD and JPY economic releases
  - Normalizes timestamps to ISO-8601 UTC and generates deterministic SHA256 hashes
  - Writes to SQLite table `macro_events` with primary key deduplication.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from fx_signal_reader.collectors.base import BaseCollector
from fx_signal_reader.core import DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH, PROJECT_ROOT


class CalendarCollector(BaseCollector):
    """
    Collector for Forex Factory weekly calendar releases.
    """

    JSON_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    XML_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "raw_calendar_thisweek.json"

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        schema_path: Path | str = DEFAULT_SCHEMA_PATH,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        jitter: bool = True,
        timeout: float = 12.0,
    ) -> None:
        super().__init__(
            db_path=db_path,
            schema_path=schema_path,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            jitter=jitter,
            timeout=timeout,
        )
        self.cache_path = Path(cache_path)

    @staticmethod
    def parse_json_timestamp(date_str: str) -> str:
        """
        Parses Forex Factory JSON date string (e.g., '2026-11-15T18:50:00-05:00')
        into normalized ISO-8601 UTC ('2026-11-15T23:50:00Z').
        """
        try:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            utc_dt = dt.astimezone(timezone.utc)
            return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            m = re.match(r"^(\d{4}-\d{2}-\d{2})", date_str)
            if m:
                return f"{m.group(1)}T00:00:00Z"
            return date_str

    @staticmethod
    def parse_xml_timestamp(date_str: str, time_str: str) -> str:
        """
        Parses Forex Factory XML date ('11-15-2026') and time ('11:50pm', 'All Day')
        into ISO-8601 UTC.
        """
        try:
            dt_base = datetime.strptime(date_str.strip(), "%m-%d-%Y")
            time_clean = time_str.strip().lower()
            if not time_clean or "day" in time_clean or "tentative" in time_clean:
                return dt_base.strftime("%Y-%m-%dT00:00:00Z")

            t = datetime.strptime(time_clean, "%I:%M%p").time()
            combined = datetime.combine(dt_base.date(), t, tzinfo=timezone.utc)
            return combined.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return f"{date_str}T00:00:00Z"

    @staticmethod
    def generate_event_hash(timestamp_utc: str, currency: str, title: str) -> str:
        """
        Computes a deterministic 32-character SHA256 hex digest for an event.
        """
        payload = (
            f"{timestamp_utc}:{currency.strip().upper()}:{title.strip()}".encode(
                "utf-8"
            )
        )
        return hashlib.sha256(payload).hexdigest()[:32]

    def fetch_feed_data(self) -> Tuple[List[Dict[str, Any]], str]:
        """
        Fetches calendar items from live JSON feed, cached file, or XML fallback.
        """
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Attempt live JSON (skip long 429 waits if cache exists)
        try:
            self.logger.info(f"Requesting live JSON feed: {self.JSON_FEED_URL}")
            resp = self.fetch_url(
                self.JSON_FEED_URL,
                headers={"Accept": "application/json"},
                retry_on_429=not self.cache_path.exists(),
            )
            if resp.status_code == 200 and "application/json" in resp.headers.get(
                "Content-Type", ""
            ):
                data = resp.json()
                # Update cache
                with open(self.cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                self.logger.info(f"Live JSON feed retrieved: {len(data)} items")
                return data, "live_json"
            elif resp.status_code == 429:
                self.logger.warning(
                    "Live JSON feed returned HTTP 429 (rate-limited). Falling back to cached data."
                )
        except Exception as exc:
            self.logger.warning(f"Live JSON fetch failed: {exc}")

        # 2. Check local cached JSON
        if self.cache_path.exists():
            self.logger.info(f"Loading calendar from cache: {self.cache_path}")
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data, "cached_json"
            except Exception as exc:
                self.logger.warning(f"Failed reading cache file: {exc}")

        # 3. Fallback to XML
        try:
            self.logger.info(f"Attempting XML fallback: {self.XML_FEED_URL}")
            resp = self.fetch_url(self.XML_FEED_URL)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                events = []
                for ev in root.findall("event"):
                    events.append(
                        {
                            "title": ev.findtext("title", ""),
                            "country": ev.findtext("country", ""),
                            "date": self.parse_xml_timestamp(
                                ev.findtext("date", ""), ev.findtext("time", "")
                            ),
                            "impact": ev.findtext("impact", ""),
                            "forecast": ev.findtext("forecast", ""),
                            "previous": ev.findtext("previous", ""),
                            "actual": ev.findtext("actual", ""),
                        }
                    )
                self.logger.info(f"XML fallback parsed: {len(events)} events")
                return events, "xml_fallback"
        except Exception as exc:
            self.logger.error(f"XML fallback failed: {exc}")

        raise RuntimeError(
            "All calendar sources (live JSON, local cache, XML) failed."
        )

    def collect(self) -> Dict[str, Any]:
        """
        Parses calendar releases, filters USD/JPY events, and inserts into `macro_events`.
        """
        raw_events, source_used = self.fetch_feed_data()
        conn = self.get_connection()

        mapped_events: List[Dict[str, Any]] = []
        usd_count = 0
        jpy_count = 0
        impact_distribution: Dict[str, int] = {}

        for item in raw_events:
            curr = (item.get("country") or item.get("currency") or "").strip().upper()
            if curr not in ("USD", "JPY"):
                continue

            raw_date = item.get("date") or ""
            timestamp_utc = self.parse_json_timestamp(raw_date)
            title = (item.get("title") or "").strip()
            impact = (item.get("impact") or "Non-Economic").strip()
            if not impact or impact.lower() in ("holiday", ""):
                impact = "Non-Economic"

            forecast = item.get("forecast")
            previous = item.get("previous")
            actual = item.get("actual")

            forecast_str = (
                str(forecast).strip()
                if forecast is not None and str(forecast).strip() != ""
                else None
            )
            prev_str = (
                str(previous).strip()
                if previous is not None and str(previous).strip() != ""
                else None
            )
            actual_str = (
                str(actual).strip()
                if actual is not None and str(actual).strip() != ""
                else None
            )

            event_hash = self.generate_event_hash(timestamp_utc, curr, title)

            if curr == "USD":
                usd_count += 1
            else:
                jpy_count += 1
            impact_distribution[impact] = impact_distribution.get(impact, 0) + 1

            mapped_events.append(
                {
                    "event_hash": event_hash,
                    "timestamp_utc": timestamp_utc,
                    "currency": curr,
                    "title": title,
                    "impact": impact,
                    "forecast": forecast_str,
                    "previous": prev_str,
                    "actual": actual_str,
                }
            )

        # Batch insert into macro_events
        conn.executemany(
            """
            INSERT OR REPLACE INTO macro_events
            (event_hash, timestamp_utc, currency, title, impact, forecast, previous, actual)
            VALUES (:event_hash, :timestamp_utc, :currency, :title, :impact, :forecast, :previous, :actual)
            """,
            mapped_events,
        )
        conn.commit()

        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM macro_events")
        total_in_db = cur.fetchone()[0]
        conn.close()

        summary = {
            "status": "SUCCESS",
            "source_used": source_used,
            "total_feed_events": len(raw_events),
            "usd_jpy_events_inserted": len(mapped_events),
            "db_macro_events_rows": total_in_db,
            "usd_events": usd_count,
            "jpy_events": jpy_count,
            "impact_distribution": impact_distribution,
        }
        self.logger.info(
            f"Calendar collection complete: {len(mapped_events)} USD/JPY events written "
            f"({total_in_db} total in DB, source: {source_used})"
        )
        return summary
