# P8-05: Specialist Agent Nodes Policy & Event Blackout Risk

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** Agent Layer & Policy Engine
* **Last Updated (UTC):** `2026-11-19T10:47:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Adapt the LangGraph specialist agent nodes (`macro_analyst_node` and `risk_committee_node`) to operate with pair awareness and pair-specific risk policy engines:

1. In `macro_analyst_node`:
   - Resolve spot price and trend using the configured pair's spot ticker (`EURUSD=X` vs `JPY=X`).
   - Evaluate macro bias based on pair-specific 10Y yield spread ($\text{DE 10Y} - \text{US 10Y}$ for EUR/USD).
   - Expand `KEY_MACRO_EVENT_KEYWORDS` to include European catalysts (`"ecb"`, `"lagarde"`, `"eurozone"`, `"bundesbank"`).
2. In `risk_committee_node`:
   - Route risk evaluation through `PairProfile.risk_policy`.
   - For JPY pairs (`VERBAL_LADDER`): preserve MoF/BoJ 4-tier verbal intervention ladder and 160.00 psychological barrier.
   - For EUR/USD (`CENTRAL_BANK`): activate the **Event Blackout & Divergence Filter**:
     - Veto trades within $\pm 4$ hours of Tier-1 catalysts: ECB rate decision, FOMC, US NFP/CPI, Eurozone Flash CPI.
     - Enforce global volatility regime veto ($\text{VIX} \ge 25.0$).
3. In `_build_trade_proposal`:
   - Construct valid `TradeProposal` with proper price precision (5 decimals for EUR/USD), pip-scaled SL/TP, and equal-dollar risk position units.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/agent/nodes.py`:**
   - Read `pair` from `AgentState` (defaulting to `"USD/JPY"`).
   - In `macro_analyst_node`, extract spot price for `profile.spot_symbol`.
   - In `risk_committee_node`, branch on `profile.risk_policy == RiskPolicyType.CENTRAL_BANK`.
   - Format proposal rationale with pair-appropriate decimal precision.
2. **Acceptance Criteria:**
   - EUR/USD pipeline run generates a valid `TradeProposal` with 5-decimal prices and standard-lot sizing.
   - High-impact events within $\pm 4$ hours trigger an automatic risk veto for EUR/USD.
   - Existing USD/JPY tests pass without regression.

- [x] `macro_analyst_node` updated with dynamic spot lookup and European keywords
- [x] `risk_committee_node` updated with Event Blackout & Divergence Filter
- [x] `_build_trade_proposal` updated with profile-driven price precision
- [x] Unit tests written and passing
