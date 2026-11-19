"""
Unit and integration tests for LangGraph AgentState and Pydantic v2 models.

Covers:
  1. Enum definitions (`YieldBias`, `RiskRegime`, `InterventionAlertLevel`, `TradeAction`, `ApprovalStatus`).
  2. Pydantic v2 validation and boundary constraints (`MacroAssessment`, `RiskAssessment`, `TradeProposal`).
  3. JSON enum serialization via `model_dump(mode="json")`.
  4. `create_initial_state` normalization and default safety.
  5. `validate_state_models` round-trip validation and error propagation.
  6. `StateGraph(AgentState)` compilation and `operator.add` reducer accumulation on `audit_log`.
"""

import json
import operator
from typing import get_args, get_origin, get_type_hints
import unittest
import uuid

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from fx_signal_reader.agent.state import (
    AgentState,
    ApprovalStatus,
    InterventionAlertLevel,
    MacroAssessment,
    RiskAssessment,
    RiskRegime,
    TradeAction,
    TradeProposal,
    YieldBias,
    create_initial_state,
    validate_state_models,
)
from fx_signal_reader.core.normalizer import is_valid_iso8601_utc


def _sample_macro_payload(**overrides) -> dict:
    payload = {
        "yield_bias": "BULLISH_USD",
        "us_10y": 5.103,
        "jp_10y": 2.925,
        "spread_10y": 2.178,
        "spread_bps": 217.8,
        "upcoming_high_impact_events": [
            {
                "timestamp_utc": "2026-11-18T13:30:00Z",
                "currency": "USD",
                "title": "CPI m/m",
                "impact": "High",
            }
        ],
        "policy_divergence_summary": "US-JP 10Y spread remains elevated above 215 bps.",
        "confidence": 0.85,
    }
    payload.update(overrides)
    return payload


def _sample_risk_payload(**overrides) -> dict:
    payload = {
        "risk_regime": "RISK_ON",
        "vix_level": 14.8,
        "sp500_price": 5980.25,
        "nikkei_price": 38950.0,
        "gold_price": 2610.5,
        "intervention_risk_score": 42.5,
        "intervention_alert_level": "WATCH",
        "highest_mof_tier": 2,
        "flagged_headlines": [
            {
                "title": "Japan MoF watching FX moves with sense of urgency",
                "tier": 2,
                "published_utc": "2026-11-18T08:15:00Z",
            }
        ],
        "veto_trade": False,
        "risk_notes": "Moderate verbal warning tier 2 detected; volatility subdued.",
    }
    payload.update(overrides)
    return payload


def _sample_proposal_payload(**overrides) -> dict:
    payload = {
        "proposal_id": "prop-20261118-001",
        "timestamp_utc": "2026-11-18T09:00:00Z",
        "pair": "USD/JPY",
        "action": "BUY",
        "entry_price": 159.10,
        "stop_loss": 158.40,
        "take_profit": 160.50,
        "risk_reward_ratio": 2.0,
        "position_size_units": 10000,
        "rationale": "Wide 10Y spread and risk-on equity tone favor long USD/JPY.",
        "macro_bias": "BULLISH_USD",
        "risk_regime": "RISK_ON",
        "intervention_risk_score": 42.5,
    }
    payload.update(overrides)
    return payload


class TestStateEnums(unittest.TestCase):
    """Verify str-backed enum members and values."""

    def test_enum_values_and_str_behavior(self):
        self.assertEqual(
            [m.value for m in YieldBias],
            ["BULLISH", "BEARISH", "NEUTRAL"],
        )
        self.assertEqual(
            [m.value for m in RiskRegime],
            ["RISK_ON", "RISK_OFF", "TRANSITIONAL"],
        )
        self.assertEqual(
            [m.value for m in InterventionAlertLevel],
            ["NONE", "WATCH", "WARNING", "CRITICAL"],
        )
        self.assertEqual(
            [m.value for m in TradeAction],
            ["BUY", "SELL", "HOLD"],
        )
        self.assertEqual(
            [m.value for m in ApprovalStatus],
            ["PENDING", "APPROVED", "REJECTED", "NOT_REQUIRED"],
        )

        self.assertIsInstance(YieldBias.BULLISH_USD, str)
        self.assertEqual(YieldBias.BULLISH, "BULLISH")
        # Legacy aliases resolve to the generalized members.
        self.assertIs(YieldBias.BULLISH_USD, YieldBias.BULLISH)
        self.assertIs(YieldBias.BEARISH_USD, YieldBias.BEARISH)
        self.assertEqual(YieldBias.BULLISH_USD.value, "BULLISH")

    def test_legacy_yield_bias_strings_validate(self):
        self.assertIs(YieldBias("BULLISH_USD"), YieldBias.BULLISH)
        self.assertIs(YieldBias("BEARISH_USD"), YieldBias.BEARISH)
        self.assertIs(YieldBias("BULLISH"), YieldBias.BULLISH)
        with self.assertRaises(ValueError):
            YieldBias("SIDEWAYS_USD")
        for legacy, expected in (("BULLISH_USD", YieldBias.BULLISH), ("BEARISH_USD", YieldBias.BEARISH)):
            macro = MacroAssessment.model_validate(_sample_macro_payload(yield_bias=legacy))
            self.assertIs(macro.yield_bias, expected)
            proposal = TradeProposal.model_validate(_sample_proposal_payload(macro_bias=legacy))
            self.assertIs(proposal.macro_bias, expected)
            self.assertEqual(proposal.model_dump(mode="json")["macro_bias"], expected.value)
        macro_new = MacroAssessment.model_validate(_sample_macro_payload(yield_bias="BEARISH"))
        self.assertIs(macro_new.yield_bias, YieldBias.BEARISH)
        with self.assertRaises(ValidationError):
            MacroAssessment.model_validate(_sample_macro_payload(yield_bias="SIDEWAYS_USD"))

    def test_create_initial_state_pair_default_and_override(self):
        self.assertEqual(create_initial_state()["pair"], "USD/JPY")
        self.assertEqual(create_initial_state(pair="EUR/USD")["pair"], "EUR/USD")
        self.assertEqual(TradeProposal.model_validate(_sample_proposal_payload()).pair, "USD/JPY")
        self.assertEqual(ApprovalStatus.NOT_REQUIRED, "NOT_REQUIRED")


class TestPydanticValidationAndBounds(unittest.TestCase):
    """Verify Pydantic v2 validation, bounds checking, defaults, and JSON serialization."""

    def test_macro_assessment_valid_and_defaults(self):
        minimal = MacroAssessment(
            yield_bias=YieldBias.NEUTRAL,
            policy_divergence_summary="No directional edge.",
            confidence=0.0,
        )
        self.assertIsNone(minimal.us_10y)
        self.assertIsNone(minimal.jp_10y)
        self.assertIsNone(minimal.spread_10y)
        self.assertIsNone(minimal.spread_bps)
        self.assertEqual(minimal.upcoming_high_impact_events, [])
        self.assertEqual(minimal.confidence, 0.0)

        full = MacroAssessment.model_validate(_sample_macro_payload(confidence=1.0))
        self.assertEqual(full.yield_bias, YieldBias.BULLISH_USD)
        self.assertEqual(full.confidence, 1.0)
        self.assertEqual(len(full.upcoming_high_impact_events), 1)

    def test_macro_assessment_confidence_bounds(self):
        for invalid_confidence in (-0.01, -1.0, 1.01, 2.5, float("nan"), float("inf")):
            with self.subTest(confidence=invalid_confidence):
                with self.assertRaises(ValidationError):
                    MacroAssessment.model_validate(
                        _sample_macro_payload(confidence=invalid_confidence)
                    )

    def test_macro_assessment_invalid_enum_rejected(self):
        with self.assertRaises(ValidationError):
            MacroAssessment.model_validate(_sample_macro_payload(yield_bias="INVALID_BIAS"))

    def test_risk_assessment_valid_and_defaults(self):
        minimal = RiskAssessment(
            risk_regime=RiskRegime.TRANSITIONAL,
            intervention_risk_score=0.0,
            intervention_alert_level=InterventionAlertLevel.NONE,
            highest_mof_tier=0,
            risk_notes="Baseline quiet session.",
        )
        self.assertIsNone(minimal.vix_level)
        self.assertIsNone(minimal.sp500_price)
        self.assertIsNone(minimal.nikkei_price)
        self.assertIsNone(minimal.gold_price)
        self.assertEqual(minimal.flagged_headlines, [])
        self.assertFalse(minimal.veto_trade)

        boundary = RiskAssessment.model_validate(
            _sample_risk_payload(
                intervention_risk_score=100.0,
                highest_mof_tier=4,
                intervention_alert_level="CRITICAL",
                veto_trade=True,
            )
        )
        self.assertEqual(boundary.intervention_risk_score, 100.0)
        self.assertEqual(boundary.highest_mof_tier, 4)
        self.assertTrue(boundary.veto_trade)

    def test_risk_assessment_score_bounds(self):
        for invalid_score in (-0.01, -10.0, 100.01, 150.0, float("nan"), float("inf")):
            with self.subTest(intervention_risk_score=invalid_score):
                with self.assertRaises(ValidationError):
                    RiskAssessment.model_validate(
                        _sample_risk_payload(intervention_risk_score=invalid_score)
                    )

    def test_risk_assessment_mof_tier_bounds(self):
        for valid_tier in (0, 1, 2, 3, 4):
            model = RiskAssessment.model_validate(
                _sample_risk_payload(highest_mof_tier=valid_tier)
            )
            self.assertEqual(model.highest_mof_tier, valid_tier)

        for invalid_tier in (-1, -5, 5, 10, 2.5):
            with self.subTest(highest_mof_tier=invalid_tier):
                with self.assertRaises(ValidationError):
                    RiskAssessment.model_validate(
                        _sample_risk_payload(highest_mof_tier=invalid_tier)
                    )

    def test_trade_proposal_valid_and_bounds(self):
        hold = TradeProposal(
            proposal_id="prop-hold",
            timestamp_utc="2026-11-18T09:00:00Z",
            action=TradeAction.HOLD,
            rationale="Intervention risk too elevated.",
            macro_bias=YieldBias.NEUTRAL,
            risk_regime=RiskRegime.RISK_OFF,
            intervention_risk_score=88.0,
        )
        self.assertEqual(hold.pair, "USD/JPY")
        self.assertEqual(hold.position_size_units, 0)
        self.assertIsNone(hold.risk_reward_ratio)

        for invalid_score in (-0.5, 100.5, float("nan"), float("inf")):
            with self.assertRaises(ValidationError):
                TradeProposal.model_validate(
                    _sample_proposal_payload(intervention_risk_score=invalid_score)
                )

        with self.assertRaises(ValidationError):
            TradeProposal.model_validate(_sample_proposal_payload(risk_reward_ratio=-0.5))

        with self.assertRaises(ValidationError):
            TradeProposal.model_validate(_sample_proposal_payload(position_size_units=-100))

    def test_enum_json_serialization(self):
        macro = MacroAssessment.model_validate(_sample_macro_payload())
        macro_json = macro.model_dump(mode="json")
        self.assertIs(type(macro_json["yield_bias"]), str)
        self.assertEqual(macro_json["yield_bias"], "BULLISH")

        risk = RiskAssessment.model_validate(_sample_risk_payload())
        risk_json = risk.model_dump(mode="json")
        self.assertIs(type(risk_json["risk_regime"]), str)
        self.assertIs(type(risk_json["intervention_alert_level"]), str)
        self.assertEqual(risk_json["risk_regime"], "RISK_ON")
        self.assertEqual(risk_json["intervention_alert_level"], "WATCH")

        proposal = TradeProposal.model_validate(_sample_proposal_payload())
        proposal_json = proposal.model_dump(mode="json")
        self.assertIs(type(proposal_json["action"]), str)
        self.assertIs(type(proposal_json["macro_bias"]), str)
        self.assertIs(type(proposal_json["risk_regime"]), str)
        self.assertEqual(proposal_json["action"], "BUY")
        self.assertEqual(proposal_json["macro_bias"], "BULLISH")
        self.assertEqual(proposal_json["risk_regime"], "RISK_ON")

        # Verify full round-trip through json.dumps/json.loads
        dumped_str = json.dumps(
            {"macro": macro_json, "risk": risk_json, "proposal": proposal_json}
        )
        reloaded = json.loads(dumped_str)
        self.assertEqual(reloaded["proposal"]["action"], "BUY")


class TestAgentStateHelpers(unittest.TestCase):
    """Test create_initial_state and validate_state_models helpers."""

    def test_create_initial_state_defaults(self):
        state = create_initial_state()
        # UUID4 check
        parsed_uuid = uuid.UUID(state["run_id"])
        self.assertEqual(parsed_uuid.version, 4)

        self.assertTrue(is_valid_iso8601_utc(state["timestamp_utc"]))
        self.assertIsNone(state["spot_price"])
        self.assertEqual(state["yield_bias"], "NEUTRAL")
        self.assertIsNone(state["macro_assessment"])
        self.assertEqual(state["risk_regime"], "TRANSITIONAL")
        self.assertEqual(state["intervention_risk_score"], 0.0)
        self.assertIsNone(state["risk_assessment"])
        self.assertIsNone(state["trade_proposal"])
        self.assertEqual(state["approval_status"], "PENDING")
        self.assertIsNone(state["reviewer_notes"])
        self.assertIsNone(state["execution_receipt"])
        self.assertEqual(state["audit_log"], [])

        # Ensure audit_log is not shared across calls
        other = create_initial_state()
        state["audit_log"].append("mutated")
        self.assertEqual(other["audit_log"], [])

    def test_create_initial_state_with_explicit_args_and_normalization(self):
        state = create_initial_state(
            run_id="run-usdjpy-42",
            timestamp_utc="2026-11-18T15:30:00+09:00",
        )
        self.assertEqual(state["run_id"], "run-usdjpy-42")
        self.assertEqual(state["timestamp_utc"], "2026-11-18T06:30:00Z")

        with self.assertRaises(ValueError):
            create_initial_state(run_id="bad-ts", timestamp_utc="not-a-date")

    def test_validate_state_models_empty_and_populated(self):
        initial = create_initial_state(run_id="val-1", timestamp_utc="2026-11-18T09:00:00Z")
        empty_validated = validate_state_models(initial)
        self.assertEqual(
            empty_validated,
            {
                "macro_assessment": None,
                "risk_assessment": None,
                "trade_proposal": None,
            },
        )

        macro = MacroAssessment.model_validate(_sample_macro_payload())
        risk = RiskAssessment.model_validate(_sample_risk_payload())
        proposal = TradeProposal.model_validate(_sample_proposal_payload())

        populated: AgentState = {
            **initial,
            "spot_price": 159.138,
            "yield_bias": macro.yield_bias.value,
            "macro_assessment": macro.model_dump(mode="json"),
            "risk_regime": risk.risk_regime.value,
            "intervention_risk_score": risk.intervention_risk_score,
            "risk_assessment": risk.model_dump(mode="json"),
            "trade_proposal": proposal.model_dump(mode="json"),
        }
        validated = validate_state_models(populated)
        self.assertIsInstance(validated["macro_assessment"], MacroAssessment)
        self.assertIsInstance(validated["risk_assessment"], RiskAssessment)
        self.assertIsInstance(validated["trade_proposal"], TradeProposal)
        self.assertEqual(validated["macro_assessment"], macro)
        self.assertEqual(validated["risk_assessment"], risk)
        self.assertEqual(validated["trade_proposal"], proposal)

    def test_validate_state_models_rejects_invalid_embedded_payloads(self):
        state = create_initial_state(run_id="val-err", timestamp_utc="2026-11-18T09:00:00Z")
        state["risk_assessment"] = _sample_risk_payload(highest_mof_tier=9)
        with self.assertRaises(ValidationError):
            validate_state_models(state)

        state2 = create_initial_state(run_id="val-err-2", timestamp_utc="2026-11-18T09:00:00Z")
        state2["macro_assessment"] = _sample_macro_payload(confidence=1.8)
        with self.assertRaises(ValidationError):
            validate_state_models(state2)

        state3 = create_initial_state(run_id="val-err-3", timestamp_utc="2026-11-18T09:00:00Z")
        state3["trade_proposal"] = _sample_proposal_payload(action="SHORT")
        with self.assertRaises(ValidationError):
            validate_state_models(state3)

        with self.assertRaises(TypeError):
            validate_state_models("not-a-dict")  # type: ignore[arg-type]


class TestLangGraphStateCompilation(unittest.TestCase):
    """Verify LangGraph StateGraph(AgentState) compilation and operator.add reducer."""

    def test_audit_log_annotation_uses_operator_add(self):
        hints = get_type_hints(AgentState, include_extras=True)
        audit_hint = hints["audit_log"]
        self.assertIsNotNone(get_origin(audit_hint))
        args = get_args(audit_hint)
        self.assertEqual(args[0], list[str])
        self.assertIs(args[1], operator.add)

    def test_state_graph_compile_and_reducer_accumulation(self):
        def macro_analyst_node(state: AgentState) -> dict:
            macro = MacroAssessment.model_validate(_sample_macro_payload())
            return {
                "spot_price": 159.138,
                "yield_bias": macro.yield_bias.value,
                "macro_assessment": macro.model_dump(mode="json"),
                "audit_log": [
                    f"macro_analyst:{state['run_id']}:yield_bias={macro.yield_bias.value}"
                ],
            }

        def risk_committee_node(state: AgentState) -> dict:
            risk = RiskAssessment.model_validate(_sample_risk_payload())
            return {
                "risk_regime": risk.risk_regime.value,
                "intervention_risk_score": risk.intervention_risk_score,
                "risk_assessment": risk.model_dump(mode="json"),
                "audit_log": [
                    f"risk_committee:regime={risk.risk_regime.value}:score={risk.intervention_risk_score}"
                ],
            }

        def proposal_node(state: AgentState) -> dict:
            proposal = TradeProposal.model_validate(_sample_proposal_payload())
            return {
                "trade_proposal": proposal.model_dump(mode="json"),
                "approval_status": ApprovalStatus.PENDING.value,
                "audit_log": [
                    f"proposal_node:action={proposal.action.value}:id={proposal.proposal_id}"
                ],
            }

        builder = StateGraph(AgentState)
        builder.add_node("macro_analyst", macro_analyst_node)
        builder.add_node("risk_committee", risk_committee_node)
        builder.add_node("proposal_builder", proposal_node)
        builder.add_edge(START, "macro_analyst")
        builder.add_edge("macro_analyst", "risk_committee")
        builder.add_edge("risk_committee", "proposal_builder")
        builder.add_edge("proposal_builder", END)

        graph = builder.compile()

        initial_state = create_initial_state(
            run_id="graph-run-001",
            timestamp_utc="2026-11-18T09:00:00Z",
        )
        initial_state["audit_log"] = ["init:pipeline_started"]

        final_state = graph.invoke(initial_state)

        self.assertEqual(final_state["run_id"], "graph-run-001")
        self.assertEqual(final_state["spot_price"], 159.138)
        self.assertEqual(final_state["yield_bias"], "BULLISH")
        self.assertEqual(final_state["risk_regime"], "RISK_ON")
        self.assertEqual(final_state["intervention_risk_score"], 42.5)
        self.assertEqual(final_state["approval_status"], "PENDING")

        # Verify operator.add concatenated audit entries across all nodes in order
        self.assertEqual(
            final_state["audit_log"],
            [
                "init:pipeline_started",
                "macro_analyst:graph-run-001:yield_bias=BULLISH",
                "risk_committee:regime=RISK_ON:score=42.5",
                "proposal_node:action=BUY:id=prop-20261118-001",
            ],
        )

        validated = validate_state_models(final_state)
        self.assertIsInstance(validated["macro_assessment"], MacroAssessment)
        self.assertIsInstance(validated["risk_assessment"], RiskAssessment)
        self.assertIsInstance(validated["trade_proposal"], TradeProposal)


if __name__ == "__main__":
    unittest.main()
