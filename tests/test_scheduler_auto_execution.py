"""Tests for P11-02: Automated Scheduled Execution, Regime Overrides & Telegram Action Alerts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fx_signal_reader.agent.state import RiskRegime
from fx_signal_reader.cli import pipeline as pipeline_cli
from fx_signal_reader.cli import schedule as schedule_cli
from fx_signal_reader.notifications.formatters import format_execution_alert
from fx_signal_reader.notifications.settings import TelegramConfig, TelegramSettings
from fx_signal_reader.notifications.telegram import TelegramNotifier


class _StubTelegramClient:
    """In-memory stub for TelegramBotClient recording sent messages."""

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send_message(self, text: str, reply_markup: dict | None = None) -> dict:
        self.sent_messages.append({"text": text, "reply_markup": reply_markup})
        return {"message_id": len(self.sent_messages)}


class TestExecutionAlertFormattingAndNotifier(unittest.TestCase):
    def test_format_execution_alert_includes_trade_and_regime_fields(self) -> None:
        state = {
            "pair": "USD/JPY",
            "risk_regime": "TRANSITIONAL",
            "trade_proposal": {
                "action": "BUY",
                "risk_reward_ratio": 2.0,
                "rationale": "Strong 2Y/10Y spread expansion with Pre-Fix Gotobi demand.",
            },
        }
        receipt = {
            "order_id": "PAPER-ABC123DEF456",
            "status": "EXECUTED",
            "pair": "USD/JPY",
            "action": "BUY",
            "position_size_units": 33333,
            "fill_price": 155.50,
            "stop_loss": 154.30,
            "take_profit": 157.90,
            "executed_at_utc": "2026-11-23T01:00:00Z",
        }
        text = format_execution_alert(state, receipt)
        self.assertIn("Automated Paper Trade Executed (USD/JPY)", text)
        self.assertIn("BUY 33333 USD/JPY", text)
        self.assertIn("155.5", text)
        self.assertIn("154.3", text)
        self.assertIn("157.9", text)
        self.assertIn("TRANSITIONAL", text)
        self.assertIn("PAPER-ABC123DEF456", text)
        self.assertIn("Pre-Fix Gotobi demand", text)

    def test_notifier_send_execution_alert_dispatches_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "tg_settings.json"
            cfg = TelegramConfig(
                bot_token="test-token",
                chat_id="123456",
                settings_path=settings_path,
            )
            stub_client = _StubTelegramClient()
            notifier = TelegramNotifier(cfg, client=stub_client, settings=TelegramSettings())
            res = notifier.send_execution_alert(
                {"pair": "USD/JPY", "risk_regime": "RISK_ON"},
                {
                    "order_id": "PAPER-999",
                    "status": "EXECUTED",
                    "pair": "USD/JPY",
                    "action": "SELL",
                    "position_size_units": 12500,
                    "fill_price": 154.0,
                    "stop_loss": 154.8,
                    "take_profit": 152.4,
                },
            )
            self.assertEqual(res["message_id"], 1)
            self.assertEqual(len(stub_client.sent_messages), 1)
            self.assertIn("SELL 12500 USD/JPY", str(stub_client.sent_messages[0]["text"]))
            self.assertIsNone(stub_client.sent_messages[0]["reply_markup"])


class TestPipelineAutoApproveAlert(unittest.TestCase):
    def test_run_signal_pipeline_sends_alert_on_auto_approve_with_telegram(self) -> None:
        fake_graph = MagicMock()
        fake_snapshot = MagicMock()
        fake_snapshot.next = ("paper_execution",)
        fake_graph.get_state.side_effect = [
            fake_snapshot,
            MagicMock(next=()),
        ]
        fake_graph.invoke.return_value = {
            "pair": "USD/JPY",
            "risk_regime": "TRANSITIONAL",
            "trade_proposal": {"action": "BUY", "position_size_units": 20000},
        }
        executed_state = {
            "pair": "USD/JPY",
            "risk_regime": "TRANSITIONAL",
            "trade_proposal": {"action": "BUY", "position_size_units": 20000},
            "execution_receipt": {
                "order_id": "PAPER-AUTO01",
                "status": "EXECUTED",
                "pair": "USD/JPY",
                "action": "BUY",
                "position_size_units": 20000,
                "fill_price": 155.0,
                "stop_loss": 153.8,
                "take_profit": 157.4,
            },
        }
        mock_notifier = MagicMock()

        with (
            patch.object(pipeline_cli, "build_signal_graph", return_value=fake_graph),
            patch.object(pipeline_cli, "resume_with_decision", return_value=executed_state),
        ):
            result = pipeline_cli.run_signal_pipeline(
                pair="USD/JPY",
                auto_approve=True,
                telegram=True,
                telegram_notifier=mock_notifier,
                regime_overrides={"RISK_ON": {"risk_pct": 1.0, "sl_pips": 80, "tp_pips": 160}},
            )

        self.assertFalse(result["paused_for_approval"])
        mock_notifier.send_execution_alert.assert_called_once_with(
            executed_state, executed_state["execution_receipt"]
        )
        mock_notifier.send_approval_request.assert_not_called()

    def test_run_signal_pipeline_skips_alert_when_telegram_disabled(self) -> None:
        fake_graph = MagicMock()
        fake_snapshot = MagicMock()
        fake_snapshot.next = ("paper_execution",)
        fake_graph.get_state.side_effect = [fake_snapshot, MagicMock(next=())]
        fake_graph.invoke.return_value = {"pair": "USD/JPY"}
        executed_state = {
            "execution_receipt": {"order_id": "PAPER-AUTO02", "status": "EXECUTED"}
        }

        with (
            patch.object(pipeline_cli, "build_signal_graph", return_value=fake_graph),
            patch.object(pipeline_cli, "resume_with_decision", return_value=executed_state),
            patch.object(pipeline_cli, "TelegramNotifier") as mock_notifier_cls,
        ):
            pipeline_cli.run_signal_pipeline(
                pair="USD/JPY",
                auto_approve=True,
                telegram=False,
                telegram_notifier=None,
            )

        mock_notifier_cls.assert_not_called()


class TestSchedulerAutoExecutionAndConfig(unittest.TestCase):
    def test_cli_flags_and_yaml_config_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            yaml_path = tmp / "sched.yaml"
            yaml_path.write_text(
                "\n".join(
                    [
                        "pair: USD/JPY",
                        "run_pipeline: true",
                        "auto_approve: true",
                        "telegram: true",
                        "interval_ticks: 600",
                        "regime_overrides:",
                        "  RISK_ON:",
                        "    risk_pct: 1.0",
                        "    sl_pips: 80",
                        "    tp_pips: 160",
                        "  TRANSITIONAL:",
                        "    risk_pct: 4.0",
                        "    sl_pips: 120",
                        "    tp_pips: 240",
                    ]
                ),
                encoding="utf-8",
            )

            pre_parser = schedule_cli.build_arg_parser()
            defaults = schedule_cli.load_scheduler_config_file(yaml_path)
            pre_parser.set_defaults(**defaults)
            # CLI --interval-ticks 300 overrides YAML 600
            args = pre_parser.parse_args(["--config", str(yaml_path), "--interval-ticks", "300"])
            cfg = schedule_cli.SchedulerConfig.from_env_and_args(args)

            self.assertEqual(cfg.pair, "USD/JPY")
            self.assertTrue(cfg.run_pipeline)
            self.assertTrue(cfg.auto_approve)
            self.assertTrue(cfg.telegram)
            self.assertEqual(cfg.ticks_interval, 300)
            assert cfg.regime_overrides is not None
            self.assertAlmostEqual(
                cfg.regime_overrides[RiskRegime.RISK_ON.value]["risk_fraction"], 0.01
            )
            self.assertEqual(cfg.regime_overrides[RiskRegime.RISK_ON.value]["sl_pips"], 80.0)
            self.assertAlmostEqual(
                cfg.regime_overrides[RiskRegime.TRANSITIONAL.value]["risk_fraction"], 0.04
            )
            self.assertEqual(cfg.regime_overrides[RiskRegime.TRANSITIONAL.value]["tp_pips"], 240.0)

    def test_scheduler_forwards_auto_approve_and_regime_overrides(self) -> None:
        overrides_json = json.dumps(
            {
                "RISK_ON": {"risk_pct": 1.0, "sl_pips": 80, "tp_pips": 160},
                "TRANSITIONAL": {"risk_pct": 4.0, "sl_pips": 120, "tp_pips": 240},
            }
        )
        parser = schedule_cli.build_arg_parser()
        args = parser.parse_args(
            [
                "--pair",
                "USD/JPY",
                "--run-pipeline",
                "--auto-approve",
                "--telegram",
                "--regime-overrides",
                overrides_json,
            ]
        )
        cfg = schedule_cli.SchedulerConfig.from_env_and_args(args)
        mock_notifier = MagicMock()
        scheduler = schedule_cli.SignalScheduler(cfg, telegram_notifier=mock_notifier)

        with patch.object(schedule_cli, "run_signal_pipeline", return_value={"ok": True}) as mock_run:
            res = scheduler.run_pipeline_evaluation()

        self.assertEqual(res, {"ok": True})
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs["pair"], "USD/JPY")
        self.assertTrue(kwargs["auto_approve"])
        self.assertTrue(kwargs["telegram"])
        self.assertIs(kwargs["telegram_notifier"], mock_notifier)
        self.assertEqual(kwargs["regime_overrides"]["RISK_ON"]["sl_pips"], 80.0)
        self.assertEqual(kwargs["regime_overrides"]["TRANSITIONAL"]["risk_fraction"], 0.04)


if __name__ == "__main__":
    unittest.main()
