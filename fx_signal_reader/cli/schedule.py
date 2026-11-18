#!/usr/bin/env python3
"""
Production Tiered Scheduler for USD/JPY Signal Reader.

Manages tiered polling cadences, market session awareness (Tokyo Fix,
London/NY overlap), graceful shutdown, and CLI execution.

Cadences:
  - News & Verbal Intervention RSS: Every 5 minutes (300s default)
  - Market Ticks & Yield Spread Calculation: Every 15 minutes (900s default)
  - Macro Economic Calendar: Daily at 00:05 UTC (or configurable cycle)

Session Awareness:
  - Tokyo Fix Window: 09:45–10:05 JST (00:45–01:05 UTC) with expedited tick polling
  - Exact Tokyo Fix Snapshot: 09:55 JST (00:55 UTC)
  - London / NY Overlap: 13:00–17:00 UTC (high-liquidity regime)

Usage:
  python scheduler.py --run-once
  python scheduler.py --dry-run
  python scheduler.py --interval-news 300 --interval-ticks 900
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from fx_signal_reader.collectors.calendar import CalendarCollector
from fx_signal_reader.collectors.market_ticks import MarketTicksCollector
from fx_signal_reader.collectors.news_rss import NewsRSSCollector
from fx_signal_reader.core import DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("Scheduler")

# Timezones
UTC = timezone.utc
JST = timezone(timedelta(hours=9))


def parse_interval(val: Any, default: int) -> int:
    """Parses an integer interval from string/int, falling back to default if invalid or <= 0."""
    if val is None:
        return default
    try:
        parsed = int(val)
        return parsed if parsed > 0 else default
    except (ValueError, TypeError):
        return default


def normalize_to_utc(dt: Optional[datetime] = None) -> datetime:
    """Normalizes any datetime (naive or aware) to aware UTC datetime."""
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def is_tokyo_fix_window(dt: Optional[datetime] = None) -> bool:
    """
    Checks if given datetime falls within the Tokyo Fix window.
    Tokyo Fix window: 09:45–10:05 JST (00:45–01:05 UTC).
    """
    utc_dt = normalize_to_utc(dt)
    jst_dt = utc_dt.astimezone(JST)
    t = jst_dt.time()
    return dtime(9, 45, 0) <= t <= dtime(10, 5, 0)


def is_exact_tokyo_fix(dt: Optional[datetime] = None, tolerance_sec: int = 60) -> bool:
    """
    Checks if given datetime is within tolerance of exact Tokyo Fix snapshot (09:55 JST / 00:55 UTC).
    """
    utc_dt = normalize_to_utc(dt)
    jst_dt = utc_dt.astimezone(JST)
    fix_time = jst_dt.replace(hour=9, minute=55, second=0, microsecond=0)
    diff = abs((jst_dt - fix_time).total_seconds())
    return diff <= tolerance_sec


def is_london_ny_overlap(dt: Optional[datetime] = None) -> bool:
    """
    Checks if given datetime falls within the London / NY overlap session.
    London / NY Overlap: 13:00–17:00 UTC.
    """
    utc_dt = normalize_to_utc(dt)
    t = utc_dt.time()
    return dtime(13, 0, 0) <= t <= dtime(17, 0, 0)


def get_market_session_regime(dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Evaluates global market session dynamics, anchor windows, and liquidity regime.
    """
    utc_dt = normalize_to_utc(dt)
    jst_dt = utc_dt.astimezone(JST)

    tokyo_fix = is_tokyo_fix_window(utc_dt)
    exact_fix = is_exact_tokyo_fix(utc_dt)
    london_ny = is_london_ny_overlap(utc_dt)

    weekday = utc_dt.weekday()  # 0=Monday, 6=Sunday
    is_weekend = (weekday == 5 and utc_dt.hour >= 21) or (weekday == 6 and utc_dt.hour < 21)

    t_utc = utc_dt.time()
    active_sessions: List[str] = []

    # Standard session hours (UTC)
    if dtime(0, 0) <= t_utc < dtime(9, 0):
        active_sessions.append("Tokyo/Asian")
    if dtime(7, 0) <= t_utc < dtime(16, 0):
        active_sessions.append("London/European")
    if dtime(12, 0) <= t_utc < dtime(21, 0):
        active_sessions.append("New York")
    if dtime(21, 0) <= t_utc or t_utc < dtime(6, 0):
        active_sessions.append("Sydney/Wellington")

    if is_weekend:
        regime_name = "Weekend / Interbank Closed"
        liquidity = "LOW"
    elif tokyo_fix:
        regime_name = "Tokyo Fix Window (Fix Benchmark Volatility)"
        liquidity = "HIGH"
    elif london_ny:
        regime_name = "London/New York Overlap (Peak Daily Liquidity)"
        liquidity = "VERY_HIGH"
    elif "London/European" in active_sessions and "New York" not in active_sessions:
        regime_name = "European Session"
        liquidity = "HIGH"
    elif "Tokyo/Asian" in active_sessions:
        regime_name = "Asian / Tokyo Session"
        liquidity = "NORMAL"
    elif "New York" in active_sessions:
        regime_name = "New York Afternoon Session"
        liquidity = "NORMAL"
    else:
        regime_name = "Off-Peak Asian / Pacific Session"
        liquidity = "LOW"

    return {
        "timestamp_utc": utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timestamp_jst": jst_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "is_tokyo_fix_window": tokyo_fix,
        "is_exact_tokyo_fix": exact_fix,
        "is_london_ny_overlap": london_ny,
        "is_weekend": is_weekend,
        "active_sessions": active_sessions,
        "regime_name": regime_name,
        "liquidity_level": liquidity,
        "expedited_ticks": tokyo_fix and not is_weekend,
    }


def calculate_next_calendar_run(
    now_utc: datetime,
    target_hour: int = 0,
    target_minute: int = 5,
    interval_sec: int = 86400,
) -> datetime:
    """
    Calculates next execution time for macro economic calendar collector.
    If interval_sec is daily (86400), aligns to target_hour:target_minute UTC.
    Otherwise, applies standard interval from now_utc.
    """
    now = normalize_to_utc(now_utc)
    if interval_sec == 86400:
        candidate = now.replace(
            hour=target_hour,
            minute=target_minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    else:
        return now + timedelta(seconds=interval_sec)


@dataclass
class JobSchedule:
    """Tracks state and scheduling metadata for an individual collector job."""

    name: str
    interval_sec: int
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int = 0
    last_status: str = "PENDING"
    last_duration_sec: float = 0.0
    last_result: Optional[Dict[str, Any]] = None

    def is_due(self, now: datetime) -> bool:
        if self.next_run is None:
            return True
        return now >= self.next_run


@dataclass
class SchedulerConfig:
    """Scheduler configuration parameters."""

    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)
    schema_path: Path = field(default_factory=lambda: DEFAULT_SCHEMA_PATH)
    news_interval: int = 300  # 5 minutes
    ticks_interval: int = 900  # 15 minutes
    calendar_interval: int = 86400  # 24 hours / daily
    tokyo_fix_interval: int = 60  # 1 minute during Tokyo Fix
    calendar_utc_hour: int = 0
    calendar_utc_minute: int = 5
    enable_tokyo_fix_expedited: bool = True
    dry_run: bool = False
    run_once: bool = False
    retries: int = 3
    verbose: bool = False

    @classmethod
    def from_env_and_args(cls, args: Optional[argparse.Namespace] = None) -> SchedulerConfig:
        """Constructs configuration merging defaults, environment variables, and CLI flags."""
        # Read from environment
        env_db_path = os.getenv("FX_DB_PATH", str(DEFAULT_DB_PATH))
        env_news = parse_interval(os.getenv("INTERVAL_NEWS"), 300)
        env_ticks = parse_interval(os.getenv("INTERVAL_TICKS"), 900)
        env_calendar = parse_interval(os.getenv("INTERVAL_CALENDAR"), 86400)
        env_tokyo_fix = parse_interval(os.getenv("INTERVAL_TOKYO_FIX"), 60)
        env_cal_time = os.getenv("CALENDAR_TIME_UTC", "00:05")

        hour, minute = 0, 5
        if ":" in env_cal_time:
            try:
                parts = env_cal_time.split(":")
                hour, minute = int(parts[0]), int(parts[1])
            except ValueError:
                hour, minute = 0, 5

        cfg = cls(
            db_path=Path(env_db_path),
            news_interval=env_news,
            ticks_interval=env_ticks,
            calendar_interval=env_calendar,
            tokyo_fix_interval=env_tokyo_fix,
            calendar_utc_hour=hour,
            calendar_utc_minute=minute,
        )

        # Override with CLI arguments if provided
        if args:
            if getattr(args, "db_path", None):
                cfg.db_path = Path(args.db_path)
            if getattr(args, "schema_path", None):
                cfg.schema_path = Path(args.schema_path)
            if getattr(args, "interval_news", None) is not None:
                cfg.news_interval = parse_interval(args.interval_news, cfg.news_interval)
            if getattr(args, "interval_ticks", None) is not None:
                cfg.ticks_interval = parse_interval(args.interval_ticks, cfg.ticks_interval)
            if getattr(args, "interval_calendar", None) is not None:
                cfg.calendar_interval = parse_interval(args.interval_calendar, cfg.calendar_interval)
            if getattr(args, "interval_tokyo_fix", None) is not None:
                cfg.tokyo_fix_interval = parse_interval(args.interval_tokyo_fix, cfg.tokyo_fix_interval)
            if getattr(args, "calendar_time_utc", None):
                try:
                    hp, mp = args.calendar_time_utc.split(":")
                    cfg.calendar_utc_hour = int(hp)
                    cfg.calendar_utc_minute = int(mp)
                except ValueError:
                    pass
            if getattr(args, "no_expedited_fix", False):
                cfg.enable_tokyo_fix_expedited = False
            if getattr(args, "dry_run", False):
                cfg.dry_run = True
            if getattr(args, "run_once", False):
                cfg.run_once = True
            if getattr(args, "retries", None) is not None:
                cfg.retries = args.retries
            if getattr(args, "verbose", False):
                cfg.verbose = True

        return cfg


class SignalScheduler:
    """
    Tiered Scheduler Engine for USD/JPY Signal Reader.
    Coordinates polling intervals, session awareness, graceful shutdown,
    and collector job dispatching.
    """

    def __init__(self, config: Optional[SchedulerConfig] = None) -> None:
        self.config = config or SchedulerConfig()
        self.jobs: Dict[str, JobSchedule] = {}
        self._shutdown_requested = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        if self.config.verbose:
            logger.setLevel(logging.DEBUG)

        self._init_jobs()
        self._setup_signals()

    def _init_jobs(self) -> None:
        """Initializes job schedule registry with default next-run targets."""
        now = datetime.now(UTC)
        self.jobs["news"] = JobSchedule(
            name="news",
            interval_sec=self.config.news_interval,
            next_run=now,
        )
        self.jobs["ticks"] = JobSchedule(
            name="ticks",
            interval_sec=self.config.ticks_interval,
            next_run=now,
        )
        self.jobs["calendar"] = JobSchedule(
            name="calendar",
            interval_sec=self.config.calendar_interval,
            next_run=now,
        )

    def _setup_signals(self) -> None:
        """Configures POSIX signal handlers for graceful shutdown."""
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError):
            # Signal handling requires main thread; ignored in sub-threads/tests
            pass

    def _handle_signal(self, signum: int, frame: Any) -> None:
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info(f"Received termination signal {sig_name} ({signum}). Initiating graceful shutdown...")
        self.stop()

    def stop(self) -> None:
        """Sets shutdown flag and signals sleep interrupt."""
        self._shutdown_requested = True
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return not self._shutdown_requested

    def get_table_counts(self) -> Dict[str, int]:
        """Queries row counts across all 4 database pipeline tables."""
        tables = ["market_ticks", "yield_spreads", "macro_events", "news_headlines"]
        counts: Dict[str, int] = {t: 0 for t in tables}
        if not self.config.db_path.exists():
            return counts

        try:
            conn = sqlite3.connect(self.config.db_path)
            cur = conn.cursor()
            for tbl in tables:
                try:
                    cur.execute(f"SELECT count(*) FROM {tbl}")
                    counts[tbl] = cur.fetchone()[0]
                except sqlite3.OperationalError:
                    counts[tbl] = 0
            conn.close()
        except Exception as exc:
            logger.warning(f"Unable to read table counts: {exc}")
        return counts

    def get_session_regime(self, dt: Optional[datetime] = None) -> Dict[str, Any]:
        """Returns session regime analysis for specified or current time."""
        return get_market_session_regime(dt)

    def get_effective_ticks_interval(self, now: Optional[datetime] = None) -> int:
        """
        Determines current tick polling interval taking Tokyo Fix session window into account.
        """
        current_time = normalize_to_utc(now)
        if self.config.enable_tokyo_fix_expedited and is_tokyo_fix_window(current_time):
            return self.config.tokyo_fix_interval
        return self.config.ticks_interval

    def update_next_run(self, job_name: str, completed_at: datetime) -> datetime:
        """Calculates and updates next scheduled run time for a job."""
        job = self.jobs[job_name]
        if job_name == "calendar":
            next_run = calculate_next_calendar_run(
                completed_at,
                target_hour=self.config.calendar_utc_hour,
                target_minute=self.config.calendar_utc_minute,
                interval_sec=self.config.calendar_interval,
            )
        elif job_name == "ticks":
            eff_interval = self.get_effective_ticks_interval(completed_at)
            next_run = completed_at + timedelta(seconds=eff_interval)
        else:
            next_run = completed_at + timedelta(seconds=job.interval_sec)

        job.next_run = next_run
        return next_run

    def run_collector(self, job_name: str) -> Dict[str, Any]:
        """
        Executes an individual collector job with timing, error containment, and telemetry tracking.
        """
        job = self.jobs.get(job_name)
        if not job:
            raise ValueError(f"Unknown collector job: {job_name}")

        start_time = time.time()
        start_utc = datetime.now(UTC)
        logger.info(f"Executing collector job '{job_name}' at {start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        if self.config.dry_run:
            time.sleep(0.05)
            duration = round(time.time() - start_time, 3)
            res = {
                "status": "dry_run",
                "collector": job_name,
                "message": f"Dry-run executed for {job_name}",
                "timestamp_utc": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            job.last_run = start_utc
            job.run_count += 1
            job.last_status = "SUCCESS (DRY-RUN)"
            job.last_duration_sec = duration
            job.last_result = res
            self.update_next_run(job_name, start_utc)
            return res

        result: Dict[str, Any] = {}
        try:
            if job_name == "news":
                collector = NewsRSSCollector(
                    db_path=self.config.db_path,
                    schema_path=self.config.schema_path,
                    max_retries=self.config.retries,
                )
                result = collector.collect()
            elif job_name == "ticks":
                collector = MarketTicksCollector(
                    db_path=self.config.db_path,
                    schema_path=self.config.schema_path,
                    max_retries=self.config.retries,
                )
                result = collector.collect()
            elif job_name == "calendar":
                collector = CalendarCollector(
                    db_path=self.config.db_path,
                    schema_path=self.config.schema_path,
                    max_retries=self.config.retries,
                )
                result = collector.collect()
            else:
                raise ValueError(f"Unsupported job type: {job_name}")

            job.last_status = "SUCCESS"
        except Exception as exc:
            logger.error(f"Collector job '{job_name}' failed: {exc}", exc_info=True)
            result = {"error": str(exc), "status": "FAILED"}
            job.last_status = "FAILED"

        duration = round(time.time() - start_time, 2)
        end_utc = datetime.now(UTC)
        job.last_run = end_utc
        job.run_count += 1
        job.last_duration_sec = duration
        job.last_result = result
        self.update_next_run(job_name, end_utc)

        logger.info(
            f"Finished job '{job_name}' in {duration}s [Status: {job.last_status}]. "
            f"Next run at {job.next_run.strftime('%Y-%m-%dT%H:%M:%SZ') if job.next_run else 'N/A'}"
        )
        return result

    def print_telemetry(self, current_dt: Optional[datetime] = None) -> None:
        """Prints formatted scheduler status telemetry and session regime table."""
        now = normalize_to_utc(current_dt)
        session = self.get_session_regime(now)
        eff_ticks = self.get_effective_ticks_interval(now)

        tokyo_indicator = "ACTIVE (09:45-10:05 JST)" if session["is_tokyo_fix_window"] else "Inactive"
        if session["is_exact_tokyo_fix"]:
            tokyo_indicator += " [EXACT FIX SNAPSHOT: 09:55 JST]"
        london_indicator = "ACTIVE (13:00-17:00 UTC)" if session["is_london_ny_overlap"] else "Inactive"

        print("\n" + "=" * 80)
        print("USD/JPY Signal Pipeline - Production Tiered Scheduler Telemetry")
        print("=" * 80)
        print(f"Current Time (UTC): {session['timestamp_utc']} | JST: {session['timestamp_jst']}")
        print(f"Active Regime     : {session['regime_name']}")
        print(f"Liquidity Level   : {session['liquidity_level']} | Active Sessions: {', '.join(session['active_sessions']) or 'None'}")
        print(f"Tokyo Fix Window  : {tokyo_indicator}")
        print(f"London/NY Overlap : {london_indicator}")
        print(f"Target Database   : {self.config.db_path.resolve()}")
        if session["is_tokyo_fix_window"] and self.config.enable_tokyo_fix_expedited:
            print(f"NOTE              : Tokyo Fix active! Market ticks expedited to {eff_ticks}s cadence.")
        print("-" * 80)
        print(f"{'Job Name':<12} | {'Interval':<14} | {'Last Run (UTC)':<20} | {'Next Run (UTC)':<20} | {'Status':<10}")
        print("-" * 80)

        for name, job in self.jobs.items():
            last_run_str = job.last_run.strftime("%Y-%m-%dT%H:%M:%SZ") if job.last_run else "Never"
            next_run_str = job.next_run.strftime("%Y-%m-%dT%H:%M:%SZ") if job.next_run else "Pending"
            if name == "ticks" and session["is_tokyo_fix_window"] and self.config.enable_tokyo_fix_expedited:
                interval_str = f"{eff_ticks}s (Fix Exp.)"
            elif name == "calendar" and self.config.calendar_interval == 86400:
                interval_str = f"Daily {self.config.calendar_utc_hour:02d}:{self.config.calendar_utc_minute:02d}"
            else:
                interval_str = f"{job.interval_sec}s ({job.interval_sec // 60}m)"

            print(f"{name:<12} | {interval_str:<14} | {last_run_str:<20} | {next_run_str:<20} | {job.last_status:<10}")
        print("=" * 80)

    def run_once(self) -> Dict[str, Any]:
        """
        Executes one complete cycle across all 3 collectors sequentially.
        Displays table delta metrics and returns execution results.
        """
        start_time = time.time()
        start_utc = datetime.now(UTC)
        logger.info(f"Executing --run-once mode at {start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        init_counts = self.get_table_counts()
        self.print_telemetry(start_utc)

        results: Dict[str, Any] = {}
        for job_name in ["news", "ticks", "calendar"]:
            print(f"\n>>> Running collector job: {job_name.upper()}...")
            res = self.run_collector(job_name)
            results[job_name] = res

        final_counts = self.get_table_counts()
        elapsed = round(time.time() - start_time, 2)

        print("\n" + "=" * 80)
        print(f"Cycle Execution Summary (Completed in {elapsed}s)")
        print("=" * 80)
        print(f"{'Table Name':<22} | {'Initial':<10} | {'Final':<10} | {'Net Change':<10}")
        print("-" * 80)
        for tbl in ["market_ticks", "yield_spreads", "macro_events", "news_headlines"]:
            ic = init_counts.get(tbl, 0)
            fc = final_counts.get(tbl, 0)
            diff = fc - ic
            diff_str = f"+{diff}" if diff > 0 else str(diff)
            print(f"{tbl:<22} | {ic:<10} | {fc:<10} | {diff_str:<10}")
        print("=" * 80)

        # Highlight key metrics
        if "ticks" in results and isinstance(results["ticks"], dict) and "latest_prices" in results["ticks"]:
            t_res = results["ticks"]
            print(
                f"• Market Ticks: USD/JPY = {t_res.get('latest_prices', {}).get('JPY=X')}, "
                f"US 10Y = {t_res.get('us_10y')}%, JGB 10Y = {t_res.get('jgb_10y')}%, "
                f"10Y Spread = {t_res.get('spread_10y')}%"
            )
        if "calendar" in results and isinstance(results["calendar"], dict) and "usd_jpy_events_inserted" in results["calendar"]:
            c_res = results["calendar"]
            print(
                f"• Calendar    : {c_res.get('usd_jpy_events_inserted')} events via {c_res.get('source_used')}"
            )
        if "news" in results and isinstance(results["news"], dict) and "flagged_intervention_rows" in results["news"]:
            n_res = results["news"]
            print(
                f"• News RSS    : {n_res.get('total_items_processed')} items parsed across {n_res.get('feeds_polled')} feeds, "
                f"{n_res.get('flagged_intervention_rows')} intervention signals flagged"
            )
        print("=" * 80)
        return results

    def start(self, max_cycles: Optional[int] = None) -> int:
        """
        Main scheduler loop with interruptible sleep and session monitoring.
        Runs indefinitely until SIGINT / SIGTERM or max_cycles reached.
        """
        logger.info("Starting USD/JPY Signal Reader Production Scheduler loop...")
        cycle_count = 0

        try:
            while not self._shutdown_requested:
                now = datetime.now(UTC)

                # Check which jobs are due
                due_jobs: List[str] = [
                    name for name, job in self.jobs.items() if job.is_due(now)
                ]

                if due_jobs:
                    self.print_telemetry(now)
                    for job_name in due_jobs:
                        if self._shutdown_requested:
                            logger.info("Shutdown requested. Breaking out of job cycle.")
                            break
                        with self._lock:
                            self.run_collector(job_name)

                    cycle_count += 1
                    if max_cycles and cycle_count >= max_cycles:
                        logger.info(f"Max test cycles ({max_cycles}) reached. Stopping loop.")
                        break

                # Sleep in short slices (1.0s) so shutdown signals wake immediately
                self._stop_event.wait(timeout=1.0)

        except Exception as exc:
            logger.error(f"Fatal error in scheduler loop: {exc}", exc_info=True)
            return 1
        finally:
            logger.info("Scheduler loop exited. Performing final cleanup...")

        logger.info("Scheduler terminated cleanly.")
        return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds command-line argument parser for scheduler CLI."""
    parser = argparse.ArgumentParser(
        description="USD/JPY Signal Reader Production Tiered Scheduler",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Execute one cycle of all collectors and exit immediately",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run scheduler without performing real network requests or DB writes",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("FX_DB_PATH", str(DEFAULT_DB_PATH)),
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "--schema-path",
        type=str,
        default=str(DEFAULT_SCHEMA_PATH),
        help="Path to SQL schema file",
    )
    parser.add_argument(
        "--interval-news",
        type=int,
        default=parse_interval(os.getenv("INTERVAL_NEWS"), 300),
        help="News & verbal intervention polling interval in seconds (default: 300 / 5m)",
    )
    parser.add_argument(
        "--interval-ticks",
        type=int,
        default=parse_interval(os.getenv("INTERVAL_TICKS"), 900),
        help="Market ticks & yield spread polling interval in seconds (default: 900 / 15m)",
    )
    parser.add_argument(
        "--interval-calendar",
        type=int,
        default=parse_interval(os.getenv("INTERVAL_CALENDAR"), 86400),
        help="Macro economic calendar polling interval in seconds (default: 86400 / daily)",
    )
    parser.add_argument(
        "--interval-tokyo-fix",
        type=int,
        default=parse_interval(os.getenv("INTERVAL_TOKYO_FIX"), 60),
        help="Expedited market ticks polling interval during Tokyo Fix window in seconds (default: 60)",
    )
    parser.add_argument(
        "--calendar-time-utc",
        type=str,
        default=os.getenv("CALENDAR_TIME_UTC", "00:05"),
        help="Target time of day in UTC for daily calendar polling (format: HH:MM, default: 00:05)",
    )
    parser.add_argument(
        "--no-expedited-fix",
        action="store_true",
        help="Disable expedited tick polling during Tokyo Fix window",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Maximum retries for collector network requests",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    config = SchedulerConfig.from_env_and_args(args)

    scheduler = SignalScheduler(config)

    if config.run_once:
        scheduler.run_once()
        return 0

    return scheduler.start()


if __name__ == "__main__":
    sys.exit(main())
