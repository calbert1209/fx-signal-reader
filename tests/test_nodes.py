"""
Unit and Integration Tests for Specialist LangGraph Agent Nodes (Task P3-04).

Covers:
  1. Live `usdjpy_signals.db` end-to-end execution of `macro_analyst_node` and
     `risk_committee_node` (verifying elevated intervention veto / HOLD).
  2. Synthetic SQLite fixture scenarios:
     - Scenario A: Carry Trade Greenlight (`BULLISH_USD`, `RISK_ON` -> `BUY`, `PENDING`)
     - Scenario B: Bearish Carry Unwind (`BEARISH_USD`, `TRANSITIONAL` -> `SELL`, `PENDING`)
     - Scenario C: Intervention Veto (`BULLISH_USD`, Tier 4 rate check near 160 -> `HOLD`, `NOT_REQUIRED`)
     - Scenario D: Empty DB graceful degradation (`NEUTRAL` / `HOLD` without crashing)
  3. Yield-spread boundary conditions, macro event confidence adjustment,
     spot barrier proximity bonuses, RISK_OFF sizing vs. veto escalation.
  4. Compiled 2-node LangGraph `StateGraph(AgentState)` execution via
     `make_macro_analyst_node` and `make_risk_committee_node`.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

from langgraph.graph import END, START, StateGraph

from fx_signal_reader.agent.nodes import (
    macro_analyst_node,
    make_macro_analyst_node,
    make_risk_committee_node,
    risk_committee_node,
)
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
from fx_signal_reader.core import (
    DEFAULT_DB_PATH as LIVE_DB_PATH,
    DEFAULT_SCHEMA_PATH as SCHEMA_PATH,
    PROJECT_ROOT,
)


def _build_two_node_graph(db_path: str | None = None):
    """Compile a minimal 2-node StateGraph(AgentState): macro_analyst -> risk_committee."""
    builder = StateGraph(AgentState)
    builder.add_node("macro_analyst", make_macro_analyst_node(db_path=db_path))
    builder.add_node("risk_committee", make_risk_committee_node(db_path=db_path))
    builder.add_edge(START, "macro_analyst")
    builder.add_edge("macro_analyst", "risk_committee")
    builder.add_edge("risk_committee", END)
    return builder.compile()


def _create_fixture_db(
    db_file: Path,
    *,
    yield_row: tuple[str, float | None, float | None, float | None, float | None, str] | None = None,
    ticks: list[tuple[str, str, float, float | None, float | None, float | None, float | None]] | None = None,
    events: list[tuple[str, str, str, str, str, str | None, str | None, str | None]] | None = None,
    headlines: list[tuple[str, str, str, str, str, str]] | None = None,
) -> Path:
    """Create a schema-initialized SQLite database populated with scenario fixtures."""
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        if yield_row is not None:
            conn.execute(
                """
                INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                yield_row,
            )
        if ticks:
            conn.executemany(
                """
                INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ticks,
            )
        if events:
            conn.executemany(
                """
                INSERT INTO macro_events (event_hash, timestamp_utc, currency, title, impact, forecast, previous, actual)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                events,
            )
        if headlines:
            conn.executemany(
                """
                INSERT INTO news_headlines (url_hash, published_utc, source, title, url, matched_keywords)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                headlines,
            )
        conn.commit()
    finally:
        conn.close()
    return db_file


def _standard_ticks(
    *,
    spot: float,
    vix: float,
    sp500: float = 6000.0,
    nikkei: float = 39000.0,
    gold: float = 2650.0,
    ts: str = "2026-11-18T09:00:00Z",
) -> list[tuple[str, str, float, float | None, float | None, float | None, float | None]]:
    return [
        ("JPY=X", ts, spot, spot - 0.1, spot + 0.1, spot - 0.2, 0.0),
        ("^VIX", ts, vix, vix, vix + 0.3, vix - 0.2, 0.0),
        ("^GSPC", ts, sp500, sp500 - 10.0, sp500 + 15.0, sp500 - 12.0, 1000000.0),
        ("^N225", ts, nikkei, nikkei - 50.0, nikkei + 100.0, nikkei - 80.0, 500000.0),
        ("GC=F", ts, gold, gold - 2.0, gold + 5.0, gold - 4.0, 1500.0),
    ]


class TestSpecialistNodesScenarios(unittest.TestCase):
    """Test specialist agent nodes across synthetic scenarios and LangGraph execution."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="fx_nodes_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scenario_a_carry_trade_greenlight(self) -> None:
        """Wide spread (+2.40%), low VIX (14.5 RISK_ON), spot 152.00, no Tier 3/4 alerts -> BUY PENDING."""
        db_path = _create_fixture_db(
            self.temp_dir / "scenario_a.db",
            yield_row=("2026-11-18T08:45:00Z", 4.50, 4.80, 2.40, 2.40, "test_fixture"),
            ticks=_standard_ticks(spot=152.00, vix=14.5),
            headlines=[
                (
                    "h_t1",
                    "2026-11-18T08:00:00Z",
                    "Reuters",
                    "Japan official says watching currency moves closely",
                    "https://example.com/t1",
                    "watching closely",
                )
            ],
        )

        graph = _build_two_node_graph(str(db_path))
        initial = create_initial_state(
            run_id="scen-a-001",
            timestamp_utc="2026-11-18T09:00:00Z",
        )
        final_state = graph.invoke(initial)

        self.assertEqual(final_state["yield_bias"], YieldBias.BULLISH_USD.value)
        self.assertAlmostEqual(final_state["spot_price"], 152.00, places=3)
        self.assertEqual(final_state["risk_regime"], RiskRegime.RISK_ON.value)
        self.assertLess(final_state["intervention_risk_score"], 70.0)
        self.assertEqual(final_state["approval_status"], ApprovalStatus.PENDING.value)

        validated = validate_state_models(final_state)
        macro = validated["macro_assessment"]
        risk = validated["risk_assessment"]
        proposal = validated["trade_proposal"]

        self.assertIsInstance(macro, MacroAssessment)
        self.assertIsInstance(risk, RiskAssessment)
        self.assertIsInstance(proposal, TradeProposal)
        assert macro is not None and risk is not None and proposal is not None

        self.assertEqual(macro.yield_bias, YieldBias.BULLISH_USD)
        self.assertAlmostEqual(macro.spread_10y or 0.0, 2.40, places=4)
        self.assertFalse(risk.veto_trade)
        self.assertEqual(risk.highest_mof_tier, 1)
        self.assertEqual(risk.intervention_alert_level, InterventionAlertLevel.WATCH)

        self.assertEqual(proposal.action, TradeAction.BUY)
        self.assertEqual(proposal.entry_price, 152.0)
        self.assertEqual(proposal.stop_loss, 151.25)
        self.assertEqual(proposal.take_profit, 153.5)
        self.assertEqual(proposal.risk_reward_ratio, 2.0)
        self.assertEqual(proposal.position_size_units, 20266)
        self.assertEqual(len(final_state["audit_log"]), 2)

        # Ensure entire state is strictly JSON serializable for checkpointers
        json.dumps(final_state)

    def test_scenario_b_bearish_carry_unwind(self) -> None:
        """Narrow spread (+0.60%), VIX 19.0 (TRANSITIONAL), spot 145.00, no Tier 3/4 alerts -> SELL PENDING."""
        db_path = _create_fixture_db(
            self.temp_dir / "scenario_b.db",
            yield_row=("2026-11-18T08:45:00Z", 3.00, 3.10, 2.50, 0.60, "test_fixture"),
            ticks=_standard_ticks(spot=145.00, vix=19.0),
        )

        graph = _build_two_node_graph(str(db_path))
        final_state = graph.invoke(
            create_initial_state(run_id="scen-b-001", timestamp_utc="2026-11-18T09:00:00Z")
        )

        self.assertEqual(final_state["yield_bias"], YieldBias.BEARISH_USD.value)
        self.assertEqual(final_state["risk_regime"], RiskRegime.TRANSITIONAL.value)
        self.assertEqual(final_state["approval_status"], ApprovalStatus.PENDING.value)

        validated = validate_state_models(final_state)
        risk = validated["risk_assessment"]
        proposal = validated["trade_proposal"]
        assert risk is not None and proposal is not None

        self.assertFalse(risk.veto_trade)
        self.assertEqual(risk.highest_mof_tier, 0)
        self.assertEqual(risk.intervention_alert_level, InterventionAlertLevel.NONE)
        self.assertEqual(risk.intervention_risk_score, 5.0)

        self.assertEqual(proposal.action, TradeAction.SELL)
        self.assertEqual(proposal.entry_price, 145.0)
        self.assertEqual(proposal.stop_loss, 145.75)
        self.assertEqual(proposal.take_profit, 143.5)
        self.assertEqual(proposal.risk_reward_ratio, 2.0)
        self.assertEqual(proposal.position_size_units, 19333)

    def test_scenario_c_intervention_veto(self) -> None:
        """Wide spread (+2.30%), spot 159.50, Tier 4 'rate check' headline -> veto & HOLD NOT_REQUIRED."""
        db_path = _create_fixture_db(
            self.temp_dir / "scenario_c.db",
            yield_row=("2026-11-18T08:45:00Z", 4.70, 5.10, 2.80, 2.30, "test_fixture"),
            ticks=_standard_ticks(spot=159.50, vix=16.0),
            headlines=[
                (
                    "h_t4",
                    "2026-11-18T08:50:00Z",
                    "Reuters",
                    "Bank of Japan conducts rate check with dealers as USD/JPY nears 160",
                    "https://example.com/t4",
                    "Bank of Japan, rate check",
                )
            ],
        )

        graph = _build_two_node_graph(str(db_path))
        final_state = graph.invoke(
            create_initial_state(run_id="scen-c-001", timestamp_utc="2026-11-18T09:00:00Z")
        )

        self.assertEqual(final_state["yield_bias"], YieldBias.BULLISH_USD.value)
        self.assertGreaterEqual(final_state["intervention_risk_score"], 70.0)
        self.assertAlmostEqual(final_state["intervention_risk_score"], 95.0, places=2)
        self.assertEqual(final_state["approval_status"], ApprovalStatus.NOT_REQUIRED.value)

        validated = validate_state_models(final_state)
        risk = validated["risk_assessment"]
        proposal = validated["trade_proposal"]
        assert risk is not None and proposal is not None

        self.assertTrue(risk.veto_trade)
        self.assertEqual(risk.highest_mof_tier, 4)
        self.assertEqual(risk.intervention_alert_level, InterventionAlertLevel.CRITICAL)

        self.assertEqual(proposal.action, TradeAction.HOLD)
        self.assertIsNone(proposal.entry_price)
        self.assertIsNone(proposal.stop_loss)
        self.assertIsNone(proposal.take_profit)
        self.assertIsNone(proposal.risk_reward_ratio)
        self.assertEqual(proposal.position_size_units, 0)

    def test_scenario_d_empty_db_graceful_degradation(self) -> None:
        """Empty schema-initialized SQLite DB degrades gracefully to NEUTRAL / HOLD without errors."""
        db_path = _create_fixture_db(self.temp_dir / "empty.db")

        graph = _build_two_node_graph(str(db_path))
        final_state = graph.invoke(
            create_initial_state(run_id="scen-d-001", timestamp_utc="2026-11-18T09:00:00Z")
        )

        self.assertIsNone(final_state["spot_price"])
        self.assertEqual(final_state["yield_bias"], YieldBias.NEUTRAL.value)
        self.assertEqual(final_state["risk_regime"], RiskRegime.TRANSITIONAL.value)
        self.assertEqual(final_state["approval_status"], ApprovalStatus.NOT_REQUIRED.value)
        self.assertEqual(len(final_state["audit_log"]), 2)

        validated = validate_state_models(final_state)
        macro = validated["macro_assessment"]
        risk = validated["risk_assessment"]
        proposal = validated["trade_proposal"]
        assert macro is not None and risk is not None and proposal is not None

        self.assertEqual(macro.yield_bias, YieldBias.NEUTRAL)
        self.assertIsNone(macro.spread_10y)
        self.assertEqual(macro.upcoming_high_impact_events, [])
        self.assertFalse(risk.veto_trade)
        self.assertEqual(risk.highest_mof_tier, 0)
        self.assertEqual(risk.intervention_alert_level, InterventionAlertLevel.NONE)
        self.assertEqual(proposal.action, TradeAction.HOLD)
        self.assertEqual(proposal.position_size_units, 0)

    def test_neutral_spread_boundaries_produce_hold(self) -> None:
        """Exact spread boundaries 1.0% and 2.0% evaluate to NEUTRAL and HOLD."""
        for boundary_spread in (1.0, 1.5, 2.0):
            with self.subTest(spread_10y=boundary_spread):
                db_path = _create_fixture_db(
                    self.temp_dir / f"neutral_{boundary_spread}.db",
                    yield_row=(
                        "2026-11-18T08:45:00Z",
                        3.5,
                        3.5 + boundary_spread,
                        3.5,
                        boundary_spread,
                        "test_fixture",
                    ),
                    ticks=_standard_ticks(spot=150.0, vix=15.0),
                )
                graph = _build_two_node_graph(str(db_path))
                final_state = graph.invoke(
                    create_initial_state(timestamp_utc="2026-11-18T09:00:00Z")
                )
                self.assertEqual(final_state["yield_bias"], YieldBias.NEUTRAL.value)
                self.assertEqual(final_state["trade_proposal"]["action"], TradeAction.HOLD.value)
                self.assertEqual(final_state["approval_status"], ApprovalStatus.NOT_REQUIRED.value)

    def test_macro_event_window_dampens_confidence(self) -> None:
        """Imminent FOMC/CPI/BoJ releases near the run window dampen macro confidence."""
        quiet_db = _create_fixture_db(
            self.temp_dir / "quiet_macro.db",
            yield_row=("2026-11-18T08:45:00Z", 4.6, 5.0, 2.6, 2.4, "test_fixture"),
            ticks=_standard_ticks(spot=152.0, vix=15.0),
        )
        catalyst_db = _create_fixture_db(
            self.temp_dir / "catalyst_macro.db",
            yield_row=("2026-11-18T08:45:00Z", 4.6, 5.0, 2.6, 2.4, "test_fixture"),
            ticks=_standard_ticks(spot=152.0, vix=15.0),
            events=[
                ("ev_fomc", "2026-11-18T19:00:00Z", "USD", "FOMC Meeting Minutes", "High", None, None, None),
                ("ev_cpi", "2026-11-19T13:30:00Z", "USD", "US CPI m/m", "High", "0.3%", "0.2%", None),
            ],
        )

        ref_state = create_initial_state(run_id="conf-test", timestamp_utc="2026-11-18T09:00:00Z")
        quiet_out = macro_analyst_node(ref_state, db_path=str(quiet_db))
        catalyst_out = macro_analyst_node(ref_state, db_path=str(catalyst_db))

        quiet_conf = quiet_out["macro_assessment"]["confidence"]
        catalyst_conf = catalyst_out["macro_assessment"]["confidence"]
        self.assertGreater(quiet_conf, catalyst_conf)
        self.assertGreaterEqual(catalyst_conf, 0.1)
        self.assertLessEqual(quiet_conf, 1.0)
        self.assertIn("FOMC Meeting Minutes", catalyst_out["macro_assessment"]["policy_divergence_summary"])

    def test_risk_off_regime_unconditionally_vetoes_entries(self) -> None:
        """P7-01: RISK_OFF (VIX >= 25.0) unconditionally vetoes new trade entries (action=HOLD)."""
        calm_risk_off_db = _create_fixture_db(
            self.temp_dir / "risk_off_calm.db",
            ticks=_standard_ticks(spot=152.0, vix=26.5),
        )
        base_state = create_initial_state(run_id="ro-calm", timestamp_utc="2026-11-18T09:00:00Z")
        base_state["yield_bias"] = YieldBias.BULLISH_USD.value
        calm_out = risk_committee_node(base_state, db_path=str(calm_risk_off_db))

        self.assertEqual(calm_out["risk_regime"], RiskRegime.RISK_OFF.value)
        self.assertTrue(calm_out["risk_assessment"]["veto_trade"])
        self.assertEqual(calm_out["trade_proposal"]["action"], TradeAction.HOLD.value)
        self.assertEqual(calm_out["trade_proposal"]["position_size_units"], 0)
        self.assertEqual(calm_out["approval_status"], ApprovalStatus.NOT_REQUIRED.value)
        self.assertIn("RISK_OFF regime", calm_out["risk_assessment"]["risk_notes"])

        elevated_risk_off_db = _create_fixture_db(
            self.temp_dir / "risk_off_elevated.db",
            ticks=_standard_ticks(spot=157.50, vix=26.5),
            headlines=[
                (
                    "h_t2",
                    "2026-11-18T08:40:00Z",
                    "Reuters",
                    "Japan MoF warns against rapid currency moves and speculative trading",
                    "https://example.com/t2",
                    "rapid moves, speculative moves",
                )
            ],
        )
        elevated_out = risk_committee_node(base_state, db_path=str(elevated_risk_off_db))
        self.assertEqual(elevated_out["risk_regime"], RiskRegime.RISK_OFF.value)
        self.assertGreaterEqual(elevated_out["intervention_risk_score"], 40.0)
        self.assertTrue(elevated_out["risk_assessment"]["veto_trade"])
        self.assertEqual(elevated_out["trade_proposal"]["action"], TradeAction.HOLD.value)
        self.assertEqual(elevated_out["trade_proposal"]["position_size_units"], 0)
        self.assertEqual(elevated_out["approval_status"], ApprovalStatus.NOT_REQUIRED.value)

    def test_compute_position_units_bounds_and_risk_sizing(self) -> None:
        """P7-02: compute_position_units satisfies equal-dollar risk bounds and zero guards."""
        from fx_signal_reader.agent.nodes import compute_position_units

        # Spot 150.00, SL 0.75, $10,000 capital, 1% ($100) -> 20,000 units
        self.assertEqual(compute_position_units(150.0, 0.75, 10000.0, 0.01), 20000)
        # Spot 152.00, SL 0.75, $10,000 capital, 1% ($100) -> 20,266 units
        self.assertEqual(compute_position_units(152.0, 0.75, 10000.0, 0.01), 20266)
        # Spot 145.00, SL 0.75, $10,000 capital, 1% ($100) -> 19,333 units
        self.assertEqual(compute_position_units(145.0, 0.75, 10000.0, 0.01), 19333)
        # 2% risk ($200) -> 40,000 units
        self.assertEqual(compute_position_units(150.0, 0.75, 10000.0, 0.02), 40000)
        # Wider SL (1.50 JPY) -> 10,000 units
        self.assertEqual(compute_position_units(150.0, 1.50, 10000.0, 0.01), 10000)
        # Invalid / non-positive guards
        self.assertEqual(compute_position_units(None), 0)
        self.assertEqual(compute_position_units(0.0), 0)
        self.assertEqual(compute_position_units(-150.0), 0)
        self.assertEqual(compute_position_units(150.0, stop_loss_distance_jpy=0.0), 0)
        self.assertEqual(compute_position_units(150.0, account_capital_usd=0.0), 0)
        self.assertEqual(compute_position_units(150.0, risk_fraction=-0.01), 0)

    def test_spot_barrier_bonuses_and_clamping(self) -> None:
        """Verify spot proximity adjustments around 157.0, 158.5, and 160.0 and [0, 100] clamping."""
        cases = [
            (157.50, 0, 10.0),   # Tier 0 (5) + 5 = 10
            (159.00, 0, 20.0),   # Tier 0 (5) + 15 = 20
            (160.25, 0, 25.0),   # Tier 0 (5) + 20 = 25
            (160.50, 4, 100.0),  # Tier 4 (80) + 20 = 100 (clamped)
        ]
        for idx, (spot, tier, expected_score) in enumerate(cases):
            with self.subTest(spot=spot, tier=tier):
                headlines = []
                if tier == 4:
                    headlines = [
                        (
                            f"h4_{idx}_a",
                            "2026-11-18T08:50:00Z",
                            "Reuters",
                            "Suspected Yen Intervention hits market",
                            f"https://example.com/4a_{idx}",
                            "physical/suspected intervention",
                        ),
                        (
                            f"h4_{idx}_b",
                            "2026-11-18T08:45:00Z",
                            "Reuters",
                            "BoJ rate check reported by Tokyo dealers",
                            f"https://example.com/4b_{idx}",
                            "rate check",
                        ),
                    ]
                db_path = _create_fixture_db(
                    self.temp_dir / f"barrier_{idx}.db",
                    ticks=_standard_ticks(spot=spot, vix=15.0),
                    headlines=headlines,
                )
                state = create_initial_state(timestamp_utc="2026-11-18T09:00:00Z")
                out = risk_committee_node(state, db_path=str(db_path))
                self.assertAlmostEqual(out["intervention_risk_score"], expected_score, places=2)


class TestLiveDatabaseIntegration(unittest.TestCase):
    """Verify both specialist nodes and compiled graph against live usdjpy_signals.db."""

    def test_live_db_triggers_intervention_veto_and_hold(self) -> None:
        self.assertTrue(
            LIVE_DB_PATH.exists(),
            f"Expected live database at {LIVE_DB_PATH}",
        )

        graph = _build_two_node_graph(None)  # Uses default usdjpy_signals.db
        initial = create_initial_state(
            run_id="live-integration-001",
            timestamp_utc="2026-11-18T09:45:00Z",
        )
        final_state = graph.invoke(initial)

        # Verify live DB macro state (JPY=X ~159.1, spread_10y ~2.178%)
        self.assertIsNotNone(final_state["spot_price"])
        self.assertGreater(final_state["spot_price"], 158.0)
        self.assertEqual(final_state["yield_bias"], YieldBias.BULLISH_USD.value)

        validated = validate_state_models(final_state)
        macro = validated["macro_assessment"]
        risk = validated["risk_assessment"]
        proposal = validated["trade_proposal"]

        self.assertIsInstance(macro, MacroAssessment)
        self.assertIsInstance(risk, RiskAssessment)
        self.assertIsInstance(proposal, TradeProposal)
        assert macro is not None and risk is not None and proposal is not None

        self.assertGreater(macro.spread_10y or 0.0, 2.0)
        self.assertGreater(len(macro.upcoming_high_impact_events), 0)

        # Verify live DB risk state (VIX ~23.85 TRANSITIONAL, Tier 4 MoF alerts -> veto & HOLD)
        self.assertIsNotNone(risk.vix_level)
        self.assertEqual(risk.risk_regime, RiskRegime.TRANSITIONAL)
        self.assertEqual(risk.highest_mof_tier, 4)
        self.assertGreaterEqual(risk.intervention_risk_score, 70.0)
        self.assertEqual(risk.intervention_alert_level, InterventionAlertLevel.CRITICAL)
        self.assertTrue(risk.veto_trade)
        self.assertGreater(len(risk.flagged_headlines), 0)

        self.assertEqual(proposal.action, TradeAction.HOLD)
        self.assertEqual(proposal.position_size_units, 0)
        self.assertEqual(final_state["approval_status"], ApprovalStatus.NOT_REQUIRED.value)
        self.assertEqual(len(final_state["audit_log"]), 2)
        self.assertTrue(final_state["audit_log"][0].startswith("macro_analyst:"))
        self.assertTrue(final_state["audit_log"][1].startswith("risk_committee:"))


if __name__ == "__main__":
    unittest.main()
