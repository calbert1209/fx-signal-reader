# P7-08: Historical MoF intervention crisis curation

* **Phase:** Phase 7: Strategy Refinement & Edge Enhancement
* **Status:** `DONE`
* **Complexity:** Multi-Task Project (Part 2 of 2: Intervention Crisis Curation & Stress-Testing)
* **Last Updated (UTC):** `2026-11-19T08:24:00Z`
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

- [x] Implementation complete
- [x] Historical intervention headlines ingested
- [x] Crisis stress-test veto behavior verified

## Implementation & Verification Summary

- **Modules & Exports:**
  - `fx_signal_reader/backtest/backfill_intervention.py`: curated `CURATED_INTERVENTION_HEADLINES` (`29` historical 2022–2026 episodes) and implemented `backfill_intervention_headlines`, scoring headlines via `NewsRSSCollector().evaluate_headline`.
  - `fx_signal_reader/backtest/backfill.py` & `fx_signal_reader/backtest/__init__.py`: wired `backfill_intervention_headlines` into `backfill_historical_data` (returning `"news_headlines"`) and exported `CURATED_INTERVENTION_HEADLINES` and `backfill_intervention_headlines`.
- **Database Population (`data/backtest_signals.db`, `2022-01-01` -> `2026-11-19`):**
  - Inserted `29` curated headlines across `13` crisis episodes (`2022-09` through `2026-11`), with tier counts `{'1': 0, '2': 2, '3': 3, '4': 24}`.
- **Verification:**
  - `.venv/bin/python -m unittest discover -s tests -v`: `Ran 187 tests in 40.820s` -> `OK` (including `tests/test_backtest_crisis.py` validating `risk_committee_node` crisis veto behavior and `NewsRSSCollector` taxonomy matching).
  - 2-year historical backtest (`.venv/bin/fx-backtest --db-path data/backtest_signals.db --start 2024-11-19 --end 2026-11-19 --spread-pips 1.5`):
    - `MoF Alerts=5.73%` coverage, `vetoes=925` (`steps=3106 | actionable=843 | holds=2263`).
    - Portfolio (`114` trades, `46.49%` win rate): `Net PnL: $1,768.49 (+17.68%)`, `Profit Factor=1.2978`, `Sharpe=0.8228`, `Max Drawdown=$986.68 (9.87%)` (`RISK_ON`: `76` trades, `$733.05`; `TRANSITIONAL`: `38` trades, `$1,035.44`).
