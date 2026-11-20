"""
Market Ticks and Yield Spreads Collector for the FX Signal Reader (multi-pair).
Ingests:
  - Market tickers via yfinance (USD/JPY: JPY=X, ^TNX, ^GSPC, ^N225, ^VIX, GC=F;
    EUR/USD: EURUSD=X, ^TNX, ^STOXX50E, ^GSPC, ^VIX, GC=F)
  - USD/JPY: JGB 10-Year yield from the official Japan Ministry of Finance (MoF) CSV
  - EUR/USD: German 10-Year Bund yield (DE10Y) from the Deutsche Bundesbank / ECB
    public time-series APIs, falling back to FRED (IRLTLT01DEM156N) and then Yahoo
  - Computes the pair's 10Y sovereign yield spread (base yield - quote yield) and
    writes to SQLite tables.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yfinance as yf

from fx_signal_reader.collectors.base import BaseCollector
from fx_signal_reader.core import DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH
from fx_signal_reader.core.pair_profile import (
    DEFAULT_PAIR,
    PairProfile,
    get_pair_db_path,
    get_pair_profile,
)
from fx_signal_reader.core.spread_calculator import (
    compute_2y_yield_spread,
    compute_pair_spread,
    parse_de_bund_csv,
)


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

    # EUR/USD watchlist (German DE10Y is ingested separately, like JGB10Y for JPY).
    EURUSD_TICKERS = [
        "EURUSD=X",  # EUR/USD exchange rate
        "^TNX",  # 10-Year US Treasury Note Yield
        "^STOXX50E",  # Euro Stoxx 50 Index
        "^GSPC",  # S&P 500 Index
        "^VIX",  # CBOE Volatility Index
        "GC=F",  # Gold Futures
    ]

    MOF_JGB_CSV_URL = (
        "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
    )

    # German 10Y Bund yield sources (tried in this order by fetch_de_10y_bund).
    # 1. Deutsche Bundesbank SDMX REST API: daily Svensson-method yield of listed
    #    Federal securities, 10.0y residual maturity (no API key required).
    BUNDESBANK_DE10Y_URL = (
        "https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
        "D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
    )
    # 2. ECB Data Portal: euro-area AAA 10Y spot yield curve (Bund-dominated proxy).
    ECB_DE10Y_URL = (
        "https://data-api.ecb.europa.eu/service/data/"
        "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
    )
    # 3. FRED monthly long-term German government yield (needs FRED_API_KEY).
    FRED_DE10Y_SERIES = "IRLTLT01DEM156N"
    # 4. Yahoo Finance proxy tickers (percent yield), last resort.
    YAHOO_DE10Y_TICKERS = ["DE10YT=RR"]
    DE10Y_LOOKBACK_DAYS = 14

    def __init__(
        self,
        db_path: Path | str | None = None,
        schema_path: Path | str = DEFAULT_SCHEMA_PATH,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        jitter: bool = True,
        timeout: float = 12.0,
        fred_api_key: Optional[str] = None,
        pair: str | PairProfile = DEFAULT_PAIR,
    ) -> None:
        self.profile: PairProfile = get_pair_profile(pair)
        self.pair: str = self.profile.pair
        if db_path is None:
            db_path = (
                DEFAULT_DB_PATH
                if self.profile.pair == DEFAULT_PAIR
                else get_pair_db_path(self.profile)
            )
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

        rate_2y: Optional[float] = None
        if "2年" in header:
            y2_idx = header.index("2年")
            try:
                rate_2y = float(last_row[y2_idx].strip())
            except (ValueError, TypeError):
                rate_2y = None

        # Parse Reiwa era date: R8.11.17 -> 2026-11-17T00:00:00Z
        iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        m = re.match(r"R(\d+)\.(\d+)\.(\d+)", date_str)
        if m:
            reiwa_yr = int(m.group(1))
            gregorian_yr = 2018 + reiwa_yr  # R1 = 2019
            month = int(m.group(2))
            day = int(m.group(3))
            iso_date = f"{gregorian_yr:04d}-{month:02d}-{day:02d}T00:00:00Z"

        log_msg = f"Successfully parsed MoF JGB 10Y: {rate_10y}%"
        if rate_2y is not None:
            log_msg += f", 2Y: {rate_2y}%"
        log_msg += f" (date: {date_str} -> {iso_date})"
        self.logger.info(log_msg)
        return {
            "rate_10y": rate_10y,
            "rate_2y": rate_2y,
            "raw_date": date_str,
            "timestamp_utc": iso_date,
            "source": "Japan Ministry of Finance (mof.go.jp)",
        }

    def fetch_us_2y(
        self, return_candles: bool = False
    ) -> Tuple[Optional[float], Optional[str]] | Tuple[Optional[float], Optional[str], List[Dict[str, Any]]]:
        """
        Fetches the latest US 2-Year Treasury yield.
        First attempts FRED DGS2 if configured. If unavailable or None, automatically
        falls back to Yahoo Finance '2YY=F'.

        Returns:
            If return_candles is False: (rate, source_name)
            If return_candles is True: (rate, source_name, candles_list)
        """
        if self.fred_api_key:
            val = self.fetch_fred_latest("DGS2")
            if val is not None:
                self.logger.info(f"Fetched US 2Y yield from FRED DGS2: {val}%")
                return (val, "fred", []) if return_candles else (val, "fred")

        # Fallback to Yahoo Finance 2YY=F
        try:
            candles, _meta = self.fetch_ticker_data("2YY=F")
            if candles:
                latest = candles[-1]
                val = float(latest["price"])
                self.logger.info(f"Fetched US 2Y yield from Yahoo Finance 2YY=F: {val}%")
                return (val, "yahoo_2yy", candles) if return_candles else (val, "yahoo_2yy")
        except Exception as exc:
            self.logger.warning(f"Yahoo 2YY=F fallback fetch failed: {exc}")

        return (None, None, []) if return_candles else (None, None)

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

    @property
    def tickers(self) -> List[str]:
        """Yahoo watchlist for the configured pair (USD/JPY uses `TICKERS`)."""
        if self.profile.pair == DEFAULT_PAIR:
            return list(self.TICKERS)
        if self.profile.pair == "EUR/USD":
            return list(self.EURUSD_TICKERS)
        # Generic profile: spot, US 10Y, local equity benchmark, S&P, VIX, gold.
        ordered = [
            self.profile.spot_symbol,
            "^TNX",
            self.profile.equity_benchmark,
            "^GSPC",
            "^VIX",
            "GC=F",
        ]
        return list(dict.fromkeys(ordered))

    @property
    def foreign_yield_symbol(self) -> str:
        """Non-US 10Y yield symbol of the pair (JGB10Y for USD/JPY, DE10Y for EUR/USD)."""
        p = self.profile
        return p.quote_yield_symbol if p.is_base_usd else p.base_yield_symbol

    @staticmethod
    def _latest_from_series(series: Dict[str, float]) -> Optional[Tuple[str, float]]:
        if not series:
            return None
        d = max(series)
        return d, series[d]

    def fetch_de_10y_from_bundesbank(self) -> Dict[str, Any]:
        """Latest daily German 10Y yield from the Deutsche Bundesbank SDMX REST API."""
        start = (
            datetime.now(timezone.utc) - timedelta(days=self.DE10Y_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")
        self.logger.info(f"Fetching DE 10Y Bund yield from Bundesbank: {self.BUNDESBANK_DE10Y_URL}")
        resp = self.fetch_url(
            self.BUNDESBANK_DE10Y_URL,
            params={"format": "csv", "startPeriod": start},
        )
        resp.raise_for_status()
        latest = self._latest_from_series(parse_de_bund_csv(resp.text))
        if latest is None:
            raise ValueError("No observations found in Bundesbank DE 10Y response.")
        return self._de_10y_result(
            latest[1], latest[0], "Deutsche Bundesbank (api.statistiken.bundesbank.de)"
        )

    def fetch_de_10y_from_ecb(self) -> Dict[str, Any]:
        """Latest daily euro-area AAA 10Y yield (Bund proxy) from the ECB Data Portal."""
        self.logger.info(f"Fetching DE 10Y proxy from ECB: {self.ECB_DE10Y_URL}")
        resp = self.fetch_url(
            self.ECB_DE10Y_URL,
            params={"format": "csvdata", "lastNObservations": 10},
        )
        resp.raise_for_status()
        latest = self._latest_from_series(parse_de_bund_csv(resp.text))
        if latest is None:
            raise ValueError("No observations found in ECB DE 10Y response.")
        return self._de_10y_result(
            latest[1], latest[0], "ECB Data Portal (data-api.ecb.europa.eu)"
        )

    def fetch_fred_latest_observation(
        self, series_id: str
    ) -> Optional[Tuple[str, float]]:
        """Latest numeric FRED observation as (YYYY-MM-DD, value); None without API key."""
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
                if obs.get("value") not in (None, "", "."):
                    return str(obs["date"])[:10], float(obs["value"])
        except Exception as exc:
            self.logger.warning(f"FRED fetch failed for {series_id}: {exc}")
        return None

    def fetch_de_10y_from_fred(self) -> Dict[str, Any]:
        """German 10Y from FRED IRLTLT01DEM156N (monthly; uses FRED_API_KEY)."""
        obs = self.fetch_fred_latest_observation(self.FRED_DE10Y_SERIES)
        if obs is None:
            raise ValueError("FRED DE 10Y unavailable (no API key or no observations).")
        return self._de_10y_result(
            obs[1], obs[0], f"FRED ({self.FRED_DE10Y_SERIES}, monthly)"
        )

    def fetch_de_10y_from_yahoo(self) -> Dict[str, Any]:
        """Last-resort German 10Y yield proxy via Yahoo Finance tickers."""
        for symbol in self.YAHOO_DE10Y_TICKERS:
            try:
                candles, _meta = self.fetch_ticker_data(symbol)
            except Exception as exc:
                self.logger.warning(f"Yahoo DE 10Y proxy {symbol} failed: {exc}")
                continue
            if candles:
                last = candles[-1]
                return self._de_10y_result(
                    float(last["price"]),
                    last["timestamp_utc"][:10],
                    f"Yahoo Finance proxy ({symbol})",
                )
        raise ValueError("Yahoo DE 10Y proxy unavailable.")

    @staticmethod
    def _de_10y_result(rate: float, iso_day: str, source: str) -> Dict[str, Any]:
        return {
            "rate_10y": float(rate),
            "raw_date": iso_day,
            "timestamp_utc": f"{iso_day[:10]}T00:00:00Z",
            "source": source,
        }

    def fetch_de_10y_bund(self) -> Dict[str, Any]:
        """
        Fetches the German 10-Year Bund yield (DE10Y) in percentage points.

        Source order: Deutsche Bundesbank -> ECB Data Portal -> FRED
        (IRLTLT01DEM156N) -> Yahoo proxy. Returns the same shape as
        `fetch_jgb_10y_from_mof` (`rate_10y`, `raw_date`, `timestamp_utc` ISO-8601
        UTC, `source`). Raises ValueError if every source fails.
        """
        errors: List[str] = []
        for name, fn in (
            ("bundesbank", self.fetch_de_10y_from_bundesbank),
            ("ecb", self.fetch_de_10y_from_ecb),
            ("fred", self.fetch_de_10y_from_fred),
            ("yahoo", self.fetch_de_10y_from_yahoo),
        ):
            try:
                result = fn()
                self.logger.info(
                    f"Successfully parsed DE 10Y: {result['rate_10y']}% "
                    f"(date: {result['raw_date']}, source: {name})"
                )
                return result
            except Exception as exc:
                self.logger.warning(f"DE 10Y source '{name}' failed: {exc}")
                errors.append(f"{name}: {exc}")
        raise ValueError("All German 10Y Bund sources failed: " + "; ".join(errors))

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
        Collects market ticks for the pair's watchlist plus its foreign 10Y yield
        (JGB10Y for USD/JPY, DE10Y for EUR/USD), writes them to `market_ticks`
        and the base-minus-quote spread to `yield_spreads`.
        """
        conn = self.get_connection()
        total_ticks_inserted = 0
        latest_prices: Dict[str, float] = {}
        latest_timestamps: Dict[str, str] = {}
        ticker_summaries: Dict[str, Any] = {}

        # 1. Fetch and store yfinance market ticks
        for symbol in self.tickers:
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

        is_jpy_pair = self.profile.pair == DEFAULT_PAIR
        foreign_10y: Optional[float] = None
        foreign_source = ""
        jp_2y: Optional[float] = None
        if is_jpy_pair:
            # 2. Fetch JGB 10Y and 2Y yields from MoF Japan
            jgb_info = self.fetch_jgb_10y_from_mof()
            jp_10y = jgb_info["rate_10y"]
            jp_2y = jgb_info.get("rate_2y")
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
            if jp_2y is not None:
                jgb_ticks.append(
                    {
                        "symbol": "JGB2Y",
                        "timestamp_utc": jgb_time,
                        "price": jp_2y,
                        "open": jp_2y,
                        "high": jp_2y,
                        "low": jp_2y,
                        "volume": 0.0,
                    }
                )
            conn.executemany(
                """
                INSERT OR REPLACE INTO market_ticks
                (symbol, timestamp_utc, price, open, high, low, volume)
                VALUES (:symbol, :timestamp_utc, :price, :open, :high, :low, :volume)
                """,
                jgb_ticks,
            )
            total_ticks_inserted += len(jgb_ticks)
            foreign_10y = jp_10y
        else:
            # 2b. Fetch German 10Y Bund yield (DE10Y); a total outage must not
            # discard the Yahoo ticks already fetched, so spread stays NULL.
            try:
                de_info = self.fetch_de_10y_bund()
                foreign_10y = de_info["rate_10y"]
                foreign_source = de_info["source"]
                de_ticks = [
                    {
                        "symbol": self.foreign_yield_symbol,
                        "timestamp_utc": de_info["timestamp_utc"],
                        "price": foreign_10y,
                        "open": foreign_10y,
                        "high": foreign_10y,
                        "low": foreign_10y,
                        "volume": 0.0,
                    }
                ]
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO market_ticks
                    (symbol, timestamp_utc, price, open, high, low, volume)
                    VALUES (:symbol, :timestamp_utc, :price, :open, :high, :low, :volume)
                    """,
                    de_ticks,
                )
                total_ticks_inserted += len(de_ticks)
            except Exception as exc:
                self.logger.error(f"German 10Y Bund yield unavailable: {exc}")

        # 3. Calculate and store the pair's 10Y and 2Y yield spreads
        us_10y = latest_prices.get("^TNX")
        if is_jpy_pair:
            us_2y, us_2y_src, us_2y_candles = self.fetch_us_2y(return_candles=True)
            if us_2y_src == "yahoo_2yy" and us_2y_candles:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO market_ticks
                    (symbol, timestamp_utc, price, open, high, low, volume)
                    VALUES (:symbol, :timestamp_utc, :price, :open, :high, :low, :volume)
                    """,
                    us_2y_candles,
                )
                total_ticks_inserted += len(us_2y_candles)
                latest_prices["2YY=F"] = us_2y
        else:
            us_2y = self.fetch_fred_latest("DGS2")
            us_2y_src = "fred" if us_2y is not None else None
            us_2y_candles = []

        # Spread = base_yield - quote_yield. USD/JPY: US10Y - JGB10Y; EUR/USD: DE10Y - US10Y.
        spread_10y: Optional[float] = None
        if us_10y is not None and foreign_10y is not None:
            if self.profile.is_base_usd:
                spread_10y = compute_pair_spread(us_10y, foreign_10y)
            else:
                spread_10y = compute_pair_spread(foreign_10y, us_10y)

        # 2Y Spread: US 2Y - JP 2Y (for USD/JPY)
        spread_2y: Optional[float] = None
        if is_jpy_pair and us_2y is not None and jp_2y is not None:
            spread_2y = compute_2y_yield_spread(us_2y, jp_2y)

        # Deterministic timestamp for yield spread: latest timestamp of ^TNX or spot
        spread_timestamp = (
            latest_timestamps.get("^TNX")
            or latest_timestamps.get(self.profile.spot_symbol)
            or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
        )

        foreign_label = "mof_japan" if is_jpy_pair else "bundesbank_de10y"
        if us_2y_src == "fred":
            source_label = f"yfinance+{foreign_label}+fred"
        elif us_2y_src == "yahoo_2yy":
            source_label = f"yfinance+{foreign_label}+yahoo_2y"
        else:
            source_label = f"yfinance+{foreign_label}"

        # Insert into yield_spreads table with 2Y sovereign yields
        conn.execute(
            """
            INSERT OR REPLACE INTO yield_spreads
            (timestamp_utc, us_2y, jp_2y, spread_2y, us_10y, jp_10y, spread_10y, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (spread_timestamp, us_2y, jp_2y, spread_2y, us_10y, foreign_10y, spread_10y, source_label),
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
        }
        if is_jpy_pair:
            summary["jgb_10y"] = jp_10y
            if jp_2y is not None:
                summary["jgb_2y"] = jp_2y
        else:
            summary["pair"] = self.profile.pair
            summary["de_10y"] = foreign_10y
            summary["de_10y_source"] = foreign_source or None
        summary.update(
            {
                "us_10y": us_10y,
                "spread_10y": spread_10y,
                "spread_timestamp": spread_timestamp,
                "ticker_summaries": ticker_summaries,
            }
        )
        if us_2y is not None:
            summary["us_2y"] = us_2y
        if spread_2y is not None:
            summary["spread_2y"] = spread_2y

        foreign_name = "JP" if is_jpy_pair else "DE"
        spread_msg = f"10Y Spread: {spread_10y}% (US: {us_10y}%, {foreign_name}: {foreign_10y}%)"
        if spread_2y is not None:
            spread_msg += f", 2Y Spread: {spread_2y}% (US: {us_2y}%, JP: {jp_2y}%)"
        self.logger.info(
            f"Market ticks collection complete: {db_ticks_count} total ticks in DB, {spread_msg}"
        )
        return summary
