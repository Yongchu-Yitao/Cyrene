"""AgentSession model routing over live ``kind=model`` Provider Plugins."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from cyrene.core.plugin.execution import (
    PluginInvocationError,
    invoke_plugin,
    require_plugin_execution,
)
from .tool_call_parsers import GENERIC_TOOL_CALL_PARSER
from .model_catalog import (
    candidate_identity,
    candidate_provider_id,
    configured_model_candidates,
    remember_model_success,
    resolve_registered_model_plugin,
)
from cyrene.core.plugin.plugin import Plugin, PluginContext
from cyrene.model.error_details import (
    ModelCallError,
    ModelErrorDetails,
    classify_model_error,
    details_from_mapping,
    preferred_model_error,
)
from cyrene.model.status import publish_context_model_status

MODEL_ROUTER_PLUGIN = "CyreneModelRouter"
EXACT_MODEL_UNAVAILABLE = "Requested exact model identity is no longer configured"
logger = logging.getLogger(__name__)
_TRUNCATED_FINISH_REASONS = frozenset({
    "length",
    "max_tokens",
    "max_tokens_reached",
    "max_output_tokens",
})


def _invalid_provider_result_details(exc: BaseException) -> ModelErrorDetails:
    """Preserve nested parser retry semantics at the model boundary."""

    details = classify_model_error("invalid Provider Plugin result")
    if not isinstance(exc, PluginInvocationError):
        return details
    failure = exc.result.failure
    if failure is None:
        return details
    return replace(
        details,
        retryable=failure.retryable,
        retry_scope=failure.retry_scope,
    )


def _tool_call_parser(plugin: Plugin) -> str:
    provider = plugin.metadata.get("provider")
    if not isinstance(provider, Mapping):
        return GENERIC_TOOL_CALL_PARSER
    return str(
        provider.get("tool_call_parser") or GENERIC_TOOL_CALL_PARSER
    ).strip()


async def _parse_tool_calls(
    raw_calls: Any,
    provider_plugin: Plugin,
    tools: Any,
) -> list[dict[str, Any]]:
    parser_name = _tool_call_parser(provider_plugin)
    execution = require_plugin_execution()
    parser = execution.runtime.registry.resolve(parser_name)
    if (
        parser.kind != "tool"
        or parser.metadata.get("tool_call_parser") is not True
        or parser.metadata.get("model_visible") is not False
    ):
        raise ValueError(
            f"Provider Plugin selected a non-parser Plugin: {parser_name!r}"
        )
    parsed = await invoke_plugin(
        parser_name,
        {
            "tool_calls": raw_calls if isinstance(raw_calls, list) else [],
            "tools": tools if isinstance(tools, list) else [],
        },
        review=False,
    )
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("tool_calls"),
        list,
    ):
        raise ValueError(
            f"Tool-call parser Plugin {parser_name!r} returned an invalid result"
        )
    calls: list[dict[str, Any]] = []
    for call in parsed["tool_calls"]:
        if not isinstance(call, Mapping):
            raise ValueError(
                f"Tool-call parser Plugin {parser_name!r} returned a non-object call"
            )
        name = str(call.get("name") or "").strip()
        arguments = call.get("arguments")
        if not name or not isinstance(arguments, Mapping):
            raise ValueError(
                f"Tool-call parser Plugin {parser_name!r} returned a non-canonical call"
            )
        normalized: dict[str, Any] = {
            "id": str(call.get("id") or ""),
            "name": name,
            "arguments": dict(arguments),
        }
        if call.get("arguments_normalized") is True:
            normalized["arguments_normalized"] = True
        calls.append(normalized)
    return calls


def request_token_estimate(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> int:
    """Conservatively gate configured context windows without the old client."""

    from cyrene.observability.context_trace import approx_token_count

    total = 0
    for message in messages:
        total += 4 + approx_token_count(json.dumps(message, ensure_ascii=False, sort_keys=True, default=str))
    if tools:
        total += approx_token_count(json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str))
    return total


def _candidate_context_limit(candidate: Mapping[str, Any]) -> int:
    try:
        explicit = int(candidate.get("context_limit") or candidate.get("ctx_limit") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit
    try:
        from cyrene.platform.config_store import effective_ctx_limit_for_model

        return max(
            0,
            int(effective_ctx_limit_for_model(str(candidate.get("model") or "")) or 0),
        )
    except Exception:
        return 0


def _eligible_candidates(
    candidates: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: Any,
    *,
    estimated_input_tokens: Any = None,
) -> list[dict[str, Any]]:
    try:
        required = max(0, int(estimated_input_tokens))
    except (TypeError, ValueError):
        required = request_token_estimate(messages, tools)
    try:
        required += max(0, int(max_tokens or 0))
    except (TypeError, ValueError):
        pass
    eligible: list[dict[str, Any]] = []
    rejected: list[tuple[str, int]] = []
    for candidate in candidates:
        limit = _candidate_context_limit(candidate)
        if limit and required > limit:
            rejected.append((str(candidate.get("model") or ""), limit))
            continue
        eligible.append(candidate)
    if not eligible and rejected:
        limits = ", ".join(f"{model}={limit}" for model, limit in rejected)
        raise ValueError(
            "Model request exceeds all configured context windows; "
            f"requires about {required} tokens; configured limits: {limits}"
        )
    return eligible


def _safe_provider_context(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Pass only lookup/identity data through Runtime observability."""

    return {
        "id": str(candidate.get("id") or ""),
        "profile_id": str(candidate.get("profile_id") or candidate.get("id") or ""),
        "connection_id": str(candidate.get("connection_id") or ""),
        "model": str(candidate.get("model") or ""),
        "provider": str(candidate.get("provider") or ""),
        "adapter": str(candidate.get("adapter") or ""),
        "reasoning_effort": str(candidate.get("reasoning_effort") or ""),
        "preferred_endpoint": str(candidate.get("preferred_endpoint") or ""),
    }


def _provider_arguments(
    arguments: Mapping[str, Any],
    candidate: Mapping[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "operation": "complete",
        "messages": messages,
        "model": str(candidate.get("model") or ""),
    }
    for key in (
        "tools",
        "tool_choice",
        "max_tokens",
        "temperature",
        "response_format",
    ):
        if arguments.get(key) is not None:
            result[key] = arguments[key]
    effort = str(candidate.get("reasoning_effort") or "").strip()
    if effort:
        result["reasoning_effort"] = effort
    return result


def _provider_id(plugin: Plugin, candidate: Mapping[str, Any]) -> str:
    provider = plugin.metadata.get("provider")
    if isinstance(provider, Mapping) and str(provider.get("id") or "").strip():
        return str(provider.get("id") or "").strip().lower()
    return candidate_provider_id(candidate)


async def _normalized_provider_result(
    value: Mapping[str, Any],
    candidate: Mapping[str, Any],
    provider_plugin: Plugin,
    tools: Any,
) -> dict[str, Any]:
    finish_reason = str(value.get("finish_reason") or "").strip().lower()
    content = value.get("content")
    reasoning = value.get("reasoning")
    if not isinstance(reasoning, str):
        reasoning = value.get("reasoning_content")
    reasoning_details = value.get("reasoning_details")
    usage = value.get("usage")
    model = str(value.get("model") or candidate.get("model") or "")
    endpoint = str(value.get("endpoint") or "")
    identity = candidate_identity(
        candidate,
        model=model,
        endpoint=endpoint,
        provider_id=_provider_id(provider_plugin, candidate),
    )
    try:
        latency_ms = max(0.0, float(value.get("latency_ms") or 0.0))
    except (TypeError, ValueError):
        latency_ms = 0.0
    # A length stop does not imply malformed arguments: some providers return
    # complete tool calls at the output limit. Validate the entire batch before
    # accepting it, just as for any other finish reason.
    try:
        tool_calls = await _parse_tool_calls(
            value.get("tool_calls"), provider_plugin, tools,
        )
    except PluginInvocationError as exc:
        if finish_reason not in _TRUNCATED_FINISH_REASONS:
            raise
        raise ModelCallError(
            classify_model_error("model output truncated"),
            diagnostics=value.get("stream_diagnostics"),
        ) from exc
    result = {
        "content": content if isinstance(content, str) else "",
        "reasoning": reasoning if isinstance(reasoning, str) else "",
        "reasoning_details": ([dict(item) for item in reasoning_details if isinstance(item, Mapping)] if isinstance(reasoning_details, list) else []),
        "tool_calls": tool_calls,
        "finish_reason": str(value.get("finish_reason") or ""),
        "usage": dict(usage) if isinstance(usage, Mapping) else {},
        "usage_observation": (dict(value["usage_observation"]) if isinstance(value.get("usage_observation"), Mapping) else {}),
        "model": model,
        "model_identity": identity,
        "response_id": str(value.get("response_id") or value.get("id") or ""),
        "observation_node_id": str(value.get("observation_node_id") or ""),
        "latency_ms": latency_ms,
        "endpoint": endpoint,
        "provider_plugin": provider_plugin.name,
    }
    stream_diagnostics = value.get("stream_diagnostics")
    if isinstance(stream_diagnostics, Mapping):
        result["stream_diagnostics"] = dict(stream_diagnostics)
    completion_tokens = result["usage"].get("completion_tokens") or result["usage"].get("output_tokens")
    try:
        result["output_tokens_per_second"] = max(0, int(completion_tokens or 0)) / (latency_ms / 1000.0) if latency_ms > 0 else 0.0
    except (TypeError, ValueError):
        result["output_tokens_per_second"] = 0.0
    return result


def _failed_provider_response(value: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve the Provider result that failed router normalization.

    Successful LLM accounting events already retain tool arguments. Keeping the
    same evidence for failed calls makes malformed streamed arguments
    diagnosable instead of replacing the response with an empty object.
    """

    snapshot: dict[str, Any] = {}
    for key in (
        "content",
        "reasoning",
        "reasoning_details",
        "tool_calls",
        "finish_reason",
        "usage",
        "usage_observation",
        "model",
        "model_identity",
        "response_id",
        "observation_node_id",
        "latency_ms",
        "endpoint",
        "stream_diagnostics",
    ):
        if key in value:
            snapshot[key] = value[key]

    diagnostics: list[dict[str, Any]] = []
    raw_calls = value.get("tool_calls")
    for index, raw_call in enumerate(raw_calls if isinstance(raw_calls, list) else ()):
        if not isinstance(raw_call, Mapping):
            diagnostics.append({"index": index, "invalid_call_type": True})
            continue
        function = raw_call.get("function")
        source = function if isinstance(function, Mapping) else raw_call
        arguments = source.get("arguments")
        if isinstance(arguments, str):
            serialized = arguments
        else:
            try:
                serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                serialized = repr(arguments)
        diagnostics.append({
            "index": index,
            "id": str(raw_call.get("id") or ""),
            "name": str(source.get("name") or ""),
            "arguments_length": len(serialized),
            "arguments_sha256": hashlib.sha256(
                serialized.encode("utf-8")
            ).hexdigest(),
        })
    snapshot["normalization_failed"] = True
    snapshot["tool_call_diagnostics"] = diagnostics
    return snapshot


async def _publish_llm_event(
    context: PluginContext,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    candidate: Mapping[str, Any],
    provider_plugin: str,
    response: Mapping[str, Any] | None,
    duration_ms: float,
    status: str,
    error: str = "",
) -> None:
    try:
        from cyrene.core.plugin.context import publish_runtime_event
        from cyrene.observability.context_trace import (
            strip_context_metadata,
            summarize_context_trace,
        )

        run_context = context.data.get("run_context")
        run_context = run_context if isinstance(run_context, Mapping) else {}
        session_id = str(
            context.data.get("session_id")
            or run_context.get("session_id")
            or context.tree_id
            or ""
        )
        round_id = str(
            context.data.get("run_id")
            or run_context.get("round_id")
            or run_context.get("run_id")
            or ""
        )
        event: dict[str, Any] = {
            "type": "llm_call",
            "caller": str(context.data.get("caller") or run_context.get("caller") or "main_agent"),
            "phase": str(context.data.get("model_call_kind") or "agent"),
            "session_id": session_id,
            "round_id": round_id,
            "model": str((response or {}).get("model") or candidate.get("model") or ""),
            "provider": candidate_provider_id(candidate),
            "provider_plugin": provider_plugin,
            "tools": [str(tool.get("function", {}).get("name") or "") for tool in (tools or []) if isinstance(tool, Mapping)],
            "messages": strip_context_metadata(messages),
            "context_trace": summarize_context_trace(messages),
            "response": dict(response or {}),
            "usage": dict((response or {}).get("usage") or {}),
            "usage_observation": dict(
                (response or {}).get("usage_observation") or {}
            ),
            "duration_ms": max(0, int(duration_ms)),
            "status": status,
        }
        if error:
            event["error"] = str(error)[:1000]
        await publish_runtime_event(context, event)
    except Exception:
        # Presentation/accounting telemetry must not invalidate a model result.
        return


async def _publish_fallback(
    context: PluginContext,
    failed: Mapping[str, Any],
    fallback: Mapping[str, Any],
) -> None:
    try:
        from cyrene.core.plugin.context import publish_runtime_event
        fallback_model = str(fallback.get("model") or "")
        await publish_context_model_status(
            context,
            status="switching",
            model=fallback_model,
        )

        await publish_runtime_event(
            context,
            {
                "type": "phase_transition",
                "from": "primary_model",
                "to": "fallback_model",
                "detail": "Primary model unavailable, switching to a fallback model.",
                "detail_key": "phase.modelFallback",
                "detail_params": {
                    "failedModel": str(failed.get("model") or ""),
                    "fallbackModel": fallback_model,
                },
            },
        )
    except Exception:
        return


async def _persist_fallback_result(
    context: PluginContext,
    candidate: Mapping[str, Any],
    *,
    status: str,
) -> None:
    await publish_context_model_status(
        context,
        status=status,
        model=str(candidate.get("model") or ""),
    )


async def _reset_model_stream(context: PluginContext) -> None:
    sink = context.services.get("model_stream")
    if not callable(sink):
        return
    result = sink({"type": "reply_start", "reset": True})
    if hasattr(result, "__await__"):
        await result


async def _publish_next_fallback(
    context: PluginContext,
    candidate: Mapping[str, Any],
    eligible: list[dict[str, Any]],
    index: int,
) -> None:
    if index + 1 >= len(eligible):
        return
    await _reset_model_stream(context)
    await _publish_fallback(context, candidate, eligible[index + 1])


def _routed_candidates(
    session_id: str,
    route: str,
    requested_identity: Any,
) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(requested_identity, Mapping):
        candidates = configured_model_candidates(session_id, route=route)
        if not candidates:
            raise ModelCallError(
                classify_model_error(f"No model is configured in the {route} route")
            )
        return candidates, f"{route} model route"

    from .model_catalog import resolve_exact_model_candidate

    exact_identity = dict(requested_identity)
    identity_fields = (
        "candidateId",
        "profileId",
        "profile_id",
        "provider",
        "adapter",
        "model",
        "baseUrl",
    )
    exact_candidate = (
        resolve_exact_model_candidate(exact_identity)
        if any(str(exact_identity.get(key) or "").strip() for key in identity_fields)
        else None
    )
    if exact_candidate is None:
        raise ModelCallError(
            classify_model_error("model not found: " + EXACT_MODEL_UNAVAILABLE)
        )
    return [exact_candidate], "requested exact model"


def _route_call_inputs(
    arguments: dict[str, Any], context: PluginContext
) -> tuple[list[Any], list[Any] | None, str, str, list[dict[str, Any]]]:
    messages = arguments.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    if not all(isinstance(message, Mapping) for message in messages):
        raise ValueError("messages entries must be objects")
    tools = arguments.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise ValueError("tools must be an array")

    session_id = str(context.data.get("session_id") or context.tree_id or "")
    route = str(arguments.get("route") or context.data.get("model_route") or "primary").strip().lower()
    if route not in {"primary", "secondary", "vision"}:
        raise ValueError(f"Unsupported chat model route: {route}")
    model_messages = messages if all(isinstance(item, dict) for item in messages) else [dict(item) for item in messages]
    return messages, tools, session_id, route, model_messages


async def route_model_call(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    """Try configured candidates by invoking their actual Provider Plugins."""

    _messages, tools, session_id, route, model_messages = _route_call_inputs(arguments, context)
    candidates, candidate_context = _routed_candidates(
        session_id,
        route,
        context.data.get("model_identity"),
    )

    from cyrene.model.transcript_policy import require_single_provider_family

    require_single_provider_family(candidates, context=candidate_context)
    try:
        eligible = _eligible_candidates(
            candidates,
            model_messages,
            tools if isinstance(tools, list) else None,
            arguments.get("max_tokens"),
            estimated_input_tokens=context.data.get("prepared_request_tokens"),
        )
    except ValueError as exc:
        raise ModelCallError(classify_model_error(exc)) from exc
    execution = require_plugin_execution()
    # Provider Plugins receive the complete explicit PluginContext below. No
    # legacy Agent ContextVar binding is needed (or allowed) on this path.
    failures: list[str] = []
    public_failures: list[ModelErrorDetails] = []
    failure_diagnostics: list[Mapping[str, Any] | None] = []
    for index, candidate in enumerate(eligible):
        provider = resolve_registered_model_plugin(
            execution.runtime.registry,
            candidate_provider_id(candidate),
            str(candidate.get("adapter") or candidate.get("provider") or ""),
        )
        if provider is None or provider.name == MODEL_ROUTER_PLUGIN:
            error = "no matching kind=model Provider Plugin is registered"
            failures.append(f"{candidate.get('model') or candidate.get('id')}: {error}")
            public_failures.append(classify_model_error("model service unavailable"))
            failure_diagnostics.append(None)
            await _publish_llm_event(
                context,
                messages=model_messages,
                tools=tools if isinstance(tools, list) else None,
                candidate=candidate,
                provider_plugin="",
                response=None,
                duration_ms=0,
                status="failed",
                error=error,
            )
            await _publish_next_fallback(context, candidate, eligible, index)
            continue
        provider_context = replace(
            context,
            hooks=None,
            data={
                **dict(context.data),
                "model_candidate": _safe_provider_context(candidate),
                "model_profile_id": str(candidate.get("profile_id") or candidate.get("id") or ""),
            },
        )
        started = time.perf_counter()
        result = await execution.runtime.call(
            provider.name,
            _provider_arguments(arguments, candidate, model_messages),
            provider_context,
            call_id=f"{execution.call.id}.provider.{index}",
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if not result.success or not isinstance(result.value, Mapping):
            error = result.error or "Provider Plugin returned no result"
            failures.append(f"{provider.name}({candidate.get('model')}): {error}")
            public_failure = details_from_mapping(result.error_details)
            if public_failure is None and result.failure is not None:
                # Runtime timeouts are structured Plugin failures. Preserve
                # that code instead of reclassifying a localized message such
                # as Chinese "超时", which previously became the generic
                # model_call_failed error.
                public_failure = details_from_mapping(result.failure.as_dict())
            stream_diagnostics = result.error_details.get("stream_diagnostics")
            public_failures.append(public_failure or classify_model_error(error))
            failure_diagnostics.append(
                dict(stream_diagnostics)
                if isinstance(stream_diagnostics, Mapping)
                else None
            )
            failed_response = (
                {"stream_diagnostics": dict(stream_diagnostics)}
                if isinstance(stream_diagnostics, Mapping)
                else None
            )
            await _publish_llm_event(
                context,
                messages=model_messages,
                tools=tools if isinstance(tools, list) else None,
                candidate=candidate,
                provider_plugin=provider.name,
                response=failed_response,
                duration_ms=elapsed_ms,
                status="failed",
                error=error,
            )
            await _publish_next_fallback(context, candidate, eligible, index)
            continue

        try:
            output = await _normalized_provider_result(
                result.value,
                candidate,
                provider,
                tools,
            )
        except Exception as exc:
            error = f"invalid Provider Plugin result: {exc}"
            await _publish_llm_event(
                context,
                messages=model_messages,
                tools=tools if isinstance(tools, list) else None,
                candidate=candidate,
                provider_plugin=provider.name,
                response=_failed_provider_response(result.value),
                duration_ms=elapsed_ms,
                status="failed",
                error=error,
            )
            await _persist_fallback_result(
                context,
                candidate,
                status="failed",
            )
            logger.warning(
                "Model Provider Plugin returned a protocol-invalid result: %s(%s): %s",
                provider.name,
                candidate.get("model"),
                error,
            )
            # The Provider was reachable and returned a response.  A failure
            # while normalizing that response (for example, requesting a tool
            # that is not in the current schema) is a protocol error, not
            # evidence that this model candidate is unavailable.  Falling back
            # here can duplicate generation and lets a shared parser failure
            # poison otherwise healthy fallback candidates.
            if isinstance(exc, ModelCallError):
                raise
            raise ModelCallError(
                _invalid_provider_result_details(exc),
                diagnostics=result.value.get("stream_diagnostics"),
            ) from exc
        await _publish_llm_event(
            context,
            messages=model_messages,
            tools=tools if isinstance(tools, list) else None,
            candidate=candidate,
            provider_plugin=provider.name,
            response=output,
            duration_ms=output.get("latency_ms") or elapsed_ms,
            status="completed",
        )
        if index > 0:
            await _persist_fallback_result(
                context,
                candidate,
                status="switched",
            )
        try:
            remember_model_success(
                session_id,
                candidate,
                str(output.get("endpoint") or ""),
                route=str(candidate.get("_model_route") or route),
            )
        except Exception:
            pass
        return output

    if eligible:
        await _persist_fallback_result(
            context,
            eligible[-1],
            status="failed",
        )
    logger.warning(
        "All configured model Provider Plugins failed: %s",
        "; ".join(failures),
    )
    preferred = preferred_model_error(public_failures)
    diagnostics = next(
        (
            failure_diagnostics[index]
            for index, details in enumerate(public_failures)
            if details is preferred
        ),
        None,
    )
    raise ModelCallError(preferred, diagnostics=diagnostics)


def create_model_router_plugin() -> Plugin:
    """Create the AgentSession-only candidate router."""

    return Plugin(
        name=MODEL_ROUTER_PLUGIN,
        description=("Route one Agent model call through the configured editable Provider Plugins."),
        input_schema={
            "type": "object",
            "properties": {
                "messages": {"type": "array"},
                "tools": {"type": "array"},
                "tool_choice": {
                    "oneOf": [
                        {"type": "string", "enum": ["auto", "none", "required"]},
                        {"type": "object"},
                    ]
                },
                "max_tokens": {"type": "integer"},
                "temperature": {"type": "number"},
                "response_format": {"type": "object"},
                "route": {
                    "type": "string",
                    "enum": ["primary", "secondary", "vision"],
                    "default": "primary",
                },
            },
            "required": ["messages"],
            "additionalProperties": False,
        },
        handler=route_model_call,
        kind="model",
    )


__all__ = [
    "EXACT_MODEL_UNAVAILABLE",
    "MODEL_ROUTER_PLUGIN",
    "create_model_router_plugin",
    "request_token_estimate",
    "route_model_call",
]
