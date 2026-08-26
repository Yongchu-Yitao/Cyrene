"""Editable MiniMax OpenAI-compatible model Plugin."""

from __future__ import annotations

import json
import os
import hashlib
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

from agent.plugin import Plugin, PluginContext

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M2.7"


def _environment(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip().strip('"').strip("'")


def _endpoint(base_url: str) -> str:
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


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


def _tool_calls(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    raw_calls = message.get("tool_calls")
    for raw in raw_calls if isinstance(raw_calls, list) else ():
        if not isinstance(raw, Mapping):
            raise ValueError("MiniMax returned an invalid tool call")
        function = raw.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("MiniMax tool call is missing function")
        name = str(function.get("name") or "").strip()
        if not name:
            raise ValueError("MiniMax tool call is missing a name")
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"MiniMax returned invalid arguments for {name}") from exc
        if not isinstance(arguments, Mapping):
            raise ValueError(f"MiniMax arguments for {name} must be an object")
        normalized.append(
            {
                "id": str(raw.get("id") or f"call_{uuid4().hex}"),
                "name": name,
                "arguments": dict(arguments),
            }
        )
    return normalized


def _tool_choice(value: Any) -> str | dict[str, Any]:
    """Validate the small OpenAI-compatible tool choice surface we expose."""

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


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_observation(usage: Mapping[str, Any]) -> dict[str, Any]:
    prompt_tokens = _integer(usage.get("prompt_tokens"))
    completion_tokens = _integer(usage.get("completion_tokens"))
    total_tokens = _integer(usage.get("total_tokens"))
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, Mapping) else {}
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
    *,
    response_id: str,
    model: str,
    usage: Mapping[str, Any],
    latency_ms: float,
) -> str:
    if context.tree is None or not context.tree_id or not context.node_id:
        return ""
    normalized = _usage_observation(usage)
    identity = response_id or f"{time.time_ns()}"
    node_id = "model_observation_" + hashlib.sha256(
        f"{context.tree_id}:{context.node_id}:{identity}".encode("utf-8")
    ).hexdigest()[:32]
    node = context.tree.mount(
        context.tree_id,
        context.node_id,
        {
            "role": "model_observation",
            "provider": "minimax",
            "model": model,
            "call_kind": str(context.data.get("model_call_kind") or "model"),
            "response_id": response_id,
            "latency_ms": max(0.0, float(latency_ms)),
            "usage": normalized,
        },
        node_id=node_id,
    )
    context.tree.report_context_used(
        context.tree_id,
        context.node_id,
        int(normalized["prompt_tokens"]),
        token_limit=204_800,
    )
    return node.id


async def minimax(arguments: dict[str, Any], context: PluginContext) -> dict[str, Any]:
    api_key = _environment("MINIMAX_API_KEY")
    if not api_key:
        raise ValueError("MINIMAX_API_KEY is not configured")
    messages = arguments.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    tools = arguments.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise ValueError("tools must be an array")
    model = _environment("MINIMAX_MODEL", DEFAULT_MODEL)
    base_url = _environment("MINIMAX_BASE_URL", DEFAULT_BASE_URL)
    timeout = float(_environment("MINIMAX_TIMEOUT", "180"))
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "reasoning_split": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = _tool_choice(arguments.get("tool_choice"))
    if arguments.get("max_tokens") is not None:
        payload["max_tokens"] = int(arguments["max_tokens"])
    if arguments.get("temperature") is not None:
        payload["temperature"] = float(arguments["temperature"])

    client_options: dict[str, Any] = {"timeout": timeout}
    transport = context.data.get("http_transport")
    if isinstance(transport, httpx.AsyncBaseTransport):
        client_options["transport"] = transport
    started = time.perf_counter()
    async with httpx.AsyncClient(**client_options) as client:
        response = await client.post(
            _endpoint(base_url),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.is_error:
        detail = response.text.strip().replace("\n", " ")[:500]
        raise RuntimeError(f"MiniMax HTTP {response.status_code}: {detail}")
    body = response.json()
    choices = body.get("choices") if isinstance(body, Mapping) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("MiniMax returned no choices")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    if not isinstance(message, Mapping):
        raise RuntimeError("MiniMax returned no assistant message")
    content = message.get("content")
    reasoning = _reasoning_text(message)
    usage = dict(body.get("usage") or {})
    response_id = str(body.get("id") or "")
    returned_model = str(body.get("model") or model)
    observation_node_id = _mount_observation(
        context,
        response_id=response_id,
        model=returned_model,
        usage=usage,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
    return {
        "content": content if isinstance(content, str) else "",
        "reasoning": reasoning,
        "reasoning_details": _reasoning_details(message, reasoning),
        "tool_calls": _tool_calls(message),
        "finish_reason": str(choice.get("finish_reason") or ""),
        "usage": usage,
        "usage_observation": _usage_observation(usage),
        "model": returned_model,
        "response_id": response_id,
        "observation_node_id": observation_node_id,
    }


MINIMAX_PLUGIN = Plugin(
    name="MiniMax",
    description="Call MiniMax through its OpenAI-compatible chat completion API.",
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
        },
        "required": ["messages"],
        "additionalProperties": False,
    },
    handler=minimax,
    kind="model",
)


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "MINIMAX_PLUGIN",
    "minimax",
]
