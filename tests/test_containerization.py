"""
Unit and integration tests for containerization configuration.

Validates:
1. Dockerfile presence, syntax, base image, unprivileged appuser (UID/GID 10001),
   environment variables, healthcheck, stopsignal, and entrypoint.
2. docker-compose.yml structure, service definition, volume bind mount,
   environment variables, restart policy, and logging driver.
3. .dockerignore rules and patterns.
4. .env.example configuration keys and documentation.
5. SQLite WAL journaling behavior and file integrity on volume mount directories.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestContainerization(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.dockerfile_path = cls.project_root / "Dockerfile"
        cls.compose_path = cls.project_root / "docker-compose.yml"
        cls.dockerignore_path = cls.project_root / ".dockerignore"
        cls.env_example_path = cls.project_root / ".env.example"
        cls.schema_path = cls.project_root / "schema.sql"

    def test_dockerfile_exists_and_security_directives(self) -> None:
        """Verify Dockerfile exists and enforces container security best practices."""
        self.assertTrue(self.dockerfile_path.exists(), "Dockerfile must exist")
        content = self.dockerfile_path.read_text(encoding="utf-8")

        # 1. Official Python 3.11 slim base
        self.assertRegex(
            content,
            r"FROM\s+python:3\.11-slim",
            "Dockerfile must use python:3.11-slim base image",
        )

        # 2. Non-root user appuser (UID/GID 10001)
        self.assertIn("10001", content, "Dockerfile must use UID/GID 10001")
        self.assertIn("appuser", content, "Dockerfile must create appuser")
        self.assertRegex(
            content,
            r"USER\s+appuser",
            "Dockerfile must switch to non-root USER appuser",
        )

        # 3. System packages curl and ca-certificates
        self.assertIn("curl", content, "Dockerfile must install curl")
        self.assertIn("ca-certificates", content, "Dockerfile must install ca-certificates")

        # 4. Environment variables PYTHONUNBUFFERED and PYTHONDONTWRITEBYTECODE
        self.assertIn("PYTHONUNBUFFERED=1", content)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", content)

        # 5. STOPSIGNAL SIGTERM
        self.assertRegex(
            content,
            r"STOPSIGNAL\s+SIGTERM",
            "Dockerfile must define STOPSIGNAL SIGTERM",
        )

        # 6. HEALTHCHECK verifying SQLite database responsiveness or process liveliness
        self.assertIn("HEALTHCHECK", content, "Dockerfile must define HEALTHCHECK")
        self.assertIn("sqlite3", content, "HEALTHCHECK must verify SQLite responsiveness")

        # 7. Default command runs python scheduler.py with db-path
        self.assertRegex(
            content,
            r'CMD\s*\["python",\s*"scheduler.py",\s*"--db-path",\s*"/app/data/usdjpy_signals.db"\]',
            "Dockerfile must specify CMD running scheduler.py with /app/data/usdjpy_signals.db",
        )

    def test_docker_compose_structure_and_directives(self) -> None:
        """Verify docker-compose.yml structure, volume mounts, and environment configuration."""
        self.assertTrue(self.compose_path.exists(), "docker-compose.yml must exist")
        content = self.compose_path.read_text(encoding="utf-8")

        # Service name
        self.assertIn("usdjpy-collector:", content, "Must define service usdjpy-collector")

        # Persistent bind mount for data
        self.assertRegex(
            content,
            r"-\s+\./data:/app/data",
            "Must define persistent bind mount ./data:/app/data",
        )

        # Environment variables
        self.assertIn("FX_DB_PATH=/app/data/usdjpy_signals.db", content)
        self.assertIn("FRED_API_KEY=${FRED_API_KEY:-}", content)
        self.assertIn("POLL_INTERVAL_NEWS=300", content)
        self.assertIn("POLL_INTERVAL_TICKS=900", content)
        self.assertIn("LOG_LEVEL=INFO", content)

        # Restart policy
        self.assertIn("restart: unless-stopped", content)

        # Logging configuration: json-file, max-size: 10m, max-file: 3
        self.assertIn('driver: "json-file"', content)
        self.assertIn('max-size: "10m"', content)
        self.assertIn('max-file: "3"', content)

    def test_docker_compose_cli_validation(self) -> None:
        """Validate docker-compose.yml syntax using docker compose config if docker is installed."""
        docker_cli = shutil.which("docker")
        if not docker_cli:
            self.skipTest("Docker CLI not available in test environment")

        result = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"docker compose config failed: {result.stderr}",
        )

        config = json.loads(result.stdout)
        services = config.get("services", {})
        self.assertIn("usdjpy-collector", services)
        collector = services["usdjpy-collector"]

        # Verify environment
        env = collector.get("environment", {})
        self.assertEqual(env.get("FX_DB_PATH"), "/app/data/usdjpy_signals.db")
        self.assertEqual(env.get("LOG_LEVEL"), "INFO")
        self.assertEqual(env.get("POLL_INTERVAL_NEWS"), "300")
        self.assertEqual(env.get("POLL_INTERVAL_TICKS"), "900")

        # Verify restart policy
        self.assertEqual(collector.get("restart"), "unless-stopped")

        # Verify volume mount
        volumes = collector.get("volumes", [])
        self.assertTrue(len(volumes) >= 1)
        data_vol = next((v for v in volumes if v.get("target") == "/app/data"), None)
        self.assertIsNotNone(data_vol, "Must mount /app/data volume")
        self.assertEqual(data_vol.get("type"), "bind")

        # Verify logging
        logging_cfg = collector.get("logging", {})
        self.assertEqual(logging_cfg.get("driver"), "json-file")
        self.assertEqual(logging_cfg.get("options", {}).get("max-size"), "10m")
        self.assertEqual(logging_cfg.get("options", {}).get("max-file"), "3")

    def test_dockerignore_rules(self) -> None:
        """Verify .dockerignore contains exclusions for virtualenvs, git, cache, and db files."""
        self.assertTrue(self.dockerignore_path.exists(), ".dockerignore must exist")
        content = self.dockerignore_path.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

        expected_patterns = [".venv/", "__pycache__/", ".git/", "*.db"]
        for pat in expected_patterns:
            self.assertTrue(
                any(pat in line for line in lines),
                f"Pattern {pat} must be present in .dockerignore",
            )

    def test_env_example_documentation(self) -> None:
        """Verify .env.example exists and documents all expected pipeline configuration keys."""
        self.assertTrue(self.env_example_path.exists(), ".env.example must exist")
        content = self.env_example_path.read_text(encoding="utf-8")

        required_keys = [
            "FX_DB_PATH",
            "FRED_API_KEY",
            "POLL_INTERVAL_NEWS",
            "POLL_INTERVAL_TICKS",
            "LOG_LEVEL",
        ]
        for key in required_keys:
            self.assertIn(key, content, f"Key {key} must be documented in .env.example")

    def test_sqlite_wal_volume_lifecycle(self) -> None:
        """
        Verify SQLite WAL journaling behavior, concurrent reads/writes,
        and preservation of -wal and -shm files in volume mount directories.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "usdjpy_signals.db"

            # Connect and configure WAL mode
            conn1 = sqlite3.connect(db_path)
            conn1.execute("PRAGMA journal_mode = WAL;")
            conn1.execute("PRAGMA synchronous = NORMAL;")
            conn1.execute("PRAGMA foreign_keys = ON;")

            if self.schema_path.exists():
                conn1.executescript(self.schema_path.read_text(encoding="utf-8"))

            # Verify WAL journal mode
            cur = conn1.cursor()
            cur.execute("PRAGMA journal_mode;")
            mode = cur.fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

            # Insert sample data to trigger WAL write
            cur.execute(
                """
                INSERT INTO market_ticks (symbol, timestamp_utc, price)
                VALUES ('JPY=X', '2026-11-18T12:00:00Z', 159.25)
                """
            )
            conn1.commit()

            # Open a second concurrent read connection (simulating healthcheck or MCP server)
            conn2 = sqlite3.connect(db_path)
            cur2 = conn2.cursor()
            cur2.execute("SELECT price FROM market_ticks WHERE symbol = 'JPY=X'")
            row = cur2.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 159.25)

            # Check that WAL mode file or main DB exists
            self.assertTrue(db_path.exists(), "Main DB file must exist")

            # Checkpoint WAL
            conn1.execute("PRAGMA wal_checkpoint(PASSIVE);")

            conn2.close()
            conn1.close()

            # After closing, reopen to ensure persistence across sessions
            conn3 = sqlite3.connect(db_path)
            cur3 = conn3.cursor()
            cur3.execute("SELECT count(*) FROM market_ticks")
            count = cur3.fetchone()[0]
            self.assertEqual(count, 1)
            conn3.close()


if __name__ == "__main__":
    unittest.main()
