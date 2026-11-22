# P10-01: Telegram Settings Persistence & Bot API Client

* **Phase:** Phase 10: Telegram Remote Approval Gate & Daily Summary Engine
* **Status:** `COMPLETED`
* **Complexity:** Parallel Track A (Swarm Worker 1)
* **Last Updated (UTC):** `2026-11-21T14:51:00Z`
* **Implementation Plan:** [telegram_approval_plan.md](file:///Users/albert/.gemini/antigravity/brain/537f8594-a251-473e-a17b-9f32bf78951e/telegram_approval_plan.md)

---

## 1. Objective & Scope

Build the foundational configuration, persistent settings store, JST time parser, and zero-dependency HTTP client for the Telegram Bot API using `requests`:

1. **Configuration (`TelegramConfig`):**
   - Load `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_APPROVAL_TIMEOUT` (default `300` seconds), and `TELEGRAM_SUMMARY_TIME_JST` (default `"09:00"` / 9:00 AM JST) from environment variables and constructor overrides.
2. **24-Hour JST Time Parser (`parse_jst_time`):**
   - Accept 24-hour time strings in `H:MM` or `HH:MM` format (e.g., `"9:00"` -> `"09:00"`, `"7:30"` -> `"07:30"`, `"14:30"` -> `"14:30"`).
   - Validate `0 <= hour <= 23` and `0 <= minute <= 59`, raising a descriptive `ValueError` for invalid inputs.
3. **Persistent Settings (`TelegramSettings` & `data/telegram_settings.json`):**
   - Persist `daily_summary_enabled: bool = True` (enabled by default), `daily_summary_time_jst: str = "09:00"` (9:00 AM JST default), `last_update_id: int | None = None`, and `last_summary_sent_jst_date: str | None = None`.
   - Provide atomic load/save helpers so state changes from `/summary on [HH:MM]` and `/summary off` survive restarts.
4. **Telegram HTTP Bot API Client (`TelegramBotClient`):**
   - Implement synchronous REST wrappers around `https://api.telegram.org/bot<TOKEN>/<method>` for `sendMessage`, `editMessageText`, `answerCallbackQuery`, and `getUpdates`.
   - Enforce authorized `chat_id` verification helper (`is_authorized_chat`).

## 2. Implementation Notes & Acceptance Criteria

1. **Files Owned (Swarm Isolation Boundary):**
   - `fx_signal_reader/notifications/__init__.py`
   - `fx_signal_reader/notifications/settings.py`
   - `fx_signal_reader/notifications/client.py`
2. **Acceptance Criteria:**
   - `parse_jst_time("7:30")` returns `"07:30"` and `parse_jst_time("14:30")` returns `"14:30"`; out-of-range times raise `ValueError`.
   - `TelegramSettings` defaults to `daily_summary_enabled=True` and `daily_summary_time_jst="09:00"`, and cleanly round-trips to `data/telegram_settings.json`.
   - `TelegramBotClient` sends inline keyboard payloads and parses update arrays without third-party bot framework dependencies.

- [x] `TelegramConfig` and `parse_jst_time` implemented
- [x] `TelegramSettings` JSON persistence (`data/telegram_settings.json`) implemented
- [x] `TelegramBotClient` HTTP methods (`sendMessage`, `editMessageText`, `answerCallbackQuery`, `getUpdates`) implemented
