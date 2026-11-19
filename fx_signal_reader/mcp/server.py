"""
Read-Only Custom SQLite MCP Server for the USD/JPY Signal Reader.

Exposes domain-specific query tools backed by a strict read-only SQLite URI
connection (`mode=ro`) over `usdjpy_signals.db` (or `FX_DB_PATH`).

Reuses:
  - `NewsRSSCollector` 4-tier verbal intervention taxonomy (`collectors/news_rss.py`)
  - `MarketTicksCollector.TICKERS` core watchlist (`collectors/market_ticks.py`)
  - `spread_calculator` yield spread & curve metrics (`spread_calculator.py`)
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from fx_signal_reader.collectors.market_ticks import MarketTicksCollector
from fx_signal_reader.collectors.news_rss import NewsRSSCollector
from fx_signal_reader.core import DEFAULT_DB_PATH, PROJECT_ROOT
from fx_signal_reader.core.indicators import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_EMA_PERIOD,
    DEFAULT_SMA_PERIOD,
    classify_market_trend,
    classify_spread_momentum,
    compute_latest_atr,
    compute_latest_ema,
    compute_latest_sma,
    compute_rate_of_change,
)
from fx_signal_reader.core.normalizer import normalize_datetime_utc
from fx_signal_reader.core.spread_calculator import (
    compute_spread_bps,
    compute_us_2s10s_slope,
    compute_yield_spread,
)

CORE_TICKER_SYMBOLS: tuple[str, ...] = tuple(MarketTicksCollector.TICKERS)
_MAX_QUERY_LIMIT = 1000

# Stateless evaluator reusing NewsRSSCollector.ESCALATION_TIERS & KEY_ENTITIES
_NEWS_EVALUATOR = NewsRSSCollector()

_READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the target SQLite database path from explicit argument, FX_DB_PATH, or default."""
    if db_path is not None:
        return Path(db_path).resolve()
    env_path = os.getenv("FX_DB_PATH")
    raw_path = env_path if env_path else str(DEFAULT_DB_PATH)
    return Path(raw_path).resolve()


def get_readonly_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """
    Open a strict read-only SQLite URI connection (`?mode=ro`).

    Any attempted INSERT, UPDATE, DELETE, CREATE, or DDL operation will fail with
    `sqlite3.OperationalError: attempt to write a readonly database`.
    """
    resolved = resolve_db_path(db_path)
    uri = f"file:{resolved}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    return conn


def _normalize_as_of_utc(as_of_utc: str | None) -> str | None:
    """Validate and normalize an optional `as_of_utc` boundary to `YYYY-MM-DDTHH:MM:SSZ`."""
    if as_of_utc is None:
        return None
    if isinstance(as_of_utc, bool):
        raise ValueError("as_of_utc must be a valid timestamp string, not bool.")
    return normalize_datetime_utc(as_of_utc, fallback_now=False)


def _parse_utc_str(ts_utc: str) -> datetime:
    """Parse a normalized `YYYY-MM-DDTHH:MM:SSZ` timestamp into an aware UTC datetime."""
    return datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _validate_non_negative_hours(value: int | float, name: str) -> float:
    """Validate that an hour offset parameter is finite, non-negative, and not a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a non-negative number, got {type(value).__name__}.")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be finite and non-negative, got {value!r}.")
    return numeric


def get_latest_yield_spread(
    db_path: str | None = None,
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    """
    Return the latest US-Japan sovereign yield spread snapshot along with the
    most recent USD/JPY (`JPY=X`) spot tick from `market_ticks` and spread momentum.
    When `as_of_utc` is set, restricts both queries to rows at or before `as_of_utc`.
    """
    normalized_as_of = _normalize_as_of_utc(as_of_utc)
    conn = get_readonly_connection(db_path)
    try:
        if normalized_as_of is not None:
            spread_rows = conn.execute(
                """
                SELECT timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source
                FROM yield_spreads
                WHERE timestamp_utc <= ?
                ORDER BY timestamp_utc DESC
                LIMIT 21
                """,
                (normalized_as_of,),
            ).fetchall()

            spot_row = conn.execute(
                """
                SELECT price, timestamp_utc
                FROM market_ticks
                WHERE symbol = 'JPY=X' AND timestamp_utc <= ?
                ORDER BY timestamp_utc DESC
                LIMIT 1
                """,
                (normalized_as_of,),
            ).fetchone()
        else:
            spread_rows = conn.execute(
                """
                SELECT timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source
                FROM yield_spreads
                ORDER BY timestamp_utc DESC
                LIMIT 21
                """
            ).fetchall()

            spot_row = conn.execute(
                """
                SELECT price, timestamp_utc
                FROM market_ticks
                WHERE symbol = 'JPY=X'
                ORDER BY timestamp_utc DESC
                LIMIT 1
                """
            ).fetchone()
    finally:
        conn.close()

    spread_row = spread_rows[0] if spread_rows else None
    us_2y = spread_row["us_2y"] if spread_row else None
    us_10y = spread_row["us_10y"] if spread_row else None
    jp_10y = spread_row["jp_10y"] if spread_row else None
    spread_10y = spread_row["spread_10y"] if spread_row else None
    if spread_10y is None and us_10y is not None and jp_10y is not None:
        spread_10y = compute_yield_spread(us_10y, jp_10y)

    chronological_spreads: list[float] = []
    for row in reversed(spread_rows):
        row_spread = row["spread_10y"]
        if row_spread is None and row["us_10y"] is not None and row["jp_10y"] is not None:
            row_spread = compute_yield_spread(row["us_10y"], row["jp_10y"])
        if row_spread is not None:
            chronological_spreads.append(float(row_spread))

    if spread_row is None or spread_10y is None:
        spread_5d_change = None
        spread_20d_change = None
        momentum = None
    else:
        spread_5d_change = compute_rate_of_change(chronological_spreads, 5)
        spread_20d_change = compute_rate_of_change(chronological_spreads, 20)
        momentum = classify_spread_momentum(spread_5d_change, spread_20d_change)

    return {
        "timestamp_utc": spread_row["timestamp_utc"] if spread_row else None,
        "us_2y": us_2y,
        "us_10y": us_10y,
        "jp_10y": jp_10y,
        "spread_10y": spread_10y,
        "spread_bps": compute_spread_bps(spread_10y),
        "us_2s10s_slope": compute_us_2s10s_slope(us_10y, us_2y),
        "spread_5d_change": spread_5d_change,
        "spread_20d_change": spread_20d_change,
        "momentum": momentum,
        "source": spread_row["source"] if spread_row else None,
        "usdjpy_spot": spot_row["price"] if spot_row else None,
        "usdjpy_timestamp_utc": spot_row["timestamp_utc"] if spot_row else None,
    }


def get_upcoming_macro_events(
    currency: str | None = None,
    impact: str | None = None,
    limit: int = 20,
    db_path: str | None = None,
    as_of_utc: str | None = None,
    past_hours: int = 4,
    future_hours: int = 48,
) -> list[dict[str, Any]]:
    """
    Query scheduled economic calendar events from `macro_events`, optionally
    filtered by currency (`USD` / `JPY`), impact (`High` / `Medium` / `Low`),
    and a point-in-time window `[as_of_utc - past_hours, as_of_utc + future_hours]`,
    ordered chronologically (`timestamp_utc ASC`).
    """
    normalized_as_of = _normalize_as_of_utc(as_of_utc)
    past_h = _validate_non_negative_hours(past_hours, "past_hours")
    future_h = _validate_non_negative_hours(future_hours, "future_hours")

    effective_limit = min(int(limit), _MAX_QUERY_LIMIT)
    if effective_limit <= 0:
        return []

    clauses: list[str] = []
    params: list[Any] = []

    if currency and currency.strip():
        clauses.append("UPPER(currency) = UPPER(?)")
        params.append(currency.strip())

    if impact and impact.strip():
        clauses.append("LOWER(impact) = LOWER(?)")
        params.append(impact.strip())

    if normalized_as_of is not None:
        as_of_dt = _parse_utc_str(normalized_as_of)
        window_start = (as_of_dt - timedelta(hours=past_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        window_end = (as_of_dt + timedelta(hours=future_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        clauses.append("timestamp_utc >= ?")
        params.append(window_start)
        clauses.append("timestamp_utc <= ?")
        params.append(window_end)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT event_hash, timestamp_utc, currency, title, impact, forecast, previous, actual
        FROM macro_events
        {where_sql}
        ORDER BY timestamp_utc ASC
        LIMIT ?
    """
    params.append(effective_limit)

    conn = get_readonly_connection(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_recent_mof_alerts(
    min_tier: int = 1,
    limit: int = 20,
    db_path: str | None = None,
    as_of_utc: str | None = None,
    lookback_hours: int | None = None,
) -> list[dict[str, Any]]:
    """
    Query MoF / BoJ verbal intervention headlines where `matched_keywords != ''`,
    evaluate escalation tiers (1-4) using `NewsRSSCollector` regex taxonomy,
    filter to `tier >= min_tier`, and return newest alerts first.
    When `as_of_utc` and/or `lookback_hours` are provided, restricts headlines to
    `published_utc <= as_of_utc` and `published_utc >= anchor - lookback_hours`.
    """
    normalized_as_of = _normalize_as_of_utc(as_of_utc)
    lookback_h = (
        _validate_non_negative_hours(lookback_hours, "lookback_hours")
        if lookback_hours is not None
        else None
    )

    effective_limit = min(int(limit), _MAX_QUERY_LIMIT)
    if effective_limit <= 0:
        return []

    clauses = ["matched_keywords IS NOT NULL", "TRIM(matched_keywords) != ''"]
    params: list[Any] = []

    if normalized_as_of is not None:
        clauses.append("published_utc <= ?")
        params.append(normalized_as_of)

    if lookback_h is not None:
        anchor_dt = (
            _parse_utc_str(normalized_as_of)
            if normalized_as_of is not None
            else datetime.now(timezone.utc)
        )
        cutoff_utc = (anchor_dt - timedelta(hours=lookback_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        clauses.append("published_utc >= ?")
        params.append(cutoff_utc)

    query = f"""
        SELECT url_hash, published_utc, source, title, url, matched_keywords
        FROM news_headlines
        WHERE {' AND '.join(clauses)}
        ORDER BY published_utc DESC
    """

    conn = get_readonly_connection(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    alerts: list[dict[str, Any]] = []
    for row in rows:
        title = row["title"] or ""
        evaluation = _NEWS_EVALUATOR.evaluate_headline(title)
        tier = int(evaluation["highest_tier_level"])
        if tier < min_tier:
            continue

        alerts.append(
            {
                "url_hash": row["url_hash"],
                "published_utc": row["published_utc"],
                "source": row["source"],
                "title": title,
                "url": row["url"],
                "matched_keywords": row["matched_keywords"],
                "tier": tier,
                "tier_name": evaluation["highest_tier_name"],
                "severity": evaluation["severity"],
                "matched_entities": evaluation["matched_entities"],
                "matched_tier_keywords": evaluation["matched_tier_keywords"],
            }
        )
        if len(alerts) >= effective_limit:
            break

    return alerts


def get_market_ticks_summary(
    symbols: list[str] | None = None,
    db_path: str | None = None,
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    """
    Return latest OHLCV snapshot and bar-over-bar change for core USD/JPY market
    indicators (`JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F`) or caller-specified symbols.
    When `as_of_utc` is set, considers only ticks at or before `as_of_utc`.
    """
    normalized_as_of = _normalize_as_of_utc(as_of_utc)

    if symbols is None:
        target_symbols = list(CORE_TICKER_SYMBOLS)
    else:
        target_symbols = list(
            dict.fromkeys(sym.strip() for sym in symbols if sym and sym.strip())
        )

    ticks_by_symbol: dict[str, dict[str, Any]] = {}
    missing_symbols: list[str] = []
    latest_overall_ts: str | None = None

    conn = get_readonly_connection(db_path)
    try:
        for symbol in target_symbols:
            if normalized_as_of is not None:
                rows = conn.execute(
                    """
                    SELECT symbol, timestamp_utc, price, open, high, low, volume
                    FROM market_ticks
                    WHERE symbol = ? AND timestamp_utc <= ?
                    ORDER BY timestamp_utc DESC
                    LIMIT 2
                    """,
                    (symbol, normalized_as_of),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT symbol, timestamp_utc, price, open, high, low, volume
                    FROM market_ticks
                    WHERE symbol = ?
                    ORDER BY timestamp_utc DESC
                    LIMIT 2
                    """,
                    (symbol,),
                ).fetchall()

            if not rows:
                missing_symbols.append(symbol)
                continue

            latest = rows[0]
            close_val = float(latest["price"])
            open_val = float(latest["open"]) if latest["open"] is not None else None
            prev_close = float(rows[1]["price"]) if len(rows) > 1 else None

            baseline = prev_close if prev_close is not None else open_val
            if baseline is not None:
                bar_change: float | None = round(close_val - baseline, 6)
                bar_change_pct: float | None = (
                    round((bar_change / baseline) * 100.0, 4)
                    if baseline != 0
                    else None
                )
            else:
                bar_change = None
                bar_change_pct = None

            ts = latest["timestamp_utc"]
            if latest_overall_ts is None or ts > latest_overall_ts:
                latest_overall_ts = ts

            ticks_by_symbol[symbol] = {
                "symbol": symbol,
                "timestamp_utc": ts,
                "close": close_val,
                "open": open_val,
                "high": float(latest["high"]) if latest["high"] is not None else None,
                "low": float(latest["low"]) if latest["low"] is not None else None,
                "volume": float(latest["volume"]) if latest["volume"] is not None else None,
                "prev_close": prev_close,
                "bar_change": bar_change,
                "bar_change_pct": bar_change_pct,
            }
    finally:
        conn.close()

    return {
        "as_of_utc": latest_overall_ts,
        "requested_symbols": target_symbols,
        "symbol_count": len(ticks_by_symbol),
        "symbols": ticks_by_symbol,
        "missing_symbols": missing_symbols,
    }


def _validate_positive_int(value: Any, name: str) -> int:
    """Validate that an integer parameter is strictly positive and not a boolean."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}.")
    return value


def get_market_trend_summary(
    symbol: str = "JPY=X",
    db_path: str | None = None,
    as_of_utc: str | None = None,
    ema_period: int = DEFAULT_EMA_PERIOD,
    sma_period: int = DEFAULT_SMA_PERIOD,
) -> dict[str, Any]:
    """
    Compute rolling EMA-50 and SMA-200 trend indicators for `symbol` up to `as_of_utc`.

    Returns spot price, EMA-50, SMA-200, bar count analyzed, and trend classification
    (`BULLISH`, `BEARISH`, `SIDEWAYS`, or `None` when no bars exist).
    """
    normalized_as_of = _normalize_as_of_utc(as_of_utc)
    validated_ema = _validate_positive_int(ema_period, "ema_period")
    validated_sma = _validate_positive_int(sma_period, "sma_period")
    clean_symbol = (symbol or "").strip() or "JPY=X"
    fetch_limit = max(validated_ema * 5, validated_sma, 250)

    conn = get_readonly_connection(db_path)
    try:
        if normalized_as_of is not None:
            rows = conn.execute(
                """
                SELECT timestamp_utc, price, high, low
                FROM market_ticks
                WHERE symbol = ? AND timestamp_utc <= ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (clean_symbol, normalized_as_of, fetch_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT timestamp_utc, price, high, low
                FROM market_ticks
                WHERE symbol = ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (clean_symbol, fetch_limit),
            ).fetchall()
    finally:
        conn.close()

    chronological_rows = list(reversed(rows))
    valid_rows = [r for r in chronological_rows if r["price"] is not None]
    prices = [float(r["price"]) for r in valid_rows]
    highs = [
        float(r["high"]) if (r["high"] is not None and float(r["high"]) >= float(r["price"])) else float(r["price"])
        for r in valid_rows
    ]
    lows = [
        float(r["low"]) if (r["low"] is not None and float(r["low"]) <= float(r["price"])) else float(r["price"])
        for r in valid_rows
    ]
    spot_price = prices[-1] if prices else None
    latest_ts = valid_rows[-1]["timestamp_utc"] if valid_rows else None
    ema_50 = compute_latest_ema(prices, validated_ema)
    sma_200 = compute_latest_sma(prices, validated_sma)
    atr_14 = compute_latest_atr(highs, lows, prices, period=DEFAULT_ATR_PERIOD) if prices else None
    trend = classify_market_trend(spot_price, ema_50, sma_200) if spot_price is not None else None

    return {
        "symbol": clean_symbol,
        "timestamp_utc": latest_ts,
        "as_of_utc": normalized_as_of if normalized_as_of is not None else latest_ts,
        "spot_price": spot_price,
        "ema_50": ema_50,
        "sma_200": sma_200,
        "atr_14": atr_14,
        "trend": trend,
        "bars_count": len(prices),
    }


mcp_server = MCPServer(
    "usdjpy-sqlite-signals",
    title="USD/JPY SQLite Signal Reader",
    description="Read-only MCP server querying USD/JPY yield spreads, macro events, MoF/BoJ intervention alerts, and market ticks.",
    instructions=(
        "All tools query the local SQLite database strictly in read-only URI mode. "
        "Use get_latest_yield_spread for rate differentials and spread momentum, "
        "get_upcoming_macro_events for scheduled USD/JPY releases, "
        "get_recent_mof_alerts for tiered verbal intervention headlines, "
        "get_market_ticks_summary for cross-asset OHLCV snapshots, and "
        "get_market_trend_summary for rolling EMA-50 and SMA-200 trend status."
    ),
    version="1.0.0",
)


@mcp_server.tool(
    name="get_latest_yield_spread",
    title="Get Latest US-Japan Yield Spread",
    description=(
        "Return the latest US-Japan sovereign yield spread (US 2Y, US 10Y, JP 10Y, "
        "10Y spread in % and bps, 5d/20d spread changes, momentum, source) plus the "
        "latest JPY=X spot price and timestamp."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
def _tool_get_latest_yield_spread(
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    return get_latest_yield_spread(as_of_utc=as_of_utc)


@mcp_server.tool(
    name="get_upcoming_macro_events",
    title="Get Upcoming Macro Events",
    description=(
        "Query scheduled USD and JPY economic calendar events from macro_events, "
        "optionally filtered by currency (USD/JPY) and impact (High/Medium/Low), "
        "ordered by timestamp_utc ascending."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
def _tool_get_upcoming_macro_events(
    currency: str | None = None,
    impact: str | None = None,
    limit: int = 20,
    as_of_utc: str | None = None,
    past_hours: int = 4,
    future_hours: int = 48,
) -> list[dict[str, Any]]:
    return get_upcoming_macro_events(
        currency=currency,
        impact=impact,
        limit=limit,
        as_of_utc=as_of_utc,
        past_hours=past_hours,
        future_hours=future_hours,
    )


@mcp_server.tool(
    name="get_recent_mof_alerts",
    title="Get Recent MoF / BoJ Intervention Alerts",
    description=(
        "Query recent news headlines with intervention or policymaker keyword matches, "
        "score each headline on the 4-tier verbal intervention escalation ladder (1-4), "
        "filter by tier >= min_tier, and return newest alerts first."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
def _tool_get_recent_mof_alerts(
    min_tier: int = 1,
    limit: int = 20,
    as_of_utc: str | None = None,
    lookback_hours: int | None = None,
) -> list[dict[str, Any]]:
    return get_recent_mof_alerts(
        min_tier=min_tier,
        limit=limit,
        as_of_utc=as_of_utc,
        lookback_hours=lookback_hours,
    )


@mcp_server.tool(
    name="get_market_ticks_summary",
    title="Get Market Ticks Summary",
    description=(
        "Return latest close, open, high, low, volume, timestamp_utc, and bar change "
        "for core USD/JPY cross-asset symbols (JPY=X, ^TNX, ^GSPC, ^N225, ^VIX, GC=F) "
        "or requested ticker symbols."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
def _tool_get_market_ticks_summary(
    symbols: list[str] | None = None,
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    return get_market_ticks_summary(symbols=symbols, as_of_utc=as_of_utc)


@mcp_server.tool(
    name="get_market_trend_summary",
    title="Get Market Trend Summary",
    description=(
        "Compute rolling EMA-50 and SMA-200 trend indicators for a market symbol "
        "(default JPY=X) up to optional as_of_utc and return BULLISH, BEARISH, or SIDEWAYS status."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
def _tool_get_market_trend_summary(
    symbol: str = "JPY=X",
    as_of_utc: str | None = None,
) -> dict[str, Any]:
    return get_market_trend_summary(symbol=symbol, as_of_utc=as_of_utc)


def main() -> None:
    """CLI entrypoint for launching the USD/JPY SQLite MCP server over stdio."""
    parser = argparse.ArgumentParser(
        description="Run the read-only USD/JPY SQLite MCP server over stdio."
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Optional override path to the SQLite database (defaults to FX_DB_PATH or usdjpy_signals.db).",
    )
    args = parser.parse_args()
    if args.db_path:
        os.environ["FX_DB_PATH"] = str(Path(args.db_path).resolve())
    mcp_server.run(transport="stdio")


if __name__ == "__main__":
    main()
