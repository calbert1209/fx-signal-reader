# P11-02: Automated Scheduled Execution with Regime Overrides and Telegram Action Alerts

* **Phase:** Phase 11: Asymmetric Regime Risk & Bracket Configuration
* **Status:** `DONE`
* **Complexity:** Multi-File Feature & Integration Verification
* **Created (UTC):** `2026-11-23T10:55:00Z`
* **Parent Plan:** `docs/USD-JPY Signal Pipeline Plan.md`
* **Upstream Task:** `P11-01` (`1KfhhWMwmpDBhSaf-_K0pAKyN85FH1zQa`)

---

## 1. Objective & Scope

Enable hands-free, autonomous paper trade execution in the `fx-schedule` daemon using fine-tuned asymmetrical regime overrides (`RISK_ON`: 1.0% risk / 80p SL / 160p TP; `TRANSITIONAL`: 4.0% risk / 120p SL / 240p TP), while immediately notifying the user on Telegram whenever an automated paper trade is executed, and maintaining the daily scheduled summary digest.

---

## 2. Implementation Subtasks & Acceptance Criteria

### Subtask 1: Telegram Action Alert Notification Formatter & Client Method
* Add `format_execution_alert(state: dict[str, Any], receipt: dict[str, Any]) -> str` in `fx_signal_reader/notifications/formatters.py` to format mobile-friendly trade alerts (action, units, pair, fill price, SL, TP, order ID, regime).
* Add `send_execution_alert(state: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]` in `fx_signal_reader/notifications/telegram.py` on `TelegramNotifier`.

### Subtask 2: Pipeline Execution Alert Triggering on Auto-Approval
* Update `run_signal_pipeline()` in `fx_signal_reader/cli/pipeline.py`:
  * When `auto_approve=True` and an actionable proposal executes (`status == 'EXECUTED'`), trigger `telegram_notifier.send_execution_alert(final_state, receipt)` if Telegram is enabled.
  * Accept and pass `regime_overrides` down into `create_initial_state(...)`.

### Subtask 3: Scheduler CLI & Daemon Integration
* Extend `SchedulerConfig` and `build_arg_parser()` in `fx_signal_reader/cli/schedule.py`:
  * Add `--auto-approve` flag.
  * Add `--regime-overrides` parameter (JSON string or file path).
  * Add `--config` parameter supporting `.json` / `.yaml` file input.
* In `Scheduler.run_pipeline_evaluation()`, forward `auto_approve` and `regime_overrides` into `run_signal_pipeline(...)`.

### Subtask 4: Test Suite & End-to-End Verification
* Create `tests/test_scheduler_auto_execution.py` verifying CLI arg parsing, pipeline invocation forwarding, and Telegram alert firing.
* Verify 0 regressions on the full regression test suite (`373+` tests).
* Validate `fx-schedule --pair USD/JPY --run-once --dry-run --auto-approve --regime-overrides ...`.

---

## 3. Checklist
- [x] Subtask 1: `format_execution_alert` and `send_execution_alert` implemented
- [x] Subtask 2: `run_signal_pipeline` dispatches execution alerts on auto-approve
- [x] Subtask 3: `fx-schedule` supports `--auto-approve`, `--regime-overrides`, and `--config`
- [x] Subtask 4: Integration tests added and full test suite passing
