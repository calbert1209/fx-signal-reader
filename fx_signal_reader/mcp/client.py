"""
Multi-Server MCP Client for the USD/JPY Signal Reader (Task P3-02).

Loads workspace-local MCP server definitions from `mcp_servers.json` (with
`${VAR}` environment variable expansion), connects to one or all configured
servers concurrently/simultaneously over stdio via `contextlib.AsyncExitStack`,
discovers and namespaces tools (`<server>:<tool>`), and routes tool calls
with graceful isolation if an individual server fails to start.
"""

from __future__ import annotations

import argparse
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "mcp_servers.json"
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"
GLOBAL_MCP_CONFIG_PATH = Path.home() / ".gemini" / "config" / "mcp_config.json"
NAMESPACED_SEPARATOR = ":"

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")
_SENSITIVE_KEY_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")

logger = logging.getLogger(__name__)


class MCPConfigError(ValueError):
    """Raised when `mcp_servers.json` or its expanded configuration is invalid."""


class MCPServerNotConnectedError(RuntimeError):
    """Raised when attempting to interact with an unconnected MCP server."""


class MCPToolExecutionError(RuntimeError):
    """Raised when an MCP tool returns `is_error=True`."""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        message: str,
        *,
        result: CallToolResult | None = None,
    ) -> None:
        super().__init__(f"[{server_name}:{tool_name}] {message}")
        self.server_name = server_name
        self.tool_name = tool_name
        self.message = message
        self.result = result


@dataclass(frozen=True)
class MCPServerSpec:
    """Resolved stdio launch specification for a configured MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True

    def to_stdio_parameters(self) -> StdioServerParameters:
        """Convert this spec into `mcp.StdioServerParameters`."""
        return StdioServerParameters(
            command=self.command,
            args=list(self.args),
            env=dict(self.env) if self.env else None,
            cwd=self.cwd,
        )

    def to_safe_dict(self) -> dict[str, Any]:
        """Return a redacted representation safe for logs and CLI output."""
        redacted_env = {
            k: ("***" if _is_sensitive_key(k) and v else v)
            for k, v in self.env.items()
        }
        return {
            "name": self.name,
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "env": redacted_env,
            "enabled": self.enabled,
        }


@dataclass
class ServerConnectionInfo:
    """Runtime connection state and metadata for a configured MCP server."""

    name: str
    connected: bool
    server_name: str | None = None
    server_version: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "connected": self.connected,
            "server_name": self.server_name,
            "server_version": self.server_version,
            "error": self.error,
        }


def _is_sensitive_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in _SENSITIVE_KEY_MARKERS)


def _format_exception(exc: BaseException) -> str:
    """Format single exceptions or ExceptionGroups concisely."""
    if isinstance(exc, BaseExceptionGroup):
        parts = [_format_exception(sub) for sub in exc.exceptions]
        return f"{type(exc).__name__}({'; '.join(parts)})"
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def load_dotenv_vars(dotenv_path: str | Path = DEFAULT_DOTENV_PATH) -> dict[str, str]:
    """Parse a `.env` file into a dictionary without mutating `os.environ`."""
    path = Path(dotenv_path)
    if not path.is_file():
        return {}

    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
            if "=" not in line:
                continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        parsed[key] = value
    return parsed


def read_global_mcp_env_fallback(
    server_name: str,
    global_config_path: str | Path = GLOBAL_MCP_CONFIG_PATH,
) -> dict[str, str]:
    """
    Read non-empty fallback environment variables for `server_name` from the
    user's global MCP config if available on disk (read-only runtime fallback).
    """
    path = Path(global_config_path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return {}
    entry = servers.get(server_name)
    if not isinstance(entry, dict):
        return {}
    raw_env = entry.get("env")
    if not isinstance(raw_env, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in raw_env.items()
        if isinstance(k, str) and v is not None and str(v) != ""
    }


def expand_env_vars(
    value: str,
    env: Mapping[str, str] | None = None,
    *,
    strict: bool = False,
    default: str = "",
) -> str:
    """
    Expand `${VAR}` and `${VAR:-fallback}` placeholders in `value`.

    When `env` is `None`, reads from `os.environ`. If `strict=True`, raises
    `MCPConfigError` when a `${VAR}` placeholder without a `:-fallback` is
    missing from `env`.
    """
    lookup = os.environ if env is None else env

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        fallback = match.group(2)
        resolved = lookup.get(var_name)
        if resolved is not None and (resolved != "" or fallback is None):
            return str(resolved)
        if fallback is not None:
            return fallback
        if strict:
            raise MCPConfigError(
                f"Environment variable '{var_name}' is required for MCP config expansion."
            )
        return default

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _build_lookup_env(
    server_name: str,
    *,
    explicit_env: Mapping[str, str] | None,
    use_dotenv_fallback: bool,
    use_global_mcp_fallback: bool,
) -> dict[str, str]:
    """Build the environment variable mapping used to expand a server's config."""
    if explicit_env is not None:
        return {str(k): str(v) for k, v in explicit_env.items()}

    merged: dict[str, str] = {}
    if use_global_mcp_fallback:
        merged.update(read_global_mcp_env_fallback(server_name))
    if use_dotenv_fallback:
        for k, v in load_dotenv_vars(DEFAULT_DOTENV_PATH).items():
            if v != "" or k not in merged:
                merged[k] = v
    for k, v in os.environ.items():
        if v != "" or k not in merged:
            merged[k] = v
    return merged


def _build_server_spec(
    name: str,
    raw_spec: Mapping[str, Any],
    *,
    lookup_env: Mapping[str, str],
    strict_env: bool,
) -> MCPServerSpec:
    """Validate and expand a single MCP server configuration entry."""
    if not isinstance(raw_spec, Mapping):
        raise MCPConfigError(f"Server entry '{name}' must be a JSON object.")

    command = raw_spec.get("command")
    if not isinstance(command, str) or not command.strip():
        raise MCPConfigError(f"Server '{name}' is missing a valid string 'command'.")
    expanded_command = expand_env_vars(command, lookup_env, strict=strict_env)

    raw_args = raw_spec.get("args", [])
    if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
        raise MCPConfigError(f"Server '{name}' field 'args' must be a list of strings.")
    expanded_args = [
        expand_env_vars(arg, lookup_env, strict=strict_env) for arg in raw_args
    ]

    raw_cwd = raw_spec.get("cwd")
    if raw_cwd is not None and not isinstance(raw_cwd, str):
        raise MCPConfigError(f"Server '{name}' field 'cwd' must be a string or null.")
    expanded_cwd = (
        expand_env_vars(raw_cwd, lookup_env, strict=strict_env)
        if raw_cwd is not None
        else None
    )

    raw_env = raw_spec.get("env", {})
    if not isinstance(raw_env, Mapping):
        raise MCPConfigError(f"Server '{name}' field 'env' must be an object.")
    expanded_env = {
        str(k): expand_env_vars(str(v), lookup_env, strict=strict_env)
        for k, v in raw_env.items()
    }

    enabled = bool(raw_spec.get("enabled", True))
    return MCPServerSpec(
        name=name,
        command=expanded_command,
        args=expanded_args,
        env=expanded_env,
        cwd=expanded_cwd,
        enabled=enabled,
    )


def load_server_specs(
    config_path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    include_disabled: bool = False,
    strict_env: bool = False,
    use_dotenv_fallback: bool = True,
    use_global_mcp_fallback: bool = True,
) -> dict[str, MCPServerSpec]:
    """
    Load and expand MCP server specifications from `mcp_servers.json`.

    Args:
        config_path: Path to JSON config (defaults to workspace `mcp_servers.json`).
        env: Optional explicit mapping for `${VAR}` expansion. When provided,
            `.env` and global MCP config fallbacks are bypassed for deterministic
            resolution.
        include_disabled: Whether to include servers with `"enabled": false`.
        strict_env: Raise `MCPConfigError` if any `${VAR}` without `:-default`
            is unset.
        use_dotenv_fallback: Fall back to workspace `.env` when `env` is `None`.
        use_global_mcp_fallback: Fall back to `~/.gemini/config/mcp_config.json`
            at runtime when `env` is `None`.

    Returns:
        Mapping of server name to expanded `MCPServerSpec`.
    """
    resolved_path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if not resolved_path.is_file():
        raise MCPConfigError(f"MCP config file not found: {resolved_path}")

    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConfigError(f"Invalid JSON in MCP config '{resolved_path}': {exc}") from exc

    if not isinstance(payload, dict):
        raise MCPConfigError(f"MCP config '{resolved_path}' must contain a JSON object.")

    raw_servers = payload.get("mcpServers")
    if raw_servers is None:
        raise MCPConfigError(
            f"MCP config '{resolved_path}' must define a top-level 'mcpServers' object."
        )
    if not isinstance(raw_servers, dict):
        raise MCPConfigError(f"'mcpServers' in '{resolved_path}' must be an object.")

    specs: dict[str, MCPServerSpec] = {}
    for server_name, raw_spec in raw_servers.items():
        lookup_env = _build_lookup_env(
            server_name,
            explicit_env=env,
            use_dotenv_fallback=use_dotenv_fallback,
            use_global_mcp_fallback=use_global_mcp_fallback,
        )
        spec = _build_server_spec(
            server_name,
            raw_spec,
            lookup_env=lookup_env,
            strict_env=strict_env,
        )
        if spec.enabled or include_disabled:
            specs[server_name] = spec
    return specs


def parse_namespaced_tool(namespaced_name: str) -> tuple[str, str]:
    """Parse `'server:tool'` into `(server_name, tool_name)`."""
    if not isinstance(namespaced_name, str) or NAMESPACED_SEPARATOR not in namespaced_name:
        raise ValueError(
            f"Expected namespaced tool 'server{NAMESPACED_SEPARATOR}tool', got {namespaced_name!r}"
        )
    server_name, tool_name = namespaced_name.split(NAMESPACED_SEPARATOR, 1)
    if not server_name.strip() or not tool_name.strip():
        raise ValueError(
            f"Invalid namespaced tool {namespaced_name!r}: server and tool must be non-empty."
        )
    return server_name.strip(), tool_name.strip()


def extract_tool_result(result: CallToolResult) -> Any:
    """
    Convert an MCP `CallToolResult` into a clean Python object (`dict`, `list`,
    `str`, scalar, or `None`).

    Unwraps `MCPServer`/`FastMCP` list/scalar wrappers (`{"result": value}`) when
    `structured_content` has only the `"result"` wrapper key, and parses JSON
    text blocks when `structured_content` is absent.
    """
    if result.structured_content is not None:
        sc = result.structured_content
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc

    if not result.content:
        return None

    parsed_blocks: list[Any] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                parsed_blocks.append(json.loads(text))
            except json.JSONDecodeError:
                parsed_blocks.append(text)
        elif hasattr(block, "model_dump"):
            parsed_blocks.append(block.model_dump())
        else:
            parsed_blocks.append(block)

    if len(parsed_blocks) == 1:
        return parsed_blocks[0]
    if all(isinstance(item, str) for item in parsed_blocks):
        return "\n".join(parsed_blocks)
    return parsed_blocks


def _extract_error_message(result: CallToolResult) -> str:
    """Extract human-readable error text from a `CallToolResult`."""
    texts = [
        getattr(block, "text", "")
        for block in (result.content or [])
        if getattr(block, "text", None)
    ]
    if texts:
        return "\n".join(texts)
    if result.structured_content is not None:
        return json.dumps(result.structured_content)
    return "Tool returned is_error=True with no error message."


class MultiServerMCPClient:
    """
    Async context manager managing simultaneous stdio MCP sessions across
    multiple configured servers (`usdjpy-sqlite`, `nasdaq-data-link`, etc.).
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        server_specs: Mapping[str, MCPServerSpec | Mapping[str, Any]] | None = None,
        server_names: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        connect_timeout: float = 20.0,
        call_timeout: float | None = 30.0,
        errlog: Any = None,
        strict_env: bool = False,
        use_dotenv_fallback: bool = True,
        use_global_mcp_fallback: bool = True,
    ) -> None:
        self.config_path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.errlog = sys.stderr if errlog is None else errlog

        if server_specs is not None:
            resolved_specs: dict[str, MCPServerSpec] = {}
            for name, raw in server_specs.items():
                if isinstance(raw, MCPServerSpec):
                    resolved_specs[name] = raw
                else:
                    lookup_env = _build_lookup_env(
                        name,
                        explicit_env=env,
                        use_dotenv_fallback=use_dotenv_fallback,
                        use_global_mcp_fallback=use_global_mcp_fallback,
                    )
                    spec = _build_server_spec(
                        name,
                        raw,
                        lookup_env=lookup_env,
                        strict_env=strict_env,
                    )
                    if spec.enabled:
                        resolved_specs[name] = spec
        else:
            resolved_specs = load_server_specs(
                self.config_path,
                env=env,
                strict_env=strict_env,
                use_dotenv_fallback=use_dotenv_fallback,
                use_global_mcp_fallback=use_global_mcp_fallback,
            )

        if server_names is not None:
            requested = list(dict.fromkeys(server_names))
            unknown = [name for name in requested if name not in resolved_specs]
            if unknown:
                raise MCPConfigError(
                    f"Unknown MCP server(s) requested: {', '.join(unknown)}. "
                    f"Configured: {', '.join(resolved_specs.keys())}"
                )
            resolved_specs = {name: resolved_specs[name] for name in requested}

        self._specs: dict[str, MCPServerSpec] = resolved_specs
        self._exit_stack: AsyncExitStack | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._connections: dict[str, ServerConnectionInfo] = {}
        self._tools_by_server: dict[str, list[dict[str, Any]]] = {}
        self._namespaced_tools: dict[str, dict[str, Any]] = {}

    @property
    def server_specs(self) -> dict[str, MCPServerSpec]:
        """Configured server specs selected for this client."""
        return dict(self._specs)

    @property
    def connected_servers(self) -> list[str]:
        """Names of currently connected MCP servers."""
        return list(self._sessions.keys())

    @property
    def failed_servers(self) -> dict[str, str]:
        """Mapping of failed server names to their connection error messages."""
        return {
            name: info.error or "Unknown connection failure"
            for name, info in self._connections.items()
            if not info.connected
        }

    @property
    def connection_status(self) -> dict[str, ServerConnectionInfo]:
        """Connection status objects for every attempted server."""
        return dict(self._connections)

    @property
    def namespaced_tools(self) -> dict[str, dict[str, Any]]:
        """Unified namespaced tool registry keyed by `'<server>:<tool>'`."""
        return dict(self._namespaced_tools)

    def get_namespaced_tools(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the unified namespaced tool registry."""
        return dict(self._namespaced_tools)

    def list_namespaced_tool_names(self) -> list[str]:
        """Return sorted `'server:tool'` identifiers discovered across all active servers."""
        return sorted(self._namespaced_tools.keys())

    async def __aenter__(self) -> MultiServerMCPClient:
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()
        try:
            await self.connect()
        except BaseException:
            await self.aclose()
            raise
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose(exc_type, exc_val, exc_tb)

    async def aclose(
        self,
        exc_type: Any = None,
        exc_val: Any = None,
        exc_tb: Any = None,
    ) -> None:
        """Close all open MCP sessions and subprocess stdio transports."""
        self._sessions.clear()
        self._tools_by_server.clear()
        self._namespaced_tools.clear()
        stack = self._exit_stack
        self._exit_stack = None
        if stack is not None:
            await stack.__aexit__(exc_type, exc_val, exc_tb)

    async def connect(
        self,
        server_names: Sequence[str] | None = None,
    ) -> dict[str, ServerConnectionInfo]:
        """
        Connect to all configured (or specified) MCP servers over stdio.

        If an individual server fails to spawn or initialize, the failure is
        captured in `connection_status` / `failed_servers` without interrupting
        connections to healthy servers.
        """
        if self._exit_stack is None:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

        if server_names is None:
            targets = list(self._specs.keys())
        else:
            targets = list(dict.fromkeys(server_names))
            unknown = [name for name in targets if name not in self._specs]
            if unknown:
                raise MCPConfigError(f"Unknown MCP server(s): {', '.join(unknown)}")

        for name in targets:
            if name in self._sessions:
                continue
            await self._connect_single_server(self._specs[name])

        return dict(self._connections)

    async def _connect_single_server(self, spec: MCPServerSpec) -> None:
        """Spawn and initialize one stdio MCP server in its own isolated exit stack."""
        assert self._exit_stack is not None
        per_server_stack = AsyncExitStack()
        cancelled_exc = anyio.get_cancelled_exc_class()

        try:
            read_stream, write_stream = await per_server_stack.enter_async_context(
                stdio_client(spec.to_stdio_parameters(), errlog=self.errlog)
            )
            session = await per_server_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            with anyio.fail_after(self.connect_timeout):
                init_result = await session.initialize()

            self._exit_stack.push_async_exit(per_server_stack)
            self._sessions[spec.name] = session
            server_info = getattr(init_result, "server_info", None)
            self._connections[spec.name] = ServerConnectionInfo(
                name=spec.name,
                connected=True,
                server_name=getattr(server_info, "name", None),
                server_version=getattr(server_info, "version", None),
            )
        except cancelled_exc:
            await per_server_stack.aclose()
            raise
        except Exception as exc:
            err_msg = _format_exception(exc)
            logger.warning("Failed to connect to MCP server '%s': %s", spec.name, err_msg)
            await per_server_stack.aclose()
            self._connections[spec.name] = ServerConnectionInfo(
                name=spec.name,
                connected=False,
                error=err_msg,
            )

    def get_session(self, server_name: str) -> ClientSession:
        """Return the active `ClientSession` for `server_name` or raise."""
        session = self._sessions.get(server_name)
        if session is not None:
            return session
        info = self._connections.get(server_name)
        if info is not None and not info.connected:
            raise MCPServerNotConnectedError(
                f"MCP server '{server_name}' failed to connect: {info.error}"
            )
        raise MCPServerNotConnectedError(
            f"MCP server '{server_name}' is not connected. "
            f"Connected servers: {self.connected_servers}"
        )

    async def discover_tools(
        self,
        *,
        refresh: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Discover tools across all connected MCP servers.

        Returns:
            Mapping `{server_name: [tool_metadata_dict, ...]}` and updates the
            unified namespaced registry (`client.namespaced_tools` keyed by
            `'<server>:<tool>'`).
        """
        if not refresh and self._tools_by_server:
            return {k: list(v) for k, v in self._tools_by_server.items()}

        tools_by_server: dict[str, list[dict[str, Any]]] = {}
        namespaced: dict[str, dict[str, Any]] = {}
        cancelled_exc = anyio.get_cancelled_exc_class()

        for server_name, session in list(self._sessions.items()):
            try:
                if self.call_timeout is not None:
                    with anyio.fail_after(self.call_timeout):
                        list_result = await session.list_tools()
                else:
                    list_result = await session.list_tools()
            except cancelled_exc:
                raise
            except Exception as exc:
                err_msg = _format_exception(exc)
                logger.warning(
                    "Tool discovery failed for server '%s': %s", server_name, err_msg
                )
                info = self._connections.get(server_name)
                if info is not None:
                    info.error = err_msg
                tools_by_server[server_name] = []
                continue

            server_tools: list[dict[str, Any]] = []
            for tool in list_result.tools:
                ns_name = f"{server_name}{NAMESPACED_SEPARATOR}{tool.name}"
                entry: dict[str, Any] = {
                    "server": server_name,
                    "name": tool.name,
                    "namespaced_name": ns_name,
                    "title": tool.title,
                    "description": tool.description or "",
                    "input_schema": tool.input_schema,
                    "output_schema": tool.output_schema,
                    "annotations": (
                        tool.annotations.model_dump() if tool.annotations else None
                    ),
                }
                server_tools.append(entry)
                namespaced[ns_name] = entry

            tools_by_server[server_name] = server_tools

        self._tools_by_server = tools_by_server
        self._namespaced_tools = namespaced
        return {k: list(v) for k, v in tools_by_server.items()}

    async def discover_resources(self) -> dict[str, list[dict[str, Any]]]:
        """Discover MCP resources exposed by each connected server."""
        resources_by_server: dict[str, list[dict[str, Any]]] = {}
        cancelled_exc = anyio.get_cancelled_exc_class()

        for server_name, session in list(self._sessions.items()):
            try:
                if self.call_timeout is not None:
                    with anyio.fail_after(self.call_timeout):
                        res_list = await session.list_resources()
                else:
                    res_list = await session.list_resources()
                resources_by_server[server_name] = [
                    {
                        "server": server_name,
                        "uri": str(res.uri),
                        "name": res.name,
                        "description": res.description or "",
                        "mime_type": res.mime_type,
                    }
                    for res in res_list.resources
                ]
            except cancelled_exc:
                raise
            except Exception as exc:
                logger.debug(
                    "Resource discovery skipped/failed for '%s': %s",
                    server_name,
                    _format_exception(exc),
                )
                resources_by_server[server_name] = []
        return resources_by_server

    async def read_resource(self, server_name: str, uri: str) -> list[str]:
        """Read a resource URI from a connected MCP server and return text contents."""
        session = self.get_session(server_name)
        if self.call_timeout is not None:
            with anyio.fail_after(self.call_timeout):
                res = await session.read_resource(uri)
        else:
            res = await session.read_resource(uri)
        return [
            getattr(item, "text", str(item))
            for item in (res.contents or [])
        ]

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        raw: bool = False,
        raise_on_error: bool = True,
    ) -> Any:
        """
        Invoke `tool_name` on `server_name` over its active stdio `ClientSession`.

        Args:
            server_name: Configured server key (e.g. `'usdjpy-sqlite'`).
            tool_name: Unprefixed MCP tool name (e.g. `'get_latest_yield_spread'`).
            arguments: Optional JSON-serializable arguments dictionary.
            raw: When `True`, return the raw `mcp.types.CallToolResult`.
            raise_on_error: When `True` and `raw=False`, raise
                `MCPToolExecutionError` if the tool returns `is_error=True`.
        """
        session = self.get_session(server_name)
        args = {} if arguments is None else dict(arguments)

        if self.call_timeout is not None:
            with anyio.fail_after(self.call_timeout):
                result = await session.call_tool(tool_name, arguments=args)
        else:
            result = await session.call_tool(tool_name, arguments=args)

        if raw:
            return result
        if result.is_error and raise_on_error:
            raise MCPToolExecutionError(
                server_name,
                tool_name,
                _extract_error_message(result),
                result=result,
            )
        return extract_tool_result(result)

    async def call_namespaced_tool(
        self,
        namespaced_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        raw: bool = False,
        raise_on_error: bool = True,
    ) -> Any:
        """Invoke a tool using `'server:tool'` namespaced routing."""
        server_name, tool_name = parse_namespaced_tool(namespaced_name)
        return await self.call_tool(
            server_name,
            tool_name,
            arguments=arguments,
            raw=raw,
            raise_on_error=raise_on_error,
        )


def discover_tools_sync(
    config_path: str | Path | None = None,
    *,
    server_names: Sequence[str] | None = None,
    server_specs: Mapping[str, MCPServerSpec | Mapping[str, Any]] | None = None,
    env: Mapping[str, str] | None = None,
    connect_timeout: float = 20.0,
    call_timeout: float | None = 30.0,
    errlog: Any = None,
    include_metadata: bool = False,
) -> dict[str, Any]:
    """
    Synchronous convenience wrapper to connect to configured MCP servers and
    discover all exposed tools.

    Returns `tools_by_server` (`dict[str, list[dict]]`) by default, or a full
    snapshot dict (`tools_by_server`, `namespaced_tools`, `connections`) when
    `include_metadata=True`.
    """

    async def _run() -> dict[str, Any]:
        async with MultiServerMCPClient(
            config_path=config_path,
            server_names=server_names,
            server_specs=server_specs,
            env=env,
            connect_timeout=connect_timeout,
            call_timeout=call_timeout,
            errlog=errlog,
        ) as client:
            tools_by_server = await client.discover_tools()
            if not include_metadata:
                return tools_by_server
            return {
                "tools_by_server": tools_by_server,
                "namespaced_tools": client.get_namespaced_tools(),
                "connections": {
                    name: info.to_dict()
                    for name, info in client.connection_status.items()
                },
            }

    return anyio.run(_run)


def call_tool_sync(
    server_name: str,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    *,
    config_path: str | Path | None = None,
    server_specs: Mapping[str, MCPServerSpec | Mapping[str, Any]] | None = None,
    env: Mapping[str, str] | None = None,
    connect_timeout: float = 20.0,
    call_timeout: float | None = 30.0,
    errlog: Any = None,
    raw: bool = False,
    raise_on_error: bool = True,
) -> Any:
    """
    Synchronous convenience wrapper to invoke a single MCP tool (accepts either
    `(server_name, tool_name, arguments)` or `("server:tool", arguments=...)`).
    """
    if tool_name is None:
        resolved_server, resolved_tool = parse_namespaced_tool(server_name)
    else:
        resolved_server, resolved_tool = server_name, tool_name

    async def _run() -> Any:
        async with MultiServerMCPClient(
            config_path=config_path,
            server_names=[resolved_server],
            server_specs=server_specs,
            env=env,
            connect_timeout=connect_timeout,
            call_timeout=call_timeout,
            errlog=errlog,
        ) as client:
            return await client.call_tool(
                resolved_server,
                resolved_tool,
                arguments=arguments,
                raw=raw,
                raise_on_error=raise_on_error,
            )

    return anyio.run(_run)


def call_namespaced_tool_sync(
    namespaced_name: str,
    arguments: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Synchronous wrapper for `MultiServerMCPClient.call_namespaced_tool`."""
    server_name, tool_name = parse_namespaced_tool(namespaced_name)
    return call_tool_sync(server_name, tool_name, arguments=arguments, **kwargs)


async def run_smoke_test(
    config_path: str | Path | None = None,
    *,
    server_names: Sequence[str] | None = None,
    quiet: bool = True,
) -> dict[str, Any]:
    """
    Connect to configured MCP servers, verify tool discovery on all servers, and
    execute live read-only smoke calls across `usdjpy-sqlite` and `nasdaq-data-link`.
    """
    errlog = subprocess.DEVNULL if quiet else sys.stderr
    async with MultiServerMCPClient(
        config_path=config_path,
        server_names=server_names,
        errlog=errlog,
    ) as client:
        tools_by_server = await client.discover_tools()
        resources_by_server = await client.discover_resources()

        checks: dict[str, Any] = {}
        if "usdjpy-sqlite" in client.connected_servers:
            spread = await client.call_namespaced_tool(
                "usdjpy-sqlite:get_latest_yield_spread"
            )
            ticks = await client.call_namespaced_tool(
                "usdjpy-sqlite:get_market_ticks_summary",
                {"symbols": ["JPY=X", "^VIX"]},
            )
            events = await client.call_namespaced_tool(
                "usdjpy-sqlite:get_upcoming_macro_events",
                {"limit": 3},
            )
            alerts = await client.call_namespaced_tool(
                "usdjpy-sqlite:get_recent_mof_alerts",
                {"min_tier": 1, "limit": 3},
            )
            checks["usdjpy-sqlite"] = {
                "ok": True,
                "spread_10y": spread.get("spread_10y"),
                "spread_bps": spread.get("spread_bps"),
                "usdjpy_spot": spread.get("usdjpy_spot"),
                "ticks_symbol_count": ticks.get("symbol_count"),
                "upcoming_events_count": len(events),
                "mof_alerts_count": len(alerts),
            }

        if "nasdaq-data-link" in client.connected_servers:
            ndl_resource_ok = False
            ndl_resource_preview = ""
            try:
                res_texts = await client.read_resource(
                    "nasdaq-data-link", "nasdaq://databases"
                )
                ndl_resource_ok = bool(res_texts)
                ndl_resource_preview = (res_texts[0][:120] if res_texts else "").strip()
            except Exception as exc:
                ndl_resource_preview = _format_exception(exc)

            ndl_tool_raw = await client.call_namespaced_tool(
                "nasdaq-data-link:list_databases",
                {},
                raw=True,
            )
            ndl_tool_text = (
                ndl_tool_raw.content[0].text[:160]
                if ndl_tool_raw.content and getattr(ndl_tool_raw.content[0], "text", None)
                else ""
            )
            checks["nasdaq-data-link"] = {
                "discovered_tools_count": len(
                    tools_by_server.get("nasdaq-data-link", [])
                ),
                "resource_nasdaq_databases_ok": ndl_resource_ok,
                "resource_preview": ndl_resource_preview,
                "list_databases_roundtrip": True,
                "list_databases_is_error": bool(ndl_tool_raw.is_error),
                "list_databases_preview": ndl_tool_text,
            }

        return {
            "connections": {
                name: info.to_dict()
                for name, info in client.connection_status.items()
            },
            "tools_per_server": {
                name: [tool["name"] for tool in tools]
                for name, tools in tools_by_server.items()
            },
            "namespaced_tools": client.list_namespaced_tool_names(),
            "resources_per_server": {
                name: [r["uri"] for r in res]
                for name, res in resources_by_server.items()
            },
            "checks": checks,
        }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for inspecting tools, running smoke tests, or calling a tool."""
    parser = argparse.ArgumentParser(
        description="Multi-server MCP client for the USD/JPY Signal Reader."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to mcp_servers.json (defaults to workspace mcp_servers.json).",
    )
    parser.add_argument(
        "--server",
        action="append",
        dest="servers",
        default=None,
        help="Restrict connection to specified server name (repeatable).",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Connect to configured MCP servers and print discovered tools as JSON.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run end-to-end discovery and read-only tool checks across servers.",
    )
    parser.add_argument(
        "--call",
        type=str,
        default=None,
        help="Invoke a namespaced tool (e.g. 'usdjpy-sqlite:get_latest_yield_spread').",
    )
    parser.add_argument(
        "--args",
        type=str,
        default="{}",
        help="JSON object of arguments for --call (default: '{}').",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show MCP server stderr logs instead of suppressing them.",
    )

    parsed = parser.parse_args(argv)
    errlog = sys.stderr if parsed.verbose else subprocess.DEVNULL

    if parsed.smoke_test:
        report = anyio.run(
            lambda: run_smoke_test(
                parsed.config,
                server_names=parsed.servers,
                quiet=not parsed.verbose,
            )
        )
        print(json.dumps(report, indent=2))
        all_connected = all(
            info["connected"] for info in report["connections"].values()
        )
        return 0 if all_connected else 1

    if parsed.call:
        try:
            call_args = json.loads(parsed.args)
        except json.JSONDecodeError as exc:
            print(f"Invalid --args JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(call_args, dict):
            print("--args must decode to a JSON object.", file=sys.stderr)
            return 2
        output = call_namespaced_tool_sync(
            parsed.call,
            arguments=call_args,
            config_path=parsed.config,
            errlog=errlog,
        )
        print(json.dumps(output, indent=2, default=str))
        return 0

    # Default or --list-tools
    snapshot = discover_tools_sync(
        config_path=parsed.config,
        server_names=parsed.servers,
        errlog=errlog,
        include_metadata=True,
    )
    summary = {
        "connections": snapshot["connections"],
        "namespaced_tools": sorted(snapshot["namespaced_tools"].keys()),
        "tools_by_server": snapshot["tools_by_server"],
    }
    print(json.dumps(summary, indent=2))
    return 0 if any(c["connected"] for c in summary["connections"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
