# P5-01: Project README (ASD-STE100)

* **Phase:** Phase 5: Documentation & Onboarding
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T12:06:57Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Create a user-facing `README.md` at the repository root using the `simple-tech-eng` (`ASD-STE100`) specification to cover system overview, installation, configuration, CLI/Docker usage, project structure, and testing.

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete ([`README.md`](file:///Users/albert/Projects/fx-signal-reader/README.md) written following ASD-STE100 rules)
- [x] Verified in local sandbox / environment (all referenced CLI commands, paths, and `.env.example` keys verified against the `fx_signal_reader` package)
- [x] Linked artifacts and logs updated (linked [`docs/AGENT_GUIDE.md`](file:///Users/albert/Projects/fx-signal-reader/docs/AGENT_GUIDE.md) and recorded in Google Sheet row 24)

---

## 3. Execution Log & Findings

- Composed [`README.md`](file:///Users/albert/Projects/fx-signal-reader/README.md) obeying ASD-STE100 limits (<= 20 words procedural, <= 25 words descriptive, active voice, imperative mood for instructions, max 3-noun clusters).
- Documented `fx-collect`, `fx-schedule`, `fx-pipeline`, `fx-mcp`, `fx-mcp-client`, `docker compose up -d`, and `.venv/bin/python -m unittest discover -s tests -v`.
