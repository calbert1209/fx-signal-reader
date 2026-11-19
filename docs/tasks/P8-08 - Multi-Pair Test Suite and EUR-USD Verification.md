# P8-08: Multi-Pair Test Suite & EUR/USD Verification

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** Verification & Regression Testing
* **Last Updated (UTC):** `2026-11-19T12:11:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Establish comprehensive test coverage for the multi-pair architecture and execute end-to-end verification for EUR/USD:

1. Build `tests/test_pair_profile.py` validating registry lookups, property helpers, and edge cases.
2. Extend `tests/test_nodes.py` to test both USD/JPY and EUR/USD through `macro_analyst_node` and `risk_committee_node` (including Event Blackout veto verification).
3. Extend `tests/test_backtest_simulator.py` to verify EUR/USD bracket resolution with 5-decimal precision, 0.0001 pip scale, and quote-in-USD dollar PnL accounting.
4. Execute full regression suite to guarantee all existing 187+ unit/integration tests continue passing without regression.
5. Perform an end-to-end simulated backtest run for EUR/USD and verify report generation.

## 2. Implementation Notes & Acceptance Criteria

1. **`tests/test_pair_profile.py`:**
   - Test `get_pair_profile` with `"USD/JPY"`, `"USDJPY"`, `"EUR/USD"`, `"EURUSD"`, lowercase variants, and invalid pair errors.
2. **`tests/test_nodes.py` & `tests/test_backtest_simulator.py`:**
   - Add parameterized tests asserting identical baseline behavior for USD/JPY and expected pip/sizing behavior for EUR/USD.
3. **Acceptance Criteria:**
   - All tests passing (`100%` pass rate, 0 failures).
   - EUR/USD backtest generates valid `BacktestReport` with positive or verified edge metrics.

- [x] `tests/test_pair_profile.py` created and passing
- [x] Node and simulator tests updated for multi-pair assertions
- [x] Full regression test suite passing
- [x] EUR/USD backtest run and report verified
