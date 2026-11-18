"""
Validation and verification script for Yahoo Finance market tickers and JGB 10Y fallback.
Task P1-02: Yahoo Finance Ticker Validation
"""

import csv
import json
from datetime import datetime, timezone
from io import StringIO
import requests
import yfinance as yf

TICKERS = [
    "JPY=X",   # USD/JPY exchange rate
    "^TNX",    # 10-Year US Treasury Note Yield
    "^JB10YR", # Japan 10-Year Government Bond Yield (Proxy)
    "^GSPC",   # S&P 500 Index
    "^N225",   # Nikkei 225 Index
    "^VIX",    # CBOE Volatility Index
    "GC=F",    # Gold Futures
]


def test_mof_jgb_fallback():
    """Fallback collector for JGB 10Y rate from Ministry of Finance (MoF) Japan."""
    url = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        resp.encoding = "cp932"
        reader = csv.reader(resp.text.splitlines())
        header = None
        last_row = None
        for row in reader:
            if not row:
                continue
            if "基準日" in row[0]:
                header = row
            elif header and row[0].startswith("R"):
                last_row = row

        if header and last_row:
            date_str = last_row[0]
            y10_idx = header.index("10年")
            rate_10y = float(last_row[y10_idx])
            return {
                "source": "Japan Ministry of Finance (mof.go.jp)",
                "url": url,
                "latest_date": date_str,
                "jgb_10y_yield": rate_10y,
                "status": "SUCCESS",
            }
    except Exception as exc:
        return {"status": "ERROR", "error": str(exc)}
    return {"status": "NOT_FOUND"}


def test_ticker(symbol: str):
    print(f"\n{'='*20} Testing {symbol} {'='*20}")
    ticker = yf.Ticker(symbol)

    # 1. Test 15m intraday
    hist_15m = ticker.history(period="2d", interval="15m")
    has_15m = not hist_15m.empty
    candles_15m = len(hist_15m)

    # 2. Test 1d fallback
    hist_1d = ticker.history(period="5d", interval="1d")
    has_1d = not hist_1d.empty
    candles_1d = len(hist_1d)

    # 3. Metadata & delay info
    info_meta = {}
    try:
        inf = ticker.info
        info_meta = {
            "quoteType": inf.get("quoteType"),
            "exchange": inf.get("exchange"),
            "delayedBy": inf.get("exchangeDataDelayedBy"),
            "marketState": inf.get("marketState"),
            "currency": inf.get("currency"),
            "shortName": inf.get("shortName"),
            "regularMarketPrice": inf.get("regularMarketPrice"),
        }
    except Exception as e:
        info_meta["error"] = str(e)

    # 4. Extract latest candle details
    latest_candle = {}
    chosen_interval = "15m" if has_15m else ("1d" if has_1d else "none")
    active_hist = hist_15m if has_15m else hist_1d

    if not active_hist.empty:
        last_row = active_hist.iloc[-1]
        last_dt = active_hist.index[-1]
        latest_candle = {
            "timestamp": str(last_dt),
            "open": float(last_row["Open"]),
            "high": float(last_row["High"]),
            "low": float(last_row["Low"]),
            "close": float(last_row["Close"]),
            "volume": float(last_row["Volume"]) if "Volume" in last_row else 0.0,
        }

    res = {
        "symbol": symbol,
        "available": has_15m or has_1d,
        "has_15m": has_15m,
        "count_15m": candles_15m,
        "has_1d": has_1d,
        "count_1d": candles_1d,
        "chosen_interval": chosen_interval,
        "latest_candle": latest_candle,
        "metadata": info_meta,
    }

    if symbol == "^JB10YR" and not (has_15m or has_1d):
        res["fallback_investigation"] = {
            "status": "DELISTED_ON_YAHOO",
            "alternative_source": test_mof_jgb_fallback(),
        }

    print(f"Availability: {'YES' if res['available'] else 'NO (HTTP 404 / Delisted)'}")
    print(f"15m Bars: {candles_15m} | 1d Bars: {candles_1d} | Selected: {chosen_interval}")
    print(f"Latest Candle: {latest_candle}")
    print(f"Metadata: {info_meta}")
    if "fallback_investigation" in res:
        print(f"Fallback: {res['fallback_investigation']}")

    return res


def main():
    print("Beginning validation of Yahoo Finance tickers for USD/JPY signal pipeline...")
    results = {}
    for sym in TICKERS:
        try:
            results[sym] = test_ticker(sym)
        except Exception as exc:
            print(f"Error testing {sym}: {exc}")
            results[sym] = {"symbol": sym, "available": False, "error": str(exc)}

    out_file = "yfinance_validation_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nAll validations complete. Results saved to {out_file}")


if __name__ == "__main__":
    main()
