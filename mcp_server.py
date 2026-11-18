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
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Ensure project root is importable regardless of the MCP client's working directory
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from collectors.market_ticks import MarketTicksCollector
from collectors.news_rss import NewsRSSCollector
from spread_calculator import (
    compute_spread_bps,
    compute_us_2s10s_slope,
    compute_yield_spread,
)

DEFAULT_DB_PATH = PROJECT_ROOT / "usdjpy_signals.db"
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


def get_latest_yield_spread(db_path: str | None = None) -> dict[str, Any]:
    """
    Return the latest US-Japan sovereign yield spread snapshot along with the
    most recent USD/JPY (`JPY=X`) spot tick from `market_ticks`.
    """
    conn = get_readonly_connection(db_path)
    try:
        spread_row = conn.execute(
            """
            SELECT timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source
            FROM yield_spreads
            ORDER BY timestamp_utc DESC
            LIMIT 1
            """
        ).fetchone()

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

    us_2y = spread_row["us_2y"] if spread_row else None
    us_10y = spread_row["us_10y"] if spread_row else None
    jp_10y = spread_row["jp_10y"] if spread_row else None
    spread_10y = spread_row["spread_10y"] if spread_row else None
    if spread_10y is None and us_10y is not None and jp_10y is not None:
        spread_10y = compute_yield_spread(us_10y, jp_10y)

    return {
        "timestamp_utc": spread_row["timestamp_utc"] if spread_row else None,
        "us_2y": us_2y,
        "us_10y": us_10y,
        "jp_10y": jp_10y,
        "spread_10y": spread_10y,
        "spread_bps": compute_spread_bps(spread_10y),
        "us_2s10s_slope": compute_us_2s10s_slope(us_10y, us_2y),
        "source": spread_row["source"] if spread_row else None,
        "usdjpy_spot": spot_row["price"] if spot_row else None,
        "usdjpy_timestamp_utc": spot_row["timestamp_utc"] if spot_row else None,
    }


def get_upcoming_macro_events(
    currency: str | None = None,
    impact: str | None = None,
    limit: int = 20,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """
    Query scheduled economic calendar events from `macro_events`, optionally
    filtered by currency (`USD` / `JPY`) and impact (`High` / `Medium` / `Low`),
    ordered chronologically (`timestamp_utc ASC`).
    """
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
) -> list[dict[str, Any]]:
    """
    Query MoF / BoJ verbal intervention headlines where `matched_keywords != ''`,
    evaluate escalation tiers (1-4) using `NewsRSSCollector` regex taxonomy,
    filter to `tier >= min_tier`, and return newest alerts first.
    """
    effective_limit = min(int(limit), _MAX_QUERY_LIMIT)
    if effective_limit <= 0:
        return []

    conn = get_readonly_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT url_hash, published_utc, source, title, url, matched_keywords
            FROM news_headlines
            WHERE matched_keywords IS NOT NULL AND TRIM(matched_keywords) != ''
            ORDER BY published_utc DESC
            """
        ).fetchall()
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
) -> dict[str, Any]:
    """
    Return latest OHLCV snapshot and bar-over-bar change for core USD/JPY market
    indicators (`JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F`) or caller-specified symbols.
    """
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


mcp_server = MCPServer(
    "usdjpy-sqlite-signals",
    title="USD/JPY SQLite Signal Reader",
    description="Read-only MCP server querying USD/JPY yield spreads, macro events, MoF/BoJ intervention alerts, and market ticks.",
    instructions=(
        "All tools query the local SQLite database strictly in read-only URI mode. "
        "Use get_latest_yield_spread for rate differentials and spot USD/JPY, "
        "get_upcoming_macro_events for scheduled USD/JPY releases, "
        "get_recent_mof_alerts for tiered verbal intervention headlines, and "
        "get_market_ticks_summary for cross-asset OHLCV snapshots."
    ),
    version="1.0.0",
)


@mcp_server.tool(
    name="get_latest_yield_spread",
    title="Get Latest US-Japan Yield Spread",
    description=(
        "Return the latest US-Japan sovereign yield spread (US 2Y, US 10Y, JP 10Y, "
        "10Y spread in % and bps, source) plus the latest JPY=X spot price and timestamp."
    ),
    annotations=_READ_ONLY_TOOL_ANNOTATIONS,
)
def _tool_get_latest_yield_spread() -> dict[str, Any]:
    return get_latest_yield_spread()


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
) -> list[dict[str, Any]]:
    return get_upcoming_macro_events(
        currency=currency,
        impact=impact,
        limit=limit,
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
) -> list[dict[str, Any]]:
    return get_recent_mof_alerts(min_tier=min_tier, limit=limit)


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
) -> dict[str, Any]:
    return get_market_ticks_summary(symbols=symbols)


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
