"""
MCP (Model Context Protocol) manager for Cyrene.

Manages MCP server connections and tool lifecycle. Follows the
searxng_manager.py pattern for subprocess management (stdio transport)
and settings_store.py pattern for configuration persistence.

Supports three transport modes:
  - "stdio": spawn a subprocess and communicate over stdin/stdout
  - "sse": connect to a remote HTTP endpoint using Server-Sent Events
  - "streamable_http": connect to a modern MCP Streamable HTTP endpoint
"""

import asyncio
import json
import logging
import os
import pathlib
import shutil
import urllib.parse
from typing import Any

from cyrene.config import DATA_DIR
from cyrene.runtime.version import get_version

# Environment variables that are safe to pass to MCP subprocesses.
# Secrets (API keys, tokens, passwords) are intentionally excluded.
_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
    "TERM", "TMPDIR", "TEMP", "TMP", "TZ",
    "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
    "SYSTEMROOT", "WINDIR",  # Windows
})

# Executable names that must not be used as an MCP command.
_BLOCKED_EXECUTABLES = frozenset({
    "bash", "sh", "zsh", "fish", "dash", "ksh", "tcsh",
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    "python", "python3", "python.exe",  # avoid arbitrary script execution
    "node", "node.exe",
    "ruby", "perl",
    # env/xargs/script can be used to indirectly invoke blocked executables
    "env", "xargs", "script",
    "npx", "npx.exe", "uvx", "uvx.exe",
})

logger = logging.getLogger(__name__)

_MCP_SERVERS_FILE = DATA_DIR / "mcp_servers.json"
_DEFAULT_STARTUP_TIMEOUT_SECONDS = 120.0
_DEFAULT_TOOL_TIMEOUT_SECONDS = 120.0
_MIN_TIMEOUT_SECONDS = 1.0
_MAX_STARTUP_TIMEOUT_SECONDS = 300.0
_MAX_TOOL_TIMEOUT_SECONDS = 120.0
_DEFAULT_STDIO_STREAM_LIMIT_BYTES = 20 * 1024 * 1024
_MAX_STDIO_STREAM_LIMIT_BYTES = 64 * 1024 * 1024

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: "MCPManager | None" = None


def get_manager() -> "MCPManager":
    """Return the global MCPManager singleton (lazy init)."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager


async def start_mcp() -> None:
    """Start all enabled MCP servers via the global manager."""
    manager = get_manager()
    await manager.start()


async def restart_mcp() -> None:
    """Properly stop existing connections and start fresh from config.

    Use this from async contexts (e.g. FastAPI route handlers) instead of
    the synchronous stop_mcp() + start_mcp() combination, which would call
    asyncio.run() inside an already-running event loop and silently leave
    old subprocess connections as orphans.
    """
    await stop_mcp_async()
    await start_mcp()


async def stop_mcp_async() -> None:
    """Stop the singleton from a running application event loop."""
    global _manager
    if _manager is not None:
        try:
            await _manager.stop()
        except Exception:
            logger.exception("MCP manager stop failed")
        finally:
            _manager = None


def stop_mcp() -> None:
    """Synchronous wrapper — stops all MCP servers.

    Used in ``finally`` blocks outside the event loop (e.g. after
    ``asyncio.run()``), so we create a fresh loop to drive the async
    disconnect.
    """
    global _manager
    if _manager is not None:
        try:
            asyncio.run(_manager.stop())
        except Exception:
            logger.exception("MCP manager stop failed")
        finally:
            _manager = None


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

_DEFAULT_MCP_SERVERS: list[dict[str, Any]] = []


def get_mcp_servers() -> list[dict[str, Any]]:
    """Load MCP declarations from the encrypted portable settings store.

    Existing ``mcp_servers.json`` installations are imported once and the
    plaintext legacy file is removed after a successful encrypted write.
    """
    from cyrene.runtime.settings_store import get as get_setting, set_ as set_setting

    encrypted = get_setting("mcp_servers", None)
    if isinstance(encrypted, list):
        return encrypted
    if not _MCP_SERVERS_FILE.exists():
        return list(_DEFAULT_MCP_SERVERS)
    try:
        data = json.loads(_MCP_SERVERS_FILE.read_text(encoding="utf-8"))
        servers = data.get("servers", [])
        if not isinstance(servers, list):
            return list(_DEFAULT_MCP_SERVERS)
        set_setting("mcp_servers", servers)
        _MCP_SERVERS_FILE.unlink(missing_ok=True)
        return servers
    except Exception:
        logger.exception("Failed to load MCP server config")
        return list(_DEFAULT_MCP_SERVERS)


def save_mcp_servers(servers: list[dict[str, Any]]) -> None:
    """Validate and save MCP declarations into encrypted settings."""
    normalized = [dict(server) for server in servers]
    for server in normalized:
        if not isinstance(server, dict):
            raise ValueError("MCP server configuration must be an object")
        transport = str(server.get("transport") or "stdio")
        if transport not in {"stdio", "sse", "streamable_http", "streamable-http", "http"}:
            raise ValueError(f"Unsupported MCP transport: {transport}")
        if transport == "stdio":
            command = str(server.get("command") or "")
            args = [str(arg) for arg in server.get("args", [])]
            executable = pathlib.Path(command).stem.lower()
            if executable in _BLOCKED_EXECUTABLES:
                raise ValueError(f"MCP command is not allowed: {command}")
            invocation = " ".join([command, *args]).casefold()
            if "@latest" in invocation:
                raise ValueError("MCP runtime downloads must use an exact version, not @latest")
            if command and not pathlib.Path(command).is_absolute():
                resolved = shutil.which(command)
                if resolved:
                    server["command"] = str(pathlib.Path(resolved).resolve())
                else:
                    raise ValueError("MCP command must be an existing deterministic executable")
        else:
            url = str(server.get("url") or "").strip()
            parsed = urllib.parse.urlparse(url)
            local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if not url or (parsed.scheme != "https" and not local_http):
                raise ValueError("Remote MCP URLs must use HTTPS (local loopback HTTP is allowed)")
            if parsed.username or parsed.password:
                raise ValueError("Remote MCP URL must not embed credentials")
    from cyrene.runtime.settings_store import set_ as set_setting

    set_setting("mcp_servers", normalized)
    # Do not leave an older plaintext copy (which may contain explicit env
    # values) once the encrypted write has succeeded.
    _MCP_SERVERS_FILE.unlink(missing_ok=True)


def redact_mcp_servers(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return browser-safe MCP declarations without credential material."""
    result = []
    for original in servers:
        server = dict(original)
        if server.get("env"):
            server["env"] = {str(key): "[configured]" for key in server["env"]}
        if server.get("headers"):
            server["headers"] = {str(key): "[configured]" for key in server["headers"]}
        result.append(server)
    return result


def merge_redacted_mcp_servers(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve stored secrets represented by browser redaction sentinels."""
    previous = {str(server.get("name") or ""): server for server in existing}
    merged = []
    for original in incoming:
        server = dict(original)
        old = previous.get(str(server.get("name") or ""), {})
        for field in ("env", "headers"):
            values = server.get(field)
            old_values = old.get(field) if isinstance(old.get(field), dict) else {}
            if isinstance(values, dict):
                server[field] = {
                    str(key): old_values.get(key) if value == "[configured]" else value
                    for key, value in values.items()
                    if value != "[configured]" or key in old_values
                }
        merged.append(server)
    return merged


# ---------------------------------------------------------------------------
# Single server connection
# ---------------------------------------------------------------------------


class MCPServerConnection:
    """Manages one MCP server connection."""

    def __init__(self, name: str, transport: str, config: dict[str, Any]) -> None:
        self.name = name
        self.transport = transport  # "stdio" | "sse" | "streamable_http"
        self.config = config
        self._session: Any = None
        self._read_stream: Any = None
        self._write_stream: Any = None
        self._process: asyncio.subprocess.Process | None = None
        self._ctx_stack: Any = None
        self._http_client: Any = None
        self._tools: list[dict[str, Any]] = []
        # The raw stdio transport has one response stream. Serialize requests
        # so concurrent Agent tool calls cannot consume one another's replies.
        self._rpc_lock = asyncio.Lock()
        self.status = "disconnected"

    def _bounded_config_number(
        self,
        key: str,
        default: float,
        *,
        minimum: float,
        maximum: float,
    ) -> float:
        try:
            value = float(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return min(maximum, max(minimum, value))

    def startup_timeout_seconds(self) -> float:
        return self._bounded_config_number(
            "startup_timeout_seconds",
            _DEFAULT_STARTUP_TIMEOUT_SECONDS,
            minimum=_MIN_TIMEOUT_SECONDS,
            maximum=_MAX_STARTUP_TIMEOUT_SECONDS,
        )

    def tool_timeout_seconds(self) -> float:
        return self._bounded_config_number(
            "timeout_seconds",
            _DEFAULT_TOOL_TIMEOUT_SECONDS,
            minimum=_MIN_TIMEOUT_SECONDS,
            maximum=_MAX_TOOL_TIMEOUT_SECONDS,
        )

    def stdio_stream_limit_bytes(self) -> int:
        return int(self._bounded_config_number(
            "max_response_bytes",
            _DEFAULT_STDIO_STREAM_LIMIT_BYTES,
            minimum=64 * 1024,
            maximum=_MAX_STDIO_STREAM_LIMIT_BYTES,
        ))

    async def connect(self) -> None:
        """Connect to the MCP server and discover tools."""
        if self.transport == "stdio":
            await self._connect_stdio()
        elif self.transport in {"sse", "streamable_http", "streamable-http", "http"}:
            url = str(self.config.get("url", ""))
            if not url:
                raise ValueError(f"MCP server '{self.name}' has no URL configured")
            headers = {str(key): str(value) for key, value in (self.config.get("headers") or {}).items()}
            if self.transport == "sse":
                from mcp.client.sse import sse_client
                ctx = sse_client(url, headers=headers or None)
            else:
                import httpx
                from mcp.client.streamable_http import streamable_http_client
                self._http_client = httpx.AsyncClient(headers=headers, follow_redirects=True)
                ctx = streamable_http_client(url, http_client=self._http_client)
                self.transport = "streamable_http"
            self._ctx_stack = ctx
            streams = await ctx.__aenter__()
            self._read_stream, self._write_stream = streams[:2]
            from mcp import ClientSession
            self._session = ClientSession(self._read_stream, self._write_stream)
            await self._session.__aenter__()
            await self._session.initialize()
        else:
            raise ValueError(f"Unsupported MCP transport: {self.transport}")

        if self.transport == "stdio":
            # Initialize via raw JSON-RPC
            await self._json_rpc_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cyrene", "version": get_version()},
            }, timeout=self.startup_timeout_seconds())
            # Send initialized notification
            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            if self._process and self._process.stdin:
                self._process.stdin.write(notif.encode("utf-8"))
                await self._process.stdin.drain()

        self.status = "connected"

        # Discover tools
        try:
            await self._refresh_tools()
        except Exception:
            logger.warning("MCP server '%s' tool discovery failed", self.name, exc_info=True)

        logger.info("MCP server '%s' connected (%d tools)", self.name, len(self._tools))

    async def _connect_stdio(self) -> None:
        """Connect via stdio transport using raw asyncio subprocess + JSON-RPC.

        Uses pure asyncio instead of the MCP SDK's anyio-based stdio_client to
        avoid compatibility issues with uvicorn's event loop on Windows.
        """
        command = str(self.config.get("command", ""))
        args = list(self.config.get("args", []))
        if not command:
            raise ValueError(f"MCP server '{self.name}' has no command configured")

        # Reject shell interpreters and scripting runtimes as MCP commands.
        exe_stem = pathlib.Path(command).stem.lower()
        if exe_stem in _BLOCKED_EXECUTABLES:
            raise ValueError(
                f"MCP server '{self.name}': command '{command}' is not allowed "
                f"(blocked executable). Use a dedicated MCP server binary instead."
            )

        full_args = [command] + args
        _cwd = self.config.get("cwd") or None

        # Build a minimal environment — only safe system variables plus any
        # keys the user explicitly opted in via the server's "env" config dict.
        # This prevents leaking API keys, bot tokens, and other secrets that
        # live in the parent process environment into MCP subprocesses.
        _env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        _env["PYTHONUNBUFFERED"] = "1"
        for k, v in (self.config.get("env") or {}).items():
            _env[str(k)] = str(v)

        self._process = await asyncio.create_subprocess_exec(
            *full_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # DEVNULL avoids a potential deadlock: if stderr were a PIPE and the
            # subprocess wrote enough to fill the OS buffer before we read it,
            # the subprocess would block and the JSON-RPC handshake would hang.
            stderr=asyncio.subprocess.DEVNULL,
            cwd=_cwd,
            env=_env,
            limit=self.stdio_stream_limit_bytes(),
        )

        logger.info("MCP server '%s' subprocess started (pid=%s)", self.name, self._process.pid)

    async def _json_rpc_request(
        self,
        method: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        """Send a JSON-RPC request to the subprocess and return the result."""
        import uuid as _uuid
        req_id = _uuid.uuid4().hex[:8]
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError(f"MCP server '{self.name}' not running")

        request_timeout = timeout or self.tool_timeout_seconds()
        payload = (json.dumps(request) + "\n").encode("utf-8")
        async with self._rpc_lock:
            self._process.stdin.write(payload)
            await self._process.stdin.drain()

            loop = asyncio.get_running_loop()
            deadline = loop.time() + request_timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError(
                        f"MCP server '{self.name}' request '{method}' timed out "
                        f"after {request_timeout:g} seconds"
                    )
                line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=remaining,
                )
                if not line:
                    return_code = self._process.returncode
                    raise RuntimeError(
                        f"MCP server '{self.name}' closed stdout"
                        + (
                            f" (exit code {return_code})"
                            if return_code is not None
                            else ""
                        )
                    )
                try:
                    response = json.loads(line.decode("utf-8").strip())
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"MCP server '{self.name}' returned invalid JSON-RPC"
                    ) from exc
                # Server notifications have no id. Ignore them while waiting
                # for the response paired with this request.
                if "id" not in response:
                    continue
                if str(response.get("id")) != str(req_id):
                    raise RuntimeError(
                        f"MCP server '{self.name}' returned response id "
                        f"{response.get('id')!r}; expected {req_id!r}"
                    )
                if "error" in response:
                    raise RuntimeError(
                        f"MCP server '{self.name}' error: {response['error']}"
                    )
                return response.get("result", {})

    async def _connect_sse(self) -> None:
        """Connect via SSE transport."""
        from mcp.client.sse import sse_client

        url = str(self.config.get("url", ""))
        if not url:
            raise ValueError(f"MCP server '{self.name}' has no URL configured")

        ctx = sse_client(url)
        self._ctx_stack = ctx
        self._read_stream, self._write_stream = await ctx.__aenter__()

    async def _refresh_tools(self) -> None:
        """Fetch and cache tool definitions from the server via JSON-RPC."""
        try:
            if self.transport == "stdio":
                result = await self._json_rpc_request(
                    "tools/list",
                    timeout=self.startup_timeout_seconds(),
                )
                raw_tools = result.get("tools", [])
            else:
                if self._session is None:
                    raise RuntimeError(f"MCP server '{self.name}' is not connected")
                result = await asyncio.wait_for(
                    self._session.list_tools(),
                    timeout=self.startup_timeout_seconds(),
                )
                raw_tools = [
                    item.model_dump(by_alias=True, exclude_none=True)
                    for item in result.tools
                ]
            self._tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", "") or "",
                        "parameters": t.get("inputSchema", {}),
                    },
                }
                for t in raw_tools
                if t.get("name")
            ]
        except Exception:
            logger.exception("Failed to list tools from MCP server '%s'", self.name)
            self._tools = []

    def get_tool_defs(self) -> list[dict[str, Any]]:
        """Return cached tool definitions in OpenAI-compatible format."""
        return list(self._tools)

    def has_tool(self, name: str) -> bool:
        """Check if this server has a tool with the given name."""
        return any(td["function"]["name"] == name for td in self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool and serialize supported MCP content for the Agent loop."""
        from cyrene.tooling.mcp_content import serialize_mcp_content_blocks

        result = await self.call_tool_raw(name, arguments)
        text = serialize_mcp_content_blocks(name, result.get("content") or [])
        if result.get("is_error"):
            raise RuntimeError(text)
        return text

    async def call_tool_raw(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Call a tool without discarding structured or non-text MCP content."""

        if self.transport == "stdio":
            # Raw JSON-RPC for stdio
            result = await self._json_rpc_request("tools/call", {
                "name": name,
                "arguments": arguments or {},
            }, timeout=self.tool_timeout_seconds())
            return {
                "content": list(result.get("content") or []),
                "structured_content": result.get("structuredContent") or {},
                "is_error": bool(result.get("isError", False)),
            }
        else:
            # SSE transport uses the MCP SDK session
            if self._session is None:
                raise RuntimeError(f"MCP server '{self.name}' is not connected")
            result = await asyncio.wait_for(
                self._session.call_tool(name, arguments or {}),
                timeout=30.0,
            )
            dumped = result.model_dump(by_alias=True, exclude_none=True)
            return {
                "content": list(dumped.get("content") or []),
                "structured_content": (
                    dumped.get("structuredContent")
                    or dumped.get("structured_content")
                    or {}
                ),
                "is_error": bool(dumped.get("isError") or dumped.get("is_error")),
            }

    async def disconnect(self) -> None:
        """Disconnect from the server and clean up resources."""
        self.status = "disconnected"
        self._tools = []

        # Close SSE session/context if present
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._ctx_stack is not None:
            try:
                await self._ctx_stack.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx_stack = None
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None

        # Terminate subprocess (stdio transport)
        if self._process is not None:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    self._process.kill()
                    await asyncio.wait_for(self._process.wait(), timeout=3)
                except Exception:
                    logger.debug("MCP child %s survived terminate and kill; orphan may remain", self.name, exc_info=True)
            except Exception:
                pass
            self._process = None

        logger.info("MCP server '%s' disconnected", self.name)


# ---------------------------------------------------------------------------
# Manager (singleton)
# ---------------------------------------------------------------------------


class MCPManager:
    """Singleton managing all MCP server connections."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConnection] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Load config and connect all enabled servers."""
        servers = get_mcp_servers()
        new_connections: dict[str, MCPServerConnection] = {}
        try:
            for cfg in servers:
                name = str(cfg.get("name", "")).strip()
                if not name:
                    continue
                if not cfg.get("enabled", True):
                    continue

                transport = str(cfg.get("transport", "stdio")).strip()
                conn = MCPServerConnection(name, transport, cfg)
                try:
                    await conn.connect()
                    new_connections[name] = conn
                except asyncio.CancelledError:
                    # connect() may already have spawned a subprocess or opened
                    # an HTTP client before the caller cancels startup.
                    await conn.disconnect()
                    raise
                except Exception:
                    await conn.disconnect()
                    logger.warning("Failed to connect MCP server '%s'", name, exc_info=True)

            async with self._lock:
                self._servers = new_connections
        except asyncio.CancelledError:
            # Connections are only published after every configured server has
            # been attempted, so cancellation must explicitly unwind staged
            # connections as well as the connection currently being opened.
            for conn in new_connections.values():
                await conn.disconnect()
            raise

    async def stop(self) -> None:
        """Disconnect all servers."""
        async with self._lock:
            snapshot = dict(self._servers)
            self._servers.clear()

        for name, conn in snapshot.items():
            try:
                await conn.disconnect()
            except Exception:
                logger.exception("Failed to disconnect MCP server '%s'", name)

    def get_tool_defs(self) -> list[dict[str, Any]]:
        """Aggregate tool definitions from all connected servers."""
        defs: list[dict[str, Any]] = []
        for conn in self._servers.values():
            defs.extend(conn.get_tool_defs())
        return defs

    def has_tool(self, name: str) -> bool:
        """Return whether a connected MCP server currently exposes ``name``."""
        return any(conn.has_tool(name) for conn in self._servers.values())

    def get_server_tool_defs(self, server_name: str) -> list[dict[str, Any]]:
        conn = self._servers.get(str(server_name or "").strip())
        return conn.get_tool_defs() if conn is not None else []

    def has_server_tool(self, server_name: str, tool_name: str) -> bool:
        conn = self._servers.get(str(server_name or "").strip())
        return bool(conn is not None and conn.has_tool(str(tool_name or "").strip()))

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Find the server that owns *name* and call it.

        Takes a snapshot of the server dict under the lock so that a concurrent
        stop/start cannot mutate the collection while we iterate.
        """
        async with self._lock:
            snapshot = list(self._servers.values())

        for conn in snapshot:
            if conn.has_tool(name):
                return await conn.call_tool(name, arguments)
        raise ValueError(f"MCP tool '{name}' not found on any connected server")

    async def _server_connection(self, server_name: str) -> MCPServerConnection:
        target = str(server_name or "").strip()
        async with self._lock:
            conn = self._servers.get(target)
        if conn is None:
            raise ValueError(f"MCP server '{target}' is not connected")
        return conn

    async def execute_tool_on(
        self, server_name: str, name: str, arguments: dict[str, Any]
    ) -> str:
        conn = await self._server_connection(server_name)
        if not conn.has_tool(name):
            raise ValueError(f"MCP tool '{name}' not found on server '{server_name}'")
        return await conn.call_tool(name, arguments)

    async def execute_tool_raw_on(
        self, server_name: str, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        conn = await self._server_connection(server_name)
        if not conn.has_tool(name):
            raise ValueError(f"MCP tool '{name}' not found on server '{server_name}'")
        return await conn.call_tool_raw(name, arguments)

    def get_tool_timeout(self, name: str) -> float:
        """Return the configured wall-clock timeout for the server owning a tool."""
        for conn in self._servers.values():
            if conn.has_tool(name):
                return (
                    conn.tool_timeout_seconds()
                    if conn.transport == "stdio"
                    else 30.0
                )
        return _DEFAULT_TOOL_TIMEOUT_SECONDS

    def get_server_tool_timeout(self, server_name: str, name: str) -> float:
        conn = self._servers.get(str(server_name or "").strip())
        if conn is None or not conn.has_tool(name):
            return _DEFAULT_TOOL_TIMEOUT_SECONDS
        return conn.tool_timeout_seconds() if conn.transport == "stdio" else 30.0

    def get_server_status(self) -> list[dict[str, Any]]:
        """Return status for all configured servers."""
        servers = get_mcp_servers()
        result: list[dict[str, Any]] = []
        for cfg in servers:
            name = str(cfg.get("name", "")).strip()
            if not name:
                continue
            conn = self._servers.get(name)
            tool_count = len(conn.get_tool_defs()) if conn else 0
            result.append({
                "name": name,
                "transport": cfg.get("transport", "stdio"),
                "command": cfg.get("command", ""),
                "url": cfg.get("url", ""),
                "enabled": cfg.get("enabled", True),
                "status": conn.status if conn else "disconnected",
                "tool_count": tool_count,
            })
        return result
