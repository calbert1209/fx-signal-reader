# P10-04: Pipeline CLI & Tiered Scheduler Telegram Integration

* **Phase:** Phase 10: Telegram Remote Approval Gate & Daily Summary Engine
* **Status:** `COMPLETED`
* **Complexity:** Downstream Track D (Depends on P10-03)
* **Last Updated (UTC):** `2026-11-21T15:01:00Z`
* **Implementation Plan:** [telegram_approval_plan.md](file:///Users/albert/.gemini/antigravity/brain/537f8594-a251-473e-a17b-9f32bf78951e/telegram_approval_plan.md)

---

## 1. Objective & Scope

Wire `TelegramNotifier` into both the one-shot LangGraph pipeline CLI (`fx-pipeline`) and the continuous tiered daemon (`fx-schedule`):

1. **Pipeline CLI (`fx_signal_reader/cli/pipeline.py`):**
   - Add CLI flags:
     - `--telegram`: Route actionable `BUY` / `SELL` trade proposals paused at `interrupt_before=["paper_execution"]` to Telegram for remote inline-button approval.
     - `--telegram-timeout <sec>`: Override the approval wait timeout in seconds (default: `300`).
     - `--telegram-test`: Send a connectivity verification message to Telegram and exit.
     - `--telegram-summary`: Evaluate current market state and send an immediate summary digest (with summary settings footer) to Telegram and exit.
   - Update `run_signal_pipeline(...)` to accept `telegram: bool = False`, `telegram_timeout: int | None = None`, and `telegram_notifier: Any | None = None`.
   - When a proposal is approved or rejected via Telegram, call `resume_with_decision(...)` and then `notifier.update_decision_message(...)` so the phone message reflects the final `ExecutionReceipt`.
2. **Tiered Scheduler (`fx_signal_reader/cli/schedule.py`):**
   - Add CLI flags:
     - `--telegram`: Enable Telegram command polling (`/summary on [HH:MM]`, `/summary off`, `/summary now`), daily summary delivery, and remote trade approvals.
     - `--run-pipeline`: Automatically evaluate the signal pipeline after each `ticks` collection cycle and send actionable `BUY`/`SELL` proposals to Telegram (`HOLD` signals log quietly without notification noise).
     - `--summary-time-jst <HH:MM>`: Initial default JST time for the daily summary (default: `09:00`, stored/overridden in `data/telegram_settings.json`).
   - Poll for incoming `/summary` commands during the scheduler loop and dynamically reschedule the daily JST summary target when `/summary on [HH:MM]` changes the time.

## 2. Implementation Notes & Acceptance Criteria

1. **Files Owned (Swarm Isolation Boundary):**
   - `fx_signal_reader/cli/pipeline.py`
   - `fx_signal_reader/cli/schedule.py`
2. **Acceptance Criteria:**
   - `fx-pipeline --telegram` pauses on actionable signals, requests Telegram approval, resumes the graph with the user's button decision, and updates the message with the receipt.
   - `fx-pipeline --telegram` does not send approval alerts when the proposal is `HOLD`.
   - `fx-schedule --telegram` responds to `/summary on`, `/summary on 7:30`, `/summary on 14:30`, `/summary off`, and `/summary now`, and dispatches the daily summary at the configured JST time when enabled.

- [x] `fx-pipeline` Telegram flags and approval loop wired
- [x] `fx-schedule` Telegram command polling, dynamic JST summary scheduling, and `--run-pipeline` hook wired
