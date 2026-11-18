# P2-04: Containerization

* **Phase:** Phase 2: Collection Architecture & Local Persistence
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T09:05:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Containerize the USD/JPY signal reader collection engine and tiered scheduler using modern container standards, security hardening, and persistent local storage:
1. **Dockerfile (`Dockerfile`):**
   - Official lightweight base: `python:3.11-slim`.
   - Security hardening: Enforce non-root execution via dedicated unprivileged `appuser` (UID/GID `10001`).
   - System dependencies: Minimal footprint installing only `curl` (for healthcheck / HTTP diagnostics) and `ca-certificates` (for secure TLS API connections), with apt cache purged.
   - Python optimization: Set `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1`.
   - Graceful termination: Set `STOPSIGNAL SIGTERM` mapped to signal handlers in `scheduler.py` for clean SQLite connection teardown.
   - Container Healthcheck: `HEALTHCHECK` with `--interval=30s --timeout=10s --start-period=15s --retries=3` verifying SQLite database responsiveness via `PRAGMA schema_version;`.
   - Default entrypoint: `python scheduler.py --db-path /app/data/usdjpy_signals.db`.
2. **Docker Compose (`docker-compose.yml`):**
   - Service name: `usdjpy-collector`.
   - Persistent bind mount: Host `./data` mapped to `/app/data` ensuring SQLite main database (`usdjpy_signals.db`), Write-Ahead Log (`usdjpy_signals.db-wal`), and shared-memory index (`usdjpy_signals.db-shm`) survive container restarts and updates.
   - Environment variables: `FX_DB_PATH=/app/data/usdjpy_signals.db`, `FRED_API_KEY=${FRED_API_KEY:-}`, `POLL_INTERVAL_NEWS=300`, `POLL_INTERVAL_TICKS=900`, `LOG_LEVEL=INFO`.
   - Restart policy: `unless-stopped`.
   - Structured logging: `json-file` driver with rotation capped at `max-size: 10m` and `max-file: 3`.
3. **Build Exclusions (`.dockerignore`):**
   - Excludes `.venv/`, `__pycache__/`, `.git/`, test databases, `.DS_Store`, local `.env` files, and test caches.
4. **Configuration Template (`.env.example`):**
   - Documents all runtime environment variables with clear security instructions and defaults.

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete
- [x] Verified in local sandbox / environment
- [x] Linked artifacts and logs updated

### Acceptance Verification Summary:
- **Dockerfile Standards:** Built upon `python:3.11-slim`, non-root `appuser` (UID/GID 10001), `STOPSIGNAL SIGTERM`, and built-in SQLite WAL healthcheck.
- **Docker Compose Syntax:** Validated with `docker compose config` (exited 0).
- **Persistent Volume Mount:** `./data:/app/data` bind mount verified for SQLite WAL journal mode and multi-connection concurrency.
- **Automated Verification Suite:** Created `tests/test_containerization.py` containing 6 unit and integration test cases covering Dockerfile directives, compose parsing, `.dockerignore`, `.env.example`, and SQLite WAL lifecycle. 100% test pass rate across 34 project tests.

---

## 3. Execution Log & Findings

### 3.1 Container Architecture & Security Rationale

#### Non-Root Execution (`appuser:10001`)
Running container processes as `root` presents severe security vulnerabilities if a container breakout or remote code execution flaw occurs in dependency libraries. In this implementation:
- Group and user `appuser` are provisioned with static UID/GID `10001`.
- Ownership of `/app` and `/app/data` is granted to `appuser:appuser` during image build.
- The runtime switches to `USER appuser` prior to execution.

#### Graceful Teardown (`STOPSIGNAL SIGTERM`)
SQLite operating in WAL mode maintains active file locks and shared memory indices (`-shm`). An abrupt container termination (`SIGKILL`) risks uncheckpointed transactions or stale lock files:
- The `Dockerfile` declares `STOPSIGNAL SIGTERM`.
- `scheduler.py` intercepts `SIGTERM` and `SIGINT`, terminating active polling loops, flushing open transactions, checkpointing the WAL, and closing database connections cleanly.

#### Healthcheck Implementation
The container healthcheck probes the database without requiring external network exposure:
```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import os, sqlite3, sys; p = os.getenv('FX_DB_PATH', '/app/data/usdjpy_signals.db'); sys.exit(0 if not os.path.exists(p) else (0 if sqlite3.connect(p).execute('PRAGMA schema_version;').fetchone() is not None else 1))" || exit 1
```

### 3.2 Docker Compose Configuration (`docker-compose.yml`)
```yaml
version: "3.8"

services:
  usdjpy-collector:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: usdjpy-collector
    restart: unless-stopped
    volumes:
      # Persistent bind mount for SQLite database and WAL/SHM journaling files
      - ./data:/app/data
    environment:
      - FX_DB_PATH=/app/data/usdjpy_signals.db
      - FRED_API_KEY=${FRED_API_KEY:-}
      - POLL_INTERVAL_NEWS=300
      - POLL_INTERVAL_TICKS=900
      - LOG_LEVEL=INFO
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### 3.3 SQLite WAL Mode Volume Mount Verification
Integration tests in `tests/test_containerization.py` verify that:
1. `PRAGMA journal_mode = WAL;` creates and operates `-wal` and `-shm` sidecar files in the mounted volume directory.
2. Concurrent read connections (such as future MCP servers or healthchecks) can read committed ticks and spreads simultaneously without blocking background collector write transactions.
3. Database and schema states persist accurately when re-opened across simulated container restarts.

### 3.4 Test Suite Execution
```text
$ .venv/bin/python -m unittest tests/test_containerization.py
......
----------------------------------------------------------------------
Ran 6 tests in 0.175s

OK

$ .venv/bin/python -m unittest discover tests
..................................
----------------------------------------------------------------------
Ran 34 tests in 0.179s

OK
```
