#!/usr/bin/env python3
"""
CLI Runner for the USD/JPY Signal Reader LangGraph Multi-Agent Pipeline.
Task P3-05: Approval Gate and Paper Execution

Executes:
  macro_analyst -> risk_committee -> human_approval_gate -> paper_execution / finalize_hold

Supports:
  - Interactive human-in-the-loop approval prompt (when TTY and actionable)
  - Non-interactive `--auto-approve` or `--reject` flags with `--reviewer-notes`
  - Clean structured text report or `--json` output
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

from fx_signal_reader.agent.graph import (
    DEFAULT_LEDGER_PATH,
    build_signal_graph,
    resume_with_decision,
)
from fx_signal_reader.agent.state import create_initial_state, parse_regime_overrides_arg
from fx_signal_reader.core.pair_profile import get_pair_db_path, get_pair_profile
from fx_signal_reader.notifications import (
    DEFAULT_SETTINGS_PATH,
    TelegramAPIError,
    TelegramConfig,
    TelegramNotifier,
    format_daily_summary,
)


def _parse_allowed_regimes(allowed_regimes: str | list[str] | None) -> list[str] | None:
    """Normalize optional allowed_regimes string or list into upper-case list."""
    if allowed_regimes is None:
        return None
    if isinstance(allowed_regimes, str):
        parsed = [r.strip().upper() for r in allowed_regimes.split(",") if r.strip()]
    else:
        parsed = [str(r).strip().upper() for r in allowed_regimes if str(r).strip()]
    if not parsed:
        raise ValueError("allowed_regimes cannot be empty.")
    return parsed


def evaluate_readonly_state(
    *,
    pair: str = "USD/JPY",
    db_path: str | None = None,
    allowed_regimes: str | list[str] | None = None,
    sl_pips: float | None = None,
    tp_pips: float | None = None,
    use_atr: bool = False,
) -> dict[str, Any]:
    """
    Evaluate the LangGraph signal pipeline in read-only mode (pausing before paper execution
    and never writing to paper_trades.jsonl) to produce a snapshot for daily/on-demand summaries.
    """
    profile = get_pair_profile(pair)
    if db_path is None and profile.pair != "USD/JPY":
        db_path = str(get_pair_db_path(profile))
    parsed_regimes = _parse_allowed_regimes(allowed_regimes)
    run_id = f"summary-{uuid.uuid4().hex[:12]}"
    graph = build_signal_graph(
        db_path=db_path,
        interrupt_on_actionable=True,
        write_ledger=False,
    )
    config = {"configurable": {"thread_id": run_id}}
    initial_state = create_initial_state(
        run_id=run_id,
        pair=profile.pair,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        use_atr=True if use_atr else None,
        allowed_regimes=parsed_regimes,
    )
    return dict(graph.invoke(initial_state, config=config))


def run_signal_pipeline(
    *,
    db_path: str | None = None,
    ledger_path: str | Path | None = None,
    thread_id: str | None = None,
    auto_approve: bool = False,
    reject: bool = False,
    reviewer_notes: str | None = None,
    interactive: bool = False,
    telegram: bool = False,
    telegram_timeout: int | None = None,
    telegram_notifier: Any | None = None,
    allowed_regimes: str | list[str] | None = None,
    regime_overrides: str | dict[str, Any] | None = None,
    sl_pips: float | None = None,
    tp_pips: float | None = None,
    use_atr: bool = False,
    pair: str = "USD/JPY",
) -> dict[str, Any]:
    """
    Execute the FX Signal Reader LangGraph pipeline end-to-end and return a structured result payload.
    """
    if auto_approve and reject:
        raise ValueError("Cannot specify both auto_approve=True and reject=True.")

    profile = get_pair_profile(pair)
    if db_path is None and profile.pair != "USD/JPY":
        db_path = str(get_pair_db_path(profile))

    parsed_regimes = _parse_allowed_regimes(allowed_regimes)
    parsed_overrides = parse_regime_overrides_arg(regime_overrides)

    resolved_thread = thread_id.strip() if thread_id and thread_id.strip() else f"run-{uuid.uuid4().hex[:12]}"
    resolved_ledger = Path(ledger_path) if ledger_path is not None else DEFAULT_LEDGER_PATH

    graph = build_signal_graph(
        db_path=db_path,
        ledger_path=resolved_ledger,
        interrupt_on_actionable=True,
    )
    config = {"configurable": {"thread_id": resolved_thread}}
    initial_state = create_initial_state(
        run_id=resolved_thread,
        pair=profile.pair,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        use_atr=True if use_atr else None,
        allowed_regimes=parsed_regimes,
        regime_overrides=parsed_overrides,
    )

    final_state = graph.invoke(initial_state, config=config)
    snapshot = graph.get_state(config)
    paused = bool(snapshot.next)

    if paused and "paper_execution" in snapshot.next:
        if auto_approve:
            final_state = resume_with_decision(
                graph,
                config,
                approved=True,
                reviewer_notes=reviewer_notes or "Auto-approved via CLI --auto-approve.",
            )
            paused = False
        elif reject:
            final_state = resume_with_decision(
                graph,
                config,
                approved=False,
                reviewer_notes=reviewer_notes or "Rejected via CLI --reject.",
            )
            paused = False
        elif telegram or telegram_notifier is not None:
            notifier = (
                telegram_notifier
                if telegram_notifier is not None
                else TelegramNotifier(TelegramConfig.from_env(timeout_sec=telegram_timeout))
            )
            msg_id, proposal_id, msg_text = notifier.send_approval_request(
                dict(final_state),
                thread_id=resolved_thread,
            )
            approved, tg_notes, timed_out = notifier.wait_for_decision(
                proposal_id=proposal_id,
                message_id=msg_id,
                timeout_sec=telegram_timeout,
                summary_provider=lambda s: format_daily_summary(
                    dict(final_state),
                    ledger_path=resolved_ledger,
                    settings=s,
                ),
            )
            final_state = resume_with_decision(
                graph,
                config,
                approved=approved,
                reviewer_notes=reviewer_notes or tg_notes,
            )
            notifier.update_decision_message(
                message_id=msg_id,
                original_text=msg_text,
                receipt=final_state.get("execution_receipt"),
                timed_out=timed_out,
            )
            paused = False
        elif interactive:
            proposal = final_state.get("trade_proposal") or {}
            print("\n" + "=" * 72)
            print("HUMAN APPROVAL REQUIRED BEFORE PAPER EXECUTION")
            print("=" * 72)
            print(
                f"Proposal: {proposal.get('action')} {proposal.get('position_size_units')} "
                f"{proposal.get('pair')} @ {proposal.get('entry_price')} "
                f"(SL: {proposal.get('stop_loss')}, TP: {proposal.get('take_profit')})"
            )
            print(f"Rationale: {proposal.get('rationale')}")
            try:
                answer = input("Approve paper trade execution? [y/N]: ").strip().lower()
            except EOFError:
                answer = ""
            approved = answer in {"y", "yes"}
            final_state = resume_with_decision(
                graph,
                config,
                approved=approved,
                reviewer_notes=reviewer_notes
                or ("Approved interactively via CLI." if approved else "Declined interactively via CLI."),
            )
            paused = False

    snapshot_after = graph.get_state(config)
    return {
        "thread_id": resolved_thread,
        "paused_for_approval": paused,
        "next_nodes": list(snapshot_after.next),
        "state": dict(final_state),
    }


def format_pipeline_summary(result: dict[str, Any]) -> str:
    """Format a human-readable summary of the pipeline assessment and execution receipt."""
    state = result.get("state", {})
    macro = state.get("macro_assessment") or {}
    risk = state.get("risk_assessment") or {}
    proposal = state.get("trade_proposal") or {}
    receipt = state.get("execution_receipt") or {}
    pair_label = state.get("pair") or "USD/JPY"

    lines = [
        "=" * 74,
        f"{pair_label} Signal Reader - LangGraph Multi-Agent Pipeline Report",
        "=" * 74,
        f"Thread / Run ID    : {result.get('thread_id')}",
        f"Timestamp (UTC)    : {state.get('timestamp_utc')}",
        f"{pair_label} Spot       : {state.get('spot_price')}",
        f"Awaiting Approval  : {result.get('paused_for_approval')} (next={result.get('next_nodes')})",
        "-" * 74,
        "[1] Macro Analyst Assessment",
        f"    Yield Bias     : {state.get('yield_bias')} (confidence={macro.get('confidence')})",
        f"    10Y Yields     : US={macro.get('us_10y')}% | JP={macro.get('jp_10y')}% | Spread={macro.get('spread_10y')}% ({macro.get('spread_bps')} bps)",
        f"    High Catalysts : {len(macro.get('upcoming_high_impact_events') or [])} event(s)",
        f"    Summary        : {macro.get('policy_divergence_summary')}",
        "-" * 74,
        "[2] Risk Committee Assessment",
        f"    Risk Regime    : {state.get('risk_regime')} (VIX={risk.get('vix_level')})",
        f"    Cross-Assets   : S&P500={risk.get('sp500_price')} | Nikkei={risk.get('nikkei_price')} | Gold={risk.get('gold_price')}",
        f"    Intervention   : score={state.get('intervention_risk_score')} [{risk.get('intervention_alert_level')}] | MoF Tier={risk.get('highest_mof_tier')} | Veto={risk.get('veto_trade')}",
        f"    Risk Notes     : {risk.get('risk_notes')}",
        "-" * 74,
        "[3] Trade Proposal & Approval Gate",
        f"    Proposal ID    : {proposal.get('proposal_id')}",
        f"    Action         : {proposal.get('action')} {proposal.get('position_size_units')} {proposal.get('pair')}",
        f"    Levels         : Entry={proposal.get('entry_price')} | SL={proposal.get('stop_loss')} | TP={proposal.get('take_profit')} | R:R={proposal.get('risk_reward_ratio')}",
        f"    Approval Status: {state.get('approval_status')} (notes={state.get('reviewer_notes')})",
        f"    Rationale      : {proposal.get('rationale')}",
    ]

    if receipt:
        lines.extend(
            [
                "-" * 74,
                "[4] Paper Execution Receipt",
                f"    Order ID       : {receipt.get('order_id')}",
                f"    Status         : {receipt.get('status')}",
                f"    Fill / Units   : {receipt.get('action')} {receipt.get('position_size_units')} @ {receipt.get('fill_price')}",
                f"    Executed (UTC) : {receipt.get('executed_at_utc')}",
                f"    Notes          : {receipt.get('notes')}",
            ]
        )

    lines.append("=" * 74)
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser for `run_pipeline.py`."""
    parser = argparse.ArgumentParser(
        description="Run the FX Signal Reader LangGraph multi-agent signal and paper-execution pipeline."
    )
    parser.add_argument(
        "--pair",
        type=str,
        default=os.getenv("FX_PAIR", "USD/JPY"),
        help="Currency pair to evaluate (default: USD/JPY or FX_PAIR env var).",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database (defaults to FX_DB_PATH or usdjpy_signals.db).",
    )
    parser.add_argument(
        "--ledger-path",
        type=str,
        default=str(DEFAULT_LEDGER_PATH),
        help="Path to JSONL ledger storing executed paper trades.",
    )
    parser.add_argument(
        "--thread-id",
        type=str,
        default=None,
        help="Optional LangGraph thread ID for checkpointed execution.",
    )
    decision_group = parser.add_mutually_exclusive_group()
    decision_group.add_argument(
        "--auto-approve",
        action="store_true",
        help="Automatically approve actionable trade proposals at the human gate.",
    )
    decision_group.add_argument(
        "--reject",
        action="store_true",
        help="Automatically reject actionable trade proposals at the human gate.",
    )
    parser.add_argument(
        "--reviewer-notes",
        type=str,
        default=None,
        help="Optional reviewer notes attached to the human approval decision.",
    )
    parser.add_argument(
        "--allowed-regimes",
        type=str,
        default=None,
        help="Optional comma-separated permitted risk regimes (e.g. RISK_ON,TRANSITIONAL).",
    )
    parser.add_argument(
        "--sl-pips",
        type=float,
        default=None,
        help="Optional custom fixed Stop Loss distance in pips (default: 75.0).",
    )
    parser.add_argument(
        "--tp-pips",
        type=float,
        default=None,
        help="Optional custom fixed Take Profit distance in pips (default: 150.0).",
    )
    parser.add_argument(
        "--use-atr",
        action="store_true",
        help="Enable dynamic ATR-14 Stop Loss (1.0x ATR) and Take Profit (2.0x ATR) brackets.",
    )
    parser.add_argument(
        "--regime-overrides",
        type=str,
        default=None,
        help="Optional JSON object string or .json file path mapping RiskRegime to {risk_pct|risk_fraction, sl_pips, tp_pips, use_atr}.",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Route actionable trade proposals to Telegram for remote inline-button approval.",
    )
    parser.add_argument(
        "--telegram-timeout",
        type=int,
        default=None,
        help="Telegram approval wait timeout in seconds (default: TELEGRAM_APPROVAL_TIMEOUT or 300).",
    )
    parser.add_argument(
        "--telegram-test",
        action="store_true",
        help="Send a connectivity test message to Telegram and exit.",
    )
    parser.add_argument(
        "--telegram-summary",
        action="store_true",
        help="Evaluate current market state and send an immediate summary digest to Telegram and exit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the complete pipeline result as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `run_pipeline.py`."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.telegram_test:
        notifier = TelegramNotifier(
            TelegramConfig.from_env(timeout_sec=args.telegram_timeout)
        )
        try:
            test_res = notifier.send_test_message()
        except TelegramAPIError as exc:
            if args.json:
                print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            else:
                print(f"[Telegram Setup Notice] {exc}")
            return 1
        if args.json:
            print(json.dumps(test_res, indent=2, default=str))
        else:
            print(
                f"Telegram test message sent successfully to chat_id={test_res.get('chat_id')} "
                f"(message_id={test_res.get('message_id')})."
            )
            if test_res.get("discovered_chat_id"):
                print(
                    f"Auto-discovered your Telegram Chat ID! Add this line to your .env file:\n"
                    f"TELEGRAM_CHAT_ID={test_res.get('discovered_chat_id')}"
                )
        return 0

    if args.telegram_summary:
        state = evaluate_readonly_state(
            pair=args.pair,
            db_path=args.db_path,
            allowed_regimes=args.allowed_regimes,
            sl_pips=args.sl_pips,
            tp_pips=args.tp_pips,
            use_atr=args.use_atr,
        )
        notifier = TelegramNotifier(
            TelegramConfig.from_env(timeout_sec=args.telegram_timeout)
        )
        msg_res = notifier.send_daily_summary(state, ledger_path=args.ledger_path)
        payload = {
            "status": "SUMMARY_SENT",
            "pair": state.get("pair") or args.pair,
            "message_id": msg_res.get("message_id"),
            "daily_summary_enabled": notifier.settings.daily_summary_enabled,
            "daily_summary_time_jst": notifier.settings.daily_summary_time_jst,
            "state": state,
        }
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(
                format_daily_summary(
                    state,
                    ledger_path=args.ledger_path,
                    settings=notifier.settings,
                )
            )
        return 0

    interactive = (
        not args.json
        and not args.auto_approve
        and not args.reject
        and not args.telegram
        and sys.stdin.isatty()
    )

    result = run_signal_pipeline(
        pair=args.pair,
        db_path=args.db_path,
        ledger_path=args.ledger_path,
        thread_id=args.thread_id,
        auto_approve=args.auto_approve,
        reject=args.reject,
        reviewer_notes=args.reviewer_notes,
        interactive=interactive,
        telegram=args.telegram,
        telegram_timeout=args.telegram_timeout,
        allowed_regimes=args.allowed_regimes,
        regime_overrides=args.regime_overrides,
        sl_pips=args.sl_pips,
        tp_pips=args.tp_pips,
        use_atr=args.use_atr,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_pipeline_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
