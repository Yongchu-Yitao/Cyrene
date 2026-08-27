"""AgentSession model routing over live ``kind=model`` Provider Plugins."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from typing import Any
from uuid import uuid4

from .execution import require_plugin_execution
from .model_catalog import (
    candidate_identity,
    candidate_provider_id,
    configured_model_candidates,
    remember_model_success,
    resolve_registered_model_plugin,
)
from .plugin import Plugin, PluginContext

MODEL_ROUTER_PLUGIN = "CyreneModelRouter"
EXACT_MODEL_UNAVAILABLE = "Requested exact model identity is no longer configured"


def project_model_messages(
    messages: list[dict[str, Any]],
    *,
    phase: str,
    system_extra: str,
) -> list[dict[str, Any]]:
    """Append turn-only Workbench context only to an Agent model request."""

    projected = deepcopy(messages)
    extra = str(system_extra or "").strip()
    if phase != "agent" or not extra:
        return projected
    for message in projected:
        if str(message.get("role") or "") != "system":
            continue
        content = str(message.get("content") or "").strip()
        message["content"] = "\n\n".join(part for part in (content, extra) if part)
        return projected
    projected.insert(0, {"role": "system", "content": extra})
    return projected


def _normalize_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
    from cyrene.model_runtime.messages import parse_tool_arguments

    iterable = raw_calls if isinstance(raw_calls, Sequence) and not isinstance(raw_calls, (str, bytes, bytearray)) else ()
    calls: list[dict[str, Any]] = []
    for raw in iterable:
        if not isinstance(raw, Mapping):
            raise ValueError("Model Provider Plugin returned an invalid tool call")
        function = raw.get("function")
        source = function if isinstance(function, Mapping) else raw
        name = str(source.get("name") or "").strip()
        if not name:
            raise ValueError("Model Provider Plugin tool call is missing a name")
        calls.append(
            {
                "id": str(raw.get("id") or f"call_{uuid4().hex}"),
                "name": name,
                "arguments": parse_tool_arguments(source.get("arguments")),
            }
        )
    return calls


def _request_token_estimate(
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
        from cyrene.runtime.config_store import effective_ctx_limit_for_model

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
) -> list[dict[str, Any]]:
    required = _request_token_estimate(messages, tools)
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


def _normalized_provider_result(
    value: Mapping[str, Any],
    candidate: Mapping[str, Any],
    provider_plugin: Plugin,
) -> dict[str, Any]:
    content = value.get("content")
    reasoning = value.get("reasoning")
    if not isinstance(reasoning, str):
        reasoning = value.get("reasoning_content")
    reasoning_details = value.get("reasoning_details")
    usage = value.get("usage")
    model = str(value.get("model") or candidate.get("model") or "")
    endpoint = str(value.get("endpoint") or "")
    identity = candidate_identity(candidate, model=model, endpoint=endpoint)
    identity["provider"] = _provider_id(provider_plugin, candidate)
    try:
        latency_ms = max(0.0, float(value.get("latency_ms") or 0.0))
    except (TypeError, ValueError):
        latency_ms = 0.0
    result = {
        "content": content if isinstance(content, str) else "",
        "reasoning": reasoning if isinstance(reasoning, str) else "",
        "reasoning_details": ([dict(item) for item in reasoning_details if isinstance(item, Mapping)] if isinstance(reasoning_details, list) else []),
        "tool_calls": _normalize_tool_calls(value.get("tool_calls")),
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
    completion_tokens = result["usage"].get("completion_tokens") or result["usage"].get("output_tokens")
    try:
        result["output_tokens_per_second"] = max(0, int(completion_tokens or 0)) / (latency_ms / 1000.0) if latency_ms > 0 else 0.0
    except (TypeError, ValueError):
        result["output_tokens_per_second"] = 0.0
    return result


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
        from agent.plugin.native_runtime import publish_runtime_event
        from cyrene.observability.context_trace import (
            strip_context_metadata,
            summarize_context_trace,
        )

        run_context = context.data.get("run_context")
        run_context = run_context if isinstance(run_context, Mapping) else {}
        event: dict[str, Any] = {
            "type": "llm_call",
            "caller": str(context.data.get("caller") or run_context.get("caller") or "main_agent"),
            "phase": str(context.data.get("model_call_kind") or "agent"),
            "model": str((response or {}).get("model") or candidate.get("model") or ""),
            "provider": candidate_provider_id(candidate),
            "provider_plugin": provider_plugin,
            "tools": [str(tool.get("function", {}).get("name") or "") for tool in (tools or []) if isinstance(tool, Mapping)],
            "messages": strip_context_metadata(messages),
            "context_trace": summarize_context_trace(messages),
            "response": dict(response or {}),
            "usage": dict((response or {}).get("usage") or {}),
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
        from agent.plugin.native_runtime import publish_runtime_event
        from cyrene.model_runtime.status import persist_model_status

        run_context = context.data.get("run_context")
        run_context = run_context if isinstance(run_context, Mapping) else {}
        session_id = str(context.data.get("session_id") or run_context.get("session_id") or "")
        round_id = str(context.data.get("run_id") or run_context.get("round_id") or "")
        fallback_model = str(fallback.get("model") or "")
        if session_id and round_id and fallback_model:
            try:
                await persist_model_status(
                    session_id,
                    round_id,
                    status="switched",
                    model=fallback_model,
                )
            except Exception:
                pass

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


async def route_model_call(
    arguments: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    """Try configured candidates by invoking their actual Provider Plugins."""

    messages = arguments.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    if not all(isinstance(message, Mapping) for message in messages):
        raise ValueError("messages entries must be objects")
    tools = arguments.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise ValueError("tools must be an array")

    session_id = str(context.data.get("session_id") or context.tree_id or "")
    phase = str(context.data.get("model_call_kind") or "agent")
    route = str(arguments.get("route") or context.data.get("model_route") or "primary").strip().lower()
    if route not in {"primary", "secondary", "vision"}:
        raise ValueError(f"Unsupported chat model route: {route}")
    model_messages = project_model_messages(
        [dict(message) for message in messages],
        phase=phase,
        system_extra=str(context.data.get("system_extra") or ""),
    )
    requested_identity = context.data.get("model_identity")
    if isinstance(requested_identity, Mapping):
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
        if not any(str(exact_identity.get(key) or "").strip() for key in identity_fields):
            raise RuntimeError(EXACT_MODEL_UNAVAILABLE)
        exact_candidate = resolve_exact_model_candidate(exact_identity)
        if exact_candidate is None:
            raise RuntimeError(EXACT_MODEL_UNAVAILABLE)
        candidates = [exact_candidate]
        candidate_context = "requested exact model"
    else:
        candidates = configured_model_candidates(session_id, route=route)
        if not candidates:
            raise RuntimeError(f"No model is configured in the {route} route")
        candidate_context = f"{route} model route"

    from cyrene.model_runtime.transcript_policy import require_single_provider_family

    require_single_provider_family(candidates, context=candidate_context)
    eligible = _eligible_candidates(
        candidates,
        model_messages,
        tools if isinstance(tools, list) else None,
        arguments.get("max_tokens"),
    )
    execution = require_plugin_execution()
    # Provider Plugins receive the complete explicit PluginContext below. No
    # legacy Agent ContextVar binding is needed (or allowed) on this path.
    failures: list[str] = []
    for index, candidate in enumerate(eligible):
        provider = resolve_registered_model_plugin(
            execution.runtime.registry,
            candidate_provider_id(candidate),
            str(candidate.get("adapter") or candidate.get("provider") or ""),
        )
        if provider is None or provider.name == MODEL_ROUTER_PLUGIN:
            error = "no matching kind=model Provider Plugin is registered"
            failures.append(f"{candidate.get('model') or candidate.get('id')}: {error}")
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
            if index + 1 < len(eligible):
                await _publish_fallback(context, candidate, eligible[index + 1])
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
            await _publish_llm_event(
                context,
                messages=model_messages,
                tools=tools if isinstance(tools, list) else None,
                candidate=candidate,
                provider_plugin=provider.name,
                response=None,
                duration_ms=elapsed_ms,
                status="failed",
                error=error,
            )
            if index + 1 < len(eligible):
                await _publish_fallback(context, candidate, eligible[index + 1])
            continue

        try:
            output = _normalized_provider_result(result.value, candidate, provider)
        except Exception as exc:
            error = f"invalid Provider Plugin result: {exc}"
            failures.append(f"{provider.name}({candidate.get('model')}): {error}")
            await _publish_llm_event(
                context,
                messages=model_messages,
                tools=tools if isinstance(tools, list) else None,
                candidate=candidate,
                provider_plugin=provider.name,
                response=None,
                duration_ms=elapsed_ms,
                status="failed",
                error=error,
            )
            if index + 1 < len(eligible):
                await _publish_fallback(context, candidate, eligible[index + 1])
            continue
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

    raise RuntimeError("All configured model Provider Plugins failed: " + "; ".join(failures))


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
    "project_model_messages",
    "route_model_call",
]
