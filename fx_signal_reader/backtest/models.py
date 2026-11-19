"""
USD/JPY Signal Reader - Backtest Pydantic v2 Schemas
Task P6-03: Backtest Schemas & Bracket Simulator

Defines validated Pydantic v2 configuration, simulated trade, aggregate metrics,
and report schemas for historical USD/JPY signal backtesting.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from fx_signal_reader.agent.state import RiskRegime, TradeAction, YieldBias
from fx_signal_reader.core.normalizer import normalize_datetime_utc


def _reject_bool(value: Any, field_name: str) -> Any:
    """Reject boolean inputs before Pydantic coerces them into ints or floats."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} cannot be a boolean.")
    return value


def _normalize_utc(value: Any, *, allow_none: bool) -> str | None:
    """Strictly normalize str/date/datetime values to YYYY-MM-DDTHH:MM:SSZ."""
    if value is None:
        if allow_none:
            return None
        raise ValueError("Timestamp cannot be None.")
    if isinstance(value, bool) or not isinstance(value, (str, date)):
        raise ValueError("Timestamp must be an ISO-8601 string, date, or datetime.")
    return normalize_datetime_utc(value, fallback_now=False)


def _validate_json_safe_value(value: Any) -> Any:
    """Recursively ensure nested dicts/lists contain only finite floats and JSON-safe types."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite float (NaN or Inf) is not allowed.")
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"Dictionary keys must be strings, got {type(key).__name__}.")
            _validate_json_safe_value(item)
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_safe_value(item)
        return value
    return value


class ExitReason(str, Enum):
    """Reason a simulated bracket trade closed."""

    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    BREAKEVEN = "BREAKEVEN"
    TIME_STOP = "TIME_STOP"
    END_OF_DATA = "END_OF_DATA"


class BacktestConfig(BaseModel):
    """Configuration parameters controlling point-in-time replay and bracket simulation."""

    model_config = ConfigDict(allow_inf_nan=False)

    start_utc: str | None = None
    end_utc: str | None = None
    step_hours: int = Field(default=4, ge=0)
    max_hold_hours: int = Field(default=120, gt=0)
    spread_pips: float = Field(default=1.5, ge=0.0)
    allow_overlapping_trades: bool = False
    conservative_tie_break: bool = True
    mof_lookback_hours: int = Field(default=48, ge=0)
    initial_capital_usd: float = Field(default=10000.0, gt=0.0)
    breakeven_trigger_r: float | None = Field(default=None, gt=0.0)
    sl_pips: float = Field(default=75.0, gt=0.0)
    tp_pips: float = Field(default=150.0, gt=0.0)
    use_atr: bool = False
    allowed_regimes: list[RiskRegime] = Field(
        default_factory=lambda: [RiskRegime.RISK_ON, RiskRegime.TRANSITIONAL]
    )

    @field_validator("start_utc", "end_utc", mode="before")
    @classmethod
    def _validate_timestamps(cls, value: Any) -> str | None:
        return _normalize_utc(value, allow_none=True)

    @field_validator(
        "step_hours",
        "max_hold_hours",
        "mof_lookback_hours",
        "spread_pips",
        "initial_capital_usd",
        "breakeven_trigger_r",
        "sl_pips",
        "tp_pips",
        mode="before",
    )
    @classmethod
    def _validate_non_bool_numbers(cls, value: Any, info: ValidationInfo) -> Any:
        return _reject_bool(value, info.field_name or "numeric_field")

    @field_validator("allowed_regimes", mode="before")
    @classmethod
    def _validate_allowed_regimes(cls, value: Any) -> list[RiskRegime]:
        if isinstance(value, str):
            raw_items: list[Any] = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raise ValueError(
                "allowed_regimes must be a comma-separated string or sequence of RiskRegime values."
            )
        if not raw_items:
            raise ValueError("allowed_regimes cannot be empty.")
        normalized: list[RiskRegime] = []
        seen: set[RiskRegime] = set()
        for item in raw_items:
            regime = item if isinstance(item, RiskRegime) else RiskRegime(str(item).strip().upper())
            if regime not in seen:
                seen.add(regime)
                normalized.append(regime)
        return normalized

    @model_validator(mode="after")
    def _validate_date_order(self) -> BacktestConfig:
        if (
            self.start_utc is not None
            and self.end_utc is not None
            and self.start_utc > self.end_utc
        ):
            raise ValueError(
                f"start_utc ({self.start_utc}) must be <= end_utc ({self.end_utc})."
            )
        return self


class SimulatedTrade(BaseModel):
    """Resolved simulated bracket trade record with spread-adjusted PnL and R-multiple."""

    model_config = ConfigDict(allow_inf_nan=False)

    trade_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    entry_time_utc: str
    exit_time_utc: str
    action: TradeAction
    position_size_units: int = Field(gt=0)
    entry_price: float = Field(gt=0.0)
    stop_loss: float = Field(gt=0.0)
    take_profit: float = Field(gt=0.0)
    exit_price: float = Field(gt=0.0)
    exit_reason: ExitReason
    pnl_pips: float
    pnl_jpy: float
    pnl_usd: float
    r_multiple: float
    macro_bias: YieldBias
    risk_regime: RiskRegime
    intervention_risk_score: float = Field(ge=0.0, le=100.0)
    bars_held: int = Field(ge=0)

    @field_validator("trade_id", "proposal_id")
    @classmethod
    def _validate_non_blank_id(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name or 'id'} must not be blank.")
        return value

    @field_validator("entry_time_utc", "exit_time_utc", mode="before")
    @classmethod
    def _validate_timestamps(cls, value: Any) -> str:
        normalized = _normalize_utc(value, allow_none=False)
        assert normalized is not None
        return normalized

    @field_validator("action")
    @classmethod
    def _validate_actionable(cls, value: TradeAction) -> TradeAction:
        if value == TradeAction.HOLD:
            raise ValueError("SimulatedTrade action cannot be HOLD.")
        return value

    @field_validator(
        "position_size_units",
        "bars_held",
        "entry_price",
        "stop_loss",
        "take_profit",
        "exit_price",
        "pnl_pips",
        "pnl_jpy",
        "pnl_usd",
        "r_multiple",
        "intervention_risk_score",
        mode="before",
    )
    @classmethod
    def _validate_non_bool_numbers(cls, value: Any, info: ValidationInfo) -> Any:
        return _reject_bool(value, info.field_name or "numeric_field")

    @model_validator(mode="after")
    def _validate_exit_after_entry(self) -> SimulatedTrade:
        if self.exit_time_utc < self.entry_time_utc:
            raise ValueError(
                f"exit_time_utc ({self.exit_time_utc}) must be >= entry_time_utc ({self.entry_time_utc})."
            )
        return self


class BacktestMetrics(BaseModel):
    """Aggregate portfolio performance metrics and regime/bias/coverage diagnostics."""

    model_config = ConfigDict(allow_inf_nan=False)

    total_steps: int = Field(default=0, ge=0)
    total_signals_actionable: int = Field(default=0, ge=0)
    total_holds: int = Field(default=0, ge=0)
    total_vetoes: int = Field(default=0, ge=0)
    trades_executed: int = Field(default=0, ge=0)
    trades_skipped_overlap: int = Field(default=0, ge=0)
    wins: int = Field(default=0, ge=0)
    losses: int = Field(default=0, ge=0)
    win_rate_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    gross_profit_usd: float = Field(default=0.0, ge=0.0)
    gross_loss_usd: float = Field(default=0.0, ge=0.0)
    net_pnl_usd: float = 0.0
    return_pct: float = 0.0
    profit_factor: float | None = Field(default=None, ge=0.0)
    expectancy_usd: float = 0.0
    avg_r_multiple: float = 0.0
    max_drawdown_usd: float = Field(default=0.0, ge=0.0)
    max_drawdown_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    sharpe_ratio: float | None = None
    breakdown_by_regime: dict[str, dict[str, Any]] = Field(default_factory=dict)
    breakdown_by_bias: dict[str, dict[str, Any]] = Field(default_factory=dict)
    data_coverage: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "total_steps",
        "total_signals_actionable",
        "total_holds",
        "total_vetoes",
        "trades_executed",
        "trades_skipped_overlap",
        "wins",
        "losses",
        "win_rate_pct",
        "gross_profit_usd",
        "gross_loss_usd",
        "net_pnl_usd",
        "return_pct",
        "profit_factor",
        "expectancy_usd",
        "avg_r_multiple",
        "max_drawdown_usd",
        "max_drawdown_pct",
        "sharpe_ratio",
        mode="before",
    )
    @classmethod
    def _validate_non_bool_numbers(cls, value: Any, info: ValidationInfo) -> Any:
        return _reject_bool(value, info.field_name or "numeric_field")

    @field_validator("breakdown_by_regime", "breakdown_by_bias", "data_coverage")
    @classmethod
    def _validate_nested_json_safety(cls, value: Any) -> Any:
        return _validate_json_safe_value(value)


class BacktestReport(BaseModel):
    """Top-level serializable backtest output combining config, metrics, equity curve, and trades."""

    model_config = ConfigDict(allow_inf_nan=False)

    config: BacktestConfig
    metrics: BacktestMetrics
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    trades: list[SimulatedTrade] = Field(default_factory=list)

    @field_validator("equity_curve")
    @classmethod
    def _validate_equity_curve_json_safety(cls, value: Any) -> Any:
        return _validate_json_safe_value(value)


__all__ = [
    "BacktestConfig",
    "BacktestMetrics",
    "BacktestReport",
    "ExitReason",
    "SimulatedTrade",
]
