"""
USD/JPY Signal Reader - Rolling Technical & Spread Momentum Indicators
Task P7-03: Trend Filter & Spread Momentum Indicators

Provides deterministic, point-in-time-safe rolling EMA, SMA, and spread
rate-of-change (RoC) helpers plus regime classification utilities.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

DEFAULT_EMA_PERIOD = 50
DEFAULT_SMA_PERIOD = 200
DEFAULT_ATR_PERIOD = 14

SPREAD_CONTRACTING_5D_THRESHOLD = -0.10
SPREAD_CONTRACTING_20D_THRESHOLD = -0.20
SPREAD_EXPANDING_5D_THRESHOLD = 0.10
SPREAD_EXPANDING_20D_THRESHOLD = 0.20

DEFAULT_COMMODITY_ROC_PERIOD = 5
COMMODITY_ROC_SHORT_PERIOD = DEFAULT_COMMODITY_ROC_PERIOD
COMMODITY_ROC_LONG_PERIOD = 20
COMMODITY_EMA_PERIOD = 50
COMMODITY_SURGE_THRESHOLD_PCT = 3.0
COMMODITY_PLUNGE_THRESHOLD_PCT = -3.0


def _validate_positive_period(period: int, name: str) -> int:
    """Validate that an integer window or lookback period is strictly positive and not bool."""
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError(f"{name} must be a positive integer, got {period!r}.")
    return period


def _coerce_finite_float_sequence(values: Sequence[float]) -> list[float]:
    """Validate and convert a sequence of numeric values into finite Python floats."""
    out: list[float] = []
    for idx, val in enumerate(values):
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise ValueError(f"Indicator input at index {idx} must be numeric, got {val!r}.")
        numeric = float(val)
        if not math.isfinite(numeric):
            raise ValueError(f"Indicator input at index {idx} must be finite, got {val!r}.")
        out.append(numeric)
    return out


def compute_ema_series(
    values: Sequence[float],
    period: int = DEFAULT_EMA_PERIOD,
) -> list[float | None]:
    """Compute a standard SMA-seeded Exponential Moving Average series rounded to 6 decimals."""
    win = _validate_positive_period(period, "period")
    series = _coerce_finite_float_sequence(values)
    n = len(series)
    if n < win:
        return [None] * n

    out: list[float | None] = [None] * (win - 1)
    prev = sum(series[:win]) / win
    out.append(round(prev, 6))

    alpha = 2.0 / (win + 1.0)
    one_minus_alpha = 1.0 - alpha
    for val in series[win:]:
        prev = alpha * val + one_minus_alpha * prev
        out.append(round(prev, 6))
    return out


def compute_latest_ema(
    values: Sequence[float],
    period: int = DEFAULT_EMA_PERIOD,
) -> float | None:
    """Return the most recent Exponential Moving Average value, or None if insufficient bars."""
    series = compute_ema_series(values, period=period)
    return series[-1] if series else None


def compute_sma_series(
    values: Sequence[float],
    period: int = DEFAULT_SMA_PERIOD,
) -> list[float | None]:
    """Compute a rolling Simple Moving Average series rounded to 6 decimals."""
    win = _validate_positive_period(period, "period")
    series = _coerce_finite_float_sequence(values)
    n = len(series)
    if n < win:
        return [None] * n

    out: list[float | None] = [None] * (win - 1)
    running_sum = sum(series[:win])
    out.append(round(running_sum / win, 6))

    for idx in range(win, n):
        running_sum += series[idx] - series[idx - win]
        out.append(round(running_sum / win, 6))
    return out


def compute_latest_sma(
    values: Sequence[float],
    period: int = DEFAULT_SMA_PERIOD,
) -> float | None:
    """Return the most recent Simple Moving Average value, or None if insufficient bars."""
    win = _validate_positive_period(period, "period")
    series = _coerce_finite_float_sequence(values)
    if len(series) < win:
        return None
    return round(sum(series[-win:]) / win, 6)


def compute_rate_of_change(
    values: Sequence[float],
    periods_back: int,
) -> float | None:
    """Compute point difference `values[-1] - values[-1 - periods_back]` rounded to 6 decimals."""
    lookback = _validate_positive_period(periods_back, "periods_back")
    series = _coerce_finite_float_sequence(values)
    if len(series) <= lookback:
        return None
    return round(series[-1] - series[-1 - lookback], 6)


def classify_market_trend(
    spot_price: float | None,
    ema_50: float | None,
    sma_200: float | None = None,
) -> str:
    """Classify spot trend as BULLISH, BEARISH, or SIDEWAYS relative to EMA-50 and SMA-200."""
    if spot_price is None or ema_50 is None:
        return "SIDEWAYS"
    if spot_price > ema_50 and (sma_200 is None or spot_price > sma_200):
        return "BULLISH"
    if spot_price < ema_50 and (sma_200 is None or spot_price < sma_200):
        return "BEARISH"
    return "SIDEWAYS"


def classify_spread_momentum(
    spread_5d_change: float | None,
    spread_20d_change: float | None,
) -> str:
    """Classify 10Y yield spread trajectory as CONTRACTING, EXPANDING, or STABLE."""
    if (
        spread_5d_change is not None
        and spread_5d_change < SPREAD_CONTRACTING_5D_THRESHOLD
    ) or (
        spread_20d_change is not None
        and spread_20d_change < SPREAD_CONTRACTING_20D_THRESHOLD
    ):
        return "CONTRACTING"
    if (
        spread_5d_change is not None
        and spread_5d_change > SPREAD_EXPANDING_5D_THRESHOLD
    ) or (
        spread_20d_change is not None
        and spread_20d_change > SPREAD_EXPANDING_20D_THRESHOLD
    ):
        return "EXPANDING"
    return "STABLE"


def compute_true_ranges(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> list[float]:
    """Compute True Range sequence for OHLC bars with strict finiteness and high >= low validation."""
    h_vals = _coerce_finite_float_sequence(highs)
    l_vals = _coerce_finite_float_sequence(lows)
    c_vals = _coerce_finite_float_sequence(closes)
    if not (len(h_vals) == len(l_vals) == len(c_vals)):
        raise ValueError(
            f"highs ({len(h_vals)}), lows ({len(l_vals)}), and closes ({len(c_vals)}) must have equal length."
        )
    if not c_vals:
        return []

    out: list[float] = []
    for idx, (h, l) in enumerate(zip(h_vals, l_vals)):
        if h < l:
            raise ValueError(f"Bar high ({h}) cannot be less than low ({l}) at index {idx}.")
        if idx == 0:
            tr = h - l
        else:
            prev_c = c_vals[idx - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        out.append(round(tr, 6))
    return out


def compute_atr_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = DEFAULT_ATR_PERIOD,
) -> list[float | None]:
    """Compute rolling Average True Range (ATR) series using simple mean over `period` bars."""
    win = _validate_positive_period(period, "period")
    trs = compute_true_ranges(highs, lows, closes)
    return compute_sma_series(trs, period=win)


def compute_latest_atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = DEFAULT_ATR_PERIOD,
) -> float | None:
    """Return the latest ATR value, or None if fewer than `period` bars exist."""
    win = _validate_positive_period(period, "period")
    trs = compute_true_ranges(highs, lows, closes)
    if len(trs) < win:
        return None
    return round(sum(trs[-win:]) / win, 6)


def compute_percentage_rate_of_change(
    values: Sequence[float],
    periods_back: int,
) -> float | None:
    """Compute percentage change `((values[-1] - values[-1 - periods_back]) / values[-1 - periods_back]) * 100.0` rounded to 4 decimals."""
    lookback = _validate_positive_period(periods_back, "periods_back")
    series = _coerce_finite_float_sequence(values)
    if len(series) <= lookback:
        return None
    past = series[-1 - lookback]
    if past <= 0.0:
        return None
    curr = series[-1]
    return round(((curr - past) / past) * 100.0, 4)


def classify_commodity_regime(
    current_price: float | None,
    roc_5d: float | None,
    ema_50: float | None,
    surge_threshold: float = COMMODITY_SURGE_THRESHOLD_PCT,
    plunge_threshold: float = COMMODITY_PLUNGE_THRESHOLD_PCT,
) -> str:
    """
    Classify terms-of-trade commodity momentum regime as SURGING, PLUNGING, or STABLE.

    - PLUNGING: 5d RoC < -3.0% (Positive terms-of-trade shock for Japan -> JPY supportive).
    - SURGING: 5d RoC > +3.0% or current_price > ema_50 (Negative terms-of-trade shock for Japan -> USD/JPY tailwind).
    - STABLE: Neutral terms-of-trade momentum.
    """
    if roc_5d is not None and roc_5d < plunge_threshold:
        return "PLUNGING"
    if (roc_5d is not None and roc_5d > surge_threshold) or (
        current_price is not None and ema_50 is not None and current_price > ema_50
    ):
        return "SURGING"
    return "STABLE"


def compute_commodity_momentum(
    prices: Sequence[float],
    period: int = DEFAULT_COMMODITY_ROC_PERIOD,
) -> dict[str, Any]:
    """
    Compute commodity (e.g. WTI crude oil CL=F) terms-of-trade momentum metrics.

    Computes:
      - Latest price
      - Short-term percentage rate-of-change (period-day RoC, default 5-day)
      - Long-term percentage rate-of-change (20-day RoC)
      - 50-day EMA and price comparison
      - Terms-of-trade regime classification ('SURGING', 'PLUNGING', 'STABLE')

    Handles empty sequences, insufficient bars, and edge cases (zeros, non-positive bases).
    Raises ValueError on non-finite values or invalid lookback periods.
    """
    lookback = _validate_positive_period(period, "period")
    series = _coerce_finite_float_sequence(prices)
    if not series:
        return {
            "current_price": None,
            "price": None,
            "roc_5d": None,
            "roc_5d_pct": None,
            "roc_20d": None,
            "roc_20d_pct": None,
            "ema_50": None,
            "regime": "STABLE",
        }

    current_price = series[-1]
    roc_short = compute_percentage_rate_of_change(series, periods_back=lookback)
    roc_20d = compute_percentage_rate_of_change(series, periods_back=COMMODITY_ROC_LONG_PERIOD)
    ema_50 = compute_latest_ema(series, period=COMMODITY_EMA_PERIOD)

    # 5d RoC used for regime classification
    roc_5d = roc_short if lookback == 5 else compute_percentage_rate_of_change(series, periods_back=5)

    regime = classify_commodity_regime(
        current_price=current_price,
        roc_5d=roc_5d,
        ema_50=ema_50,
    )

    result: dict[str, Any] = {
        "current_price": current_price,
        "price": current_price,
        "roc_5d": roc_5d,
        "roc_5d_pct": roc_5d,
        "roc_20d": roc_20d,
        "roc_20d_pct": roc_20d,
        "ema_50": ema_50,
        "regime": regime,
    }
    if lookback != 5:
        result[f"roc_{lookback}d"] = roc_short
        result[f"roc_{lookback}d_pct"] = roc_short

    return result
