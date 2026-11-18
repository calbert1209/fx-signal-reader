"""
Market Ticks and Yield Spreads Collector for USD/JPY Signal Reader.
Ingests:
  - Market tickers (JPY=X, ^TNX, ^GSPC, ^N225, ^VIX, GC=F) via yfinance
  - JGB 10-Year sovereign bond yield from official Japan Ministry of Finance (MoF) CSV
  - Computes US-Japan 10Y sovereign yield spread and writes to SQLite tables.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yfinance as yf

from fx_signal_reader.collectors.base import BaseCollector
from fx_signal_reader.core import DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH


class MarketTicksCollector(BaseCollector):
    """
    Collector for Yahoo Finance market quotes and MoF Japan JGB 10Y interest rate.
    """

    TICKERS = [
        "JPY=X",  # USD/JPY exchange rate
        "^TNX",  # 10-Year US Treasury Note Yield
        "^GSPC",  # S&P 500 Index
        "^N225",  # Nikkei 225 Index
        "^VIX",  # CBOE Volatility Index
        "GC=F",  # Gold Futures
    ]

    MOF_JGB_CSV_URL = (
        "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
    )

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        schema_path: Path | str = DEFAULT_SCHEMA_PATH,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        jitter: bool = True,
        timeout: float = 12.0,
        fred_api_key: Optional[str] = None,
    ) -> None:
        super().__init__(
            db_path=db_path,
            schema_path=schema_path,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            jitter=jitter,
            timeout=timeout,
        )
        self.fred_api_key = fred_api_key or os.getenv("FRED_API_KEY", "")

    def fetch_jgb_10y_from_mof(self) -> Dict[str, Any]:
        """
        Fetches the official JGB 10-Year yield from Japan Ministry of Finance (MoF) CSV.
        Parses Reiwa era dates into standard ISO-8601 UTC.
        """
        self.logger.info(f"Fetching JGB 10Y yield from MoF: {self.MOF_JGB_CSV_URL}")
        resp = self.fetch_url(self.MOF_JGB_CSV_URL, encoding="cp932")
        reader = csv.reader(resp.text.splitlines())

        header: Optional[List[str]] = None
        last_row: Optional[List[str]] = None

        for row in reader:
            if not row:
                continue
            if "基準日" in row[0]:
                header = row
            elif header and row[0].startswith("R"):
                last_row = row

        if not header or not last_row:
            raise ValueError("Failed to locate header or data rows in MoF JGB CSV.")

        y10_idx = header.index("10年")
        date_str = last_row[0].strip()
        rate_10y = float(last_row[y10_idx].strip())

        # Parse Reiwa era date: R8.11.17 -> 2026-11-17T00:00:00Z
        iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        m = re.match(r"R(\d+)\.(\d+)\.(\d+)", date_str)
        if m:
            reiwa_yr = int(m.group(1))
            gregorian_yr = 2018 + reiwa_yr  # R1 = 2019
            month = int(m.group(2))
            day = int(m.group(3))
            iso_date = f"{gregorian_yr:04d}-{month:02d}-{day:02d}T00:00:00Z"

        self.logger.info(
            f"Successfully parsed MoF JGB 10Y: {rate_10y}% (date: {date_str} -> {iso_date})"
        )
        return {
            "rate_10y": rate_10y,
            "raw_date": date_str,
            "timestamp_utc": iso_date,
            "source": "Japan Ministry of Finance (mof.go.jp)",
        }

    def fetch_fred_latest(self, series_id: str) -> Optional[float]:
        """
        Fetches latest FRED observation if FRED_API_KEY is configured.
        """
        if not self.fred_api_key:
            return None

        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.fred_api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        }
        try:
            resp = self.fetch_url(url, params=params)
            for obs in resp.json().get("observations", []):
                if obs["value"] != ".":
                    return float(obs["value"])
        except Exception as exc:
            self.logger.warning(f"FRED fetch failed for {series_id}: {exc}")
        return None

    def fetch_ticker_data(
        self, symbol: str
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Fetches intraday (15m) or daily (1d) candles for a ticker using yfinance.
        """
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d", interval="15m")
        interval_used = "15m"
        if hist.empty:
            hist = ticker.history(period="5d", interval="1d")
            interval_used = "1d"

        if hist.empty:
            self.logger.warning(f"No history returned for {symbol}")
            return [], {"symbol": symbol, "status": "EMPTY"}

        candles: List[Dict[str, Any]] = []
        for idx, row in hist.iterrows():
            # Convert timestamp to ISO-8601 UTC
            if hasattr(idx, "tzinfo") and idx.tzinfo is not None:
                dt_utc = idx.astimezone(timezone.utc)
            else:
                dt_utc = idx.replace(tzinfo=timezone.utc)
            timestamp_utc = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

            price = float(row["Close"])
            open_p = float(row["Open"]) if "Open" in row else price
            high_p = float(row["High"]) if "High" in row else price
            low_p = float(row["Low"]) if "Low" in row else price
            vol = (
                float(row["Volume"])
                if ("Volume" in row and row["Volume"] == row["Volume"])
                else 0.0
            )

            candles.append(
                {
                    "symbol": symbol,
                    "timestamp_utc": timestamp_utc,
                    "price": price,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "volume": vol,
                }
            )

        latest = candles[-1]
        meta = {
            "symbol": symbol,
            "interval_used": interval_used,
            "bars_count": len(candles),
            "latest_price": latest["price"],
            "latest_timestamp": latest["timestamp_utc"],
        }
        return candles, meta

    def collect(self) -> Dict[str, Any]:
        """
        Collects market ticks for all symbols and JGB 10Y yield,
        writes them to `market_ticks` and `yield_spreads` tables.
        """
        conn = self.get_connection()
        total_ticks_inserted = 0
        latest_prices: Dict[str, float] = {}
        latest_timestamps: Dict[str, str] = {}
        ticker_summaries: Dict[str, Any] = {}

        # 1. Fetch and store yfinance market ticks
        for symbol in self.TICKERS:
            try:
                candles, meta = self.fetch_ticker_data(symbol)
                ticker_summaries[symbol] = meta
                if candles:
                    latest = candles[-1]
                    latest_prices[symbol] = latest["price"]
                    latest_timestamps[symbol] = latest["timestamp_utc"]

                    # Batch insert candles with composite primary key deduplication
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO market_ticks
                        (symbol, timestamp_utc, price, open, high, low, volume)
                        VALUES (:symbol, :timestamp_utc, :price, :open, :high, :low, :volume)
                        """,
                        candles,
                    )
                    total_ticks_inserted += len(candles)
            except Exception as exc:
                self.logger.error(f"Error collecting ticker {symbol}: {exc}")
                ticker_summaries[symbol] = {"error": str(exc)}

        # 2. Fetch JGB 10Y yield from MoF Japan
        jgb_info = self.fetch_jgb_10y_from_mof()
        jp_10y = jgb_info["rate_10y"]
        jgb_time = jgb_info["timestamp_utc"]

        # Insert JGB 10Y into market_ticks under both JGB10Y and ^JB10YR
        jgb_ticks = [
            {
                "symbol": "JGB10Y",
                "timestamp_utc": jgb_time,
                "price": jp_10y,
                "open": jp_10y,
                "high": jp_10y,
                "low": jp_10y,
                "volume": 0.0,
            },
            {
                "symbol": "^JB10YR",
                "timestamp_utc": jgb_time,
                "price": jp_10y,
                "open": jp_10y,
                "high": jp_10y,
                "low": jp_10y,
                "volume": 0.0,
            },
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO market_ticks
            (symbol, timestamp_utc, price, open, high, low, volume)
            VALUES (:symbol, :timestamp_utc, :price, :open, :high, :low, :volume)
            """,
            jgb_ticks,
        )
        total_ticks_inserted += len(jgb_ticks)

        # 3. Calculate and store US-Japan 10Y yield spread
        us_10y = latest_prices.get("^TNX")
        us_2y = self.fetch_fred_latest("DGS2")

        spread_10y: Optional[float] = None
        if us_10y is not None and jp_10y is not None:
            spread_10y = round(us_10y - jp_10y, 4)

        # Deterministic timestamp for yield spread: latest timestamp of ^TNX or JPY=X
        spread_timestamp = (
            latest_timestamps.get("^TNX")
            or latest_timestamps.get("JPY=X")
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
        )

        source_label = (
            "yfinance+mof_japan+fred" if us_2y is not None else "yfinance+mof_japan"
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO yield_spreads
            (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (spread_timestamp, us_2y, us_10y, jp_10y, spread_10y, source_label),
        )
        conn.commit()

        # Query total row counts in database
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM market_ticks")
        db_ticks_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM yield_spreads")
        db_spreads_count = cur.fetchone()[0]

        conn.close()

        summary = {
            "status": "SUCCESS",
            "total_ticks_inserted": total_ticks_inserted,
            "db_market_ticks_rows": db_ticks_count,
            "db_yield_spreads_rows": db_spreads_count,
            "latest_prices": latest_prices,
            "jgb_10y": jp_10y,
            "us_10y": us_10y,
            "spread_10y": spread_10y,
            "spread_timestamp": spread_timestamp,
            "ticker_summaries": ticker_summaries,
        }
        self.logger.info(
            f"Market ticks collection complete: {db_ticks_count} total ticks in DB, "
            f"10Y Spread: {spread_10y}% (US: {us_10y}%, JP: {jp_10y}%)"
        )
        return summary
