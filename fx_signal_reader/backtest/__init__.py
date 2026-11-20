"""Historical backtesting schemas, bracket simulator, and backfill utilities."""

from fx_signal_reader.backtest.backfill import (
    DEFAULT_BACKTEST_DB_PATH,
    backfill_historical_data,
    backfill_jgb_yields,
    backfill_market_data,
    backfill_market_ticks,
    backfill_yield_spreads,
)
from fx_signal_reader.backtest.backfill_calendar import backfill_calendar
from fx_signal_reader.backtest.backfill_intervention import (
    CURATED_INTERVENTION_HEADLINES,
    backfill_intervention_headlines,
)
from fx_signal_reader.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestReport,
    ExitReason,
    SimulatedTrade,
)
from fx_signal_reader.backtest.simulator import (
    compute_backtest_metrics,
    run_backtest,
    simulate_trade_resolution,
)

__all__ = [
    "BacktestConfig",
    "BacktestMetrics",
    "BacktestReport",
    "CURATED_INTERVENTION_HEADLINES",
    "DEFAULT_BACKTEST_DB_PATH",
    "ExitReason",
    "SimulatedTrade",
    "backfill_calendar",
    "backfill_historical_data",
    "backfill_intervention_headlines",
    "backfill_jgb_yields",
    "backfill_market_data",
    "backfill_market_ticks",
    "backfill_yield_spreads",
    "compute_backtest_metrics",
    "run_backtest",
    "simulate_trade_resolution",
]
