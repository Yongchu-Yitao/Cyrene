"""Wire-protocol adapters for remote model providers.

Cyrene's internal message contract is OpenAI-shaped because it is convenient
for tool loops, but a configured adapter must speak its provider's native
protocol.  This module performs that boundary conversion for Anthropic
Messages, OpenAI Responses, and Gemini generateContent.  OpenAI Chat
Completions (including the legacy ``openai_compatible`` id) stays on the
well-tested path in :mod:`cyrene.model_runtime.client`.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlsplit

import httpx


NATIVE_PROTOCOL_ADAPTERS = frozenset({"anthropic", "openai_responses", "gemini"})
OPENAI_CHAT_ADAPTERS = frozenset({"openai", "openai_compatible", "ollama"})
_OFFICIAL_VERSIONED_OPENAI_CHAT_HOSTS = frozenset({
    "api.deepseek.com",
    "api.moonshot.cn",
    "api.minimax.com",
    "api.minimax.io",
    "api.minimaxi.com",
})

_OPENCODE_GO_ANTHROPIC_MODEL_PREFIXES = (
    "minimax-",
    "qwen3.",
)
_OPENCODE_GO_RESPONSES_MODEL_PREFIXES = (
    "gpt-",
    "grok-",
    "muse-",
)


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    payload: dict[str, Any]
    headers: dict[str, str]


def runtime_adapter_for_provider(
    adapter_id: str,
    model: str,
    *,
    provider_preset: str = "",
) -> str:
    """Resolve provider presets whose models use more than one wire protocol.

    OpenCode Go publishes one model catalog backed by Chat Completions,
    Responses, and Anthropic Messages endpoints. The durable connection stays
    a single service while each selected profile is routed over the protocol
    documented for its model family.
    """

    adapter = str(adapter_id or "openai_compatible").strip().lower()
    preset = str(provider_preset or "").strip().lower()
    if preset != "opencode_go":
        return adapter
    model_id = str(model or "").strip().lower().rsplit("/", 1)[-1]
    if model_id.startswith(_OPENCODE_GO_ANTHROPIC_MODEL_PREFIXES):
        return "anthropic"
    if model_id.startswith(_OPENCODE_GO_RESPONSES_MODEL_PREFIXES):
        return "openai_responses"
    return "openai"


def official_versioned_chat_endpoint(base_url: str) -> str | None:
    """Return the sole supported Chat Completions route for known providers."""
    base = str(base_url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(base)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in _OFFICIAL_VERSIONED_OPENAI_CHAT_HOSTS
        or port not in {None, 443}
        or parsed.path.lower() not in {"", "/v1"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"https://{host}/v1/chat/completions"


def protocol_endpoints(adapter_id: str, base_url: str, model: str) -> list[str]:
    adapter = str(adapter_id or "openai_compatible").strip().lower()
    base = str(base_url or "").strip().rstrip("/")
    if adapter == "openai_responses":
        return [f"{base}/responses"]
    if adapter == "anthropic":
        return [f"{base}/messages"]
    if adapter == "gemini":
        model_id = str(model or "").strip()
        if model_id.startswith("models/"):
            model_id = model_id[len("models/"):]
        return [f"{base}/models/{quote(model_id, safe='')}:generateContent"]
    if adapter == "ollama":
        compatibility_base = base if base.endswith("/v1") else f"{base}/v1"
        return [f"{compatibility_base}/chat/completions"]
    official_endpoint = official_versioned_chat_endpoint(base)
    if official_endpoint:
        return [official_endpoint]
    return [f"{base}/chat/completions"]


def discovery_request(adapter_id: str, base_url: str, api_key: str) -> tuple[str, dict[str, str]]:
    adapter = str(adapter_id or "openai_compatible").strip().lower()
    base = str(base_url or "").strip().rstrip("/")
    if adapter == "ollama":
        return f"{base}/api/tags", {}
    if adapter == "anthropic":
        return f"{base}/models", {
            "x-api-key": str(api_key or ""),
            "anthropic-version": "2023-06-01",
        }
    if adapter == "gemini":
        return f"{base}/models", {"x-goog-api-key": str(api_key or "")}
    official_endpoint = official_versioned_chat_endpoint(base)
    if official_endpoint:
        base = official_endpoint.removesuffix("/chat/completions")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return f"{base}/models", headers


def parse_discovery_response(adapter_id: str, payload: Any) -> list[dict[str, Any]]:
    adapter = str(adapter_id or "openai_compatible").strip().lower()
    source = payload if isinstance(payload, dict) else {}
    raw_items: Any
    if adapter == "ollama":
        raw_items = source.get("models")
    elif adapter == "gemini":
        raw_items = source.get("models")
    else:
        raw_items = source.get("data")
    result: list[dict[str, Any]] = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        model_id = str(
            item.get("id") or item.get("name") or item.get("model") or ""
        ).strip()
        if adapter == "gemini" and model_id.startswith("models/"):
            model_id = model_id[len("models/"):]
        if not model_id:
            continue
        if adapter == "gemini":
            methods = item.get("supportedGenerationMethods") or []
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
        capabilities = ["chat"]
        lowered = model_id.lower()
        if "embed" in lowered:
            capabilities = ["embedding"]
        elif adapter in {"anthropic", "gemini"}:
            capabilities.extend(["vision", "tools", "reasoning"])
        else:
            architecture = (
                item.get("architecture")
                if isinstance(item.get("architecture"), dict)
                else {}
            )
            input_modalities = architecture.get("input_modalities") or []
            if not isinstance(input_modalities, list):
                input_modalities = []
            supported_parameters = item.get("supported_parameters") or []
            if not isinstance(supported_parameters, list):
                supported_parameters = []
            if "image" in input_modalities or any(
                token in lowered for token in ("vision", "gpt-4o", "gpt-5")
            ):
                capabilities.append("vision")
            if "tools" in supported_parameters or "tool_choice" in supported_parameters:
                capabilities.append("tools")
            if "reasoning" in supported_parameters or "include_reasoning" in supported_parameters:
                capabilities.append("reasoning")
        discovered = {
            "id": model_id,
            "model": model_id,
            "name": str(
                item.get("displayName")
                or item.get("display_name")
                or item.get("name")
                or model_id
            ),
            "capabilities": list(dict.fromkeys(capabilities)),
        }
        try:
            context_limit = int(item.get("context_length") or 0)
        except (TypeError, ValueError):
            context_limit = 0
        if context_limit > 0:
            discovered["context_limit"] = context_limit
        result.append(discovered)
    return result


def _data_uri(value: str) -> tuple[str, str] | None:
    source = str(value or "")
    if not source.startswith("data:") or ";base64," not in source:
        return None
    header, data = source.split(",", 1)
    media_type = header[5:].split(";", 1)[0] or "application/octet-stream"
    return media_type, data


def _openai_image_url(block: dict[str, Any]) -> str:
    image = block.get("image_url")
    if isinstance(image, dict):
        return str(image.get("url") or "")
    return str(image or block.get("url") or "")


def _tool_definitions(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "description": str(function.get("description") or ""),
            "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object", "properties": {}},
        })
    return result


def _anthropic_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    result: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "input_text", "output_text"}:
            result.append({"type": "text", "text": str(block.get("text") or "")})
        elif block.get("type") in {"image_url", "input_image"}:
            url = _openai_image_url(block)
            inline = _data_uri(url)
            if inline:
                result.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": inline[0], "data": inline[1]},
                })
            elif url.startswith(("http://", "https://")):
                result.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                raise ValueError("Anthropic image input requires an HTTPS URL or base64 data URI")
    return result


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    tool_names: dict[str, str] = {}
    for message in messages:
        role = str(message.get("role") or "user")
        if role in {"system", "developer"}:
            system_parts.append(str(message.get("content") or ""))
            continue
        if role == "assistant":
            content = _anthropic_content(message.get("content"))
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else {}
                call_id = str(call.get("id") or f"toolu_{uuid.uuid4().hex}")
                name = str((function or {}).get("name") or "")
                tool_names[call_id] = name
                arguments = (function or {}).get("arguments") or "{}"
                try:
                    parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                except json.JSONDecodeError:
                    parsed = {"value": str(arguments)}
                content.append({"type": "tool_use", "id": call_id, "name": name, "input": parsed})
            if content:
                converted.append({"role": "assistant", "content": content})
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            block = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": str(message.get("content") or ""),
            }
            if converted and converted[-1].get("role") == "user" and all(
                isinstance(item, dict) and item.get("type") == "tool_result"
                for item in converted[-1].get("content") or []
            ):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        user_content = _anthropic_content(message.get("content"))
        if user_content:
            converted.append({"role": "user", "content": user_content})
    return "\n\n".join(part for part in system_parts if part), converted


def _responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            result.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or ""),
                "output": str(message.get("content") or ""),
            })
            continue
        content = message.get("content")
        blocks: list[dict[str, Any]] = []
        if isinstance(content, str):
            blocks = [{"type": "input_text", "text": content}] if content else []
        else:
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in {"text", "input_text", "output_text"}:
                    text = str(block.get("text") or "")
                    if text:
                        blocks.append({"type": "input_text", "text": text})
                elif block.get("type") in {"image_url", "input_image"}:
                    blocks.append({"type": "input_image", "image_url": _openai_image_url(block)})
        result.append({"type": "message", "role": role, "content": blocks})
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else {}
                result.append({
                    "type": "function_call",
                    "call_id": str(call.get("id") or ""),
                    "name": str((function or {}).get("name") or ""),
                    "arguments": str((function or {}).get("arguments") or "{}"),
                })
    return result


def _gemini_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}] if content else []
    result: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "input_text", "output_text"}:
            result.append({"text": str(block.get("text") or "")})
        elif block.get("type") in {"image_url", "input_image"}:
            url = _openai_image_url(block)
            inline = _data_uri(url)
            if not inline:
                raise ValueError("Gemini image input requires a base64 data URI")
            result.append({"inlineData": {"mimeType": inline[0], "data": inline[1]}})
    return result


def _gemini_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system_parts: list[dict[str, Any]] = []
    contents: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}
    for message in messages:
        role = str(message.get("role") or "user")
        if role in {"system", "developer"}:
            system_parts.extend(_gemini_parts(message.get("content")))
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            name = call_names.get(call_id) or str(message.get("name") or "tool")
            raw = message.get("content")
            try:
                response = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                response = {"result": str(raw or "")}
            if not isinstance(response, dict):
                response = {"result": response}
            contents.append({"role": "user", "parts": [{"functionResponse": {"name": name, "response": response}}]})
            continue
        parts = _gemini_parts(message.get("content"))
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else {}
                call_id = str(call.get("id") or "")
                name = str((function or {}).get("name") or "")
                call_names[call_id] = name
                arguments = (function or {}).get("arguments") or "{}"
                try:
                    args = json.loads(arguments) if isinstance(arguments, str) else arguments
                except json.JSONDecodeError:
                    args = {"value": str(arguments)}
                parts.append({"functionCall": {"name": name, "args": args}})
        if parts:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    return system_parts, contents


def prepare_request(
    adapter_id: str,
    *,
    api_key: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model: str,
    max_tokens: int | None,
    stream: bool,
    response_format: dict[str, Any] | None,
    reasoning_effort: str = "",
    tool_choice: str | dict[str, Any] | None = None,
) -> PreparedRequest:
    adapter = str(adapter_id or "").strip().lower()
    tool_defs = _tool_definitions(tools)
    if adapter == "anthropic":
        system, converted = _anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": max(1, int(max_tokens or 4096)),
            "stream": bool(stream),
        }
        if system:
            payload["system"] = system
        if tool_defs:
            payload["tools"] = [
                {"name": item["name"], "description": item["description"], "input_schema": item["parameters"]}
                for item in tool_defs
            ]
            # Anthropic's native Messages API has no schema-preserving "none"
            # mode. Keep the definitions stable and rely on the explicit final
            # synthesis instruction when callers disable tool selection.
            payload["tool_choice"] = (
                tool_choice
                if isinstance(tool_choice, dict)
                else {"type": "auto"}
            )
        if response_format is not None:
            raise ValueError("Anthropic adapter does not yet support response_format")
        return PreparedRequest(payload, {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        })
    if adapter == "openai_responses":
        payload = {"model": model, "input": _responses_input(messages), "stream": bool(stream)}
        if max_tokens is not None:
            payload["max_output_tokens"] = int(max_tokens)
        if tool_defs:
            payload["tools"] = [
                {"type": "function", "name": item["name"], "description": item["description"], "parameters": item["parameters"]}
                for item in tool_defs
            ]
            payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
        if response_format is not None:
            canonical_format = dict(response_format)
            if canonical_format.get("type") == "json_schema":
                nested = canonical_format.pop("json_schema", None)
                if isinstance(nested, dict):
                    canonical_format.update(nested)
                    canonical_format["type"] = "json_schema"
            payload["text"] = {"format": canonical_format}
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        return PreparedRequest(payload, {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        })
    if adapter == "gemini":
        system_parts, contents = _gemini_messages(messages)
        payload = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        generation: dict[str, Any] = {}
        if max_tokens is not None:
            generation["maxOutputTokens"] = int(max_tokens)
        if response_format is not None:
            generation["responseMimeType"] = "application/json"
            if response_format.get("type") == "json_schema":
                schema = response_format.get("json_schema")
                if isinstance(schema, dict) and isinstance(schema.get("schema"), dict):
                    generation["responseSchema"] = schema["schema"]
        if generation:
            payload["generationConfig"] = generation
        if tool_defs:
            payload["tools"] = [{"functionDeclarations": tool_defs}]
            mode = (
                "NONE"
                if str(tool_choice or "").strip().lower() == "none"
                else "AUTO"
            )
            payload["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
        return PreparedRequest(payload, {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        })
    raise ValueError(f"adapter {adapter!r} does not use the native protocol layer")


def _usage(adapter: str, data: dict[str, Any]) -> dict[str, int]:
    if adapter == "gemini":
        raw = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
        prompt = int(raw.get("promptTokenCount") or 0)
        completion = int(raw.get("candidatesTokenCount") or raw.get("thoughtsTokenCount") or 0)
        result = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": int(raw.get("totalTokenCount") or prompt + completion)}
        if isinstance(raw.get("cachedContentTokenCount"), int):
            result["prompt_cache_hit_tokens"] = int(raw["cachedContentTokenCount"])
        return result
    raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt = int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0)
    completion = int(raw.get("output_tokens") or raw.get("completion_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": int(raw.get("total_tokens") or prompt + completion)}


def parse_response(adapter_id: str, data: dict[str, Any]) -> dict[str, Any]:
    adapter = str(adapter_id or "").strip().lower()
    message: dict[str, Any] = {"role": "assistant", "content": ""}
    tool_calls: list[dict[str, Any]] = []
    reasoning: list[str] = []
    finish = ""
    if adapter == "anthropic":
        text: list[str] = []
        for block in data.get("content") if isinstance(data.get("content"), list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text.append(str(block.get("text") or ""))
            elif block.get("type") in {"thinking", "redacted_thinking"}:
                reasoning.append(str(block.get("thinking") or ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": str(block.get("id") or f"toolu_{uuid.uuid4().hex}"),
                    "type": "function",
                    "function": {"name": str(block.get("name") or ""), "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)},
                })
        message["content"] = "".join(text)
        finish = str(data.get("stop_reason") or "")
    elif adapter == "openai_responses":
        text = []
        for item in data.get("output") if isinstance(data.get("output"), list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for block in item.get("content") if isinstance(item.get("content"), list) else []:
                    if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                        text.append(str(block.get("text") or ""))
            elif item.get("type") == "function_call":
                tool_calls.append({
                    "id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
                    "type": "function",
                    "function": {"name": str(item.get("name") or ""), "arguments": str(item.get("arguments") or "{}")},
                })
            elif item.get("type") == "reasoning":
                for summary in item.get("summary") if isinstance(item.get("summary"), list) else []:
                    if isinstance(summary, dict):
                        reasoning.append(str(summary.get("text") or ""))
        message["content"] = "".join(text)
        finish = "completed" if data.get("status") == "completed" else str(data.get("status") or "")
    elif adapter == "gemini":
        text = []
        candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
        candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
        for part in content.get("parts") if isinstance(content.get("parts"), list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("thought") is True and isinstance(part.get("text"), str):
                reasoning.append(part["text"])
            elif isinstance(part.get("text"), str):
                text.append(part["text"])
            function = part.get("functionCall") if isinstance(part.get("functionCall"), dict) else None
            if function is not None:
                tool_calls.append({
                    "id": f"call_gemini_{uuid.uuid4().hex[:16]}",
                    "type": "function",
                    "function": {"name": str(function.get("name") or ""), "arguments": json.dumps(function.get("args") or {}, ensure_ascii=False)},
                })
        message["content"] = "".join(text)
        finish = str(candidate.get("finishReason") or "")
    else:
        raise ValueError(f"unsupported native adapter: {adapter}")
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    if finish:
        message["finish_reason"] = "length" if finish.lower() in {"max_tokens", "max_tokens_reached"} else finish.lower()
    message["usage"] = _usage(adapter, data)
    return message


async def _sse_payloads(response: httpx.Response):
    async for raw_line in response.aiter_lines():
        line = str(raw_line or "").strip()
        if not line.startswith("data:"):
            continue
        value = line[5:].strip()
        if not value or value == "[DONE]":
            continue
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


async def handle_stream(
    adapter_id: str,
    client: httpx.AsyncClient,
    endpoint: str,
    request: PreparedRequest,
    callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    timing: dict[str, float] | None = None,
) -> dict[str, Any]:
    adapter = str(adapter_id or "").strip().lower()
    target = endpoint
    if adapter == "gemini":
        target = endpoint.replace(":generateContent", ":streamGenerateContent") + "?alt=sse"
    request_started = time.monotonic()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[str, dict[str, Any]] = {}
    usage: dict[str, int] = {}
    finish = ""
    started = False
    reasoning_started = False

    async def emit_text(text: str) -> None:
        nonlocal started
        if not text:
            return
        if not started and callback:
            await callback({"type": "reply_start"})
            started = True
        text_parts.append(text)
        if callback:
            await callback({"type": "reply_delta", "delta": text})

    async with client.stream("POST", target, json=request.payload, headers=request.headers) as response:
        if timing is not None:
            timing["response_headers_ms"] = (time.monotonic() - request_started) * 1000
        response.raise_for_status()
        async for data in _sse_payloads(response):
            if timing is not None and "ttft_ms" not in timing:
                timing["ttft_ms"] = (time.monotonic() - request_started) * 1000
            if adapter == "anthropic":
                event_type = str(data.get("type") or "")
                if event_type == "message_start":
                    usage.update(_usage(adapter, data.get("message") or {}))
                elif event_type == "content_block_start":
                    block = data.get("content_block") if isinstance(data.get("content_block"), dict) else {}
                    if block.get("type") == "tool_use":
                        call_id = str(block.get("id") or f"toolu_{uuid.uuid4().hex}")
                        tool_calls[str(data.get("index") or 0)] = {"id": call_id, "name": str(block.get("name") or ""), "arguments": ""}
                    elif block.get("type") == "text":
                        await emit_text(str(block.get("text") or ""))
                elif event_type == "content_block_delta":
                    delta = data.get("delta") if isinstance(data.get("delta"), dict) else {}
                    if delta.get("type") == "text_delta":
                        await emit_text(str(delta.get("text") or ""))
                    elif delta.get("type") == "input_json_delta":
                        tool_calls.setdefault(str(data.get("index") or 0), {"id": f"toolu_{uuid.uuid4().hex}", "name": "", "arguments": ""})["arguments"] += str(delta.get("partial_json") or "")
                    elif delta.get("type") == "thinking_delta":
                        reasoning = str(delta.get("thinking") or "")
                        if reasoning:
                            if callback and not reasoning_started:
                                await callback({"type": "reasoning_start"})
                                reasoning_started = True
                            reasoning_parts.append(reasoning)
                            if callback:
                                await callback({"type": "reasoning_delta", "delta": reasoning})
                elif event_type == "message_delta":
                    finish = str((data.get("delta") or {}).get("stop_reason") or finish)
                    raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                    if raw_usage:
                        current = {"usage": raw_usage}
                        next_usage = _usage(adapter, current)
                        usage["completion_tokens"] = next_usage["completion_tokens"]
                        usage["total_tokens"] = int(usage.get("prompt_tokens") or 0) + next_usage["completion_tokens"]
            elif adapter == "openai_responses":
                event_type = str(data.get("type") or "")
                if event_type == "response.output_text.delta":
                    await emit_text(str(data.get("delta") or ""))
                elif event_type == "response.reasoning_summary_text.delta":
                    reasoning = str(data.get("delta") or "")
                    if reasoning:
                        if callback and not reasoning_started:
                            await callback({"type": "reasoning_start"})
                            reasoning_started = True
                        reasoning_parts.append(reasoning)
                        if callback:
                            await callback({"type": "reasoning_delta", "delta": reasoning})
                elif event_type == "response.output_item.added":
                    item = data.get("item") if isinstance(data.get("item"), dict) else {}
                    if item.get("type") == "function_call":
                        key = str(data.get("output_index") or len(tool_calls))
                        tool_calls[key] = {"id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"), "name": str(item.get("name") or ""), "arguments": str(item.get("arguments") or "")}
                elif event_type == "response.function_call_arguments.delta":
                    key = str(data.get("output_index") or 0)
                    tool_calls.setdefault(key, {"id": str(data.get("item_id") or f"call_{uuid.uuid4().hex}"), "name": str(data.get("name") or ""), "arguments": ""})["arguments"] += str(data.get("delta") or "")
                elif event_type == "response.completed":
                    completed = data.get("response") if isinstance(data.get("response"), dict) else {}
                    usage.update(_usage(adapter, completed))
                    finish = str(completed.get("status") or finish)
                    completed_message = parse_response(adapter, completed)
                    for call in completed_message.get("tool_calls") or []:
                        call_id = str(call.get("id") or "")
                        if not any(value.get("id") == call_id for value in tool_calls.values()):
                            tool_calls[str(len(tool_calls))] = {
                                "id": call_id,
                                "name": str((call.get("function") or {}).get("name") or ""),
                                "arguments": str((call.get("function") or {}).get("arguments") or "{}"),
                            }
            elif adapter == "gemini":
                parsed = parse_response(adapter, data)
                await emit_text(str(parsed.get("content") or ""))
                reasoning = str(parsed.get("reasoning_content") or "")
                if reasoning:
                    if callback and not reasoning_started:
                        await callback({"type": "reasoning_start"})
                        reasoning_started = True
                    reasoning_parts.append(reasoning)
                    if callback:
                        await callback({"type": "reasoning_delta", "delta": reasoning})
                for call_index, call in enumerate(parsed.get("tool_calls") or []):
                    function = call.get("function") or {}
                    key = f"gemini:{call_index}:{str(function.get('name') or '')}"
                    tool_calls[key] = {
                        "id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                usage.update(parsed.get("usage") or {})
                finish = str(parsed.get("finish_reason") or finish)

    if not started and callback:
        await callback({"type": "reply_start"})
    if reasoning_started and callback:
        await callback({"type": "reasoning_done", "response": "".join(reasoning_parts)})
    if callback:
        await callback({"type": "reply_done", "response": "".join(text_parts)})
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts), "usage": usage}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": value["id"],
                "type": "function",
                "function": {"name": value["name"], "arguments": value["arguments"] or "{}"},
            }
            for _key, value in sorted(tool_calls.items(), key=lambda item: item[0])
        ]
    if finish:
        message["finish_reason"] = "length" if finish.lower() in {"max_tokens", "max_tokens_reached"} else finish.lower()
    return message


__all__ = [
    "NATIVE_PROTOCOL_ADAPTERS",
    "OPENAI_CHAT_ADAPTERS",
    "PreparedRequest",
    "discovery_request",
    "handle_stream",
    "parse_discovery_response",
    "parse_response",
    "prepare_request",
    "protocol_endpoints",
    "runtime_adapter_for_provider",
]
