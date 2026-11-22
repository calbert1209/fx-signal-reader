# P10-03: Interactive Approval Loop & Summary Command Controller

* **Phase:** Phase 10: Telegram Remote Approval Gate & Daily Summary Engine
* **Status:** `COMPLETED`
* **Complexity:** Downstream Track C (Depends on P10-01 & P10-02)
* **Last Updated (UTC):** `2026-11-21T14:57:00Z`
* **Implementation Plan:** [telegram_approval_plan.md](file:///Users/albert/.gemini/antigravity/brain/537f8594-a251-473e-a17b-9f32bf78951e/telegram_approval_plan.md)

---

## 1. Objective & Scope

Orchestrate the 2-way Telegram inline-button approval lifecycle and interactive `/summary` command processor in `TelegramNotifier`:

1. **Interactive Trade Approval Gate (`send_approval_request`, `wait_for_decision`, `update_decision_message`):**
   - Send the formatted trade proposal with an inline keyboard containing:
     - `[ ✅ Approve ]` (`callback_data="approve:<proposal_id>"`)
     - `[ ❌ Reject ]` (`callback_data="reject:<proposal_id>"`)
   - Long-poll `getUpdates` up to `timeout_sec` (default `300s`).
   - Verify all incoming callbacks and messages against the authorized `TELEGRAM_CHAT_ID`; ignore any unauthorized chat IDs.
   - Acknowledge button taps via `answerCallbackQuery`.
   - Edit the original message via `editMessageText` to remove the inline buttons and append the final `ExecutionReceipt` (or timeout expiration notice).
2. **Interactive `/summary` Command Controller (`poll_commands` & `handle_command`):**
   - Process incoming slash commands from the authorized user:
     - `/summary on`: Enable daily summary and set schedule to default **`09:00` JST** (9:00 AM JST) in `data/telegram_settings.json`; reply with confirmation.
     - `/summary on <H:MM|HH:MM>`: Enable daily summary and schedule it for the specified 24-hour time in JST (e.g., `/summary on 7:30` -> `07:30` JST; `/summary on 14:30` -> `14:30` JST). If the time format is invalid, reply with a friendly validation message.
     - `/summary off`: Disable scheduled daily summaries in `data/telegram_settings.json`; reply with confirmation.
     - `/summary now` (or `/summary`): Immediately invoke the summary callback/generator and send an on-demand summary digest (including current summary settings), even when the daily schedule is disabled.

## 2. Implementation Notes & Acceptance Criteria

1. **Files Owned (Swarm Isolation Boundary):**
   - `fx_signal_reader/notifications/telegram.py`
   - `fx_signal_reader/notifications/__init__.py` (public exports)
2. **Acceptance Criteria:**
   - Button taps (`approve:<id>` / `reject:<id>`) immediately resolve `wait_for_decision` with `(True/False, reviewer_notes)`.
   - Unanswered proposals safely auto-reject (`approved=False`) when `timeout_sec` expires and update the Telegram message.
   - `/summary on`, `/summary on 7:30`, `/summary on 14:30`, `/summary off`, and `/summary now` update persistent settings and send accurate replies.

- [x] `TelegramNotifier` approval lifecycle (`send_approval_request`, `wait_for_decision`, `update_decision_message`) implemented
- [x] `/summary on [HH:MM]`, `/summary off`, and `/summary now` command controller implemented
- [x] `send_test_message` connectivity helper implemented
