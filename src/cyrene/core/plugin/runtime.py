"""Thin execution boundary between model calls, Hooks, and Plugins."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..hook import HookAwaitingUser
from ..observability import log_operation, operation
from .execution import bind_plugin_execution
from .context import plugin_language, plugin_localized
from .plugin import Plugin, PluginCall, PluginCallResult, PluginContext
from .registry import PluginNotFoundError, PluginRegistry, PluginUnavailableError
from .validation import (
    PluginInputValidationError,
    PluginSchemaError,
    normalize_plugin_arguments,
    validate_plugin_arguments,
)

logger = logging.getLogger(__name__)


def _dispatches_own_tool_hooks(plugin: Plugin) -> bool:
    """The toolbox gateway delegates Hook dispatch to its invoked target."""

    return (
        plugin.kind == "tool"
        and plugin.name != "toolbox"
        and plugin.metadata.get("permission_review", True) is not False
    )


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


def plugin_context_is_read_only(context: PluginContext) -> bool:
    """Resolve the execution policy shared by direct and Toolbox calls."""

    if context.data.get("read_only") is True:
        return True
    run_context = context.data.get("run_context")
    return isinstance(run_context, Mapping) and run_context.get("read_only") is True


def _validation_error_text(context: PluginContext, exc: Exception) -> str:
    english = str(exc) or "Plugin call validation failed."
    if isinstance(exc, PluginInputValidationError):
        chinese = f"插件参数无效：{english}"
    elif isinstance(exc, PluginSchemaError):
        chinese = f"插件输入规则无效：{english}"
    elif isinstance(exc, PluginNotFoundError):
        chinese = "未找到请求的插件。"
    elif isinstance(exc, PluginUnavailableError):
        chinese = "请求的插件当前不可用。"
    else:
        chinese = "无法验证插件调用。"
    # Validation messages can contain literal JSON object representations.
    # They are already complete strings, so they must not pass through the
    # localization template formatter where braces are interpreted as fields.
    return chinese if plugin_language(context) == "zh" else english


def _execution_error_text(
    context: PluginContext,
    plugin: Plugin,
    exc: Exception,
) -> str:
    public_error = str(exc).strip()
    if plugin.metadata.get("public_errors") is True and public_error:
        return public_error
    return plugin_localized(
        context,
        "Plugin execution failed.",
        "插件执行失败。",
    )


def _execution_error_details(exc: BaseException) -> dict[str, Any]:
    """Copy explicitly public structured details without exposing raw exceptions."""

    exporter = getattr(exc, "as_error_details", None)
    if not callable(exporter):
        return {}
    try:
        details = exporter()
    except Exception:
        return {}
    return dict(details) if isinstance(details, Mapping) else {}


@dataclass(frozen=True, slots=True)
class PreparedPluginCall:
    """A resolved call whose PreToolUse review has completed."""

    call: PluginCall
    plugin: Plugin
    arguments: dict[str, Any]
    permission_boundary: dict[str, Any] | None = None
    argument_repairs: tuple[dict[str, str], ...] = ()


async def _permission_boundary(
    plugin: Plugin,
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any] | None:
    if plugin.permission_boundary is None:
        return None
    raw_permission = plugin.permission_boundary(arguments, context)
    if inspect.isawaitable(raw_permission):
        raw_permission = await raw_permission
    if raw_permission is None:
        return None
    if not isinstance(raw_permission, Mapping):
        raise TypeError("Plugin permission_boundary must return an object or None")
    return dict(raw_permission)


async def _validated_call(
    registry: PluginRegistry,
    call: PluginCall,
    context: PluginContext,
) -> tuple[Plugin, Any, dict[str, Any], dict[str, Any] | None]:
    plugin = registry.resolve(call.name, agent_id=_context_agent_id(context))
    normalization = normalize_plugin_arguments(call.arguments, plugin.input_schema)
    arguments = normalization.arguments
    validate_plugin_arguments(plugin.name, arguments, plugin.input_schema)
    if (
        plugin_context_is_read_only(context)
        and not plugin.permits_read_only(arguments)
    ):
        raise PluginUnavailableError(
            f"Plugin is unavailable in a read-only context: {plugin.name}"
        )
    permission_request = await _permission_boundary(plugin, arguments, context)
    return plugin, normalization, arguments, permission_request


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
            review_permissions: list[dict[str, Any] | None] = []
            for call in calls:
                normalization = None
                try:
                    plugin, normalization, arguments, permission_request = (
                        await _validated_call(self.registry, call, context)
                    )
                except Exception as exc:
                    log_operation(
                        logger,
                        "plugin.runtime",
                        "validate",
                        phase="rejected",
                        call_id=call.id,
                        plugin=call.name,
                        original_arguments=dict(call.arguments),
                        arguments=(
                            normalization.arguments
                            if normalization is not None
                            else dict(call.arguments)
                        ),
                        argument_repairs=(
                            [repair.as_dict() for repair in normalization.repairs]
                            if normalization is not None
                            else []
                        ),
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
                    original_arguments=dict(call.arguments),
                    arguments=arguments,
                    argument_repairs=[
                        repair.as_dict() for repair in normalization.repairs
                    ],
                    **_context_fields(context),
                )
                item = PreparedPluginCall(
                    call=call,
                    plugin=plugin,
                    arguments=arguments,
                    permission_boundary=permission_request,
                    argument_repairs=tuple(
                        repair.as_dict() for repair in normalization.repairs
                    ),
                )
                prepared.append(item)
                if _dispatches_own_tool_hooks(plugin) and context.hooks is not None:
                    review_positions.append(len(prepared) - 1)
                    review_calls.append((call.name, dict(arguments)))
                    review_permissions.append(permission_request)

            if review_calls:
                decisions = await context.hooks.pre_tool_use_batch(
                    tuple(review_calls),
                    permissions=tuple(review_permissions),
                )
                for position, decision in zip(review_positions, decisions):
                    item = prepared[position]
                    assert isinstance(item, PreparedPluginCall)
                    if isinstance(decision, HookAwaitingUser):
                        question = (
                            dict(decision.question)
                            if isinstance(decision.question, Mapping)
                            else {}
                        )
                        prepared[position] = PluginCallResult(
                            item.call.id,
                            item.call.name,
                            True,
                            question,
                            "",
                            _utc_now(),
                        )
                        continue
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
                            reviewed_normalization = normalize_plugin_arguments(
                                dict(decision),
                                item.plugin.input_schema,
                            )
                            reviewed_arguments = reviewed_normalization.arguments
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
                                argument_repairs=[
                                    repair.as_dict()
                                    for repair in reviewed_normalization.repairs
                                ],
                                **_context_fields(context),
                            )
                            prepared[position] = PreparedPluginCall(
                                call=item.call,
                                plugin=item.plugin,
                                arguments=reviewed_arguments,
                                permission_boundary=item.permission_boundary,
                                argument_repairs=(
                                    item.argument_repairs
                                    + tuple(
                                        repair.as_dict()
                                        for repair in reviewed_normalization.repairs
                                    )
                                ),
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
            argument_repairs=list(prepared.argument_repairs),
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
                current_boundary = await _permission_boundary(
                    plugin,
                    arguments,
                    context,
                )
                if current_boundary != prepared.permission_boundary:
                    raise PermissionError(
                        "Plugin permission boundary changed after review; retry the exact call."
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
                error = _execution_error_text(context, plugin, exc)
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
                    _execution_error_details(exc),
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
