# P7-08: Historical MoF intervention crisis curation

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `TODO`
* **Complexity:** Multi-Task Project (Part 2 of 2: Intervention Crisis Curation & Stress-Testing)
* **Last Updated (UTC):** `2026-11-19T05:50:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Curate and backfill historical MoF/BoJ verbal and physical intervention statements covering major historical intervention episodes (September/October 2022, April/May 2024, and July 2024) into `news_headlines` to stress-test and validate the Risk Committee's 4-tier verbal escalation and physical veto logic against real historical intervention crashes.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/backtest/backfill_intervention.py`:**
   - Seed curated official policymaker headlines (Kanda, Suzuki, Ueda) from Bloomberg/Reuters archives across the 2022–2024 intervention windows with timestamps, sources, and matched keywords.
   - Insert into `news_headlines` in `data/backtest_signals.db`.
2. **Intervention Stress-Test Replay:**
   - Run `fx-backtest` focused on the historical intervention windows (e.g. `2024-04-20` to `2024-05-10` and `2024-07-05` to `2024-07-25`).
   - Verify that Tier-3/Tier-4 verbal warnings and 160.00 spot proximity trigger automated trade vetoes before physical intervention shocks occur, protecting the equity curve from 500-pip flash drops.
3. **Tests & Verification:**
   - Automated test suite verifying headline scoring and veto activation during historical crisis windows.

- [ ] Implementation complete
- [ ] Historical intervention headlines ingested
- [ ] Crisis stress-test veto behavior verified
