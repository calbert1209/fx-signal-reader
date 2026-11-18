# P3-03: LangGraph state definition

* **Phase:** Phase 3: Agent Integration (MCP & LangGraph)
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T09:39:36Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Define the shared LangGraph workflow state schema and Pydantic v2 structured output models for passing macro, risk, intervention, and trade proposal signals across specialist agent nodes, the human-in-the-loop approval gate, and paper-trading execution.

### Deliverables Implemented:
1. **`agent/state.py`:**
   - Five `str, Enum` types (`YieldBias`, `RiskRegime`, `InterventionAlertLevel`, `TradeAction`, `ApprovalStatus`).
   - Three Pydantic v2 `BaseModel` schemas (`MacroAssessment`, `RiskAssessment`, `TradeProposal`) with field-level bounds validation.
   - `AgentState(TypedDict)` with `Annotated[list[str], operator.add]` reducer on `audit_log`.
   - Helper utilities `create_initial_state()` (using `normalizer.normalize_datetime_utc`) and `validate_state_models()`.
2. **`tests/test_state.py`:**
   - Unit and integration test suite covering enum definitions, Pydantic validation and bounds checking, JSON enum serialization (`model_dump(mode="json")`), state helpers, and LangGraph `StateGraph(AgentState)` compilation and `operator.add` reducer accumulation.

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete (`agent/state.py` and `tests/test_state.py`)
- [x] Verified in local sandbox / environment (`.venv` with Python 3.11.17, `pydantic 2.14.3`, `langgraph 1.2.22` — 15/15 tests passed)
- [x] Linked artifacts and logs updated (Drive task doc `1m3DogdyV_IVGlhlaPgKv4vsuCQsTi2h5` and Google Sheet tracker Row 13)

---

## 3. Execution Log & Findings

### 3.1 Schema Summary (`agent/state.py`)

#### Enumerations (`str, Enum`)
| Enum Class | Members | Purpose |
| :--- | :--- | :--- |
| `YieldBias` | `BULLISH_USD`, `BEARISH_USD`, `NEUTRAL` | Directional USD/JPY bias from US–JP yield spreads & central bank divergence |
| `RiskRegime` | `RISK_ON`, `RISK_OFF`, `TRANSITIONAL` | Cross-asset risk appetite (S&P 500, Nikkei 225, VIX, Gold) |
| `InterventionAlertLevel` | `NONE`, `WATCH`, `WARNING`, `CRITICAL` | MoF / BoJ FX intervention alert severity |
| `TradeAction` | `BUY`, `SELL`, `HOLD` | Proposed USD/JPY trade action |
| `ApprovalStatus` | `PENDING`, `APPROVED`, `REJECTED`, `NOT_REQUIRED` | Human-in-the-loop approval gate decision state |

#### Pydantic v2 Structured Output Models
1. **`MacroAssessment(BaseModel)`**
   - `yield_bias: YieldBias`
   - `us_10y: float | None = None`, `jp_10y: float | None = None`, `spread_10y: float | None = None`, `spread_bps: float | None = None`
   - `upcoming_high_impact_events: list[dict[str, Any]] = Field(default_factory=list)`
   - `policy_divergence_summary: str`
   - `confidence: float = Field(ge=0.0, le=1.0)`
2. **`RiskAssessment(BaseModel)`**
   - `risk_regime: RiskRegime`
   - `vix_level: float | None = None`, `sp500_price: float | None = None`, `nikkei_price: float | None = None`, `gold_price: float | None = None`
   - `intervention_risk_score: float = Field(ge=0.0, le=100.0)`
   - `intervention_alert_level: InterventionAlertLevel`
   - `highest_mof_tier: int = Field(ge=0, le=4)`
   - `flagged_headlines: list[dict[str, Any]] = Field(default_factory=list)`
   - `veto_trade: bool = False`
   - `risk_notes: str`
3. **`TradeProposal(BaseModel)`**
   - `proposal_id: str`, `timestamp_utc: str`, `pair: str = "USD/JPY"`, `action: TradeAction`
   - `entry_price: float | None = None`, `stop_loss: float | None = None`, `take_profit: float | None = None`
   - `risk_reward_ratio: float | None = Field(default=None, ge=0.0)`
   - `position_size_units: int = Field(default=0, ge=0)`
   - `rationale: str`, `macro_bias: YieldBias`, `risk_regime: RiskRegime`
   - `intervention_risk_score: float = Field(ge=0.0, le=100.0)`

#### Shared LangGraph State (`AgentState(TypedDict)`)
| Field | Type | Default / Reducer |
| :--- | :--- | :--- |
| `run_id` | `str` | UUIDv4 if omitted in `create_initial_state` |
| `timestamp_utc` | `str` | Normalized ISO-8601 UTC via `normalizer.normalize_datetime_utc` |
| `spot_price` | `float \| None` | `None` |
| `yield_bias` | `str` | `"NEUTRAL"` |
| `macro_assessment` | `dict[str, Any] \| None` | `None` (JSON-serialized `MacroAssessment`) |
| `risk_regime` | `str` | `"TRANSITIONAL"` |
| `intervention_risk_score` | `float` | `0.0` |
| `risk_assessment` | `dict[str, Any] \| None` | `None` (JSON-serialized `RiskAssessment`) |
| `trade_proposal` | `dict[str, Any] \| None` | `None` (JSON-serialized `TradeProposal`) |
| `approval_status` | `str` | `"PENDING"` |
| `reviewer_notes` | `str \| None` | `None` |
| `execution_receipt` | `dict[str, Any] \| None` | `None` |
| `audit_log` | `Annotated[list[str], operator.add]` | `[]`; LangGraph concatenates node updates via `operator.add` |

#### Helper Functions
- `create_initial_state(run_id: str | None = None, timestamp_utc: str | None = None) -> AgentState`: Initializes neutral, JSON-serializable state with strict UTC timestamp normalization.
- `validate_state_models(state: AgentState) -> dict[str, BaseModel | None]`: Re-validates embedded `macro_assessment`, `risk_assessment`, and `trade_proposal` dicts into typed Pydantic v2 model instances (returning `None` for unset fields and raising `ValidationError` on schema violations).

### 3.2 Design Decisions
- **JSON-Safe `AgentState` Embedding:** Specialist assessment and proposal payloads are stored as `dict[str, Any] | None` inside `AgentState` (produced via `.model_dump(mode="json")`), keeping state natively serializable for LangGraph checkpointers while preserving strict runtime schema validation through `validate_state_models()`.
- **String-Backed Enums (`str, Enum`):** Enums inherit from `(str, Enum)` without `use_enum_values=True` so validated models retain enum type identity in Python while serializing cleanly to plain strings under `model_dump(mode="json")`.

### 3.3 Automated Test Suite Output (`.venv/bin/python -m unittest -v tests/test_state.py`)
```text
test_create_initial_state_defaults (tests.test_state.TestAgentStateHelpers.test_create_initial_state_defaults) ... ok
test_create_initial_state_with_explicit_args_and_normalization (tests.test_state.TestAgentStateHelpers.test_create_initial_state_with_explicit_args_and_normalization) ... ok
test_validate_state_models_empty_and_populated (tests.test_state.TestAgentStateHelpers.test_validate_state_models_empty_and_populated) ... ok
test_validate_state_models_rejects_invalid_embedded_payloads (tests.test_state.TestAgentStateHelpers.test_validate_state_models_rejects_invalid_embedded_payloads) ... ok
test_audit_log_annotation_uses_operator_add (tests.test_state.TestLangGraphStateCompilation.test_audit_log_annotation_uses_operator_add) ... ok
test_state_graph_compile_and_reducer_accumulation (tests.test_state.TestLangGraphStateCompilation.test_state_graph_compile_and_reducer_accumulation) ... ok
test_enum_json_serialization (tests.test_state.TestPydanticValidationAndBounds.test_enum_json_serialization) ... ok
test_macro_assessment_confidence_bounds (tests.test_state.TestPydanticValidationAndBounds.test_macro_assessment_confidence_bounds) ... ok
test_macro_assessment_invalid_enum_rejected (tests.test_state.TestPydanticValidationAndBounds.test_macro_assessment_invalid_enum_rejected) ... ok
test_macro_assessment_valid_and_defaults (tests.test_state.TestPydanticValidationAndBounds.test_macro_assessment_valid_and_defaults) ... ok
test_risk_assessment_mof_tier_bounds (tests.test_state.TestPydanticValidationAndBounds.test_risk_assessment_mof_tier_bounds) ... ok
test_risk_assessment_score_bounds (tests.test_state.TestPydanticValidationAndBounds.test_risk_assessment_score_bounds) ... ok
test_risk_assessment_valid_and_defaults (tests.test_state.TestPydanticValidationAndBounds.test_risk_assessment_valid_and_defaults) ... ok
test_trade_proposal_valid_and_bounds (tests.test_state.TestPydanticValidationAndBounds.test_trade_proposal_valid_and_bounds) ... ok
test_enum_values_and_str_behavior (tests.test_state.TestStateEnums.test_enum_values_and_str_behavior) ... ok

----------------------------------------------------------------------
Ran 15 tests in 0.011s

OK
```
