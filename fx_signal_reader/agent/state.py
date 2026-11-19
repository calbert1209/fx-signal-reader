"""
USD/JPY Signal Reader - LangGraph State & Structured Output Models
Task P3-03: LangGraph State Definition

Defines Pydantic v2 schemas for structured agent outputs (Macro Analyst,
Risk Committee, Trade Proposal), the shared LangGraph `AgentState` TypedDict
with `operator.add` reducer support on `audit_log`, and state helper utilities.
"""

from enum import Enum
import operator
from typing import Annotated, Any, Mapping, TypedDict
import uuid

from pydantic import BaseModel, Field

from fx_signal_reader.core.normalizer import normalize_datetime_utc


class YieldBias(str, Enum):
    """Directional USD/JPY bias implied by sovereign yield spreads and policy."""

    BULLISH_USD = "BULLISH_USD"
    BEARISH_USD = "BEARISH_USD"
    NEUTRAL = "NEUTRAL"


class MacroAssessment(BaseModel):
    """Structured output produced by the Macro Analyst node."""

    yield_bias: YieldBias
    us_10y: float | None = None
    jp_10y: float | None = None
    spread_10y: float | None = None
    spread_bps: float | None = None
    spread_5d_change: float | None = None
    spread_20d_change: float | None = None
    spread_momentum: str | None = None
    upcoming_high_impact_events: list[dict[str, Any]] = Field(default_factory=list)
    policy_divergence_summary: str
    confidence: float = Field(ge=0.0, le=1.0)


class RiskRegime(str, Enum):
    """Cross-asset global risk sentiment classification."""

    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    TRANSITIONAL = "TRANSITIONAL"


class InterventionAlertLevel(str, Enum):
    """MoF/BoJ verbal and physical FX intervention alert severity."""

    NONE = "NONE"
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class RiskAssessment(BaseModel):
    """Structured output produced by the Risk Committee node."""

    risk_regime: RiskRegime
    vix_level: float | None = None
    sp500_price: float | None = None
    nikkei_price: float | None = None
    gold_price: float | None = None
    intervention_risk_score: float = Field(ge=0.0, le=100.0)
    intervention_alert_level: InterventionAlertLevel
    highest_mof_tier: int = Field(ge=0, le=4)
    flagged_headlines: list[dict[str, Any]] = Field(default_factory=list)
    ema_50: float | None = None
    sma_200: float | None = None
    market_trend: str | None = None
    veto_trade: bool = False
    risk_notes: str


class TradeAction(str, Enum):
    """Proposed directional trade action for USD/JPY."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class ApprovalStatus(str, Enum):
    """Human-in-the-loop approval gate status."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NOT_REQUIRED = "NOT_REQUIRED"


class TradeProposal(BaseModel):
    """Structured USD/JPY trade proposal routed to the human approval gate."""

    proposal_id: str
    timestamp_utc: str
    pair: str = "USD/JPY"
    action: TradeAction
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward_ratio: float | None = Field(default=None, ge=0.0)
    position_size_units: int = Field(default=0, ge=0)
    rationale: str
    macro_bias: YieldBias
    risk_regime: RiskRegime
    intervention_risk_score: float = Field(ge=0.0, le=100.0)


class AgentState(TypedDict):
    """Shared LangGraph workflow state passed between specialist and execution nodes."""

    run_id: str
    timestamp_utc: str
    spot_price: float | None
    yield_bias: str
    macro_assessment: dict[str, Any] | None
    risk_regime: str
    intervention_risk_score: float
    risk_assessment: dict[str, Any] | None
    trade_proposal: dict[str, Any] | None
    approval_status: str
    reviewer_notes: str | None
    execution_receipt: dict[str, Any] | None
    audit_log: Annotated[list[str], operator.add]
    account_capital_usd: float | None
    risk_fraction: float | None


def create_initial_state(
    run_id: str | None = None,
    timestamp_utc: str | None = None,
    account_capital_usd: float | None = None,
    risk_fraction: float | None = None,
) -> AgentState:
    """
    Create a normalized initial `AgentState` dictionary for a LangGraph pipeline run.

    Args:
        run_id: Optional run identifier; defaults to a generated UUIDv4 string.
        timestamp_utc: Optional run timestamp (normalized to strict ISO-8601 UTC via
            `normalizer.normalize_datetime_utc`); defaults to current UTC time.
        account_capital_usd: Optional total account equity in USD (defaults to 10,000.0).
        risk_fraction: Optional risk fraction per trade (defaults to 0.01 / 1.0%).

    Returns:
        Initialized `AgentState` dictionary with safe neutral defaults.
    """
    resolved_run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else str(uuid.uuid4())
    resolved_timestamp = (
        normalize_datetime_utc(None, fallback_now=True)
        if timestamp_utc is None
        else normalize_datetime_utc(timestamp_utc, fallback_now=False)
    )

    return AgentState(
        run_id=resolved_run_id,
        timestamp_utc=resolved_timestamp,
        spot_price=None,
        yield_bias=YieldBias.NEUTRAL.value,
        macro_assessment=None,
        risk_regime=RiskRegime.TRANSITIONAL.value,
        intervention_risk_score=0.0,
        risk_assessment=None,
        trade_proposal=None,
        approval_status=ApprovalStatus.PENDING.value,
        reviewer_notes=None,
        execution_receipt=None,
        audit_log=[],
        account_capital_usd=10000.0 if account_capital_usd is None else float(account_capital_usd),
        risk_fraction=0.01 if risk_fraction is None else float(risk_fraction),
    )


def validate_state_models(state: AgentState) -> dict[str, BaseModel | None]:
    """
    Validate embedded serialized assessment and proposal dicts inside `AgentState`
    against their corresponding Pydantic v2 models.

    Args:
        state: Shared `AgentState` mapping containing optional serialized dicts for
            `macro_assessment`, `risk_assessment`, and `trade_proposal`.

    Returns:
        Mapping of `"macro_assessment"`, `"risk_assessment"`, and `"trade_proposal"`
        to validated Pydantic model instances or `None` if unset.

    Raises:
        TypeError: If `state` is not a mapping.
        pydantic.ValidationError: If any non-None payload fails schema validation.
    """
    if not isinstance(state, Mapping):
        raise TypeError(f"Expected state mapping, got {type(state).__name__}")

    model_specs: dict[str, type[BaseModel]] = {
        "macro_assessment": MacroAssessment,
        "risk_assessment": RiskAssessment,
        "trade_proposal": TradeProposal,
    }
    return {
        field_name: (
            model_cls.model_validate(state.get(field_name))
            if state.get(field_name) is not None
            else None
        )
        for field_name, model_cls in model_specs.items()
    }


__all__ = [
    "AgentState",
    "ApprovalStatus",
    "InterventionAlertLevel",
    "MacroAssessment",
    "RiskAssessment",
    "RiskRegime",
    "TradeAction",
    "TradeProposal",
    "YieldBias",
    "create_initial_state",
    "validate_state_models",
]
