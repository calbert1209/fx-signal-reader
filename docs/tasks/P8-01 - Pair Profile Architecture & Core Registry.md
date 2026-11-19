# P8-01: Pair Profile Architecture & Core Registry

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** Single-Task Foundation
* **Last Updated (UTC):** `2026-11-19T10:47:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Establish the core foundational abstraction layer (`PairProfile` dataclass and centralized registry) that decouples currency pair specifications, pricing conventions, pip mathematics, and sovereign yield benchmarks from the USD/JPY-specific assumptions:

1. Define a type-safe `PairProfile` dataclass supporting base/quote currencies, pip scales (`0.01` for JPY pairs, `0.0001` for EUR/USD), price precision (`3` vs `5` decimals), spot symbols (`JPY=X`, `EURUSD=X`), and benchmark yield symbols (`^TNX`, `JGB10Y`, `DE10Y`).
2. Implement built-in profiles for `USD/JPY` and `EUR/USD` in `fx_signal_reader.core.pair_profile`.
3. Provide profile resolution utilities: `get_pair_profile(pair_name)` supporting standard formats (`"USD/JPY"`, `"USDJPY"`, `"EUR/USD"`, `"EURUSD"`).
4. Provide database path resolution per pair: `get_pair_db_path(pair, base_dir)` routing to `data/usdjpy_signals.db`, `data/eurusd_signals.db`, etc.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/core/pair_profile.py`:**
   - Create dataclass `PairProfile` with fields: `pair`, `symbol_slug`, `base_currency`, `quote_currency`, `spot_symbol`, `pip_size`, `price_precision`, `default_sl_pips`, `default_tp_pips`, `base_yield_symbol`, `quote_yield_symbol`, `equity_benchmark`, `calendar_currencies`, `risk_policy`, and `psychological_barriers`.
   - Add property helpers: `is_quote_usd` and `is_base_usd`.
   - Implement `get_pair_profile(...)` with case-insensitive normalization.
   - Export profiles from `fx_signal_reader.core`.
2. **Acceptance Criteria:**
   - `get_pair_profile("EUR/USD")` and `get_pair_profile("EURUSD")` return the EUR/USD profile.
   - `get_pair_profile("USD/JPY")` returns exact baseline parameters matching USD/JPY current behavior.
   - Unit tests verify all fields and helper properties.

- [x] `PairProfile` dataclass & registry created
- [x] Profile lookup & path resolution implemented
- [x] Unit tests written and passing
