"""
Forex Factory Economic Calendar Mapping and Validation.
Task P1-03: Economic Calendar Mapping for USD/JPY Signal Reader.

This script:
1. Fetches the Forex Factory weekly calendar feed (JSON primary, with XML/CSV fallback).
2. Filters events to USD and JPY currencies.
3. Normalizes timestamps to ISO-8601 UTC and generates deterministic SHA256 event hashes.
4. Maps fields into the macro_events SQLite table schema.
5. Verifies insertion and deduplication into SQLite.
6. Saves validation outputs to calendar_validation_results.json.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests

JSON_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
XML_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
CSV_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.csv"

CACHE_FILE = Path("data/raw_calendar_thisweek.json")
RESULTS_FILE = Path("calendar_validation_results.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/xml, */*",
}


def parse_json_timestamp(date_str: str) -> str:
    """
    Parses Forex Factory JSON date string (e.g., '2026-11-15T18:50:00-05:00')
    and returns normalized ISO-8601 UTC (e.g., '2026-11-15T23:50:00Z').
    Handles edge cases like date-only strings without time components.
    """
    try:
        # Standard ISO-8601 with offset
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            # Assume UTC if naive
            dt = dt.replace(tzinfo=timezone.utc)
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        # Fallback regex parsing if format deviates
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", date_str)
        if m:
            return f"{m.group(1)}T00:00:00Z"
        return date_str


def parse_xml_timestamp(date_str: str, time_str: str) -> str:
    """
    Parses Forex Factory XML date ('11-15-2026') and time ('11:50pm', 'All Day', 'Tentative')
    into normalized ISO-8601 UTC.
    Note: Forex Factory XML times are in UTC / GMT.
    """
    try:
        dt_base = datetime.strptime(date_str.strip(), "%m-%d-%Y")
        time_clean = time_str.strip().lower()
        if not time_clean or "day" in time_clean or "tentative" in time_clean:
            # All day or tentative event - midnight UTC
            return dt_base.strftime("%Y-%m-%dT00:00:00Z")

        # Parse 12-hour format e.g. 11:50pm, 2:00am
        t = datetime.strptime(time_clean, "%I:%M%p").time()
        combined = datetime.combine(dt_base.date(), t, tzinfo=timezone.utc)
        return combined.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return f"{date_str}T00:00:00Z"


def generate_event_hash(timestamp_utc: str, currency: str, title: str) -> str:
    """
    Generates a deterministic 32-character SHA256 hex digest for an event.
    """
    payload = f"{timestamp_utc}:{currency.strip().upper()}:{title.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def fetch_calendar_feed() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Fetches weekly calendar data from Forex Factory.
    Tries JSON first; if rate-limited (HTTP 429), checks local cache or falls back to XML.
    """
    meta = {
        "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "primary_url": JSON_FEED_URL,
        "source_used": None,
        "status_code": None,
        "rate_limited": False,
        "retry_after_seconds": None,
    }

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"[*] Requesting Forex Factory calendar JSON: {JSON_FEED_URL}")
    try:
        resp = requests.get(JSON_FEED_URL, headers=HEADERS, timeout=12)
        meta["status_code"] = resp.status_code

        if resp.status_code == 200 and "application/json" in resp.headers.get("Content-Type", ""):
            data = resp.json()
            meta["source_used"] = "json_live"
            # Cache the fresh JSON data
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"[+] Successfully fetched {len(data)} events via live JSON feed.")
            return data, meta

        if resp.status_code == 429:
            meta["rate_limited"] = True
            retry_after = resp.headers.get("Retry-After")
            meta["retry_after_seconds"] = int(retry_after) if retry_after and retry_after.isdigit() else 300
            print(f"[!] Rate limited (HTTP 429). Retry-After: {meta['retry_after_seconds']}s")

    except Exception as exc:
        print(f"[!] Live JSON request failed: {exc}")
        meta["json_error"] = str(exc)

    # If live JSON was rate-limited or failed, check local cache first
    if CACHE_FILE.exists():
        print(f"[*] Loading calendar data from local cache: {CACHE_FILE}")
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            meta["source_used"] = "json_cache"
            return data, meta
        except Exception as exc:
            print(f"[!] Cache read failed: {exc}")

    # Fallback to XML feed if cache is unavailable
    print(f"[*] Attempting XML feed fallback: {XML_FEED_URL}")
    try:
        resp_xml = requests.get(XML_FEED_URL, headers=HEADERS, timeout=12)
        if resp_xml.status_code == 200:
            root = ET.fromstring(resp_xml.content)
            events = []
            for ev in root.findall("event"):
                title = ev.findtext("title", "")
                country = ev.findtext("country", "")
                date_text = ev.findtext("date", "")
                time_text = ev.findtext("time", "")
                impact = ev.findtext("impact", "")
                forecast = ev.findtext("forecast", "")
                previous = ev.findtext("previous", "")
                url = ev.findtext("url", "")
                iso_date = parse_xml_timestamp(date_text, time_text)

                events.append({
                    "title": title,
                    "country": country,
                    "date": iso_date,
                    "impact": impact,
                    "forecast": forecast,
                    "previous": previous,
                    "url": url,
                })
            meta["source_used"] = "xml_fallback"
            print(f"[+] Successfully fetched {len(events)} events via XML fallback.")
            return events, meta
    except Exception as exc:
        print(f"[!] XML fallback failed: {exc}")
        meta["xml_error"] = str(exc)

    raise RuntimeError("Failed to fetch calendar feed from JSON, cache, and XML fallback.")


def map_feed_to_macro_events(raw_events: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Filters events to USD & JPY and maps them to the macro_events table schema:
      - event_hash (TEXT PRIMARY KEY)
      - timestamp_utc (TEXT NOT NULL)
      - currency (TEXT NOT NULL)
      - title (TEXT NOT NULL)
      - impact (TEXT: High, Medium, Low, Non-Economic)
      - forecast (TEXT or None)
      - previous (TEXT or None)
      - actual (TEXT or None)
    """
    mapped_events: List[Dict[str, Any]] = []
    currency_counts: Dict[str, int] = {}
    impact_counts: Dict[str, int] = {}

    for item in raw_events:
        curr = (item.get("country") or item.get("currency") or "").strip().upper()
        impact = (item.get("impact") or "Non-Economic").strip()
        title = (item.get("title") or "").strip()
        raw_date = item.get("date") or ""

        # Normalize timestamp to UTC ISO-8601
        timestamp_utc = parse_json_timestamp(raw_date)

        # Standardize impact labels
        if not impact or impact.lower() in ("holiday", ""):
            impact = "Non-Economic"

        currency_counts[curr] = currency_counts.get(curr, 0) + 1

        # Target USD and JPY only
        if curr not in ("USD", "JPY"):
            continue

        impact_counts[impact] = impact_counts.get(impact, 0) + 1

        forecast_val = item.get("forecast")
        prev_val = item.get("previous")
        actual_val = item.get("actual")

        # Clean empty strings to None (NULL in SQL)
        forecast_str = str(forecast_val).strip() if forecast_val is not None and str(forecast_val).strip() != "" else None
        prev_str = str(prev_val).strip() if prev_val is not None and str(prev_val).strip() != "" else None
        actual_str = str(actual_val).strip() if actual_val is not None and str(actual_val).strip() != "" else None

        event_hash = generate_event_hash(timestamp_utc, curr, title)

        mapped_events.append({
            "event_hash": event_hash,
            "timestamp_utc": timestamp_utc,
            "currency": curr,
            "title": title,
            "impact": impact,
            "forecast": forecast_str,
            "previous": prev_str,
            "actual": actual_str,
            "raw_date_input": raw_date,
        })

    # Sort deterministically by timestamp_utc, then currency, then title
    mapped_events.sort(key=lambda x: (x["timestamp_utc"], x["currency"], x["title"]))

    stats = {
        "total_feed_events": len(raw_events),
        "total_usd_jpy_events": len(mapped_events),
        "usd_events": sum(1 for e in mapped_events if e["currency"] == "USD"),
        "jpy_events": sum(1 for e in mapped_events if e["currency"] == "JPY"),
        "impact_breakdown_usd_jpy": impact_counts,
        "all_currencies_breakdown": currency_counts,
    }

    return mapped_events, stats


def verify_sqlite_storage(mapped_events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Initializes an in-memory SQLite database using the official schema,
    tests insertion, deduplication, and query retrieval.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    schema_sql = """
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS macro_events (
        event_hash TEXT PRIMARY KEY,
        timestamp_utc TEXT NOT NULL,
        currency TEXT NOT NULL,
        title TEXT NOT NULL,
        impact TEXT,     -- High, Medium, Low, Non-Economic
        forecast TEXT,
        previous TEXT,
        actual TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_macro_time ON macro_events(timestamp_utc DESC);
    CREATE INDEX IF NOT EXISTS idx_macro_curr_impact ON macro_events(currency, impact);
    """
    conn.executescript(schema_sql)

    insert_sql = """
    INSERT OR REPLACE INTO macro_events (
        event_hash, timestamp_utc, currency, title, impact, forecast, previous, actual
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """

    records = [
        (
            e["event_hash"],
            e["timestamp_utc"],
            e["currency"],
            e["title"],
            e["impact"],
            e["forecast"],
            e["previous"],
            e["actual"],
        )
        for e in mapped_events
    ]

    # Execute batch insert
    conn.executemany(insert_sql, records)
    conn.commit()

    total_inserted = conn.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0]

    # Test deduplication by inserting the exact same records again
    conn.executemany(insert_sql, records)
    conn.commit()
    total_after_dedup = conn.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0]

    # Query High Impact USD events
    cur = conn.execute(
        """
        SELECT event_hash, timestamp_utc, currency, title, impact, forecast, previous
        FROM macro_events
        WHERE currency = 'USD' AND impact = 'High'
        ORDER BY timestamp_utc ASC
        """
    )
    high_impact_usd = [dict(row) for row in cur.fetchall()]

    # Query JPY events
    cur = conn.execute(
        """
        SELECT event_hash, timestamp_utc, currency, title, impact, forecast, previous
        FROM macro_events
        WHERE currency = 'JPY'
        ORDER BY timestamp_utc ASC
        """
    )
    jpy_events = [dict(row) for row in cur.fetchall()]

    conn.close()

    return {
        "total_rows_inserted": total_inserted,
        "deduplication_verified": (total_inserted == total_after_dedup),
        "high_impact_usd_count": len(high_impact_usd),
        "high_impact_usd_sample": high_impact_usd[:5],
        "jpy_events_count": len(jpy_events),
        "jpy_events_sample": jpy_events[:5],
    }


def main():
    print("=" * 60)
    print("P1-03: Economic Calendar Mapping & SQLite Verification")
    print("=" * 60)

    raw_events, fetch_meta = fetch_calendar_feed()
    mapped_events, stats = map_feed_to_macro_events(raw_events)
    sqlite_results = verify_sqlite_storage(mapped_events)

    # Mapping definition schema documentation
    field_mapping_spec = {
        "event_hash": {
            "source_fields": ["date (normalized UTC)", "country (currency)", "title"],
            "transformation": "sha256('{timestamp_utc}:{currency}:{title}').hexdigest()[:32]",
            "sqlite_type": "TEXT PRIMARY KEY",
            "description": "Deterministic 32-char hex digest preventing duplicate event entries.",
        },
        "timestamp_utc": {
            "source_fields": ["date"],
            "transformation": "datetime.fromisoformat(date).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')",
            "sqlite_type": "TEXT NOT NULL",
            "description": "Normalized ISO-8601 UTC timestamp string.",
        },
        "currency": {
            "source_fields": ["country"],
            "transformation": "country.strip().upper() (filtered to 'USD' and 'JPY')",
            "sqlite_type": "TEXT NOT NULL",
            "description": "Three-letter ISO currency code.",
        },
        "title": {
            "source_fields": ["title"],
            "transformation": "title.strip()",
            "sqlite_type": "TEXT NOT NULL",
            "description": "Official economic indicator or release name.",
        },
        "impact": {
            "source_fields": ["impact"],
            "transformation": "Normalized to 'High', 'Medium', 'Low', or 'Non-Economic'",
            "sqlite_type": "TEXT",
            "description": "Market impact rating.",
        },
        "forecast": {
            "source_fields": ["forecast"],
            "transformation": "String value or NULL if empty/omitted",
            "sqlite_type": "TEXT",
            "description": "Consensus market forecast estimate.",
        },
        "previous": {
            "source_fields": ["previous"],
            "transformation": "String value or NULL if empty/omitted",
            "sqlite_type": "TEXT",
            "description": "Prior period published value.",
        },
        "actual": {
            "source_fields": ["actual"],
            "transformation": "String value or NULL if omitted or pending release",
            "sqlite_type": "TEXT",
            "description": "Actual published figure once released.",
        },
    }

    full_results = {
        "execution_timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fetch_metadata": fetch_meta,
        "feed_statistics": stats,
        "sqlite_verification": sqlite_results,
        "field_mapping_spec": field_mapping_spec,
        "sample_raw_events": raw_events[:5],
        "mapped_events_usd_jpy": mapped_events,
    }

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(full_results, f, indent=2)

    print(f"\n[+] Processing Summary:")
    print(f"    - Source used: {fetch_meta.get('source_used')}")
    print(f"    - Total feed events: {stats['total_feed_events']}")
    print(f"    - Total USD & JPY events: {stats['total_usd_jpy_events']} (USD: {stats['usd_events']}, JPY: {stats['jpy_events']})")
    print(f"    - Impact breakdown: {stats['impact_breakdown_usd_jpy']}")
    print(f"    - SQLite rows inserted: {sqlite_results['total_rows_inserted']}")
    print(f"    - Deduplication verified: {sqlite_results['deduplication_verified']}")
    print(f"    - Results saved to: {RESULTS_FILE.resolve()}")


if __name__ == "__main__":
    main()
