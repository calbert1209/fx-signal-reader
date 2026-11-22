"""
USD/JPY Signal Reader - Deterministic Bracket Trade Simulator & Analytics
Task P6-03: Backtest Schemas & Bracket Simulator

Provides bar-by-bar bracket resolution (`simulate_trade_resolution`) with
spread/slippage deduction, conservative SL-first collision tie-breaking, and
time-stop/end-of-data exits, plus portfolio & regime/bias analytics
(`compute_backtest_metrics`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from fx_signal_reader.agent.graph import build_signal_graph
from fx_signal_reader.agent.state import (
    RiskRegime,
    TradeAction,
    TradeProposal,
    YieldBias,
    create_initial_state,
)
from fx_signal_reader.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestReport,
    ExitReason,
    SimulatedTrade,
)
from fx_signal_reader.core.normalizer import normalize_datetime_utc
from fx_signal_reader.core.pair_profile import get_pair_profile
from fx_signal_reader.mcp.server import get_readonly_connection, resolve_db_path


@dataclass(frozen=True, slots=True)
class _ValidatedBar:
    """Normalized OHLC bar with strictly increasing UTC timestamp."""

    ts: str
    dt: datetime
    high: float
    low: float
    close: float


def _require_positive_finite(value: Any, field_name: str) -> float:
    """Validate that value is a finite positive number (excluding booleans)."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive finite number.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive finite number.") from exc
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{field_name} must be > 0 and finite, got {value!r}.")
    return numeric


def _require_non_negative_int(value: Any, field_name: str) -> int:
    """Validate that a counter keyword argument is a non-negative integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a non-negative integer.")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0, got {value}.")
    return value


def _parse_utc_datetime_from_iso(iso_ts: str) -> datetime:
    """Parse a strict YYYY-MM-DDTHH:MM:SSZ string into an aware UTC datetime."""
    return datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _parse_utc_strict(value: Any, field_name: str) -> tuple[str, datetime]:
    """Normalize str/date/datetime input strictly and return (iso_str, aware_utc_dt)."""
    if value is None or isinstance(value, bool) or not isinstance(value, (str, date)):
        raise ValueError(f"{field_name} must be an ISO-8601 string, date, or datetime.")
    normalized = normalize_datetime_utc(value, fallback_now=False)
    return normalized, _parse_utc_datetime_from_iso(normalized)


def simulate_trade_resolution(
    proposal: TradeProposal | Mapping[str, Any],
    future_bars: Sequence[Mapping[str, Any]],
    config: BacktestConfig | Mapping[str, Any],
) -> SimulatedTrade:
    """
    Resolve an actionable USD/JPY TradeProposal deterministically against forward OHLC bars.

    Conventions:
      - Round-trip spread/slippage (`config.spread_pips * 0.01` JPY) is applied adversely
        once at entry (`effective_entry = entry_price + direction * spread_jpy`).
      - Bars with `timestamp_utc <= proposal.timestamp_utc` are skipped to prevent
        entry-bar look-ahead exits.
      - Brackets are evaluated inclusively (`BUY`: `low <= SL`, `high >= TP`;
        `SELL`: `high >= SL`, `low <= TP`) over `(entry_dt, entry_dt + max_hold_hours]`.
      - When both SL and TP trigger in the same bar, `conservative_tie_break=True`
        resolves `STOP_LOSS`; `False` resolves `TAKE_PROFIT`.
      - `r_multiple` uses planned proposal risk `abs(proposal.entry_price - proposal.stop_loss)`
        as `1R` (so spread drag appears transparently in realized R-multiples, e.g.,
        `-1.02R` on a 75-pip SL hit with 1.5-pip spread).
    """
    if isinstance(proposal, TradeProposal):
        proposal_obj = proposal
    elif isinstance(proposal, Mapping):
        proposal_obj = TradeProposal.model_validate(proposal)
    else:
        raise TypeError(f"Expected TradeProposal or Mapping, got {type(proposal).__name__}.")

    if isinstance(config, BacktestConfig):
        config_obj = config
    elif isinstance(config, Mapping):
        config_obj = BacktestConfig.model_validate(config)
    else:
        raise TypeError(f"Expected BacktestConfig or Mapping, got {type(config).__name__}.")

    if future_bars is None or isinstance(future_bars, (str, bytes, Mapping)):
        raise TypeError("future_bars must be a sequence of bar mappings.")

    bars_list = list(future_bars)
    if not bars_list:
        raise ValueError("future_bars must not be empty.")

    if not isinstance(proposal_obj.proposal_id, str) or not proposal_obj.proposal_id.strip():
        raise ValueError("proposal_id must be a non-empty string.")

    if proposal_obj.action == TradeAction.HOLD:
        raise ValueError("Cannot simulate a HOLD trade proposal.")

    entry_price = _require_positive_finite(proposal_obj.entry_price, "entry_price")
    stop_loss = _require_positive_finite(proposal_obj.stop_loss, "stop_loss")
    take_profit = _require_positive_finite(proposal_obj.take_profit, "take_profit")

    if proposal_obj.action == TradeAction.BUY:
        if not (stop_loss < entry_price < take_profit):
            raise ValueError(
                f"Invalid BUY bracket levels: stop_loss ({stop_loss}) < "
                f"entry_price ({entry_price}) < take_profit ({take_profit}) required."
            )
    elif proposal_obj.action == TradeAction.SELL:
        if not (take_profit < entry_price < stop_loss):
            raise ValueError(
                f"Invalid SELL bracket levels: take_profit ({take_profit}) < "
                f"entry_price ({entry_price}) < stop_loss ({stop_loss}) required."
            )

    units = proposal_obj.position_size_units
    if isinstance(units, bool) or units <= 0:
        raise ValueError("position_size_units must be positive.")

    entry_ts, entry_dt = _parse_utc_strict(proposal_obj.timestamp_utc, "proposal.timestamp_utc")

    pair_name = (
        getattr(config_obj, "pair", None)
        or getattr(proposal_obj, "pair", None)
        or "USD/JPY"
    )
    profile = get_pair_profile(pair_name)
    pip_size = profile.pip_size

    spread_dist = config_obj.spread_pips * pip_size
    direction = 1 if proposal_obj.action == TradeAction.BUY else -1
    effective_entry = round(entry_price + direction * spread_dist, 6)
    if not math.isfinite(effective_entry) or effective_entry <= 0.0:
        raise ValueError("Spread-adjusted entry price must be positive and finite.")

    validated_bars: list[_ValidatedBar] = []
    prev_bar_dt: datetime | None = None
    for idx, bar in enumerate(bars_list):
        if not isinstance(bar, Mapping):
            raise TypeError(f"Bar at index {idx} must be a mapping, got {type(bar).__name__}.")
        bar_ts, bar_dt = _parse_utc_strict(bar.get("timestamp_utc"), f"future_bars[{idx}].timestamp_utc")
        if prev_bar_dt is not None and bar_dt <= prev_bar_dt:
            raise ValueError("future_bars must be strictly chronologically ordered.")
        prev_bar_dt = bar_dt

        raw_close = bar.get("close") if bar.get("close") is not None else bar.get("price")
        bar_close = _require_positive_finite(raw_close, f"future_bars[{idx}].close/price")

        raw_high = bar.get("high")
        bar_high = (
            _require_positive_finite(raw_high, f"future_bars[{idx}].high")
            if raw_high is not None
            else bar_close
        )

        raw_low = bar.get("low")
        bar_low = (
            _require_positive_finite(raw_low, f"future_bars[{idx}].low")
            if raw_low is not None
            else bar_close
        )

        if bar_high < bar_low:
            raise ValueError(
                f"Bar high ({bar_high}) cannot be less than bar low ({bar_low}) at index {idx}."
            )

        validated_bars.append(
            _ValidatedBar(
                ts=bar_ts,
                dt=bar_dt,
                high=bar_high,
                low=bar_low,
                close=bar_close,
            )
        )

    post_entry_bars = [bar for bar in validated_bars if bar.dt > entry_dt]
    if not post_entry_bars:
        raise ValueError("future_bars contains no bars strictly after proposal.timestamp_utc.")

    deadline_dt = entry_dt + timedelta(hours=config_obj.max_hold_hours)
    breakeven_trigger_r = config_obj.breakeven_trigger_r
    risk_per_unit = abs(entry_price - stop_loss)
    active_sl = stop_loss
    breakeven_armed = False
    bars_held = 0
    last_in_window_bar: _ValidatedBar | None = None
    exit_ts: str | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None

    for bar in post_entry_bars:
        if bar.dt > deadline_dt:
            if last_in_window_bar is not None:
                exit_ts = last_in_window_bar.ts
                exit_price = last_in_window_bar.close
                exit_reason = ExitReason.TIME_STOP
                break
            bars_held = 1
            exit_ts = bar.ts
            exit_price = bar.close
            exit_reason = ExitReason.TIME_STOP
            break

        bars_held += 1
        last_in_window_bar = bar

        if proposal_obj.action == TradeAction.BUY:
            sl_hit = bar.low <= active_sl
            tp_hit = bar.high >= take_profit
        else:
            sl_hit = bar.high >= active_sl
            tp_hit = bar.low <= take_profit

        if sl_hit and tp_hit:
            if config_obj.conservative_tie_break:
                exit_reason = ExitReason.BREAKEVEN if breakeven_armed else ExitReason.STOP_LOSS
                exit_price = active_sl
            else:
                exit_reason = ExitReason.TAKE_PROFIT
                exit_price = take_profit
            exit_ts = bar.ts
            break

        if sl_hit:
            exit_reason = ExitReason.BREAKEVEN if breakeven_armed else ExitReason.STOP_LOSS
            exit_price = active_sl
            exit_ts = bar.ts
            break

        if tp_hit:
            exit_reason = ExitReason.TAKE_PROFIT
            exit_price = take_profit
            exit_ts = bar.ts
            break

        if breakeven_trigger_r is not None and not breakeven_armed:
            if (
                proposal_obj.action == TradeAction.BUY
                and bar.high >= effective_entry + breakeven_trigger_r * risk_per_unit
            ) or (
                proposal_obj.action == TradeAction.SELL
                and bar.low <= effective_entry - breakeven_trigger_r * risk_per_unit
            ):
                active_sl = effective_entry
                breakeven_armed = True

        if bar.dt == deadline_dt:
            exit_reason = ExitReason.TIME_STOP
            exit_price = bar.close
            exit_ts = bar.ts
            break

    if exit_reason is None:
        assert last_in_window_bar is not None
        exit_reason = ExitReason.END_OF_DATA
        exit_price = last_in_window_bar.close
        exit_ts = last_in_window_bar.ts

    assert exit_ts is not None and exit_price is not None

    price_delta = direction * (exit_price - effective_entry)
    pnl_pips = round(price_delta / pip_size, 2)
    if profile.is_quote_usd:
        pnl_usd = round(price_delta * units, 4)
        pnl_jpy = 0.0
    else:
        pnl_jpy = round(price_delta * units, 2)
        pnl_usd = round(pnl_jpy / exit_price, 4)

    r_multiple = round(price_delta / risk_per_unit, 4)

    return SimulatedTrade(
        trade_id=f"sim-{proposal_obj.proposal_id}",
        proposal_id=proposal_obj.proposal_id,
        pair=profile.pair,
        entry_time_utc=entry_ts,
        exit_time_utc=exit_ts,
        action=proposal_obj.action,
        position_size_units=units,
        entry_price=effective_entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl_pips=pnl_pips,
        pnl_jpy=pnl_jpy,
        pnl_usd=pnl_usd,
        r_multiple=r_multiple,
        macro_bias=proposal_obj.macro_bias,
        risk_regime=proposal_obj.risk_regime,
        intervention_risk_score=proposal_obj.intervention_risk_score,
        bars_held=bars_held,
    )


def _compute_bucket_stats(bucket_trades: Sequence[SimulatedTrade]) -> dict[str, Any]:
    """Compute JSON-safe summary statistics for a regime or bias bucket."""
    count = len(bucket_trades)
    if count == 0:
        return {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "gross_profit_usd": 0.0,
            "gross_loss_usd": 0.0,
            "net_pnl_usd": 0.0,
            "avg_r_multiple": 0.0,
            "profit_factor": None,
        }

    wins = sum(1 for t in bucket_trades if t.pnl_usd > 0.0)
    losses = sum(1 for t in bucket_trades if t.pnl_usd <= 0.0)
    gross_profit = round(sum(t.pnl_usd for t in bucket_trades if t.pnl_usd > 0.0), 4)
    gross_loss = round(abs(sum(t.pnl_usd for t in bucket_trades if t.pnl_usd < 0.0)), 4)
    net_pnl = round(sum(t.pnl_usd for t in bucket_trades), 4)
    win_rate = round((wins / count) * 100.0, 4)
    avg_r = round(sum(t.r_multiple for t in bucket_trades) / count, 4)
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0.0 else None

    return {
        "count": count,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "gross_profit_usd": gross_profit,
        "gross_loss_usd": gross_loss,
        "net_pnl_usd": net_pnl,
        "avg_r_multiple": avg_r,
        "profit_factor": profit_factor,
    }


def _compute_sharpe_ratio(
    ordered_trades: Sequence[SimulatedTrade],
    config: BacktestConfig,
) -> float | None:
    """
    Compute JSON-safe annualized Sharpe ratio.

    Returns `None` when fewer than 2 trades exist and `0.0` when return standard
    deviation is zero. When `config.start_utc` and `config.end_utc` cover at least
    2 trading days and all trade exits fall within `[start_date, end_date]`, uses
    daily equity returns annualized by `sqrt(252)`. Otherwise uses per-trade returns
    `pnl_usd / initial_capital_usd` annualized by calendar-time trade frequency.
    """
    if len(ordered_trades) < 2:
        return None

    initial_capital = float(config.initial_capital_usd)

    if config.start_utc is not None and config.end_utc is not None:
        start_date = _parse_utc_datetime_from_iso(config.start_utc).date()
        end_date = _parse_utc_datetime_from_iso(config.end_utc).date()
        trade_exit_dates = {
            _parse_utc_datetime_from_iso(t.exit_time_utc).date() for t in ordered_trades
        }
        if all(start_date <= d <= end_date for d in trade_exit_dates):
            trading_days: list[date] = []
            cur_date = start_date
            while cur_date <= end_date:
                if cur_date.weekday() < 5 or cur_date in trade_exit_dates:
                    trading_days.append(cur_date)
                cur_date += timedelta(days=1)

            if len(trading_days) >= 2:
                daily_pnl: dict[date, float] = {}
                for trade in ordered_trades:
                    exit_day = _parse_utc_datetime_from_iso(trade.exit_time_utc).date()
                    daily_pnl[exit_day] = daily_pnl.get(exit_day, 0.0) + trade.pnl_usd

                running_equity = initial_capital
                daily_returns: list[float] = []
                for day in trading_days:
                    day_pnl = daily_pnl.get(day, 0.0)
                    ret = (day_pnl / running_equity) if running_equity > 0.0 else 0.0
                    daily_returns.append(ret)
                    running_equity += day_pnl

                mean_ret = sum(daily_returns) / len(daily_returns)
                var = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
                std_ret = math.sqrt(var)
                if std_ret == 0.0:
                    return 0.0
                sharpe = round((mean_ret / std_ret) * math.sqrt(252.0), 4)
                return sharpe if math.isfinite(sharpe) else None

    per_trade_returns = [t.pnl_usd / initial_capital for t in ordered_trades]
    mean_ret = sum(per_trade_returns) / len(per_trade_returns)
    var = sum((r - mean_ret) ** 2 for r in per_trade_returns) / (len(per_trade_returns) - 1)
    std_ret = math.sqrt(var)
    if std_ret == 0.0:
        return 0.0

    first_entry_dt = min(_parse_utc_datetime_from_iso(t.entry_time_utc) for t in ordered_trades)
    last_exit_dt = max(_parse_utc_datetime_from_iso(t.exit_time_utc) for t in ordered_trades)
    span_days = max(1.0, (last_exit_dt - first_entry_dt).total_seconds() / 86400.0)
    trades_per_year = len(ordered_trades) * (365.25 / span_days)
    sharpe = round((mean_ret / std_ret) * math.sqrt(trades_per_year), 4)
    return sharpe if math.isfinite(sharpe) else None


def compute_backtest_metrics(
    trades: Sequence[SimulatedTrade | Mapping[str, Any]],
    config: BacktestConfig | Mapping[str, Any],
    *,
    total_steps: int = 0,
    total_signals_actionable: int = 0,
    total_holds: int = 0,
    total_vetoes: int = 0,
    trades_skipped_overlap: int = 0,
    data_coverage: Mapping[str, Any] | None = None,
) -> tuple[BacktestMetrics, list[dict[str, Any]]]:
    """
    Compute chronological equity curve, drawdown, risk/return metrics, and
    regime/bias breakdowns across resolved `SimulatedTrade` records.
    """
    if isinstance(config, BacktestConfig):
        config_obj = config
    elif isinstance(config, Mapping):
        config_obj = BacktestConfig.model_validate(config)
    else:
        raise TypeError(f"Expected BacktestConfig or Mapping, got {type(config).__name__}.")

    validated_total_steps = _require_non_negative_int(total_steps, "total_steps")
    validated_actionable = _require_non_negative_int(
        total_signals_actionable, "total_signals_actionable"
    )
    validated_holds = _require_non_negative_int(total_holds, "total_holds")
    validated_vetoes = _require_non_negative_int(total_vetoes, "total_vetoes")
    validated_skipped = _require_non_negative_int(
        trades_skipped_overlap, "trades_skipped_overlap"
    )

    if data_coverage is not None and not isinstance(data_coverage, Mapping):
        raise TypeError("data_coverage must be a mapping or None.")
    coverage_dict = dict(data_coverage) if data_coverage is not None else {}

    validated_trades: list[SimulatedTrade] = []
    for item in trades:
        if isinstance(item, SimulatedTrade):
            validated_trades.append(item)
        elif isinstance(item, Mapping):
            validated_trades.append(SimulatedTrade.model_validate(item))
        else:
            raise TypeError(
                f"Expected SimulatedTrade or Mapping, got {type(item).__name__}."
            )

    ordered_trades = sorted(
        validated_trades,
        key=lambda t: (t.exit_time_utc, t.entry_time_utc, t.trade_id),
    )

    initial_capital = float(config_obj.initial_capital_usd)
    equity_curve: list[dict[str, Any]] = []

    if config_obj.start_utc is not None or ordered_trades:
        anchor_ts = (
            config_obj.start_utc
            if config_obj.start_utc is not None
            else ordered_trades[0].entry_time_utc
        )
        equity_curve.append(
            {
                "timestamp_utc": anchor_ts,
                "equity_usd": round(initial_capital, 4),
                "trade_id": None,
                "pnl_usd": 0.0,
                "drawdown_usd": 0.0,
                "drawdown_pct": 0.0,
            }
        )

    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown_usd = 0.0
    max_drawdown_pct = 0.0

    for trade in ordered_trades:
        equity += trade.pnl_usd
        peak_equity = max(peak_equity, equity)
        dd_usd = max(0.0, peak_equity - equity)
        dd_pct = min(100.0, (dd_usd / peak_equity) * 100.0) if peak_equity > 0.0 else 0.0
        max_drawdown_usd = max(max_drawdown_usd, dd_usd)
        max_drawdown_pct = max(max_drawdown_pct, dd_pct)
        equity_curve.append(
            {
                "timestamp_utc": trade.exit_time_utc,
                "equity_usd": round(equity, 4),
                "trade_id": trade.trade_id,
                "pnl_usd": round(trade.pnl_usd, 4),
                "drawdown_usd": round(dd_usd, 4),
                "drawdown_pct": round(dd_pct, 4),
            }
        )

    trades_executed = len(ordered_trades)
    wins = sum(1 for t in ordered_trades if t.pnl_usd > 0.0)
    losses = sum(1 for t in ordered_trades if t.pnl_usd <= 0.0)
    win_rate_pct = round((wins / trades_executed) * 100.0, 4) if trades_executed > 0 else 0.0
    gross_profit_usd = round(sum(t.pnl_usd for t in ordered_trades if t.pnl_usd > 0.0), 4)
    gross_loss_usd = round(abs(sum(t.pnl_usd for t in ordered_trades if t.pnl_usd < 0.0)), 4)
    net_pnl_usd = round(sum(t.pnl_usd for t in ordered_trades), 4)
    return_pct = round((net_pnl_usd / initial_capital) * 100.0, 4)
    profit_factor = (
        round(gross_profit_usd / gross_loss_usd, 4) if gross_loss_usd > 0.0 else None
    )
    expectancy_usd = (
        round(net_pnl_usd / trades_executed, 4) if trades_executed > 0 else 0.0
    )
    avg_r_multiple = (
        round(sum(t.r_multiple for t in ordered_trades) / trades_executed, 4)
        if trades_executed > 0
        else 0.0
    )
    sharpe_ratio = _compute_sharpe_ratio(ordered_trades, config_obj)

    breakdown_by_regime = {
        regime.value: _compute_bucket_stats(
            [t for t in ordered_trades if t.risk_regime == regime]
        )
        for regime in RiskRegime
    }
    breakdown_by_bias = {
        bias.value: _compute_bucket_stats(
            [t for t in ordered_trades if t.macro_bias == bias]
        )
        for bias in YieldBias
    }

    metrics = BacktestMetrics(
        total_steps=validated_total_steps,
        total_signals_actionable=validated_actionable,
        total_holds=validated_holds,
        total_vetoes=validated_vetoes,
        trades_executed=trades_executed,
        trades_skipped_overlap=validated_skipped,
        wins=wins,
        losses=losses,
        win_rate_pct=win_rate_pct,
        gross_profit_usd=gross_profit_usd,
        gross_loss_usd=gross_loss_usd,
        net_pnl_usd=net_pnl_usd,
        return_pct=return_pct,
        profit_factor=profit_factor,
        expectancy_usd=expectancy_usd,
        avg_r_multiple=avg_r_multiple,
        max_drawdown_usd=round(max_drawdown_usd, 4),
        max_drawdown_pct=round(max_drawdown_pct, 4),
        sharpe_ratio=sharpe_ratio,
        breakdown_by_regime=breakdown_by_regime,
        breakdown_by_bias=breakdown_by_bias,
        data_coverage=coverage_dict,
    )
    return metrics, equity_curve


def _load_pair_ohlc_bars(
    db_path: Path,
    symbol: str = "JPY=X",
    start_utc: str | None = None,
    end_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Load chronological OHLC bars for symbol from SQLite within optional UTC bounds."""
    clauses = ["symbol = ?"]
    params: list[Any] = [symbol]
    if start_utc is not None:
        clauses.append("timestamp_utc >= ?")
        params.append(start_utc)
    if end_utc is not None:
        clauses.append("timestamp_utc <= ?")
        params.append(end_utc)

    query = f"""
        SELECT timestamp_utc, price, open, high, low, volume
        FROM market_ticks
        WHERE {' AND '.join(clauses)}
        ORDER BY timestamp_utc ASC
    """
    conn = get_readonly_connection(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    bars: list[dict[str, Any]] = []
    for row in rows:
        close_val = float(row["price"])
        bars.append(
            {
                "timestamp_utc": normalize_datetime_utc(row["timestamp_utc"], fallback_now=False),
                "price": close_val,
                "close": close_val,
                "open": float(row["open"]) if row["open"] is not None else close_val,
                "high": float(row["high"]) if row["high"] is not None else close_val,
                "low": float(row["low"]) if row["low"] is not None else close_val,
                "volume": float(row["volume"]) if row["volume"] is not None else 0.0,
            }
        )
    return bars


def _load_jpy_ohlc_bars(
    db_path: Path,
    start_utc: str | None,
    end_utc: str | None,
) -> list[dict[str, Any]]:
    """Load chronological JPY=X OHLC bars from SQLite within optional UTC bounds (wrapper)."""
    return _load_pair_ohlc_bars(db_path, symbol="JPY=X", start_utc=start_utc, end_utc=end_utc)


def _sample_evaluation_indices(
    bars: Sequence[Mapping[str, Any]],
    step_hours: int,
) -> list[int]:
    """Select bar indices to evaluate based on `step_hours` (0 evaluates every bar)."""
    if len(bars) < 2:
        return []
    if step_hours == 0:
        return list(range(len(bars) - 1))

    selected: list[int] = []
    next_eval_dt: datetime | None = None
    step_delta = timedelta(hours=step_hours)
    for idx in range(len(bars) - 1):
        bar_dt = _parse_utc_datetime_from_iso(str(bars[idx]["timestamp_utc"]))
        if next_eval_dt is None or bar_dt >= next_eval_dt:
            selected.append(idx)
            next_eval_dt = bar_dt + step_delta
    return selected


def run_backtest(
    db_path: str | Path | None = None,
    config: BacktestConfig | Mapping[str, Any] | None = None,
) -> BacktestReport:
    """
    Execute an end-to-end point-in-time historical backtest over SQLite data.

    Replays the compiled LangGraph workflow (`point_in_time=True`, `write_ledger=False`)
    across sampled `JPY=X` timestamps, enforces concurrency rules, simulates forward
    bracket exits, and computes aggregate metrics and coverage diagnostics.
    """
    if config is None:
        config_obj = BacktestConfig()
    elif isinstance(config, BacktestConfig):
        config_obj = config
    elif isinstance(config, Mapping):
        config_obj = BacktestConfig.model_validate(config)
    else:
        raise TypeError(f"Expected BacktestConfig or Mapping, got {type(config).__name__}.")

    resolved_db = resolve_db_path(db_path)
    profile = get_pair_profile(config_obj.pair)
    bars = _load_pair_ohlc_bars(
        resolved_db,
        symbol=profile.spot_symbol,
        start_utc=config_obj.start_utc,
        end_utc=config_obj.end_utc,
    )

    if not bars:
        empty_coverage = {
            "bars": 0,
            "jpy_bars": 0,
            "steps_evaluated": 0,
            "yield_spread_pct": 0.0,
            "spread_2y_pct": 0.0,
            "oil_pct": 0.0,
            "gotobi_pct": 0.0,
            "vix_pct": 0.0,
            "macro_events_pct": 0.0,
            "mof_alerts_pct": 0.0,
        }
        metrics, equity_curve = compute_backtest_metrics(
            [],
            config_obj,
            data_coverage=empty_coverage,
        )
        return BacktestReport(
            config=config_obj,
            metrics=metrics,
            equity_curve=equity_curve,
            trades=[],
        )

    effective_config = config_obj.model_copy(
        update={
            "start_utc": config_obj.start_utc or str(bars[0]["timestamp_utc"]),
            "end_utc": config_obj.end_utc or str(bars[-1]["timestamp_utc"]),
        }
    )

    eval_indices = _sample_evaluation_indices(bars, effective_config.step_hours)
    graph = build_signal_graph(
        db_path=str(resolved_db),
        point_in_time=True,
        mof_lookback_hours=effective_config.mof_lookback_hours,
        interrupt_on_actionable=False,
        write_ledger=False,
    )

    trades: list[SimulatedTrade] = []
    active_exit_dt: datetime | None = None
    total_signals_actionable = 0
    total_holds = 0
    total_vetoes = 0
    trades_skipped_overlap = 0

    spread_hits = 0
    spread_2y_hits = 0
    oil_hits = 0
    gotobi_hits = 0
    vix_hits = 0
    macro_hits = 0
    mof_hits = 0

    def _get_val(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, Mapping):
            return obj.get(key)
        return getattr(obj, key, None)

    for step_num, idx in enumerate(eval_indices, start=1):
        bar = bars[idx]
        step_ts = str(bar["timestamp_utc"])
        step_dt = _parse_utc_datetime_from_iso(step_ts)
        run_id = f"bt-{step_num:05d}"

        initial_state = create_initial_state(
            run_id=run_id,
            timestamp_utc=step_ts,
            pair=effective_config.pair,
            account_capital_usd=effective_config.initial_capital_usd,
            risk_fraction=effective_config.risk_fraction,
            sl_pips=effective_config.sl_pips,
            tp_pips=effective_config.tp_pips,
            use_atr=effective_config.use_atr,
            allowed_regimes=[r.value for r in effective_config.allowed_regimes],
            regime_overrides=effective_config.regime_overrides,
        )
        final_state = graph.invoke(
            initial_state,
            config={"configurable": {"thread_id": run_id}},
        )

        macro_assessment = final_state.get("macro_assessment") or {}
        risk_assessment = final_state.get("risk_assessment") or {}
        if _get_val(macro_assessment, "spread_10y") is not None:
            spread_hits += 1
        if _get_val(macro_assessment, "spread_2y") is not None:
            spread_2y_hits += 1
        if _get_val(macro_assessment, "crude_oil_price") is not None:
            oil_hits += 1
        if _get_val(macro_assessment, "gotobi_session") is not None:
            gotobi_hits += 1
        if _get_val(risk_assessment, "vix_level") is not None:
            vix_hits += 1
        if _get_val(macro_assessment, "upcoming_high_impact_events"):
            macro_hits += 1
        if _get_val(risk_assessment, "flagged_headlines"):
            mof_hits += 1
        if _get_val(risk_assessment, "veto_trade"):
            total_vetoes += 1

        raw_proposal = final_state.get("trade_proposal")
        if raw_proposal is None:
            total_holds += 1
            continue

        proposal = TradeProposal.model_validate(raw_proposal)
        if proposal.action == TradeAction.HOLD:
            total_holds += 1
            continue

        total_signals_actionable += 1
        if (
            not effective_config.allow_overlapping_trades
            and active_exit_dt is not None
            and step_dt < active_exit_dt
        ):
            trades_skipped_overlap += 1
            continue

        future_bars = bars[idx + 1 :]
        if not future_bars:
            continue

        trade = simulate_trade_resolution(proposal, future_bars, effective_config)
        trades.append(trade)
        if not effective_config.allow_overlapping_trades:
            active_exit_dt = _parse_utc_datetime_from_iso(trade.exit_time_utc)

    total_steps = len(eval_indices)
    denom = float(total_steps) if total_steps > 0 else 1.0
    data_coverage = {
        "bars": len(bars),
        "jpy_bars": len(bars),
        "steps_evaluated": total_steps,
        "yield_spread_pct": round((spread_hits / denom) * 100.0, 2) if total_steps else 0.0,
        "spread_2y_pct": round((spread_2y_hits / denom) * 100.0, 2) if total_steps else 0.0,
        "oil_pct": round((oil_hits / denom) * 100.0, 2) if total_steps else 0.0,
        "gotobi_pct": round((gotobi_hits / denom) * 100.0, 2) if total_steps else 0.0,
        "vix_pct": round((vix_hits / denom) * 100.0, 2) if total_steps else 0.0,
        "macro_events_pct": round((macro_hits / denom) * 100.0, 2) if total_steps else 0.0,
        "mof_alerts_pct": round((mof_hits / denom) * 100.0, 2) if total_steps else 0.0,
    }

    metrics, equity_curve = compute_backtest_metrics(
        trades,
        effective_config,
        total_steps=total_steps,
        total_signals_actionable=total_signals_actionable,
        total_holds=total_holds,
        total_vetoes=total_vetoes,
        trades_skipped_overlap=trades_skipped_overlap,
        data_coverage=data_coverage,
    )
    return BacktestReport(
        config=effective_config,
        metrics=metrics,
        equity_curve=equity_curve,
        trades=trades,
    )


__all__ = [
    "compute_backtest_metrics",
    "run_backtest",
    "simulate_trade_resolution",
]
