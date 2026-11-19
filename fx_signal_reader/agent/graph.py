"""
USD/JPY Signal Reader - LangGraph Approval Gate & Paper Execution Workflow
Task P3-05: Approval Gate and Paper Execution

Wires the specialist analyst and risk committee nodes into a stateful
`StateGraph(AgentState)` with a human-in-the-loop `interrupt_before` gate
on actionable (`BUY` / `SELL`) proposals before paper-trade execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Literal
import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from fx_signal_reader.agent.nodes import make_macro_analyst_node, make_risk_committee_node
from fx_signal_reader.agent.state import (
    AgentState,
    ApprovalStatus,
    TradeAction,
    TradeProposal,
)
from fx_signal_reader.core import PROJECT_ROOT
from fx_signal_reader.core.normalizer import normalize_datetime_utc


DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "paper_trades.jsonl"
ACTIONABLE_ACTIONS: frozenset[str] = frozenset({TradeAction.BUY.value, TradeAction.SELL.value})


class ExecutionReceipt(BaseModel):
    """Structured paper-execution outcome produced after the approval gate."""

    order_id: str
    proposal_id: str
    run_id: str
    executed_at_utc: str
    status: Literal["EXECUTED", "SKIPPED_HOLD", "CANCELLED_REJECTED"]
    pair: str = "USD/JPY"
    action: TradeAction
    fill_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    position_size_units: int = Field(default=0, ge=0)
    approval_status: ApprovalStatus
    reviewer_notes: str | None = None
    notes: str


def _parse_trade_proposal(state: AgentState) -> TradeProposal | None:
    """Validate and return the `TradeProposal` embedded in `state`, if present."""
    raw = state.get("trade_proposal")
    if raw is None:
        return None
    return TradeProposal.model_validate(raw)


def _resolve_action(state: AgentState, proposal: TradeProposal | None) -> TradeAction:
    """Extract the `TradeAction` from `proposal`, defaulting to `HOLD`."""
    if proposal is None:
        return TradeAction.HOLD
    return proposal.action


def _build_receipt(
    *,
    state: AgentState,
    proposal: TradeProposal | None,
    status: Literal["EXECUTED", "SKIPPED_HOLD", "CANCELLED_REJECTED"],
    approval_status: ApprovalStatus,
    fill_price: float | None,
    position_size_units: int,
    notes: str,
) -> ExecutionReceipt:
    """Construct a validated `ExecutionReceipt` from workflow state and outcome fields."""
    run_id = str(state.get("run_id") or "").strip() or str(uuid.uuid4())
    now_utc = normalize_datetime_utc(None, fallback_now=True)
    return ExecutionReceipt(
        order_id=f"PAPER-{uuid.uuid4().hex[:12].upper()}",
        proposal_id=proposal.proposal_id if proposal else f"PROP-{run_id[:8]}",
        run_id=run_id,
        executed_at_utc=now_utc,
        status=status,
        pair=proposal.pair if proposal else "USD/JPY",
        action=proposal.action if proposal else TradeAction.HOLD,
        fill_price=fill_price,
        stop_loss=proposal.stop_loss if proposal else None,
        take_profit=proposal.take_profit if proposal else None,
        position_size_units=position_size_units,
        approval_status=approval_status,
        reviewer_notes=state.get("reviewer_notes"),
        notes=notes,
    )


def _append_to_ledger(receipt: ExecutionReceipt, ledger_path: str | Path) -> None:
    """Append an executed paper-trade receipt as a single JSONL line."""
    target = Path(ledger_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt.model_dump(mode="json"), sort_keys=True) + "\n")


def human_approval_gate_node(state: AgentState) -> dict[str, Any]:
    """
    Evaluate whether the trade proposal requires human approval before execution.

    HOLD proposals are marked `NOT_REQUIRED` and routed directly to terminal logging;
    actionable `BUY`/`SELL` proposals preserve `PENDING` (or an injected override)
    and pause at the `interrupt_before=["paper_execution"]` breakpoint.
    """
    proposal = _parse_trade_proposal(state)
    action = _resolve_action(state, proposal)
    current_status = str(state.get("approval_status") or ApprovalStatus.PENDING.value)

    if action == TradeAction.HOLD:
        return {
            "approval_status": ApprovalStatus.NOT_REQUIRED.value,
            "audit_log": ["human_approval_gate:action=HOLD;approval=NOT_REQUIRED;routing=finalize_hold"],
        }

    if current_status not in {
        ApprovalStatus.PENDING.value,
        ApprovalStatus.APPROVED.value,
        ApprovalStatus.REJECTED.value,
    }:
        current_status = ApprovalStatus.PENDING.value

    return {
        "approval_status": current_status,
        "audit_log": [
            f"human_approval_gate:action={action.value};approval={current_status};routing=paper_execution"
        ],
    }


def route_after_approval_gate(state: AgentState) -> Literal["paper_execution", "finalize_hold"]:
    """Route actionable `BUY`/`SELL` proposals to `paper_execution` and `HOLD` to `finalize_hold`."""
    proposal = _parse_trade_proposal(state)
    action = _resolve_action(state, proposal)
    if action.value in ACTIONABLE_ACTIONS:
        return "paper_execution"
    return "finalize_hold"


# Alias retained for clarity when referencing risk-to-gate conditional routing
route_after_risk_committee = route_after_approval_gate


def finalize_hold_node(state: AgentState) -> dict[str, Any]:
    """Record a `SKIPPED_HOLD` receipt for vetoed or neutral proposals without interrupting."""
    proposal = _parse_trade_proposal(state)
    rationale = proposal.rationale if proposal else "No trade proposal generated."
    receipt = _build_receipt(
        state=state,
        proposal=proposal,
        status="SKIPPED_HOLD",
        approval_status=ApprovalStatus.NOT_REQUIRED,
        fill_price=None,
        position_size_units=0,
        notes=f"Skipped paper execution (HOLD): {rationale}",
    )
    return {
        "approval_status": ApprovalStatus.NOT_REQUIRED.value,
        "execution_receipt": receipt.model_dump(mode="json"),
        "audit_log": [f"finalize_hold:status={receipt.status};order_id={receipt.order_id}"],
    }


def paper_execution_node(
    state: AgentState,
    ledger_path: str | Path | None = None,
    write_ledger: bool = True,
) -> dict[str, Any]:
    """
    Execute an approved paper trade and log to the JSONL ledger, or cancel cleanly
    when `approval_status` is not `APPROVED`.
    """
    proposal = _parse_trade_proposal(state)
    raw_status = str(state.get("approval_status") or ApprovalStatus.REJECTED.value)
    resolved_ledger = Path(ledger_path) if ledger_path is not None else DEFAULT_LEDGER_PATH

    if raw_status == ApprovalStatus.APPROVED.value and proposal is not None and proposal.action != TradeAction.HOLD:
        fill_price = proposal.entry_price if proposal.entry_price is not None else state.get("spot_price")
        receipt = _build_receipt(
            state=state,
            proposal=proposal,
            status="EXECUTED",
            approval_status=ApprovalStatus.APPROVED,
            fill_price=fill_price,
            position_size_units=proposal.position_size_units,
            notes=(
                f"Paper trade executed ({proposal.action.value} {proposal.position_size_units} "
                f"{proposal.pair} @ {fill_price})."
            ),
        )
        if write_ledger:
            _append_to_ledger(receipt, resolved_ledger)
        return {
            "execution_receipt": receipt.model_dump(mode="json"),
            "audit_log": [
                f"paper_execution:status=EXECUTED;order_id={receipt.order_id};"
                f"action={receipt.action.value};units={receipt.position_size_units};fill={fill_price}"
            ],
        }

    # Any non-approved outcome (e.g. REJECTED or missing approval) cancels execution without ledger write
    effective_approval = (
        ApprovalStatus.REJECTED
        if raw_status == ApprovalStatus.REJECTED.value
        else ApprovalStatus(raw_status)
        if raw_status in {s.value for s in ApprovalStatus}
        else ApprovalStatus.REJECTED
    )
    receipt = _build_receipt(
        state=state,
        proposal=proposal,
        status="CANCELLED_REJECTED",
        approval_status=effective_approval,
        fill_price=None,
        position_size_units=0,
        notes=f"Paper execution cancelled (approval_status={effective_approval.value}).",
    )
    return {
        "approval_status": effective_approval.value,
        "execution_receipt": receipt.model_dump(mode="json"),
        "audit_log": [
            f"paper_execution:status=CANCELLED_REJECTED;order_id={receipt.order_id};"
            f"approval={effective_approval.value}"
        ],
    }


def make_paper_execution_node(
    ledger_path: str | Path | None = None,
    write_ledger: bool = True,
) -> Callable[[AgentState], dict[str, Any]]:
    """Create a `StateGraph`-compatible `paper_execution` node bound to `ledger_path`."""

    def _paper_execution(state: AgentState) -> dict[str, Any]:
        return paper_execution_node(state, ledger_path=ledger_path, write_ledger=write_ledger)

    return _paper_execution


def build_signal_graph(
    db_path: str | None = None,
    ledger_path: str | Path | None = None,
    checkpointer: Any | None = None,
    interrupt_on_actionable: bool = True,
    point_in_time: bool = False,
    mof_lookback_hours: int | None = None,
    write_ledger: bool = True,
) -> Any:
    """
    Build and compile the USD/JPY LangGraph pipeline:
    START -> macro_analyst -> risk_committee -> human_approval_gate
      -> [BUY/SELL] -> (interrupt_before) paper_execution -> END
      -> [HOLD]     -> finalize_hold -> END
    """
    builder = StateGraph(AgentState)
    builder.add_node(
        "macro_analyst",
        make_macro_analyst_node(
            db_path=db_path,
            point_in_time=point_in_time,
            mof_lookback_hours=mof_lookback_hours,
        ),
    )
    builder.add_node(
        "risk_committee",
        make_risk_committee_node(
            db_path=db_path,
            point_in_time=point_in_time,
            mof_lookback_hours=mof_lookback_hours,
        ),
    )
    builder.add_node("human_approval_gate", human_approval_gate_node)
    builder.add_node("finalize_hold", finalize_hold_node)
    builder.add_node(
        "paper_execution",
        make_paper_execution_node(ledger_path=ledger_path, write_ledger=write_ledger),
    )

    builder.add_edge(START, "macro_analyst")
    builder.add_edge("macro_analyst", "risk_committee")
    builder.add_edge("risk_committee", "human_approval_gate")
    builder.add_conditional_edges(
        "human_approval_gate",
        route_after_approval_gate,
        {
            "paper_execution": "paper_execution",
            "finalize_hold": "finalize_hold",
        },
    )
    builder.add_edge("finalize_hold", END)
    builder.add_edge("paper_execution", END)

    resolved_checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
    interrupt_nodes = ["paper_execution"] if interrupt_on_actionable else []
    return builder.compile(
        checkpointer=resolved_checkpointer,
        interrupt_before=interrupt_nodes,
    )


def resume_with_decision(
    compiled_graph: Any,
    config: dict[str, Any],
    approved: bool,
    reviewer_notes: str | None = None,
) -> AgentState:
    """
    Apply a human reviewer's approval/rejection decision at the `paper_execution`
    interrupt breakpoint and resume the graph to completion.
    """
    decision = ApprovalStatus.APPROVED.value if approved else ApprovalStatus.REJECTED.value
    note_suffix = f";notes={reviewer_notes}" if reviewer_notes else ""
    compiled_graph.update_state(
        config,
        {
            "approval_status": decision,
            "reviewer_notes": reviewer_notes,
            "audit_log": [f"human_decision:approval={decision}{note_suffix}"],
        },
    )
    return compiled_graph.invoke(None, config=config)


__all__ = [
    "DEFAULT_LEDGER_PATH",
    "ExecutionReceipt",
    "build_signal_graph",
    "finalize_hold_node",
    "human_approval_gate_node",
    "make_paper_execution_node",
    "paper_execution_node",
    "resume_with_decision",
    "route_after_approval_gate",
    "route_after_risk_committee",
]
