"""
Unit and Integration Tests for LangGraph Approval Gate & Paper Execution (Task P3-05).

Covers:
  1. Interrupt breakpoint (`interrupt_before=["paper_execution"]`) on actionable
     BUY / SELL proposals using `InMemorySaver`.
  2. Resuming with `APPROVED` executes the paper trade, populates `ExecutionReceipt`,
     and writes a JSONL line to `paper_trades.jsonl`.
  3. Resuming with `REJECTED` produces `CANCELLED_REJECTED`, sets units=0, and does
     not write to `paper_trades.jsonl`.
  4. HOLD / intervention-vetoed proposals (both synthetic fixture and live
     `usdjpy_signals.db`) route through `finalize_hold` directly to `END` without pausing.
  5. CLI `run_pipeline.py` execution (`--json`, `--auto-approve`, `--reject`,
     interactive approval, and live `usdjpy_signals.db`).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fx_signal_reader.agent.graph import (
    ExecutionReceipt,
    build_signal_graph,
    resume_with_decision,
)
from fx_signal_reader.agent.state import (
    ApprovalStatus,
    MacroAssessment,
    TradeAction,
    create_initial_state,
    validate_state_models,
)
from fx_signal_reader.cli import pipeline as run_pipeline
from fx_signal_reader.core import (
    DEFAULT_DB_PATH as LIVE_DB_PATH,
    DEFAULT_SCHEMA_PATH as SCHEMA_PATH,
    PROJECT_ROOT,
)


def _build_fixture_db(
    db_file: Path,
    *,
    us_10y: float,
    jp_10y: float,
    spot: float,
    vix: float,
    headlines: list[tuple[str, str, str, str, str, str]] | None = None,
) -> Path:
    """Create a schema-initialized SQLite fixture DB for approval-gate testing."""
    spread = round(us_10y - jp_10y, 4)
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("2026-11-18T09:00:00Z", us_10y - 0.30, us_10y, jp_10y, spread, "fixture"),
        )
        ticks = [
            ("JPY=X", "2026-11-18T09:00:00Z", spot, spot - 0.1, spot + 0.1, spot - 0.15, 0.0),
            ("^TNX", "2026-11-18T09:00:00Z", us_10y, us_10y, us_10y + 0.02, us_10y - 0.02, 0.0),
            ("^VIX", "2026-11-18T09:00:00Z", vix, vix, vix + 0.2, vix - 0.2, 0.0),
            ("^GSPC", "2026-11-18T09:00:00Z", 7400.0, 7380.0, 7410.0, 7375.0, 1000000.0),
            ("^N225", "2026-11-18T09:00:00Z", 59500.0, 59400.0, 59600.0, 59350.0, 0.0),
            ("GC=F", "2026-11-18T09:00:00Z", 3750.0, 3745.0, 3755.0, 3740.0, 500.0),
        ]
        conn.executemany(
            """
            INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ticks,
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


class TestGraphApprovalGateAndExecution(unittest.TestCase):
    """End-to-end tests for LangGraph interrupt_before gate and paper trade execution."""

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="fx_graph_test_"))
        self.ledger_path = self._tmpdir / "paper_trades.jsonl"
        self.buy_db = _build_fixture_db(
            self._tmpdir / "buy_scenario.db",
            us_10y=5.20,
            jp_10y=2.80,
            spot=152.00,
            vix=14.5,
        )
        self.sell_db = _build_fixture_db(
            self._tmpdir / "sell_scenario.db",
            us_10y=3.50,
            jp_10y=2.90,
            spot=145.00,
            vix=19.0,
        )
        self.veto_db = _build_fixture_db(
            self._tmpdir / "veto_scenario.db",
            us_10y=5.20,
            jp_10y=2.90,
            spot=159.50,
            vix=21.0,
            headlines=[
                (
                    "hash-tier4-1",
                    "2026-11-18T08:45:00Z",
                    "Reuters",
                    "Bank of Japan conducts rate check with major dealers near 160",
                    "https://example.com/rate-check",
                    "Bank of Japan, rate check",
                )
            ],
        )

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_buy_proposal_interrupts_then_executes_on_approval(self) -> None:
        graph = build_signal_graph(
            db_path=str(self.buy_db),
            ledger_path=self.ledger_path,
            interrupt_on_actionable=True,
        )
        config = {"configurable": {"thread_id": "thread-buy-approve"}}
        initial = create_initial_state(run_id="run-buy-approve", timestamp_utc="2026-11-18T09:05:00Z")

        paused_state = graph.invoke(initial, config=config)
        snapshot = graph.get_state(config)

        # Must pause strictly before paper_execution with no receipt yet
        self.assertEqual(snapshot.next, ("paper_execution",))
        self.assertEqual(paused_state["trade_proposal"]["action"], TradeAction.BUY.value)
        self.assertEqual(paused_state["approval_status"], ApprovalStatus.PENDING.value)
        self.assertIsNone(paused_state.get("execution_receipt"))
        self.assertFalse(self.ledger_path.exists())

        # Resume with human approval
        final_state = resume_with_decision(
            graph,
            config,
            approved=True,
            reviewer_notes="Approved carry trade on clean RISK_ON spread.",
        )
        snapshot_after = graph.get_state(config)
        self.assertEqual(snapshot_after.next, ())
        self.assertEqual(final_state["approval_status"], ApprovalStatus.APPROVED.value)
        self.assertEqual(
            final_state["reviewer_notes"],
            "Approved carry trade on clean RISK_ON spread.",
        )

        receipt = ExecutionReceipt.model_validate(final_state["execution_receipt"])
        self.assertEqual(receipt.status, "EXECUTED")
        self.assertEqual(receipt.action, TradeAction.BUY)
        self.assertAlmostEqual(receipt.fill_price or 0.0, 152.00)
        self.assertEqual(receipt.position_size_units, 20266)
        self.assertEqual(receipt.approval_status, ApprovalStatus.APPROVED)
        validate_state_models(final_state)

        # Verify ledger persistence
        self.assertTrue(self.ledger_path.exists())
        lines = self.ledger_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        recorded = json.loads(lines[0])
        self.assertEqual(recorded["order_id"], receipt.order_id)
        self.assertEqual(recorded["status"], "EXECUTED")
        self.assertEqual(recorded["action"], "BUY")

    def test_sell_proposal_interrupts_then_cancels_on_rejection(self) -> None:
        graph = build_signal_graph(
            db_path=str(self.sell_db),
            ledger_path=self.ledger_path,
            interrupt_on_actionable=True,
        )
        config = {"configurable": {"thread_id": "thread-sell-reject"}}
        initial = create_initial_state(run_id="run-sell-reject", timestamp_utc="2026-11-18T09:05:00Z")

        paused_state = graph.invoke(initial, config=config)
        snapshot = graph.get_state(config)
        self.assertEqual(snapshot.next, ("paper_execution",))
        self.assertEqual(paused_state["trade_proposal"]["action"], TradeAction.SELL.value)
        self.assertIsNone(paused_state.get("execution_receipt"))

        final_state = resume_with_decision(
            graph,
            config,
            approved=False,
            reviewer_notes="Too close to support; hold fire.",
        )
        snapshot_after = graph.get_state(config)
        self.assertEqual(snapshot_after.next, ())
        self.assertEqual(final_state["approval_status"], ApprovalStatus.REJECTED.value)

        receipt = ExecutionReceipt.model_validate(final_state["execution_receipt"])
        self.assertEqual(receipt.status, "CANCELLED_REJECTED")
        self.assertEqual(receipt.action, TradeAction.SELL)
        self.assertEqual(receipt.position_size_units, 0)
        self.assertIsNone(receipt.fill_price)
        self.assertEqual(receipt.reviewer_notes, "Too close to support; hold fire.")
        self.assertFalse(self.ledger_path.exists())

    def test_hold_vetoed_proposal_bypasses_interrupt_to_end(self) -> None:
        graph = build_signal_graph(
            db_path=str(self.veto_db),
            ledger_path=self.ledger_path,
            interrupt_on_actionable=True,
        )
        config = {"configurable": {"thread_id": "thread-veto-hold"}}
        initial = create_initial_state(run_id="run-veto-hold", timestamp_utc="2026-11-18T09:05:00Z")

        final_state = graph.invoke(initial, config=config)
        snapshot = graph.get_state(config)

        # Should finish completely without interrupting for human approval
        self.assertEqual(snapshot.next, ())
        self.assertEqual(final_state["trade_proposal"]["action"], TradeAction.HOLD.value)
        self.assertEqual(final_state["approval_status"], ApprovalStatus.NOT_REQUIRED.value)

        receipt = ExecutionReceipt.model_validate(final_state["execution_receipt"])
        self.assertEqual(receipt.status, "SKIPPED_HOLD")
        self.assertEqual(receipt.position_size_units, 0)
        self.assertFalse(self.ledger_path.exists())

    def test_graph_execution_with_phase9_signals(self) -> None:
        """Verify full graph execution properly threads 2Y spread, oil terms-of-trade, and Gotobi session."""
        db_file = self._tmpdir / "phase9_graph.db"
        conn = sqlite3.connect(db_file)
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO yield_spreads (timestamp_utc, us_2y, jp_2y, spread_2y, us_10y, jp_10y, spread_10y, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-11-20T00:00:00Z", 4.50, 1.00, 3.50, 5.20, 2.80, 2.40, "fixture_p9"),
        )
        ticks = [
            ("JPY=X", "2026-11-20T00:15:00Z", 152.00, 151.90, 152.10, 151.85, 0.0),
            ("^TNX", "2026-11-20T00:15:00Z", 5.20, 5.18, 5.22, 5.18, 0.0),
            ("^VIX", "2026-11-20T00:15:00Z", 15.0, 15.2, 15.3, 14.9, 0.0),
            ("^GSPC", "2026-11-20T00:15:00Z", 7400.0, 7380.0, 7410.0, 7375.0, 1000000.0),
            ("^N225", "2026-11-20T00:15:00Z", 59500.0, 59400.0, 59600.0, 59350.0, 0.0),
            ("GC=F", "2026-11-20T00:15:00Z", 3750.0, 3745.0, 3755.0, 3740.0, 500.0),
        ]
        for i in range(6):
            ticks.append(("CL=F", f"2026-11-1{i}T00:15:00Z", 70.0 + i, 70.0, 71.0 + i, 69.5, 1000.0))
        conn.executemany(
            """
            INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ticks,
        )
        conn.commit()
        conn.close()

        graph = build_signal_graph(
            db_path=str(db_file),
            ledger_path=self.ledger_path,
            interrupt_on_actionable=True,
        )
        config = {"configurable": {"thread_id": "thread-p9-graph"}}
        initial = create_initial_state(run_id="run-p9-graph", timestamp_utc="2026-11-20T00:15:00Z")

        paused_state = graph.invoke(initial, config=config)
        snapshot = graph.get_state(config)
        self.assertEqual(snapshot.next, ("paper_execution",))

        # Check macro assessment phase 9 fields
        macro = paused_state["macro_assessment"]
        self.assertEqual(macro["spread_2y"], 3.50)
        self.assertEqual(macro["terms_of_trade_bias"], "BULLISH_USD")
        self.assertIsNotNone(macro["gotobi_session"])
        self.assertEqual(macro["gotobi_session"]["session_phase"], "PRE_FIX")
        self.assertTrue(macro["gotobi_session"]["is_gotobi_pre_fix"])

        # Check trade proposal phase 9 fields & Gotobi conviction boost
        proposal = paused_state["trade_proposal"]
        self.assertEqual(proposal["action"], "BUY")
        self.assertIn("Gotobi Pre-Fix window", proposal["rationale"])
        self.assertIn("Terms of trade: BULLISH_USD", proposal["rationale"])
        self.assertGreaterEqual(proposal["confidence"], macro["confidence"])

        # Resume with approval
        final_state = resume_with_decision(
            graph,
            config,
            approved=True,
            reviewer_notes="Approved Gotobi Pre-Fix buy with 2Y confirmation.",
        )
        validate_state_models(final_state)
        receipt = ExecutionReceipt.model_validate(final_state["execution_receipt"])
        self.assertEqual(receipt.status, "EXECUTED")

    @unittest.skipUnless(LIVE_DB_PATH.exists(), "Live usdjpy_signals.db not present")
    def test_live_database_end_to_end_graph_execution(self) -> None:
        graph = build_signal_graph(
            db_path=str(LIVE_DB_PATH),
            ledger_path=self.ledger_path,
            interrupt_on_actionable=True,
        )
        config = {"configurable": {"thread_id": "thread-live-db"}}
        final_state = graph.invoke(create_initial_state(run_id="run-live-db"), config=config)
        snapshot = graph.get_state(config)

        validate_state_models(final_state)
        self.assertIsNotNone(final_state["trade_proposal"])
        if final_state["trade_proposal"]["action"] == TradeAction.HOLD.value:
            self.assertEqual(snapshot.next, ())
            receipt = ExecutionReceipt.model_validate(final_state["execution_receipt"])
            self.assertEqual(receipt.status, "SKIPPED_HOLD")
        else:  # pragma: no cover
            self.assertEqual(snapshot.next, ("paper_execution",))

    def test_cli_run_pipeline_auto_approve_and_reject(self) -> None:
        # 1. --auto-approve on actionable BUY DB
        proc_approve = subprocess.run(
            [
                sys.executable,
                "-m",
                "fx_signal_reader.cli.pipeline",
                "--db-path",
                str(self.buy_db),
                "--ledger-path",
                str(self.ledger_path),
                "--auto-approve",
                "--reviewer-notes",
                "CLI auto-approve check",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload_approve = json.loads(proc_approve.stdout)
        self.assertFalse(payload_approve["paused_for_approval"])
        self.assertEqual(payload_approve["state"]["execution_receipt"]["status"], "EXECUTED")
        self.assertEqual(payload_approve["state"]["approval_status"], "APPROVED")

        # 2. --reject on actionable BUY DB
        proc_reject = subprocess.run(
            [
                sys.executable,
                "-m",
                "fx_signal_reader.cli.pipeline",
                "--db-path",
                str(self.buy_db),
                "--ledger-path",
                str(self.ledger_path),
                "--reject",
                "--reviewer-notes",
                "CLI reject check",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload_reject = json.loads(proc_reject.stdout)
        self.assertFalse(payload_reject["paused_for_approval"])
        self.assertEqual(payload_reject["state"]["execution_receipt"]["status"], "CANCELLED_REJECTED")
        self.assertEqual(payload_reject["state"]["approval_status"], "REJECTED")

        # Only 1 line written to ledger (from the approved run)
        lines = self.ledger_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)

    @unittest.skipUnless(LIVE_DB_PATH.exists(), "Live usdjpy_signals.db not present")
    def test_cli_run_pipeline_live_db_text_and_json(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "fx_signal_reader.cli.pipeline",
                "--db-path",
                str(LIVE_DB_PATH),
                "--ledger-path",
                str(self.ledger_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("USD/JPY Signal Reader - LangGraph Multi-Agent Pipeline Report", proc.stdout)
        self.assertIn("Paper Execution Receipt", proc.stdout)

    def test_interactive_approval_prompt_path(self) -> None:
        buf = io.StringIO()
        with patch("builtins.input", return_value="y"), contextlib.redirect_stdout(buf):
            res = run_pipeline.run_signal_pipeline(
                db_path=str(self.buy_db),
                ledger_path=self.ledger_path,
                thread_id="thread-interactive-yes",
                interactive=True,
            )
        self.assertFalse(res["paused_for_approval"])
        self.assertEqual(res["state"]["approval_status"], "APPROVED")
        self.assertEqual(res["state"]["execution_receipt"]["status"], "EXECUTED")
        self.assertIn("HUMAN APPROVAL REQUIRED", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
