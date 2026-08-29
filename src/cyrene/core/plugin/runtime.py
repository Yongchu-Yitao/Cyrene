"""Thin execution boundary between model calls, Hooks, and Plugins."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..observability import log_operation, operation
from .execution import bind_plugin_execution
from .context import plugin_localized
from .plugin import Plugin, PluginCall, PluginCallResult, PluginContext
from .registry import PluginNotFoundError, PluginRegistry, PluginUnavailableError
from .validation import (
    PluginInputValidationError,
    PluginSchemaError,
    validate_plugin_arguments,
)

logger = logging.getLogger(__name__)


def _dispatches_own_tool_hooks(plugin: Plugin) -> bool:
    """The toolbox gateway delegates Hook dispatch to its invoked target."""

    return plugin.kind == "tool" and plugin.name != "toolbox"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _context_fields(context: PluginContext) -> dict[str, Any]:
    return {
        "workspace": str(context.workspace) if context.workspace is not None else None,
        "tree_id": context.tree_id,
        "node_id": context.node_id,
        "context_data": dict(context.data),
    }


def _context_agent_id(context: PluginContext) -> str:
    """Resolve the calling Agent identity from the stable Plugin context."""

    direct = str(context.data.get("agent_id") or "").strip()
    if direct:
        return direct
    run_context = context.data.get("run_context")
    if isinstance(run_context, Mapping):
        nested = str(run_context.get("agent_id") or "").strip()
        if nested:
            return nested
    return "main"


def _validation_error_text(context: PluginContext, exc: Exception) -> str:
    english = str(exc) or "Plugin call validation failed."
    if isinstance(exc, PluginInputValidationError):
        chinese = "插件参数无效。"
    elif isinstance(exc, PluginSchemaError):
        chinese = "插件输入规则无效。"
    elif isinstance(exc, PluginNotFoundError):
        chinese = "未找到请求的插件。"
    elif isinstance(exc, PluginUnavailableError):
        chinese = "请求的插件当前不可用。"
    else:
        chinese = "无法验证插件调用。"
    return plugin_localized(context, english, chinese)


@dataclass(frozen=True, slots=True)
class PreparedPluginCall:
    """A resolved call whose PreToolUse review has completed."""

    call: PluginCall
    plugin: Plugin
    arguments: dict[str, Any]


class PluginRuntime:
    """Resolve one Plugin, run tree Hooks, and return its opaque result."""

    def __init__(self, registry: PluginRegistry) -> None:
        self.registry = registry

    async def run(
        self,
        call: PluginCall,
        context: PluginContext | None = None,
    ) -> PluginCallResult:
        context = context or PluginContext()
        with operation(
            logger,
            "plugin.runtime",
            "run",
            call_id=call.id,
            plugin=call.name,
            arguments=dict(call.arguments),
            **_context_fields(context),
        ) as op:
            reviewed = await self.review_batch((call,), context)
            prepared = reviewed[0]
            if isinstance(prepared, PluginCallResult):
                op.finish(success=False, error=prepared.error, rejected=True)
                return prepared
            result = await self.execute(prepared, context)
            op.finish(
                success=result.success,
                error=result.error,
                result=result.value,
                rejected=False,
            )
            return result

    async def review_batch(
        self,
        calls: tuple[PluginCall, ...],
        context: PluginContext | None = None,
    ) -> tuple[PreparedPluginCall | PluginCallResult, ...]:
        """Resolve every call and review all valid tool calls as one Hook batch."""

        context = context or PluginContext()
        with operation(
            logger,
            "plugin.runtime",
            "review_batch",
            calls=[
                {"call_id": call.id, "plugin": call.name, "arguments": dict(call.arguments)}
                for call in calls
            ],
            **_context_fields(context),
        ) as op:
            prepared: list[PreparedPluginCall | PluginCallResult | None] = []
            review_positions: list[int] = []
            review_calls: list[tuple[str, dict[str, Any]]] = []
            for call in calls:
                try:
                    plugin = self.registry.resolve(
                        call.name,
                        agent_id=_context_agent_id(context),
                    )
                    arguments = dict(call.arguments)
                    validate_plugin_arguments(
                        plugin.name,
                        arguments,
                        plugin.input_schema,
                    )
                except Exception as exc:
                    log_operation(
                        logger,
                        "plugin.runtime",
                        "validate",
                        phase="rejected",
                        call_id=call.id,
                        plugin=call.name,
                        arguments=dict(call.arguments),
                        error=exc,
                        **_context_fields(context),
                    )
                    prepared.append(
                        PluginCallResult(
                            call.id,
                            call.name,
                            False,
                            None,
                            _validation_error_text(context, exc),
                            _utc_now(),
                        )
                    )
                    continue
                log_operation(
                    logger,
                    "plugin.runtime",
                    "validate",
                    phase="accepted",
                    call_id=call.id,
                    plugin=call.name,
                    plugin_kind=plugin.kind,
                    arguments=arguments,
                    **_context_fields(context),
                )
                item = PreparedPluginCall(call, plugin, arguments)
                prepared.append(item)
                if _dispatches_own_tool_hooks(plugin) and context.hooks is not None:
                    review_positions.append(len(prepared) - 1)
                    review_calls.append((call.name, dict(call.arguments)))

            if review_calls:
                decisions = await context.hooks.pre_tool_use_batch(tuple(review_calls))
                for position, decision in zip(review_positions, decisions):
                    item = prepared[position]
                    assert isinstance(item, PreparedPluginCall)
                    if isinstance(decision, BaseException):
                        log_operation(
                            logger,
                            "plugin.runtime",
                            "pre_tool_review",
                            phase="rejected",
                            call_id=item.call.id,
                            plugin=item.call.name,
                            error=decision,
                            **_context_fields(context),
                        )
                        prepared[position] = PluginCallResult(
                            item.call.id,
                            item.call.name,
                            False,
                            None,
                            str(decision),
                            _utc_now(),
                        )
                    else:
                        try:
                            reviewed_arguments = dict(decision)
                            validate_plugin_arguments(
                                item.plugin.name,
                                reviewed_arguments,
                                item.plugin.input_schema,
                            )
                        except Exception as exc:
                            log_operation(
                                logger,
                                "plugin.runtime",
                                "pre_tool_review",
                                phase="rejected",
                                call_id=item.call.id,
                                plugin=item.call.name,
                                arguments=decision,
                                error=exc,
                                **_context_fields(context),
                            )
                            prepared[position] = PluginCallResult(
                                item.call.id,
                                item.call.name,
                                False,
                                None,
                                _validation_error_text(context, exc),
                                _utc_now(),
                            )
                        else:
                            log_operation(
                                logger,
                                "plugin.runtime",
                                "pre_tool_review",
                                phase="accepted",
                                call_id=item.call.id,
                                plugin=item.call.name,
                                arguments=reviewed_arguments,
                                modified=reviewed_arguments != item.arguments,
                                **_context_fields(context),
                            )
                            prepared[position] = PreparedPluginCall(
                                item.call,
                                item.plugin,
                                reviewed_arguments,
                            )
            result = tuple(item for item in prepared if item is not None)
            op.finish(
                result_count=len(result),
                accepted=sum(isinstance(item, PreparedPluginCall) for item in result),
                rejected=sum(isinstance(item, PluginCallResult) for item in result),
            )
            return result

    async def execute(
        self,
        prepared: PreparedPluginCall,
        context: PluginContext | None = None,
    ) -> PluginCallResult:
        """Execute one already-reviewed call and publish PostToolUse."""

        context = context or PluginContext()
        call = prepared.call
        plugin = prepared.plugin
        arguments = dict(prepared.arguments)
        with operation(
            logger,
            "plugin.runtime",
            "execute",
            call_id=call.id,
            plugin=call.name,
            plugin_kind=plugin.kind,
            arguments=arguments,
            timeout_seconds=plugin.timeout_seconds,
            allow_parallel=plugin.allow_parallel,
            **_context_fields(context),
        ) as op:
            try:
                self.registry.resolve(
                    plugin.name,
                    agent_id=_context_agent_id(context),
                )
                validate_plugin_arguments(
                    plugin.name,
                    arguments,
                    plugin.input_schema,
                )
            except Exception as exc:
                op.finish(success=False, rejected=True, error=exc)
                return PluginCallResult(
                    call.id,
                    call.name,
                    False,
                    None,
                    _validation_error_text(context, exc),
                    _utc_now(),
                )
            try:
                with bind_plugin_execution(self, call, context):
                    if plugin.timeout_seconds is not None and not inspect.iscoroutinefunction(
                        plugin.handler
                    ):
                        value = await asyncio.wait_for(
                            asyncio.to_thread(plugin.handler, arguments, context),
                            timeout=plugin.timeout_seconds,
                        )
                    else:
                        value = plugin.handler(arguments, context)
                        if inspect.isawaitable(value):
                            if plugin.timeout_seconds is None:
                                value = await value
                            else:
                                value = await asyncio.wait_for(
                                    value,
                                    timeout=plugin.timeout_seconds,
                                )
                    if inspect.isawaitable(value):
                        value = await value
            except asyncio.TimeoutError:
                error = plugin_localized(
                    context,
                    "Plugin timed out after {seconds:g} seconds.",
                    "插件在 {seconds:g} 秒后超时。",
                    seconds=plugin.timeout_seconds,
                )
                if _dispatches_own_tool_hooks(plugin):
                    await self._post(context, call.name, arguments, None, False, error)
                op.finish(success=False, timed_out=True, error=error)
                return PluginCallResult(
                    call.id,
                    call.name,
                    False,
                    None,
                    error,
                    _utc_now(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_operation(
                    logger,
                    "plugin.runtime",
                    "handler",
                    phase="failed",
                    level=logging.ERROR,
                    exc_info=True,
                    call_id=call.id,
                    plugin=call.name,
                    error=exc,
                    **_context_fields(context),
                )
                error = plugin_localized(
                    context,
                    "Plugin execution failed.",
                    "插件执行失败。",
                )
                if _dispatches_own_tool_hooks(plugin):
                    await self._post(context, call.name, arguments, None, False, error)
                op.finish(success=False, error=exc)
                return PluginCallResult(
                    call.id,
                    call.name,
                    False,
                    None,
                    error,
                    _utc_now(),
                )

            if _dispatches_own_tool_hooks(plugin):
                await self._post(context, call.name, arguments, value, True, "")
            op.finish(success=True, result=value)
            return PluginCallResult(call.id, call.name, True, value, "", _utc_now())

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: PluginContext | None = None,
        *,
        call_id: str | None = None,
    ) -> PluginCallResult:
        call = (
            PluginCall(name=name, arguments=arguments)
            if call_id is None
            else PluginCall(name=name, arguments=arguments, id=call_id)
        )
        return await self.run(call, context)

    async def call_canonical(
        self,
        canonical_name: str,
        arguments: dict[str, Any],
        context: PluginContext | None = None,
        *,
        call_id: str | None = None,
    ) -> PluginCallResult:
        """Invoke an application-owned tool by its stable canonical identity."""

        try:
            registered = self.registry.registered_by_canonical(canonical_name)
        except Exception as exc:
            return PluginCallResult(
                call_id or f"call_{canonical_name}",
                str(canonical_name),
                False,
                None,
                _validation_error_text(context or PluginContext(), exc),
                _utc_now(),
            )
        return await self.call(
            registered.plugin.name,
            arguments,
            context,
            call_id=call_id,
        )

    @staticmethod
    async def _post(
        context: PluginContext,
        name: str,
        arguments: dict[str, Any],
        value: Any,
        success: bool,
        error: str,
    ) -> None:
        if context.hooks is None:
            log_operation(
                logger,
                "plugin.runtime",
                "post_tool_use",
                phase="skipped",
                plugin=name,
                reason="no_hooks",
                success=success,
                **_context_fields(context),
            )
            return
        with operation(
            logger,
            "plugin.runtime",
            "post_tool_use",
            plugin=name,
            arguments=arguments,
            result=value,
            success=success,
            error=error,
            **_context_fields(context),
        ) as op:
            try:
                results = await context.hooks.post_tool_use(
                    name,
                    arguments,
                    value,
                    success=success,
                    error=error,
                )
                op.finish(hook_result_count=len(results))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                op.finish(dispatch_success=False, error=exc)
                log_operation(
                    logger,
                    "plugin.runtime",
                    "post_tool_use_dispatch",
                    phase="failed_open",
                    level=logging.ERROR,
                    exc_info=True,
                    plugin=name,
                    error=exc,
                    **_context_fields(context),
                )


__all__ = ["PluginRuntime", "PreparedPluginCall"]
