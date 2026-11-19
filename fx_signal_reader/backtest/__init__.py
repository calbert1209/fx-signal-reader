"""Historical backtesting schemas, bracket simulator, and backfill utilities."""

from fx_signal_reader.backtest.backfill import (
    DEFAULT_BACKTEST_DB_PATH,
    backfill_historical_data,
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
    "DEFAULT_BACKTEST_DB_PATH",
    "ExitReason",
    "SimulatedTrade",
    "backfill_historical_data",
    "compute_backtest_metrics",
    "run_backtest",
    "simulate_trade_resolution",
]
