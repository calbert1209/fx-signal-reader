# Agent Guide for USD/JPY Signal Reader

This guide shows how to connect an AI agent to this package. It expands on [README.md](../README.md) with basic code examples.

---

## 1. Connect an External MCP Host

The package includes a read-only MCP server over `stdio`. External AI hosts can call four tools to inspect market data.

### Available Tools

| Tool Name | Purpose |
| :--- | :--- |
| `get_latest_yield_spread` | Return the latest 10Y yield spread and USD/JPY spot price. |
| `get_upcoming_macro_events` | Return scheduled USD and JPY economic events. |
| `get_recent_mof_alerts` | Return news headlines with intervention keywords and tier scores. |
| `get_market_ticks_summary` | Return the latest prices for `JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, and `GC=F`. |

### Configuration Steps

1. Open the MCP configuration file for your AI host.
2. Add the `usdjpy-sqlite` server entry:
   ```json
   {
     "mcpServers": {
       "usdjpy-sqlite": {
         "command": "/Users/albert/Projects/fx-signal-reader/.venv/bin/python",
         "args": ["-m", "fx_signal_reader.mcp.server"],
         "env": {
           "FX_DB_PATH": "/Users/albert/Projects/fx-signal-reader/data/usdjpy_signals.db"
         }
       }
     }
   }
   ```
3. Restart your AI host to load the four tools.

---

## 2. Call MCP Tools from Python

Your Python agent can call MCP tools directly through `fx_signal_reader.mcp.client`.

1. Activate the project virtual environment.
2. Run this script to list tools and query the latest yield spread:

```python
from fx_signal_reader.mcp.client import (
    call_namespaced_tool_sync,
    discover_tools_sync,
)

tools = discover_tools_sync()
print("Servers:", list(tools.keys()))
print("SQLite tools:", [t["namespaced_name"] for t in tools["usdjpy-sqlite"]])

spread = call_namespaced_tool_sync("usdjpy-sqlite:get_latest_yield_spread")
print("Spread (bps):", spread["spread_bps"])
print("Spot USD/JPY:", spread["usdjpy_spot"])

alerts = call_namespaced_tool_sync(
    "usdjpy-sqlite:get_recent_mof_alerts",
    {"min_tier": 3, "limit": 3},
)
print("High-tier alerts:", len(alerts))
```

This client connects to every enabled server in `fx_signal_reader/mcp/mcp_servers.json`.

---

## 3. Run the LangGraph Workflow in Python

The package includes a LangGraph workflow with four nodes:

1. `macro_analyst`: Checks the 10Y yield spread and high-impact events.
2. `risk_committee`: Checks VIX, cross-asset prices, and intervention risk.
3. `human_approval_gate`: Pauses actionable `BUY` or `SELL` proposals before execution.
4. `paper_execution`: Writes approved trades to `data/paper_trades.jsonl`.

Run this script to execute the workflow and handle the approval gate:

```python
from fx_signal_reader.agent import (
    build_signal_graph,
    create_initial_state,
    resume_with_decision,
)

graph = build_signal_graph(db_path="data/usdjpy_signals.db")
config = {"configurable": {"thread_id": "guide-demo-1"}}

initial_state = create_initial_state()
state = graph.invoke(initial_state, config=config)

snapshot = graph.get_state(config)
if "paper_execution" in snapshot.next:
    print("Proposal needs approval:", state["trade_proposal"])
    state = resume_with_decision(
        graph,
        config,
        approved=True,
        reviewer_notes="Approved in guide demo.",
    )

print("Final action:", state["trade_proposal"]["action"])
print("Receipt status:", state["execution_receipt"]["status"])
```

If the Risk Committee sets `veto_trade` to `True`, the workflow proposes `HOLD`. For `HOLD` proposals, the graph skips the approval prompt and sets status to `SKIPPED_HOLD`.

---

## 4. Call the CLI from Agent Tools

External agents can also run `fx-pipeline` as a shell command with `--json`.

1. Run the pipeline without auto-approval:
   ```bash
   .venv/bin/fx-pipeline --db-path data/usdjpy_signals.db --json
   ```
2. Add `--auto-approve` to execute actionable proposals immediately:
   ```bash
   .venv/bin/fx-pipeline --db-path data/usdjpy_signals.db --auto-approve --json
   ```
3. Add `--reject` to cancel actionable proposals:
   ```bash
   .venv/bin/fx-pipeline --db-path data/usdjpy_signals.db --reject --json
   ```

---

## 5. Understand Signal Fields and Rules

Agents can read these key fields from the output state:

| Field | Values | Meaning |
| :--- | :--- | :--- |
| `yield_bias` | `BULLISH_USD`, `BEARISH_USD`, `NEUTRAL` | Direction from `spread_10y` (`> 2.0%` is bullish, `< 1.0%` is bearish). |
| `risk_regime` | `RISK_ON`, `RISK_OFF`, `TRANSITIONAL` | Market regime from `^VIX` (`< 18` is risk-on, `>= 25` is risk-off). |
| `intervention_risk_score` | `0.0` to `100.0` | Risk score from MoF alert tiers and proximity to `160.00`. |
| `veto_trade` | `True` or `False` | `True` when `intervention_risk_score >= 70.0` or Tier 4 alerts appear. |
| `execution_receipt.status` | `EXECUTED`, `SKIPPED_HOLD`, `CANCELLED_REJECTED` | Final result of the paper execution step. |
