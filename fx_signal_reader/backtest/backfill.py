"""Historical data backfill module for Phase 6 backtesting (Task P6-02).

Populates an isolated SQLite database (defaulting to ``data/backtest_signals.db``)
with historical Yahoo Finance OHLCV market ticks, Japan Ministry of Finance (MoF)
JGB 10Y yields (``JGB10Y`` and ``^JB10YR``), and daily aligned US-Japan yield spreads
(via FRED ``DGS10``/``DGS2`` or ``^TNX`` fallback).
"""

from __future__ import annotations

import csv
import logging
import math
import numbers
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
import yfinance as yf

from fx_signal_reader.cli.collect import get_table_counts
from fx_signal_reader.collectors.base import BaseCollector
from fx_signal_reader.collectors.market_ticks import MarketTicksCollector
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH, PROJECT_ROOT
from fx_signal_reader.core.normalizer import (
    JAPANESE_ERA_PATTERN,
    normalize_datetime_utc,
    parse_float_or_none,
    parse_japanese_era_date,
)
from fx_signal_reader.core.spread_calculator import (
    align_yield_series,
    extract_fred_series,
    save_yield_spread,
)

logger = logging.getLogger("BacktestBackfill")

DEFAULT_BACKTEST_DB_PATH: Path = PROJECT_ROOT / "data" / "backtest_signals.db"
MOF_JGB_CURRENT_URL: str = MarketTicksCollector.MOF_JGB_CSV_URL
MOF_JGB_ALL_URL: str = (
    "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
)
FRED_OBSERVATIONS_URL: str = (
    "https://api.stlouisfed.org/fred/series/observations"
)
YF_INTERVAL_MAX_DAYS: dict[str, int] = {"15m": 59, "1h": 729}
SUPPORTED_YF_INTERVALS: frozenset[str] = frozenset({"15m", "1h", "1d"})

_GREGORIAN_DATE_RE = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$")
_DATE_ONLY_STR_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


class _BackfillHTTPClient(BaseCollector):
    """Internal BaseCollector subclass to reuse retry/backoff HTTP and WAL DB setup."""

    def collect(self) -> dict[str, Any]:
        return {}


def _validate_interval(interval: str) -> str:
    if not isinstance(interval, str) or interval not in SUPPORTED_YF_INTERVALS:
        raise ValueError(
            f"Unsupported interval '{interval}'. "
            f"Supported intervals: {sorted(SUPPORTED_YF_INTERVALS)}"
        )
    return interval


def _is_date_only_input(value: str | date | datetime) -> bool:
    if isinstance(value, datetime):
        return False
    if isinstance(value, date):
        return True
    stripped = value.strip()
    return bool(
        _DATE_ONLY_STR_RE.match(stripped) or JAPANESE_ERA_PATTERN.match(stripped)
    )


def _coerce_finite_float(val: Any) -> float | None:
    """Convert scalar numeric/string values (including NumPy/pandas scalars) to finite float."""
    if val is None or isinstance(val, (bool, np.bool_)):
        return None
    if not pd.api.types.is_scalar(val):
        return None
    if pd.isna(val):
        return None
    if isinstance(val, numbers.Real):
        result = float(val)
        return result if math.isfinite(result) else None
    parsed = parse_float_or_none(val)
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _parse_bound_datetime(
    value: str | date | datetime,
    *,
    is_end: bool,
) -> datetime:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid date/timestamp")
    if isinstance(value, str) and not value.strip():
        raise ValueError("Date/timestamp string cannot be empty.")

    utc_str = normalize_datetime_utc(
        value,
        default_timezone=timezone.utc,
        fallback_now=False,
    )
    dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    if is_end and _is_date_only_input(value):
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _resolve_date_range(
    start_date: str | date | datetime | None,
    end_date: str | date | datetime | None,
    *,
    interval: str = "1h",
) -> tuple[datetime, datetime, str, str, str, str]:
    """Normalize start/end bounds into UTC datetimes, ISO UTC strings, and YYYY-MM-DD."""
    _validate_interval(interval)

    if end_date is None:
        today = datetime.now(timezone.utc).date()
        end_dt = datetime(
            today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc
        )
    else:
        end_dt = _parse_bound_datetime(end_date, is_end=True)

    if start_date is None:
        default_days = 59 if interval == "15m" else 60
        start_day = end_dt.date() - timedelta(days=default_days)
        start_dt = datetime(
            start_day.year, start_day.month, start_day.day, 0, 0, 0, tzinfo=timezone.utc
        )
    else:
        start_dt = _parse_bound_datetime(start_date, is_end=False)

    if start_dt > end_dt:
        raise ValueError(
            f"start_date ({start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}) "
            f"must be <= end_date ({end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')})."
        )

    start_utc = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    start_date_str = start_dt.strftime("%Y-%m-%d")
    end_date_str = end_dt.strftime("%Y-%m-%d")
    return start_dt, end_dt, start_utc, end_utc, start_date_str, end_date_str


def _open_db_connection(db_path: str | Path) -> sqlite3.Connection:
    client = _BackfillHTTPClient(
        db_path=Path(db_path),
        schema_path=DEFAULT_SCHEMA_PATH,
    )
    return client.get_connection()


def _parse_mof_date(date_raw: str) -> str:
    """Parse Japanese Era (R/H/S) or Gregorian (YYYY/M/D, YYYY-M-D) MoF dates."""
    cleaned = date_raw.strip()
    if not cleaned:
        raise ValueError("Empty MoF date string.")
    if JAPANESE_ERA_PATTERN.match(cleaned):
        return parse_japanese_era_date(cleaned)
    match = _GREGORIAN_DATE_RE.match(cleaned)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        return date(year, month, day).isoformat()
    raise ValueError(f"Unsupported MoF date format: '{date_raw}'")


def _parse_mof_csv_raw(
    csv_content: str,
    target_tenor: str = "10年",
) -> dict[str, float | None]:
    """Parse MoF JGB CSV into ISO date -> yield (or ``None`` for explicit ``'-'``/missing)."""
    result: dict[str, float | None] = {}
    reader = csv.reader(StringIO(csv_content))
    header: list[str] | None = None
    target_idx: int | None = None

    for row in reader:
        if not row:
            continue
        first_col = row[0].strip()
        if "基準日" in first_col:
            header = [col.strip() for col in row]
            if target_tenor in header:
                target_idx = header.index(target_tenor)
            else:
                target_idx = None
            continue

        if header is not None and target_idx is not None and target_idx < len(row):
            try:
                iso_date = _parse_mof_date(first_col)
            except ValueError:
                continue
            result[iso_date] = _coerce_finite_float(row[target_idx])

    return result


def _parse_mof_csv_multi_date(
    csv_content: str,
    target_tenor: str = "10年",
) -> dict[str, float]:
    """Parse MoF JGB CSV supporting R/H/S and Gregorian YYYY/M/D date formats."""
    return {
        iso_date: rate
        for iso_date, rate in _parse_mof_csv_raw(
            csv_content, target_tenor=target_tenor
        ).items()
        if rate is not None
    }


def _flatten_yf_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        level0 = set(map(str, out.columns.get_level_values(0)))
        if "Close" in level0:
            out.columns = out.columns.get_level_values(0)
        else:
            out.columns = out.columns.get_level_values(-1)
    return out


def _extract_yf_rows_to_daily_close(
    hist: pd.DataFrame,
    start_date_str: str,
    end_date_str: str,
) -> dict[str, float]:
    """Extract sorted YYYY-MM-DD -> Close price mapping from a yfinance DataFrame."""
    if hist is None or hist.empty:
        return {}
    df = _flatten_yf_columns(hist)

    daily: dict[str, float] = {}
    for idx, row in df.iterrows():
        if idx is None or (pd.api.types.is_scalar(idx) and pd.isna(idx)):
            continue
        raw_idx = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        if raw_idx is None or (pd.api.types.is_scalar(raw_idx) and pd.isna(raw_idx)):
            continue
        ts_utc = normalize_datetime_utc(
            raw_idx, default_timezone=timezone.utc, fallback_now=False
        )
        day_str = ts_utc[:10]
        if day_str < start_date_str or day_str > end_date_str:
            continue
        close_val = _coerce_finite_float(
            row["Close"] if "Close" in df.columns else None
        )
        if close_val is not None:
            daily[day_str] = close_val
    return daily


def backfill_market_ticks(
    db_path: str | Path = DEFAULT_BACKTEST_DB_PATH,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    interval: str = "1h",
    symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Download historical OHLCV bars from Yahoo Finance into ``market_ticks``."""
    _validate_interval(interval)
    start_dt, end_dt, start_utc, end_utc, start_date_str, end_date_str = (
        _resolve_date_range(start_date, end_date, interval=interval)
    )

    target_symbols = (
        list(MarketTicksCollector.TICKERS)
        if symbols is None
        else list(
            dict.fromkeys(sym.strip() for sym in symbols if sym and sym.strip())
        )
    )

    now_utc = datetime.now(timezone.utc)
    max_days = YF_INTERVAL_MAX_DAYS.get(interval)
    effective_start_dt = start_dt
    clamped = False
    skip_download = False
    warnings: list[str] = []

    if max_days is not None:
        now_limit_day = (now_utc - timedelta(days=max_days)).date()
        now_limit_start = datetime(
            now_limit_day.year,
            now_limit_day.month,
            now_limit_day.day,
            0,
            0,
            0,
            tzinfo=timezone.utc,
        )
        if end_dt < now_limit_start:
            clamped = True
            skip_download = True
            msg = (
                f"Requested range [{start_date_str}, {end_date_str}] for interval "
                f"'{interval}' predates Yahoo Finance's {max_days}-day retention "
                f"cutoff ({now_limit_start.strftime('%Y-%m-%d')}); returning 0 rows."
            )
            logger.warning(msg)
            warnings.append(msg)
        else:
            span_limit_day = end_dt.date() - timedelta(days=max_days)
            span_limit_start = datetime(
                span_limit_day.year,
                span_limit_day.month,
                span_limit_day.day,
                0,
                0,
                0,
                tzinfo=timezone.utc,
            )
            min_allowed_start = max(span_limit_start, now_limit_start)
            if effective_start_dt < min_allowed_start:
                effective_start_dt = min_allowed_start
                clamped = True
                msg = (
                    f"Clamped start_date for interval '{interval}' from "
                    f"{start_date_str} to {effective_start_dt.strftime('%Y-%m-%d')} "
                    f"(Yahoo Finance {max_days}-day retention limit)."
                )
                logger.warning(msg)
                warnings.append(msg)

    effective_start_utc = effective_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    effective_start_date_str = effective_start_dt.strftime("%Y-%m-%d")
    yf_end_date_str = (end_dt.date() + timedelta(days=1)).isoformat()

    conn = _open_db_connection(db_path)
    per_symbol: dict[str, dict[str, Any]] = {}
    total_rows_inserted = 0

    try:
        if skip_download:
            for symbol in target_symbols:
                per_symbol[symbol] = {
                    "status": "SKIPPED_RETENTION_LIMIT",
                    "bars_fetched": 0,
                    "rows_inserted": 0,
                    "min_timestamp_utc": None,
                    "max_timestamp_utc": None,
                }
        else:
            for symbol in target_symbols:
                try:
                    hist = yf.Ticker(symbol).history(
                        start=effective_start_date_str,
                        end=yf_end_date_str,
                        interval=interval,
                    )
                    if hist is None or hist.empty:
                        msg = f"No Yahoo Finance history returned for {symbol} ({interval})."
                        logger.warning(msg)
                        warnings.append(msg)
                        per_symbol[symbol] = {
                            "status": "EMPTY",
                            "bars_fetched": 0,
                            "rows_inserted": 0,
                            "min_timestamp_utc": None,
                            "max_timestamp_utc": None,
                        }
                        continue

                    df = _flatten_yf_columns(hist)

                    cols = set(df.columns)
                    rows_to_insert: list[
                        tuple[str, str, float, float, float, float, float]
                    ] = []

                    for idx, row in df.iterrows():
                        if idx is None or (
                            pd.api.types.is_scalar(idx) and pd.isna(idx)
                        ):
                            continue
                        raw_idx = (
                            idx.to_pydatetime()
                            if hasattr(idx, "to_pydatetime")
                            else idx
                        )
                        if raw_idx is None or (
                            pd.api.types.is_scalar(raw_idx) and pd.isna(raw_idx)
                        ):
                            continue
                        ts_utc = normalize_datetime_utc(
                            raw_idx,
                            default_timezone=timezone.utc,
                            fallback_now=False,
                        )
                        if ts_utc < effective_start_utc or ts_utc > end_utc:
                            continue

                        close_val = _coerce_finite_float(
                            row["Close"] if "Close" in cols else None
                        )
                        if close_val is None:
                            continue

                        open_val = _coerce_finite_float(
                            row["Open"] if "Open" in cols else None
                        )
                        if open_val is None:
                            open_val = close_val

                        high_val = _coerce_finite_float(
                            row["High"] if "High" in cols else None
                        )
                        if high_val is None:
                            high_val = close_val

                        low_val = _coerce_finite_float(
                            row["Low"] if "Low" in cols else None
                        )
                        if low_val is None:
                            low_val = close_val

                        vol_val = _coerce_finite_float(
                            row["Volume"] if "Volume" in cols else None
                        )
                        if vol_val is None:
                            vol_val = 0.0

                        rows_to_insert.append(
                            (
                                symbol,
                                ts_utc,
                                close_val,
                                open_val,
                                high_val,
                                low_val,
                                vol_val,
                            )
                        )

                    if rows_to_insert:
                        conn.executemany(
                            """
                            INSERT OR REPLACE INTO market_ticks
                            (symbol, timestamp_utc, price, open, high, low, volume)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            rows_to_insert,
                        )
                        conn.commit()
                        total_rows_inserted += len(rows_to_insert)

                    timestamps = [r[1] for r in rows_to_insert]
                    per_symbol[symbol] = {
                        "status": "SUCCESS" if rows_to_insert else "EMPTY",
                        "bars_fetched": int(len(df)),
                        "rows_inserted": len(rows_to_insert),
                        "min_timestamp_utc": min(timestamps) if timestamps else None,
                        "max_timestamp_utc": max(timestamps) if timestamps else None,
                    }
                except Exception as exc:
                    msg = f"Failed to backfill {symbol}: {exc}"
                    logger.warning(msg)
                    warnings.append(msg)
                    per_symbol[symbol] = {
                        "status": "ERROR",
                        "error": str(exc),
                        "bars_fetched": 0,
                        "rows_inserted": 0,
                        "min_timestamp_utc": None,
                        "max_timestamp_utc": None,
                    }
    finally:
        conn.close()

    if total_rows_inserted > 0:
        has_error = any(
            meta.get("status") == "ERROR" for meta in per_symbol.values()
        )
        status = "PARTIAL" if has_error else "SUCCESS"
    elif any(meta.get("status") == "ERROR" for meta in per_symbol.values()):
        status = "ERROR"
    else:
        status = "EMPTY"

    return {
        "status": status,
        "db_path": str(Path(db_path)),
        "interval": interval,
        "requested_start_date": start_date_str,
        "requested_end_date": end_date_str,
        "effective_start_date": effective_start_date_str,
        "end_date": end_date_str,
        "clamped": clamped,
        "total_rows_inserted": total_rows_inserted,
        "symbols": per_symbol,
        "warnings": warnings,
    }


def backfill_jgb_yields(
    db_path: str | Path = DEFAULT_BACKTEST_DB_PATH,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
) -> dict[str, Any]:
    """Fetch Japan MoF JGB 10Y CSVs and store ``JGB10Y`` & ``^JB10YR`` in ``market_ticks``."""
    _, _, _, _, start_date_str, end_date_str = _resolve_date_range(
        start_date, end_date, interval="1h"
    )

    http_client = _BackfillHTTPClient(
        db_path=Path(db_path),
        schema_path=DEFAULT_SCHEMA_PATH,
    )

    resp_current = http_client.fetch_url(MOF_JGB_CURRENT_URL, encoding="cp932")
    resp_current.raise_for_status()
    current_raw = _parse_mof_csv_raw(resp_current.text)

    earliest_current = min(current_raw.keys()) if current_raw else None
    used_historical_csv = (
        earliest_current is None or start_date_str < earliest_current
    )

    if used_historical_csv:
        resp_all = http_client.fetch_url(MOF_JGB_ALL_URL, encoding="cp932")
        resp_all.raise_for_status()
        all_raw = _parse_mof_csv_raw(resp_all.text)
        merged_raw = {**all_raw, **current_raw}
    else:
        merged_raw = current_raw

    merged_series = {
        iso_date: rate
        for iso_date, rate in merged_raw.items()
        if rate is not None
    }

    filtered_dates = sorted(
        d for d in merged_series if start_date_str <= d <= end_date_str
    )

    rows_to_insert: list[tuple[str, str, float, float, float, float, float]] = []
    for iso_date in filtered_dates:
        rate = merged_series[iso_date]
        ts_utc = f"{iso_date}T00:00:00Z"
        for sym in ("JGB10Y", "^JB10YR"):
            rows_to_insert.append((sym, ts_utc, rate, rate, rate, rate, 0.0))

    conn = http_client.get_connection()
    try:
        if rows_to_insert:
            conn.executemany(
                """
                INSERT OR REPLACE INTO market_ticks
                (symbol, timestamp_utc, price, open, high, low, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows_to_insert,
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "status": "SUCCESS" if filtered_dates else "EMPTY",
        "db_path": str(Path(db_path)),
        "start_date": start_date_str,
        "end_date": end_date_str,
        "dates_covered": len(filtered_dates),
        "rows_inserted": len(rows_to_insert),
        "used_historical_csv": used_historical_csv,
        "min_date": filtered_dates[0] if filtered_dates else None,
        "max_date": filtered_dates[-1] if filtered_dates else None,
    }


def backfill_yield_spreads(
    db_path: str | Path = DEFAULT_BACKTEST_DB_PATH,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    fred_api_key: str | None = None,
) -> dict[str, Any]:
    """Compute and store daily aligned US-Japan 10Y yield spreads into ``yield_spreads``."""
    _, _, _, _, start_date_str, end_date_str = _resolve_date_range(
        start_date, end_date, interval="1h"
    )
    start_day = date.fromisoformat(start_date_str)
    end_day = date.fromisoformat(end_date_str)
    lookback_start_str = (start_day - timedelta(days=7)).isoformat()

    api_key = (
        fred_api_key.strip()
        if fred_api_key is not None
        else os.getenv("FRED_API_KEY", "").strip()
    )

    http_client = _BackfillHTTPClient(
        db_path=Path(db_path),
        schema_path=DEFAULT_SCHEMA_PATH,
    )

    us_10y_series: dict[str, float] = {}
    us_2y_series: dict[str, float] = {}
    used_fred = False
    fallback_used = False
    source_label = "yfinance+mof_japan"
    warnings: list[str] = []

    if api_key:
        try:
            resp_10y = http_client.fetch_url(
                FRED_OBSERVATIONS_URL,
                params={
                    "series_id": "DGS10",
                    "api_key": api_key,
                    "file_type": "json",
                    "observation_start": lookback_start_str,
                    "observation_end": end_date_str,
                    "sort_order": "asc",
                    "limit": 100000,
                },
            )
            resp_10y.raise_for_status()
            obs_10y = resp_10y.json().get("observations", [])
            us_10y_series = extract_fred_series(obs_10y)

            if us_10y_series:
                used_fred = True
                source_label = "FRED+MoF"
                try:
                    resp_2y = http_client.fetch_url(
                        FRED_OBSERVATIONS_URL,
                        params={
                            "series_id": "DGS2",
                            "api_key": api_key,
                            "file_type": "json",
                            "observation_start": lookback_start_str,
                            "observation_end": end_date_str,
                            "sort_order": "asc",
                            "limit": 100000,
                        },
                    )
                    resp_2y.raise_for_status()
                    obs_2y = resp_2y.json().get("observations", [])
                    us_2y_series = extract_fred_series(obs_2y)
                except Exception as exc_2y:
                    msg = f"FRED DGS2 fetch failed ({exc_2y}); continuing without us_2y."
                    logger.warning(msg)
                    warnings.append(msg)
            else:
                msg = "FRED DGS10 returned empty series; falling back to ^TNX."
                logger.warning(msg)
                warnings.append(msg)
        except Exception as exc:
            msg = f"FRED DGS10 fetch failed ({exc}); falling back to ^TNX."
            logger.warning(msg)
            warnings.append(msg)

    conn = http_client.get_connection()
    rows_inserted = 0
    saved_dates: list[str] = []

    try:
        if not used_fred:
            fallback_used = True
            source_label = "yfinance+mof_japan"
            tnx_rows = conn.execute(
                """
                SELECT timestamp_utc, price
                FROM market_ticks
                WHERE symbol = '^TNX'
                  AND timestamp_utc >= ?
                  AND timestamp_utc <= ?
                ORDER BY timestamp_utc ASC
                """,
                (f"{lookback_start_str}T00:00:00Z", f"{end_date_str}T23:59:59Z"),
            ).fetchall()

            for row in tnx_rows:
                val = _coerce_finite_float(row["price"])
                if val is not None:
                    us_10y_series[row["timestamp_utc"][:10]] = val

            if not us_10y_series:
                try:
                    tnx_hist = yf.Ticker("^TNX").history(
                        start=lookback_start_str,
                        end=(end_day + timedelta(days=1)).isoformat(),
                        interval="1d",
                    )
                    us_10y_series = _extract_yf_rows_to_daily_close(
                        tnx_hist, lookback_start_str, end_date_str
                    )
                except Exception as exc:
                    msg = f"Fallback yfinance ^TNX download failed: {exc}"
                    logger.warning(msg)
                    warnings.append(msg)

        jgb_rows = conn.execute(
            """
            SELECT timestamp_utc, price
            FROM market_ticks
            WHERE symbol = 'JGB10Y'
              AND timestamp_utc >= ?
              AND timestamp_utc <= ?
            ORDER BY timestamp_utc ASC
            """,
            (f"{lookback_start_str}T00:00:00Z", f"{end_date_str}T23:59:59Z"),
        ).fetchall()

        jp_10y_series: dict[str, float] = {}
        for row in jgb_rows:
            val = _coerce_finite_float(row["price"])
            if val is not None:
                jp_10y_series[row["timestamp_utc"][:10]] = val

        if not us_10y_series or not jp_10y_series:
            msg = (
                "Insufficient yield inputs to align spreads "
                f"(us_10y={len(us_10y_series)}, jp_10y={len(jp_10y_series)})."
            )
            logger.warning(msg)
            warnings.append(msg)
        else:
            aligned = align_yield_series(
                us_10y_series=us_10y_series,
                jp_10y_series=jp_10y_series,
                us_2y_series=us_2y_series if us_2y_series else None,
                fill_method="ffill",
                max_stale_days=5,
                source_label=source_label,
            )
            records_to_save = [
                rec
                for rec in aligned
                if start_date_str <= rec.timestamp_utc[:10] <= end_date_str
                and rec.spread_10y is not None
            ]
            for rec in records_to_save:
                save_yield_spread(conn, rec)
                saved_dates.append(rec.timestamp_utc[:10])
            conn.commit()
            rows_inserted = len(records_to_save)
    finally:
        conn.close()

    return {
        "status": "SUCCESS" if rows_inserted > 0 else "EMPTY",
        "db_path": str(Path(db_path)),
        "start_date": start_date_str,
        "end_date": end_date_str,
        "source": source_label,
        "used_fred": used_fred,
        "fallback_used": fallback_used,
        "rows_inserted": rows_inserted,
        "min_date": saved_dates[0] if saved_dates else None,
        "max_date": saved_dates[-1] if saved_dates else None,
        "warnings": warnings,
    }


def backfill_historical_data(
    db_path: str | Path = DEFAULT_BACKTEST_DB_PATH,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    interval: str = "1h",
    symbols: Sequence[str] | None = None,
    fred_api_key: str | None = None,
) -> dict[str, Any]:
    """Orchestrate full historical backfill into an isolated SQLite database."""
    _validate_interval(interval)
    _, _, _, _, start_date_str, end_date_str = _resolve_date_range(
        start_date, end_date, interval=interval
    )

    resolved_start = start_date if start_date is not None else start_date_str
    resolved_end = end_date if end_date is not None else end_date_str

    init_conn = _open_db_connection(db_path)
    init_conn.close()

    ticks_summary = backfill_market_ticks(
        db_path=db_path,
        start_date=resolved_start,
        end_date=resolved_end,
        interval=interval,
        symbols=symbols,
    )
    jgb_summary = backfill_jgb_yields(
        db_path=db_path,
        start_date=resolved_start,
        end_date=resolved_end,
    )
    spreads_summary = backfill_yield_spreads(
        db_path=db_path,
        start_date=resolved_start,
        end_date=resolved_end,
        fred_api_key=fred_api_key,
    )

    counts_conn = _open_db_connection(db_path)
    try:
        db_counts = get_table_counts(counts_conn)
    finally:
        counts_conn.close()

    return {
        "db_path": str(Path(db_path)),
        "start_date": start_date_str,
        "end_date": end_date_str,
        "interval": interval,
        "market_ticks": ticks_summary,
        "jgb_yields": jgb_summary,
        "yield_spreads": spreads_summary,
        "db_counts": db_counts,
    }
