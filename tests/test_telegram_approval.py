"""
Unit and Integration Tests for Phase 10: Telegram Remote Approval Gate & Daily Summary Engine.
Task P10-05: Telegram Test Suite, Configuration & Documentation

Covers:
  1. 24-hour JST time parsing (`parse_jst_time`: '9:00' -> '09:00', '7:30' -> '07:30', '14:30' -> '14:30', invalid errors).
  2. Persistent `TelegramSettings` (`daily_summary_enabled=True`, `daily_summary_time_jst='09:00'` defaults & JSON round-trip).
  3. Message formatters (`format_proposal_message`, `format_execution_update`, `format_daily_summary` with mandatory Summary Settings block).
  4. End-to-end LangGraph pipeline (`run_signal_pipeline(..., telegram=True)`):
     - Inline button approval (`approve:<proposal_id>`) -> EXECUTED + writes to `paper_trades.jsonl` + edits message.
     - Inline button rejection (`reject:<proposal_id>`) -> CANCELLED_REJECTED + no ledger write + edits message.
     - Approval timeout expiry -> auto-rejects (`CANCELLED_REJECTED`) + marks message `⏱️ EXPIRED (Auto-Rejected)`.
     - Non-actionable `HOLD` proposal -> does not send Telegram approval alert.
  5. Chat ID authorization filtering (unauthorized `chat_id` callbacks and commands are ignored).
  6. Interactive `/summary` commands & scheduler daily summary dispatch:
     - `/summary on` -> enables and defaults to `09:00` JST.
     - `/summary on 7:30` & `/summary on 14:30` -> enables and schedules for `07:30` JST and `14:30` JST.
     - `/summary on 25:99` -> returns validation notice without corrupting settings.
     - `/summary off` -> disables daily summary (`daily_summary_enabled=False`) and suppresses scheduled delivery.
     - `/summary now` -> sends immediate on-demand summary (including Summary Settings block) even when daily schedule is `OFF`.
  7. CLI flags (`--telegram-test`, `--telegram-summary`, `fx-schedule --telegram --summary-time-jst`).
"""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from fx_signal_reader.cli import pipeline as pipeline_cli
from fx_signal_reader.cli import schedule as schedule_cli
from fx_signal_reader.core import DEFAULT_SCHEMA_PATH as SCHEMA_PATH
from fx_signal_reader.notifications import (
    DEFAULT_SUMMARY_TIME_JST,
    TelegramAPIError,
    TelegramBotClient,
    TelegramConfig,
    TelegramNotifier,
    TelegramSettings,
    build_approval_inline_keyboard,
    format_daily_summary,
    format_execution_update,
    format_proposal_message,
    format_summary_settings_block,
    parse_jst_time,
)


def _build_fixture_db(
    db_file: Path,
    *,
    us_10y: float,
    jp_10y: float,
    spot: float,
    vix: float,
) -> Path:
    """Create a schema-initialized SQLite fixture DB for Telegram approval testing."""
    spread_10y = round(us_10y - jp_10y, 4)
    us_2y = round(us_10y - 0.25, 4)
    jp_2y = round(jp_10y - 0.40, 4)
    spread_2y = round(us_2y - jp_2y, 4)
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO yield_spreads (timestamp_utc, us_2y, jp_2y, spread_2y, us_10y, jp_10y, spread_10y, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-11-20T09:00:00Z", us_2y, jp_2y, spread_2y, us_10y, jp_10y, spread_10y, "fixture"),
        )
        ticks = [
            ("JPY=X", "2026-11-20T09:00:00Z", spot, spot - 0.1, spot + 0.1, spot - 0.15, 0.0),
            ("^TNX", "2026-11-20T09:00:00Z", us_10y, us_10y, us_10y + 0.02, us_10y - 0.02, 0.0),
            ("^VIX", "2026-11-20T09:00:00Z", vix, vix, vix + 0.2, vix - 0.2, 0.0),
            ("^GSPC", "2026-11-20T09:00:00Z", 7400.0, 7380.0, 7410.0, 7375.0, 1000000.0),
            ("^N225", "2026-11-20T09:00:00Z", 59500.0, 59400.0, 59600.0, 59350.0, 0.0),
            ("GC=F", "2026-11-20T09:00:00Z", 3750.0, 3745.0, 3755.0, 3740.0, 500.0),
            ("CL=F", "2026-11-20T09:00:00Z", 72.5, 72.0, 73.0, 71.8, 1200.0),
        ]
        conn.executemany(
            """
            INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ticks,
        )
        conn.commit()
    finally:
        conn.close()
    return db_file


class FakeTelegramBotClient(TelegramBotClient):
    """In-memory mock replacement for TelegramBotClient recording all API calls."""

    def __init__(self, bot_token: str = "test-token", chat_id: str = "123456789") -> None:
        super().__init__(bot_token=bot_token, chat_id=chat_id)
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.answered_callbacks: list[dict[str, Any]] = []
        self.queued_updates: list[list[dict[str, Any]]] = []
        self._next_msg_id = 100

    def send_message(
        self,
        text: str,
        *,
        chat_id: str | int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        self._next_msg_id += 1
        record = {
            "message_id": self._next_msg_id,
            "chat_id": str(chat_id if chat_id is not None else self.chat_id),
            "text": text,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
        }
        self.sent_messages.append(record)
        return record

    def edit_message_text(
        self,
        message_id: int,
        text: str,
        *,
        chat_id: str | int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "message_id": int(message_id),
            "chat_id": str(chat_id if chat_id is not None else self.chat_id),
            "text": text,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
        }
        self.edited_messages.append(record)
        return record

    def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        self.answered_callbacks.append(
            {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            }
        )
        return True

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 10,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.queued_updates:
            batch = self.queued_updates.pop(0)
            if offset is not None:
                return [u for u in batch if int(u.get("update_id", 0)) >= offset]
            return batch
        return []


class TestTelegramSettingsAndTimeParser(unittest.TestCase):
    """Tests for 24-hour JST time parsing and persistent settings."""

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="fx_tg_settings_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_parse_jst_time_normalizes_valid_24h_times(self) -> None:
        self.assertEqual(parse_jst_time("9:00"), "09:00")
        self.assertEqual(parse_jst_time("09:00"), "09:00")
        self.assertEqual(parse_jst_time("7:30"), "07:30")
        self.assertEqual(parse_jst_time("07:30"), "07:30")
        self.assertEqual(parse_jst_time("14:30"), "14:30")
        self.assertEqual(parse_jst_time("0:05"), "00:05")
        self.assertEqual(parse_jst_time("23:59"), "23:59")

    def test_parse_jst_time_rejects_invalid_times(self) -> None:
        for invalid in ["", "24:00", "25:99", "12:60", "7:3", "9am", "1430", "-1:00"]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_jst_time(invalid)

    def test_telegram_settings_defaults_and_persistence(self) -> None:
        settings_file = self._tmpdir / "telegram_settings.json"
        loaded_default = TelegramSettings.load(settings_file)
        self.assertTrue(loaded_default.daily_summary_enabled)
        self.assertEqual(loaded_default.daily_summary_time_jst, DEFAULT_SUMMARY_TIME_JST)
        self.assertEqual(loaded_default.daily_summary_time_jst, "09:00")

        loaded_default.daily_summary_enabled = False
        loaded_default.daily_summary_time_jst = parse_jst_time("14:30")
        loaded_default.last_update_id = 42
        loaded_default.last_summary_sent_jst_date = "2026-11-21"
        loaded_default.save(settings_file)

        reloaded = TelegramSettings.load(settings_file)
        self.assertFalse(reloaded.daily_summary_enabled)
        self.assertEqual(reloaded.daily_summary_time_jst, "14:30")
        self.assertEqual(reloaded.last_update_id, 42)
        self.assertEqual(reloaded.last_summary_sent_jst_date, "2026-11-21")

    def test_telegram_bot_client_http_requests_and_error_handling(self) -> None:
        class DummyResponse:
            def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
                self.status_code = status_code
                self._payload = payload

            def json(self) -> dict[str, Any]:
                return self._payload

        class DummySession:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []
                self.next_response = DummyResponse(200, {"ok": True, "result": {"message_id": 77}})

            def post(self, url: str, json: dict[str, Any], timeout: float) -> DummyResponse:
                self.calls.append((url, json))
                return self.next_response

        sess = DummySession()
        client = TelegramBotClient(bot_token="tok123", chat_id="555", session=sess)  # type: ignore[arg-type]
        self.assertTrue(client.is_authorized_chat(555))
        self.assertFalse(client.is_authorized_chat(999))

        res = client.send_message("hello", reply_markup=build_approval_inline_keyboard("P1"))
        self.assertEqual(res["message_id"], 77)
        self.assertTrue(sess.calls[-1][0].endswith("/bottok123/sendMessage"))

        sess.next_response = DummyResponse(400, {"ok": False, "description": "Bad Request"})
        with self.assertRaises(TelegramAPIError):
            client.send_message("fail")


class TestTelegramFormatters(unittest.TestCase):
    """Tests for trade proposal, execution receipt, and daily summary formatters."""

    def test_format_daily_summary_includes_summary_settings_block(self) -> None:
        state = {
            "pair": "USD/JPY",
            "spot_price": 154.20,
            "yield_bias": "BULLISH_USD",
            "risk_regime": "RISK_ON",
            "macro_assessment": {
                "us_10y": 4.45,
                "jp_10y": 1.05,
                "spread_10y": 3.40,
                "spread_bps": 340.0,
                "us_2y": 4.30,
                "jp_2y": 0.55,
                "spread_2y": 3.75,
                "crude_oil_price": 72.50,
                "crude_oil_5d_change_pct": 3.2,
                "terms_of_trade_bias": "BULLISH_USD",
                "confidence": 0.85,
            },
            "trade_proposal": {
                "proposal_id": "PROP-123",
                "action": "BUY",
                "pair": "USD/JPY",
                "position_size_units": 10000,
                "entry_price": 154.20,
                "stop_loss": 153.45,
                "take_profit": 155.70,
            },
        }
        settings_on = TelegramSettings(daily_summary_enabled=True, daily_summary_time_jst="07:30")
        summary_on = format_daily_summary(state, settings=settings_on)
        self.assertIn("Summary Settings", summary_on)
        self.assertIn("ON (Daily at 07:30 JST)", summary_on)
        self.assertIn("/summary on [HH:MM]", summary_on)
        self.assertIn("/summary off", summary_on)
        self.assertIn("/summary now", summary_on)

        settings_off = TelegramSettings(daily_summary_enabled=False, daily_summary_time_jst="14:30")
        summary_off = format_daily_summary(state, settings=settings_off)
        self.assertIn("OFF (Configured time: 14:30 JST)", summary_off)

    def test_proposal_and_execution_formatters(self) -> None:
        kb = build_approval_inline_keyboard("PROP-ABC")
        buttons = kb["inline_keyboard"][0]
        self.assertEqual(buttons[0]["callback_data"], "approve:PROP-ABC")
        self.assertEqual(buttons[1]["callback_data"], "reject:PROP-ABC")

        state = {
            "pair": "USD/JPY",
            "spot_price": 152.00,
            "trade_proposal": {
                "proposal_id": "PROP-ABC",
                "pair": "USD/JPY",
                "action": "BUY",
                "position_size_units": 12000,
                "entry_price": 152.00,
                "stop_loss": 151.25,
                "take_profit": 153.50,
                "risk_reward_ratio": 2.0,
                "rationale": "Widening 10Y and 2Y yield spreads.",
            },
        }
        msg = format_proposal_message(state, thread_id="run-01")
        self.assertIn("BUY", msg)
        self.assertIn("12000 units", msg)
        self.assertIn("PROP-ABC", msg)

        exec_msg = format_execution_update(
            msg,
            {
                "status": "EXECUTED",
                "order_id": "PAPER-999",
                "action": "BUY",
                "position_size_units": 12000,
                "pair": "USD/JPY",
                "fill_price": 152.00,
            },
        )
        self.assertIn("✅ EXECUTED", exec_msg)
        self.assertIn("PAPER-999", exec_msg)

        timeout_msg = format_execution_update(msg, {"status": "CANCELLED_REJECTED"}, timed_out=True)
        self.assertIn("⏱️ EXPIRED (Auto-Rejected)", timeout_msg)


class TestTelegramApprovalAndSummaryWorkflow(unittest.TestCase):
    """End-to-end tests for Telegram inline button approvals, timeouts, and /summary commands."""

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="fx_tg_workflow_"))
        self.ledger_path = self._tmpdir / "paper_trades.jsonl"
        self.settings_path = self._tmpdir / "telegram_settings.json"
        self.buy_db = _build_fixture_db(
            self._tmpdir / "buy.db",
            us_10y=5.20,
            jp_10y=2.80,
            spot=152.00,
            vix=14.5,
        )
        self.hold_db = _build_fixture_db(
            self._tmpdir / "hold.db",
            us_10y=4.10,
            jp_10y=2.60,
            spot=150.00,
            vix=16.0,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_notifier(
        self,
        *,
        timeout_sec: int = 10,
        time_fn: Any = None,
    ) -> tuple[TelegramNotifier, FakeTelegramBotClient]:
        cfg = TelegramConfig(
            bot_token="123:test-bot-token",
            chat_id="987654321",
            timeout_sec=timeout_sec,
            summary_time_jst="09:00",
            settings_path=self.settings_path,
        )
        client = FakeTelegramBotClient(bot_token=cfg.bot_token, chat_id=cfg.chat_id)
        kwargs: dict[str, Any] = {
            "config": cfg,
            "client": client,
            "sleep_fn": lambda _: None,
        }
        if time_fn is not None:
            kwargs["time_fn"] = time_fn
        notifier = TelegramNotifier(**kwargs)
        return notifier, client

    def test_inline_button_approve_executes_paper_trade_and_updates_message(self) -> None:
        notifier, client = self._make_notifier()
        client.queued_updates.append(
            [
                {
                    "update_id": 501,
                    "callback_query": {
                        "id": "cb-approve-1",
                        "data": "approve",
                        "message": {"chat": {"id": 987654321}},
                    },
                }
            ]
        )

        result = pipeline_cli.run_signal_pipeline(
            pair="USD/JPY",
            db_path=str(self.buy_db),
            ledger_path=self.ledger_path,
            telegram=True,
            telegram_notifier=notifier,
        )

        self.assertFalse(result["paused_for_approval"])
        state = result["state"]
        self.assertEqual(state["approval_status"], "APPROVED")
        receipt = state["execution_receipt"]
        self.assertEqual(receipt["status"], "EXECUTED")
        self.assertTrue(self.ledger_path.exists())
        ledger_lines = self.ledger_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(ledger_lines), 1)

        # Verify Telegram messages and button removal
        self.assertEqual(len(client.sent_messages), 1)
        self.assertIsNotNone(client.sent_messages[0]["reply_markup"])
        self.assertEqual(len(client.answered_callbacks), 1)
        self.assertEqual(len(client.edited_messages), 1)
        self.assertEqual(client.edited_messages[0]["reply_markup"], {"inline_keyboard": []})
        self.assertIn("✅ EXECUTED", client.edited_messages[0]["text"])

    def test_inline_button_reject_cancels_paper_trade_without_ledger_write(self) -> None:
        notifier, client = self._make_notifier()
        client.queued_updates.append(
            [
                {
                    "update_id": 502,
                    "callback_query": {
                        "id": "cb-reject-1",
                        "data": "reject",
                        "message": {"chat": {"id": 987654321}},
                    },
                }
            ]
        )

        result = pipeline_cli.run_signal_pipeline(
            pair="USD/JPY",
            db_path=str(self.buy_db),
            ledger_path=self.ledger_path,
            telegram=True,
            telegram_notifier=notifier,
        )

        self.assertFalse(result["paused_for_approval"])
        state = result["state"]
        self.assertEqual(state["approval_status"], "REJECTED")
        receipt = state["execution_receipt"]
        self.assertEqual(receipt["status"], "CANCELLED_REJECTED")
        self.assertFalse(self.ledger_path.exists())
        self.assertEqual(len(client.edited_messages), 1)
        self.assertIn("❌ REJECTED", client.edited_messages[0]["text"])

    def test_timeout_auto_rejects_and_marks_message_expired(self) -> None:
        clock = {"t": 0.0}

        def fake_time() -> float:
            clock["t"] += 2.0
            return clock["t"]

        notifier, client = self._make_notifier(timeout_sec=3, time_fn=fake_time)

        result = pipeline_cli.run_signal_pipeline(
            pair="USD/JPY",
            db_path=str(self.buy_db),
            ledger_path=self.ledger_path,
            telegram=True,
            telegram_timeout=3,
            telegram_notifier=notifier,
        )

        self.assertFalse(result["paused_for_approval"])
        state = result["state"]
        self.assertEqual(state["approval_status"], "REJECTED")
        self.assertEqual(state["execution_receipt"]["status"], "CANCELLED_REJECTED")
        self.assertFalse(self.ledger_path.exists())
        self.assertEqual(len(client.edited_messages), 1)
        self.assertIn("⏱️ EXPIRED (Auto-Rejected)", client.edited_messages[0]["text"])

    def test_unauthorized_chat_id_is_ignored(self) -> None:
        notifier, client = self._make_notifier()
        # First update from attacker chat_id=111111 (tries to approve), second from authorized 987654321 (rejects)
        client.queued_updates.append(
            [
                {
                    "update_id": 600,
                    "callback_query": {
                        "id": "cb-attacker",
                        "data": "approve",
                        "message": {"chat": {"id": 111111}},
                    },
                },
                {
                    "update_id": 601,
                    "message": {
                        "message_id": 200,
                        "chat": {"id": 111111},
                        "text": "/summary off",
                    },
                },
                {
                    "update_id": 602,
                    "callback_query": {
                        "id": "cb-owner",
                        "data": "reject",
                        "message": {"chat": {"id": 987654321}},
                    },
                },
            ]
        )

        result = pipeline_cli.run_signal_pipeline(
            pair="USD/JPY",
            db_path=str(self.buy_db),
            ledger_path=self.ledger_path,
            telegram=True,
            telegram_notifier=notifier,
        )

        self.assertEqual(result["state"]["approval_status"], "REJECTED")
        # Attacker's /summary off must have been ignored
        self.assertTrue(notifier.settings.daily_summary_enabled)
        self.assertEqual(len(client.answered_callbacks), 1)
        self.assertEqual(client.answered_callbacks[0]["callback_query_id"], "cb-owner")

    def test_non_actionable_hold_does_not_send_telegram_approval_prompt(self) -> None:
        notifier, client = self._make_notifier()
        result = pipeline_cli.run_signal_pipeline(
            pair="USD/JPY",
            db_path=str(self.hold_db),
            ledger_path=self.ledger_path,
            telegram=True,
            telegram_notifier=notifier,
        )
        self.assertFalse(result["paused_for_approval"])
        self.assertEqual(result["state"]["execution_receipt"]["status"], "SKIPPED_HOLD")
        self.assertEqual(len(client.sent_messages), 0)

    def test_summary_commands_on_default_custom_jst_off_and_now(self) -> None:
        notifier, client = self._make_notifier()
        summary_state = {
            "pair": "USD/JPY",
            "spot_price": 152.50,
            "yield_bias": "BULLISH_USD",
            "risk_regime": "RISK_ON",
        }
        provider = lambda s: format_daily_summary(
            summary_state,
            ledger_path=self.ledger_path,
            settings=s,
        )

        # 1. /summary off -> disables daily summary
        client.queued_updates.append(
            [
                {
                    "update_id": 701,
                    "message": {
                        "message_id": 10,
                        "chat": {"id": 987654321},
                        "text": "/summary off",
                    },
                }
            ]
        )
        handled = notifier.poll_commands(summary_provider=provider)
        self.assertEqual(handled[0]["command"], "summary_off")
        self.assertFalse(notifier.settings.daily_summary_enabled)
        self.assertFalse(TelegramSettings.load(self.settings_path).daily_summary_enabled)
        self.assertIn("Daily summary disabled", client.sent_messages[-1]["text"])

        # 2. /summary now -> works even while daily summary is OFF and includes summary settings
        client.queued_updates.append(
            [
                {
                    "update_id": 702,
                    "message": {
                        "message_id": 11,
                        "chat": {"id": 987654321},
                        "text": "/summary now",
                    },
                }
            ]
        )
        handled = notifier.poll_commands(summary_provider=provider)
        self.assertEqual(handled[0]["command"], "summary_now")
        now_msg = client.sent_messages[-1]["text"]
        self.assertIn("USD/JPY Market & Portfolio Summary", now_msg)
        self.assertIn("Summary Settings", now_msg)
        self.assertIn("OFF (Configured time: 09:00 JST)", now_msg)

        # 3. /summary on 7:30 -> enables and sets schedule to 07:30 JST
        client.queued_updates.append(
            [
                {
                    "update_id": 703,
                    "message": {
                        "message_id": 12,
                        "chat": {"id": 987654321},
                        "text": "/summary on 7:30",
                    },
                }
            ]
        )
        handled = notifier.poll_commands(summary_provider=provider)
        self.assertEqual(handled[0]["command"], "summary_on")
        self.assertTrue(notifier.settings.daily_summary_enabled)
        self.assertEqual(notifier.settings.daily_summary_time_jst, "07:30")
        self.assertEqual(TelegramSettings.load(self.settings_path).daily_summary_time_jst, "07:30")
        self.assertIn("07:30 JST", client.sent_messages[-1]["text"])

        # 4. /summary on 14:30 -> enables and sets schedule to 14:30 JST
        client.queued_updates.append(
            [
                {
                    "update_id": 704,
                    "message": {
                        "message_id": 13,
                        "chat": {"id": 987654321},
                        "text": "/summary on 14:30",
                    },
                }
            ]
        )
        handled = notifier.poll_commands(summary_provider=provider)
        self.assertEqual(handled[0]["command"], "summary_on")
        self.assertTrue(notifier.settings.daily_summary_enabled)
        self.assertEqual(notifier.settings.daily_summary_time_jst, "14:30")
        self.assertIn("14:30 JST", client.sent_messages[-1]["text"])

        # 5. /summary on (no time arg) -> defaults back to 09:00 JST (9:00 AM JST)
        client.queued_updates.append(
            [
                {
                    "update_id": 705,
                    "message": {
                        "message_id": 14,
                        "chat": {"id": 987654321},
                        "text": "/summary on",
                    },
                }
            ]
        )
        handled = notifier.poll_commands(summary_provider=provider)
        self.assertEqual(handled[0]["command"], "summary_on")
        self.assertTrue(notifier.settings.daily_summary_enabled)
        self.assertEqual(notifier.settings.daily_summary_time_jst, "09:00")
        self.assertIn("09:00 JST", client.sent_messages[-1]["text"])

        # 6. /summary on 25:99 -> returns validation notice without corrupting 09:00 JST setting
        client.queued_updates.append(
            [
                {
                    "update_id": 706,
                    "message": {
                        "message_id": 15,
                        "chat": {"id": 987654321},
                        "text": "/summary on 25:99",
                    },
                }
            ]
        )
        handled = notifier.poll_commands(summary_provider=provider)
        self.assertEqual(handled[0]["command"], "summary_invalid_time")
        self.assertEqual(notifier.settings.daily_summary_time_jst, "09:00")
        self.assertIn("Invalid 24-hour time", client.sent_messages[-1]["text"])

    def test_scheduler_daily_summary_dispatch_and_dynamic_rescheduling(self) -> None:
        notifier, client = self._make_notifier()
        cfg = schedule_cli.SchedulerConfig(
            pair="USD/JPY",
            db_path=self.buy_db,
            ledger_path=self.ledger_path,
            telegram=True,
            dry_run=True,
        )
        scheduler = schedule_cli.SignalScheduler(cfg, telegram_notifier=notifier)

        # Before 09:00 JST (e.g. 2026-11-21 23:30 UTC == 2026-11-22 08:30 JST): should NOT send yet
        before_9am_jst = datetime(2026, 11, 21, 23, 30, 0, tzinfo=timezone.utc)
        res_before = scheduler.poll_telegram_commands_and_summary(before_9am_jst)
        self.assertFalse(res_before["daily_summary_sent"])
        self.assertEqual(len(client.sent_messages), 0)

        # At 09:00 JST (2026-11-22 00:00 UTC == 2026-11-22 09:00 JST): sends daily summary once
        at_9am_jst = datetime(2026, 11, 22, 0, 0, 0, tzinfo=timezone.utc)
        res_at = scheduler.poll_telegram_commands_and_summary(at_9am_jst)
        self.assertTrue(res_at["daily_summary_sent"])
        self.assertEqual(len(client.sent_messages), 1)
        self.assertIn("ON (Daily at 09:00 JST)", client.sent_messages[-1]["text"])
        self.assertEqual(notifier.settings.last_summary_sent_jst_date, "2026-11-22")

        # Polling again on the same JST day does NOT send a duplicate summary
        res_dup = scheduler.poll_telegram_commands_and_summary(at_9am_jst)
        self.assertFalse(res_dup["daily_summary_sent"])
        self.assertEqual(len(client.sent_messages), 1)

    def test_cli_telegram_test_and_summary_flags(self) -> None:
        notifier, client = self._make_notifier()
        with patch.object(pipeline_cli, "TelegramNotifier", return_value=notifier):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = pipeline_cli.main(["--telegram-test", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["chat_id"], "987654321")

            buf_sum = io.StringIO()
            with patch("sys.stdout", buf_sum):
                rc_sum = pipeline_cli.main(
                    [
                        "--db-path",
                        str(self.buy_db),
                        "--ledger-path",
                        str(self.ledger_path),
                        "--telegram-summary",
                        "--json",
                    ]
                )
            self.assertEqual(rc_sum, 0)
            sum_payload = json.loads(buf_sum.getvalue())
            self.assertEqual(sum_payload["status"], "SUMMARY_SENT")
            self.assertIn("Summary Settings", client.sent_messages[-1]["text"])


if __name__ == "__main__":
    unittest.main()
