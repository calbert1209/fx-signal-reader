"""
USD/JPY Signal Reader - Rolling Technical & Spread Momentum Indicators
Task P7-03: Trend Filter & Spread Momentum Indicators

Provides deterministic, point-in-time-safe rolling EMA, SMA, and spread
rate-of-change (RoC) helpers plus regime classification utilities.
"""

from __future__ import annotations

import math
from typing import Sequence

DEFAULT_EMA_PERIOD = 50
DEFAULT_SMA_PERIOD = 200

SPREAD_CONTRACTING_5D_THRESHOLD = -0.10
SPREAD_CONTRACTING_20D_THRESHOLD = -0.20
SPREAD_EXPANDING_5D_THRESHOLD = 0.10
SPREAD_EXPANDING_20D_THRESHOLD = 0.20


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
