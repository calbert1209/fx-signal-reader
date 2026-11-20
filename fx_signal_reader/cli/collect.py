#!/usr/bin/env python3
"""
Production Collector Runner for USD/JPY Signal Reader.

Usage:
    python collector.py --collector all
    python collector.py --collector ticks --db-path usdjpy_signals.db
    python collector.py --collector calendar
    python collector.py --collector news
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fx_signal_reader.collectors.base import BaseCollector, migrate_yield_spreads_schema
from fx_signal_reader.collectors.calendar import CalendarCollector
from fx_signal_reader.collectors.market_ticks import MarketTicksCollector
from fx_signal_reader.collectors.news_rss import NewsRSSCollector
from fx_signal_reader.core import DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH
from fx_signal_reader.core.pair_profile import get_pair_db_path, get_pair_profile


def init_db(db_path: Path, schema_path: Path) -> sqlite3.Connection:
    """Initializes SQLite database in WAL mode and applies schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

    if schema_path.exists():
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        conn.commit()
    migrate_yield_spreads_schema(conn)
    return conn


def get_table_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Queries row counts across all 4 pipeline tables."""
    tables = ["market_ticks", "yield_spreads", "macro_events", "news_headlines"]
    counts: Dict[str, int] = {}
    cur = conn.cursor()
    for tbl in tables:
        try:
            cur.execute(f"SELECT count(*) FROM {tbl}")
            counts[tbl] = cur.fetchone()[0]
        except sqlite3.OperationalError:
            counts[tbl] = 0
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="FX Signal Reader Production Collector"
    )
    parser.add_argument(
        "--pair",
        type=str,
        default=os.getenv("FX_PAIR", "USD/JPY"),
        help="Currency pair to collect (default: USD/JPY or FX_PAIR env var)",
    )
    parser.add_argument(
        "--collector",
        choices=["all", "ticks", "calendar", "news"],
        default="all",
        help="Collector module to execute (default: all)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database file (defaults to pair-specific database)",
    )
    parser.add_argument(
        "--schema-path",
        type=str,
        default=str(DEFAULT_SCHEMA_PATH),
        help=f"Path to SQL schema file (default: {DEFAULT_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Maximum retries for network requests (default: 3)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )

    args = parser.parse_args()

    profile = get_pair_profile(args.pair)
    start_time = time.time()
    start_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.db_path:
        db_path = Path(args.db_path)
    elif os.getenv("FX_DB_PATH"):
        db_path = Path(os.getenv("FX_DB_PATH"))
    else:
        db_path = get_pair_db_path(profile)

    schema_path = Path(args.schema_path)

    print("=" * 70)
    print(f"{profile.pair} Signal Pipeline - Modular Collector Runner")
    print(f"Start Time (UTC): {start_utc}")
    print(f"Currency Pair   : {profile.pair}")
    print(f"Target Database : {db_path.resolve()}")
    print(f"Collector Target: {args.collector.upper()}")
    print("=" * 70)

    # Initialize database
    conn = init_db(db_path, schema_path)
    initial_counts = get_table_counts(conn)

    results: Dict[str, Any] = {}

    try:
        # Run Market Ticks & Yield Spreads
        if args.collector in ("all", "ticks"):
            print("\n>>> [1/3] Running Market Ticks & Yield Spreads Collector...")
            tick_collector = MarketTicksCollector(
                db_path=db_path,
                schema_path=schema_path,
                max_retries=args.retries,
                pair=profile,
            )
            results["ticks"] = tick_collector.collect()

        # Run Economic Calendar
        if args.collector in ("all", "calendar"):
            print("\n>>> [2/3] Running Economic Calendar Collector...")
            cal_collector = CalendarCollector(
                db_path=db_path,
                schema_path=schema_path,
                max_retries=args.retries,
                pair=profile,
            )
            results["calendar"] = cal_collector.collect()

        # Run News RSS
        if args.collector in ("all", "news"):
            print("\n>>> [3/3] Running News & Intervention RSS Collector...")
            news_collector = NewsRSSCollector(
                db_path=db_path,
                schema_path=schema_path,
                max_retries=args.retries,
                pair=profile,
            )
            results["news"] = news_collector.collect()

    except Exception as exc:
        print(f"\n[FATAL ERROR] Collection failed: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        conn.close()
        return 1

    final_counts = get_table_counts(conn)
    conn.close()

    elapsed = round(time.time() - start_time, 2)
    print("\n" + "=" * 70)
    print(f"Collection Execution Summary (Completed in {elapsed}s)")
    print("=" * 70)
    print(f"{'Table Name':<20} | {'Initial':<10} | {'Final':<10} | {'Net Change':<10}")
    print("-" * 70)
    for tbl in ["market_ticks", "yield_spreads", "macro_events", "news_headlines"]:
        init_cnt = initial_counts.get(tbl, 0)
        fin_cnt = final_counts.get(tbl, 0)
        diff = fin_cnt - init_cnt
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        print(f"{tbl:<20} | {init_cnt:<10} | {fin_cnt:<10} | {diff_str:<10}")
    print("=" * 70)

    # Key highlights
    if "ticks" in results:
        t_res = results["ticks"]
        spot_val = t_res.get("latest_prices", {}).get(profile.spot_symbol)
        print(
            f"• Market Ticks: {profile.pair} = {spot_val}, "
            f"Base 10Y = {t_res.get('base_10y') or t_res.get('us_10y')}%, "
            f"Quote 10Y = {t_res.get('quote_10y') or t_res.get('foreign_10y') or t_res.get('jgb_10y')}%, "
            f"10Y Spread = {t_res.get('spread_10y')}%"
        )
    if "calendar" in results:
        c_res = results["calendar"]
        inserted_events = c_res.get("usd_jpy_events_inserted") or c_res.get(f"{profile.symbol_slug[:3]}_{profile.symbol_slug[3:]}_events_inserted", 0)
        print(
            f"• Calendar    : {inserted_events} {profile.pair} events via {c_res.get('source_used')}"
        )
    if "news" in results:
        n_res = results["news"]
        print(
            f"• News RSS    : {n_res.get('total_items_processed')} items parsed across {n_res.get('feeds_polled')} feeds, "
            f"{n_res.get('flagged_intervention_rows')} policy/intervention signals flagged"
        )
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
