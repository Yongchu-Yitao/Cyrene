"""Shared runtime for editable model provider Plugins.

Provider modules in this directory intentionally stay very small: each one
declares its identity and endpoints, while this module owns the common Plugin
contract, model discovery, protocol conversion, and normalized result shape.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from cyrene.core.observability import log_operation
from cyrene.core.plugin import Plugin, PluginContext
from cyrene.model.status import publish_context_model_status
from cyrene.plugins.tool_call_parsers import GENERIC_TOOL_CALL_PARSER


logger = logging.getLogger(__name__)
MODEL_CONNECT_RETRY_LIMIT = 5
MODEL_CONNECT_RETRY_DELAY_SECONDS = 10.0


class _IPv4FallbackTransport(httpx.AsyncBaseTransport):
    """Retry connection-establishment failures over IPv4.

    A dual-stack host can accept an IPv6 TCP connection and then reset the TLS
    handshake.  httpcore treats that as a completed address selection, so its
    normal Happy Eyeballs behavior never reaches the working IPv4 address.
    Keep the ordinary dual-stack transport first and retry only ConnectError
    failures with an IPv4-bound transport; HTTP responses and request errors
    are never replayed.
    """

    def __init__(
        self,
        primary: httpx.AsyncBaseTransport | None = None,
        ipv4: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._primary = primary or httpx.AsyncHTTPTransport()
        self._ipv4 = ipv4 or httpx.AsyncHTTPTransport(local_address="0.0.0.0")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._primary.handle_async_request(request)
        except httpx.ConnectError:
            logger.info(
                "Direct model connection failed over the preferred address; "
                "retrying over IPv4 [host=%s]",
                request.url.host,
            )
            return await self._ipv4.handle_async_request(request)

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._ipv4.aclose()


@dataclass(frozen=True, slots=True)
class ModelProvider:
    id: str
    name: str
    plugin_name: str
    adapter: str
    default_base_url: str
    default_model: str = ""
    auth_type: str = "api_key"
    capabilities: tuple[str, ...] = ("chat",)
    icon: str = ""
    supports_discovery: bool = True
    supported_reasoning_efforts: tuple[str, ...] = ()
    default_reasoning_effort: str = ""
    tool_call_parser: str = GENERIC_TOOL_CALL_PARSER
    include_stream_usage: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": {
                "id": self.id,
                "name": self.name,
                "adapter": self.adapter,
                "default_base_url": self.default_base_url,
                "auth_type": self.auth_type,
                "capabilities": list(self.capabilities),
                "default_model": self.default_model,
                "icon": self.icon or self.id,
                "supports_discovery": self.supports_discovery,
                "supported_reasoning_efforts": list(
                    self.supported_reasoning_efforts
                ),
                "default_reasoning_effort": self.default_reasoning_effort,
                "tool_call_parser": self.tool_call_parser,
            }
        }

def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _connection(context: PluginContext) -> Mapping[str, Any]:
    explicit = _mapping(context.services.get("model_connection"))
    if not explicit:
        explicit = _mapping(context.data.get("model_connection"))
    return explicit or _candidate(context)


def _profile(context: PluginContext) -> Mapping[str, Any]:
    explicit = _mapping(context.services.get("model_profile"))
    if not explicit:
        explicit = _mapping(context.data.get("model_profile"))
    return explicit or _candidate(context)


def _candidate(context: PluginContext) -> Mapping[str, Any]:
    supplied = dict(_mapping(context.data.get("model_candidate")))
    profile_id = str(
        context.data.get("model_profile_id")
        or supplied.get("profile_id")
        or supplied.get("id")
        or ""
    ).strip()
    if not profile_id:
        return supplied
    try:
        from .configuration import candidate_for_profile

        configured = candidate_for_profile(profile_id)
    except Exception:
        configured = None
    if not isinstance(configured, Mapping):
        return supplied
    # Credentials stay inside the Provider handler rather than travelling in
    # PluginContext.data, which the Runtime may include in diagnostic records.
    return {**dict(configured), **supplied}


def _provider_value(
    arguments: Mapping[str, Any],
    context: PluginContext,
    provider: ModelProvider,
    name: str,
) -> str:
    candidate = _candidate(context)
    profile = _profile(context)
    connection = _connection(context)
    if name == "model":
        values = (
            arguments.get("model"),
            candidate.get("model"),
            profile.get("model"),
            profile.get("model_id"),
            provider.default_model,
        )
    elif name == "base_url":
        values = (
            candidate.get("base_url"),
            connection.get("base_url"),
            provider.default_base_url,
        )
    elif name == "api_key":
        # A configured connection is an explicit credential boundary.  An
        # empty key must stay empty instead of silently borrowing (and
        # potentially leaking) a provider key from the process environment to
        # a user-supplied endpoint.
        if "api_key" in candidate:
            return str(candidate.get("api_key") or "").strip()
        if connection:
            return str(connection.get("api_key") or "").strip()
        values = ()
    else:
        values = ()
    return next((str(value).strip() for value in values if str(value or "").strip()), "")


def _timeout(context: PluginContext, provider: ModelProvider) -> float:
    candidate = _candidate(context)
    raw = (
        candidate.get("timeout")
        or context.data.get("model_timeout")
        or 180
    )
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("model timeout must be a number") from exc


def _client_options(
    context: PluginContext,
    provider: ModelProvider,
    *,
    discovery: bool,
) -> dict[str, Any]:
    timeout = httpx.Timeout(20.0, connect=5.0) if discovery else _timeout(context, provider)
    # Provider connections must not silently inherit OS or environment proxy
    # settings.  In particular, macOS system proxy discovery can otherwise
    # route private/Tailscale endpoints through a local HTTP proxy even when
    # the connection's ``use_proxy`` switch is off.  An opted-in Cyrene proxy
    # is applied explicitly below.
    options: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
        "trust_env": False,
    }
    transport = context.data.get("http_transport")
    if isinstance(transport, httpx.AsyncBaseTransport):
        options["transport"] = transport
        return options
    connection = _connection(context)
    candidate = _candidate(context)
    if connection.get("use_proxy") is True or candidate.get("use_proxy") is True:
        try:
            from cyrene.platform.network_proxy import configured_proxy_url

            proxy = configured_proxy_url(opt_in=True)
        except Exception:
            proxy = ""
        if proxy:
            options["proxy"] = proxy
    if "proxy" not in options:
        options["transport"] = _IPv4FallbackTransport()
    return options


def _tool_choice(value: Any) -> str | dict[str, Any]:
    if value is None:
        return "auto"
    if isinstance(value, str):
        normalized = value.strip()
        if normalized not in {"auto", "none", "required"}:
            raise ValueError("tool_choice must be auto, none, required, or a function")
        return normalized
    if not isinstance(value, Mapping) or value.get("type") != "function":
        raise ValueError("tool_choice must be auto, none, required, or a function")
    function = value.get("function")
    if not isinstance(function, Mapping):
        raise ValueError("tool_choice function is missing")
    name = str(function.get("name") or "").strip()
    if not name:
        raise ValueError("tool_choice function name is missing")
    return {"type": "function", "function": {"name": name}}


def _tool_calls(message: Mapping[str, Any]) -> list[Any]:
    """Preserve Provider wire calls for the model-router Plugin boundary."""

    raw_calls = message.get("tool_calls")
    return list(raw_calls) if isinstance(raw_calls, list) else []


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def usage_observation(usage: Mapping[str, Any]) -> dict[str, Any]:
    prompt_tokens = _integer(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _integer(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )
    total_tokens = _integer(usage.get("total_tokens"))
    details = _mapping(usage.get("prompt_tokens_details"))
    cache_fields = (
        details.get("cached_tokens"),
        usage.get("cached_tokens"),
        usage.get("cached_input_tokens"),
        usage.get("cache_read_input_tokens"),
        usage.get("prompt_cache_hit_tokens"),
    )
    cache_observed = any(value is not None for value in cache_fields)
    cached_tokens = next(
        (_integer(value) for value in cache_fields if value is not None),
        0,
    )
    miss_value = usage.get("prompt_cache_miss_tokens")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens or prompt_tokens + completion_tokens,
        "cached_prompt_tokens": cached_tokens,
        "cache_hit_rate": (
            cached_tokens / prompt_tokens
            if cache_observed and prompt_tokens
            else 0.0
            if cache_observed
            else None
        ),
        "cache_observed": cache_observed,
        "cache_miss_tokens": _integer(miss_value) if miss_value is not None else None,
        "raw": dict(usage),
    }


def _mount_observation(
    context: PluginContext,
    provider: ModelProvider,
    *,
    response_id: str,
    model: str,
    usage: Mapping[str, Any],
    latency_ms: float,
) -> str:
    if context.tree is None or not context.tree_id or not context.node_id:
        return ""
    normalized = usage_observation(usage)
    identity = response_id or f"{time.time_ns()}"
    node_id = "model_observation_" + hashlib.sha256(
        f"{context.tree_id}:{context.node_id}:{identity}".encode("utf-8")
    ).hexdigest()[:32]
    node = context.tree.mount(
        context.tree_id,
        context.node_id,
        {
            "role": "model_observation",
            "provider": provider.id,
            "model": model,
            "call_kind": str(context.data.get("model_call_kind") or "model"),
            "response_id": response_id,
            "latency_ms": max(0.0, float(latency_ms)),
            "usage": normalized,
        },
        node_id=node_id,
    )
    profile = _profile(context)
    candidate = _candidate(context)
    token_limit = _integer(
        candidate.get("context_limit")
        or candidate.get("ctx_limit")
        or profile.get("context_limit")
    )
    if str(context.data.get("model_call_kind") or "agent") == "agent":
        context.tree.report_context_used(
            context.tree_id,
            context.node_id,
            int(normalized["prompt_tokens"]),
            token_limit=token_limit or 204_800,
        )
    return node.id


def _reasoning_text(message: Mapping[str, Any]) -> str:
    direct = message.get("reasoning_content")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    details = message.get("reasoning_details")
    for detail in details if isinstance(details, list) else ():
        if isinstance(detail, Mapping) and isinstance(detail.get("text"), str):
            parts.append(str(detail["text"]))
    return "".join(parts)


def _reasoning_details(message: Mapping[str, Any], reasoning: str) -> list[dict[str, Any]]:
    details = message.get("reasoning_details")
    if isinstance(details, list):
        return [dict(detail) for detail in details if isinstance(detail, Mapping)]
    if reasoning:
        return [{"type": "reasoning.text", "text": reasoning}]
    return []


def _normalized_result(
    message: Mapping[str, Any],
    provider: ModelProvider,
    context: PluginContext,
    *,
    response_id: str,
    model: str,
    latency_ms: float,
    endpoint: str = "",
) -> dict[str, Any]:
    usage = dict(_mapping(message.get("usage")))
    reasoning = _reasoning_text(message)
    try:
        observation_node_id = _mount_observation(
            context,
            provider,
            response_id=response_id,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
        )
    except Exception:
        # Observability is best effort. A successful provider response must
        # still advance the Agent if its diagnostic tree mount cannot commit.
        observation_node_id = ""
    content = message.get("content")
    candidate = _candidate(context)
    try:
        from cyrene.plugins.model_catalog import candidate_identity

        model_identity = candidate_identity(
            candidate,
            model=model,
            endpoint=endpoint,
            provider_id=provider.id,
        )
    except Exception:
        model_identity = {
            "candidateId": str(candidate.get("id") or ""),
            "adapter": str(candidate.get("adapter") or provider.adapter),
            "provider": provider.id,
            "model": model,
            "baseUrl": "",
            "endpoint": endpoint,
            "reasoningEffort": str(candidate.get("reasoning_effort") or ""),
        }
    normalized_usage = usage_observation(usage)
    result = {
        "content": content if isinstance(content, str) else "",
        "reasoning": reasoning,
        "reasoning_details": _reasoning_details(message, reasoning),
        "tool_calls": _tool_calls(message),
        "finish_reason": str(message.get("finish_reason") or ""),
        "usage": usage,
        "usage_observation": normalized_usage,
        "model": model,
        "model_identity": model_identity,
        "response_id": response_id,
        "observation_node_id": observation_node_id,
        "latency_ms": max(0.0, float(latency_ms)),
        "endpoint": str(endpoint or ""),
    }
    stream_diagnostics = message.get("stream_diagnostics")
    if isinstance(stream_diagnostics, Mapping):
        result["stream_diagnostics"] = dict(stream_diagnostics)
    completion_tokens = int(normalized_usage.get("completion_tokens") or 0)
    result["output_tokens_per_second"] = (
        completion_tokens / (float(latency_ms) / 1000.0)
        if latency_ms > 0
        else 0.0
    )
    return result


async def discover_models(
    provider: ModelProvider,
    context: PluginContext,
) -> dict[str, Any]:
    if provider.adapter == "codex_oauth":
        from cyrene.model.codex_provider import get_codex_provider

        raw_models = await get_codex_provider().models()
        models = [
            {
                "id": str(item.get("model") or item.get("id") or "").strip(),
                "model": str(item.get("model") or item.get("id") or "").strip(),
                "name": str(
                    item.get("name") or item.get("model") or item.get("id") or ""
                ).strip(),
                "capabilities": list(provider.capabilities),
            }
            for item in raw_models
            if isinstance(item, Mapping)
            and str(item.get("model") or item.get("id") or "").strip()
        ]
        return {"provider": provider.id, "models": models}

    if provider.adapter == "local_onnx":
        return {
            "provider": provider.id,
            "models": [{
                "id": "qwen3-embedding-0.6b",
                "model": "qwen3-embedding-0.6b",
                "name": "Qwen3 Embedding 0.6B",
                "capabilities": ["embedding"],
                "dimensions": 1024,
            }],
        }

    from cyrene.model.protocol_adapters import (
        discovery_request,
        next_discovery_page,
        parse_discovery_response,
    )

    connection = _connection(context)
    adapter = str(connection.get("adapter") or provider.adapter).strip().lower()
    base_url = _provider_value({}, context, provider, "base_url").rstrip("/")
    api_key = _provider_value({}, context, provider, "api_key")
    if provider.auth_type == "api_key" and not api_key:
        from cyrene.model.error_details import ModelCallError, classify_model_error

        raise ModelCallError(
            classify_model_error(f"{provider.name} API key is not configured")
        )
    endpoint, headers = discovery_request(
        adapter,
        base_url,
        api_key,
        provider_preset=provider.id,
    )
    models: list[dict[str, Any]] = []
    seen_model_ids: set[str] = set()
    async with httpx.AsyncClient(
        **_client_options(context, provider, discovery=True)
    ) as client:
        for _page in range(20):
            response = await client.get(endpoint, headers=headers)
            response.raise_for_status()
            payload = response.json()
            for item in parse_discovery_response(
                adapter,
                payload,
                provider_preset=provider.id,
            ):
                model_id = str(item.get("id") or item.get("model") or "").strip()
                if not model_id or model_id in seen_model_ids:
                    continue
                seen_model_ids.add(model_id)
                models.append(item)
            next_endpoint = next_discovery_page(
                endpoint,
                payload,
                provider_preset=provider.id,
            )
            if not next_endpoint or next_endpoint == endpoint:
                break
            endpoint = next_endpoint
    return {
        "provider": provider.id,
        "models": models,
    }


def _openai_payload(
    arguments: Mapping[str, Any],
    provider: ModelProvider,
    model: str,
) -> dict[str, Any]:
    messages = arguments.get("messages")
    tools = arguments.get("tools")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if provider.id == "minimax":
        payload["reasoning_split"] = True
        payload["stream_options"] = {"include_usage": True}
    elif provider.include_stream_usage:
        payload["stream_options"] = {"include_usage": True}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = _tool_choice(arguments.get("tool_choice"))
    if arguments.get("max_tokens") is not None:
        payload["max_tokens"] = int(arguments["max_tokens"])
    if arguments.get("temperature") is not None:
        payload["temperature"] = float(arguments["temperature"])
    if isinstance(arguments.get("response_format"), Mapping):
        payload["response_format"] = dict(arguments["response_format"])
    reasoning_effort = str(arguments.get("reasoning_effort") or "").strip()
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    return payload


def _log_completed_stream(
    *,
    provider: ModelProvider,
    model: str,
    endpoint: str,
    timing: Mapping[str, float],
    started: float,
    diagnostics: Mapping[str, Any] | None = None,
) -> None:
    log_operation(
        logger,
        "model.provider",
        "stream",
        phase="completed",
        provider=provider.id,
        model=model,
        endpoint=endpoint,
        response_headers_ms=round(float(timing.get("response_headers_ms") or 0.0), 3),
        ttft_ms=round(float(timing.get("ttft_ms") or 0.0), 3),
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
        diagnostics=dict(diagnostics or {}),
    )


def _finalize_stream_result(
    message: Mapping[str, Any],
    *,
    provider: ModelProvider,
    context: PluginContext,
    model: str,
    endpoint: str,
    timing: Mapping[str, float],
    started: float,
) -> dict[str, Any]:
    response_id = str(message.get("response_id") or "")
    returned_model = str(message.get("model") or model)
    _log_completed_stream(
        provider=provider,
        model=returned_model,
        endpoint=endpoint,
        timing=timing,
        started=started,
        diagnostics=(
            message.get("stream_diagnostics")
            if isinstance(message.get("stream_diagnostics"), Mapping)
            else None
        ),
    )
    return _normalized_result(
        dict(message),
        provider,
        context,
        response_id=response_id,
        model=returned_model,
        latency_ms=(time.perf_counter() - started) * 1000,
        endpoint=endpoint,
    )


async def _complete_stream_endpoint(
    *,
    adapter: str,
    client: httpx.AsyncClient,
    endpoint: str,
    request: Any,
    stream_callback: Any,
    provider: ModelProvider,
    context: PluginContext,
    model: str,
    started: float,
    has_fallback: bool,
    retry_state: dict[str, int] | None = None,
) -> dict[str, Any]:
    from cyrene.model.protocol_adapters import ModelStreamError, handle_stream

    attempt_streamed = False
    attempt_emitted = False
    status_state = retry_state if retry_state is not None else {}

    async def publish_stream(event: dict[str, Any]) -> None:
        nonlocal attempt_emitted, attempt_streamed
        attempt_emitted = True
        event_type = str(event.get("type") or "")
        if event_type.startswith(("reply_", "reasoning_")):
            attempt_streamed = True
        if stream_callback is not None:
            await stream_callback(event)

    timing: dict[str, float] = {}
    protocol_trace = context.services.get("model_protocol_trace")
    if not callable(protocol_trace):
        protocol_trace = None
    while True:
        try:
            stream_arguments = (
                adapter,
                client,
                endpoint,
                request,
                publish_stream if stream_callback is not None else None,
                timing,
            )
            message = (
                await handle_stream(
                    *stream_arguments,
                    protocol_trace=protocol_trace,
                )
                if protocol_trace is not None
                else await handle_stream(*stream_arguments)
            )
            break
        except Exception as exc:
            diagnostics = getattr(exc, "diagnostics", None)
            if not isinstance(diagnostics, Mapping):
                response = getattr(exc, "response", None)
                diagnostics = {
                    "adapter": adapter,
                    "http_status": int(getattr(response, "status_code", 0) or 0),
                    "stream_completed": False,
                    "termination_reason": (
                        "http_rejected" if response is not None else "request_failed"
                    ),
                }
            log_operation(
                logger,
                "model.provider",
                "stream",
                phase="failed",
                provider=provider.id,
                model=model,
                endpoint=endpoint,
                error_type=type(exc).__name__,
                diagnostics=dict(diagnostics),
            )
            if protocol_trace is not None:
                try:
                    await protocol_trace({
                        "type": "response_end",
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "diagnostics": dict(diagnostics),
                    })
                except Exception:
                    pass
            transient = isinstance(
                exc,
                (httpx.TransportError, TimeoutError, OSError),
            ) or (
                isinstance(exc, ModelStreamError)
                and exc.kind == "transport_interrupted"
            )
            retry_count = int(status_state.get("count") or 0)
            if (
                retry_count < MODEL_CONNECT_RETRY_LIMIT
                and transient
                and not attempt_emitted
            ):
                retry_count += 1
                status_state["count"] = retry_count
                log_operation(
                    logger,
                    "model.provider",
                    "stream_retry",
                    phase="retrying",
                    provider=provider.id,
                    model=model,
                    endpoint=endpoint,
                    attempt=retry_count,
                    error_type=type(exc).__name__,
                    error=repr(exc),
                )
                await publish_context_model_status(
                    context,
                    status="retry",
                    model=model,
                    retry_count=retry_count,
                    retry_limit=MODEL_CONNECT_RETRY_LIMIT,
                )
                await asyncio.sleep(MODEL_CONNECT_RETRY_DELAY_SECONDS)
                continue
            if attempt_streamed and stream_callback is not None and has_fallback:
                await stream_callback({"type": "reply_start", "reset": True})
            raise
    if int(status_state.get("count") or 0) > 0:
        await publish_context_model_status(
            context,
            status="recovered",
            model=model,
        )
    return _finalize_stream_result(
        message,
        provider=provider,
        context=context,
        model=model,
        endpoint=endpoint,
        timing=timing,
        started=started,
    )


async def complete_model(
    arguments: dict[str, Any],
    context: PluginContext,
    provider: ModelProvider,
) -> dict[str, Any]:
    messages = arguments.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    tools = arguments.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise ValueError("tools must be an array")
    from cyrene.observability.context_trace import strip_context_metadata

    messages = strip_context_metadata(messages)
    tools = strip_context_metadata(tools) if tools is not None else None
    model = _provider_value(arguments, context, provider, "model")
    if not model:
        raise ValueError(f"{provider.name} model is not configured")
    started = time.perf_counter()
    stream_callback = context.services.get("model_stream")
    if not callable(stream_callback):
        stream_callback = None

    if provider.adapter == "codex_oauth":
        from cyrene.model.codex_provider import get_codex_provider

        message = await get_codex_provider().complete(
            messages=messages,
            tools=tools,
            model=model,
            phase=str(context.data.get("model_call_kind") or "model"),
            reasoning_effort=str(arguments.get("reasoning_effort") or ""),
            timeout=_timeout(context, provider),
            stream_callback=stream_callback,
        )
        if not isinstance(message, Mapping):
            raise RuntimeError("Codex OAuth returned no assistant message")
        return _normalized_result(
            message,
            provider,
            context,
            response_id=str(message.get("id") or message.get("response_id") or ""),
            model=str(message.get("model") or model),
            latency_ms=(time.perf_counter() - started) * 1000,
            endpoint="codex://oauth",
        )

    if provider.adapter == "local_onnx":
        raise ValueError("Local ONNX is an embedding model; use the embed operation")

    from cyrene.model.protocol_adapters import (
        NATIVE_PROTOCOL_ADAPTERS,
        PreparedRequest,
        prepare_request,
        protocol_endpoints,
        runtime_adapter_for_provider,
    )

    connection = _connection(context)
    adapter = runtime_adapter_for_provider(
        str(connection.get("adapter") or provider.adapter),
        model,
        provider_preset=provider.id,
    )
    base_url = _provider_value(arguments, context, provider, "base_url").rstrip("/")
    api_key = _provider_value(arguments, context, provider, "api_key")
    if provider.auth_type == "api_key" and not api_key:
        raise ValueError(f"{provider.name} API key is not configured")
    endpoints = protocol_endpoints(adapter, base_url, model)
    if not endpoints:
        raise ValueError(f"{provider.name} has no callable endpoint")

    if adapter in NATIVE_PROTOCOL_ADAPTERS:
        prepared = prepare_request(
            adapter,
            api_key=api_key,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=(
                int(arguments["max_tokens"])
                if arguments.get("max_tokens") is not None
                else None
            ),
            stream=True,
            response_format=(
                dict(arguments["response_format"])
                if isinstance(arguments.get("response_format"), Mapping)
                else None
            ),
            reasoning_effort=str(arguments.get("reasoning_effort") or ""),
            tool_choice=arguments.get("tool_choice"),
        )
        payload = prepared.payload
        headers = prepared.headers
    else:
        payload = _openai_payload(arguments, provider, model)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    preferred_endpoint = str(_candidate(context).get("preferred_endpoint") or "")
    ordered_endpoints = list(endpoints)
    if preferred_endpoint in ordered_endpoints:
        ordered_endpoints.remove(preferred_endpoint)
        ordered_endpoints.insert(0, preferred_endpoint)

    from cyrene.model.error_details import (
        ModelCallError,
        ModelErrorDetails,
        classify_model_error,
        preferred_model_error,
    )

    failures: list[str] = []
    public_failures: list[ModelErrorDetails] = []
    failure_diagnostics: list[Mapping[str, Any] | None] = []
    retry_state: dict[str, int] = {}
    async with httpx.AsyncClient(
        **_client_options(context, provider, discovery=False)
    ) as client:
        for endpoint_index, endpoint in enumerate(ordered_endpoints):
            try:
                request = (
                    prepared
                    if adapter in NATIVE_PROTOCOL_ADAPTERS
                    else PreparedRequest(payload, headers)
                )
                return await _complete_stream_endpoint(
                    adapter=adapter, client=client, endpoint=endpoint, request=request,
                    stream_callback=stream_callback, provider=provider, context=context,
                    model=model, started=started,
                    has_fallback=endpoint_index + 1 < len(ordered_endpoints),
                    retry_state=retry_state,
                )
            except Exception as exc:
                public_failures.append(classify_model_error(exc))
                diagnostics = getattr(exc, "diagnostics", None)
                failure_diagnostics.append(
                    dict(diagnostics) if isinstance(diagnostics, Mapping) else None
                )
                failures.append(
                    f"{endpoint}: {type(exc).__name__}: {exc!r}"
                )

    logger.warning(
        "%s failed on every endpoint: %s",
        provider.name,
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


def _normalize_embedding_vectors(
    vectors: list[list[Any]],
    *,
    expected: int,
) -> list[list[float]]:
    if len(vectors) != expected:
        raise RuntimeError(f"Expected {expected} embeddings, got {len(vectors)}")
    vector_size = len(vectors[0]) if vectors else 0
    if vector_size == 0 or any(len(vector) != vector_size for vector in vectors):
        raise RuntimeError("Embedding Provider returned inconsistent dimensions")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for vector in vectors
        for value in vector
    ):
        raise RuntimeError("Embedding Provider returned an invalid vector")
    normalized: list[list[float]] = []
    for vector in vectors:
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        if norm <= 0:
            raise RuntimeError("Embedding Provider returned a zero vector")
        normalized.append([float(value) / norm for value in vector])
    return normalized


async def embed_model(
    arguments: dict[str, Any],
    context: PluginContext,
    provider: ModelProvider,
) -> dict[str, Any]:
    if "embedding" not in provider.capabilities:
        raise ValueError(f"{provider.name} does not support embeddings")
    values = arguments.get("inputs")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError("inputs must be an array of strings")
    texts = [str(value) for value in values]
    if not texts:
        raise ValueError("inputs must be a non-empty array")
    model = _provider_value(arguments, context, provider, "model")
    if not model:
        raise ValueError(f"{provider.name} model is not configured")
    try:
        dimensions = int(
            arguments.get("dimensions") or _profile(context).get("dimensions") or 0
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("dimensions must be an integer") from exc
    if dimensions < 0 or dimensions > 65_536:
        raise ValueError("dimensions must be between 0 and 65536")

    base_url = _provider_value(arguments, context, provider, "base_url").rstrip("/")
    if not base_url:
        raise ValueError(f"{provider.name} base URL is not configured")
    api_key = _provider_value(arguments, context, provider, "api_key")
    if provider.auth_type == "api_key" and not api_key:
        raise ValueError(f"{provider.name} API key is not configured")

    is_ollama = provider.adapter == "ollama"
    if is_ollama and base_url.endswith("/v1"):
        base_url = base_url[:-3].rstrip("/")
    endpoint = f"{base_url}/api/embed" if is_ollama else f"{base_url}/embeddings"
    payload: dict[str, Any] = {"model": model, "input": texts}
    if dimensions:
        payload["dimensions"] = dimensions
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(
        **_client_options(context, provider, discovery=False)
    ) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, Mapping):
        raise RuntimeError(f"{provider.name} returned an invalid embedding response")
    if is_ollama:
        raw_vectors = body.get("embeddings")
        if isinstance(raw_vectors, list) and raw_vectors and isinstance(
            raw_vectors[0], (int, float)
        ):
            raw_vectors = [raw_vectors]
        vectors = [
            list(item)
            for item in (raw_vectors if isinstance(raw_vectors, list) else [])
            if isinstance(item, list)
        ]
    else:
        items = [
            item
            for item in (body.get("data") if isinstance(body.get("data"), list) else [])
            if isinstance(item, Mapping)
        ]
        items.sort(key=lambda item: int(item.get("index") or 0))
        vectors = [
            list(item["embedding"])
            for item in items
            if isinstance(item.get("embedding"), list)
        ]
    embeddings = _normalize_embedding_vectors(vectors, expected=len(texts))
    return {
        "model": str(body.get("model") or model),
        "embeddings": embeddings,
        "dimensions": len(embeddings[0]) if embeddings else 0,
    }


async def embed_local_model(
    arguments: dict[str, Any],
    _context: PluginContext,
    provider: ModelProvider,
) -> dict[str, Any]:
    values = arguments.get("inputs")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError("inputs must be an array of strings")
    texts = [str(value) for value in values]
    if not texts:
        raise ValueError("inputs must be a non-empty array")
    requested_model = str(arguments.get("model") or provider.default_model).strip()
    if requested_model != provider.default_model:
        raise ValueError(f"Local ONNX does not provide model {requested_model!r}")
    from cyrene.core.plugin import application_plugin_service

    knowledge = application_plugin_service("knowledge")
    if knowledge is None:
        raise RuntimeError("Local ONNX requires the knowledge Plugin")
    vectors = await knowledge.embed_local_texts(
        texts,
        query=str(arguments.get("input_type") or "document") == "query",
    )
    requested_dimensions = int(arguments.get("dimensions") or 0)
    actual_dimensions = len(vectors[0]) if vectors else 0
    if requested_dimensions and requested_dimensions != actual_dimensions:
        raise ValueError(
            f"Local ONNX produces {actual_dimensions} dimensions, "
            f"not {requested_dimensions}"
        )
    return {
        "model": provider.default_model or "qwen3-embedding-0.6b",
        "embeddings": vectors,
        "dimensions": actual_dimensions,
    }


def model_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["complete", "embed", "list_models"],
                "default": "complete",
            },
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
            "model": {"type": "string"},
            "inputs": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "input_type": {
                "type": "string",
                "enum": ["document", "query"],
                "default": "document",
            },
            "dimensions": {
                "type": "integer",
                "minimum": 0,
                "maximum": 65536,
            },
            "response_format": {"type": "object"},
            "reasoning_effort": {"type": "string"},
        },
        "anyOf": [
            {
                "required": ["messages"],
                "properties": {"operation": {"const": "complete"}},
            },
            {
                "required": ["operation"],
                "properties": {"operation": {"const": "list_models"}},
            },
            {
                "required": ["operation", "inputs"],
                "properties": {"operation": {"const": "embed"}},
            },
        ],
        "additionalProperties": False,
    }


def create_model_plugin(provider: ModelProvider) -> Plugin:
    async def handler(arguments: dict[str, Any], context: PluginContext) -> dict[str, Any]:
        from cyrene.model.error_details import (
            ModelCallError,
            classify_model_error,
        )
        from cyrene.model.protocol_adapters import ModelStreamError

        operation = str(arguments.get("operation") or "complete")
        try:
            if operation == "list_models":
                return await discover_models(provider, context)
            if operation == "embed":
                return await embed_model(arguments, context, provider)
            return await complete_model(arguments, context, provider)
        except ModelCallError:
            raise
        except ModelStreamError as exc:
            raise ModelCallError(
                classify_model_error(exc),
                diagnostics=exc.diagnostics,
            ) from exc
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise ModelCallError(classify_model_error(exc)) from exc
        except (TypeError, ValueError) as exc:
            if operation != "list_models":
                raise
            raise ModelCallError(classify_model_error(exc)) from exc

    return Plugin(
        name=provider.plugin_name,
        description=(
            f"Call {provider.name} and discover the models available to its configured connection."
        ),
        input_schema=model_input_schema(),
        handler=handler,
        kind="model",
        # Model streams may remain healthy and productive for longer than a
        # fixed wall-clock deadline. Transport-level idle timeouts and the
        # owning chat run's cancellation lifecycle still bound failed calls.
        metadata=provider.metadata(),
    )


def create_local_model_plugin(provider: ModelProvider) -> Plugin:
    async def handler(arguments: dict[str, Any], context: PluginContext) -> dict[str, Any]:
        if str(arguments.get("operation") or "embed") == "list_models":
            return await discover_models(provider, context)
        return await embed_local_model(arguments, context, provider)

    return Plugin(
        name=provider.plugin_name,
        description="Run Cyrene's locally managed ONNX embedding model.",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["embed", "list_models"],
                    "default": "embed",
                },
                "inputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "model": {"type": "string"},
                "input_type": {
                    "type": "string",
                    "enum": ["document", "query"],
                    "default": "document",
                },
                "dimensions": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 65536,
                },
            },
            "anyOf": [
                {
                    "required": ["inputs"],
                    "properties": {"operation": {"const": "embed"}},
                },
                {
                    "required": ["operation"],
                    "properties": {"operation": {"const": "list_models"}},
                },
            ],
            "additionalProperties": False,
        },
        handler=handler,
        kind="model",
        # Local model work is likewise governed by the owning run rather than
        # an unrelated Plugin wall-clock deadline.
        metadata=provider.metadata(),
    )


__all__ = [
    "ModelProvider",
    "complete_model",
    "create_local_model_plugin",
    "create_model_plugin",
    "discover_models",
    "embed_model",
    "model_input_schema",
    "usage_observation",
]
