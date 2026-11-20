# Agent Guide: Multi-Pair FX Signal Reader (USD/JPY & EUR/USD)

This guide explains how external AI agents and trading automation workflows connect to the `fx_signal_reader` package. It expands on [README.md](../README.md) with practical MCP tool schemas, Python API examples, and signal interpretation rules across both **USD/JPY** and **EUR/USD**.

---

## 1. Connect an External MCP Host

The package exposes a read-only Model Context Protocol (MCP) server over `stdio`. External AI hosts (such as Claude Desktop, Cursor, or custom agent runtimes) can invoke tools to inspect market data, sovereign yield spreads, macro events, and risk metrics.

### Available MCP Tools

| Tool Name | Key Parameters | Output / Purpose |
| :--- | :--- | :--- |
| `get_latest_yield_spread` | `spot_symbol="JPY=X"` or `"EURUSD=X"`, `as_of_utc=None` | Returns 10Y sovereign yield spread (`spread_10y`, `spread_bps`), 5d/20d momentum, and latest spot price (`spot_price`, `spot_symbol`). |
| `get_upcoming_macro_events` | `currency=None` (`USD`, `JPY`, `EUR`), `impact=None`, `limit=20`, `as_of_utc=None` | Returns scheduled economic calendar releases filtered by currency and impact. |
| `get_recent_mof_alerts` | `min_tier=1`, `limit=10`, `as_of_utc=None` | Returns Japan MoF / BoJ verbal intervention headlines with tier scores (active for JPY pairs; returns empty/zero for EUR/USD). |
| `get_market_ticks_summary` | `as_of_utc=None` | Returns latest prices and timestamps across spot FX, sovereign yield proxies (`^TNX`), equity benchmarks (`^GSPC`, `^N225`, `^STOXX50E`), VIX, and Gold. |

### MCP Host Configuration

Add server definitions to your host's MCP configuration file (e.g., `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "usdjpy-signals": {
      "command": "/path/to/fx-signal-reader/.venv/bin/python",
      "args": ["-m", "fx_signal_reader.mcp.server"],
      "env": {
        "FX_PAIR": "USD/JPY",
        "FX_DB_PATH": "/path/to/fx-signal-reader/data/usdjpy_signals.db"
      }
    },
    "eurusd-signals": {
      "command": "/path/to/fx-signal-reader/.venv/bin/python",
      "args": ["-m", "fx_signal_reader.mcp.server"],
      "env": {
        "FX_PAIR": "EUR/USD",
        "FX_DB_PATH": "/path/to/fx-signal-reader/data/eurusd_signals.db"
      }
    }
  }
}
```

---

## 2. Call MCP Tools from Python

Python agents can invoke MCP tools directly via `fx_signal_reader.mcp.client`:

```python
from fx_signal_reader.mcp.client import (
    call_namespaced_tool_sync,
    discover_tools_sync,
)

# Discover configured servers
tools = discover_tools_sync()
print("Connected MCP servers:", list(tools.keys()))

# Query EUR/USD yield spread (DE 10Y Bund - US 10Y Treasury)
eur_spread = call_namespaced_tool_sync(
    "eurusd-signals:get_latest_yield_spread",
    {"spot_symbol": "EURUSD=X"},
)
print("EUR/USD Spot Price:", eur_spread["spot_price"])
print("DE-US 10Y Spread  :", eur_spread["spread_10y"], "%")
print("Spread Momentum   :", eur_spread["momentum"])

# Query upcoming high-impact EUR and USD events
events = call_namespaced_tool_sync(
    "eurusd-signals:get_upcoming_macro_events",
    {"currency": "EUR", "impact": "High", "limit": 5},
)
print(f"Found {len(events)} high-impact EUR events.")
```

---

## 3. Run the LangGraph Workflow in Python

The package provides an end-to-end multi-agent signal and execution pipeline built on LangGraph:

```text
[macro_analyst] ──> [risk_committee] ──> [human_approval_gate] ──> [paper_execution / finalize_hold]
```

### Python Agent Execution Script

```python
from fx_signal_reader.agent import (
    build_signal_graph,
    create_initial_state,
    resume_with_decision,
)

# 1. Build graph connected to EUR/USD database
graph = build_signal_graph(db_path="data/eurusd_signals.db")
config = {"configurable": {"thread_id": "agent-run-eurusd-01"}}

# 2. Initialize state for EUR/USD with custom bracket parameters
initial_state = create_initial_state(
    pair="EUR/USD",
    sl_pips=50.0,
    tp_pips=100.0,
    allowed_regimes=["RISK_ON"],
)

# 3. Execute until pause or completion
state = graph.invoke(initial_state, config=config)

# 4. Check approval gate
snapshot = graph.get_state(config)
if "paper_execution" in snapshot.next:
    proposal = state["trade_proposal"]
    print(f"Actionable proposal generated: {proposal['action']} {proposal['pair']} @ {proposal['entry_price']}")
    print(f"Position Units: {proposal['position_size_units']} | SL: {proposal['stop_loss']} | TP: {proposal['take_profit']}")

    # Resume graph with approval decision
    state = resume_with_decision(
        graph,
        config,
        approved=True,
        reviewer_notes="Approved by autonomous supervisor.",
    )
    print("Execution Receipt:", state["execution_receipt"]["status"])
else:
    print("Pipeline completed with non-actionable state:", state.get("trade_proposal", {}).get("action"))
```

---

## 4. Call the CLI from Agent Tools

External agent harnesses (such as AutoGen, CrewAI, or shell tools) can invoke the CLI with `--json`:

### Pipeline Execution
```bash
# Evaluate USD/JPY with auto-approval
.venv/bin/fx-pipeline --pair USD/JPY --auto-approve --json

# Evaluate EUR/USD with dynamic ATR brackets
.venv/bin/fx-pipeline --pair EUR/USD --use-atr --allowed-regimes RISK_ON --json
```

### Backtest Replay
```bash
# Replay 2-year backtest on EUR/USD with JSON output
.venv/bin/fx-backtest --pair EUR/USD \
  --db-path data/backtest_signals_eurusd.db \
  --step-hours 4 \
  --allowed-regimes RISK_ON \
  --breakeven-r 1.25 \
  --json
```

---

## 5. Understand Signal Fields, Regimes & Pair Policies

### Universal Yield Bias (`yield_bias`)

Yield bias is determined by comparing sovereign bond yields:

| Pair | Sovereign Spread Definition | Bullish Threshold | Bearish Threshold | Neutral Range |
| :--- | :--- | :--- | :--- | :--- |
| **USD/JPY** | $\text{US10Y} - \text{JGB10Y}$ | $\text{Spread} > +2.0\%$ | $\text{Spread} < +1.0\%$ | $+1.0\% \le \text{Spread} \le +2.0\%$ |
| **EUR/USD** | $\text{DE10Y} - \text{US10Y}$ | $\text{Spread} > -1.0\%$ | $\text{Spread} < -2.0\%$ | $-2.0\% \le \text{Spread} \le -1.0\%$ |

Universal `YieldBias` values:
- `BULLISH`: Expects base currency appreciation (BUY).
- `BEARISH`: Expects base currency depreciation (SELL).
- `NEUTRAL`: No directional edge from yields (HOLD).
- *Backward-compatible aliases:* `BULLISH_USD` (BUY on USD/JPY, SELL on EUR/USD) and `BEARISH_USD` (SELL on USD/JPY, BUY on EUR/USD).

### Pair-Aware Risk Committee Policies

The Risk Committee node automatically routes through `PairProfile.risk_policy`:

#### 1. JPY Pairs (`VERBAL_LADDER`)
- Evaluates Japan Ministry of Finance (MoF) verbal intervention escalation (Tiers 1 through 4).
- Spot psychological barrier proximity (157.00, 158.50, 160.00).
- Sets `intervention_risk_score` (0.0 to 100.0) and `intervention_alert_level`.
- `trade_veto` is triggered if score $\ge 70.0$, Tier 4 alerts appear, or VIX $\ge 25.0$.

#### 2. EUR Pairs (`CENTRAL_BANK`)
- MoF intervention ladder is bypassed (`intervention_risk_score = 0.0`, `alert_level = NONE`).
- **Tier-1 Event Blackout:** Automatically triggers `trade_veto = True` if a Tier-1 economic release (ECB Interest Rate Decision, FOMC Rate Decision, US Non-Farm Payrolls, or US CPI) is scheduled within $\pm 4$ hours of the evaluation timestamp.
- **Falling-Knife Protection:** Vetoes BUY signals when 20-day yield spread momentum is falling sharply.
- **Global Volatility Filter:** Vetoes trades when VIX $\ge 25.0$ (Risk-Off regime).

### Position Sizing Mathematics

Positions are calculated to guarantee equal dollar risk (e.g. 1% of equity) regardless of quote currency:

#### Quote is USD (e.g. EUR/USD):
$$\text{Units} = \left\lfloor \frac{\text{Capital} \times \text{Risk Fraction}}{\text{SL Distance}}\right\rfloor$$
$$\text{PnL}_{\text{USD}} = (\text{Exit Price} - \text{Entry Price}) \times \text{Units}, \quad \text{PnL}_{\text{JPY}} = 0.0$$

#### Base is USD (e.g. USD/JPY):
$$\text{Units} = \left\lfloor \frac{\text{Capital} \times \text{Risk Fraction} \times \text{Spot Price}}{\text{SL Distance}}\right\rfloor$$
$$\text{PnL}_{\text{JPY}} = (\text{Exit Price} - \text{Entry Price}) \times \text{Units}, \quad \text{PnL}_{\text{USD}} = \frac{\text{PnL}_{\text{JPY}}}{\text{Exit Price}}$$
