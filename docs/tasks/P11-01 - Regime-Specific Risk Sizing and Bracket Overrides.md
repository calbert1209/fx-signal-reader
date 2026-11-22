# P11-01: Regime-Specific Risk Sizing and Bracket Overrides

* **Phase:** Phase 11: Asymmetric Regime Risk & Bracket Configuration
* **Status:** `DONE`
* **Complexity:** Multi-File Feature & Unified Backtest Verification
* **Last Updated (UTC):** `2026-11-22T10:08:00Z`
* **Reference Study:** `data/backtest_regime_study/summary.md`

---

## 1. Objective & Scope

Enable per-regime parameter overrides (`risk_fraction`, `sl_pips`, `tp_pips`, `use_atr`) across the LangGraph signal pipeline, backtest simulator, and CLIs so asymmetric regime strategies (e.g. conservative `RISK_ON` at `1.0% / 80p / 160p` and aggressive `TRANSITIONAL` at `4.0% / 120p / 240p`) can be backtested in a single unified pass with true cross-regime overlap blocking and a single shared equity pool—and run identically in live paper execution.

## 2. Implementation Notes & Acceptance Criteria

1. **Agent State (`fx_signal_reader/agent/state.py`):**
   - Add `regime_overrides: dict[str, dict[str, Any]] | None` to `AgentState` and `create_initial_state(...)`.
   - Normalize regime keys (uppercase `RiskRegime` names) and validate numeric fields.
2. **Risk Committee Node (`fx_signal_reader/agent/nodes.py`):**
   - In `risk_committee_node`, after `risk_regime` is classified, resolve effective `risk_fraction`, `sl_pips`, `tp_pips`, and `use_atr` by checking `state.get("regime_overrides", {}).get(risk_regime.value)` before falling back to top-level state values.
3. **Backtest Schemas & Simulator (`fx_signal_reader/backtest/models.py`, `fx_signal_reader/backtest/simulator.py`):**
   - Add `regime_overrides: dict[str, dict[str, Any]] | None = None` to `BacktestConfig` with strict validation (valid `RiskRegime` keys, finite positive `risk_fraction` in `(0, 1]`, positive `sl_pips` / `tp_pips`, optional `use_atr` bool).
   - Pass `regime_overrides=effective_config.regime_overrides` into `create_initial_state(...)` in `run_backtest(...)`.
4. **CLI Flags (`fx_signal_reader/cli/backtest.py`, `fx_signal_reader/cli/pipeline.py`):**
   - Add `--regime-overrides` (JSON string or path to `.json` config file) to both `fx-backtest` and `fx-pipeline`, supporting both `risk_fraction` and `risk_pct` convenience keys in JSON input.
   - Surface active `regime_overrides` in `format_backtest_summary(...)`.
5. **Tests & Unified 5-Year Verification:**
   - Add unit and integration tests covering `AgentState`, `risk_committee_node` regime resolution, `BacktestConfig` validation, `run_backtest` cross-regime overlap + shared equity curve, and CLI `--regime-overrides` parsing.
   - Run the existing test suite to ensure zero regressions.
   - Execute a unified 5-year USD/JPY backtest on `data/backtest_signals_5y.db` with `{"RISK_ON": {"risk_pct": 1.0, "sl_pips": 80, "tp_pips": 160}, "TRANSITIONAL": {"risk_pct": 4.0, "sl_pips": 120, "tp_pips": 240}}` and record the unified results in `data/backtest_regime_study/`.

- [x] `AgentState` and `create_initial_state` updated with `regime_overrides`
- [x] `risk_committee_node` applies per-regime sizing and bracket overrides
- [x] `BacktestConfig` and `run_backtest` support and validate `regime_overrides`
- [x] `fx-backtest` and `fx-pipeline` expose `--regime-overrides`
- [x] Unit/integration tests added and full test suite passing (`373 passed, 152 subtests passed`)
- [x] Unified 5-year backtest executed and stored (`data/backtest_regime_study/unified_recommended_*`)
