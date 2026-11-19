# P8-04: Multi-Pair Calendar & Central Bank News Ingestion

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** Ingestion Layer Generalization
* **Last Updated (UTC):** `2026-11-19T10:47:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](https://drive.google.com/file/d/1ZV7tTlHPUgQLTlcHwQkUfTcMC6eBD5Ho/view)

---

## 1. Objective & Scope

Expand economic calendar filtering and news headline monitoring to cover European catalysts:

1. Generalize `CalendarCollector` to accept target currency sets dynamically based on `PairProfile` (e.g. `{"EUR", "USD"}` for EUR/USD vs `{"USD", "JPY"}` for USD/JPY).
2. Ensure ISO-8601 UTC timestamp parsing and deterministic SHA256 event deduplication function seamlessly for EUR releases (ECB rate decisions, German CPI, Eurozone flash PMI).
3. Expand `NewsRSSCollector` to include European Central Bank official press release RSS feeds (`https://www.ecb.europa.eu/rss/press.html`) and EUR FX news wires.
4. Add European central banking entity recognition (Lagarde, Schnabel, de Guindos, Nagel, Villeroy) and policy divergence keywords.

## 2. Implementation Notes & Acceptance Criteria

1. **`fx_signal_reader/collectors/calendar.py`:**
   - Add `pair: str = "USD/JPY"` or `currencies: Optional[Set[str]] = None` parameter to `CalendarCollector`.
   - Filter events: `curr in target_currencies`.
2. **`fx_signal_reader/collectors/news_rss.py`:**
   - Parameterize feed registry and keyword evaluation by `pair`.
   - For `EUR/USD`, activate ECB official releases and Google News EUR/USD queries.
3. **Acceptance Criteria:**
   - Running calendar collection for `EUR/USD` extracts EUR and USD releases into `macro_events`.
   - Running news collection for `EUR/USD` polls ECB and EUR news feeds without error.

- [x] Calendar collector parameterized by pair currencies
- [x] ECB RSS feed and European policymaker entities added to news collector
- [x] Unit tests for multi-currency calendar filtering written and passing
