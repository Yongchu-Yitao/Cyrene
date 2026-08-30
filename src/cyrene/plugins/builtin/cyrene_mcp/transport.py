"""Event-loop-owned MCP transports used by the editable MCP Plugin."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from cyrene.platform.version import get_version

logger = logging.getLogger(__name__)

SAFE_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TERM",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TZ",
        "XDG_RUNTIME_DIR",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "SYSTEMROOT",
        "WINDIR",
    }
)

BLOCKED_EXECUTABLES = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "fish",
        "dash",
        "ksh",
        "tcsh",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "python",
        "python3",
        "python.exe",
        "node",
        "node.exe",
        "ruby",
        "perl",
        "env",
        "xargs",
        "script",
        "npx",
        "npx.exe",
        "uvx",
        "uvx.exe",
    }
)

DEFAULT_STARTUP_TIMEOUT_SECONDS = 120.0
DEFAULT_TOOL_TIMEOUT_SECONDS = 120.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_STARTUP_TIMEOUT_SECONDS = 300.0
MAX_TOOL_TIMEOUT_SECONDS = 120.0
DEFAULT_STDIO_STREAM_LIMIT_BYTES = 20 * 1024 * 1024
MAX_STDIO_STREAM_LIMIT_BYTES = 64 * 1024 * 1024


@dataclass(slots=True)
class _ToolCall:
    name: str
    arguments: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]


class MCPServerConnection:
    """Own one MCP connection and all of its I/O in a single asyncio task.

    The MCP SDK's HTTP transports use AnyIO task groups whose context must be
    exited by the same task that entered it.  A command queue also prevents
    Agent worker loops from touching streams created by the application loop.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.name = str(config.get("name") or "").strip()
        self.transport = str(config.get("transport") or "stdio").strip()
        self.status = "disconnected"
        self.error = ""
        self._tools: list[dict[str, Any]] = []
        self._commands: asyncio.Queue[_ToolCall | None] = asyncio.Queue()
        self._ready: asyncio.Future[None] | None = None
        self._runner: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None

    def _bounded_number(
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

    @property
    def startup_timeout_seconds(self) -> float:
        return self._bounded_number(
            "startup_timeout_seconds",
            DEFAULT_STARTUP_TIMEOUT_SECONDS,
            minimum=MIN_TIMEOUT_SECONDS,
            maximum=MAX_STARTUP_TIMEOUT_SECONDS,
        )

    @property
    def tool_timeout_seconds(self) -> float:
        return self._bounded_number(
            "timeout_seconds",
            DEFAULT_TOOL_TIMEOUT_SECONDS,
            minimum=MIN_TIMEOUT_SECONDS,
            maximum=MAX_TOOL_TIMEOUT_SECONDS,
        )

    @property
    def stdio_stream_limit_bytes(self) -> int:
        return int(
            self._bounded_number(
                "max_response_bytes",
                DEFAULT_STDIO_STREAM_LIMIT_BYTES,
                minimum=64 * 1024,
                maximum=MAX_STDIO_STREAM_LIMIT_BYTES,
            )
        )

    @property
    def tools(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(tool) for tool in self._tools)

    async def start(self) -> None:
        if self._runner is not None and not self._runner.done():
            if self._ready is not None:
                await asyncio.shield(self._ready)
            return
        loop = asyncio.get_running_loop()
        self._commands = asyncio.Queue()
        self._ready = loop.create_future()
        self.status = "connecting"
        self.error = ""
        self._runner = loop.create_task(
            self._run(),
            name=f"mcp-server:{self.name}",
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(self._ready),
                timeout=self.startup_timeout_seconds,
            )
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        runner = self._runner
        if runner is None:
            self.status = "disconnected"
            self._tools = []
            return
        if not runner.done() and self.status == "connected":
            await self._commands.put(None)
            try:
                await asyncio.wait_for(asyncio.shield(runner), timeout=8.0)
            except asyncio.TimeoutError:
                runner.cancel()
        elif not runner.done():
            runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        self._runner = None
        self._ready = None
        self.status = "disconnected"
        self._tools = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if self.status != "connected" or self._runner is None:
            raise RuntimeError(
                f"MCP server '{self.name}' is not connected"
                + (f": {self.error}" if self.error else "")
            )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        await self._commands.put(
            _ToolCall(str(name or ""), dict(arguments or {}), future)
        )
        return await future

    async def _run(self) -> None:
        assert self._ready is not None
        ready = self._ready
        try:
            async with AsyncExitStack() as stack:
                if self.transport == "stdio":
                    await self._open_stdio()
                    await self._initialize_stdio()
                    raw_tools = (
                        await self._json_rpc_request(
                            "tools/list",
                            timeout=self.startup_timeout_seconds,
                        )
                    ).get("tools") or []
                    session = None
                else:
                    session = await self._open_http(stack)
                    result = await asyncio.wait_for(
                        session.list_tools(),
                        timeout=self.startup_timeout_seconds,
                    )
                    raw_tools = [
                        item.model_dump(by_alias=True, exclude_none=True)
                        for item in result.tools
                    ]
                self._tools = [
                    {
                        "name": str(item.get("name") or "").strip(),
                        "description": str(item.get("description") or ""),
                        "input_schema": dict(
                            item.get("inputSchema")
                            or item.get("input_schema")
                            or {"type": "object"}
                        ),
                    }
                    for item in raw_tools
                    if isinstance(item, dict)
                    and str(item.get("name") or "").strip()
                ]
                self.status = "connected"
                if not ready.done():
                    ready.set_result(None)
                await self._serve(session)
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except BaseException as exc:
            self.status = "error"
            self.error = str(exc)
            if not ready.done():
                ready.set_exception(exc)
            logger.warning(
                "MCP server '%s' stopped: %s",
                self.name,
                exc,
                exc_info=True,
            )
        finally:
            await self._close_stdio()
            self._fail_pending(
                RuntimeError(
                    f"MCP server '{self.name}' stopped"
                    + (f": {self.error}" if self.error else "")
                )
            )
            if self.status != "error":
                self.status = "disconnected"

    async def _open_http(self, stack: AsyncExitStack) -> Any:
        url = str(self.config.get("url") or "").strip()
        headers = {
            str(key): str(value)
            for key, value in (self.config.get("headers") or {}).items()
        }
        if self.transport == "sse":
            from mcp.client.sse import sse_client

            streams = await stack.enter_async_context(
                sse_client(url, headers=headers or None)
            )
        else:
            import httpx
            from mcp.client.streamable_http import streamable_http_client

            client = await stack.enter_async_context(
                httpx.AsyncClient(headers=headers, follow_redirects=True)
            )
            streams = await stack.enter_async_context(
                streamable_http_client(url, http_client=client)
            )
            self.transport = "streamable_http"
        from mcp import ClientSession

        session = await stack.enter_async_context(
            ClientSession(streams[0], streams[1])
        )
        await asyncio.wait_for(
            session.initialize(),
            timeout=self.startup_timeout_seconds,
        )
        return session

    async def _open_stdio(self) -> None:
        command = str(self.config.get("command") or "").strip()
        args = [str(item) for item in self.config.get("args") or ()]
        if not command:
            raise ValueError(f"MCP server '{self.name}' has no command configured")
        executable = pathlib.Path(command).stem.lower()
        if executable in BLOCKED_EXECUTABLES:
            raise ValueError(
                f"MCP server '{self.name}': command '{command}' is not allowed"
            )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in SAFE_ENV_KEYS
        }
        environment["PYTHONUNBUFFERED"] = "1"
        for key, value in (self.config.get("env") or {}).items():
            environment[str(key)] = str(value)
        self._process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.config.get("cwd") or None,
            env=environment,
            limit=self.stdio_stream_limit_bytes,
        )

    async def _initialize_stdio(self) -> None:
        await self._json_rpc_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cyrene", "version": get_version()},
            },
            timeout=self.startup_timeout_seconds,
        )
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        process.stdin.write((json.dumps(notification) + "\n").encode("utf-8"))
        await process.stdin.drain()

    async def _json_rpc_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        request_id = uuid.uuid4().hex[:12]
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params
        process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        await process.stdin.drain()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"MCP server '{self.name}' request '{method}' timed out"
                )
            line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=remaining,
            )
            if not line:
                raise RuntimeError(
                    f"MCP server '{self.name}' closed stdout"
                    + (
                        f" (exit code {process.returncode})"
                        if process.returncode is not None
                        else ""
                    )
                )
            try:
                response = json.loads(line.decode("utf-8").strip())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"MCP server '{self.name}' returned invalid JSON-RPC"
                ) from exc
            if "id" not in response:
                continue
            if str(response.get("id")) != request_id:
                raise RuntimeError(
                    f"MCP server '{self.name}' returned response id "
                    f"{response.get('id')!r}; expected {request_id!r}"
                )
            if "error" in response:
                raise RuntimeError(
                    f"MCP server '{self.name}' error: {response['error']}"
                )
            result = response.get("result")
            return dict(result) if isinstance(result, dict) else {}

    async def _serve(self, session: Any) -> None:
        while True:
            command = await self._commands.get()
            if command is None:
                return
            try:
                if self.transport == "stdio":
                    raw = await asyncio.wait_for(
                        self._json_rpc_request(
                            "tools/call",
                            {
                                "name": command.name,
                                "arguments": command.arguments,
                            },
                            timeout=self.tool_timeout_seconds,
                        ),
                        timeout=self.tool_timeout_seconds + 1.0,
                    )
                    result = {
                        "content": list(raw.get("content") or []),
                        "structured_content": raw.get("structuredContent") or {},
                        "is_error": bool(raw.get("isError", False)),
                    }
                else:
                    response = await asyncio.wait_for(
                        session.call_tool(command.name, command.arguments),
                        timeout=self.tool_timeout_seconds,
                    )
                    raw = response.model_dump(by_alias=True, exclude_none=True)
                    result = {
                        "content": list(raw.get("content") or []),
                        "structured_content": (
                            raw.get("structuredContent")
                            or raw.get("structured_content")
                            or {}
                        ),
                        "is_error": bool(
                            raw.get("isError") or raw.get("is_error")
                        ),
                    }
            except BaseException as exc:
                if not command.future.done():
                    command.future.set_exception(exc)
            else:
                if not command.future.done():
                    command.future.set_result(result)

    async def _close_stdio(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except Exception:
                pass
        if process.returncode is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            process.kill()
            await asyncio.gather(process.wait(), return_exceptions=True)
        except ProcessLookupError:
            pass

    def _fail_pending(self, error: BaseException) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except asyncio.QueueEmpty:
                return
            if command is not None and not command.future.done():
                command.future.set_exception(error)


__all__ = [
    "BLOCKED_EXECUTABLES",
    "MCPServerConnection",
]
