# P8-09: Documentation Multi-Pair Generalization

* **Phase:** Phase 8: Multi-Pair Generalization & EUR/USD Expansion
* **Status:** `COMPLETED`
* **Complexity:** Documentation & Architecture Sync
* **Last Updated (UTC):** `2026-11-20T03:18:00Z`
* **Architecture Plan:** [Multi-Pair Expansion Plan.md](../Multi-Pair%20Expansion%20Plan.md)

---

## 1. Objective & Scope

Synchronize repository documentation, user guides, configuration templates, and project metadata with the completed Phase 8 multi-pair expansion supporting both **USD/JPY** and **EUR/USD**:

1. Modernize [README.md](../../README.md) to describe the multi-pair architecture, pair-segregated databases (`usdjpy_signals.db`, `eurusd_signals.db`), German Bund yield ingestion, European calendar feeds, and the six CLI commands (`fx-collect`, `fx-schedule`, `fx-pipeline`, `fx-backtest`, `fx-mcp`, `fx-mcp-client`).
2. Add a comprehensive historical backtest guide to [README.md](../../README.md) explaining 2-year hourly (`--interval 1h`) and 5-year daily (`--interval 1d`) simulation options.
3. Update [AGENT_GUIDE.md](../AGENT_GUIDE.md) with multi-pair MCP host configurations, universal `YieldBias` thresholds, pair-aware Risk Committee policies (`VERBAL_LADDER` vs `CENTRAL_BANK`), and equal-dollar position sizing math.
4. Add archival reference banners to [USD-JPY Signal Pipeline Plan.md](../USD-JPY%20Signal%20Pipeline%20Plan.md) pointing to [Multi-Pair Expansion Plan.md](../Multi-Pair%20Expansion%20Plan.md).
5. Update project metadata in [pyproject.toml](../../pyproject.toml), [.env.example](../../.env.example), and [AGENTS.md](../../AGENTS.md).

---

## 2. Deliverables & Acceptance Criteria

- [x] Primary `README.md` updated with multi-pair overview, data source tables, `--pair` CLI flags, and `fx-backtest` guide
- [x] `docs/AGENT_GUIDE.md` updated with dual-pair MCP configuration, Python graph API examples, and pair risk rules
- [x] `docs/Multi-Pair Expansion Plan.md` updated with completion milestones and empirical backtest benchmarks
- [x] Archival reference banner added to `docs/USD-JPY Signal Pipeline Plan.md`
- [x] Package metadata updated in `pyproject.toml`, `.env.example`, and `AGENTS.md`
- [x] All 288 automated tests passing without regression
