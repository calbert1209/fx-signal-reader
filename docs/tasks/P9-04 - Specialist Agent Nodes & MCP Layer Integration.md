# P9-04: Specialist Agent Nodes & MCP Layer Integration

* **Phase:** Phase 9: Advanced USD/JPY Signals (Yield Curve, Terms-of-Trade & Intraday Fix)
* **Status:** `PENDING`
* **Complexity:** Integration Task (Depends on P9-01, P9-02, P9-03)
* **Last Updated (UTC):** `2026-11-20T13:35:00Z`
* **Implementation Plan:** [plan_p1_p2_p3_signals.md](file:///Users/albert/.gemini/antigravity/brain/df88ef12-66c7-48bc-a6ad-f98f47ed301e/plan_p1_p2_p3_signals.md)

---

## 1. Objective & Scope

Synthesize the 3 new signals into the LangGraph specialist agent workflow (`macro_analyst_node` and `risk_committee_node`) and expose them via the local read-only SQLite MCP server:

1. **State Schema Evolution (`agent/state.py`):**
   - Expand `MacroAssessment` with `us_2y`, `jp_2y`, `spread_2y`, `crude_oil_price`, `crude_oil_5d_change_pct`, `terms_of_trade_bias`, and `gotobi_session`.
2. **Specialist Node Decision Rules (`agent/nodes.py`):**
   - **2Y Spread Confirmation & Dampening (P1):** When 2Y spread confirms 10Y directional bias, increase confidence (+0.05 to +0.10). When 2Y and 10Y diverge (e.g. curve inversion or policy conflict), moderate confidence and annotate curve divergence.
   - **Symmetric Oil Terms of Trade (P2):** Factor surging oil as a USD/JPY bullish tailwind; factor plunging oil as JPY supportive.
   - **Gotobi Conviction Modulation (P3):** On Gotobi mornings during Pre-Fix window (08:30–09:55 JST), grant conviction boost (+0.05 confidence) on `BUY` proposals; add soft advisory caution on `SELL` proposals without hard veto. In Post-Fix window, annotate caution against chasing fresh highs.
3. **MCP Tool Exposure (`mcp/server.py`):**
   - Include `spread_2y`, `us_2y`, `jp_2y` in `get_latest_yield_spread`.
   - Include `CL=F` and `gotobi_session` in `get_market_ticks_summary`.

## 2. Implementation Notes & Acceptance Criteria

1. **Files Affected:**
   - `fx_signal_reader/agent/state.py`
   - `fx_signal_reader/agent/nodes.py`
   - `fx_signal_reader/mcp/server.py`
   - `tests/test_nodes.py`
   - `tests/test_graph_execution.py`
   - `tests/test_mcp_server.py`
2. **Acceptance Criteria:**
   - `macro_analyst_node` evaluates all 3 signals and generates valid `MacroAssessment`.
   - `risk_committee_node` incorporates Gotobi phase and oil momentum into `TradeProposal` rationale and confidence.
   - MCP tools return enriched payloads while remaining backward-compatible with legacy consumers.
   - Graph execution tests pass with 100% deterministic state transitions.

- [ ] `MacroAssessment` state model updated
- [ ] 2Y confirmation, symmetric oil bias, and Gotobi rules implemented in `nodes.py`
- [ ] MCP tools updated with new fields
- [ ] Unit and graph execution tests written and passing
