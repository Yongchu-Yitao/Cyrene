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
from .circuit import PluginCircuitBreaker
from .plugin import (
    Plugin,
    PluginCall,
    PluginCallResult,
    PluginContext,
    PluginExecutionError,
    PluginFailure,
)
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


def _context_run_id(context: PluginContext) -> str:
    direct = str(context.data.get("run_id") or "").strip()
    if direct:
        return direct
    run_context = context.data.get("run_context")
    if not isinstance(run_context, Mapping):
        return ""
    return str(
        run_context.get("round_id") or run_context.get("run_id") or ""
    ).strip()


def _validation_failure(context: PluginContext, exc: Exception) -> PluginFailure:
    if isinstance(exc, PluginInputValidationError):
        code = "plugin_invalid_arguments"
        retryable = True
        retry_scope = "different_arguments"
    elif isinstance(exc, PluginSchemaError):
        code = "plugin_invalid_schema"
        retryable = False
        retry_scope = "after_config_change"
    elif isinstance(exc, PluginNotFoundError):
        code = "plugin_not_found"
        retryable = False
        retry_scope = "after_config_change"
    elif isinstance(exc, PluginUnavailableError):
        code = "plugin_unavailable"
        retryable = False
        retry_scope = "after_config_change"
    else:
        code = "plugin_validation_failed"
        retryable = False
        retry_scope = "never"
    return PluginFailure(
        error_code=code,
        message=_validation_error_text(context, exc),
        retryable=retryable,
        retry_scope=retry_scope,
    )


def plugin_context_is_read_only(context: PluginContext) -> bool:
    """Resolve the execution policy shared by direct and Toolbox calls."""

    if context.data.get("read_only") is True:
        return True
    run_context = context.data.get("run_context")
    return isinstance(run_context, Mapping) and run_context.get("read_only") is True


def _validation_error_text(context: PluginContext, exc: Exception) -> str:
    english = str(exc) or "Plugin call validation failed."
    if isinstance(exc, PluginInputValidationError):
        chinese = exc.localized_message("zh")
    elif isinstance(exc, PluginSchemaError):
        chinese = "插件输入规则无效。"
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


@dataclass(frozen=True, slots=True)
class PreparedPluginCall:
    """A resolved call whose PreToolUse review has completed."""

    call: PluginCall
    plugin: Plugin
    arguments: dict[str, Any]
    permission_boundary: dict[str, Any] | None = None
    argument_repairs: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedPluginCall:
    """One canonical Plugin call plus its effective resource target."""

    call: PluginCall
    plugin: Plugin
    arguments: dict[str, Any]
    effective_plugin: Plugin
    effective_arguments: dict[str, Any]
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
    runtime: PluginRuntime,
    call: PluginCall,
    context: PluginContext,
) -> tuple[Plugin, NormalizedPluginCall, dict[str, Any], dict[str, Any] | None]:
    normalized = runtime.normalize_call(call, context)
    plugin = normalized.plugin
    arguments = normalized.arguments
    validate_plugin_arguments(plugin.name, arguments, plugin.input_schema)
    if (
        plugin_context_is_read_only(context)
        and not plugin.permits_read_only(arguments)
    ):
        raise PluginUnavailableError(
            f"Plugin is unavailable in a read-only context: {plugin.name}"
        )
    permission_request = await _permission_boundary(plugin, arguments, context)
    return plugin, normalized, arguments, permission_request


class PluginRuntime:
    """Resolve one Plugin, run tree Hooks, and return its opaque result."""

    def __init__(self, registry: PluginRegistry) -> None:
        self.registry = registry
        self.circuits = PluginCircuitBreaker()

    def circuit_failure(
        self,
        name: str,
        run_id: str,
        *,
        agent_id: str = "main",
    ) -> PluginFailure | None:
        try:
            canonical_name = self.registry.resolve(
                name,
                agent_id=agent_id,
            ).canonical_name
        except Exception:
            canonical_name = str(name or "")
        return self.circuits.failure_for(run_id, canonical_name)

    def restore_circuit(
        self,
        name: str,
        run_id: str,
        failure: PluginFailure | Mapping[str, object] | None,
        *,
        agent_id: str = "main",
    ) -> None:
        try:
            canonical_name = self.registry.resolve(
                name,
                agent_id=agent_id,
            ).canonical_name
        except Exception:
            canonical_name = str(name or "")
        self.circuits.record(run_id, canonical_name, failure)

    def _blocked_failure(
        self,
        plugin: Plugin,
        context: PluginContext,
    ) -> PluginFailure | None:
        return self.circuits.blocked_failure(
            _context_run_id(context),
            plugin.canonical_name,
        )

    def _record_failure(
        self,
        plugin: Plugin,
        context: PluginContext,
        failure: PluginFailure | None,
    ) -> None:
        self.circuits.record(
            _context_run_id(context),
            plugin.canonical_name,
            failure,
        )

    def normalize_call(
        self,
        call: PluginCall,
        context: PluginContext | None = None,
    ) -> NormalizedPluginCall:
        """Canonicalize one call chain through the resolved Plugin schemas.

        Model Provider Plugins may mark only their outer arguments canonical.
        Deferred toolbox arguments are normalized here against the dynamically
        resolved target schema, so Session and proxy Plugins can consume the
        same result without implementing their own repair paths.
        """

        context = context or PluginContext()
        agent_id = _context_agent_id(context)
        plugin = self.registry.resolve(call.name, agent_id=agent_id)
        repairs = tuple(dict(item) for item in call.argument_repairs)
        if call.arguments_normalized:
            arguments = dict(call.arguments)
        else:
            normalization = normalize_plugin_arguments(
                call.arguments,
                plugin.input_schema,
            )
            arguments = normalization.arguments
            repairs += tuple(repair.as_dict() for repair in normalization.repairs)

        effective_plugin = plugin
        effective_arguments = arguments
        nested_normalized = call.nested_arguments_normalized
        if (
            plugin.name == "toolbox"
            and str(arguments.get("operation") or "") == "invoke"
            and str(arguments.get("name") or "").strip()
        ):
            try:
                target_plugin = self.registry.resolve(
                    str(arguments.get("name") or ""),
                    agent_id=agent_id,
                )
            except (PluginNotFoundError, PluginUnavailableError):
                # Preserve toolbox's opaque failure boundary for disappeared or
                # inaccessible deferred Plugins. The handler refreshes the live
                # registry and reports the normal generic execution failure.
                target_plugin = None
            if target_plugin is not None:
                effective_plugin = target_plugin
                nested_arguments = arguments.get("arguments") or {}
                if not isinstance(nested_arguments, Mapping):
                    raise TypeError("toolbox invoke arguments must be an object")
                if nested_normalized:
                    effective_arguments = dict(nested_arguments)
                else:
                    nested = normalize_plugin_arguments(
                        nested_arguments,
                        effective_plugin.input_schema,
                    )
                    effective_arguments = nested.arguments
                    repairs += tuple(repair.as_dict() for repair in nested.repairs)
                arguments["arguments"] = effective_arguments
                nested_normalized = True

        canonical_call = PluginCall(
            name=call.name,
            arguments=arguments,
            id=call.id,
            arguments_normalized=True,
            nested_arguments_normalized=nested_normalized,
            argument_repairs=repairs,
        )
        return NormalizedPluginCall(
            call=canonical_call,
            plugin=plugin,
            arguments=arguments,
            effective_plugin=effective_plugin,
            effective_arguments=effective_arguments,
            argument_repairs=repairs,
        )

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
                        await _validated_call(self, call, context)
                    )
                except Exception as exc:
                    failure = _validation_failure(context, exc)
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
                            list(normalization.argument_repairs)
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
                            failure.message,
                            _utc_now(),
                            failure,
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
                        dict(repair) for repair in normalization.argument_repairs
                    ],
                    **_context_fields(context),
                )
                item = PreparedPluginCall(
                    call=normalization.call,
                    plugin=plugin,
                    arguments=arguments,
                    permission_boundary=permission_request,
                    argument_repairs=tuple(
                        dict(repair) for repair in normalization.argument_repairs
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
                        failure = PluginFailure(
                            error_code="plugin_review_rejected",
                            message=str(decision),
                            retryable=False,
                            retry_scope="never",
                        )
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
                            failure.message,
                            _utc_now(),
                            failure,
                        )
                    else:
                        try:
                            reviewed = self.normalize_call(
                                PluginCall(
                                    name=item.call.name,
                                    arguments=dict(decision),
                                    id=item.call.id,
                                    argument_repairs=item.argument_repairs,
                                ),
                                context,
                            )
                            reviewed_arguments = reviewed.arguments
                            validate_plugin_arguments(
                                item.plugin.name,
                                reviewed_arguments,
                                item.plugin.input_schema,
                            )
                        except Exception as exc:
                            failure = _validation_failure(context, exc)
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
                                failure.message,
                                _utc_now(),
                                failure,
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
                                argument_repairs=list(reviewed.argument_repairs),
                                **_context_fields(context),
                            )
                            prepared[position] = PreparedPluginCall(
                                call=reviewed.call,
                                plugin=item.plugin,
                                arguments=reviewed_arguments,
                                permission_boundary=item.permission_boundary,
                                argument_repairs=reviewed.argument_repairs,
                            )
            result = tuple(item for item in prepared if item is not None)
            op.finish(
                result_count=len(result),
                accepted=sum(isinstance(item, PreparedPluginCall) for item in result),
                rejected=sum(isinstance(item, PluginCallResult) for item in result),
            )
            return result

    async def _validate_prepared_call(
        self,
        prepared: PreparedPluginCall,
        context: PluginContext,
    ) -> None:
        plugin = prepared.plugin
        arguments = dict(prepared.arguments)
        self.registry.resolve(
            plugin.name,
            agent_id=_context_agent_id(context),
        )
        validate_plugin_arguments(plugin.name, arguments, plugin.input_schema)
        current_boundary = await _permission_boundary(plugin, arguments, context)
        if current_boundary != prepared.permission_boundary:
            raise PermissionError(
                "Plugin permission boundary changed after review; retry the exact call."
            )

    async def _invoke_prepared_handler(
        self,
        prepared: PreparedPluginCall,
        context: PluginContext,
    ) -> Any:
        plugin = prepared.plugin
        arguments = dict(prepared.arguments)
        with bind_plugin_execution(self, prepared.call, context):
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
        return value

    def _handler_failure(
        self,
        plugin: Plugin,
        call: PluginCall,
        context: PluginContext,
        exc: Exception,
    ) -> tuple[str, PluginFailure]:
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
        if isinstance(exc, PluginExecutionError):
            failure = exc.failure
            error = failure.message
        else:
            error = _execution_error_text(context, plugin, exc)
            failure = PluginFailure(
                error_code="plugin_execution_failed",
                message=error,
                retryable=False,
                retry_scope="new_run",
                circuit_scope="run_plugin" if plugin.kind == "tool" else "none",
            )
        self._record_failure(plugin, context, failure)
        return error, failure

    async def _blocked_result(
        self,
        prepared: PreparedPluginCall,
        context: PluginContext,
    ) -> PluginCallResult | None:
        plugin = prepared.plugin
        blocked = self._blocked_failure(plugin, context)
        if blocked is None:
            return None
        arguments = dict(prepared.arguments)
        if _dispatches_own_tool_hooks(plugin):
            await self._post(
                context,
                prepared.call.name,
                arguments,
                None,
                False,
                blocked.message,
                failure=blocked,
            )
        return PluginCallResult(
            prepared.call.id,
            prepared.call.name,
            False,
            None,
            blocked.message,
            _utc_now(),
            blocked,
        )

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
                await self._validate_prepared_call(prepared, context)
            except Exception as exc:
                failure = _validation_failure(context, exc)
                op.finish(success=False, rejected=True, error=exc)
                return PluginCallResult(
                    call.id,
                    call.name,
                    False,
                    None,
                    failure.message,
                    _utc_now(),
                    failure,
                )
            blocked_result = await self._blocked_result(prepared, context)
            if blocked_result is not None:
                op.finish(
                    success=False,
                    circuit_open=True,
                    error_code=blocked_result.failure.error_code,
                )
                return blocked_result
            try:
                value = await self._invoke_prepared_handler(prepared, context)
            except asyncio.TimeoutError:
                error = plugin_localized(
                    context,
                    "Plugin timed out after {seconds:g} seconds.",
                    "插件在 {seconds:g} 秒后超时。",
                    seconds=plugin.timeout_seconds,
                )
                failure = PluginFailure(
                    error_code="plugin_timeout",
                    message=error,
                    retryable=True,
                    retry_scope="after_delay",
                    circuit_scope=(
                        "run_plugin" if plugin.kind == "tool" else "none"
                    ),
                )
                self._record_failure(plugin, context, failure)
                if _dispatches_own_tool_hooks(plugin):
                    await self._post(
                        context,
                        call.name,
                        arguments,
                        None,
                        False,
                        error,
                        failure=failure,
                    )
                op.finish(success=False, timed_out=True, error=error)
                return PluginCallResult(
                    call.id,
                    call.name,
                    False,
                    None,
                    error,
                    _utc_now(),
                    failure,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error, failure = self._handler_failure(
                    plugin, call, context, exc
                )
                if _dispatches_own_tool_hooks(plugin):
                    await self._post(
                        context,
                        call.name,
                        arguments,
                        None,
                        False,
                        error,
                        failure=failure,
                    )
                op.finish(success=False, error=exc)
                return PluginCallResult(
                    call.id,
                    call.name,
                    False,
                    None,
                    error,
                    _utc_now(),
                    failure,
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
        arguments_normalized: bool = False,
        nested_arguments_normalized: bool = False,
        argument_repairs: tuple[Mapping[str, str], ...] = (),
    ) -> PluginCallResult:
        call = PluginCall(
            name=name,
            arguments=arguments,
            arguments_normalized=arguments_normalized,
            nested_arguments_normalized=nested_arguments_normalized,
            argument_repairs=argument_repairs,
            **({} if call_id is None else {"id": call_id}),
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
            failure = _validation_failure(context or PluginContext(), exc)
            return PluginCallResult(
                call_id or f"call_{canonical_name}",
                str(canonical_name),
                False,
                None,
                failure.message,
                _utc_now(),
                failure,
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
        *,
        failure: PluginFailure | None = None,
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
                    failure=(failure.as_dict() if failure is not None else None),
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
