"""
Telegram Message Formatters for Trade Proposals, Execution Receipts, and Daily Summaries.
Task P10-02: Trade Proposal & Daily Summary Message Formatters

Provides mobile-friendly plain-text message builders and inline keyboard markup
helpers for the Telegram remote approval gate and scheduled/on-demand summaries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from fx_signal_reader.core.gotobi import JST, evaluate_gotobi_session


def _enum_or_str(value: Any, default: str = "N/A") -> str:
    """Extract string value from an Enum instance or arbitrary object."""
    if value is None:
        return default
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text if text else default


def _fmt_number(value: Any, decimals: int | None = None, suffix: str = "", default: str = "N/A") -> str:
    """Format a numeric value safely with optional fixed decimals and suffix."""
    if value is None:
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        return f"{text}{suffix}" if text else default
    if decimals is not None:
        return f"{num:.{decimals}f}{suffix}"
    return f"{num}{suffix}"


def _parse_iso_utc(ts_value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string or datetime into a timezone-aware UTC datetime."""
    if ts_value is None:
        return None
    if isinstance(ts_value, datetime):
        if ts_value.tzinfo is None:
            return ts_value.replace(tzinfo=timezone.utc)
        return ts_value.astimezone(timezone.utc)
    if isinstance(ts_value, str):
        cleaned = ts_value.strip()
        if not cleaned:
            return None
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _coerce_mapping(obj: Any) -> dict[str, Any]:
    """Convert a dict, Mapping, or Pydantic model into a plain dict."""
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        try:
            return dict(obj.model_dump(mode="json"))
        except TypeError:
            return dict(obj.model_dump())
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return dict(obj.to_dict())
    return {}


def build_approval_inline_keyboard(proposal_id: str) -> dict[str, Any]:
    """
    Build a Telegram InlineKeyboardMarkup dictionary with Approve and Reject buttons.

    Args:
        proposal_id: Unique proposal identifier bound to callback_data.

    Returns:
        Telegram InlineKeyboardMarkup payload dictionary.
    """
    clean_id = str(proposal_id or "").strip() or "UNKNOWN"
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{clean_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{clean_id}"},
            ]
        ]
    }


def format_proposal_message(state: dict[str, Any], thread_id: str | None = None) -> str:
    """
    Format an actionable BUY/SELL trade proposal from AgentState into a mobile-friendly
    plain-text Telegram notification.

    Args:
        state: Shared LangGraph AgentState dictionary.
        thread_id: Optional LangGraph thread/run identifier override.

    Returns:
        Formatted plain-text proposal message string.
    """
    state_dict = _coerce_mapping(state)
    proposal = _coerce_mapping(state_dict.get("trade_proposal"))
    macro = _coerce_mapping(state_dict.get("macro_assessment"))
    risk = _coerce_mapping(state_dict.get("risk_assessment"))

    pair = (
        _enum_or_str(state_dict.get("pair"), default="")
        or _enum_or_str(proposal.get("pair"), default="")
        or "USD/JPY"
    )
    action = _enum_or_str(proposal.get("action"), default="HOLD")
    units_raw = proposal.get("position_size_units")
    units = int(units_raw) if isinstance(units_raw, (int, float)) else (units_raw if units_raw is not None else 0)

    entry_price = (
        proposal.get("entry_price")
        if proposal.get("entry_price") is not None
        else state_dict.get("spot_price")
    )
    stop_loss = proposal.get("stop_loss")
    take_profit = proposal.get("take_profit")
    rr_ratio = proposal.get("risk_reward_ratio")

    confidence = (
        proposal.get("confidence")
        if proposal.get("confidence") is not None
        else macro.get("confidence")
    )
    proposal_id = _enum_or_str(proposal.get("proposal_id"), default="N/A")
    resolved_thread = (
        str(thread_id).strip()
        if thread_id is not None and str(thread_id).strip()
        else _enum_or_str(state_dict.get("run_id"), default="N/A")
    )

    action_icon = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"

    lines: list[str] = [
        f"🚨 FX Trade Proposal: {action_icon} {action} {pair}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• Pair          : {pair}",
        f"• Action        : {action} ({units} units)",
        f"• Entry / Spot  : {_fmt_number(entry_price)}",
        f"• Stop Loss     : {_fmt_number(stop_loss)}",
        f"• Take Profit   : {_fmt_number(take_profit)}",
        f"• Risk:Reward   : {_fmt_number(rr_ratio)}",
        f"• Confidence    : {_fmt_number(confidence)}",
        f"• Proposal ID   : {proposal_id}",
        f"• Thread/Run ID : {resolved_thread}",
    ]

    # Macro Drivers Section
    yield_bias = _enum_or_str(
        state_dict.get("yield_bias")
        or macro.get("yield_bias")
        or proposal.get("macro_bias"),
        default="NEUTRAL",
    )
    us_10y = macro.get("us_10y")
    jp_10y = macro.get("jp_10y")
    spread_10y = macro.get("spread_10y")
    spread_bps = macro.get("spread_bps")

    lines.extend(
        [
            "",
            "📊 Macro Drivers",
            f"• Yield Bias    : {yield_bias}",
            (
                f"• 10Y Spread    : {_fmt_number(spread_10y, suffix='%')} "
                f"({_fmt_number(spread_bps, suffix=' bps')}) "
                f"[US={_fmt_number(us_10y, suffix='%')} | JP={_fmt_number(jp_10y, suffix='%')}]"
            ),
        ]
    )

    us_2y = macro.get("us_2y")
    jp_2y = macro.get("jp_2y")
    spread_2y = macro.get("spread_2y")
    if spread_2y is not None or us_2y is not None or jp_2y is not None:
        lines.append(
            f"• 2Y Spread     : {_fmt_number(spread_2y, suffix='%')} "
            f"[US={_fmt_number(us_2y, suffix='%')} | JP={_fmt_number(jp_2y, suffix='%')}]"
        )

    crude_price = macro.get("crude_oil_price")
    crude_5d = macro.get("crude_oil_5d_change_pct")
    tot_bias = macro.get("terms_of_trade_bias")
    if crude_price is not None or crude_5d is not None or tot_bias is not None:
        lines.append(
            f"• Crude Oil     : {_fmt_number(crude_price, decimals=2)} "
            f"(5d: {_fmt_number(crude_5d, decimals=2, suffix='%')}) "
            f"[{_enum_or_str(tot_bias, default='NEUTRAL')}]"
        )

    gotobi = _coerce_mapping(macro.get("gotobi_session"))
    if gotobi:
        is_gotobi = bool(gotobi.get("is_gotobi_day"))
        phase = _enum_or_str(gotobi.get("session_phase"), default="STANDARD")
        reason = (
            gotobi.get("gotobi_reason")
            or gotobi.get("description")
            or ("Gotobi settlement day" if is_gotobi else "Non-Gotobi day")
        )
        gotobi_flag = "YES" if is_gotobi else "NO"
        lines.append(f"• Tokyo Gotobi  : {gotobi_flag} (phase={phase}) - {reason}")

    # Risk Committee Section
    risk_regime = _enum_or_str(
        state_dict.get("risk_regime")
        or risk.get("risk_regime")
        or proposal.get("risk_regime"),
        default="TRANSITIONAL",
    )
    vix_level = risk.get("vix_level")
    intervention_score = (
        state_dict.get("intervention_risk_score")
        if state_dict.get("intervention_risk_score") is not None
        else (
            risk.get("intervention_risk_score")
            if risk.get("intervention_risk_score") is not None
            else proposal.get("intervention_risk_score")
        )
    )
    alert_level = _enum_or_str(risk.get("intervention_alert_level"), default="NONE")
    mof_tier = risk.get("highest_mof_tier")
    risk_notes = _enum_or_str(risk.get("risk_notes"), default="None")

    lines.extend(
        [
            "",
            "🛡️ Risk Committee",
            f"• Risk Regime   : {risk_regime} (VIX={_fmt_number(vix_level)})",
            (
                f"• Intervention  : score={_fmt_number(intervention_score)} "
                f"[{alert_level}] (MoF Tier={_fmt_number(mof_tier, default='0')})"
            ),
            f"• Risk Notes    : {risk_notes}",
        ]
    )

    # Rationale Section
    rationale = _enum_or_str(proposal.get("rationale"), default="No rationale provided.")
    lines.extend(
        [
            "",
            "📝 Rationale",
            rationale,
        ]
    )

    return "\n".join(lines)


def format_execution_update(
    original_text: str,
    receipt: dict[str, Any] | None = None,
    *,
    timed_out: bool = False,
) -> str:
    """
    Append a final resolution status block to the original trade proposal message
    when inline approval buttons are cleared via editMessageText.

    Args:
        original_text: The initial proposal message text sent to Telegram.
        receipt: Optional ExecutionReceipt dictionary (or model) from graph execution.
        timed_out: Whether the approval request expired due to timeout.

    Returns:
        Updated message string combining original_text and the execution receipt block.
    """
    base_text = (original_text or "").rstrip()
    receipt_dict = _coerce_mapping(receipt)

    status = _enum_or_str(receipt_dict.get("status"), default="")
    order_id = receipt_dict.get("order_id")
    action = _enum_or_str(receipt_dict.get("action"), default="HOLD")
    units = receipt_dict.get("position_size_units")
    pair = _enum_or_str(receipt_dict.get("pair"), default="USD/JPY")
    fill_price = receipt_dict.get("fill_price")
    executed_at_utc = receipt_dict.get("executed_at_utc")
    reviewer_notes = receipt_dict.get("reviewer_notes")
    notes = receipt_dict.get("notes")

    footer_lines: list[str] = [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if status == "EXECUTED" and not timed_out:
        footer_lines.append("✅ EXECUTED")
        if order_id:
            footer_lines.append(f"• Order ID      : {order_id}")
        footer_lines.append(
            f"• Fill          : {action} {units if units is not None else 0} {pair} @ {_fmt_number(fill_price)}"
        )
        if executed_at_utc:
            footer_lines.append(f"• Executed (UTC): {executed_at_utc}")
        if reviewer_notes:
            footer_lines.append(f"• Reviewer Notes: {reviewer_notes}")
        if notes:
            footer_lines.append(f"• Notes         : {notes}")
    elif timed_out:
        footer_lines.append("⏱️ EXPIRED (Auto-Rejected)")
        if order_id:
            footer_lines.append(f"• Order ID      : {order_id}")
        timeout_note = reviewer_notes or notes or "Timed out waiting for Telegram approval."
        footer_lines.append(f"• Notes         : {timeout_note}")
    else:
        footer_lines.append("❌ REJECTED (CANCELLED_REJECTED)")
        if order_id:
            footer_lines.append(f"• Order ID      : {order_id}")
        rejection_note = reviewer_notes or notes or "Rejected by reviewer."
        footer_lines.append(f"• Notes         : {rejection_note}")

    if not base_text:
        return "\n".join(footer_lines).lstrip()
    return base_text + "\n" + "\n".join(footer_lines)


def format_execution_alert(
    state: dict[str, Any],
    receipt: dict[str, Any],
) -> str:
    """
    Format a standalone fire-and-forget Telegram alert for an automatically executed
    paper trade (e.g., via ``--auto-approve``).

    Args:
        state: Shared LangGraph AgentState dictionary.
        receipt: ExecutionReceipt dictionary (or model) from ``paper_execution_node``.

    Returns:
        Formatted plain-text execution alert string.
    """
    state_dict = _coerce_mapping(state)
    receipt_dict = _coerce_mapping(receipt)
    proposal = _coerce_mapping(state_dict.get("trade_proposal"))
    risk = _coerce_mapping(state_dict.get("risk_assessment"))

    pair = (
        _enum_or_str(receipt_dict.get("pair"), default="")
        or _enum_or_str(state_dict.get("pair"), default="")
        or "USD/JPY"
    )
    action = _enum_or_str(receipt_dict.get("action"), default="HOLD")
    action_icon = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"

    units_raw = receipt_dict.get("position_size_units")
    units = int(units_raw) if isinstance(units_raw, (int, float)) else (units_raw if units_raw is not None else 0)

    fill_price = receipt_dict.get("fill_price")
    stop_loss = receipt_dict.get("stop_loss") if receipt_dict.get("stop_loss") is not None else proposal.get("stop_loss")
    take_profit = receipt_dict.get("take_profit") if receipt_dict.get("take_profit") is not None else proposal.get("take_profit")
    order_id = _enum_or_str(receipt_dict.get("order_id"), default="N/A")
    executed_at_utc = _enum_or_str(receipt_dict.get("executed_at_utc"), default="")
    risk_regime = _enum_or_str(
        state_dict.get("risk_regime")
        or risk.get("risk_regime")
        or proposal.get("risk_regime"),
        default="TRANSITIONAL",
    )
    rr_ratio = proposal.get("risk_reward_ratio")
    rationale = _enum_or_str(proposal.get("rationale"), default="")

    lines: list[str] = [
        f"⚡ Automated Paper Trade Executed ({pair})",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• Action        : {action_icon} {action} {units} {pair}",
        f"• Fill Price    : {_fmt_number(fill_price)}",
        f"• Stop Loss     : {_fmt_number(stop_loss)}",
        f"• Take Profit   : {_fmt_number(take_profit)}",
    ]
    if rr_ratio is not None:
        lines.append(f"• R:R Ratio     : {_fmt_number(rr_ratio, decimals=2)}")
    lines.extend(
        [
            f"• Risk Regime   : {risk_regime}",
            f"• Order ID      : {order_id}",
        ]
    )
    if executed_at_utc:
        lines.append(f"• Executed (UTC): {executed_at_utc}")
    if rationale:
        lines.extend(["", "📝 Rationale", rationale])
    return "\n".join(lines)


def format_summary_settings_block(settings: Any | dict[str, Any] | None = None) -> str:
    """
    Format the mandatory Summary Settings footer included at the bottom of every
    scheduled or on-demand (/summary now) market summary.

    Supports duck-typing across TelegramSettings dataclass instances, dicts, or None.

    Args:
        settings: TelegramSettings instance, dict, or None.

    Returns:
        Formatted summary settings block string.
    """
    if settings is None:
        enabled = True
        time_jst = "09:00"
    elif isinstance(settings, Mapping):
        raw_enabled = settings.get("daily_summary_enabled", True)
        enabled = True if raw_enabled is None else bool(raw_enabled)
        time_jst = str(settings.get("daily_summary_time_jst") or "09:00").strip() or "09:00"
    else:
        raw_enabled = getattr(settings, "daily_summary_enabled", True)
        enabled = True if raw_enabled is None else bool(raw_enabled)
        time_jst = str(getattr(settings, "daily_summary_time_jst", "09:00") or "09:00").strip() or "09:00"

    if enabled:
        schedule_line = f"ON (Daily at {time_jst} JST)"
    else:
        schedule_line = f"OFF (Configured time: {time_jst} JST)"

    lines = [
        "⚙️ Summary Settings",
        f"• Daily Summary Schedule: {schedule_line}",
        "• Commands: /summary on [HH:MM] | /summary off | /summary now",
    ]
    return "\n".join(lines)


def _read_ledger_entries(ledger_path: str | Path | None) -> list[dict[str, Any]]:
    """Safely load JSONL paper trade entries from ledger_path."""
    if ledger_path is None:
        return []
    path = Path(ledger_path)
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                entries.append(parsed)
        except json.JSONDecodeError:
            continue
    return entries


def format_daily_summary(
    state: dict[str, Any],
    *,
    ledger_path: str | Path | None = None,
    settings: Any | dict[str, Any] | None = None,
    lookback_hours: int = 24,
) -> str:
    """
    Format a comprehensive daily or on-demand market and paper-ledger summary digest,
    always concluding with the mandatory Summary Settings block.

    Args:
        state: Shared LangGraph AgentState dictionary.
        ledger_path: Optional path to data/paper_trades.jsonl.
        settings: Optional TelegramSettings dataclass or dict.
        lookback_hours: Lookback window in hours for recent ledger activity (default: 24).

    Returns:
        Formatted summary string ready for Telegram delivery.
    """
    state_dict = _coerce_mapping(state)
    macro = _coerce_mapping(state_dict.get("macro_assessment"))
    risk = _coerce_mapping(state_dict.get("risk_assessment"))
    proposal = _coerce_mapping(state_dict.get("trade_proposal"))

    pair = (
        _enum_or_str(state_dict.get("pair"), default="")
        or _enum_or_str(proposal.get("pair"), default="")
        or "USD/JPY"
    )
    spot_price = (
        state_dict.get("spot_price")
        if state_dict.get("spot_price") is not None
        else proposal.get("entry_price")
    )
    signal_action = _enum_or_str(proposal.get("action"), default="HOLD")

    # Resolve UTC & JST timestamps
    raw_ts = state_dict.get("timestamp_utc") or proposal.get("timestamp_utc")
    utc_dt = _parse_iso_utc(raw_ts) or datetime.now(timezone.utc)
    jst_dt = utc_dt.astimezone(JST)
    utc_str = utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    jst_str = jst_dt.strftime("%Y-%m-%d %H:%M:%S JST (+09:00)")

    lines: list[str] = [
        f"📋 {pair} Market & Portfolio Summary",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• Pair          : {pair}",
        f"• Timestamp UTC : {utc_str}",
        f"• Timestamp JST : {jst_str}",
        f"• Spot Price    : {_fmt_number(spot_price)}",
        f"• Current Signal: {signal_action}",
    ]

    # Macro Snapshot
    yield_bias = _enum_or_str(
        state_dict.get("yield_bias")
        or macro.get("yield_bias")
        or proposal.get("macro_bias"),
        default="NEUTRAL",
    )
    confidence = (
        macro.get("confidence")
        if macro.get("confidence") is not None
        else proposal.get("confidence")
    )
    spread_10y = macro.get("spread_10y")
    spread_bps = macro.get("spread_bps")
    spread_2y = macro.get("spread_2y")
    crude_price = macro.get("crude_oil_price")
    crude_5d = macro.get("crude_oil_5d_change_pct")
    tot_bias = _enum_or_str(macro.get("terms_of_trade_bias"), default="NEUTRAL")

    gotobi = _coerce_mapping(macro.get("gotobi_session"))
    if not gotobi:
        gotobi = evaluate_gotobi_session(utc_dt).to_dict()
    is_gotobi = bool(gotobi.get("is_gotobi_day"))
    gotobi_phase = _enum_or_str(gotobi.get("session_phase"), default="STANDARD")
    gotobi_reason = (
        gotobi.get("gotobi_reason")
        or gotobi.get("description")
        or ("Gotobi settlement day" if is_gotobi else "Standard session")
    )
    gotobi_label = f"{'YES' if is_gotobi else 'NO'} ({gotobi_phase}) - {gotobi_reason}"

    lines.extend(
        [
            "",
            "📊 Macro Snapshot",
            f"• Yield Bias    : {yield_bias} (confidence={_fmt_number(confidence)})",
            (
                f"• 10Y Spread    : {_fmt_number(spread_10y, suffix='%')}"
                + (f" ({_fmt_number(spread_bps, suffix=' bps')})" if spread_bps is not None else "")
            ),
            f"• 2Y Spread     : {_fmt_number(spread_2y, suffix='%')}",
            (
                f"• Crude Oil CL=F: {_fmt_number(crude_price, decimals=2)} "
                f"(5d: {_fmt_number(crude_5d, decimals=2, suffix='%')}) [{tot_bias}]"
            ),
            f"• Tokyo Gotobi  : {gotobi_label}",
        ]
    )

    # Risk Snapshot
    risk_regime = _enum_or_str(
        state_dict.get("risk_regime")
        or risk.get("risk_regime")
        or proposal.get("risk_regime"),
        default="TRANSITIONAL",
    )
    vix_level = risk.get("vix_level")
    intervention_score = (
        state_dict.get("intervention_risk_score")
        if state_dict.get("intervention_risk_score") is not None
        else (
            risk.get("intervention_risk_score")
            if risk.get("intervention_risk_score") is not None
            else proposal.get("intervention_risk_score")
        )
    )
    alert_level = _enum_or_str(risk.get("intervention_alert_level"), default="NONE")
    mof_tier = risk.get("highest_mof_tier")

    lines.extend(
        [
            "",
            "🛡️ Risk Snapshot",
            f"• Risk Regime   : {risk_regime} (VIX={_fmt_number(vix_level)})",
            (
                f"• Intervention  : score={_fmt_number(intervention_score)} "
                f"[{alert_level}] (MoF Tier={_fmt_number(mof_tier, default='0')})"
            ),
        ]
    )

    # Upcoming High-Impact Macro Catalysts
    events = macro.get("upcoming_high_impact_events") or []
    lines.extend(
        [
            "",
            f"📅 Upcoming High-Impact Macro Catalysts ({len(events)})",
        ]
    )
    if events:
        for ev in events[:5]:
            ev_dict = _coerce_mapping(ev)
            ev_ccy = _enum_or_str(ev_dict.get("currency"), default="")
            ev_name = (
                ev_dict.get("event_name")
                or ev_dict.get("event")
                or ev_dict.get("title")
                or "Macro Event"
            )
            ev_time = (
                ev_dict.get("event_timestamp_utc")
                or ev_dict.get("timestamp_utc")
                or ev_dict.get("datetime_utc")
                or ev_dict.get("time_utc")
                or ""
            )
            prefix = f"[{ev_ccy}] " if ev_ccy else ""
            suffix = f" ({ev_time})" if ev_time else ""
            lines.append(f"• {prefix}{ev_name}{suffix}")
    else:
        lines.append("• None scheduled in upcoming window.")

    # Paper Ledger Activity
    entries = _read_ledger_entries(ledger_path)
    executed_entries = [
        e for e in entries if str(e.get("status") or "EXECUTED").upper() == "EXECUTED"
    ] or entries

    cutoff_utc = utc_dt - timedelta(hours=max(1, int(lookback_hours)))
    recent_entries: list[dict[str, Any]] = []
    for entry in executed_entries:
        entry_dt = _parse_iso_utc(entry.get("executed_at_utc") or entry.get("timestamp_utc"))
        if entry_dt is not None and entry_dt >= cutoff_utc:
            recent_entries.append(entry)

    display_entries = recent_entries[-3:] if recent_entries else executed_entries[-3:]

    lines.extend(
        [
            "",
            f"📒 Paper Ledger Activity ({lookback_hours}h Lookback)",
            f"• Total Executed Trades: {len(executed_entries)} (Recent {lookback_hours}h: {len(recent_entries)})",
        ]
    )
    if display_entries:
        for item in display_entries:
            o_id = item.get("order_id") or "N/A"
            o_act = _enum_or_str(item.get("action"), default="TRADE")
            o_units = item.get("position_size_units", 0)
            o_pair = item.get("pair") or pair
            o_fill = _fmt_number(item.get("fill_price"))
            lines.append(f"• {o_id}: {o_act} {o_units} {o_pair} @ {o_fill}")
    else:
        lines.append("• No executed paper trades recorded.")

    # Mandatory Summary Settings Block
    lines.extend(
        [
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            format_summary_settings_block(settings),
        ]
    )

    return "\n".join(lines)


__all__ = [
    "build_approval_inline_keyboard",
    "format_daily_summary",
    "format_execution_update",
    "format_proposal_message",
    "format_summary_settings_block",
]
