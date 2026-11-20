# P9-06: Multi-Signal Documentation Overhaul & Verification

* **Phase:** Phase 9: Advanced USD/JPY Signals (Yield Curve, Terms-of-Trade & Intraday Fix)
* **Status:** `PENDING`
* **Complexity:** Finalization & Quality Assurance
* **Last Updated (UTC):** `2026-11-20T13:35:00Z`
* **Implementation Plan:** [plan_p1_p2_p3_signals.md](file:///Users/albert/.gemini/antigravity/brain/df88ef12-66c7-48bc-a6ad-f98f47ed301e/plan_p1_p2_p3_signals.md)

---

## 1. Objective & Scope

Synchronize all project documentation, architecture logs, and user guides with the 3 new signals, and perform exhaustive end-to-end verification:

1. **Repository Documentation:**
   - Update `README.md` with:
     - Tracked Data Sources table (`CL=F`, `2YY=F`, MoF 2Y JGB).
     - System Overview subsection describing the 2Y policy spread, energy terms of trade, and Tokyo Gotobi fixing mechanics.
     - Database schema section documenting the new `yield_spreads` columns.
   - Update `AGENTS.md` with the new signal roles.
   - Update `docs/AGENT_GUIDE.md` with new MCP return schemas and query examples.
   - Update `docs/USD-JPY Signal Pipeline Plan.md` with Phase 9 tasks and deliverables.
2. **End-to-End Verification:**
   - Execute live one-shot collection (`fx-collect --collector all`).
   - Run pipeline execution (`fx-pipeline --reject --json`).
   - Run full regression test suite (`pytest`) ensuring 100% pass rate.

## 2. Implementation Notes & Acceptance Criteria

1. **Files Affected:**
   - `README.md`
   - `AGENTS.md`
   - `docs/AGENT_GUIDE.md`
   - `docs/USD-JPY Signal Pipeline Plan.md`
2. **Acceptance Criteria:**
   - All references to data sources, MCP schemas, and agent decisions are accurate.
   - All 288+ existing tests plus all new unit tests pass without errors.
   - CLI commands execute successfully and produce properly structured output.

- [ ] `README.md`, `AGENTS.md`, and `docs/AGENT_GUIDE.md` updated
- [ ] Task logs synchronized
- [ ] Full automated test suite passing
