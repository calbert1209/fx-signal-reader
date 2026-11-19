"""Core normalization, yield-spread calculation, and SQLite schema defaults."""

from pathlib import Path

from fx_signal_reader.core.indicators import (
    DEFAULT_EMA_PERIOD,
    DEFAULT_SMA_PERIOD,
    SPREAD_CONTRACTING_5D_THRESHOLD,
    SPREAD_CONTRACTING_20D_THRESHOLD,
    SPREAD_EXPANDING_5D_THRESHOLD,
    SPREAD_EXPANDING_20D_THRESHOLD,
    classify_market_trend,
    classify_spread_momentum,
    compute_ema_series,
    compute_latest_ema,
    compute_latest_sma,
    compute_rate_of_change,
    compute_sma_series,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "usdjpy_signals.db"
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_EMA_PERIOD",
    "DEFAULT_SCHEMA_PATH",
    "DEFAULT_SMA_PERIOD",
    "PROJECT_ROOT",
    "SPREAD_CONTRACTING_5D_THRESHOLD",
    "SPREAD_CONTRACTING_20D_THRESHOLD",
    "SPREAD_EXPANDING_5D_THRESHOLD",
    "SPREAD_EXPANDING_20D_THRESHOLD",
    "classify_market_trend",
    "classify_spread_momentum",
    "compute_ema_series",
    "compute_latest_ema",
    "compute_latest_sma",
    "compute_rate_of_change",
    "compute_sma_series",
]
