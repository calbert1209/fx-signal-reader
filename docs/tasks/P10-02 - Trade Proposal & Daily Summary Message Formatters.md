# P10-02: Trade Proposal & Daily Summary Message Formatters

* **Phase:** Phase 10: Telegram Remote Approval Gate & Daily Summary Engine
* **Status:** `COMPLETED`
* **Complexity:** Parallel Track B (Swarm Worker 2)
* **Last Updated (UTC):** `2026-11-21T14:52:00Z`
* **Implementation Plan:** [telegram_approval_plan.md](file:///Users/albert/.gemini/antigravity/brain/537f8594-a251-473e-a17b-9f32bf78951e/telegram_approval_plan.md)

---

## 1. Objective & Scope

Implement mobile-friendly message formatters for actionable trade approval alerts, post-decision execution updates, and scheduled/on-demand market & portfolio summaries:

1. **Trade Proposal Alert Formatter (`format_proposal_message`):**
   - Format actionable `BUY` / `SELL` proposals from `AgentState` including:
     - Pair, Action, Position Size (units), Entry Price, Stop Loss, Take Profit, Risk:Reward ratio, and Confidence score.
     - Macro drivers: 10Y & 2Y sovereign yield spreads (`spread_10y`, `spread_2y`), WTI Crude Oil (`CL=F`) terms-of-trade momentum, and Tokyo Gotobi / Nakane session phase.
     - Risk regime, VIX level, MoF / Central Bank intervention alert status, and trade rationale.
2. **Execution Receipt Formatter (`format_execution_update`):**
   - Append final execution status to the original proposal message when `editMessageText` removes the inline buttons:
     - `✅ EXECUTED` (with Order ID, Action, Units, and Fill Price)
     - `❌ REJECTED` (with reviewer notes)
     - `⏱️ EXPIRED (Auto-Rejected)` (when approval timeout elapses)
3. **Daily & On-Demand Summary Formatter (`format_daily_summary`):**
   - Format a comprehensive market & paper-ledger digest:
     - Current Spot price, 10Y & 2Y yield spreads, Crude Oil regime, and whether today is a Tokyo Gotobi corporate settlement date.
     - Risk Regime, VIX, and MoF / Central Bank risk assessment.
     - Upcoming high-impact macro calendar releases in the next 24 hours.
     - 24-hour paper trade ledger recap from `data/paper_trades.jsonl` (count of executed trades and latest orders).
   - **Mandatory Summary Settings Block:** Always include the current summary configuration at the bottom of every scheduled or on-demand (`/summary now`) summary:
     - Summary Schedule Status: `ON (Daily at HH:MM JST)` or `OFF`
     - Quick commands reference: `/summary on [HH:MM]`, `/summary off`, `/summary now`

## 2. Implementation Notes & Acceptance Criteria

1. **Files Owned (Swarm Isolation Boundary):**
   - `fx_signal_reader/notifications/formatters.py`
2. **Acceptance Criteria:**
   - `format_proposal_message` renders clean, readable text for both `USD/JPY` and `EUR/USD` states without crashing on optional/None fields.
   - `format_daily_summary` includes the Summary Settings Block showing whether daily summaries are `ON` or `OFF` and the configured 24-hour JST schedule (e.g., `09:00 JST`, `07:30 JST`, `14:30 JST`).
   - Ledger recap gracefully handles missing or empty `paper_trades.jsonl`.

- [x] `format_proposal_message` implemented
- [x] `format_execution_update` implemented
- [x] `format_daily_summary` with mandatory Summary Settings Block implemented
