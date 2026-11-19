# P8-02: Universal Directional Bias & Sizing Math

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** Single-Task Foundation
* **Last Updated (UTC):** `2026-11-19T10:47:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Generalize the directional bias classification and position sizing logic to support arbitrary currency pairs:

1. Update `YieldBias` enum to `BULLISH` (spread favors base currency appreciation / BUY), `BEARISH` (spread favors quote currency appreciation / SELL), and `NEUTRAL`.
2. Maintain backward-compatibility aliases: `BULLISH_USD` and `BEARISH_USD` mapping cleanly to `BULLISH` and `BEARISH`.
3. Add `pair` field to `AgentState` and `create_initial_state` (defaulting to `"USD/JPY"`).
4. Generalize equal-dollar risk position sizing in `compute_position_units` to support both:
   - Quote-is-USD (EUR/USD, GBP/USD): $\text{Units} = \lfloor \frac{\text{Capital} \times \text{Risk Fraction}}{\text{SL Distance}} \rfloor$.
   - Base-is-USD (USD/JPY, USD/CHF): $\text{Units} = \lfloor \frac{\text{Capital} \times \text{Risk Fraction} \times \text{Spot}}{\text{SL Distance}} \rfloor$.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/agent/state.py`:**
   - Update `YieldBias` with `BULLISH = "BULLISH"`, `BEARISH = "BEARISH"`, `NEUTRAL = "NEUTRAL"`, and custom validator accepting legacy strings `"BULLISH_USD"` and `"BEARISH_USD"`.
   - Add `pair: str` to `AgentState` TypedDict and `create_initial_state(..., pair="USD/JPY")`.
2. **`fx_signal_reader/agent/nodes.py`:**
   - Parameterize `compute_position_units(spot_price, stop_loss_distance, account_capital_usd, risk_fraction, is_quote_usd=False)`.
   - Ensure USD/JPY calculation remains strictly bit-for-bit identical to baseline.
3. **Acceptance Criteria:**
   - Existing state tests pass without modifications.
   - EUR/USD sizing generates correct standard-lot scale units risking exact target dollars ($100 per $10k at 1%).

- [x] `YieldBias` enum generalized with legacy alias support
- [x] `AgentState` updated with `pair` field
- [x] `compute_position_units` updated for quote-in-USD pairs
- [x] Unit tests written and passing
