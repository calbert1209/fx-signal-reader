# P3-05: Approval gate & paper execution

* **Phase:** Phase 3: Agent Integration (MCP & LangGraph)
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T09:56:11Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Connect the specialist agent nodes into a stateful LangGraph workflow (`StateGraph(AgentState)`) with a human-in-the-loop interrupt gate before routing approved signals to paper-trading execution.

### Deliverables Implemented:
1. **[`agent/graph.py`](file:///Users/albert/Projects/fx-signal-reader/agent/graph.py):**
   - `ExecutionReceipt` Pydantic v2 model recording `order_id`, `proposal_id`, `run_id`, `executed_at_utc`, `status` (`EXECUTED` | `SKIPPED_HOLD` | `CANCELLED_REJECTED`), `pair`, `action`, `fill_price`, `stop_loss`, `take_profit`, `position_size_units`, `approval_status`, `reviewer_notes`, and `notes`.
   - `human_approval_gate_node` & `route_after_approval_gate` routing `HOLD` proposals directly to `finalize_hold` (`NOT_REQUIRED`) and actionable `BUY`/`SELL` proposals to `paper_execution`.
   - `build_signal_graph(...)` compiled with `langgraph.checkpoint.memory.InMemorySaver` and `interrupt_before=["paper_execution"]`.
   - `paper_execution_node` persisting approved paper trades to `data/paper_trades.jsonl` and cancelling rejected orders cleanly without writing to the ledger.
   - `resume_with_decision(...)` helper applying `update_state` (`APPROVED` / `REJECTED` + `reviewer_notes`) and resuming checkpointed execution.
2. **[`run_pipeline.py`](file:///Users/albert/Projects/fx-signal-reader/run_pipeline.py):**
   - CLI entrypoint supporting `--db-path`, `--ledger-path`, `--thread-id`, `--auto-approve`, `--reject`, `--reviewer-notes`, interactive TTY approval prompts, and `--json` output.
3. **[`tests/test_graph_execution.py`](file:///Users/albert/Projects/fx-signal-reader/tests/test_graph_execution.py):**
   - 7 unit and integration tests covering `interrupt_before` breakpoint pausing, `APPROVED` ledger execution, `REJECTED` cancellation, `HOLD` bypass on vetoed & live DB runs, interactive prompt handling, and CLI flags.

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete (`agent/graph.py`, `agent/__init__.py`, `run_pipeline.py`, `tests/test_graph_execution.py`)
- [x] Verified in local sandbox / environment (`7/7` graph execution tests passing; `118/118` project-wide tests passing)
- [x] Linked artifacts and logs updated (Drive task doc `1s481qnz8QwSZUDa_GDdonBNd2Sa3Etsh`, Google Sheet Row 15, Master Plan `1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb`)

---

## 3. Execution Log & Findings

### 3.1 Graph Topology

```text
START
  │
  ▼
macro_analyst  (yield spread + upcoming High-impact USD/JPY macro calendar)
  │
  ▼
risk_committee (VIX risk regime + MoF 4-tier verbal intervention veto + TradeProposal)
  │
  ▼
human_approval_gate
  ├──[HOLD / NOT_REQUIRED]──▶ finalize_hold (SKIPPED_HOLD) ────────────────────▶ END
  └──[BUY / SELL]───────────▶ << interrupt_before: paper_execution >>
                                  │ (human update_state: APPROVED / REJECTED)
                                  ▼
                              paper_execution (EXECUTED -> paper_trades.jsonl
                                               or CANCELLED_REJECTED) ─────────▶ END
```

### 3.2 Live Database Pipeline Run (`usdjpy_signals.db`)

Executed `.venv/bin/python run_pipeline.py`:
```text
==========================================================================
USD/JPY Signal Reader - LangGraph Multi-Agent Pipeline Report
==========================================================================
Thread / Run ID    : run-702b7c14bf74
Timestamp (UTC)    : 2026-11-18T09:55:13Z
USD/JPY Spot       : 159.13800048828125
Awaiting Approval  : False (next=[])
--------------------------------------------------------------------------
[1] Macro Analyst Assessment
    Yield Bias     : BULLISH_USD (confidence=0.6345)
    10Y Yields     : US=5.102999687194824% | JP=2.925% | Spread=2.178% (217.8 bps)
    High Catalysts : 4 event(s)
    Summary        : US-JP 10Y spread at 2.178% (217.8 bps; US10Y=5.103%, JP10Y=2.925%) implies BULLISH_USD. 1 imminent key USD/JPY catalyst(s) near window [FOMC Meeting Minutes] dampen directional confidence.
--------------------------------------------------------------------------
[2] Risk Committee Assessment
    Risk Regime    : TRANSITIONAL (VIX=23.850000381469727)
    Cross-Assets   : S&P500=7340.6298828125 | Nikkei=59165.12890625 | Gold=3769.60009765625
    Intervention   : score=100.0 [CRITICAL] | MoF Tier=4 | Veto=True
    Risk Notes     : Regime=TRANSITIONAL (VIX=23.85); USD/JPY spot=159.138; MoF Tier 4 across 25 alert(s) -> score=100.00 [CRITICAL] (base=80.0, volume=15.0, spot=15.0); veto_trade=True.
--------------------------------------------------------------------------
[3] Trade Proposal & Approval Gate
    Proposal ID    : prop-run-702b7c14bf74
    Action         : HOLD 0 USD/JPY
    Levels         : Entry=None | SL=None | TP=None | R:R=None
    Approval Status: NOT_REQUIRED (notes=None)
    Rationale      : HOLD recommended: risk veto triggered (intervention_score=100.00, highest_mof_tier=4, regime=TRANSITIONAL).
--------------------------------------------------------------------------
[4] Paper Execution Receipt
    Order ID       : PAPER-2ABD89FD2791
    Status         : SKIPPED_HOLD
    Fill / Units   : HOLD 0 @ None
    Executed (UTC) : 2026-11-18T09:55:13Z
    Notes          : Skipped paper execution (HOLD): HOLD recommended: risk veto triggered (intervention_score=100.00, highest_mof_tier=4, regime=TRANSITIONAL).
==========================================================================
```

### 3.3 Test Verification Outputs

- `.venv/bin/python -m unittest -v tests/test_graph_execution.py`: **7/7 passed** (`Ran 7 tests in 8.951s — OK`).
- `.venv/bin/python -m unittest discover -s tests -v`: **118/118 passed** (`Ran 118 tests in 48.374s — OK`).
