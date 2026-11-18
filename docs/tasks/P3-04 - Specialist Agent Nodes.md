# P3-04: Specialist agent nodes

* **Phase:** Phase 3: Agent Integration (MCP & LangGraph)
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T09:48:14Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Implement the Macro Analyst node (yields + calendar) and Risk Committee node (VIX + MoF headlines) operating on `AgentState` (`agent/state.py`) and consuming the read-only SQLite domain query tools from `mcp_server.py`.

### Deliverables Implemented:
1. **`agent/nodes.py`:**
   - `macro_analyst_node(state, db_path=None)` and `make_macro_analyst_node(db_path=None)` factory wrapper for `StateGraph`.
   - `risk_committee_node(state, db_path=None)` and `make_risk_committee_node(db_path=None)` factory wrapper for `StateGraph`.
2. **`tests/test_nodes.py`:**
   - 9 comprehensive unit and integration tests covering synthetic fixture scenarios (A–D), boundary conditions, macro event confidence dampening, `RISK_OFF` sizing vs. elevated intervention veto, spot proximity barrier bonuses, live `usdjpy_signals.db` verification, and compiled 2-node LangGraph `StateGraph(AgentState)` execution.

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete (`agent/nodes.py` and `tests/test_nodes.py`)
- [x] Verified in local sandbox / environment (`.venv/bin/python -m unittest -v tests/test_nodes.py` — 9/9 tests passed)
- [x] Linked artifacts and logs updated (Drive task doc `1ey_ClX5TCRXvN204lVGIbsAC92lEN72k` and Google Sheet tracker Row 14)

---

## 3. Execution Log & Findings

### 3.1 Specialist Node Decision Rules (`agent/nodes.py`)

#### 1. Macro Analyst Node (`macro_analyst_node`)
Consumes `mcp_server.get_latest_yield_spread(db_path=db_path)` and `mcp_server.get_upcoming_macro_events(currency=None, impact="High", limit=10, db_path=db_path)`:
- **Yield Spread Classification (`spread_10y = us_10y - jp_10y`):**
  - `spread_10y > 2.0` (`> +200 bps`) → `YieldBias.BULLISH_USD`
  - `spread_10y < 1.0` (`< +100 bps`) → `YieldBias.BEARISH_USD`
  - `1.0 <= spread_10y <= 2.0` or `None` → `YieldBias.NEUTRAL`
- **Confidence Calibration (`0.0–1.0`):**
  - Directional bias (`BULLISH_USD` / `BEARISH_USD`): base `0.65` + spread-magnitude bonus up to `+0.25`.
  - Neutral zone (`1.0 <= spread_10y <= 2.0`): `0.45–0.55` centered around `1.50%`.
  - Missing spread (`None`): `0.25` baseline.
  - Catalyst dampening: high-impact USD/JPY releases (`FOMC`, `CPI`, `NFP`, `BoJ`, `Retail Sales`, `PCE`, `GDP`, `Warsh`, `Powell`, `Ueda`, etc.) scheduled within `[-4h, +48h]` of `state["timestamp_utc"]` apply `-0.06` per imminent release (capped at `-0.15`), keeping confidence within `[0.10, 0.95]`.
- **Validation & State Update:** Validates `MacroAssessment` via Pydantic (`model_dump(mode="json")`), runs `validate_state_models`, and updates `spot_price`, `yield_bias`, `macro_assessment`, and `audit_log`.

#### 2. Risk Committee Node (`risk_committee_node`)
Consumes `mcp_server.get_market_ticks_summary(db_path=db_path)` (`JPY=X`, `^VIX`, `^GSPC`, `^N225`, `GC=F`) and `mcp_server.get_recent_mof_alerts(min_tier=1, limit=25, db_path=db_path)`:
- **Cross-Asset Risk Regime (`^VIX`):**
  - `vix >= 25.0` → `RiskRegime.RISK_OFF`
  - `vix < 18.0` → `RiskRegime.RISK_ON`
  - `18.0 <= vix < 25.0` or `None` → `RiskRegime.TRANSITIONAL`
- **Intervention Risk Score (`0.0–100.0`) & Alert Level:**
  - Base score by `highest_mof_tier`: `Tier 4 -> 80.0`, `Tier 3 -> 55.0`, `Tier 2 -> 35.0`, `Tier 1 -> 15.0`, `Tier 0 -> 5.0`.
  - Volume bonus for multiple Tier 3/4 alerts (`+4.0` per extra Tier 4, `+1.5`/`+2.5` per Tier 3, capped at `+15.0`).
  - Spot proximity bonus to `160.00`: `spot >= 160.0 -> +20.0`, `158.5 <= spot < 160.0 -> +15.0`, `157.0 <= spot < 158.5 -> +5.0`, clamped to `[0.0, 100.0]`.
  - `InterventionAlertLevel`: `CRITICAL` (`score >= 70.0` or `tier >= 4`), `WARNING` (`score >= 45.0` or `tier == 3`), `WATCH` (`score >= 15.0` or `tier >= 1`), `NONE` otherwise.
- **Veto Logic (`veto_trade = True`):**
  - `intervention_risk_score >= 70.0` or `highest_mof_tier >= 4`, OR
  - `yield_bias == BULLISH_USD` while `risk_regime == RISK_OFF` and intervention risk is elevated (`intervention_risk_score >= 35.0` or `highest_mof_tier >= 2`).
- **Trade Proposal Synthesis (`TradeProposal`):**
  - `veto_trade` or `yield_bias == NEUTRAL` or `spot_price is None` → `TradeAction.HOLD`, `position_size_units = 0`, `approval_status = "NOT_REQUIRED"`.
  - `BULLISH_USD` (unvetoed) → `TradeAction.BUY`, `entry = round(spot, 3)`, `stop_loss = entry - 0.75`, `take_profit = entry + 1.50` (`2.0` R:R), `approval_status = "PENDING"`.
  - `BEARISH_USD` (unvetoed) → `TradeAction.SELL`, `entry = round(spot, 3)`, `stop_loss = entry + 0.75`, `take_profit = entry - 1.50` (`2.0` R:R), `approval_status = "PENDING"`.
  - Position size scaling: `10000` (`RISK_ON`), `5000` (`TRANSITIONAL`), `2500` (`RISK_OFF`).
  - Validates both `RiskAssessment` and `TradeProposal` via Pydantic and runs `validate_state_models`.

### 3.2 Live Database Execution Output (`usdjpy_signals.db`)

Compiled 2-node `StateGraph(AgentState)` (`macro_analyst` → `risk_committee`) output against live `usdjpy_signals.db`:

```json
{
  "run_id": "live-verify-001",
  "timestamp_utc": "2026-11-18T09:48:00Z",
  "spot_price": 159.13800048828125,
  "yield_bias": "BULLISH_USD",
  "macro_assessment": {
    "yield_bias": "BULLISH_USD",
    "us_10y": 5.102999687194824,
    "jp_10y": 2.925,
    "spread_10y": 2.178,
    "spread_bps": 217.8,
    "policy_divergence_summary": "US-JP 10Y spread at 2.178% (217.8 bps; US10Y=5.103%, JP10Y=2.925%) implies BULLISH_USD. 1 imminent key USD/JPY catalyst(s) near window [FOMC Meeting Minutes] dampen directional confidence.",
    "confidence": 0.6345,
    "upcoming_high_impact_events_count": 4
  },
  "risk_regime": "TRANSITIONAL",
  "intervention_risk_score": 100.0,
  "risk_assessment": {
    "risk_regime": "TRANSITIONAL",
    "vix_level": 23.850000381469727,
    "sp500_price": 7340.6298828125,
    "nikkei_price": 59165.12890625,
    "gold_price": 3769.60009765625,
    "intervention_risk_score": 100.0,
    "intervention_alert_level": "CRITICAL",
    "highest_mof_tier": 4,
    "veto_trade": true,
    "risk_notes": "Regime=TRANSITIONAL (VIX=23.85); USD/JPY spot=159.138; MoF Tier 4 across 25 alert(s) -> score=100.00 [CRITICAL] (base=80.0, volume=15.0, spot=15.0); veto_trade=True.",
    "flagged_headlines_count": 10
  },
  "trade_proposal": {
    "proposal_id": "prop-live-verify-001",
    "timestamp_utc": "2026-11-18T09:48:00Z",
    "pair": "USD/JPY",
    "action": "HOLD",
    "entry_price": null,
    "stop_loss": null,
    "take_profit": null,
    "risk_reward_ratio": null,
    "position_size_units": 0,
    "rationale": "HOLD recommended: risk veto triggered (intervention_score=100.00, highest_mof_tier=4, regime=TRANSITIONAL).",
    "macro_bias": "BULLISH_USD",
    "risk_regime": "TRANSITIONAL",
    "intervention_risk_score": 100.0
  },
  "approval_status": "NOT_REQUIRED",
  "audit_log": [
    "macro_analyst:yield_bias=BULLISH_USD;spread_10y=2.178;confidence=0.6345;high_impact_events=4;near_catalysts=1",
    "risk_committee:regime=TRANSITIONAL;intervention_score=100.00;alert_level=CRITICAL;highest_tier=4;veto=True;action=HOLD;approval=NOT_REQUIRED"
  ]
}
```

### 3.3 Automated Test Suite Output (`.venv/bin/python -m unittest -v tests/test_nodes.py`)

```text
test_live_db_triggers_intervention_veto_and_hold (tests.test_nodes.TestLiveDatabaseIntegration.test_live_db_triggers_intervention_veto_and_hold) ... ok
test_macro_event_window_dampens_confidence (tests.test_nodes.TestSpecialistNodesScenarios.test_macro_event_window_dampens_confidence)
Imminent FOMC/CPI/BoJ releases near the run window dampen macro confidence. ... ok
test_neutral_spread_boundaries_produce_hold (tests.test_nodes.TestSpecialistNodesScenarios.test_neutral_spread_boundaries_produce_hold)
Exact spread boundaries 1.0% and 2.0% evaluate to NEUTRAL and HOLD. ... ok
test_risk_off_sizing_vs_elevated_intervention_veto (tests.test_nodes.TestSpecialistNodesScenarios.test_risk_off_sizing_vs_elevated_intervention_veto)
RISK_OFF scales BUY to 2500 units when calm, but vetoes BULLISH_USD if intervention is elevated. ... ok
test_scenario_a_carry_trade_greenlight (tests.test_nodes.TestSpecialistNodesScenarios.test_scenario_a_carry_trade_greenlight)
Wide spread (+2.40%), low VIX (14.5 RISK_ON), spot 152.00, no Tier 3/4 alerts -> BUY PENDING. ... ok
test_scenario_b_bearish_carry_unwind (tests.test_nodes.TestSpecialistNodesScenarios.test_scenario_b_bearish_carry_unwind)
Narrow spread (+0.60%), VIX 19.0 (TRANSITIONAL), spot 145.00, no Tier 3/4 alerts -> SELL PENDING. ... ok
test_scenario_c_intervention_veto (tests.test_nodes.TestSpecialistNodesScenarios.test_scenario_c_intervention_veto)
Wide spread (+2.30%), spot 159.50, Tier 4 'rate check' headline -> veto & HOLD NOT_REQUIRED. ... ok
test_scenario_d_empty_db_graceful_degradation (tests.test_nodes.TestSpecialistNodesScenarios.test_scenario_d_empty_db_graceful_degradation)
Empty schema-initialized SQLite DB degrades gracefully to NEUTRAL / HOLD without errors. ... ok
test_spot_barrier_bonuses_and_clamping (tests.test_nodes.TestSpecialistNodesScenarios.test_spot_barrier_bonuses_and_clamping)
Verify spot proximity adjustments around 157.0, 158.5, and 160.0 and [0, 100] clamping. ... ok

----------------------------------------------------------------------
Ran 9 tests in 0.197s

OK
```
