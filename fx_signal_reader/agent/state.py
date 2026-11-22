"""
USD/JPY Signal Reader - LangGraph State & Structured Output Models
Task P3-03: LangGraph State Definition

Defines Pydantic v2 schemas for structured agent outputs (Macro Analyst,
Risk Committee, Trade Proposal), the shared LangGraph `AgentState` TypedDict
with `operator.add` reducer support on `audit_log`, and state helper utilities.
"""

from enum import Enum
import json
import math
import operator
from pathlib import Path
from typing import Annotated, Any, Mapping, TypedDict
import uuid

from pydantic import BaseModel, Field

from fx_signal_reader.core.normalizer import normalize_datetime_utc


class YieldBias(str, Enum):
    """Directional bias implied by sovereign yield spreads and policy.

    ``BULLISH`` favours appreciation of the base currency (BUY); ``BEARISH``
    favours the quote currency (SELL). Legacy USD/JPY names ``BULLISH_USD`` /
    ``BEARISH_USD`` remain as enum aliases, and legacy strings are accepted on input.
    """

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

    # Backward-compatible aliases (same members as BULLISH / BEARISH).
    BULLISH_USD = "BULLISH"
    BEARISH_USD = "BEARISH"

    @classmethod
    def _missing_(cls, value: object) -> "YieldBias | None":
        """Accept legacy serialized values ``"BULLISH_USD"`` / ``"BEARISH_USD"``."""
        if isinstance(value, str):
            legacy = _LEGACY_YIELD_BIAS_VALUES.get(value.strip().upper())
            if legacy is not None:
                return cls(legacy)
        return None


_LEGACY_YIELD_BIAS_VALUES: dict[str, str] = {
    "BULLISH_USD": "BULLISH",
    "BEARISH_USD": "BEARISH",
}


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
    us_2y: float | None = None
    jp_2y: float | None = None
    spread_2y: float | None = None
    crude_oil_price: float | None = None
    crude_oil_5d_change_pct: float | None = None
    terms_of_trade_bias: str | None = None
    gotobi_session: dict[str, Any] | None = None


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
    atr_14: float | None = None
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
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AgentState(TypedDict):
    """Shared LangGraph workflow state passed between specialist and execution nodes."""

    run_id: str
    timestamp_utc: str
    pair: str
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
    sl_pips: float | None
    tp_pips: float | None
    use_atr: bool | None
    allowed_regimes: list[str] | None
    regime_overrides: dict[str, dict[str, Any]] | None


_ALLOWED_REGIME_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {"risk_fraction", "risk_pct", "sl_pips", "tp_pips", "use_atr"}
)


def _require_positive_float_override(value: Any, field_name: str) -> float:
    """Validate a finite, strictly positive non-boolean float override value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a positive number.")
    fval = float(value)
    if not math.isfinite(fval) or fval <= 0.0:
        raise ValueError(f"{field_name} must be finite and > 0.")
    return fval


def normalize_regime_overrides(
    raw: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]] | None:
    """Validate and normalize per-regime sizing and bracket overrides.

    Keys must match ``RiskRegime`` names (case-insensitive). Supported per-regime
    fields are ``risk_fraction`` (or ``risk_pct``), ``sl_pips``, ``tp_pips``, and
    ``use_atr``.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("regime_overrides must be a mapping of RiskRegime -> override dict.")

    normalized: dict[str, dict[str, Any]] = {}
    for raw_regime, raw_cfg in raw.items():
        if not isinstance(raw_regime, str) or not raw_regime.strip():
            raise ValueError("regime_overrides keys must be non-empty RiskRegime names.")
        try:
            regime_key = RiskRegime(raw_regime.strip().upper()).value
        except ValueError as exc:
            raise ValueError(f"Unknown risk regime in regime_overrides: {raw_regime!r}") from exc

        if not isinstance(raw_cfg, Mapping):
            raise ValueError(f"regime_overrides[{regime_key!r}] must be a mapping.")

        unknown_keys = set(raw_cfg.keys()) - _ALLOWED_REGIME_OVERRIDE_KEYS
        if unknown_keys:
            raise ValueError(
                f"Unsupported keys in regime_overrides[{regime_key!r}]: {sorted(unknown_keys)}"
            )

        entry: dict[str, Any] = {}
        if "risk_fraction" in raw_cfg and raw_cfg["risk_fraction"] is not None:
            rf = _require_positive_float_override(
                raw_cfg["risk_fraction"], f"regime_overrides[{regime_key}].risk_fraction"
            )
            if rf > 1.0:
                raise ValueError(
                    f"regime_overrides[{regime_key}].risk_fraction must be <= 1.0."
                )
            entry["risk_fraction"] = rf
        elif "risk_pct" in raw_cfg and raw_cfg["risk_pct"] is not None:
            rp = _require_positive_float_override(
                raw_cfg["risk_pct"], f"regime_overrides[{regime_key}].risk_pct"
            )
            if rp > 100.0:
                raise ValueError(
                    f"regime_overrides[{regime_key}].risk_pct must be <= 100.0."
                )
            entry["risk_fraction"] = rp / 100.0

        for pip_field in ("sl_pips", "tp_pips"):
            if pip_field in raw_cfg and raw_cfg[pip_field] is not None:
                entry[pip_field] = _require_positive_float_override(
                    raw_cfg[pip_field], f"regime_overrides[{regime_key}].{pip_field}"
                )

        if "use_atr" in raw_cfg and raw_cfg["use_atr"] is not None:
            if not isinstance(raw_cfg["use_atr"], bool):
                raise ValueError(
                    f"regime_overrides[{regime_key}].use_atr must be a boolean."
                )
            entry["use_atr"] = raw_cfg["use_atr"]

        if entry:
            normalized[regime_key] = entry

    return normalized or None


def parse_regime_overrides_arg(raw: str | Mapping[str, Any] | None) -> dict[str, dict[str, Any]] | None:
    """Parse ``--regime-overrides`` from a JSON string, file path, or mapping."""
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return normalize_regime_overrides(raw)
    if not isinstance(raw, str):
        raise ValueError("regime_overrides argument must be a JSON string, file path, or mapping.")
    text = raw.strip()
    if not text:
        return None
    if not text.startswith("{"):
        candidate = Path(text)
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
        else:
            raise ValueError(
                f"regime_overrides must be a JSON object string or existing .json file path (got {raw!r})."
            )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in regime_overrides: {exc}") from exc
    return normalize_regime_overrides(parsed)


def create_initial_state(
    run_id: str | None = None,
    timestamp_utc: str | None = None,
    account_capital_usd: float | None = None,
    risk_fraction: float | None = None,
    sl_pips: float | None = None,
    tp_pips: float | None = None,
    use_atr: bool | None = None,
    allowed_regimes: list[str] | None = None,
    regime_overrides: Mapping[str, Any] | None = None,
    pair: str = "USD/JPY",
) -> AgentState:
    """
    Create a normalized initial `AgentState` dictionary for a LangGraph pipeline run.

    Args:
        run_id: Optional run identifier; defaults to a generated UUIDv4 string.
        timestamp_utc: Optional run timestamp (normalized to strict ISO-8601 UTC via
            `normalizer.normalize_datetime_utc`); defaults to current UTC time.
        account_capital_usd: Optional total account equity in USD (defaults to 10,000.0).
        risk_fraction: Optional risk fraction per trade (defaults to 0.01 / 1.0%).
        sl_pips: Optional custom fixed Stop Loss distance in pips (default 75.0).
        tp_pips: Optional custom fixed Take Profit distance in pips (default 150.0).
        use_atr: Optional toggle for ATR-14 dynamic Stop Loss / Take Profit brackets.
        allowed_regimes: Optional list of permitted RiskRegime names (e.g. ["RISK_ON", "TRANSITIONAL"]).
        pair: Currency pair symbol for this run (defaults to "USD/JPY").

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
        pair=pair.strip() if isinstance(pair, str) and pair.strip() else "USD/JPY",
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
        sl_pips=float(sl_pips) if sl_pips is not None else None,
        tp_pips=float(tp_pips) if tp_pips is not None else None,
        use_atr=bool(use_atr) if use_atr is not None else None,
        allowed_regimes=list(allowed_regimes) if allowed_regimes is not None else None,
        regime_overrides=normalize_regime_overrides(regime_overrides),
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
