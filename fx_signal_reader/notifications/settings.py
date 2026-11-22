"""Telegram configuration, 24-hour JST time parsing, and persistent JSON settings."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any

from fx_signal_reader.core import PROJECT_ROOT

DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "data" / "telegram_settings.json"
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_SUMMARY_TIME_JST = "09:00"
DEFAULT_APPROVAL_TIMEOUT_SEC = 300

_JST_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")


def _load_dotenv_dict(dotenv_path: str | Path = DEFAULT_DOTENV_PATH) -> dict[str, str]:
    """Parse a .env file into a key-value dictionary without mutating os.environ."""
    path = Path(dotenv_path)
    if not path.is_file():
        return {}
    parsed: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
            if "=" not in line:
                continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        parsed[key] = value
    return parsed


def parse_jst_time(time_str: str) -> str:
    """Parse and normalize a 24-hour JST time string ('H:MM' or 'HH:MM') to 'HH:MM'.

    Examples:
        '9:00'  -> '09:00'
        '7:30'  -> '07:30'
        '14:30' -> '14:30'
        '00:05' -> '00:05'

    Raises:
        ValueError: If time_str is not a valid 24-hour colon-separated time (00:00 to 23:59).
    """
    if not isinstance(time_str, str):
        raise ValueError(f"Invalid JST time {time_str!r}: expected string in HH:MM format.")

    cleaned = time_str.strip()
    match = _JST_TIME_PATTERN.match(cleaned)
    if not match:
        raise ValueError(
            f"Invalid JST time '{time_str}': must be 24-hour H:MM or HH:MM format (e.g. '09:00', '7:30', '14:30')."
        )

    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(
            f"Invalid JST time '{time_str}': hour must be 00-23 and minute must be 00-59."
        )

    return f"{hour:02d}:{minute:02d}"


@dataclass
class TelegramConfig:
    """Runtime configuration for the Telegram Remote Approval Gate and Daily Summary."""

    bot_token: str = ""
    chat_id: str = ""
    timeout_sec: int = DEFAULT_APPROVAL_TIMEOUT_SEC
    summary_time_jst: str = DEFAULT_SUMMARY_TIME_JST
    settings_path: Path = field(default_factory=lambda: DEFAULT_SETTINGS_PATH)

    def __post_init__(self) -> None:
        self.bot_token = str(self.bot_token or "").strip()
        self.chat_id = str(self.chat_id or "").strip()
        self.timeout_sec = int(self.timeout_sec)
        self.summary_time_jst = parse_jst_time(self.summary_time_jst or DEFAULT_SUMMARY_TIME_JST)
        self.settings_path = Path(self.settings_path)

    @classmethod
    def from_env(
        cls,
        *,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout_sec: int | None = None,
        summary_time_jst: str | None = None,
        settings_path: str | Path | None = None,
        dotenv_path: str | Path | None = DEFAULT_DOTENV_PATH,
    ) -> TelegramConfig:
        """Build TelegramConfig from environment variables (with .env fallback) and optional overrides."""
        dotenv_vars = _load_dotenv_dict(dotenv_path) if dotenv_path is not None else {}

        def _get_var(name: str, default: str = "") -> str:
            env_val = os.getenv(name)
            if env_val is not None and env_val.strip():
                return env_val.strip()
            return dotenv_vars.get(name, default).strip()

        resolved_token = (
            bot_token if bot_token is not None else _get_var("TELEGRAM_BOT_TOKEN", "")
        ).strip()
        resolved_chat_id = (
            str(chat_id) if chat_id is not None else _get_var("TELEGRAM_CHAT_ID", "")
        ).strip()

        if timeout_sec is not None:
            resolved_timeout = int(timeout_sec)
        else:
            raw_timeout = _get_var("TELEGRAM_APPROVAL_TIMEOUT", "")
            if raw_timeout:
                try:
                    resolved_timeout = int(raw_timeout)
                except ValueError:
                    resolved_timeout = DEFAULT_APPROVAL_TIMEOUT_SEC
            else:
                resolved_timeout = DEFAULT_APPROVAL_TIMEOUT_SEC

        raw_time = (
            summary_time_jst
            if summary_time_jst is not None
            else (_get_var("TELEGRAM_SUMMARY_TIME_JST", "") or DEFAULT_SUMMARY_TIME_JST)
        )
        resolved_time = parse_jst_time(raw_time)

        if settings_path is not None:
            resolved_path = Path(settings_path)
        else:
            env_path = _get_var("TELEGRAM_SETTINGS_PATH", "")
            resolved_path = Path(env_path) if env_path else DEFAULT_SETTINGS_PATH

        return cls(
            bot_token=resolved_token,
            chat_id=resolved_chat_id,
            timeout_sec=resolved_timeout,
            summary_time_jst=resolved_time,
            settings_path=resolved_path,
        )

    def is_configured(self) -> bool:
        """Return True when both bot_token and chat_id are non-empty."""
        return bool(self.bot_token.strip() and self.chat_id.strip())


@dataclass
class TelegramSettings:
    """Persistent user preferences and update offset state stored in data/telegram_settings.json."""

    daily_summary_enabled: bool = True
    daily_summary_time_jst: str = DEFAULT_SUMMARY_TIME_JST
    last_update_id: int | None = None
    last_summary_sent_jst_date: str | None = None

    def __post_init__(self) -> None:
        self.daily_summary_enabled = bool(self.daily_summary_enabled)
        self.daily_summary_time_jst = parse_jst_time(
            self.daily_summary_time_jst or DEFAULT_SUMMARY_TIME_JST
        )
        if self.last_update_id is not None:
            self.last_update_id = int(self.last_update_id)
        if self.last_summary_sent_jst_date is not None:
            cleaned_date = str(self.last_summary_sent_jst_date).strip()
            self.last_summary_sent_jst_date = cleaned_date or None

    def to_dict(self) -> dict[str, Any]:
        """Serialize settings to a JSON-compatible dictionary."""
        return {
            "daily_summary_enabled": self.daily_summary_enabled,
            "daily_summary_time_jst": self.daily_summary_time_jst,
            "last_update_id": self.last_update_id,
            "last_summary_sent_jst_date": self.last_summary_sent_jst_date,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        default_time_jst: str = DEFAULT_SUMMARY_TIME_JST,
    ) -> TelegramSettings:
        """Deserialize settings from a dictionary with safe fallback defaults."""
        fallback_time = parse_jst_time(default_time_jst or DEFAULT_SUMMARY_TIME_JST)
        raw_time = data.get("daily_summary_time_jst")
        if isinstance(raw_time, str) and raw_time.strip():
            try:
                normalized_time = parse_jst_time(raw_time)
            except ValueError:
                normalized_time = fallback_time
        else:
            normalized_time = fallback_time

        raw_enabled = data.get("daily_summary_enabled", True)
        enabled = bool(raw_enabled) if isinstance(raw_enabled, bool) else True

        raw_update_id = data.get("last_update_id")
        last_update_id: int | None = None
        if raw_update_id is not None:
            try:
                last_update_id = int(raw_update_id)
            except (TypeError, ValueError):
                last_update_id = None

        raw_sent_date = data.get("last_summary_sent_jst_date")
        last_sent_date = (
            str(raw_sent_date).strip()
            if isinstance(raw_sent_date, str) and raw_sent_date.strip()
            else None
        )

        return cls(
            daily_summary_enabled=enabled,
            daily_summary_time_jst=normalized_time,
            last_update_id=last_update_id,
            last_summary_sent_jst_date=last_sent_date,
        )

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        default_time_jst: str = DEFAULT_SUMMARY_TIME_JST,
    ) -> TelegramSettings:
        """Load persistent settings from JSON file, returning defaults if missing or corrupt."""
        target_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
        fallback_time = parse_jst_time(default_time_jst or DEFAULT_SUMMARY_TIME_JST)

        if not target_path.exists():
            return cls(
                daily_summary_enabled=True,
                daily_summary_time_jst=fallback_time,
            )

        try:
            raw_text = target_path.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                return cls(
                    daily_summary_enabled=True,
                    daily_summary_time_jst=fallback_time,
                )
            return cls.from_dict(payload, default_time_jst=fallback_time)
        except (OSError, json.JSONDecodeError, ValueError):
            return cls(
                daily_summary_enabled=True,
                daily_summary_time_jst=fallback_time,
            )

    def save(self, path: str | Path | None = None) -> Path:
        """Persist settings to disk as formatted JSON."""
        target_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        return target_path
