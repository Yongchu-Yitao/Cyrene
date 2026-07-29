"""Codex OAuth provider backed by OpenAI's pinned Codex SDK runtime.

The app-server owns browser login, credential refresh, model discovery, and
ChatGPT-plan rate limits. Cyrene never reads ``~/.codex/auth.json`` or handles
OAuth access/refresh tokens directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable

from openai_codex import CodexConfig
from openai_codex.async_client import AsyncCodexClient
from openai_codex.generated.v2_all import GetAccountRateLimitsResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

CODEX_PROVIDER = "codex_oauth"
CODEX_BASE_URL = "codex://oauth"
_FIRST_UPSTREAM_SIGNAL_TIMEOUT_SECONDS = 35.0
_TRANSPORT_ERROR_KEYS = frozenset(
    {
        "httpConnectionFailed",
        "responseStreamConnectionFailed",
        "responseStreamDisconnected",
        "responseTooManyFailedAttempts",
    }
)


class CodexTransportError(RuntimeError):
    """A Codex upstream transport failed before a usable model response."""


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        dumped = value.model_dump(
            by_alias=True,
            exclude_none=True,
            mode="json",
        )
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _normalized_effort(value: str) -> str:
    effort = str(value or "").strip().lower()
    return (
        effort
        if effort
        in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
        else ""
    )


def _transport_error_kind(error: dict[str, Any]) -> str:
    info = error.get("codexErrorInfo")
    if not isinstance(info, dict):
        return ""
    return next((key for key in _TRANSPORT_ERROR_KEYS if key in info), "")


def _first_signal_timeout(request_timeout: float) -> float:
    raw = str(
        os.environ.get(
            "CYRENE_CODEX_FIRST_SIGNAL_TIMEOUT_SECONDS",
            _FIRST_UPSTREAM_SIGNAL_TIMEOUT_SECONDS,
        )
    ).strip()
    try:
        configured = float(raw)
    except ValueError:
        configured = _FIRST_UPSTREAM_SIGNAL_TIMEOUT_SECONDS
    return min(float(request_timeout), max(5.0, configured))


def _codex_sdk_config() -> CodexConfig:
    return CodexConfig(
        # Published SDK builds own a same-version Codex runtime. Do not pass
        # codex_bin: that would fall back to an unpinned PATH/ChatGPT.app
        # executable.
        config_overrides=("features.respect_system_proxy=true",),
        client_name="cyrene",
        client_title="Cyrene",
        client_version="1",
        experimental_api=True,
    )


class CodexAppServer:
    """Async facade over OpenAI's official pinned Codex Python SDK."""

    def __init__(self) -> None:
        self._client: AsyncCodexClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._start_lock = asyncio.Lock()
        self._limits_cache: tuple[float, dict[str, Any]] | None = None
        self._limits_refresh_task: asyncio.Task[dict[str, Any]] | None = None

    async def _ensure_started(self) -> None:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._client_loop is current_loop:
            return
        if self._client is not None:
            await self.close()
        async with self._start_lock:
            if self._client is not None and self._client_loop is current_loop:
                return
            client = AsyncCodexClient(_codex_sdk_config())
            try:
                await asyncio.wait_for(client.start(), timeout=15)
                metadata = await asyncio.wait_for(client.initialize(), timeout=15)
            except BaseException:
                await client.close()
                raise
            self._client = client
            self._client_loop = current_loop
            logger.info(
                "Codex SDK runtime started [sdk_runtime=%s system_proxy=true]",
                str(getattr(metadata, "user_agent", "") or "pinned"),
            )

    async def _ready_client(self) -> AsyncCodexClient:
        await self._ensure_started()
        if self._client is None:
            raise RuntimeError("Codex SDK client is unavailable")
        return self._client

    async def account(self, *, refresh: bool = False) -> dict[str, Any]:
        client = await self._ready_client()
        result = await asyncio.wait_for(
            client.account_read({"refreshToken": bool(refresh)}),
            timeout=20,
        )
        return _model_dump(result)

    async def start_login(self) -> dict[str, Any]:
        client = await self._ready_client()
        result = await asyncio.wait_for(
            client.account_login_start(
                {
                    "type": "chatgpt",
                    "appBrand": "codex",
                    "codexStreamlinedLogin": True,
                    "useHostedLoginSuccessPage": True,
                }
            ),
            timeout=20,
        )
        return _model_dump(result)

    async def logout(self) -> None:
        client = await self._ready_client()
        await asyncio.wait_for(client.account_logout(), timeout=20)

    async def models(self) -> list[dict[str, Any]]:
        client = await self._ready_client()
        result = await asyncio.wait_for(
            client.model_list(include_hidden=False),
            timeout=30,
        )
        payload = _model_dump(result)
        return [
            item
            for item in payload.get("data") or []
            if isinstance(item, dict) and not item.get("hidden")
        ]

    async def rate_limits(self) -> dict[str, Any]:
        client = await self._ready_client()
        result = await asyncio.wait_for(
            client.request(
                "account/rateLimits/read",
                {},
                response_model=GetAccountRateLimitsResponse,
            ),
            timeout=30,
        )
        normalized = _model_dump(result)
        self._limits_cache = (time.monotonic(), normalized)
        return normalized

    async def rate_limits_cached(self, *, max_age: float = 30) -> dict[str, Any]:
        cached = self._limits_cache
        if cached is not None and time.monotonic() - cached[0] <= max_age:
            return cached[1]
        return await self.rate_limits()

    async def rate_limits_stale_first(
        self, *, refresh_after: float = 30
    ) -> dict[str, Any]:
        """Return cached limits immediately and refresh an old snapshot in the background."""
        cached = self._limits_cache
        if cached is None:
            return await self.rate_limits()
        if time.monotonic() - cached[0] > refresh_after:
            self._schedule_rate_limits_refresh()
        return cached[1]

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

    async def snapshot(
        self,
        *,
        include_limits: bool = True,
        include_models: bool = True,
        stale_limits: bool = False,
    ) -> dict[str, Any]:
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
            model_request = self.models() if include_models else None
            limit_request = (
                self.rate_limits_stale_first()
                if include_limits and stale_limits
                else self.rate_limits()
                if include_limits
                else None
            )
            requests = [
                request
                for request in (model_request, limit_request)
                if request is not None
            ]
            results = (
                await asyncio.gather(*requests, return_exceptions=True)
                if requests
                else []
            )
            result_index = 0
            if include_models:
                model_result = results[result_index]
                result_index += 1
                if isinstance(model_result, BaseException):
                    errors["models"] = str(model_result)
                else:
                    models = model_result
            if include_limits:
                limit_result = results[result_index]
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
        transport_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Run one provider turn without exposing Codex's own host tools.

        Cyrene's tool loop remains authoritative. Tool schemas are described in
        the base instructions and Codex is asked to emit the existing DSML
        envelope, which the normal model-runtime parser converts back to
        OpenAI-style tool calls.
        """
        client = await self._ready_client()
        instructions = _provider_instructions(messages, tools)
        effort = _normalized_effort(reasoning_effort)
        thread_result = await asyncio.wait_for(
            client.thread_start(
                {
                    "model": model,
                    "baseInstructions": instructions,
                    "developerInstructions": (
                        "Act only as Cyrene's language-model backend. "
                        "Do not inspect files, run commands, browse, or call built-in tools."
                    ),
                    "ephemeral": True,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                }
            ),
            timeout=min(timeout, 30),
        )
        thread = _model_dump(thread_result).get("thread") or {}
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise RuntimeError("Codex did not create a provider thread")

        turn_id = ""
        notification_task: asyncio.Task[Any] | None = None

        async def emit_transport(
            status: str,
            *,
            message: str = "",
            kind: str = "",
            will_retry: bool = False,
        ) -> None:
            event = {
                "type": "provider_transport",
                "provider": CODEX_PROVIDER,
                "transport": "responses",
                "status": status,
                "message": message,
                "error_kind": kind,
                "will_retry": bool(will_retry),
                "thread_id": thread_id,
                "turn_id": turn_id,
                "reasoning_effort": effort,
            }
            if transport_callback is not None:
                try:
                    await transport_callback(event)
                except Exception:
                    logger.debug(
                        "Failed to publish Codex transport telemetry",
                        exc_info=True,
                    )

        async def interrupt_turn() -> None:
            if not turn_id:
                return
            try:
                await asyncio.wait_for(
                    client.turn_interrupt(thread_id, turn_id),
                    timeout=3,
                )
            except Exception:
                logger.debug(
                    "Failed to interrupt Codex provider turn %s",
                    turn_id,
                    exc_info=True,
                )

        async def settle_notification_wait() -> None:
            """Wake the SDK's blocking notification worker before loop shutdown."""
            nonlocal notification_task
            task = notification_task
            notification_task = None
            if task is None:
                return
            if not task.done():
                try:
                    # An interrupt normally produces turn/completed, which
                    # releases the SDK's thread-backed queue wait.
                    await asyncio.wait_for(asyncio.shield(task), timeout=3)
                except TimeoutError:
                    # The transport itself may be gone, so no completion can
                    # arrive. Closing the official client terminates app-server
                    # and makes its router wake every blocked waiter.
                    if isinstance(client, AsyncCodexClient):
                        if self._client is client:
                            self._client = None
                            self._client_loop = None
                        try:
                            await client.close()
                        except Exception:
                            logger.debug(
                                "Failed to retire a stalled Codex SDK client",
                                exc_info=True,
                            )
                    else:
                        task.cancel()
                except BaseException:
                    pass
            if not task.done():
                task.cancel()
            try:
                await task
            except BaseException:
                pass

        try:
            user_input = _provider_input(messages)
            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": user_input}],
                "model": model,
                # Ask only for the provider-supported summary. Raw private
                # reasoning text is deliberately not exposed to Cyrene.
                "summary": "auto",
            }
            if effort:
                turn_params["effort"] = effort
            logger.info(
                "Starting Codex turn [model=%s effort=%s proxy=system]",
                model,
                effort or "model-default",
            )
            turn_result = await asyncio.wait_for(
                client.turn_start(
                    thread_id,
                    [{"type": "text", "text": user_input}],
                    turn_params,
                ),
                timeout=min(timeout, 30),
            )
            turn = _model_dump(turn_result).get("turn") or {}
            turn_id = str(turn.get("id") or "")
            if not turn_id:
                raise RuntimeError("Codex did not start a provider turn")

            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            final_text = ""
            usage: dict[str, Any] = {}
            reasoning_started = False
            upstream_signal_seen = False
            if stream_callback:
                await stream_callback({"type": "reply_start"})
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            first_signal_timeout = _first_signal_timeout(timeout)
            first_signal_deadline = loop.time() + first_signal_timeout
            while True:
                now = loop.time()
                remaining = deadline - now
                if remaining <= 0:
                    await interrupt_turn()
                    await emit_transport(
                        "timed_out",
                        message=f"Codex request exceeded {timeout:.0f}s",
                    )
                    raise TimeoutError("Codex model request timed out")
                notification_timeout = remaining
                if not upstream_signal_seen:
                    first_signal_remaining = first_signal_deadline - now
                    if first_signal_remaining <= 0:
                        await interrupt_turn()
                        message = (
                            "Codex produced no upstream model signal within "
                            f"{first_signal_timeout:.0f}s"
                        )
                        logger.warning(message)
                        await emit_transport("timed_out", message=message)
                        raise CodexTransportError(message)
                    notification_timeout = min(
                        notification_timeout,
                        first_signal_remaining,
                    )
                notification_task = asyncio.create_task(
                    client.next_turn_notification(turn_id)
                )
                completed, _ = await asyncio.wait(
                    {notification_task},
                    timeout=notification_timeout,
                )
                if not completed:
                    await interrupt_turn()
                    await settle_notification_wait()
                    if not upstream_signal_seen:
                        message = (
                            "Codex produced no upstream model signal within "
                            f"{notification_timeout:.0f}s"
                        )
                        logger.warning(message)
                        await emit_transport("timed_out", message=message)
                        raise CodexTransportError(message)
                    raise TimeoutError("Codex model request timed out")
                notification = notification_task.result()
                notification_task = None

                method = str(notification.method or "")
                params = _model_dump(notification.payload)
                if method == "item/agentMessage/delta":
                    delta = str(params.get("delta") or "")
                    if delta:
                        if not upstream_signal_seen:
                            upstream_signal_seen = True
                            await emit_transport("connected")
                        text_parts.append(delta)
                        if stream_callback:
                            await stream_callback(
                                {"type": "reply_delta", "delta": delta}
                            )
                elif method == "item/reasoning/summaryTextDelta":
                    delta = str(params.get("delta") or "")
                    if delta:
                        if not upstream_signal_seen:
                            upstream_signal_seen = True
                            await emit_transport("connected")
                        if stream_callback and not reasoning_started:
                            await stream_callback({"type": "reasoning_start"})
                        reasoning_started = True
                        reasoning_parts.append(delta)
                        if stream_callback:
                            await stream_callback(
                                {"type": "reasoning_delta", "delta": delta}
                            )
                elif method == "error":
                    error = params.get("error") or {}
                    error = error if isinstance(error, dict) else {}
                    message = str(error.get("message") or "Codex provider error")
                    will_retry = bool(params.get("willRetry"))
                    kind = _transport_error_kind(error)
                    logger.warning(
                        "Codex upstream error [kind=%s will_retry=%s model=%s effort=%s]: %s",
                        kind or "unknown",
                        will_retry,
                        model,
                        effort or "model-default",
                        message,
                    )
                    await emit_transport(
                        "retrying" if will_retry else "failed",
                        message=message,
                        kind=kind,
                        will_retry=will_retry,
                    )
                    if kind:
                        # Cyrene owns cross-provider fallback. Do not also pay
                        # Codex's internal multi-retry budget for a broken
                        # upstream transport.
                        await interrupt_turn()
                        raise CodexTransportError(message)
                    if not will_retry:
                        raise RuntimeError(message)
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
                    if not upstream_signal_seen:
                        upstream_signal_seen = True
                        await emit_transport("connected")
                    for item in completed_turn.get("items") or []:
                        if (
                            isinstance(item, dict)
                            and item.get("type") == "agentMessage"
                        ):
                            final_text = str(item.get("text") or final_text)
                    break

            content = final_text or "".join(text_parts)
            reasoning_content = "".join(reasoning_parts)
            if reasoning_started and stream_callback:
                await stream_callback(
                    {
                        "type": "reasoning_done",
                        "response": reasoning_content,
                    }
                )
            if stream_callback:
                await stream_callback({"type": "reply_done", "response": content})
            response = {
                "role": "assistant",
                "content": content,
                "usage": usage,
            }
            if reasoning_content:
                response["reasoning_content"] = reasoning_content
            return response
        except asyncio.CancelledError:
            # Keep cancellation responsive without orphaning the SDK's
            # thread-backed queue read in the event loop's default executor.
            await interrupt_turn()
            await settle_notification_wait()
            raise
        finally:
            await settle_notification_wait()
            if turn_id:
                client.unregister_turn_notifications(turn_id)

    async def close(self) -> None:
        client = self._client
        self._client = None
        self._client_loop = None
        if client is not None:
            try:
                await client.close()
            except RuntimeError:
                logger.debug("Codex SDK client close crossed event loops")
        if self._limits_refresh_task is not None:
            self._limits_refresh_task.cancel()
        self._limits_refresh_task = None


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
