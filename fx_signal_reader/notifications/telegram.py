"""
Telegram Remote Approval Gate & Interactive /summary Command Controller.
Task P10-03: Interactive Approval Loop & Summary Command Controller

Orchestrates:
  - 2-way inline-button trade approval (send_approval_request, wait_for_decision, update_decision_message)
  - Interactive /summary command controller (/summary on [HH:MM], /summary off, /summary now)
  - Scheduled & on-demand daily summary delivery
  - Connectivity verification & chat_id auto-discovery (send_test_message)
"""

from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Any, Callable

from fx_signal_reader.notifications.client import TelegramAPIError, TelegramBotClient
from fx_signal_reader.notifications.formatters import (
    build_approval_inline_keyboard,
    format_daily_summary,
    format_execution_update,
    format_proposal_message,
    format_summary_settings_block,
)
from fx_signal_reader.notifications.settings import (
    DEFAULT_SUMMARY_TIME_JST,
    TelegramConfig,
    TelegramSettings,
    parse_jst_time,
)

logger = logging.getLogger("TelegramNotifier")


class TelegramNotifier:
    """High-level coordinator for Telegram trade approvals and /summary commands."""

    def __init__(
        self,
        config: TelegramConfig | None = None,
        *,
        client: TelegramBotClient | None = None,
        settings: TelegramSettings | None = None,
        summary_provider: Callable[[TelegramSettings], str] | None = None,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config if config is not None else TelegramConfig.from_env()
        self.client = (
            client
            if client is not None
            else TelegramBotClient(
                bot_token=self.config.bot_token,
                chat_id=self.config.chat_id,
            )
        )
        self.settings = (
            settings
            if settings is not None
            else TelegramSettings.load(
                path=self.config.settings_path,
                default_time_jst=self.config.summary_time_jst,
            )
        )
        self.summary_provider = summary_provider
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn

    def save_settings(self) -> Path:
        """Persist current TelegramSettings to config.settings_path."""
        return self.settings.save(self.config.settings_path)

    def send_approval_request(
        self,
        state: dict[str, Any],
        thread_id: str | None = None,
    ) -> tuple[int, str, str]:
        """
        Format and send an actionable BUY/SELL trade proposal with Approve/Reject inline buttons.

        Returns:
            Tuple of (message_id, proposal_id, formatted_message_text).
        """
        proposal = state.get("trade_proposal") or {}
        if hasattr(proposal, "model_dump"):
            proposal = proposal.model_dump(mode="json")
        proposal_id = (
            str(proposal.get("proposal_id") or state.get("run_id") or "PROP").strip()
            or "PROP"
        )
        message_text = format_proposal_message(state, thread_id=thread_id)
        keyboard = build_approval_inline_keyboard(proposal_id)
        response = self.client.send_message(message_text, reply_markup=keyboard)
        message_id = int(response.get("message_id") or 0)
        return message_id, proposal_id, message_text

    def update_decision_message(
        self,
        message_id: int,
        original_text: str,
        receipt: dict[str, Any] | None = None,
        *,
        timed_out: bool = False,
    ) -> dict[str, Any]:
        """
        Edit the original Telegram proposal message to remove inline buttons and append
        the final ExecutionReceipt status (or timeout expiration).
        """
        updated_text = format_execution_update(
            original_text,
            receipt,
            timed_out=timed_out,
        )
        return self.client.edit_message_text(
            message_id=message_id,
            text=updated_text,
            reply_markup={"inline_keyboard": []},
        )

    def handle_summary_command(
        self,
        text: str,
        *,
        summary_provider: Callable[[TelegramSettings], str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Parse and execute a `/summary` command from an authorized user.

        Supported commands:
          - `/summary on` -> enables daily summary at default 09:00 JST (9:00 AM JST)
          - `/summary on <H:MM|HH:MM>` -> enables daily summary at specified 24-hour JST time
          - `/summary off` -> disables daily scheduled summary
          - `/summary now` (or `/summary`) -> immediately sends an on-demand market & portfolio summary

        Returns:
            Command metadata dict if text was a `/summary` command, or None otherwise.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        parts = cleaned.split()
        cmd_token = parts[0].lower()
        # Strip optional @bot_username suffix (e.g. /summary@my_fx_bot)
        if "@" in cmd_token:
            cmd_token = cmd_token.split("@", 1)[0]

        if cmd_token != "/summary":
            return None

        args = parts[1:]
        active_provider = summary_provider or self.summary_provider

        # `/summary` or `/summary now`
        if not args or (len(args) == 1 and args[0].lower() == "now"):
            if active_provider is not None:
                summary_text = active_provider(self.settings)
            else:
                summary_text = (
                    "📋 FX Signal Reader Summary Status\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    + format_summary_settings_block(self.settings)
                )
            self.client.send_message(summary_text)
            return {
                "command": "summary_now",
                "enabled": self.settings.daily_summary_enabled,
                "time_jst": self.settings.daily_summary_time_jst,
            }

        subcmd = args[0].lower()

        if subcmd == "off" and len(args) == 1:
            self.settings.daily_summary_enabled = False
            self.save_settings()
            reply = (
                "🔕 Daily summary disabled.\n"
                "Send '/summary on [HH:MM]' to re-enable or '/summary now' for an on-demand summary.\n\n"
                + format_summary_settings_block(self.settings)
            )
            self.client.send_message(reply)
            return {
                "command": "summary_off",
                "enabled": False,
                "time_jst": self.settings.daily_summary_time_jst,
            }

        if subcmd == "on":
            if len(args) == 1:
                target_time = DEFAULT_SUMMARY_TIME_JST
            elif len(args) == 2:
                raw_time_arg = args[1].strip()
                try:
                    target_time = parse_jst_time(raw_time_arg)
                except ValueError as exc:
                    err_reply = (
                        f"⚠️ Invalid 24-hour time '{raw_time_arg}'.\n"
                        "Please provide a valid 24-hour JST time in H:MM or HH:MM format "
                        "(e.g., '/summary on 7:30' or '/summary on 14:30')."
                    )
                    self.client.send_message(err_reply)
                    return {
                        "command": "summary_invalid_time",
                        "error": str(exc),
                        "enabled": self.settings.daily_summary_enabled,
                        "time_jst": self.settings.daily_summary_time_jst,
                    }
            else:
                usage_reply = (
                    "⚠️ Too many arguments for '/summary on'.\n"
                    "Usage: '/summary on' (defaults to 09:00 JST) or '/summary on HH:MM' (e.g. '/summary on 7:30')."
                )
                self.client.send_message(usage_reply)
                return {
                    "command": "summary_invalid_time",
                    "error": "Too many arguments",
                    "enabled": self.settings.daily_summary_enabled,
                    "time_jst": self.settings.daily_summary_time_jst,
                }

            self.settings.daily_summary_enabled = True
            self.settings.daily_summary_time_jst = target_time
            self.save_settings()
            reply = (
                f"✅ Daily summary enabled and scheduled for {target_time} JST.\n\n"
                + format_summary_settings_block(self.settings)
            )
            self.client.send_message(reply)
            return {
                "command": "summary_on",
                "enabled": True,
                "time_jst": target_time,
            }

        help_reply = (
            "ℹ️ Unknown /summary command.\n"
            "Available commands:\n"
            "• /summary on — Enable daily summary at 09:00 JST (9:00 AM JST)\n"
            "• /summary on HH:MM — Enable daily summary at custom 24h JST time (e.g. /summary on 7:30 or /summary on 14:30)\n"
            "• /summary off — Disable daily scheduled summary\n"
            "• /summary now — Request an immediate summary at any time"
        )
        self.client.send_message(help_reply)
        return {
            "command": "summary_help",
            "enabled": self.settings.daily_summary_enabled,
            "time_jst": self.settings.daily_summary_time_jst,
        }

    def poll_commands(
        self,
        *,
        poll_timeout: int = 0,
        summary_provider: Callable[[TelegramSettings], str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Poll Telegram getUpdates once for any incoming `/summary` slash commands
        from the authorized chat_id.
        """
        offset = (
            self.settings.last_update_id + 1
            if self.settings.last_update_id is not None
            else None
        )
        updates = self.client.get_updates(offset=offset, timeout=poll_timeout)
        handled: list[dict[str, Any]] = []
        offset_changed = False

        for upd in updates:
            upd_id = upd.get("update_id")
            if isinstance(upd_id, int):
                if self.settings.last_update_id is None or upd_id > self.settings.last_update_id:
                    self.settings.last_update_id = upd_id
                    offset_changed = True

            msg = upd.get("message")
            if not isinstance(msg, dict):
                continue

            chat_id = (msg.get("chat") or {}).get("id")
            if not self.client.is_authorized_chat(chat_id):
                logger.warning(f"Ignoring message from unauthorized Telegram chat_id={chat_id}")
                continue

            text = msg.get("text")
            if isinstance(text, str) and text.strip():
                cmd_result = self.handle_summary_command(
                    text,
                    summary_provider=summary_provider,
                )
                if cmd_result is not None:
                    handled.append(cmd_result)

        if offset_changed:
            self.save_settings()

        return handled

    def wait_for_decision(
        self,
        proposal_id: str,
        message_id: int | None = None,
        *,
        timeout_sec: int | None = None,
        poll_slice_sec: int = 5,
        summary_provider: Callable[[TelegramSettings], str] | None = None,
    ) -> tuple[bool, str, bool]:
        """
        Long-poll Telegram getUpdates until the authorized user taps Approve/Reject
        (or replies approve/reject) or the approval timeout expires.

        While waiting, `/summary` commands from the authorized user are also honored.

        Returns:
            Tuple of (approved: bool, reviewer_notes: str, timed_out: bool).
        """
        effective_timeout = max(
            1,
            int(timeout_sec if timeout_sec is not None else self.config.timeout_sec),
        )
        deadline = self._time_fn() + float(effective_timeout)
        clean_proposal_id = str(proposal_id or "").strip()

        while True:
            remaining = deadline - self._time_fn()
            if remaining <= 0:
                break

            poll_wait = max(0, min(int(poll_slice_sec), int(remaining)))
            offset = (
                self.settings.last_update_id + 1
                if self.settings.last_update_id is not None
                else None
            )
            updates = self.client.get_updates(offset=offset, timeout=poll_wait)
            offset_changed = False

            for upd in updates:
                upd_id = upd.get("update_id")
                if isinstance(upd_id, int):
                    if self.settings.last_update_id is None or upd_id > self.settings.last_update_id:
                        self.settings.last_update_id = upd_id
                        offset_changed = True

                # 1. Check inline button callback_query
                cb = upd.get("callback_query")
                if isinstance(cb, dict):
                    cb_msg = cb.get("message") or {}
                    cb_chat_id = (cb_msg.get("chat") or {}).get("id")
                    if cb_chat_id is None:
                        cb_chat_id = (cb.get("from") or {}).get("id")

                    if not self.client.is_authorized_chat(cb_chat_id):
                        logger.warning(
                            f"Ignoring callback_query from unauthorized Telegram chat_id={cb_chat_id}"
                        )
                        continue

                    cb_id = str(cb.get("id") or "")
                    cb_data = str(cb.get("data") or "").strip()

                    if cb_data in {f"approve:{clean_proposal_id}", "approve"}:
                        if cb_id:
                            try:
                                self.client.answer_callback_query(cb_id, text="✅ Trade Approved")
                            except TelegramAPIError:
                                pass
                        if offset_changed:
                            self.save_settings()
                        return True, "Approved via Telegram inline button.", False

                    if cb_data in {f"reject:{clean_proposal_id}", "reject"}:
                        if cb_id:
                            try:
                                self.client.answer_callback_query(cb_id, text="❌ Trade Rejected")
                            except TelegramAPIError:
                                pass
                        if offset_changed:
                            self.save_settings()
                        return False, "Rejected via Telegram inline button.", False

                # 2. Check text messages / slash commands
                msg = upd.get("message")
                if isinstance(msg, dict):
                    msg_chat_id = (msg.get("chat") or {}).get("id")
                    if not self.client.is_authorized_chat(msg_chat_id):
                        logger.warning(
                            f"Ignoring message from unauthorized Telegram chat_id={msg_chat_id}"
                        )
                        continue

                    text = str(msg.get("text") or "").strip()
                    if not text:
                        continue

                    # Honor /summary commands while waiting
                    if text.lower().startswith("/summary"):
                        self.handle_summary_command(text, summary_provider=summary_provider)
                        continue

                    # Check text approval/rejection replies (if sent after proposal message_id)
                    incoming_msg_id = msg.get("message_id")
                    if (
                        message_id is not None
                        and message_id > 0
                        and isinstance(incoming_msg_id, int)
                        and incoming_msg_id <= message_id
                    ):
                        continue

                    lowered = text.lower()
                    if lowered in {"approve", "/approve", "y", "yes"}:
                        if offset_changed:
                            self.save_settings()
                        return True, "Approved via Telegram text reply.", False
                    if lowered in {"reject", "/reject", "n", "no"}:
                        if offset_changed:
                            self.save_settings()
                        return False, "Rejected via Telegram text reply.", False

            if offset_changed:
                self.save_settings()

            if self._time_fn() >= deadline:
                break

            if not updates and poll_wait == 0:
                self._sleep_fn(min(0.1, max(0.0, deadline - self._time_fn())))

        return (
            False,
            f"Timed out waiting for Telegram approval ({effective_timeout}s).",
            True,
        )

    def send_daily_summary(
        self,
        state: dict[str, Any],
        *,
        ledger_path: str | Path | None = None,
        mark_sent_date: str | None = None,
    ) -> dict[str, Any]:
        """Format and send a daily or on-demand summary digest to Telegram."""
        summary_text = format_daily_summary(
            state,
            ledger_path=ledger_path,
            settings=self.settings,
        )
        response = self.client.send_message(summary_text)
        if mark_sent_date:
            self.settings.last_summary_sent_jst_date = str(mark_sent_date).strip()
            self.save_settings()
        return response

    def send_test_message(self) -> dict[str, Any]:
        """
        Send a test message to verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
        If TELEGRAM_CHAT_ID is empty, attempts to auto-discover it from recent getUpdates.
        """
        discovered_chat_id: str | None = None
        if not self.client.chat_id:
            updates = self.client.get_updates(timeout=0)
            for upd in reversed(updates):
                msg = upd.get("message") or (upd.get("callback_query") or {}).get("message") or {}
                cid = (msg.get("chat") or {}).get("id")
                if cid is not None:
                    discovered_chat_id = str(cid).strip()
                    break
            if discovered_chat_id:
                self.client.chat_id = discovered_chat_id
                self.config.chat_id = discovered_chat_id
            else:
                raise TelegramAPIError(
                    "TELEGRAM_CHAT_ID is not set in .env and no message to your bot was found yet. "
                    "Open Telegram, send 'hello' (or tap Start) to your bot, then run "
                    "'.venv/bin/fx-pipeline --telegram-test' again to auto-discover your TELEGRAM_CHAT_ID."
                )

        text = (
            "✅ FX Signal Reader: Telegram Bot Connected!\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• Authorized Chat ID: {self.client.chat_id}\n"
            f"• Approval Timeout  : {self.config.timeout_sec}s\n\n"
            + format_summary_settings_block(self.settings)
        )
        res = self.client.send_message(text)
        return {
            "ok": True,
            "chat_id": self.client.chat_id,
            "discovered_chat_id": discovered_chat_id,
            "message_id": res.get("message_id"),
            "daily_summary_enabled": self.settings.daily_summary_enabled,
            "daily_summary_time_jst": self.settings.daily_summary_time_jst,
        }


__all__ = ["TelegramNotifier"]
