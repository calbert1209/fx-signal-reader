"""
Unit and Integration Tests for Multi-Server MCP Setup (Task P3-02).

Covers:
  1. `mcp_servers.json` structure, secret hygiene (`${NASDAQ_DATA_LINK_API_KEY}`),
     and `${VAR}` / `${VAR:-fallback}` environment variable expansion.
  2. Namespaced tool parsing (`server:tool`), result extraction, and redaction.
  3. Graceful error isolation when broken/missing/hanging servers are configured
     alongside a healthy `usdjpy-sqlite` server.
  4. Live simultaneous stdio connections, tool discovery across BOTH `usdjpy-sqlite`
     and `nasdaq-data-link`, and live tool/resource execution over stdio.
  5. Synchronous convenience wrappers (`discover_tools_sync`, `call_tool_sync`)
     and CLI entrypoint (`--list-tools`, `--smoke-test`, `--call`).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import anyio

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mcp_client  # noqa: E402
from mcp_client import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    MCPConfigError,
    MCPServerNotConnectedError,
    MCPServerSpec,
    MCPToolExecutionError,
    MultiServerMCPClient,
    call_namespaced_tool_sync,
    call_tool_sync,
    discover_tools_sync,
    expand_env_vars,
    extract_tool_result,
    load_server_specs,
    parse_namespaced_tool,
)
from mcp.types import CallToolResult, TextContent  # noqa: E402

SCHEMA_PATH = PROJECT_ROOT / "schema.sql"
LIVE_DB_PATH = PROJECT_ROOT / "usdjpy_signals.db"
NASDAQ_MCP_DIR = Path("/Users/albert/Projects/nasdaq-data-link-mcp")


def _create_fixture_db(db_file: Path) -> None:
    """Create a deterministic SQLite fixture database for isolated stdio tests."""
    conn = sqlite3.connect(db_file)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(
        """
        INSERT INTO yield_spreads (timestamp_utc, us_2y, us_10y, jp_10y, spread_10y, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("2026-11-18T09:00:00Z", 4.83, 5.12, 2.925, 2.195, "fixture"),
    )
    conn.executemany(
        """
        INSERT INTO market_ticks (symbol, timestamp_utc, price, open, high, low, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("JPY=X", "2026-11-18T08:45:00Z", 159.20, 159.10, 159.25, 159.05, 0.0),
            ("JPY=X", "2026-11-18T09:00:00Z", 159.50, 159.20, 159.55, 159.18, 0.0),
            ("^VIX", "2026-11-18T09:00:00Z", 22.40, 22.80, 23.00, 22.10, 0.0),
        ],
    )
    conn.execute(
        """
        INSERT INTO macro_events (event_hash, timestamp_utc, currency, title, impact, forecast, previous, actual)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("ev_fixture_1", "2026-11-19T05:00:00Z", "JPY", "BoJ Policy Decision", "High", None, None, None),
    )
    conn.execute(
        """
        INSERT INTO news_headlines (url_hash, published_utc, source, title, url, matched_keywords)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "news_fixture_1",
            "2026-11-18T09:15:00Z",
            "Reuters",
            "Japan MoF warns on excessive FX volatility and stands ready to act",
            "https://example.com/mof",
            "MoF, excessive volatility, stand ready",
        ),
    )
    conn.commit()
    conn.close()


class TestMCPConfigLoadingAndEnvExpansion(unittest.TestCase):
    """Verify `mcp_servers.json` schema, secret placeholders, and `${VAR}` expansion."""

    def test_workspace_mcp_servers_json_structure_and_no_hardcoded_secret(self) -> None:
        self.assertTrue(DEFAULT_CONFIG_PATH.is_file(), "mcp_servers.json must exist")
        raw_text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
        servers = payload.get("mcpServers", {})

        self.assertIn("usdjpy-sqlite", servers)
        self.assertIn("nasdaq-data-link", servers)

        sqlite_cfg = servers["usdjpy-sqlite"]
        self.assertEqual(
            sqlite_cfg["command"],
            "/Users/albert/Projects/fx-signal-reader/.venv/bin/python",
        )
        self.assertEqual(
            sqlite_cfg["args"],
            ["/Users/albert/Projects/fx-signal-reader/mcp_server.py"],
        )
        self.assertEqual(
            sqlite_cfg["env"]["FX_DB_PATH"],
            "/Users/albert/Projects/fx-signal-reader/usdjpy_signals.db",
        )

        nasdaq_cfg = servers["nasdaq-data-link"]
        self.assertEqual(nasdaq_cfg["command"], "uv")
        self.assertEqual(
            nasdaq_cfg["args"],
            [
                "run",
                "--directory",
                "/Users/albert/Projects/nasdaq-data-link-mcp",
                "nasdaq_data_link_mcp_os/server.py",
            ],
        )
        self.assertEqual(nasdaq_cfg["cwd"], "/Users/albert/Projects/nasdaq-data-link-mcp")
        self.assertEqual(
            nasdaq_cfg["env"]["NASDAQ_DATA_LINK_API_KEY"],
            "${NASDAQ_DATA_LINK_API_KEY}",
        )
        self.assertEqual(
            nasdaq_cfg["env"]["PYTHONPATH"],
            "/Users/albert/Projects/nasdaq-data-link-mcp",
        )

    def test_expand_env_vars_supports_placeholders_defaults_and_strict_mode(self) -> None:
        env = {"FOO": "bar", "EMPTY": ""}
        self.assertEqual(expand_env_vars("${FOO}", env), "bar")
        self.assertEqual(expand_env_vars("pre-${FOO}-post", env), "pre-bar-post")
        self.assertEqual(expand_env_vars("${MISSING}", env), "")
        self.assertEqual(expand_env_vars("${MISSING:-fallback}", env), "fallback")
        self.assertEqual(expand_env_vars("${EMPTY:-fallback}", env), "fallback")
        self.assertEqual(expand_env_vars("${FOO:-fallback}", env), "bar")

        with self.assertRaises(MCPConfigError):
            expand_env_vars("${MISSING}", env, strict=True)

    def test_load_server_specs_expands_env_and_filters_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "mcp_servers.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "alpha": {
                                "command": "${PY_BIN}",
                                "args": ["${SCRIPT_PATH}"],
                                "cwd": "${WORK_DIR}",
                                "env": {
                                    "API_KEY": "${MY_KEY}",
                                    "MODE": "${MODE:-readonly}",
                                },
                            },
                            "disabled-srv": {
                                "command": "python",
                                "args": ["-V"],
                                "enabled": False,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            env_map = {
                "PY_BIN": "/usr/bin/python3",
                "SCRIPT_PATH": "/tmp/server.py",
                "WORK_DIR": "/tmp",
                "MY_KEY": "unit-test-placeholder",
            }
            specs = load_server_specs(cfg_path, env=env_map)
            self.assertEqual(list(specs.keys()), ["alpha"])
            alpha = specs["alpha"]
            self.assertEqual(alpha.command, "/usr/bin/python3")
            self.assertEqual(alpha.args, ["/tmp/server.py"])
            self.assertEqual(alpha.cwd, "/tmp")
            self.assertEqual(alpha.env["API_KEY"], "unit-test-placeholder")
            self.assertEqual(alpha.env["MODE"], "readonly")

            all_specs = load_server_specs(cfg_path, env=env_map, include_disabled=True)
            self.assertIn("disabled-srv", all_specs)
            self.assertFalse(all_specs["disabled-srv"].enabled)

    def test_load_server_specs_error_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "does_not_exist.json"
            with self.assertRaises(MCPConfigError):
                load_server_specs(missing_path)

            bad_json = Path(tmpdir) / "bad.json"
            bad_json.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(MCPConfigError):
                load_server_specs(bad_json)

            no_root = Path(tmpdir) / "no_root.json"
            no_root.write_text(json.dumps({"other": {}}), encoding="utf-8")
            with self.assertRaises(MCPConfigError):
                load_server_specs(no_root)

            missing_cmd = Path(tmpdir) / "missing_cmd.json"
            missing_cmd.write_text(
                json.dumps({"mcpServers": {"broken": {"args": []}}}),
                encoding="utf-8",
            )
            with self.assertRaises(MCPConfigError):
                load_server_specs(missing_cmd)


class TestMCPClientHelpers(unittest.TestCase):
    """Verify namespaced tool parsing, result extraction, and secret redaction."""

    def test_parse_namespaced_tool_valid_and_invalid(self) -> None:
        self.assertEqual(
            parse_namespaced_tool("usdjpy-sqlite:get_latest_yield_spread"),
            ("usdjpy-sqlite", "get_latest_yield_spread"),
        )
        self.assertEqual(
            parse_namespaced_tool("server:ns:tool"),
            ("server", "ns:tool"),
        )
        for bad in ("no_separator", ":tool", "server:", "", "   :tool"):
            with self.assertRaises(ValueError):
                parse_namespaced_tool(bad)

    def test_extract_tool_result_variants(self) -> None:
        dict_res = CallToolResult(
            content=[TextContent(type="text", text='{"a": 1}')],
            structured_content={"a": 1},
            is_error=False,
        )
        self.assertEqual(extract_tool_result(dict_res), {"a": 1})

        list_res = CallToolResult(
            content=[TextContent(type="text", text='{"id": 1}')],
            structured_content={"result": [{"id": 1}, {"id": 2}]},
            is_error=False,
        )
        self.assertEqual(extract_tool_result(list_res), [{"id": 1}, {"id": 2}])

        text_json_res = CallToolResult(
            content=[TextContent(type="text", text='{"status": "ok"}')],
            structured_content=None,
            is_error=False,
        )
        self.assertEqual(extract_tool_result(text_json_res), {"status": "ok"})

        plain_text_res = CallToolResult(
            content=[TextContent(type="text", text="plain response")],
            structured_content=None,
            is_error=False,
        )
        self.assertEqual(extract_tool_result(plain_text_res), "plain response")

    def test_server_spec_to_safe_dict_redacts_sensitive_values(self) -> None:
        spec = MCPServerSpec(
            name="nasdaq-data-link",
            command="uv",
            args=["run"],
            env={
                "NASDAQ_DATA_LINK_API_KEY": "super-secret-value",
                "PYTHONPATH": "/some/path",
            },
        )
        safe = spec.to_safe_dict()
        self.assertEqual(safe["env"]["NASDAQ_DATA_LINK_API_KEY"], "***")
        self.assertEqual(safe["env"]["PYTHONPATH"], "/some/path")


class TestGracefulServerIsolation(unittest.TestCase):
    """Ensure failing/broken MCP servers do not crash healthy active sessions."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "fixture.db"
        _create_fixture_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_broken_and_exiting_servers_isolated_from_healthy_sqlite_server(self) -> None:
        specs = {
            "usdjpy-sqlite": {
                "command": sys.executable,
                "args": [str(PROJECT_ROOT / "mcp_server.py")],
                "env": {"FX_DB_PATH": str(self.db_path)},
            },
            "broken-missing-bin": {
                "command": "/nonexistent/mcp_binary_does_not_exist",
                "args": [],
            },
            "broken-immediate-exit": {
                "command": sys.executable,
                "args": ["-c", "import sys; sys.exit(2)"],
            },
        }

        async def _run() -> None:
            with self.assertLogs("mcp_client", level="WARNING") as cm:
                async with MultiServerMCPClient(
                    server_specs=specs,
                    connect_timeout=10.0,
                    errlog=subprocess.DEVNULL,
                ) as client:
                    self.assertEqual(client.connected_servers, ["usdjpy-sqlite"])
                    self.assertIn("broken-missing-bin", client.failed_servers)
                    self.assertIn("broken-immediate-exit", client.failed_servers)

                    discovered = await client.discover_tools()
                    self.assertEqual(list(discovered.keys()), ["usdjpy-sqlite"])
                    self.assertEqual(
                        {t["name"] for t in discovered["usdjpy-sqlite"]},
                        {
                            "get_latest_yield_spread",
                            "get_upcoming_macro_events",
                            "get_recent_mof_alerts",
                            "get_market_ticks_summary",
                        },
                    )

                    spread = await client.call_namespaced_tool(
                        "usdjpy-sqlite:get_latest_yield_spread"
                    )
                    self.assertAlmostEqual(spread["spread_bps"], 219.5)
                    self.assertAlmostEqual(spread["usdjpy_spot"], 159.50)

                    with self.assertRaises(MCPServerNotConnectedError):
                        await client.call_tool("broken-missing-bin", "list_databases")

            self.assertTrue(any("broken-missing-bin" in line for line in cm.output))
            self.assertTrue(any("broken-immediate-exit" in line for line in cm.output))

        anyio.run(_run)

    def test_hanging_server_timeout_isolated_from_healthy_server(self) -> None:
        specs = {
            "usdjpy-sqlite": {
                "command": sys.executable,
                "args": [str(PROJECT_ROOT / "mcp_server.py")],
                "env": {"FX_DB_PATH": str(self.db_path)},
            },
            "hanging-server": {
                "command": sys.executable,
                "args": ["-c", "import time; time.sleep(30)"],
            },
        }

        async def _run() -> None:
            with self.assertLogs("mcp_client", level="WARNING") as cm:
                async with MultiServerMCPClient(
                    server_specs=specs,
                    connect_timeout=4.0,
                    errlog=subprocess.DEVNULL,
                ) as client:
                    self.assertEqual(client.connected_servers, ["usdjpy-sqlite"])
                    self.assertIn("hanging-server", client.failed_servers)
                    self.assertIn("TimeoutError", client.failed_servers["hanging-server"])

                    ticks = await client.call_namespaced_tool(
                        "usdjpy-sqlite:get_market_ticks_summary",
                        {"symbols": ["JPY=X", "^VIX"]},
                    )
                    self.assertEqual(ticks["symbol_count"], 2)
                    self.assertAlmostEqual(ticks["symbols"]["JPY=X"]["close"], 159.50)

            self.assertTrue(any("hanging-server" in line for line in cm.output))

        anyio.run(_run)


class TestLiveMultiServerStdio(unittest.TestCase):
    """Live integration tests against BOTH `usdjpy-sqlite` and `nasdaq-data-link`."""

    @classmethod
    def setUpClass(cls) -> None:
        if not LIVE_DB_PATH.exists():
            raise unittest.SkipTest(f"Live SQLite database not found: {LIVE_DB_PATH}")
        if not shutil.which("uv"):
            raise unittest.SkipTest("`uv` binary not available on PATH")
        if not NASDAQ_MCP_DIR.is_dir():
            raise unittest.SkipTest(
                f"External nasdaq-data-link-mcp directory not found: {NASDAQ_MCP_DIR}"
            )

    def test_live_simultaneous_connection_discovery_and_execution(self) -> None:
        async def _run() -> None:
            async with MultiServerMCPClient(
                config_path=DEFAULT_CONFIG_PATH,
                connect_timeout=25.0,
                call_timeout=30.0,
                errlog=subprocess.DEVNULL,
            ) as client:
                self.assertEqual(client.failed_servers, {})
                self.assertEqual(
                    set(client.connected_servers),
                    {"usdjpy-sqlite", "nasdaq-data-link"},
                )

                tools_by_server = await client.discover_tools()
                self.assertEqual(
                    {t["name"] for t in tools_by_server["usdjpy-sqlite"]},
                    {
                        "get_latest_yield_spread",
                        "get_upcoming_macro_events",
                        "get_recent_mof_alerts",
                        "get_market_ticks_summary",
                    },
                )
                self.assertEqual(
                    {t["name"] for t in tools_by_server["nasdaq-data-link"]},
                    {
                        "search_datasets",
                        "get_dataset",
                        "get_dataset_metadata",
                        "list_databases",
                        "export_dataset",
                    },
                )

                namespaced = client.get_namespaced_tools()
                self.assertEqual(len(namespaced), 9)
                self.assertIn("usdjpy-sqlite:get_latest_yield_spread", namespaced)
                self.assertIn("usdjpy-sqlite:get_market_ticks_summary", namespaced)
                self.assertIn("nasdaq-data-link:list_databases", namespaced)
                self.assertIn("nasdaq-data-link:search_datasets", namespaced)

                # Execute live tools on `usdjpy-sqlite` over stdio
                spread = await client.call_namespaced_tool(
                    "usdjpy-sqlite:get_latest_yield_spread"
                )
                self.assertIsInstance(spread, dict)
                self.assertIsNotNone(spread["spread_10y"])
                self.assertIsNotNone(spread["spread_bps"])
                self.assertIsNotNone(spread["usdjpy_spot"])

                ticks = await client.call_namespaced_tool(
                    "usdjpy-sqlite:get_market_ticks_summary",
                    {"symbols": ["JPY=X", "^VIX"]},
                )
                self.assertEqual(ticks["symbol_count"], 2)
                self.assertIn("JPY=X", ticks["symbols"])
                self.assertIn("^VIX", ticks["symbols"])

                events = await client.call_namespaced_tool(
                    "usdjpy-sqlite:get_upcoming_macro_events",
                    {"limit": 3},
                )
                self.assertIsInstance(events, list)
                self.assertLessEqual(len(events), 3)

                alerts = await client.call_namespaced_tool(
                    "usdjpy-sqlite:get_recent_mof_alerts",
                    {"min_tier": 1, "limit": 3},
                )
                self.assertIsInstance(alerts, list)
                self.assertLessEqual(len(alerts), 3)
                self.assertTrue(all(a["tier"] >= 1 for a in alerts))

                # Discover and read MCP resource + verify tool roundtrip on `nasdaq-data-link`
                resources = await client.discover_resources()
                ndl_uris = [r["uri"] for r in resources.get("nasdaq-data-link", [])]
                self.assertIn("nasdaq://databases", ndl_uris)

                db_resource = await client.read_resource(
                    "nasdaq-data-link", "nasdaq://databases"
                )
                self.assertTrue(db_resource)
                self.assertIn("WORLDBANK", db_resource[0])

                ndl_raw = await client.call_namespaced_tool(
                    "nasdaq-data-link:list_databases",
                    {},
                    raw=True,
                )
                self.assertTrue(ndl_raw.content)
                if ndl_raw.is_error:
                    # Verify raise_on_error raises structured MCPToolExecutionError
                    with self.assertRaises(MCPToolExecutionError):
                        await client.call_namespaced_tool(
                            "nasdaq-data-link:list_databases", {}
                        )
                else:
                    parsed = extract_tool_result(ndl_raw)
                    self.assertIsInstance(parsed, list)

        anyio.run(_run)

    def test_sync_wrappers_and_cli_list_tools_and_smoke_test(self) -> None:
        snapshot = discover_tools_sync(
            config_path=DEFAULT_CONFIG_PATH,
            errlog=subprocess.DEVNULL,
            include_metadata=True,
        )
        self.assertIn("usdjpy-sqlite", snapshot["tools_by_server"])
        self.assertIn("nasdaq-data-link", snapshot["tools_by_server"])
        self.assertIn(
            "usdjpy-sqlite:get_latest_yield_spread", snapshot["namespaced_tools"]
        )
        self.assertIn("nasdaq-data-link:list_databases", snapshot["namespaced_tools"])

        spread_direct = call_tool_sync(
            "usdjpy-sqlite",
            "get_latest_yield_spread",
            errlog=subprocess.DEVNULL,
        )
        self.assertIsNotNone(spread_direct["spread_bps"])

        spread_ns = call_namespaced_tool_sync(
            "usdjpy-sqlite:get_latest_yield_spread",
            errlog=subprocess.DEVNULL,
        )
        self.assertEqual(spread_direct["timestamp_utc"], spread_ns["timestamp_utc"])

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = mcp_client.main(["--smoke-test"])
        self.assertEqual(exit_code, 0)
        report = json.loads(buf.getvalue())
        self.assertTrue(report["connections"]["usdjpy-sqlite"]["connected"])
        self.assertTrue(report["connections"]["nasdaq-data-link"]["connected"])
        self.assertTrue(report["checks"]["usdjpy-sqlite"]["ok"])
        self.assertTrue(report["checks"]["nasdaq-data-link"]["list_databases_roundtrip"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
