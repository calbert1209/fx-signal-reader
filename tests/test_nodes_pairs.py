"""
Tests for Multi-Pair Specialist Agent Nodes (Task P8-05).

Validates:
  1. macro_analyst_node with EUR/USD:
     - Spot price resolution for EURUSD=X.
     - Currency filtering for calendar events (EUR, USD vs JPY).
     - Macro yield bias determination based on DE10Y - US10Y thresholds (-1.0 / -2.0).
     - Catalyst keyword matching for European entities and releases (ECB, Lagarde, etc.).
  2. risk_committee_node with EUR/USD (CENTRAL_BANK policy):
     - Verbal intervention score defaults to 0.0 / NONE (no MoF ladder effect).
     - Event Blackout & Divergence Filter vetoes trade within ±4h of Tier-1 catalysts.
     - Trades outside blackout window generate valid TradeProposal with 5-decimal precision.
     - Equal-dollar risk position units (quote-in-USD sizing: dollar_risk / sl_distance).
     - Global volatility regime veto (VIX >= 25.0).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from fx_signal_reader.agent.nodes import (
    compute_bracket_distances,
    macro_analyst_node,
    risk_committee_node,
)
from fx_signal_reader.agent.state import (
    ApprovalStatus,
    InterventionAlertLevel,
    RiskRegime,
    TradeAction,
    YieldBias,
    create_initial_state,
)
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH as SCHEMA_PATH
from fx_signal_reader.core.pair_profile import get_pair_profile


def _create_eurusd_fixture_db(
    db_file: Path,
    *,
    yield_row: tuple[str, float | None, float | None, float | None, float | None, str] | None = None,
    ticks: list[tuple[str, str, float, float | None, float | None, float | None, float | None]] | None = None,
    events: list[tuple[str, str, str, str, str, str | None, str | None, str | None]] | None = None,
) -> Path:
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
        conn.commit()
    finally:
        conn.close()
    return db_file


class TestMacroAnalystEurUsd(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_eurusd.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_eurusd_macro_bias_bullish(self) -> None:
        # DE10Y = 3.5%, US10Y = 4.0% -> spread = -0.5% > -1.0% threshold (BULLISH)
        _create_eurusd_fixture_db(
            self.db_path,
            yield_row=("2026-03-01T10:00:00Z", 3.8, 4.0, 3.5, -0.5, "yfinance+bundesbank"),
            ticks=[
                ("EURUSD=X", "2026-03-01T10:00:00Z", 1.08500, 1.08450, 1.08600, 1.08400, 1000.0),
            ],
            events=[
                ("ev-1", "2026-03-05T12:00:00Z", "EUR", "ECB Monetary Policy Statement", "High", None, None, None),
                ("ev-2", "2026-03-05T14:00:00Z", "JPY", "BOJ Core CPI", "High", None, None, None),  # JPY should be ignored
            ],
        )

        state = create_initial_state(pair="EUR/USD")
        update = macro_analyst_node(state, db_path=str(self.db_path))

        self.assertEqual(update["spot_price"], 1.08500)
        self.assertEqual(update["yield_bias"], YieldBias.BULLISH.value)
        assessment = update["macro_assessment"]
        self.assertEqual(assessment["yield_bias"], YieldBias.BULLISH.value)
        self.assertAlmostEqual(assessment["spread_10y"], -0.5)
        # JPY event excluded, only EUR event kept
        self.assertEqual(len(assessment["upcoming_high_impact_events"]), 1)
        self.assertEqual(assessment["upcoming_high_impact_events"][0]["currency"], "EUR")

    def test_eurusd_macro_bias_bearish(self) -> None:
        # DE10Y = 1.8%, US10Y = 4.2% -> spread = -2.4% < -2.0% threshold (BEARISH)
        _create_eurusd_fixture_db(
            self.db_path,
            yield_row=("2026-03-01T10:00:00Z", 4.0, 4.2, 1.8, -2.4, "yfinance+bundesbank"),
            ticks=[
                ("EURUSD=X", "2026-03-01T10:00:00Z", 1.04250, 1.04200, 1.04300, 1.04100, 800.0),
            ],
        )

        state = create_initial_state(pair="EUR/USD")
        update = macro_analyst_node(state, db_path=str(self.db_path))

        self.assertEqual(update["spot_price"], 1.04250)
        self.assertEqual(update["yield_bias"], YieldBias.BEARISH.value)
        self.assertAlmostEqual(update["macro_assessment"]["spread_10y"], -2.4)


class TestRiskCommitteeEurUsd(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_eurusd.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_eurusd_trade_proposal_clean_greenlight(self) -> None:
        # Clean market: VIX = 14.5 (RISK_ON), no blackout events, bullish bias
        _create_eurusd_fixture_db(
            self.db_path,
            yield_row=("2026-03-01T12:00:00Z", 3.8, 4.0, 3.5, -0.5, "yfinance+bundesbank"),
            ticks=[
                ("EURUSD=X", "2026-03-01T12:00:00Z", 1.08520, 1.08500, 1.08600, 1.08450, 1500.0),
                ("^VIX", "2026-03-01T12:00:00Z", 14.5, 14.8, 15.0, 14.2, 0.0),
                ("^STOXX50E", "2026-03-01T12:00:00Z", 5000.0, 4990.0, 5010.0, 4980.0, 0.0),
                ("^GSPC", "2026-03-01T12:00:00Z", 5800.0, 5790.0, 5810.0, 5780.0, 0.0),
                ("GC=F", "2026-03-01T12:00:00Z", 2650.0, 2645.0, 2655.0, 2640.0, 0.0),
            ],
            events=[
                # Far in the future (+48h), not in ±4h blackout
                ("ev-1", "2026-03-03T12:00:00Z", "EUR", "ECB Rate Decision", "High", None, None, None),
            ],
        )

        state = create_initial_state(pair="EUR/USD", account_capital_usd=10000.0, risk_fraction=0.01)
        state["spot_price"] = 1.08520
        state["yield_bias"] = YieldBias.BULLISH.value

        update = risk_committee_node(state, db_path=str(self.db_path))

        risk_assessment = update["risk_assessment"]
        self.assertEqual(risk_assessment["risk_regime"], RiskRegime.RISK_ON.value)
        self.assertEqual(risk_assessment["intervention_risk_score"], 0.0)
        self.assertEqual(risk_assessment["highest_mof_tier"], 0)
        self.assertFalse(risk_assessment["veto_trade"])

        proposal = update["trade_proposal"]
        self.assertEqual(proposal["action"], TradeAction.BUY.value)
        self.assertEqual(proposal["entry_price"], 1.08520)
        # Default EUR/USD SL is 50 pips = 0.0050, TP is 100 pips = 0.0100
        self.assertAlmostEqual(proposal["stop_loss"], 1.08020, places=5)
        self.assertAlmostEqual(proposal["take_profit"], 1.09520, places=5)
        self.assertEqual(proposal["risk_reward_ratio"], 2.0)
        # Sizing: $100 risk / 0.0050 SL distance = 20,000 units
        self.assertEqual(proposal["position_size_units"], 20000)
        self.assertEqual(update["approval_status"], ApprovalStatus.PENDING.value)

    def test_eurusd_event_blackout_veto(self) -> None:
        # Catalyst within 2 hours of current time (within ±4h window) -> VETO
        _create_eurusd_fixture_db(
            self.db_path,
            yield_row=("2026-03-01T12:00:00Z", 3.8, 4.0, 3.5, -0.5, "yfinance+bundesbank"),
            ticks=[
                ("EURUSD=X", "2026-03-01T12:00:00Z", 1.08520, 1.08500, 1.08600, 1.08450, 1500.0),
                ("^VIX", "2026-03-01T12:00:00Z", 14.5, 14.8, 15.0, 14.2, 0.0),
            ],
            events=[
                ("ev-ecb", "2026-03-01T13:30:00Z", "EUR", "ECB Monetary Policy Statement & Rate Decision", "High", None, None, None),
            ],
        )

        state = create_initial_state(pair="EUR/USD")
        state["timestamp_utc"] = "2026-03-01T12:00:00Z"
        state["spot_price"] = 1.08520
        state["yield_bias"] = YieldBias.BULLISH.value

        update = risk_committee_node(state, db_path=str(self.db_path))

        risk_assessment = update["risk_assessment"]
        self.assertTrue(risk_assessment["veto_trade"])
        self.assertIn("event blackout", risk_assessment["risk_notes"])

        proposal = update["trade_proposal"]
        self.assertEqual(proposal["action"], TradeAction.HOLD.value)
        self.assertEqual(update["approval_status"], ApprovalStatus.NOT_REQUIRED.value)

    def test_eurusd_vix_risk_off_veto(self) -> None:
        # VIX = 28.5 >= 25.0 -> RISK_OFF veto
        _create_eurusd_fixture_db(
            self.db_path,
            yield_row=("2026-03-01T12:00:00Z", 3.8, 4.0, 3.5, -0.5, "yfinance+bundesbank"),
            ticks=[
                ("EURUSD=X", "2026-03-01T12:00:00Z", 1.08520, 1.08500, 1.08600, 1.08450, 1500.0),
                ("^VIX", "2026-03-01T12:00:00Z", 28.5, 28.0, 29.0, 27.5, 0.0),
            ],
        )

        state = create_initial_state(pair="EUR/USD")
        state["spot_price"] = 1.08520
        state["yield_bias"] = YieldBias.BULLISH.value

        update = risk_committee_node(state, db_path=str(self.db_path))

        self.assertTrue(update["risk_assessment"]["veto_trade"])
        self.assertEqual(update["risk_assessment"]["risk_regime"], RiskRegime.RISK_OFF.value)
        self.assertEqual(update["trade_proposal"]["action"], TradeAction.HOLD.value)


class TestBracketDistances(unittest.TestCase):
    def test_compute_bracket_distances_eurusd(self) -> None:
        profile = get_pair_profile("EUR/USD")
        sl_dist, tp_dist = compute_bracket_distances(profile)
        self.assertEqual(sl_dist, 0.0050)
        self.assertEqual(tp_dist, 0.0100)

    def test_compute_bracket_distances_usdjpy(self) -> None:
        profile = get_pair_profile("USD/JPY")
        sl_dist, tp_dist = compute_bracket_distances(profile)
        self.assertEqual(sl_dist, 0.75)
        self.assertEqual(tp_dist, 1.50)


if __name__ == "__main__":
    unittest.main()
