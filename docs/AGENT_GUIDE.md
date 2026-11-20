# Agent Guide: Multi-Pair FX Signal Reader (USD/JPY & EUR/USD)

This guide explains how external AI agents and trading automation workflows connect to the `fx_signal_reader` package. It expands on [README.md](../README.md) with practical MCP tool schemas, Python API examples, and signal interpretation rules across both **USD/JPY** and **EUR/USD**.

---

## 1. Connect an External MCP Host

The package exposes a read-only Model Context Protocol (MCP) server over `stdio`. External AI hosts (such as Claude Desktop, Cursor, or custom agent runtimes) can invoke tools to inspect market data, sovereign yield spreads, macro events, and risk metrics.

### Available MCP Tools

| Tool Name | Key Parameters | Output / Purpose |
| :--- | :--- | :--- |
| `get_latest_yield_spread` | `spot_symbol="JPY=X"` or `"EURUSD=X"`, `as_of_utc=None` | Returns 10Y and 2Y sovereign yield spreads (`us_10y`, `jp_10y`, `spread_10y`, `spread_bps`, `us_2y`, `jp_2y`, `spread_2y`, `us_2s10s_slope`), 5d/20d momentum, and latest spot price (`spot_price`, `spot_symbol`). |
| `get_upcoming_macro_events` | `currency=None` (`USD`, `JPY`, `EUR`), `impact=None`, `limit=20`, `as_of_utc=None` | Returns scheduled economic calendar releases filtered by currency and impact. |
| `get_recent_mof_alerts` | `min_tier=1`, `limit=10`, `as_of_utc=None` | Returns Japan MoF / BoJ verbal intervention headlines with tier scores (active for JPY pairs; returns empty/zero for EUR/USD). |
| `get_market_ticks_summary` | `symbols=None`, `as_of_utc=None` | Returns latest OHLCV prices, bar changes, and timestamps across spot FX, sovereign yield proxies (`^TNX`), equity benchmarks (`^GSPC`, `^N225`, `^STOXX50E`), VIX, Gold (`GC=F`), WTI Crude Oil (`CL=F`), plus `gotobi_session` metadata. |
| `get_market_trend_summary` | `symbol="JPY=X"`, `as_of_utc=None` | Returns rolling EMA-50, SMA-200, ATR-14, and trend status (`BULLISH`, `BEARISH`, `SIDEWAYS`). |

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

# Query USD/JPY 10Y + 2Y yield spreads and market ticks (including CL=F and gotobi_session)
jpy_spread = call_namespaced_tool_sync(
    "usdjpy-signals:get_latest_yield_spread",
    {"spot_symbol": "JPY=X"},
)
print("US-JP 10Y Spread:", jpy_spread["spread_10y"], "% | 2Y Spread:", jpy_spread["spread_2y"], "%")

ticks_summary = call_namespaced_tool_sync(
    "usdjpy-signals:get_market_ticks_summary",
    {},
)
print("WTI Crude Oil (CL=F):", ticks_summary["symbols"].get("CL=F", {}).get("close"))
print("Gotobi Session:", ticks_summary["gotobi_session"])
```

### MCP Tool Payload Schemas (`get_latest_yield_spread` & `get_market_ticks_summary`)

- **`get_latest_yield_spread` Return Fields:**
  - `timestamp_utc`, `source`
  - `us_10y`, `jp_10y`, `spread_10y`, `spread_bps`
  - `us_2y`, `jp_2y`, `spread_2y`, `us_2s10s_slope`
  - `spread_5d_change`, `spread_20d_change`, `momentum`
  - `spot_price`, `spot_symbol`, `usdjpy_spot`, `usdjpy_timestamp_utc`
- **`get_market_ticks_summary` Return Fields:**
  - `as_of_utc`, `requested_symbols`, `symbol_count`, `missing_symbols`
  - `symbols`: Mapping of symbol (`JPY=X`, `^TNX`, `^GSPC`, `^N225`, `^VIX`, `GC=F`, `CL=F`, etc.) to `{symbol, timestamp_utc, close, open, high, low, volume, prev_close, bar_change, bar_change_pct}`.
  - `gotobi_session`: Dictionary produced by `evaluate_gotobi_session(as_of_utc)` containing `{timestamp_utc, jst_date, jst_time, is_gotobi_day, gotobi_reason, session_phase, is_pre_fix_window, is_post_fix_window, notes}`.
- **Python Helper `get_commodity_momentum_summary(symbol="CL=F", db_path=None, as_of_utc=None)`:**
  - Computes `{symbol, as_of_utc, current_price, price, roc_5d, roc_5d_pct, roc_20d, roc_20d_pct, ema_50, regime, bars_count}` where `regime` is `"SURGING"`, `"PLUNGING"`, or `"STABLE"`.

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

### Structured State Models (`MacroAssessment` & `TradeProposal`)

#### `MacroAssessment` Fields (`fx_signal_reader/agent/state.py`)
- **10Y Yield & Momentum Fields:** `yield_bias`, `us_10y`, `jp_10y`, `spread_10y`, `spread_bps`, `spread_5d_change`, `spread_20d_change`, `spread_momentum`, `upcoming_high_impact_events`, `policy_divergence_summary`, `confidence` (`0.0` to `1.0`).
- **2Y Sovereign Yield Spread Fields (P1):**
  - `us_2y` (`float | None`): US 2-Year Treasury yield (%).
  - `jp_2y` (`float | None`): Japan 2-Year JGB yield (%).
  - `spread_2y` (`float | None`): 2-Year sovereign spread (`us_2y - jp_2y`, in percentage points). Confirms 10Y bias (`+0.05` confidence boost when `spread_2y > 2.0%` on bullish or `< 1.0%` on bearish) or applies curve divergence dampening (`-0.05` confidence when 2Y and 10Y spreads disagree).
- **Crude Oil Terms-of-Trade Fields (P2):**
  - `crude_oil_price` (`float | None`): Latest WTI Crude Oil (`CL=F`) price in USD.
  - `crude_oil_5d_change_pct` (`float | None`): 5-day percentage rate of change in `CL=F`.
  - `terms_of_trade_bias` (`str | None`): Symmetric energy terms-of-trade directional bias for Japan (`"BULLISH_USD"` when `CL=F` is `SURGING`, `"BEARISH_USD"` when `PLUNGING`, or `"NEUTRAL"` when `STABLE`).
- **Tokyo Gotobi & Nakane Session Field (P3):**
  - `gotobi_session` (`dict[str, Any] | None`): Evaluated Tokyo 9:55 AM JST fixing metadata (`is_gotobi_day`, `session_phase` in `{"PRE_FIX", "POST_FIX", "STANDARD"}`, `is_pre_fix_window`, `is_post_fix_window`, `gotobi_reason`, `notes`).

#### `TradeProposal` Fields (`fx_signal_reader/agent/state.py`)
- `proposal_id`, `timestamp_utc`, `pair`, `action` (`BUY`, `SELL`, `HOLD`), `entry_price`, `stop_loss`, `take_profit`, `risk_reward_ratio`, `position_size_units`, `rationale`, `macro_bias`, `risk_regime`, `intervention_risk_score`.
- `confidence` (`float | None`, `0.0` to `1.0`): Composite conviction score propagated from `MacroAssessment.confidence` and modulated by the Risk Committee (e.g., `+0.05` conviction boost for `BUY` proposals during a Gotobi `PRE_FIX` window; soft advisory warning on `SELL` proposals during Gotobi `PRE_FIX`; exhaustion caution during `POST_FIX`).

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
