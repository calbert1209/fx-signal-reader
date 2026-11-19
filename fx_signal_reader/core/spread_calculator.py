"""
USD/JPY Signal Reader - Yield Spread & Curve Slope Calculation Module
Task P2-03: Normalization & Spread Calculation

Computes US10Y - JGB10Y yield spreads, US 2s10s curve slope, parses multi-source
yield data (FRED, Yahoo Finance, Japan MoF), and handles holiday/publication
misalignment between US and Japanese sovereign bond markets.
"""

import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import math
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union

from fx_signal_reader.core.normalizer import (
    clean_text,
    is_valid_iso8601_utc,
    normalize_datetime_utc,
    parse_float_or_none,
    parse_japanese_era_date,
)


@dataclass
class YieldSpreadRecord:
    """Represents a normalized yield spread record aligned with the SQLite schema."""

    timestamp_utc: str
    us_2y: Optional[float]
    us_10y: Optional[float]
    jp_10y: Optional[float]
    spread_10y: Optional[float]
    source: str
    us_2s10s_slope: Optional[float] = None
    spread_bps: Optional[float] = None
    is_us_stale: bool = False
    is_jp_stale: bool = False
    us_observation_date: Optional[str] = None
    jp_observation_date: Optional[str] = None
    notes: Optional[str] = None

    def to_db_tuple(self) -> Tuple[str, Optional[float], Optional[float], Optional[float], Optional[float], str]:
        """Returns tuple suitable for INSERT into yield_spreads table."""
        return (
            self.timestamp_utc,
            self.us_2y,
            self.us_10y,
            self.jp_10y,
            self.spread_10y,
            self.source,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Returns dictionary representation."""
        return asdict(self)


# -----------------------------------------------------------------------------
# Core Calculation Functions
# -----------------------------------------------------------------------------


def compute_pair_spread(
    base_yield: Optional[float],
    quote_yield: Optional[float],
    precision: int = 4,
) -> Optional[float]:
    """
    Pair-agnostic sovereign yield spread:
        `spread = round(base_yield - quote_yield, precision)`

    A positive, widening spread means the base currency offers an increasing yield
    advantage (base-currency strength). Examples:
        - EUR/USD: base = DE 10Y Bund, quote = US 10Y  -> DE10Y - US10Y
        - USD/JPY: base = US 10Y,      quote = JGB 10Y -> US10Y - JGB10Y

    Returns:
        Rounded float spread in percentage points, or None if either input is
        None, NaN or infinite.
    """
    if base_yield is None or quote_yield is None:
        return None
    try:
        b = float(base_yield)
        q = float(quote_yield)
        if math.isnan(b) or math.isnan(q) or math.isinf(b) or math.isinf(q):
            return None
        return round(b - q, precision)
    except (ValueError, TypeError):
        return None


def compute_yield_spread(
    us_10y: Optional[float],
    jp_10y: Optional[float],
    precision: int = 4,
) -> Optional[float]:
    """
    Computes the US10Y - JGB10Y yield spread (USD/JPY, backward compatible):
        `spread_10y = round(us_10y - jp_10y, precision)`

    Thin wrapper over :func:`compute_pair_spread` (USD is the base currency of
    USD/JPY, so base_yield = us_10y and quote_yield = jp_10y).

    Args:
        us_10y: US 10-Year Treasury Yield (in percentage points, e.g., 5.12).
        jp_10y: Japan 10-Year Government Bond Yield (in percentage points, e.g., 2.925).
        precision: Rounding decimal places (default: 4 decimal places).

    Returns:
        Rounded float spread in percentage points, or None if either input is None or NaN.
    """
    return compute_pair_spread(us_10y, jp_10y, precision=precision)


def compute_spread_for_pair(
    pair: Any,
    us_10y: Optional[float],
    foreign_10y: Optional[float],
    precision: int = 4,
) -> Optional[float]:
    """
    Computes `base_yield - quote_yield` for a pair profile given the US 10Y yield
    and the non-US ("foreign") 10Y yield (JGB10Y for USD/JPY, DE10Y for EUR/USD).

    `pair` may be a pair name or a PairProfile. The USD leg is the base for
    USD/JPY (US - JP) and the quote for EUR/USD (DE - US).
    """
    from fx_signal_reader.core.pair_profile import get_pair_profile

    profile = get_pair_profile(pair)
    if profile.is_base_usd:
        return compute_pair_spread(us_10y, foreign_10y, precision=precision)
    return compute_pair_spread(foreign_10y, us_10y, precision=precision)


def compute_spread_bps(
    spread_percent: Optional[float],
    precision: int = 2,
) -> Optional[float]:
    """
    Converts a percentage spread (e.g., 2.195) into basis points (bps, e.g., 219.50 bps).
    1% = 100 bps.
    """
    if spread_percent is None:
        return None
    try:
        val = float(spread_percent)
        if math.isnan(val) or math.isinf(val):
            return None
        return round(val * 100.0, precision)
    except (ValueError, TypeError):
        return None


def compute_curve_slope(
    long_yield: Optional[float],
    short_yield: Optional[float],
    precision: int = 4,
) -> Optional[float]:
    """
    Computes yield curve slope between two maturities:
        `slope = round(long_yield - short_yield, precision)`

    Returns float or None if either input is invalid.
    """
    if long_yield is None or short_yield is None:
        return None
    try:
        l = float(long_yield)
        s = float(short_yield)
        if math.isnan(l) or math.isnan(s) or math.isinf(l) or math.isinf(s):
            return None
        return round(l - s, precision)
    except (ValueError, TypeError):
        return None


def compute_us_2s10s_slope(
    us_10y: Optional[float],
    us_2y: Optional[float],
    precision: int = 4,
) -> Optional[float]:
    """
    Computes the benchmark US 2s10s Treasury yield curve slope:
        `slope = round(us_10y - us_2y, 4)`
    A positive slope indicates a normal curve; negative indicates an inverted curve.
    """
    return compute_curve_slope(us_10y, us_2y, precision=precision)


# -----------------------------------------------------------------------------
# Multi-Source Parsers (FRED, Yahoo Finance, Japan MoF)
# -----------------------------------------------------------------------------


def parse_fred_yield(observation_val: Any) -> Optional[float]:
    """
    Parses a single FRED observation value. FRED represents market holidays
    and missing daily values with a period ('.').
    """
    if observation_val is None:
        return None
    if isinstance(observation_val, dict):
        raw_val = observation_val.get("value")
    else:
        raw_val = str(observation_val)

    return parse_float_or_none(raw_val)


def extract_fred_series(observations: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Extracts a clean date -> yield mapping from a FRED API observation array.
    Ignores non-numeric / holiday observations ('.').
    Dates are formatted as YYYY-MM-DD.
    """
    result: Dict[str, float] = {}
    for obs in observations:
        d = obs.get("date")
        if not d:
            continue
        val = parse_fred_yield(obs.get("value"))
        if val is not None:
            # Standardize date to YYYY-MM-DD
            try:
                norm_d = normalize_datetime_utc(d)[:10]
                result[norm_d] = val
            except Exception:
                pass
    return result


def parse_yahoo_tnx(price_or_candle: Any) -> Optional[float]:
    """
    Parses yield from Yahoo Finance `^TNX` quote or candle dict.
    Note: ^TNX represents the 10-Year US Treasury yield directly in percentage points
    (e.g., 5.103 means 5.103%).
    """
    if price_or_candle is None:
        return None
    if isinstance(price_or_candle, (int, float)):
        return parse_float_or_none(price_or_candle)
    if isinstance(price_or_candle, dict):
        for key in ("Close", "close", "price", "regularMarketPrice"):
            if key in price_or_candle:
                return parse_float_or_none(price_or_candle[key])
    return parse_float_or_none(price_or_candle)


def parse_mof_csv(
    csv_content: str,
    target_tenor: str = "10年",
) -> Dict[str, float]:
    """
    Parses Ministry of Finance Japan (MoF) daily JGB interest rate CSV (`jgbcm.csv`).
    MoF dates are formatted in Japanese Imperial Era (e.g., 'R8.11.17' or 'R6.11.15').

    Returns:
        Dictionary mapping ISO-8601 calendar date (`YYYY-MM-DD`) -> JGB yield float.
    """
    result: Dict[str, float] = {}
    reader = csv.reader(StringIO(csv_content))
    header: Optional[List[str]] = None
    target_idx: Optional[int] = None

    for row in reader:
        if not row:
            continue
        first_col = row[0].strip()
        if "基準日" in first_col:
            header = [col.strip() for col in row]
            if target_tenor in header:
                target_idx = header.index(target_tenor)
            continue

        if header and target_idx is not None and target_idx < len(row):
            date_raw = first_col
            try:
                # Convert Reiwa date (e.g., R8.11.17) to YYYY-MM-DD
                iso_date = parse_japanese_era_date(date_raw)
                rate_val = parse_float_or_none(row[target_idx])
                if rate_val is not None:
                    result[iso_date] = rate_val
            except Exception:
                continue

    return result


def parse_de_bund_csv(csv_content: str) -> Dict[str, float]:
    """
    Parses a daily German 10Y Bund yield series from a Deutsche Bundesbank
    (`format=csv`) or ECB Data Portal (`format=csvdata`) time-series download.

    Supported layouts (delimiter `,` or `;`, optional UTF-8 BOM):
      - SDMX-CSV with a header row containing `TIME_PERIOD` and `OBS_VALUE`
        (ECB `csvdata`, Bundesbank `sdmx_csv`).
      - Bundesbank classic CSV: data rows of `YYYY-MM-DD<delim>value[<delim>flags]`
        (decimal comma tolerated when the delimiter is `;`). Missing observations
        (`.`, empty) are skipped.

    Returns:
        Dictionary mapping `YYYY-MM-DD` -> yield in percentage points.
    """
    result: Dict[str, float] = {}
    text = csv_content.lstrip("\ufeff")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return result
    delimiter = ";" if lines[0].count(";") > lines[0].count(",") else ","
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def _num(raw: str) -> Optional[float]:
        raw = raw.strip().strip('"')
        if delimiter == ";":
            raw = raw.replace(",", ".")
        return parse_float_or_none(raw)

    time_idx: Optional[int] = None
    val_idx: Optional[int] = None
    for row in csv.reader(lines, delimiter=delimiter):
        cells = [c.strip().strip('"') for c in row]
        upper = [c.upper() for c in cells]
        if "TIME_PERIOD" in upper and "OBS_VALUE" in upper:
            time_idx = upper.index("TIME_PERIOD")
            val_idx = upper.index("OBS_VALUE")
            continue
        if time_idx is not None and val_idx is not None:
            if max(time_idx, val_idx) >= len(cells):
                continue
            d_raw, v_raw = cells[time_idx], cells[val_idx]
        else:
            d_idx = next((i for i, c in enumerate(cells) if date_re.match(c)), None)
            if d_idx is None or d_idx + 1 >= len(cells):
                continue
            d_raw, v_raw = cells[d_idx], cells[d_idx + 1]
        if not date_re.match(d_raw):
            continue
        val = _num(v_raw)
        if val is not None and not math.isinf(val):
            result[d_raw] = val
    return result


# -----------------------------------------------------------------------------
# Holiday & Publication Misalignment Handling
# -----------------------------------------------------------------------------


def align_yield_series(
    us_10y_series: Dict[str, float],
    jp_10y_series: Dict[str, float],
    us_2y_series: Optional[Dict[str, float]] = None,
    fill_method: str = "ffill",
    max_stale_days: int = 5,
    source_label: str = "FRED+MoF",
    is_base_usd: bool = True,
) -> List[YieldSpreadRecord]:
    """
    Aligns historical daily yields across US and Japanese bond markets, handling
    holidays and publication schedule misalignments.

    When `fill_method='ffill'` (forward fill), if a market is closed for a holiday
    or weekend, the last available published yield is carried forward up to
    `max_stale_days`.

    Args:
        us_10y_series: Mapping of `YYYY-MM-DD` -> US 10Y yield.
        jp_10y_series: Mapping of `YYYY-MM-DD` -> JGB 10Y yield.
        us_2y_series: Optional mapping of `YYYY-MM-DD` -> US 2Y yield.
        fill_method: 'ffill' for forward filling, or 'exact' for exact matching only.
        max_stale_days: Maximum calendar days to carry forward a holiday observation.
        source_label: Label for the data sources combined.

    Returns:
        List of aligned `YieldSpreadRecord` objects sorted chronologically by timestamp.
    """
    if us_2y_series is None:
        us_2y_series = {}

    all_dates = sorted(set(us_10y_series.keys()) | set(jp_10y_series.keys()) | set(us_2y_series.keys()))
    if not all_dates:
        return []

    records: List[YieldSpreadRecord] = []

    last_us_10y: Optional[float] = None
    last_us_10y_date: Optional[date] = None

    last_jp_10y: Optional[float] = None
    last_jp_10y_date: Optional[date] = None

    last_us_2y: Optional[float] = None
    last_us_2y_date: Optional[date] = None

    for d_str in all_dates:
        try:
            curr_date = date.fromisoformat(d_str)
        except ValueError:
            continue

        # --- US 10Y ---
        if d_str in us_10y_series:
            current_us_10y = us_10y_series[d_str]
            last_us_10y = current_us_10y
            last_us_10y_date = curr_date
            us_10y_val = current_us_10y
            is_us_stale = False
            us_obs_date_str = d_str
        elif fill_method == "ffill" and last_us_10y is not None and last_us_10y_date is not None:
            days_diff = (curr_date - last_us_10y_date).days
            if days_diff <= max_stale_days:
                us_10y_val = last_us_10y
                is_us_stale = True
                us_obs_date_str = last_us_10y_date.isoformat()
            else:
                us_10y_val = None
                is_us_stale = True
                us_obs_date_str = None
        else:
            us_10y_val = None
            is_us_stale = False
            us_obs_date_str = None

        # --- JP 10Y ---
        if d_str in jp_10y_series:
            current_jp_10y = jp_10y_series[d_str]
            last_jp_10y = current_jp_10y
            last_jp_10y_date = curr_date
            jp_10y_val = current_jp_10y
            is_jp_stale = False
            jp_obs_date_str = d_str
        elif fill_method == "ffill" and last_jp_10y is not None and last_jp_10y_date is not None:
            days_diff = (curr_date - last_jp_10y_date).days
            if days_diff <= max_stale_days:
                jp_10y_val = last_jp_10y
                is_jp_stale = True
                jp_obs_date_str = last_jp_10y_date.isoformat()
            else:
                jp_10y_val = None
                is_jp_stale = True
                jp_obs_date_str = None
        else:
            jp_10y_val = None
            is_jp_stale = False
            jp_obs_date_str = None

        # --- US 2Y ---
        if d_str in us_2y_series:
            current_us_2y = us_2y_series[d_str]
            last_us_2y = current_us_2y
            last_us_2y_date = curr_date
            us_2y_val = current_us_2y
        elif fill_method == "ffill" and last_us_2y is not None and last_us_2y_date is not None:
            days_diff = (curr_date - last_us_2y_date).days
            if days_diff <= max_stale_days:
                us_2y_val = last_us_2y
            else:
                us_2y_val = None
        else:
            us_2y_val = None

        # Calculations
        if is_base_usd:
            spread = compute_pair_spread(us_10y_val, jp_10y_val)
        else:
            spread = compute_pair_spread(jp_10y_val, us_10y_val)
        bps = compute_spread_bps(spread)
        slope = compute_us_2s10s_slope(us_10y_val, us_2y_val)
        ts_utc = f"{d_str}T00:00:00Z"

        notes_parts: List[str] = []
        if is_us_stale and us_obs_date_str:
            notes_parts.append(f"US holiday/closed (carried from {us_obs_date_str})")
        if is_jp_stale and jp_obs_date_str:
            notes_parts.append(f"JP holiday/closed (carried from {jp_obs_date_str})")

        rec = YieldSpreadRecord(
            timestamp_utc=ts_utc,
            us_2y=us_2y_val,
            us_10y=us_10y_val,
            jp_10y=jp_10y_val,
            spread_10y=spread,
            source=source_label,
            us_2s10s_slope=slope,
            spread_bps=bps,
            is_us_stale=is_us_stale,
            is_jp_stale=is_jp_stale,
            us_observation_date=us_obs_date_str,
            jp_observation_date=jp_obs_date_str,
            notes="; ".join(notes_parts) if notes_parts else None,
        )
        records.append(rec)

    return records


def calculate_live_spread(
    us_10y: Optional[float],
    jp_10y: Optional[float],
    us_2y: Optional[float] = None,
    us_timestamp_utc: Optional[str] = None,
    jp_timestamp_utc: Optional[str] = None,
    evaluation_timestamp_utc: Optional[str] = None,
    max_misalignment_hours: float = 96.0,
    source: str = "FRED+MoF",
) -> YieldSpreadRecord:
    """
    Computes a single real-time or latest yield spread record, evaluating timestamp
    misalignment between US and Japanese inputs.

    Args:
        us_10y: US 10-Year yield (from FRED DGS10 or Yahoo ^TNX).
        jp_10y: JGB 10-Year yield (from MoF or Yahoo).
        us_2y: Optional US 2-Year yield (from FRED DGS2).
        us_timestamp_utc: Timestamp of the US observation.
        jp_timestamp_utc: Timestamp of the JP observation.
        evaluation_timestamp_utc: Current evaluation timestamp (defaults to now).
        max_misalignment_hours: Threshold in hours before flagging a leg as stale.
        source: Source identifier string.
    """
    eval_ts = normalize_datetime_utc(evaluation_timestamp_utc, fallback_now=True)
    eval_dt = datetime.strptime(eval_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    is_us_stale = False
    is_jp_stale = False
    notes_list: List[str] = []

    if us_timestamp_utc and is_valid_iso8601_utc(us_timestamp_utc):
        us_dt = datetime.strptime(us_timestamp_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        us_hours_lag = (eval_dt - us_dt).total_seconds() / 3600.0
        if us_hours_lag > max_misalignment_hours:
            is_us_stale = True
            notes_list.append(f"US yield lag ({us_hours_lag:.1f}h)")

    if jp_timestamp_utc and is_valid_iso8601_utc(jp_timestamp_utc):
        jp_dt = datetime.strptime(jp_timestamp_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        jp_hours_lag = (eval_dt - jp_dt).total_seconds() / 3600.0
        if jp_hours_lag > max_misalignment_hours:
            is_jp_stale = True
            notes_list.append(f"JP yield lag ({jp_hours_lag:.1f}h)")

    spread = compute_yield_spread(us_10y, jp_10y)
    slope = compute_us_2s10s_slope(us_10y, us_2y)
    bps = compute_spread_bps(spread)

    return YieldSpreadRecord(
        timestamp_utc=eval_ts,
        us_2y=us_2y,
        us_10y=us_10y,
        jp_10y=jp_10y,
        spread_10y=spread,
        source=source,
        us_2s10s_slope=slope,
        spread_bps=bps,
        is_us_stale=is_us_stale,
        is_jp_stale=is_jp_stale,
        us_observation_date=us_timestamp_utc[:10] if us_timestamp_utc else None,
        jp_observation_date=jp_timestamp_utc[:10] if jp_timestamp_utc else None,
        notes="; ".join(notes_list) if notes_list else None,
    )


# -----------------------------------------------------------------------------
# SQLite Persistence Helper
# -----------------------------------------------------------------------------


def save_yield_spread(conn: sqlite3.Connection, record: YieldSpreadRecord) -> None:
    """
    Inserts or replaces a yield spread record into the SQLite database.
    """
    sql = """
    INSERT OR REPLACE INTO yield_spreads (
        timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source
    ) VALUES (?, ?, ?, ?, ?, ?)
    """
    conn.execute(sql, record.to_db_tuple())
