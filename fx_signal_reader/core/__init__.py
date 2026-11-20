"""Core normalization, yield-spread calculation, and SQLite schema defaults."""

from pathlib import Path

from fx_signal_reader.core.gotobi import (
    GotobiSessionInfo,
    PHASE_POST_FIX,
    PHASE_PRE_FIX,
    PHASE_STANDARD,
    evaluate_gotobi_session,
    get_gotobi_dates_for_month,
    get_japanese_holidays,
    get_preceding_business_day,
    get_target_gotobi_days,
    is_gotobi_day,
    is_japanese_business_day,
    is_japanese_holiday,
)
from fx_signal_reader.core.indicators import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_EMA_PERIOD,
    DEFAULT_SMA_PERIOD,
    SPREAD_CONTRACTING_5D_THRESHOLD,
    SPREAD_CONTRACTING_20D_THRESHOLD,
    SPREAD_EXPANDING_5D_THRESHOLD,
    SPREAD_EXPANDING_20D_THRESHOLD,
    classify_market_trend,
    classify_spread_momentum,
    compute_atr_series,
    compute_ema_series,
    compute_latest_atr,
    compute_latest_ema,
    compute_latest_sma,
    compute_rate_of_change,
    compute_sma_series,
    compute_true_ranges,
)

from fx_signal_reader.core.pair_profile import (
    DEFAULT_PAIR,
    PAIR_PROFILES,
    PairProfile,
    RiskPolicyType,
    get_pair_db_path,
    get_pair_profile,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "usdjpy_signals.db"
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

__all__ = [
    "DEFAULT_PAIR",
    "PAIR_PROFILES",
    "PairProfile",
    "RiskPolicyType",
    "get_pair_db_path",
    "get_pair_profile",
    "GotobiSessionInfo",
    "PHASE_POST_FIX",
    "PHASE_PRE_FIX",
    "PHASE_STANDARD",
    "evaluate_gotobi_session",
    "get_gotobi_dates_for_month",
    "get_japanese_holidays",
    "get_preceding_business_day",
    "get_target_gotobi_days",
    "is_gotobi_day",
    "is_japanese_business_day",
    "is_japanese_holiday",
    "DEFAULT_ATR_PERIOD",
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
    "compute_atr_series",
    "compute_ema_series",
    "compute_latest_atr",
    "compute_latest_ema",
    "compute_latest_sma",
    "compute_rate_of_change",
    "compute_sma_series",
    "compute_true_ranges",
]
