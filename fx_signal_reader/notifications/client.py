"""Lightweight synchronous HTTP client for the Telegram Bot API using requests."""

from __future__ import annotations

from typing import Any

import requests


class TelegramAPIError(RuntimeError):
    """Raised when a Telegram Bot API HTTP request fails or returns ok=False."""


class TelegramBotClient:
    """Zero-dependency synchronous wrapper around the Telegram Bot REST API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str = "",
        *,
        session: requests.Session | None = None,
        request_timeout: float = 30.0,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self.bot_token = str(bot_token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.session = session if session is not None else requests.Session()
        self.request_timeout = float(request_timeout)
        self.base_url = base_url.rstrip("/")

    def is_authorized_chat(self, candidate_chat_id: int | str | None) -> bool:
        """Return True only when self.chat_id is configured and matches candidate_chat_id."""
        if not self.chat_id or candidate_chat_id is None:
            return False
        candidate_str = str(candidate_chat_id).strip()
        return bool(candidate_str) and candidate_str == self.chat_id

    def _resolve_chat_id(self, chat_id: str | int | None) -> str:
        resolved = str(chat_id if chat_id is not None else self.chat_id).strip()
        if not resolved:
            raise TelegramAPIError("TELEGRAM_CHAT_ID is not configured.")
        return resolved

    def _request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if not self.bot_token:
            raise TelegramAPIError("TELEGRAM_BOT_TOKEN is not configured.")

        url = f"{self.base_url}/bot{self.bot_token}/{method}"
        effective_timeout = timeout if timeout is not None else self.request_timeout

        try:
            response = self.session.post(
                url,
                json=payload or {},
                timeout=effective_timeout,
            )
        except requests.RequestException as exc:
            raise TelegramAPIError(f"Telegram HTTP request failed for {method}: {exc}") from exc

        status_code = getattr(response, "status_code", 200)
        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramAPIError(
                f"Telegram API returned non-JSON response for {method} (HTTP {status_code})."
            ) from exc

        if not isinstance(data, dict) or not data.get("ok", False):
            description = (
                data.get("description", "Unknown Telegram API error")
                if isinstance(data, dict)
                else str(data)
            )
            raise TelegramAPIError(
                f"Telegram API error on {method} (HTTP {status_code}): {description}"
            )

        if isinstance(status_code, int) and status_code >= 400:
            description = data.get("description", "HTTP error")
            raise TelegramAPIError(
                f"Telegram API HTTP {status_code} on {method}: {description}"
            )

        return data.get("result")

    def send_message(
        self,
        text: str,
        *,
        chat_id: str | int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message (optionally with an inline keyboard) via sendMessage."""
        target_chat_id = self._resolve_chat_id(chat_id)
        payload: dict[str, Any] = {
            "chat_id": target_chat_id,
            "text": str(text),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode

        result = self._request("sendMessage", payload)
        return result if isinstance(result, dict) else {"result": result}

    def edit_message_text(
        self,
        message_id: int,
        text: str,
        *,
        chat_id: str | int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """Edit an existing message's text and inline keyboard via editMessageText."""
        target_chat_id = self._resolve_chat_id(chat_id)
        payload: dict[str, Any] = {
            "chat_id": target_chat_id,
            "message_id": int(message_id),
            "text": str(text),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode

        result = self._request("editMessageText", payload)
        return result if isinstance(result, dict) else {"result": result}

    def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge an inline keyboard callback button press via answerCallbackQuery."""
        payload: dict[str, Any] = {
            "callback_query_id": str(callback_query_id),
        }
        if text is not None:
            payload["text"] = str(text)
        if show_alert:
            payload["show_alert"] = True

        result = self._request("answerCallbackQuery", payload)
        return bool(result)

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 10,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Long-poll incoming updates (messages and callback_query events) via getUpdates."""
        payload: dict[str, Any] = {
            "timeout": int(timeout),
        }
        if offset is not None:
            payload["offset"] = int(offset)
        if allowed_updates is not None:
            payload["allowed_updates"] = list(allowed_updates)

        http_timeout = max(self.request_timeout, float(timeout) + 5.0)
        result = self._request("getUpdates", payload, timeout=http_timeout)
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []
