"""ACP stdio JSON-RPC transport over a newline-delimited child process.

Spawns the Agent's ACP binary with ``asyncio.create_subprocess_exec`` (never
``shell=True``), correlates concurrent JSON-RPC requests by id, routes
server-to-client notifications to subscribers, captures stderr with a hard
byte bound, and tears the process down gracefully (stdin EOF -> exit wait ->
``terminate`` -> ``kill``) with timeouts at every stage.

Security posture (handoff §17):

* The command must be a bare executable name already validated by the
  installation record; it is re-validated here (no path separators, no
  embedded arguments) before ``create_subprocess_exec`` is called.
* Arguments come from a built-in profile allowlist owned by the runtime, never
  from the Manifest (phase 1 ignores manifest-provided args).
* The child environment is built from an allowlist of benign base variables
  plus an explicit binder-supplied env map; Cyrene secrets are never inherited.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import urllib.parse
from collections import deque
from typing import Any, AsyncIterator, Awaitable, Callable

from cyrene.agent_runtime.acp_protocol import (
    ACP_METHOD_INITIALIZE,
    ACP_METHOD_SESSION_INTERRUPT,
    ACP_METHOD_SESSION_CANCEL,
    ACP_NOTIFICATIONS,
    ERROR_METHOD_NOT_FOUND,
    ACP_METHOD_REQUEST_PERMISSION,
    ACP_METHOD_ELICITATION_CREATE,
    ERROR_PARSE_ERROR,
    ACP_PROTOCOL_VERSION,
    JsonRpcError,
    build_error,
    build_notification,
    build_request,
    build_response,
    error_from_frame,
    frame_id,
    frame_kind,
    parse_frame,
)
from cyrene.agent_runtime.errors import AgentRuntimeError

logger = logging.getLogger(__name__)

# A command is acceptable only when it is a bare executable name: no path
# separators, no flags, no whitespace.  This mirrors the install-time
# validation in ``cyrene.extensions.agent_runtime`` so the runtime never
# depends on the caller having validated correctly.
_BARE_COMMAND_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Defaults (all overridable through the constructor / request kwargs).
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0
DEFAULT_INITIALIZE_TIMEOUT_SECONDS = 30.0
# ACP encodes image/resource attachments inline in a one-line JSON-RPC frame.
# OpenCode resizes images to 2000 px, but PNG/base64 plus the ACP envelope can
# still exceed 1 MiB. Keep a firm bound while allowing normal multimodal turns.
DEFAULT_MAX_FRAME_BYTES = 16 * 1_048_576  # 16 MiB per JSON-RPC frame on stdout
DEFAULT_STDERR_LIMIT_BYTES = 65_536  # 64 KiB captured stderr
DEFAULT_SHUTDOWN_GRACE_SECONDS = 5.0
DEFAULT_KILL_GRACE_SECONDS = 3.0
_MAX_PROTOCOL_ERRORS = 8
_NOTIFICATION_QUEUE_MAX = 10_000

# Base environment keys allowed into the child process.  Everything else from
# the parent environment is dropped so Cyrene secrets (API keys, tokens,
# credentials) cannot leak into the Agent process (handoff §17).
SAFE_BASE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE",
    "LC_MESSAGES", "LC_NUMERIC", "LC_TIME", "TMPDIR", "TMP", "TERM",
    "NO_COLOR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "CLICOLOR", "CLICOLOR_FORCE", "FORCE_COLOR",
})

_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})
def _safe_proxy_url(value: object) -> str:
    """Return a credential-free proxy URL, or an empty string when unsafe."""
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urllib.parse.urlsplit(candidate)
        # Never copy proxy credentials into an untrusted external Agent.
        if (
            parsed.scheme.lower() not in _PROXY_SCHEMES
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        _ = parsed.port  # Reject malformed/out-of-range ports.
    except ValueError:
        return ""
    return candidate


def safe_proxy_environment(
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """Validate an explicitly supplied, credential-free proxy environment."""
    source = os.environ if base is None else base
    result: dict[str, str] = {}
    for upper in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        value = _safe_proxy_url(source.get(upper) or source.get(upper.lower()))
        if value:
            result[upper] = value
            result[upper.lower()] = value
    no_proxy = str(source.get("NO_PROXY") or source.get("no_proxy") or "").strip()
    if no_proxy:
        result["NO_PROXY"] = no_proxy
        result["no_proxy"] = no_proxy

    return result


def is_valid_bare_command(command: str) -> bool:
    """True only for a bare executable name without path or arguments."""
    return isinstance(command, str) and bool(_BARE_COMMAND_PATTERN.fullmatch(command.strip()))


def build_safe_env(*, base: dict[str, str] | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Construct the child environment from an allowlist plus explicit extras."""
    source = base if isinstance(base, dict) else os.environ
    env = {key: str(source[key]) for key in SAFE_BASE_ENV_KEYS if key in source}
    if isinstance(extra, dict):
        for key, value in extra.items():
            if isinstance(key, str) and key.strip() and isinstance(value, str):
                env[key.strip()] = value
    return env


class AcpTransportError(AgentRuntimeError):
    """Transport-level failure normalized to a stable ``failureKind``.

    Unresponsive agents, crashed processes, and broken framing map to
    ``agent_crashed`` (retryable); install/dependency problems map to
    ``dependency_missing``; wrong drivers map to ``protocol_mismatch``.
    """


class AcpStdioTransport:
    """One stdio JSON-RPC session with a single ACP agent process."""

    def __init__(
        self,
        command: str,
        args: tuple[str, ...] = (),
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        which_fn: Callable[[str], str | None] | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        initialize_timeout: float = DEFAULT_INITIALIZE_TIMEOUT_SECONDS,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        stderr_limit_bytes: int = DEFAULT_STDERR_LIMIT_BYTES,
        shutdown_grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
        kill_grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS,
        notification_limit: int = _NOTIFICATION_QUEUE_MAX,
    ) -> None:
        self.command = str(command or "").strip()
        self.args = tuple(str(arg) for arg in (args or ()))
        self.env = dict(env) if isinstance(env, dict) else None
        self.cwd = cwd
        self._which = which_fn or (
            lambda command: shutil.which(command, path=(self.env or {}).get("PATH"))
        )
        self.request_timeout = float(request_timeout)
        self.initialize_timeout = float(initialize_timeout)
        self.max_frame_bytes = int(max_frame_bytes)
        self.stderr_limit = int(stderr_limit_bytes)
        self.shutdown_grace = float(shutdown_grace_seconds)
        self.kill_grace = float(kill_grace_seconds)
        self.notification_limit = int(notification_limit)

        self.process: asyncio.subprocess.Process | None = None
        self.negotiated_protocol_version = 0
        self.agent_capabilities: dict[str, Any] = {}
        self.auth_methods: list[dict[str, Any]] = []
        self.negotiated_by_fallback = False

        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._dropped_notifications = 0
        self._stderr_lines: deque[str] = deque()
        self._stderr_bytes = 0
        self._stderr_truncated = False
        self._protocol_errors: list[dict[str, Any]] = []
        self._closing = False
        self._closed = False
        self._started = False
        self._reader_task: asyncio.Task[Any] | None = None
        self._stderr_task: asyncio.Task[Any] | None = None
        self._close_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Validate the command and spawn the ACP process.

        Raises ``AcpTransportError`` with ``dependency_missing`` when the
        command is not a bare executable or cannot be resolved on PATH.
        """
        if self._started:
            return
        if not is_valid_bare_command(self.command):
            raise AcpTransportError(
                "dependency_missing",
                f"ACP command {self.command!r} is not a bare executable name",
            )
        resolved = self._which(self.command)
        if not resolved:
            raise AcpTransportError(
                "dependency_missing",
                f"ACP command {self.command!r} not found on PATH",
                detail={"command": self.command},
            )
        env = build_safe_env(base=None, extra=self.env)
        try:
            self.process = await asyncio.create_subprocess_exec(
                resolved,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.cwd,
                # asyncio's default StreamReader limit is only 64 KiB. ACP
                # image/resource updates are one-line JSON frames and easily
                # exceed that even while remaining below our explicit frame
                # safety limit.
                limit=max(65_536, self.max_frame_bytes + 1),
            )
        except (OSError, ValueError) as exc:
            raise AcpTransportError(
                "dependency_missing",
                f"failed to start ACP command {self.command!r}: {exc}",
                detail={"command": self.command},
            ) from exc
        self._started = True
        self._close_lock = asyncio.Lock()
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def initialize(self) -> dict[str, Any]:
        """Negotiate ``initialize`` with tolerant fallback.

        A server that answers ``method not found`` is treated as protocol
        version 1 with conservative (empty) capabilities instead of a hard
        failure; the negotiated result is stored on the transport.
        """
        try:
            result = await self.request(
                ACP_METHOD_INITIALIZE,
                {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {
                        "session": {"configOptions": {"boolean": {}}},
                    },
                },
                timeout=self.initialize_timeout,
            )
        except AcpTransportError as exc:
            if not self._is_negotiable_failure(exc):
                raise
            result = {}
            self.negotiated_by_fallback = True
        if not isinstance(result, dict):
            result = {}
        self.negotiated_protocol_version = self._coerce_protocol_version(result.get("protocolVersion"))
        raw_caps = result.get("agentCapabilities")
        self.agent_capabilities = raw_caps if isinstance(raw_caps, dict) else {}
        raw_auth = result.get("authMethods")
        self.auth_methods = [item for item in raw_auth if isinstance(item, dict)] if isinstance(raw_auth, list) else []
        return {
            "protocolVersion": self.negotiated_protocol_version,
            "agentCapabilities": self.agent_capabilities,
            "authMethods": self.auth_methods,
            "negotiatedByFallback": self.negotiated_by_fallback,
        }

    @staticmethod
    def _is_negotiable_failure(exc: AgentRuntimeError) -> bool:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return bool(detail.get("methodNotFound"))

    @staticmethod
    def _coerce_protocol_version(value: Any) -> int:
        try:
            version = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(version, 0)

    # ------------------------------------------------------------------
    # Requests / notifications
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send one JSON-RPC request and await its correlated response.

        Concurrent requests are safe: each pending frame future is keyed by a
        monotonically increasing request id.  Raises ``AcpTransportError`` on
        transport death, timeout, or a remote JSON-RPC error (wrapping
        ``JsonRpcError``).
        """
        self._ensure_running()
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        frame = build_request(method, params, request_id)
        try:
            await self._write_frame(frame)
        except Exception:
            self._pending.pop(request_id, None)
            raise
        effective_timeout = self.request_timeout if timeout is None else float(timeout)
        try:
            if effective_timeout > 0:
                result = await asyncio.wait_for(future, timeout=effective_timeout)
            else:
                result = await future
        except asyncio.TimeoutError:
            if not future.done():
                future.cancel()
            raise AcpTransportError(
                "agent_crashed",
                f"ACP request {method!r} timed out after {effective_timeout:g}s",
                detail={"method": method, "timeout": effective_timeout, "kind": "timeout"},
                retryable=True,
            ) from None
        except JsonRpcError as exc:
            # Remote JSON-RPC errors are wrapped so callers can implement
            # tolerant fallbacks (e.g. method-not-found -> alternate method)
            # without branching on free-form text.
            raise AcpTransportError(
                "protocol_mismatch",
                f"ACP method {method!r} failed: {exc}",
                detail={
                    "method": method,
                    "jsonrpc": exc.to_dict(),
                    "methodNotFound": exc.is_method_not_found,
                },
            ) from exc
        finally:
            self._pending.pop(request_id, None)
        return result

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send one JSON-RPC notification (no response expected)."""
        self._ensure_running()
        await self._write_frame(build_notification(method, params))

    async def respond(self, request_id: int | str, result: Any) -> None:
        """Respond to an Agent→Client JSON-RPC request."""
        self._ensure_running()
        await self._write_frame(build_response(result, request_id))

    def notifications(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator over server-to-client notifications.

        Notifications are buffered in a bounded queue so a slow consumer cannot
        grow memory without limit; overflow drops the oldest notifications and
        increments ``dropped_notifications``.
        """
        return self._notification_iterator()

    @property
    def notifications_pending(self) -> bool:
        return not self._notifications.empty()

    async def discard_notifications_until_quiet(
        self,
        *,
        quiet_seconds: float = 0.05,
        max_wait_seconds: float = 0.5,
    ) -> int:
        """Discard setup/replay notifications before a new prompt starts.

        ACP ``session/load`` implementations may replay the entire historical
        transcript through the same notification channel used for live output.
        The load response can also race the last replay frames, so draining only
        what is already queued is insufficient.  Wait for a short quiet window,
        bounded by ``max_wait_seconds``; callers invoke this before sending the
        prompt, therefore genuine output from the new turn cannot be discarded.
        """
        quiet = max(0.0, float(quiet_seconds))
        deadline = asyncio.get_running_loop().time() + max(quiet, float(max_wait_seconds))
        discarded = 0
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return discarded
            try:
                await asyncio.wait_for(
                    self._notifications.get(),
                    timeout=min(quiet, remaining) if quiet > 0 else 0,
                )
            except (asyncio.TimeoutError, asyncio.QueueEmpty):
                return discarded
            discarded += 1

    async def _notification_iterator(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            if self._closed and self._notifications.empty():
                return
            try:
                frame = await asyncio.wait_for(self._notifications.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            yield frame

    # ------------------------------------------------------------------
    # Reader / stderr
    # ------------------------------------------------------------------

    async def _read_stdout(self) -> None:
        process = self.process
        assert process is not None and process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                if len(line) > self.max_frame_bytes:
                    self._record_protocol_error(
                        "frame_too_large",
                        f"stdout frame exceeded {self.max_frame_bytes} bytes",
                    )
                    continue
                await self._dispatch_line(line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self._record_protocol_error("reader_error", str(exc))
        finally:
            self._on_eof_or_error()

    async def _dispatch_line(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace")
        try:
            frame = parse_frame(text)
        except ValueError:
            self._record_protocol_error("invalid_json", text[:200])
            return
        if frame is None:
            return
        kind = frame_kind(frame)
        if kind in {"response", "error"}:
            request_id = frame_id(frame)
            if request_id is None:
                return
            future = self._pending.pop(request_id, None)
            if future is None or future.done():
                return
            if kind == "error":
                future.set_exception(error_from_frame(frame))
            else:
                future.set_result(frame.get("result"))
            return
        if kind == "request":
            request_id = frame_id(frame)
            if str(frame.get("method") or "") in {ACP_METHOD_REQUEST_PERMISSION, ACP_METHOD_ELICITATION_CREATE}:
                self._queue_notification(frame)
                return
            await self._write_frame(
                build_error(
                    ERROR_METHOD_NOT_FOUND,
                    "method not supported by Cyrene ACP client",
                    request_id,
                )
            )
            return
        if kind == "notification":
            method = str(frame.get("method") or "")
            if method in ACP_NOTIFICATIONS:
                self._queue_notification(frame)
            else:
                # Well-formed JSON-RPC notifications we do not model are not
                # protocol failures; log and ignore them.
                logger.debug("ignoring unknown ACP notification %r", method)
            return
        self._record_protocol_error("invalid_frame", text[:200])

    def _queue_notification(self, frame: dict[str, Any]) -> None:
        if self._notifications.qsize() >= self.notification_limit:
            try:
                self._notifications.get_nowait()
                self._dropped_notifications += 1
            except asyncio.QueueEmpty:
                pass
        try:
            self._notifications.put_nowait(frame)
        except Exception:  # pragma: no cover - defensive
            pass

    async def _drain_stderr(self) -> None:
        process = self.process
        assert process is not None and process.stderr is not None
        try:
            while True:
                # Agents may emit long unbroken stderr. Fixed-size reads keep
                # capture bounded without StreamReader line-limit failures.
                chunk = await process.stderr.read(4096)
                if not chunk:
                    break
                self._capture_stderr(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            pass

    def _capture_stderr(self, chunk: bytes) -> None:
        if self.stderr_limit <= 0:
            return
        try:
            text = chunk.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            return
        self._stderr_lines.append(text)
        total = sum(len(line) for line in self._stderr_lines)
        while total > self.stderr_limit and self._stderr_lines:
            dropped = self._stderr_lines.popleft()
            total -= len(dropped)
            self._stderr_truncated = True
        self._stderr_bytes = total

    def _record_protocol_error(self, code: str, message: str) -> None:
        self._protocol_errors.append({"code": code, "message": str(message)[:300]})
        if len(self._protocol_errors) >= _MAX_PROTOCOL_ERRORS:
            self._fail_pending(
                AcpTransportError(
                    "agent_crashed",
                    "ACP transport failed: too many protocol errors",
                    detail={"errors": list(self._protocol_errors)},
                    retryable=True,
                )
            )
            if self.process is not None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass

    def _on_eof_or_error(self) -> None:
        if self._closed:
            return
        exit_code = self.process.returncode if self.process is not None else None
        detail: dict[str, Any] = {
            "exitCode": exit_code,
            "protocolErrors": list(self._protocol_errors),
            "stderrTruncated": self._stderr_truncated,
        }
        message = "ACP process exited unexpectedly"
        if self._protocol_errors:
            last_error = self._protocol_errors[-1]
            code = str(last_error.get("code") or "protocol_error")
            reason = str(last_error.get("message") or "ACP stdout reader failed")
            message = f"ACP protocol stream failed ({code}): {reason}"
        logger.error("%s; exit_code=%s", message, exit_code)
        self._fail_pending(
            AcpTransportError(
                "agent_crashed",
                message,
                detail=detail,
                retryable=True,
            )
        )
        self._closed = True

    def _fail_pending(self, exc: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    def _ensure_running(self) -> None:
        if self._closed:
            raise AcpTransportError("agent_crashed", "ACP transport is closed", retryable=True)
        if not self._started or self.process is None or self.process.returncode is not None:
            raise AcpTransportError(
                "agent_crashed",
                "ACP transport is not running",
                detail={"started": self._started},
                retryable=True,
            )

    async def _write_frame(self, frame: dict[str, Any]) -> None:
        self._ensure_running()
        assert self.process is not None and self.process.stdin is not None
        try:
            self.process.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            self._on_eof_or_error()
            raise AcpTransportError(
                "agent_crashed",
                f"failed to write to ACP stdin: {exc}",
                retryable=True,
            ) from exc

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Graceful shutdown: stdin EOF, wait, terminate, then kill.

        Idempotent and safe to call from cancellation paths.
        """
        lock = self._close_lock
        if lock is not None:
            async with lock:
                if self._closed and not self._process_is_alive():
                    return
                await self._do_close()
        elif not self._closed or self._process_is_alive():
            await self._do_close()

    def _process_is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def _do_close(self) -> None:
        if self._closed and not self._process_is_alive():
            return
        self._closing = True
        process = self.process
        if process is not None:
            stdin = process.stdin
            if stdin is not None and not stdin.is_closing():
                try:
                    stdin.close()
                except Exception:
                    pass
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=self.shutdown_grace)
                except asyncio.TimeoutError:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=self.kill_grace)
                    except asyncio.TimeoutError:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        try:
                            await asyncio.wait_for(process.wait(), timeout=self.kill_grace)
                        except asyncio.TimeoutError:
                            pass
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        if self._reader_task is not None:
            try:
                await asyncio.gather(self._reader_task, return_exceptions=True)
            except Exception:
                pass
        if self._stderr_task is not None:
            try:
                await asyncio.gather(self._stderr_task, return_exceptions=True)
            except Exception:
                pass
        self._closed = True
        self._fail_pending(
            AcpTransportError("agent_crashed", "ACP transport closed", retryable=True)
        )

    async def abort(self) -> None:
        """Force-kill the process without the graceful window."""
        process = self.process
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=self.kill_grace)
            except asyncio.TimeoutError:
                pass
        await self.close()

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stderr_snapshot(self, *, max_chars: int = 4000) -> dict[str, Any]:
        text = "".join(self._stderr_lines)
        return {
            "bytes": self._stderr_bytes,
            "truncated": self._stderr_truncated,
            "tail": text[-max_chars:] if max_chars > 0 else text,
        }

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def dropped_notifications(self) -> int:
        return self._dropped_notifications
