"""Telegram notification, persistent settings, and interactive approval package."""

from fx_signal_reader.notifications.client import (
    TelegramAPIError,
    TelegramBotClient,
)
from fx_signal_reader.notifications.formatters import (
    build_approval_inline_keyboard,
    format_daily_summary,
    format_execution_update,
    format_proposal_message,
    format_summary_settings_block,
)
from fx_signal_reader.notifications.settings import (
    DEFAULT_APPROVAL_TIMEOUT_SEC,
    DEFAULT_SETTINGS_PATH,
    DEFAULT_SUMMARY_TIME_JST,
    TelegramConfig,
    TelegramSettings,
    parse_jst_time,
)
from fx_signal_reader.notifications.telegram import TelegramNotifier

__all__ = [
    "DEFAULT_APPROVAL_TIMEOUT_SEC",
    "DEFAULT_SETTINGS_PATH",
    "DEFAULT_SUMMARY_TIME_JST",
    "TelegramAPIError",
    "TelegramBotClient",
    "TelegramConfig",
    "TelegramNotifier",
    "TelegramSettings",
    "build_approval_inline_keyboard",
    "format_daily_summary",
    "format_execution_update",
    "format_proposal_message",
    "format_summary_settings_block",
    "parse_jst_time",
]
