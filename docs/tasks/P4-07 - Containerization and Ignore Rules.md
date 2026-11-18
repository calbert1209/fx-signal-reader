# P4-07: Containerization & ignore rules

* **Phase:** Phase 4: Package Reorganization & Cleanup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T11:41:17Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Update `Dockerfile`, `.dockerignore`, and `.gitignore` to package and run the `fx_signal_reader` package cleanly while ignoring build artifacts and runtime SQLite databases.

## 2. Implementation Notes & Acceptance Criteria

1. Update `Dockerfile` to copy `pyproject.toml`, `requirements.txt`, and `fx_signal_reader/`, install the package (`pip install --no-cache-dir --no-deps .`), and set `CMD ["fx-schedule", "--db-path", "/app/data/usdjpy_signals.db"]`.
2. Update `.dockerignore` to exclude `scripts/`, `docs/`, `tests/`, `.venv/`, and `data/*.db*`.
3. Update `.gitignore` to exclude `*.egg-info/`, `build/`, `dist/`, and `data/*.db*` while keeping `data/.gitkeep` and `data/raw_calendar_thisweek.json` tracked.
4. Validate `docker-compose.yml` syntax via `docker compose config -q`.

- [x] Implementation complete
- [x] Verified in local sandbox / environment
- [x] Linked artifacts and logs updated

## 3. Execution Log & Findings

- **Dockerfile:** Updated to `COPY pyproject.toml requirements.txt ./`, install dependencies with `pip install --no-cache-dir -r requirements.txt`, copy `fx_signal_reader/` and `data/` with `--chown=appuser:appuser`, install the local package via `pip install --no-cache-dir --no-deps .`, and set `CMD ["fx-schedule", "--db-path", "/app/data/usdjpy_signals.db"]` while preserving `appuser` (UID/GID 10001), `HEALTHCHECK`, and `STOPSIGNAL SIGTERM`.
- **`.dockerignore`:** Updated to exclude `.venv/`, `__pycache__/`, `*.py[cod]`, `*$py.class`, `.git/`, `.gitignore`, `.DS_Store`, `.env*`, `*.db*`, `data/*.db*`, `tests/`, `docs/`, `scripts/`, `*.md`, `*.egg-info/`, `build/`, and `dist/`.
- **`.gitignore`:** Added `*.egg-info/`, `build/`, `dist/`, and `data/*.db*`.
- **Verification (`2026-11-18T11:41:17Z`):**
  - `docker compose config -q` -> `docker compose config: OK` (exit code 0).
  - `git check-ignore -v data/.gitkeep data/raw_calendar_thisweek.json data/usdjpy_signals.db data/usdjpy_signals.db-wal fx_signal_reader.egg-info/ build/ dist/` confirmed `data/.gitkeep` and `data/raw_calendar_thisweek.json` remain tracked while `data/usdjpy_signals.db`, `data/usdjpy_signals.db-wal`, `fx_signal_reader.egg-info/`, `build/`, and `dist/` are ignored.
