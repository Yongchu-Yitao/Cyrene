"""Thin execution boundary between model calls, Hooks, and Plugins."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .execution import bind_plugin_execution
from .plugin import Plugin, PluginCall, PluginCallResult, PluginContext
from .registry import PluginRegistry
from .validation import validate_plugin_arguments

logger = logging.getLogger(__name__)


def _dispatches_own_tool_hooks(plugin: Plugin) -> bool:
    """The toolbox gateway delegates Hook dispatch to its invoked target."""

    return plugin.kind == "tool" and plugin.name != "toolbox"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        reviewed = await self.review_batch((call,), context)
        prepared = reviewed[0]
        if isinstance(prepared, PluginCallResult):
            return prepared
        return await self.execute(prepared, context)

    async def review_batch(
        self,
        calls: tuple[PluginCall, ...],
        context: PluginContext | None = None,
    ) -> tuple[PreparedPluginCall | PluginCallResult, ...]:
        """Resolve every call and review all valid tool calls as one Hook batch."""

        context = context or PluginContext()
        prepared: list[PreparedPluginCall | PluginCallResult | None] = []
        review_positions: list[int] = []
        review_calls: list[tuple[str, dict[str, Any]]] = []
        for call in calls:
            try:
                plugin = self.registry.resolve(call.name)
                arguments = dict(call.arguments)
                validate_plugin_arguments(
                    plugin.name,
                    arguments,
                    plugin.input_schema,
                )
            except Exception as exc:
                prepared.append(
                    PluginCallResult(
                        call.id,
                        call.name,
                        False,
                        None,
                        str(exc),
                        _utc_now(),
                    )
                )
                continue
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
                        prepared[position] = PluginCallResult(
                            item.call.id,
                            item.call.name,
                            False,
                            None,
                            str(exc),
                            _utc_now(),
                        )
                    else:
                        prepared[position] = PreparedPluginCall(
                            item.call,
                            item.plugin,
                            reviewed_arguments,
                        )
        return tuple(item for item in prepared if item is not None)

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
        try:
            validate_plugin_arguments(
                plugin.name,
                arguments,
                plugin.input_schema,
            )
        except Exception as exc:
            return PluginCallResult(
                call.id,
                call.name,
                False,
                None,
                str(exc),
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
            error = f"Plugin timed out after {plugin.timeout_seconds:g} seconds"
            if _dispatches_own_tool_hooks(plugin):
                await self._post(context, call.name, arguments, None, False, error)
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
            if _dispatches_own_tool_hooks(plugin):
                await self._post(context, call.name, arguments, None, False, str(exc))
            return PluginCallResult(
                call.id,
                call.name,
                False,
                None,
                str(exc),
                _utc_now(),
            )

        if _dispatches_own_tool_hooks(plugin):
            await self._post(context, call.name, arguments, value, True, "")
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
            return
        try:
            await context.hooks.post_tool_use(
                name,
                arguments,
                value,
                success=success,
                error=error,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("PostToolUse dispatch failed for Plugin %s", name)


__all__ = ["PluginRuntime", "PreparedPluginCall"]
