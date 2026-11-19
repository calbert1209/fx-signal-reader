"""
USD/JPY Signal Reader - Specialist LangGraph Agent Nodes
Task P3-04: Specialist Agent Nodes

Implements deterministic, rule-augmented specialist node functions operating
on `AgentState` and consuming the read-only domain query tools from
`mcp_server.py`:
  1. `macro_analyst_node`: evaluates US-Japan 10Y yield spreads and upcoming
     high-impact USD/JPY macro releases into a validated `MacroAssessment`.
  2. `risk_committee_node`: evaluates cross-asset VIX risk regime and MoF/BoJ
     4-tier verbal intervention risk into a validated `RiskAssessment` and
     synthesizes a `TradeProposal` (`BUY`, `SELL`, or `HOLD`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Callable
import uuid

from fx_signal_reader.agent.state import (
    AgentState,
    ApprovalStatus,
    InterventionAlertLevel,
    MacroAssessment,
    RiskAssessment,
    RiskRegime,
    TradeAction,
    TradeProposal,
    YieldBias,
    validate_state_models,
)
from fx_signal_reader.core.normalizer import normalize_datetime_utc
from fx_signal_reader.mcp.server import (
    get_latest_yield_spread,
    get_market_ticks_summary,
    get_market_trend_summary,
    get_recent_mof_alerts,
    get_upcoming_macro_events,
)


# Yield-spread directional thresholds (percentage points; 1.0% = 100 bps)
SPREAD_BULLISH_THRESHOLD = 2.0
SPREAD_BEARISH_THRESHOLD = 1.0

# High-impact event window & keywords for USD/JPY binary catalyst risk
EVENT_WINDOW_PAST_HOURS = 4
EVENT_WINDOW_FUTURE_HOURS = 48
DEFAULT_PIT_MOF_LOOKBACK_HOURS = 48
KEY_MACRO_EVENT_KEYWORDS: tuple[str, ...] = (
    "fomc",
    "cpi",
    "nfp",
    "non-farm",
    "nonfarm",
    "boj",
    "bank of japan",
    "retail sales",
    "pce",
    "gdp",
    "rate decision",
    "policy rate",
    "payroll",
    "employment",
    "ppi",
    "warsh",
    "powell",
    "ueda",
)

# Risk regime VIX boundaries
VIX_RISK_OFF_THRESHOLD = 25.0
VIX_RISK_ON_THRESHOLD = 18.0

# MoF/BoJ verbal escalation base intervention scores by tier (0..4)
MOF_TIER_BASE_SCORES: dict[int, float] = {
    4: 80.0,
    3: 55.0,
    2: 35.0,
    1: 15.0,
    0: 5.0,
}

# Psychological USD/JPY intervention barrier thresholds
SPOT_BARRIER_CRITICAL = 160.0
SPOT_BARRIER_HIGH = 158.5
SPOT_BARRIER_WATCH = 157.0

INTERVENTION_VETO_SCORE = 70.0
ELEVATED_INTERVENTION_FOR_RISK_OFF = 35.0

# Trade sizing & bracket defaults (2.0 risk-reward ratio)
DEFAULT_ACCOUNT_CAPITAL_USD = 10000.0
DEFAULT_RISK_FRACTION = 0.01  # 1.0% = $100 fixed dollar risk per $10k account capital
POSITION_UNITS_BY_REGIME: dict[RiskRegime, int] = {
    RiskRegime.RISK_ON: 10000,
    RiskRegime.TRANSITIONAL: 5000,
    RiskRegime.RISK_OFF: 2500,
}
STOP_LOSS_DISTANCE_JPY = 0.75
TAKE_PROFIT_DISTANCE_JPY = 1.50


def compute_position_units(
    spot_price: float | None,
    stop_loss_distance_jpy: float = STOP_LOSS_DISTANCE_JPY,
    account_capital_usd: float = DEFAULT_ACCOUNT_CAPITAL_USD,
    risk_fraction: float = DEFAULT_RISK_FRACTION,
    is_quote_usd: bool = False,
) -> int:
    """
    Compute equal-dollar risk position sizing.

    Base-is-USD (default, e.g. USD/JPY, USD/CHF; SL distance in quote currency):
    Units = floor((account_capital_usd * risk_fraction * spot_price) / stop_loss_distance)
    Quote-is-USD (``is_quote_usd=True``, e.g. EUR/USD, GBP/USD; SL distance in USD):
    Units = floor((account_capital_usd * risk_fraction) / stop_loss_distance)

    Guarantees non-negative integer sizing; returns 0 if inputs are non-positive or invalid.
    """
    if spot_price is None or spot_price <= 0.0 or stop_loss_distance_jpy <= 0.0:
        return 0
    if account_capital_usd <= 0.0 or risk_fraction <= 0.0:
        return 0
    dollar_risk = account_capital_usd * risk_fraction
    if is_quote_usd:
        units = int(math.floor(dollar_risk / stop_loss_distance_jpy))
        return max(0, units)
    units = int(math.floor((dollar_risk * spot_price) / stop_loss_distance_jpy))
    return max(0, units)


def compute_bracket_distances_jpy(
    atr_14: float | None = None,
    *,
    use_atr: bool = False,
    sl_pips: float | None = None,
    tp_pips: float | None = None,
) -> tuple[float, float]:
    """
    Compute Stop Loss and Take Profit distances in JPY.

    When `use_atr=True` and a positive `atr_14` is available, clamps SL distance to
    `[0.25, 2.50]` JPY and targets a `2.0 * SL` Take Profit distance. Otherwise
    uses configured `sl_pips`/`tp_pips` overrides or the default 0.75 / 1.50 JPY brackets.
    """
    if use_atr and atr_14 is not None and atr_14 > 0.0:
        sl_dist = round(max(0.25, min(2.50, float(atr_14))), 3)
        tp_dist = round(2.0 * sl_dist, 3)
        return sl_dist, tp_dist

    has_custom_sl = sl_pips is not None and sl_pips > 0.0
    sl_dist = round(float(sl_pips) * 0.01, 3) if has_custom_sl else STOP_LOSS_DISTANCE_JPY
    if tp_pips is not None and tp_pips > 0.0:
        tp_dist = round(float(tp_pips) * 0.01, 3)
    elif has_custom_sl:
        tp_dist = round(2.0 * sl_dist, 3)
    else:
        tp_dist = TAKE_PROFIT_DISTANCE_JPY
    return sl_dist, tp_dist


def _safe_float(value: Any) -> float | None:
    """Convert a numeric value to a finite float or return None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _parse_utc_datetime(timestamp_value: Any) -> datetime | None:
    """Normalize a timestamp input to a timezone-aware UTC datetime, or return None."""
    if timestamp_value is None:
        return None
    try:
        normalized = normalize_datetime_utc(timestamp_value, fallback_now=False)
        return datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return None


def _is_key_macro_catalyst(title: str) -> bool:
    """Return True when an event title matches a major USD/JPY policy or data catalyst."""
    lowered = title.lower()
    return any(keyword in lowered for keyword in KEY_MACRO_EVENT_KEYWORDS)


def _find_near_window_catalysts(
    events: list[dict[str, Any]],
    reference_ts_utc: str,
) -> list[dict[str, Any]]:
    """Identify high-impact USD/JPY catalyst releases near the reference window."""
    ref_dt = _parse_utc_datetime(reference_ts_utc)
    if ref_dt is None:
        return []

    window_start = ref_dt - timedelta(hours=EVENT_WINDOW_PAST_HOURS)
    window_end = ref_dt + timedelta(hours=EVENT_WINDOW_FUTURE_HOURS)

    near_events: list[dict[str, Any]] = []
    for event in events:
        title = str(event.get("title") or "")
        if not _is_key_macro_catalyst(title):
            continue
        event_dt = _parse_utc_datetime(event.get("timestamp_utc"))
        if event_dt is None:
            continue
        if window_start <= event_dt <= window_end:
            near_events.append(event)
    return near_events


def _evaluate_yield_bias(spread_10y: float | None) -> YieldBias:
    """Map US-Japan 10Y yield spread (percentage points) to directional YieldBias."""
    if spread_10y is None:
        return YieldBias.NEUTRAL
    if spread_10y > SPREAD_BULLISH_THRESHOLD:
        return YieldBias.BULLISH_USD
    if spread_10y < SPREAD_BEARISH_THRESHOLD:
        return YieldBias.BEARISH_USD
    return YieldBias.NEUTRAL


def _compute_macro_confidence(
    spread_10y: float | None,
    yield_bias: YieldBias,
    near_catalyst_count: int,
) -> float:
    """Compute bounded [0.0, 1.0] macro confidence from spread magnitude and event risk."""
    if spread_10y is None:
        base_confidence = 0.25
        event_penalty = min(0.10, 0.05 * near_catalyst_count)
        return round(max(0.05, min(1.0, base_confidence - event_penalty)), 4)

    if yield_bias == YieldBias.BULLISH_USD:
        magnitude_bonus = min(0.25, (spread_10y - SPREAD_BULLISH_THRESHOLD) * 0.25)
        base_confidence = 0.65 + magnitude_bonus
    elif yield_bias == YieldBias.BEARISH_USD:
        magnitude_bonus = min(0.25, (SPREAD_BEARISH_THRESHOLD - spread_10y) * 0.25)
        base_confidence = 0.65 + magnitude_bonus
    else:
        distance_from_mid = abs(spread_10y - 1.5)
        base_confidence = 0.55 - min(0.10, distance_from_mid * 0.20)

    event_penalty = min(0.15, 0.06 * near_catalyst_count)
    return round(max(0.10, min(0.95, base_confidence - event_penalty)), 4)


def _format_macro_summary(
    yield_bias: YieldBias,
    us_10y: float | None,
    jp_10y: float | None,
    spread_10y: float | None,
    spread_bps: float | None,
    events: list[dict[str, Any]],
    near_catalysts: list[dict[str, Any]],
    spread_momentum: str | None = None,
    downgraded_from_bullish: bool = False,
) -> str:
    """Build a concise human-readable macro policy divergence summary."""
    if spread_10y is None:
        spread_desc = "US-JP 10Y yield spread data unavailable; defaulting to NEUTRAL bias."
    else:
        bps_text = f"{spread_bps:.1f} bps" if spread_bps is not None else "n/a bps"
        us_text = f"{us_10y:.3f}%" if us_10y is not None else "n/a"
        jp_text = f"{jp_10y:.3f}%" if jp_10y is not None else "n/a"
        if downgraded_from_bullish:
            spread_desc = (
                f"US-JP 10Y spread at {spread_10y:.3f}% ({bps_text}; "
                f"US10Y={us_text}, JP10Y={jp_text}) but carry compression "
                f"({spread_momentum}) downgrades BULLISH_USD to NEUTRAL."
            )
        else:
            momentum_note = f" [momentum={spread_momentum}]" if spread_momentum else ""
            spread_desc = (
                f"US-JP 10Y spread at {spread_10y:.3f}% ({bps_text}; "
                f"US10Y={us_text}, JP10Y={jp_text}){momentum_note} implies {yield_bias.value}."
            )

    if near_catalysts:
        titles = ", ".join(str(ev.get("title", "Unnamed")) for ev in near_catalysts[:3])
        event_desc = (
            f" {len(near_catalysts)} imminent key USD/JPY catalyst(s) near window "
            f"[{titles}] dampen directional confidence."
        )
    elif events:
        event_desc = (
            f" {len(events)} high-impact calendar release(s) tracked; "
            f"none inside the immediate {EVENT_WINDOW_FUTURE_HOURS}h catalyst window."
        )
    else:
        event_desc = " No high-impact USD/JPY calendar releases queued."

    return f"{spread_desc}{event_desc}"


def _validate_node_update(state: AgentState, update: dict[str, Any]) -> None:
    """Validate merged node output against all AgentState Pydantic schemas."""
    merged_preview: AgentState = {
        **state,
        **update,
        "audit_log": list(state.get("audit_log") or []) + list(update.get("audit_log") or []),
    }
    validate_state_models(merged_preview)


def _resolve_pit_as_of_utc(state: AgentState, point_in_time: bool) -> str | None:
    """Validate and normalize `state['timestamp_utc']` strictly when `point_in_time=True`."""
    if not point_in_time:
        return None
    raw_ts = state.get("timestamp_utc")
    if isinstance(raw_ts, bool):
        raise ValueError("state['timestamp_utc'] must be a valid timestamp string, not bool.")
    return normalize_datetime_utc(raw_ts, fallback_now=False)


def macro_analyst_node(
    state: AgentState,
    db_path: str | None = None,
    point_in_time: bool = False,
    mof_lookback_hours: int | None = None,
) -> dict[str, Any]:
    """
    Evaluate US-Japan yield differentials and high-impact macro calendar releases.

    Reads from `mcp_server.get_latest_yield_spread` and
    `mcp_server.get_upcoming_macro_events`, validates a `MacroAssessment` Pydantic
    model, and returns a partial `AgentState` update dict.
    """
    _ = mof_lookback_hours
    as_of_utc = _resolve_pit_as_of_utc(state, point_in_time)

    spread_snapshot = get_latest_yield_spread(db_path=db_path, as_of_utc=as_of_utc)
    raw_events = get_upcoming_macro_events(
        currency=None,
        impact="High",
        limit=10,
        db_path=db_path,
        as_of_utc=as_of_utc,
        past_hours=EVENT_WINDOW_PAST_HOURS,
        future_hours=EVENT_WINDOW_FUTURE_HOURS,
    )
    high_impact_events = [
        dict(event)
        for event in raw_events
        if str(event.get("currency") or "").upper() in {"USD", "JPY", ""}
    ]

    us_10y = _safe_float(spread_snapshot.get("us_10y"))
    jp_10y = _safe_float(spread_snapshot.get("jp_10y"))
    spread_10y = _safe_float(spread_snapshot.get("spread_10y"))
    if spread_10y is None and us_10y is not None and jp_10y is not None:
        spread_10y = round(us_10y - jp_10y, 6)

    spread_bps = _safe_float(spread_snapshot.get("spread_bps"))
    if spread_bps is None and spread_10y is not None:
        spread_bps = round(spread_10y * 100.0, 2)

    usdjpy_spot = _safe_float(spread_snapshot.get("usdjpy_spot"))
    resolved_spot = usdjpy_spot if usdjpy_spot is not None else _safe_float(state.get("spot_price"))

    reference_ts_utc = (
        as_of_utc
        if as_of_utc is not None
        else normalize_datetime_utc(state.get("timestamp_utc"), fallback_now=True)
    )
    near_catalysts = _find_near_window_catalysts(high_impact_events, reference_ts_utc)

    spread_5d_change = _safe_float(spread_snapshot.get("spread_5d_change"))
    spread_20d_change = _safe_float(spread_snapshot.get("spread_20d_change"))
    raw_momentum = spread_snapshot.get("momentum") or spread_snapshot.get("spread_momentum")
    spread_momentum = str(raw_momentum) if raw_momentum is not None else None

    raw_yield_bias = _evaluate_yield_bias(spread_10y)
    downgraded_from_bullish = (
        raw_yield_bias == YieldBias.BULLISH_USD and spread_momentum == "CONTRACTING"
    )
    yield_bias = YieldBias.NEUTRAL if downgraded_from_bullish else raw_yield_bias

    confidence = _compute_macro_confidence(
        spread_10y=spread_10y,
        yield_bias=yield_bias,
        near_catalyst_count=len(near_catalysts),
    )
    summary = _format_macro_summary(
        yield_bias=yield_bias,
        us_10y=us_10y,
        jp_10y=jp_10y,
        spread_10y=spread_10y,
        spread_bps=spread_bps,
        events=high_impact_events,
        near_catalysts=near_catalysts,
        spread_momentum=spread_momentum,
        downgraded_from_bullish=downgraded_from_bullish,
    )

    assessment = MacroAssessment(
        yield_bias=yield_bias,
        us_10y=us_10y,
        jp_10y=jp_10y,
        spread_10y=spread_10y,
        spread_bps=spread_bps,
        spread_5d_change=spread_5d_change,
        spread_20d_change=spread_20d_change,
        spread_momentum=spread_momentum,
        upcoming_high_impact_events=high_impact_events,
        policy_divergence_summary=summary,
        confidence=confidence,
    )
    assessment_json = assessment.model_dump(mode="json")

    audit_entry = (
        f"macro_analyst:yield_bias={assessment.yield_bias.value};"
        f"spread_10y={assessment.spread_10y};"
        f"confidence={assessment.confidence:.4f};"
        f"high_impact_events={len(high_impact_events)};"
        f"near_catalysts={len(near_catalysts)}"
    )

    update: dict[str, Any] = {
        "spot_price": resolved_spot,
        "yield_bias": assessment.yield_bias.value,
        "macro_assessment": assessment_json,
        "audit_log": [audit_entry],
    }
    _validate_node_update(state, update)
    return update


def _extract_symbol_close(symbols_map: dict[str, Any], symbol: str) -> float | None:
    """Safely extract finite close price for a ticker from `get_market_ticks_summary`."""
    tick = symbols_map.get(symbol)
    if not isinstance(tick, dict):
        return None
    return _safe_float(tick.get("close"))


def _evaluate_risk_regime(vix_level: float | None) -> RiskRegime:
    """Classify cross-asset volatility regime from VIX spot level."""
    if vix_level is None:
        return RiskRegime.TRANSITIONAL
    if vix_level >= VIX_RISK_OFF_THRESHOLD:
        return RiskRegime.RISK_OFF
    if vix_level < VIX_RISK_ON_THRESHOLD:
        return RiskRegime.RISK_ON
    return RiskRegime.TRANSITIONAL


def _compute_intervention_metrics(
    mof_alerts: list[dict[str, Any]],
    spot_price: float | None,
) -> tuple[int, float, InterventionAlertLevel, dict[str, float]]:
    """
    Compute highest MoF verbal tier, composite intervention risk score (0-100),
    alert severity level, and score component breakdown.
    """
    tier_counts: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    highest_tier = 0
    for alert in mof_alerts:
        raw_tier = int(alert.get("tier") or 0)
        clamped_tier = max(0, min(4, raw_tier))
        if clamped_tier > highest_tier:
            highest_tier = clamped_tier
        if clamped_tier in tier_counts:
            tier_counts[clamped_tier] += 1

    base_score = MOF_TIER_BASE_SCORES[highest_tier]

    # Weight repeated Tier 3/4 headlines beyond the primary peak signal
    if highest_tier == 4:
        extra_t4 = max(0, tier_counts[4] - 1)
        extra_t3 = tier_counts[3]
        volume_bonus = min(15.0, extra_t4 * 4.0 + extra_t3 * 1.5)
    elif highest_tier == 3:
        extra_t3 = max(0, tier_counts[3] - 1)
        volume_bonus = min(15.0, extra_t3 * 2.5)
    else:
        volume_bonus = 0.0

    # Psychological 160.00 MoF intervention barrier proximity premium
    if spot_price is None:
        spot_bonus = 0.0
    elif spot_price >= SPOT_BARRIER_CRITICAL:
        spot_bonus = 20.0
    elif spot_price >= SPOT_BARRIER_HIGH:
        spot_bonus = 15.0
    elif spot_price >= SPOT_BARRIER_WATCH:
        spot_bonus = 5.0
    else:
        spot_bonus = 0.0

    score = round(max(0.0, min(100.0, base_score + volume_bonus + spot_bonus)), 2)

    if score >= INTERVENTION_VETO_SCORE or highest_tier >= 4:
        alert_level = InterventionAlertLevel.CRITICAL
    elif score >= 45.0 or highest_tier == 3:
        alert_level = InterventionAlertLevel.WARNING
    elif score >= 15.0 or highest_tier >= 1:
        alert_level = InterventionAlertLevel.WATCH
    else:
        alert_level = InterventionAlertLevel.NONE

    breakdown = {
        "base_score": base_score,
        "volume_bonus": round(volume_bonus, 2),
        "spot_bonus": spot_bonus,
    }
    return highest_tier, score, alert_level, breakdown


def _build_trade_proposal(
    run_id: str,
    timestamp_utc: str,
    spot_price: float | None,
    yield_bias: YieldBias,
    risk_assessment: RiskAssessment,
    account_capital_usd: float = DEFAULT_ACCOUNT_CAPITAL_USD,
    risk_fraction: float = DEFAULT_RISK_FRACTION,
    stop_loss_distance_jpy: float = STOP_LOSS_DISTANCE_JPY,
    take_profit_distance_jpy: float = TAKE_PROFIT_DISTANCE_JPY,
) -> tuple[TradeProposal, str]:
    """Synthesize a Pydantic-validated `TradeProposal` and initial `ApprovalStatus`."""
    proposal_id = f"prop-{run_id}"
    regime = risk_assessment.risk_regime
    score = risk_assessment.intervention_risk_score

    if risk_assessment.veto_trade or yield_bias == YieldBias.NEUTRAL or spot_price is None:
        reasons: list[str] = []
        if risk_assessment.veto_trade:
            reasons.append(
                f"risk veto triggered (intervention_score={score:.2f}, "
                f"highest_mof_tier={risk_assessment.highest_mof_tier}, "
                f"regime={regime.value})"
            )
        if yield_bias == YieldBias.NEUTRAL:
            reasons.append("macro yield bias is NEUTRAL")
        if spot_price is None:
            reasons.append("USD/JPY spot price unavailable")

        proposal = TradeProposal(
            proposal_id=proposal_id,
            timestamp_utc=timestamp_utc,
            action=TradeAction.HOLD,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            risk_reward_ratio=None,
            position_size_units=0,
            rationale=f"HOLD recommended: {'; '.join(reasons)}.",
            macro_bias=yield_bias,
            risk_regime=regime,
            intervention_risk_score=score,
        )
        return proposal, ApprovalStatus.NOT_REQUIRED.value

    entry_price = round(spot_price, 3)
    units = compute_position_units(
        spot_price=entry_price,
        stop_loss_distance_jpy=stop_loss_distance_jpy,
        account_capital_usd=account_capital_usd,
        risk_fraction=risk_fraction,
    )
    is_bullish = yield_bias == YieldBias.BULLISH_USD
    direction = 1 if is_bullish else -1
    action = TradeAction.BUY if is_bullish else TradeAction.SELL
    stop_loss = round(entry_price - direction * stop_loss_distance_jpy, 3)
    take_profit = round(entry_price + direction * take_profit_distance_jpy, 3)
    risk_reward_ratio = (
        round(take_profit_distance_jpy / stop_loss_distance_jpy, 4)
        if stop_loss_distance_jpy > 0.0
        else 2.0
    )
    dollar_risk = account_capital_usd * risk_fraction
    rationale = (
        f"{action.value} USD/JPY at {entry_price:.3f} "
        f"(SL {stop_loss:.3f}, TP {take_profit:.3f}, {risk_reward_ratio:.2g} R:R): "
        f"macro bias {yield_bias.value} with fixed-dollar risk sizing "
        f"({units:,} units risking ${dollar_risk:.2f} / {risk_fraction * 100:.1f}%) "
        f"and manageable intervention risk ({score:.2f}/100)."
    )

    proposal = TradeProposal(
        proposal_id=proposal_id,
        timestamp_utc=timestamp_utc,
        action=action,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward_ratio=risk_reward_ratio,
        position_size_units=units,
        rationale=rationale,
        macro_bias=yield_bias,
        risk_regime=regime,
        intervention_risk_score=score,
    )
    return proposal, ApprovalStatus.PENDING.value


def risk_committee_node(
    state: AgentState,
    db_path: str | None = None,
    point_in_time: bool = False,
    mof_lookback_hours: int | None = None,
) -> dict[str, Any]:
    """
    Evaluate cross-asset risk regime, MoF/BoJ verbal intervention hazard, and
    synthesize a structured `TradeProposal`.

    Reads from `mcp_server.get_market_ticks_summary` and
    `mcp_server.get_recent_mof_alerts`, validates `RiskAssessment` and
    `TradeProposal`, runs `validate_state_models`, and returns a partial
    `AgentState` update dict.
    """
    as_of_utc = _resolve_pit_as_of_utc(state, point_in_time)
    effective_lookback_hours = (
        DEFAULT_PIT_MOF_LOOKBACK_HOURS
        if (point_in_time and mof_lookback_hours is None)
        else mof_lookback_hours
    )

    ticks_summary = get_market_ticks_summary(db_path=db_path, as_of_utc=as_of_utc)
    mof_alerts = get_recent_mof_alerts(
        min_tier=1,
        limit=25,
        db_path=db_path,
        as_of_utc=as_of_utc,
        lookback_hours=effective_lookback_hours,
    )
    trend_summary = get_market_trend_summary(symbol="JPY=X", db_path=db_path, as_of_utc=as_of_utc)
    ema_50 = _safe_float(trend_summary.get("ema_50"))
    sma_200 = _safe_float(trend_summary.get("sma_200"))
    atr_14 = _safe_float(trend_summary.get("atr_14"))
    raw_trend = trend_summary.get("trend")
    market_trend = str(raw_trend) if raw_trend is not None else None

    symbols_map = ticks_summary.get("symbols") or {}
    spot_from_ticks = _extract_symbol_close(symbols_map, "JPY=X")
    spot_price = (
        spot_from_ticks
        if spot_from_ticks is not None
        else _safe_float(state.get("spot_price"))
    )
    vix_level = _extract_symbol_close(symbols_map, "^VIX")
    sp500_price = _extract_symbol_close(symbols_map, "^GSPC")
    nikkei_price = _extract_symbol_close(symbols_map, "^N225")
    gold_price = _extract_symbol_close(symbols_map, "GC=F")

    risk_regime = _evaluate_risk_regime(vix_level)
    highest_tier, intervention_score, alert_level, breakdown = _compute_intervention_metrics(
        mof_alerts=mof_alerts,
        spot_price=spot_price,
    )

    raw_bias = state.get("yield_bias") or YieldBias.NEUTRAL.value
    try:
        yield_bias = YieldBias(raw_bias)
    except ValueError:
        yield_bias = YieldBias.NEUTRAL

    is_risk_off = (
        risk_regime == RiskRegime.RISK_OFF
        or (vix_level is not None and vix_level >= VIX_RISK_OFF_THRESHOLD)
    )
    raw_allowed_regimes = state.get("allowed_regimes")
    if isinstance(raw_allowed_regimes, (list, tuple, set)) and len(raw_allowed_regimes) > 0:
        allowed_set = {str(r).strip().upper() for r in raw_allowed_regimes if str(r).strip()}
        is_disallowed_regime = bool(allowed_set) and (risk_regime.value not in allowed_set)
    else:
        is_disallowed_regime = False

    is_falling_knife = bool(
        yield_bias == YieldBias.BULLISH_USD
        and spot_price is not None
        and ema_50 is not None
        and spot_price < ema_50
    )
    veto_trade = bool(
        intervention_score >= INTERVENTION_VETO_SCORE
        or highest_tier >= 4
        or is_risk_off
        or is_disallowed_regime
        or is_falling_knife
    )

    flagged_headlines = [
        {
            "published_utc": alert.get("published_utc"),
            "source": alert.get("source"),
            "title": alert.get("title"),
            "tier": int(alert.get("tier") or 0),
            "severity": alert.get("severity"),
            "matched_entities": alert.get("matched_entities", []),
            "matched_tier_keywords": alert.get("matched_tier_keywords", []),
        }
        for alert in mof_alerts[:10]
    ]

    vix_text = f"{vix_level:.2f}" if vix_level is not None else "n/a"
    spot_text = f"{spot_price:.3f}" if spot_price is not None else "n/a"
    veto_reasons: list[str] = []
    if intervention_score >= INTERVENTION_VETO_SCORE:
        veto_reasons.append(f"intervention score {intervention_score:.2f} >= {INTERVENTION_VETO_SCORE}")
    if highest_tier >= 4:
        veto_reasons.append(f"MoF verbal tier {highest_tier} >= 4")
    if is_risk_off:
        veto_reasons.append(f"RISK_OFF regime (VIX {vix_text} >= {VIX_RISK_OFF_THRESHOLD})")
    if is_disallowed_regime and not is_risk_off:
        veto_reasons.append(f"regime {risk_regime.value} excluded by allowed_regimes")
    if is_falling_knife and spot_price is not None and ema_50 is not None:
        veto_reasons.append(f"trend filter veto (spot {spot_price:.3f} < EMA-50 {ema_50:.3f})")
    veto_suffix = f" [{'; '.join(veto_reasons)}]" if veto_reasons else ""

    risk_notes = (
        f"Regime={risk_regime.value} (VIX={vix_text}); USD/JPY spot={spot_text}; "
        f"MoF Tier {highest_tier} across {len(mof_alerts)} alert(s) -> "
        f"score={intervention_score:.2f} [{alert_level.value}] "
        f"(base={breakdown['base_score']:.1f}, volume={breakdown['volume_bonus']:.1f}, "
        f"spot={breakdown['spot_bonus']:.1f}); veto_trade={veto_trade}{veto_suffix}."
    )

    risk_assessment = RiskAssessment(
        risk_regime=risk_regime,
        vix_level=vix_level,
        sp500_price=sp500_price,
        nikkei_price=nikkei_price,
        gold_price=gold_price,
        intervention_risk_score=intervention_score,
        intervention_alert_level=alert_level,
        highest_mof_tier=highest_tier,
        flagged_headlines=flagged_headlines,
        ema_50=ema_50,
        sma_200=sma_200,
        atr_14=atr_14,
        market_trend=market_trend,
        veto_trade=veto_trade,
        risk_notes=risk_notes,
    )

    run_id = str(state.get("run_id") or "").strip() or str(uuid.uuid4())
    proposal_ts = (
        as_of_utc
        if as_of_utc is not None
        else normalize_datetime_utc(state.get("timestamp_utc"), fallback_now=True)
    )
    account_capital_usd = (
        _safe_float(state.get("account_capital_usd")) or DEFAULT_ACCOUNT_CAPITAL_USD
    )
    risk_fraction = _safe_float(state.get("risk_fraction")) or DEFAULT_RISK_FRACTION
    sl_dist, tp_dist = compute_bracket_distances_jpy(
        atr_14=atr_14,
        use_atr=bool(state.get("use_atr")),
        sl_pips=_safe_float(state.get("sl_pips")),
        tp_pips=_safe_float(state.get("tp_pips")),
    )
    trade_proposal, approval_status = _build_trade_proposal(
        run_id=run_id,
        timestamp_utc=proposal_ts,
        spot_price=spot_price,
        yield_bias=yield_bias,
        risk_assessment=risk_assessment,
        account_capital_usd=account_capital_usd,
        risk_fraction=risk_fraction,
        stop_loss_distance_jpy=sl_dist,
        take_profit_distance_jpy=tp_dist,
    )

    audit_entry = (
        f"risk_committee:regime={risk_assessment.risk_regime.value};"
        f"intervention_score={risk_assessment.intervention_risk_score:.2f};"
        f"alert_level={risk_assessment.intervention_alert_level.value};"
        f"highest_tier={risk_assessment.highest_mof_tier};"
        f"veto={risk_assessment.veto_trade};"
        f"action={trade_proposal.action.value};"
        f"approval={approval_status}"
    )

    update: dict[str, Any] = {
        "spot_price": spot_price,
        "risk_regime": risk_assessment.risk_regime.value,
        "intervention_risk_score": risk_assessment.intervention_risk_score,
        "risk_assessment": risk_assessment.model_dump(mode="json"),
        "trade_proposal": trade_proposal.model_dump(mode="json"),
        "approval_status": approval_status,
        "audit_log": [audit_entry],
    }
    _validate_node_update(state, update)
    return update


def make_macro_analyst_node(
    db_path: str | None = None,
    point_in_time: bool = False,
    mof_lookback_hours: int | None = None,
) -> Callable[[AgentState], dict[str, Any]]:
    """Create a `StateGraph`-compatible Macro Analyst node bound to `db_path` and PIT options."""

    def _macro_analyst(state: AgentState) -> dict[str, Any]:
        return macro_analyst_node(
            state,
            db_path=db_path,
            point_in_time=point_in_time,
            mof_lookback_hours=mof_lookback_hours,
        )

    return _macro_analyst


def make_risk_committee_node(
    db_path: str | None = None,
    point_in_time: bool = False,
    mof_lookback_hours: int | None = None,
) -> Callable[[AgentState], dict[str, Any]]:
    """Create a `StateGraph`-compatible Risk Committee node bound to `db_path` and PIT options."""

    def _risk_committee(state: AgentState) -> dict[str, Any]:
        return risk_committee_node(
            state,
            db_path=db_path,
            point_in_time=point_in_time,
            mof_lookback_hours=mof_lookback_hours,
        )

    return _risk_committee


__all__ = [
    "DEFAULT_PIT_MOF_LOOKBACK_HOURS",
    "macro_analyst_node",
    "make_macro_analyst_node",
    "make_risk_committee_node",
    "risk_committee_node",
]
