#!/usr/bin/env python3
"""
CLI Runner for USD/JPY Signal Reader Historical Backtesting (`fx-backtest`).
Task P6-04: Backtest CLI & Package Integration
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

from fx_signal_reader.backtest import (
    DEFAULT_BACKTEST_DB_PATH,
    BacktestConfig,
    BacktestReport,
    backfill_historical_data,
    run_backtest,
)
from fx_signal_reader.core.pair_profile import get_pair_db_path, get_pair_profile
from fx_signal_reader.mcp.server import resolve_db_path


def _resolve_target_db(
    db_path: str | None,
    *,
    backfill: bool,
    pair: str = "USD/JPY",
) -> Path:
    """Resolve target SQLite DB path (isolated `backtest_signals_{slug}.db` when `--backfill` is set)."""
    if db_path is not None:
        return Path(db_path).resolve()
    profile = get_pair_profile(pair)
    if backfill:
        if profile.pair == "USD/JPY":
            return DEFAULT_BACKTEST_DB_PATH.resolve()
        return (DEFAULT_BACKTEST_DB_PATH.parent / f"backtest_signals_{profile.symbol_slug}.db").resolve()
    if profile.pair == "USD/JPY":
        return resolve_db_path(None)
    return get_pair_db_path(profile)


def export_trades_file(report: BacktestReport, export_path: str | Path) -> Path:
    """Export simulated trades to `.jsonl` or `.csv` based on file extension."""
    target = Path(export_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [trade.model_dump(mode="json") for trade in report.trades]

    if target.suffix.lower() == ".jsonl":
        with target.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        return target

    fieldnames = (
        list(rows[0].keys())
        if rows
        else [
            "trade_id",
            "proposal_id",
            "entry_time_utc",
            "exit_time_utc",
            "action",
            "position_size_units",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "exit_reason",
            "pnl_pips",
            "pnl_jpy",
            "pnl_usd",
            "r_multiple",
            "macro_bias",
            "risk_regime",
            "intervention_risk_score",
            "bars_held",
        ]
    )
    with target.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return target


def format_backtest_summary(
    report: BacktestReport,
    *,
    db_path: Path,
    backfill_summary: dict[str, Any] | None = None,
) -> str:
    """Format a human-readable summary of backtest metrics, coverage, and breakdowns."""
    cfg = report.config
    pair_label = cfg.pair or "USD/JPY"
    m = report.metrics
    cov = m.data_coverage
    pf_text = f"{m.profit_factor:.4f}" if m.profit_factor is not None else "n/a"
    sharpe_text = f"{m.sharpe_ratio:.4f}" if m.sharpe_ratio is not None else "n/a"
    regimes_str = ",".join(r.value for r in cfg.allowed_regimes)
    bracket_str = (
        "ATR-14 (1.0x/2.0x)"
        if cfg.use_atr
        else f"fixed (SL={cfg.sl_pips}p, TP={cfg.tp_pips}p)"
    )
    be_str = f"+{cfg.breakeven_trigger_r}R" if cfg.breakeven_trigger_r is not None else "off"

    lines = [
        "=" * 74,
        f"{pair_label} Signal Reader - Historical Backtest Report",
        "=" * 74,
        f"Database           : {db_path}",
        f"Window (UTC)       : {cfg.start_utc or 'n/a'} -> {cfg.end_utc or 'n/a'}",
        (
            f"Config             : step={cfg.step_hours}h | max_hold={cfg.max_hold_hours}h | "
            f"spread={cfg.spread_pips} pips | overlap={cfg.allow_overlapping_trades} | "
            f"conservative_tie={cfg.conservative_tie_break} | mof_lookback={cfg.mof_lookback_hours}h"
        ),
        f"Strategy           : regimes={regimes_str} | risk={cfg.risk_fraction * 100:.1f}% | brackets={bracket_str} | breakeven={be_str}",
    ]

    if backfill_summary is not None:
        lines.append(
            f"Backfill           : ticks={backfill_summary.get('market_ticks', {}).get('rows_inserted', 0)} | "
            f"spreads={backfill_summary.get('yield_spreads', {}).get('rows_inserted', 0)} "
            f"(interval={backfill_summary.get('interval')})"
        )

    lines.extend(
        [
            "-" * 74,
            "[1] Signal & Execution Summary",
            (
                f"    Steps / Signals: steps={m.total_steps} | actionable={m.total_signals_actionable} | "
                f"holds={m.total_holds} | vetoes={m.total_vetoes}"
            ),
            (
                f"    Trades         : executed={m.trades_executed} | skipped_overlap={m.trades_skipped_overlap} | "
                f"wins={m.wins} | losses={m.losses} | win_rate={m.win_rate_pct:.2f}%"
            ),
            "-" * 74,
            "[2] Portfolio Performance",
            (
                f"    Net PnL        : ${m.net_pnl_usd:,.2f} ({m.return_pct:+.2f}%) | "
                f"Gross Profit=${m.gross_profit_usd:,.2f} | Gross Loss=${m.gross_loss_usd:,.2f}"
            ),
            (
                f"    Risk Metrics   : Profit Factor={pf_text} | Expectancy=${m.expectancy_usd:,.2f} | "
                f"Avg R={m.avg_r_multiple:+.4f} | Sharpe={sharpe_text}"
            ),
            f"    Max Drawdown   : ${m.max_drawdown_usd:,.2f} ({m.max_drawdown_pct:.2f}%)",
            "-" * 74,
            "[3] Data Coverage Diagnostics",
            (
                f"    {pair_label} Bars={cov.get('bars', cov.get('jpy_bars', 0))} | Yield Spread={cov.get('yield_spread_pct', 0.0)}% | "
                + (f"2Y Spread={cov.get('spread_2y_pct', 0.0)}% | " if "spread_2y_pct" in cov else "")
                + (f"Oil={cov.get('oil_pct', 0.0)}% | " if "oil_pct" in cov else "")
                + (f"Gotobi={cov.get('gotobi_pct', 0.0)}% | " if "gotobi_pct" in cov else "")
                + f"VIX={cov.get('vix_pct', 0.0)}% | Macro Events={cov.get('macro_events_pct', 0.0)}% | "
                + f"MoF Alerts={cov.get('mof_alerts_pct', 0.0)}%"
            ),
        ]
    )

    if m.breakdown_by_regime:
        lines.append("-" * 74)
        lines.append("[4] Breakdown by Risk Regime")
        for regime_name, stats in m.breakdown_by_regime.items():
            lines.append(
                f"    {regime_name:<14}: trades={stats.get('count', 0)} | "
                f"win_rate={stats.get('win_rate_pct', 0.0):.2f}% | "
                f"net_pnl=${stats.get('net_pnl_usd', 0.0):,.2f} | "
                f"avg_R={stats.get('avg_r_multiple', 0.0):+.4f}"
            )

    if m.breakdown_by_bias:
        lines.append("-" * 74)
        lines.append("[5] Breakdown by Macro Yield Bias")
        for bias_name, stats in m.breakdown_by_bias.items():
            lines.append(
                f"    {bias_name:<14}: trades={stats.get('count', 0)} | "
                f"win_rate={stats.get('win_rate_pct', 0.0):.2f}% | "
                f"net_pnl=${stats.get('net_pnl_usd', 0.0):,.2f} | "
                f"avg_R={stats.get('avg_r_multiple', 0.0):+.4f}"
            )

    lines.append("=" * 74)
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser for `fx-backtest`."""
    parser = argparse.ArgumentParser(
        description="Run historical point-in-time backtesting for the FX Signal Reader pipeline."
    )
    parser.add_argument(
        "--pair",
        type=str,
        default=os.getenv("FX_PAIR", "USD/JPY"),
        help="Currency pair to backtest (default: USD/JPY or FX_PAIR env var).",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help=(
            "SQLite DB path (defaults to data/backtest_signals_{symbol_slug}.db when --backfill is passed, "
            "or FX_DB_PATH / data/{symbol_slug}_signals.db otherwise)."
        ),
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Run historical data backfill before executing the backtest simulation.",
    )
    parser.add_argument(
        "--interval",
        choices=("15m", "1h", "1d"),
        default="1h",
        help="Yahoo Finance bar interval used with --backfill (default: 1h).",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Optional ISO-8601 start date or UTC timestamp (e.g. 2026-09-18).",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Optional ISO-8601 end date or UTC timestamp (e.g. 2026-11-18).",
    )
    parser.add_argument(
        "--step-hours",
        type=int,
        default=4,
        help="Evaluation cadence in hours between signal checks (0 evaluates every bar; default: 4).",
    )
    parser.add_argument(
        "--max-hold-hours",
        type=int,
        default=120,
        help="Maximum holding period in hours before TIME_STOP exit (default: 120).",
    )
    parser.add_argument(
        "--spread-pips",
        type=float,
        default=1.5,
        help="Round-trip spread/slippage cost in pips deducted at entry (default: 1.5).",
    )
    parser.add_argument(
        "--allow-overlap",
        action="store_true",
        help="Allow opening new trades while a previous bracket is still open.",
    )
    parser.add_argument(
        "--optimistic-tie-break",
        action="store_true",
        help="Resolve same-bar SL/TP collisions optimistically (TP first) instead of conservative SL first.",
    )
    parser.add_argument(
        "--mof-lookback-hours",
        type=int,
        default=48,
        help="Lookback window in hours for MoF/BoJ intervention headlines (default: 48).",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=10000.0,
        help="Initial portfolio capital in USD (default: 10000.0).",
    )
    parser.add_argument(
        "--risk-pct",
        type=float,
        default=None,
        help="Account risk percentage per trade (e.g. 2.0 for 2.0%; default: 1.0%).",
    )
    parser.add_argument(
        "--allowed-regimes",
        type=str,
        default="RISK_ON,TRANSITIONAL",
        help="Comma-separated permitted risk regimes (default: RISK_ON,TRANSITIONAL).",
    )
    parser.add_argument(
        "--breakeven-r",
        type=float,
        default=None,
        help="Optional unrealized R-multiple threshold to ratchet Stop Loss to breakeven (default: None / disabled).",
    )
    parser.add_argument(
        "--no-breakeven",
        action="store_true",
        help="Disable breakeven Stop Loss ratcheting.",
    )
    parser.add_argument(
        "--sl-pips",
        type=float,
        default=None,
        help="Fixed Stop Loss distance in pips when --use-atr is disabled (default: 75.0 for USD/JPY, 50.0 for EUR/USD).",
    )
    parser.add_argument(
        "--tp-pips",
        type=float,
        default=None,
        help="Fixed Take Profit distance in pips when --use-atr is disabled (default: 150.0 for USD/JPY, 100.0 for EUR/USD).",
    )
    parser.add_argument(
        "--use-atr",
        action="store_true",
        help="Enable dynamic ATR-14 Stop Loss (1.0x ATR) and Take Profit (2.0x ATR) brackets.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the complete BacktestReport as JSON.",
    )
    parser.add_argument(
        "--export-trades",
        type=str,
        default=None,
        help="Optional output path (.csv or .jsonl) to export simulated trades.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `fx-backtest`."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    target_db = _resolve_target_db(args.db_path, backfill=args.backfill, pair=args.pair)
    backfill_summary: dict[str, Any] | None = None
    if args.backfill:
        backfill_summary = backfill_historical_data(
            db_path=target_db,
            start_date=args.start,
            end_date=args.end,
            interval=args.interval,
            pair=args.pair,
        )

    breakeven_trigger_r = None if args.no_breakeven else args.breakeven_r
    config_kwargs: dict[str, Any] = {
        "pair": args.pair,
        "start_utc": args.start,
        "end_utc": args.end,
        "step_hours": args.step_hours,
        "max_hold_hours": args.max_hold_hours,
        "spread_pips": args.spread_pips,
        "allow_overlapping_trades": args.allow_overlap,
        "conservative_tie_break": not args.optimistic_tie_break,
        "mof_lookback_hours": args.mof_lookback_hours,
        "initial_capital_usd": args.initial_capital,
        "breakeven_trigger_r": breakeven_trigger_r,
        "use_atr": args.use_atr,
        "allowed_regimes": args.allowed_regimes,
    }
    if args.sl_pips is not None:
        config_kwargs["sl_pips"] = args.sl_pips
    if args.tp_pips is not None:
        config_kwargs["tp_pips"] = args.tp_pips
    if args.risk_pct is not None:
        config_kwargs["risk_fraction"] = args.risk_pct / 100.0

    config = BacktestConfig(**config_kwargs)
    report = run_backtest(db_path=target_db, config=config)

    if args.export_trades:
        export_trades_file(report, args.export_trades)

    if args.json:
        payload = report.model_dump(mode="json")
        if backfill_summary is not None:
            payload["backfill_summary"] = backfill_summary
        print(json.dumps(payload, indent=2))
    else:
        print(format_backtest_summary(report, db_path=target_db, backfill_summary=backfill_summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
