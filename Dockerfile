# USD/JPY Signal Reader - Collection Engine & Scheduler
# Lightweight official Python 3.11 slim image
FROM python:3.11-slim

# Set runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FX_DB_PATH=/app/data/usdjpy_signals.db

# Security: Non-root user appuser (UID/GID 10001) for unprivileged execution
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/bash -m appuser

# Install system dependencies: curl for healthcheck / HTTP diagnostics, ca-certificates for TLS
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies from requirements.txt
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Create data directory and assign ownership to appuser
RUN mkdir -p /app/data && chown -R appuser:appuser /app

# Copy application source code and initial data, then install the local package
COPY --chown=appuser:appuser fx_signal_reader/ ./fx_signal_reader/
COPY --chown=appuser:appuser data/ ./data/
RUN pip install --no-cache-dir --no-deps .

# Switch to non-root user
USER appuser

# Healthcheck: verifies SQLite database responsiveness and WAL accessibility
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import os, sqlite3, sys; p = os.getenv('FX_DB_PATH', '/app/data/usdjpy_signals.db'); sys.exit(0 if not os.path.exists(p) else (0 if sqlite3.connect(p).execute('PRAGMA schema_version;').fetchone() is not None else 1))" || exit 1

# Signal for graceful container shutdown
STOPSIGNAL SIGTERM

# Default command: run scheduler with volume database path
CMD ["fx-schedule", "--db-path", "/app/data/usdjpy_signals.db"]
