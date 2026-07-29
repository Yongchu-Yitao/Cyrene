"""Codex OAuth provider backed by the bundled Codex app-server.

The app-server owns browser login, credential refresh, model discovery, and
ChatGPT-plan rate limits. Cyrene never reads ``~/.codex/auth.json`` or handles
OAuth access/refresh tokens directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

CODEX_PROVIDER = "codex_oauth"
CODEX_BASE_URL = "codex://oauth"
_MAX_ORPHAN_NOTIFICATION_THREADS = 128
_MAX_GLOBAL_NOTIFICATIONS = 256


def _codex_executable() -> str:
    configured = str(os.environ.get("CODEX_BIN") or "").strip()
    candidates = [
        configured,
        shutil.which("codex") or "",
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Codex CLI is not installed")


class CodexAppServer:
    """Small asyncio JSON-RPC client for one local Codex app-server process."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._process_loop: asyncio.AbstractEventLoop | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._notification_queues: dict[
            str, asyncio.Queue[dict[str, Any]]
        ] = {}
        self._notification_backlog: dict[str, list[dict[str, Any]]] = {}
        self._request_id = 0
        self._start_lock = asyncio.Lock()
        self._limits_cache: tuple[float, dict[str, Any]] | None = None
        self._limits_refresh_task: asyncio.Task[dict[str, Any]] | None = None

    async def _ensure_started(self) -> None:
        current_loop = asyncio.get_running_loop()
        if (
            self._process is not None
            and self._process.returncode is None
            and self._process_loop is current_loop
        ):
            return
        if self._process is not None:
            # Test clients and embedded runtimes can create a fresh event loop
            # while the singleton provider still owns a process from the old
            # loop. Never reuse asyncio subprocess transports across loops.
            await self.close()
        async with self._start_lock:
            if (
                self._process is not None
                and self._process.returncode is None
                and self._process_loop is current_loop
            ):
                return
            executable = _codex_executable()
            self._process = await asyncio.create_subprocess_exec(
                executable,
                "app-server",
                "--stdio",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._process_loop = current_loop
            self._reader_task = asyncio.create_task(self._reader_loop())
            self._stderr_task = asyncio.create_task(self._stderr_loop())
            await self._request_raw(
                "initialize",
                {
                    "clientInfo": {
                        "name": "cyrene",
                        "title": "Cyrene",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
                timeout=15,
            )
            await self._notify_raw("initialized", {})

    async def _reader_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Ignoring non-JSON Codex app-server output")
                    continue
                request_id = message.get("id")
                if request_id is not None and "method" not in message:
                    future = self._pending.pop(int(request_id), None)
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        error = message.get("error") or {}
                        future.set_exception(
                            RuntimeError(str(error.get("message") or error))
                        )
                    else:
                        future.set_result(message.get("result"))
                    continue
                if request_id is not None and message.get("method"):
                    await self._reply_unsupported_request(int(request_id))
                    continue
                if message.get("method"):
                    self._route_notification(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex app-server reader failed")
        finally:
            error = RuntimeError("Codex app-server stopped")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()
            stopped = {
                "method": "cyrene/providerStopped",
                "params": {"message": str(error)},
            }
            for queue in list(self._notification_queues.values()):
                queue.put_nowait(stopped)

    @staticmethod
    def _notification_thread_id(message: dict[str, Any]) -> str:
        params = message.get("params") if isinstance(message, dict) else {}
        if not isinstance(params, dict):
            return ""
        direct = str(params.get("threadId") or "").strip()
        if direct:
            return direct
        for key in ("thread", "turn", "item"):
            nested = params.get(key)
            if not isinstance(nested, dict):
                continue
            nested_thread = str(
                nested.get("threadId")
                or (
                    nested.get("id")
                    if key == "thread"
                    else ""
                )
                or ""
            ).strip()
            if nested_thread:
                return nested_thread
        return ""

    def _route_notification(self, message: dict[str, Any]) -> None:
        """Route app-server notifications to the owning provider thread."""
        thread_id = self._notification_thread_id(message)
        queue = self._notification_queues.get(thread_id) if thread_id else None
        if queue is not None:
            queue.put_nowait(message)
            return
        if thread_id:
            if (
                thread_id not in self._notification_backlog
                and len(self._notification_backlog)
                >= _MAX_ORPHAN_NOTIFICATION_THREADS
            ):
                oldest_thread_id = next(iter(self._notification_backlog))
                self._notification_backlog.pop(oldest_thread_id, None)
            backlog = self._notification_backlog.setdefault(thread_id, [])
            backlog.append(message)
            # A provider thread is ephemeral and normally gets its queue within
            # the same event-loop turn as ``thread/start``. Keep a defensive
            # bound so an unexpected foreign notification cannot grow forever.
            if len(backlog) > 64:
                del backlog[:-64]
            return
        # Account/model notifications are not tied to a completion. Keep the
        # legacy queue for diagnostics and forward compatibility.
        if self._notifications.qsize() >= _MAX_GLOBAL_NOTIFICATIONS:
            self._notifications.get_nowait()
        self._notifications.put_nowait(message)

    async def _stderr_loop(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        try:
            while line := await self._process.stderr.readline():
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug("codex app-server: %s", text)
        except asyncio.CancelledError:
            raise

    async def _write(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Codex app-server is unavailable")
        self._process.stdin.write(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        )
        await self._process.stdin.drain()

    async def _reply_unsupported_request(self, request_id: int) -> None:
        await self._write(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "Cyrene does not expose host tools to Codex app-server",
                },
            }
        )

    async def _request_raw(
        self, method: str, params: dict[str, Any], *, timeout: float = 30
    ) -> Any:
        self._request_id += 1
        request_id = self._request_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write(
            {
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _request(
        self, method: str, params: dict[str, Any], *, timeout: float = 30
    ) -> Any:
        await self._ensure_started()
        return await self._request_raw(method, params, timeout=timeout)

    async def _notify_raw(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def account(self, *, refresh: bool = False) -> dict[str, Any]:
        result = await self._request(
            "account/read", {"refreshToken": bool(refresh)}, timeout=20
        )
        return result if isinstance(result, dict) else {}

    async def start_login(self) -> dict[str, Any]:
        result = await self._request(
            "account/login/start",
            {
                "type": "chatgpt",
                "appBrand": "codex",
                "codexStreamlinedLogin": True,
                "useHostedLoginSuccessPage": True,
            },
            timeout=20,
        )
        return result if isinstance(result, dict) else {}

    async def logout(self) -> None:
        await self._request("account/logout", {}, timeout=20)

    async def models(self) -> list[dict[str, Any]]:
        result = await self._request(
            "model/list",
            {"includeHidden": False, "limit": 100},
            timeout=30,
        )
        if not isinstance(result, dict):
            return []
        return [
            item
            for item in result.get("data") or []
            if isinstance(item, dict) and not item.get("hidden")
        ]

    async def rate_limits(self) -> dict[str, Any]:
        result = await self._request("account/rateLimits/read", {}, timeout=30)
        normalized = result if isinstance(result, dict) else {}
        self._limits_cache = (time.monotonic(), normalized)
        return normalized

    async def rate_limits_cached(self, *, max_age: float = 30) -> dict[str, Any]:
        cached = self._limits_cache
        if cached is not None and time.monotonic() - cached[0] <= max_age:
            return cached[1]
        return await self.rate_limits()

    def _schedule_rate_limits_refresh(self) -> None:
        task = self._limits_refresh_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self.rate_limits())
        self._limits_refresh_task = task

        def _settled(done: asyncio.Task[dict[str, Any]]) -> None:
            if self._limits_refresh_task is done:
                self._limits_refresh_task = None
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                # Quota telemetry is advisory; the provider still owns the hard
                # enforcement path for the model request.
                logger.warning(
                    "Codex quota refresh failed in the background: %s",
                    exc,
                )

        task.add_done_callback(_settled)

    @staticmethod
    def _quota_available_from_limits(limits: dict[str, Any]) -> bool:
        buckets = limits.get("rateLimitsByLimitId") or {}
        if isinstance(buckets, dict):
            codex_limit = buckets.get("codex")
            candidates = [codex_limit] if isinstance(codex_limit, dict) else []
        else:
            candidates = []
        if not candidates and isinstance(limits.get("rateLimits"), dict):
            candidates = [limits["rateLimits"]]
        for bucket in candidates:
            if bucket.get("rateLimitReachedType"):
                return False
            windows = [bucket.get("primary"), bucket.get("secondary")]
            if any(
                isinstance(window, dict)
                and float(window.get("usedPercent") or 0) >= 100
                for window in windows
                if window is not None
            ):
                return False
        return True

    async def quota_available(self) -> bool:
        """Return quota state without blocking on refresh when stale data exists."""
        cached = self._limits_cache
        if cached is not None:
            age = time.monotonic() - cached[0]
            if age > 30:
                self._schedule_rate_limits_refresh()
            return self._quota_available_from_limits(cached[1])
        try:
            limits = await self.rate_limits_cached()
        except (RuntimeError, OSError, TimeoutError) as exc:
            # Quota telemetry is advisory. A transient failure from the usage
            # endpoint must not make a healthy Codex model look unavailable.
            # Prefer even a stale snapshot when one exists; otherwise let the
            # model request proceed and allow the provider to enforce its own
            # hard quota.
            cached = self._limits_cache
            if cached is None:
                logger.warning(
                    "Codex quota check unavailable; proceeding without a local "
                    "quota gate: %s",
                    exc,
                )
                return True
            limits = cached[1]
            logger.warning(
                "Codex quota check unavailable; using the last cached limits: %s",
                exc,
            )
        return self._quota_available_from_limits(limits)

    async def snapshot(self, *, include_limits: bool = True) -> dict[str, Any]:
        account = await self.account()
        account_data = account.get("account")
        connected = (
            isinstance(account_data, dict)
            and account_data.get("type") == "chatgpt"
        )
        models: list[dict[str, Any]] = []
        limits: dict[str, Any] = {}
        errors: dict[str, str] = {}
        if connected:
            if include_limits:
                model_result, limit_result = await asyncio.gather(
                    self.models(),
                    self.rate_limits(),
                    return_exceptions=True,
                )
            else:
                model_result = await self.models()
                limit_result = {}
            if isinstance(model_result, BaseException):
                errors["models"] = str(model_result)
            else:
                models = model_result
            if isinstance(limit_result, BaseException):
                errors["limits"] = str(limit_result)
            else:
                limits = limit_result
        snapshot = {
            "available": True,
            "connected": connected,
            "account": account_data if isinstance(account_data, dict) else None,
            "models": models,
            "limits": limits,
        }
        if errors:
            snapshot["errors"] = errors
        return snapshot

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        timeout: float,
        reasoning_effort: str = "",
        stream_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Run one provider turn without exposing Codex's own host tools.

        Cyrene's tool loop remains authoritative. Tool schemas are described in
        the base instructions and Codex is asked to emit the existing DSML
        envelope, which the normal model-runtime parser converts back to
        OpenAI-style tool calls.
        """
        await self._ensure_started()
        instructions = _provider_instructions(messages, tools)
        thread_result = await self._request_raw(
            "thread/start",
            {
                "model": model,
                "baseInstructions": instructions,
                "developerInstructions": (
                    "Act only as Cyrene's language-model backend. "
                    "Do not inspect files, run commands, browse, or call built-in tools."
                ),
                "dynamicTools": [],
                "environments": [],
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
            timeout=min(timeout, 30),
        )
        thread = (thread_result or {}).get("thread") or {}
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise RuntimeError("Codex did not create a provider thread")

        notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._notification_queues[thread_id] = notifications
        for notification in self._notification_backlog.pop(thread_id, []):
            notifications.put_nowait(notification)
        try:
            user_input = _provider_input(messages)
            turn_result = await self._request_raw(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": user_input}],
                    "model": model,
                    **(
                        {"effort": reasoning_effort}
                        if reasoning_effort
                        else {}
                    ),
                },
                timeout=min(timeout, 30),
            )
            turn = (turn_result or {}).get("turn") or {}
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                raise RuntimeError("Codex did not start a provider turn")

            text_parts: list[str] = []
            final_text = ""
            usage: dict[str, Any] = {}
            if stream_callback:
                await stream_callback({"type": "reply_start"})
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("Codex model request timed out")
                notification = await asyncio.wait_for(
                    notifications.get(), timeout=remaining
                )
                method = str(notification.get("method") or "")
                params = notification.get("params") or {}
                if method == "cyrene/providerStopped":
                    raise RuntimeError("Codex app server stopped during model request")
                if method == "item/agentMessage/delta":
                    delta = str(params.get("delta") or "")
                    if delta:
                        text_parts.append(delta)
                        if stream_callback:
                            await stream_callback(
                                {"type": "reply_delta", "delta": delta}
                            )
                elif method == "item/completed":
                    item = params.get("item") or {}
                    if item.get("type") == "agentMessage":
                        final_text = str(item.get("text") or final_text)
                elif method == "thread/tokenUsage/updated":
                    breakdown = ((params.get("tokenUsage") or {}).get("last") or {})
                    usage = {
                        "prompt_tokens": int(breakdown.get("inputTokens") or 0),
                        "completion_tokens": int(breakdown.get("outputTokens") or 0),
                        "total_tokens": int(breakdown.get("totalTokens") or 0),
                        "prompt_cache_hit_tokens": int(
                            breakdown.get("cachedInputTokens") or 0
                        ),
                    }
                elif method == "turn/completed":
                    completed_turn = params.get("turn") or {}
                    if str(completed_turn.get("id") or "") != turn_id:
                        continue
                    if completed_turn.get("status") == "failed":
                        error = completed_turn.get("error") or {}
                        raise RuntimeError(
                            str(error.get("message") or "Codex model request failed")
                        )
                    for item in completed_turn.get("items") or []:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "agentMessage"
                        ):
                            final_text = str(item.get("text") or final_text)
                    break

            content = final_text or "".join(text_parts)
            if stream_callback:
                await stream_callback({"type": "reply_done", "response": content})
            return {
                "role": "assistant",
                "content": content,
                "usage": usage,
            }
        finally:
            self._notification_queues.pop(thread_id, None)

    async def close(self) -> None:
        process = self._process
        process_loop = self._process_loop
        self._process = None
        self._process_loop = None
        if process is not None and process.returncode is None:
            current_loop = asyncio.get_running_loop()
            if process_loop is current_loop:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    process.kill()
            else:
                # ``Process.wait()`` is bound to the loop that spawned it.
                # If that loop has gone away, kill and reap without awaiting
                # its foreign-loop Future.
                process.kill()
        for task in (
            self._reader_task,
            self._stderr_task,
            self._limits_refresh_task,
        ):
            if task is not None:
                try:
                    task.cancel()
                except RuntimeError:
                    # The task's owning loop may already be closed.
                    pass
        self._reader_task = None
        self._stderr_task = None
        self._limits_refresh_task = None
        self._notification_queues.clear()
        self._notification_backlog.clear()


def _provider_instructions(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> str:
    system_parts = [
        str(message.get("content") or "")
        for message in messages
        if message.get("role") in {"system", "developer"}
    ]
    tool_contract = ""
    if tools:
        tool_contract = (
            "\nAvailable Cyrene tools are listed below. Never execute them yourself. "
            "When a tool is needed, output only this DSML envelope and stop:\n"
            "<||DSML||tool_calls><||DSML||invoke name=\"TOOL_NAME\">"
            "<||DSML||parameter name=\"ARG_NAME\">JSON_VALUE"
            "</||DSML||parameter></||DSML||invoke></||DSML||tool_calls>\n"
            "Tool schemas:\n"
            + json.dumps(tools, ensure_ascii=False, default=str)
        )
    return (
        "You are the model backend for Cyrene. Follow the supplied conversation "
        "and return the next assistant message. Do not use Codex built-in tools, "
        "the filesystem, shell, browser, network tools, or subagents."
        + ("\nSystem instructions:\n" + "\n\n".join(system_parts) if system_parts else "")
        + tool_contract
    )


def _provider_input(messages: list[dict[str, Any]]) -> str:
    replay = [
        message
        for message in messages
        if message.get("role") not in {"system", "developer"}
    ]
    return (
        "Continue this conversation and produce only the next assistant message.\n"
        + json.dumps(replay, ensure_ascii=False, default=str)
    )


_client = CodexAppServer()


def get_codex_provider() -> CodexAppServer:
    return _client
