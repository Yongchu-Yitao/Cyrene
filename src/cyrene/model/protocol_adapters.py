"""Wire-protocol adapters for remote model providers.

Cyrene's internal message contract is OpenAI-shaped because it is convenient
for tool loops, but a configured adapter must speak its provider's native
protocol.  This module performs that boundary conversion for Anthropic
Messages, OpenAI Responses, Gemini generateContent, and OpenAI-compatible
Chat Completions for the editable model Provider Plugins.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

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


class ModelStreamError(RuntimeError):
    """A failed streaming response with content-free protocol diagnostics."""

    def __init__(
        self,
        kind: str,
        message: str,
        diagnostics: Mapping[str, Any],
    ) -> None:
        super().__init__(message)
        self.kind = str(kind)
        self.diagnostics = dict(diagnostics)


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


def _aliyun_bailian_discovery_url(base_url: str) -> str:
    """Map a Bailian OpenAI-compatible endpoint to its model catalog API."""

    parsed = urlsplit(str(base_url or "").strip().rstrip("/"))
    path = parsed.path.rstrip("/")
    compatible_suffix = "/compatible-mode/v1"
    if path.endswith(compatible_suffix):
        path = path[: -len(compatible_suffix)] + "/api/v1/models"
    else:
        path += "/models"
    query = urlencode({"page_no": 1, "page_size": 100})
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))


def discovery_request(
    adapter_id: str,
    base_url: str,
    api_key: str,
    *,
    provider_preset: str = "",
) -> tuple[str, dict[str, str]]:
    adapter = str(adapter_id or "openai_compatible").strip().lower()
    base = str(base_url or "").strip().rstrip("/")
    preset = str(provider_preset or "").strip().lower()
    if preset == "aliyun_bailian":
        return _aliyun_bailian_discovery_url(base), {
            "Authorization": f"Bearer {api_key}"
        }
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


def next_discovery_page(
    endpoint: str,
    payload: Any,
    *,
    provider_preset: str = "",
) -> str | None:
    """Return the next catalog page URL for a paginated Provider response."""

    if str(provider_preset or "").strip().lower() != "aliyun_bailian":
        return None
    source = payload if isinstance(payload, dict) else {}
    output = source.get("output") if isinstance(source.get("output"), dict) else {}
    try:
        page_no = max(1, int(output.get("page_no") or 1))
        page_size = int(output.get("page_size") or 0)
        total = max(0, int(output.get("total") or 0))
    except (TypeError, ValueError):
        return None
    if page_size <= 0 or page_no * page_size >= total:
        return None
    parsed = urlsplit(str(endpoint or ""))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page_no"] = str(page_no + 1)
    query["page_size"] = str(page_size)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _parse_aliyun_bailian_models(payload: Any) -> list[dict[str, Any]]:
    source = payload if isinstance(payload, dict) else {}
    if source.get("success") is False:
        detail = str(source.get("message") or source.get("code") or "").strip()
        raise ValueError(
            "Alibaba Cloud Model Studio model discovery failed"
            + (f": {detail}" if detail else "")
        )
    output = source.get("output") if isinstance(source.get("output"), dict) else {}
    raw_items = output.get("models") if isinstance(output.get("models"), list) else []
    result: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("model") or "").strip()
        if not model_id:
            continue
        raw_capabilities = {
            str(value or "").strip().lower()
            for value in item.get("capabilities") or []
            if str(value or "").strip()
        }
        features = {
            str(value or "").strip().lower()
            for value in item.get("features") or []
            if str(value or "").strip()
        }
        inference = (
            item.get("inference_metadata")
            if isinstance(item.get("inference_metadata"), dict)
            else {}
        )
        request_modalities = {
            str(value or "").strip().lower()
            for value in inference.get("request_modality") or []
            if str(value or "").strip()
        }
        response_modalities = {
            str(value or "").strip().lower()
            for value in inference.get("response_modality") or []
            if str(value or "").strip()
        }
        supports_chat = bool(
            raw_capabilities.intersection({"tg", "vu", "multimodal-omni"})
            or (
                "text" in request_modalities
                and "text" in response_modalities
            )
        )
        if not supports_chat:
            continue
        capabilities = ["chat"]
        if "image" in request_modalities or "vu" in raw_capabilities:
            capabilities.append("vision")
        if "function-calling" in features:
            capabilities.append("tools")
        if "reasoning" in raw_capabilities:
            capabilities.append("reasoning")
        discovered: dict[str, Any] = {
            "id": model_id,
            "model": model_id,
            "name": str(item.get("name") or model_id).strip() or model_id,
            "capabilities": capabilities,
        }
        description = str(item.get("description") or "").strip()
        if description:
            discovered["description"] = description
        model_info = item.get("model_info") if isinstance(item.get("model_info"), dict) else {}
        try:
            context_limit = int(model_info.get("context_window") or 0)
        except (TypeError, ValueError):
            context_limit = 0
        if context_limit > 0:
            discovered["context_limit"] = context_limit
        result.append(discovered)
    return result


def parse_discovery_response(
    adapter_id: str,
    payload: Any,
    *,
    provider_preset: str = "",
) -> list[dict[str, Any]]:
    adapter = str(adapter_id or "openai_compatible").strip().lower()
    preset = str(provider_preset or "").strip().lower()
    if preset == "aliyun_bailian":
        return _parse_aliyun_bailian_models(payload)
    if not isinstance(payload, dict):
        raise ValueError("invalid response: expected a model discovery object")
    source = payload
    raw_items: Any
    if adapter == "ollama":
        raw_items = source.get("models")
    elif adapter == "gemini":
        raw_items = source.get("models")
    else:
        raw_items = source.get("data")
    if not isinstance(raw_items, list):
        raise ValueError("invalid response: expected a model array")
    result: list[dict[str, Any]] = []
    for item in raw_items:
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


def _with_anthropic_cache_breakpoint(block: dict[str, Any]) -> dict[str, Any]:
    """Return one Anthropic content block marked as an explicit cache boundary."""

    return {**block, "cache_control": {"type": "ephemeral"}}


def _anthropic_cached_prompt(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Place cache breakpoints on protocol content blocks, never at request root."""

    cached_tools = [dict(tool) for tool in tools]
    if cached_tools:
        cached_tools[-1] = _with_anthropic_cache_breakpoint(cached_tools[-1])

    cached_system: str | list[dict[str, Any]] = system
    if system:
        cached_system = [
            _with_anthropic_cache_breakpoint({"type": "text", "text": system})
        ]

    cached_messages = [
        {**message, "content": [dict(block) for block in message.get("content") or []]}
        for message in messages
    ]
    if cached_messages and cached_messages[-1]["content"]:
        cached_messages[-1]["content"][-1] = _with_anthropic_cache_breakpoint(
            cached_messages[-1]["content"][-1]
        )
    return cached_system, cached_messages, cached_tools


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
        anthropic_tools = [
            {"name": item["name"], "description": item["description"], "input_schema": item["parameters"]}
            for item in tool_defs
        ]
        system, converted, anthropic_tools = _anthropic_cached_prompt(
            system,
            converted,
            anthropic_tools,
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": max(1, int(max_tokens or 4096)),
            "stream": bool(stream),
        }
        if system:
            payload["system"] = system
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            # Anthropic's native Messages API has no schema-preserving "none"
            # mode. Keep the definitions stable and rely on the explicit final
            # synthesis instruction when callers disable tool selection.
            if isinstance(tool_choice, dict):
                payload["tool_choice"] = tool_choice
            elif str(tool_choice or "").strip().lower() == "required":
                payload["tool_choice"] = {"type": "any"}
            else:
                payload["tool_choice"] = {"type": "auto"}
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
            normalized_choice = str(tool_choice or "").strip().lower()
            if normalized_choice == "none":
                mode = "NONE"
            elif normalized_choice == "required":
                mode = "ANY"
            else:
                mode = "AUTO"
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
            hit = max(0, int(raw["cachedContentTokenCount"]))
            result["prompt_cache_hit_tokens"] = hit
            result["prompt_cache_miss_tokens"] = max(0, prompt - hit)
        return result
    raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    if adapter == "anthropic":
        # Anthropic reports uncached, cache-write, and cache-read input as
        # disjoint buckets. Their sum is the complete prompt token count.
        uncached = max(0, int(raw.get("input_tokens") or 0))
        cache_write = max(0, int(raw.get("cache_creation_input_tokens") or 0))
        cache_read = max(0, int(raw.get("cache_read_input_tokens") or 0))
        prompt = uncached + cache_write + cache_read
        completion = max(0, int(raw.get("output_tokens") or 0))
        result = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
        if (
            raw.get("cache_creation_input_tokens") is not None
            or raw.get("cache_read_input_tokens") is not None
        ):
            result["prompt_cache_hit_tokens"] = cache_read
            result["prompt_cache_miss_tokens"] = uncached + cache_write
        return result
    prompt = int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0)
    completion = int(raw.get("output_tokens") or raw.get("completion_tokens") or 0)
    result = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": int(raw.get("total_tokens") or prompt + completion)}
    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
    details = details if isinstance(details, dict) else {}
    hit_value = next(
        (
            value
            for value in (
                raw.get("prompt_cache_hit_tokens"),
                raw.get("cache_hit_tokens"),
                raw.get("cached_tokens"),
                raw.get("cached_input_tokens"),
                raw.get("cache_read_input_tokens"),
                details.get("cached_tokens"),
            )
            if value is not None
        ),
        None,
    )
    miss_value = next(
        (
            value
            for value in (
                raw.get("prompt_cache_miss_tokens"),
                raw.get("cache_miss_tokens"),
                raw.get("cache_creation_input_tokens"),
            )
            if value is not None
        ),
        None,
    )
    if hit_value is not None:
        hit = max(0, int(hit_value or 0))
        result["prompt_cache_hit_tokens"] = hit
        result["prompt_cache_miss_tokens"] = (
            max(0, int(miss_value or 0))
            if miss_value is not None
            else max(0, prompt - hit)
        )
    elif miss_value is not None:
        result["prompt_cache_hit_tokens"] = 0
        result["prompt_cache_miss_tokens"] = max(0, int(miss_value or 0))
    return result


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


def _decode_stream_payload(
    value: str,
    diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    if diagnostics is not None:
        diagnostics["data_chunk_count"] = int(
            diagnostics.get("data_chunk_count") or 0
        ) + 1
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        if diagnostics is not None:
            diagnostics["invalid_json_line_count"] = int(
                diagnostics.get("invalid_json_line_count") or 0
            ) + 1
            diagnostics["termination_reason"] = "invalid_sse_json"
            diagnostics["stream_completed"] = False
        raise ModelStreamError(
            "protocol_invalid_json",
            "The model stream contained an invalid JSON event.",
            diagnostics or {},
        ) from exc
    if not isinstance(data, dict):
        if diagnostics is not None:
            diagnostics["termination_reason"] = "invalid_sse_event"
            diagnostics["stream_completed"] = False
        raise ModelStreamError(
            "protocol_invalid_event",
            "The model stream contained a non-object JSON event.",
            diagnostics or {},
        )
    if diagnostics is not None:
        diagnostics["event_count"] = int(
            diagnostics.get("event_count") or 0
        ) + 1
        diagnostics["last_event_type"] = str(data.get("type") or "")
    return data


async def _stream_payloads(
    response: httpx.Response,
    diagnostics: dict[str, Any] | None = None,
    protocol_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
):
    data_lines: list[str] = []
    try:
        async for raw_line in response.aiter_lines():
            if diagnostics is not None:
                diagnostics["line_count"] = int(diagnostics.get("line_count") or 0) + 1
            line = str(raw_line or "").strip()
            if protocol_trace is not None and line:
                try:
                    await protocol_trace({
                        "type": "response_line",
                        "sequence": int((diagnostics or {}).get("line_count") or 0),
                        "line": line,
                    })
                except Exception:
                    # Developer tracing is observational and must never affect a call.
                    pass
            if not line:
                if data_lines:
                    yield _decode_stream_payload("\n".join(data_lines), diagnostics)
                    data_lines.clear()
                continue
            if line.startswith(":") or line.startswith(("event:", "id:", "retry:")):
                continue
            if line.startswith("data:"):
                value = line[5:].lstrip()
                if value == "[DONE]":
                    if data_lines:
                        yield _decode_stream_payload("\n".join(data_lines), diagnostics)
                        data_lines.clear()
                    if diagnostics is not None:
                        diagnostics["saw_done_marker"] = True
                    continue
                data_lines.append(value)
                continue
            if data_lines:
                yield _decode_stream_payload("\n".join(data_lines), diagnostics)
                data_lines.clear()
            yield _decode_stream_payload(line, diagnostics)
        if data_lines:
            yield _decode_stream_payload("\n".join(data_lines), diagnostics)
    except ModelStreamError:
        raise
    except (httpx.TransportError, TimeoutError, OSError) as exc:
        if diagnostics is not None:
            diagnostics["termination_reason"] = "transport_interrupted"
            diagnostics["stream_completed"] = False
            diagnostics["transport_error_type"] = type(exc).__name__
        raise ModelStreamError(
            "transport_interrupted",
            "The model stream ended because its transport was interrupted.",
            diagnostics or {},
        ) from exc


def _tool_stream_diagnostics(
    tool_calls: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for key, value in sorted(tool_calls.items(), key=lambda item: item[0]):
        arguments = str(value.get("arguments") or "")
        validation = "valid_object"
        try:
            decoded = json.loads(arguments or "{}")
            if not isinstance(decoded, dict):
                validation = "not_object"
        except json.JSONDecodeError:
            validation = "invalid_json"
        calls.append({
            "index": key,
            "name": str(value.get("name") or ""),
            "arguments_length": len(arguments),
            "arguments_sha256": hashlib.sha256(
                arguments.encode("utf-8")
            ).hexdigest(),
            "arguments_validation": validation,
        })
    return calls


def _update_tool_stream_diagnostics(
    diagnostics: dict[str, Any],
    tool_calls: Mapping[str, Mapping[str, Any]],
) -> None:
    diagnostics["tool_calls"] = _tool_stream_diagnostics(tool_calls)


async def _openai_chat_stream_event(
    adapter: str,
    data: dict[str, Any],
    emit_text: Callable[[str], Awaitable[None]],
    callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    reasoning_parts: list[str],
    tool_calls: dict[str, dict[str, Any]],
    usage: dict[str, int],
    reasoning_started: bool,
) -> tuple[str, bool]:
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    if not delta and isinstance(choice.get("message"), dict):
        delta = choice["message"]
    await emit_text(str(delta.get("content") or ""))
    reasoning = str(delta.get("reasoning_content") or delta.get("reasoning") or "")
    if not reasoning:
        details = delta.get("reasoning_details")
        reasoning = "".join(
            str(detail.get("text") or "")
            for detail in details
            if isinstance(detail, dict)
        ) if isinstance(details, list) else ""
    if reasoning:
        if callback and not reasoning_started:
            await callback({"type": "reasoning_start"})
            reasoning_started = True
        reasoning_parts.append(reasoning)
        if callback:
            await callback({"type": "reasoning_delta", "delta": reasoning})
    raw_calls = delta.get("tool_calls")
    for raw_call in raw_calls if isinstance(raw_calls, list) else ():
        if not isinstance(raw_call, dict):
            continue
        key = str(raw_call.get("index") or 0)
        function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
        call = tool_calls.setdefault(key, {
            "id": str(raw_call.get("id") or f"call_{uuid.uuid4().hex}"),
            "name": "",
            "arguments": "",
        })
        if raw_call.get("id"):
            call["id"] = str(raw_call["id"])
        if function.get("name"):
            call["name"] += str(function["name"])
        if function.get("arguments"):
            call["arguments"] += str(function["arguments"])
    if isinstance(data.get("usage"), dict):
        usage.update(_usage(adapter, data))
    return str(choice.get("finish_reason") or ""), reasoning_started


def _completed_stream_message(
    text_parts: list[str],
    reasoning_parts: list[str],
    tool_calls: dict[str, dict[str, Any]],
    usage: dict[str, int],
    finish: str,
    response_id: str,
    returned_model: str,
    stream_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts),
        "usage": usage,
    }
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
    if response_id:
        message["response_id"] = response_id
    if returned_model:
        message["model"] = returned_model
    if stream_diagnostics is not None:
        message["stream_diagnostics"] = {
            **dict(stream_diagnostics),
            "finish_reason": str(message.get("finish_reason") or ""),
            "stream_completed": True,
            "tool_calls": _tool_stream_diagnostics(tool_calls),
        }
    return message


async def handle_stream(
    adapter_id: str,
    client: httpx.AsyncClient,
    endpoint: str,
    request: PreparedRequest,
    callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    timing: dict[str, float] | None = None,
    protocol_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
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
    response_id = ""
    returned_model = ""
    stream_diagnostics: dict[str, Any] = {
        "adapter": adapter,
        "line_count": 0,
        "data_chunk_count": 0,
        "event_count": 0,
        "invalid_json_line_count": 0,
        "saw_done_marker": False,
        "http_status": 0,
        "terminal_event_seen": False,
        "stream_completed": False,
        "termination_reason": "",
    }
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
        stream_diagnostics["http_status"] = int(response.status_code)
        if timing is not None:
            timing["response_headers_ms"] = (time.monotonic() - request_started) * 1000
        response.raise_for_status()
        if protocol_trace is not None:
            try:
                await protocol_trace({
                    "type": "response_start",
                    "adapter": adapter,
                    "status_code": int(response.status_code),
                })
            except Exception:
                pass
        async for data in _stream_payloads(
            response,
            stream_diagnostics,
            protocol_trace,
        ):
            if timing is not None and "ttft_ms" not in timing:
                timing["ttft_ms"] = (time.monotonic() - request_started) * 1000
            response_id = str(data.get("id") or response_id)
            returned_model = str(data.get("model") or returned_model)
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
                        next_usage = _usage(adapter, {"usage": raw_usage})
                        if any(
                            key in raw_usage
                            for key in (
                                "input_tokens",
                                "cache_creation_input_tokens",
                                "cache_read_input_tokens",
                            )
                        ):
                            usage["prompt_tokens"] = next_usage["prompt_tokens"]
                            for key in (
                                "prompt_cache_hit_tokens",
                                "prompt_cache_miss_tokens",
                            ):
                                if key in next_usage:
                                    usage[key] = next_usage[key]
                        if "output_tokens" in raw_usage:
                            usage["completion_tokens"] = next_usage["completion_tokens"]
                        usage["total_tokens"] = (
                            int(usage.get("prompt_tokens") or 0)
                            + int(usage.get("completion_tokens") or 0)
                        )
                elif event_type == "message_stop":
                    stream_diagnostics["terminal_event_seen"] = True
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
                    stream_diagnostics["terminal_event_seen"] = True
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
            elif adapter in OPENAI_CHAT_ADAPTERS:
                event_finish, reasoning_started = await _openai_chat_stream_event(
                    adapter, data, emit_text, callback, reasoning_parts,
                    tool_calls, usage, reasoning_started,
                )
                finish = event_finish or finish

            _update_tool_stream_diagnostics(stream_diagnostics, tool_calls)

    normalized_finish = (
        "length"
        if finish.lower() in {"max_tokens", "max_tokens_reached"}
        else finish.lower()
    )
    stream_diagnostics["finish_reason"] = normalized_finish
    terminal_seen = bool(
        normalized_finish
        or stream_diagnostics.get("saw_done_marker")
        or stream_diagnostics.get("terminal_event_seen")
    )
    if not terminal_seen:
        stream_diagnostics["termination_reason"] = "eof_without_terminal_event"
        _update_tool_stream_diagnostics(stream_diagnostics, tool_calls)
        raise ModelStreamError(
            "upstream_incomplete",
            "The model stream ended before a terminal event was received.",
            stream_diagnostics,
        )
    invalid_calls = [
        item
        for item in _tool_stream_diagnostics(tool_calls)
        if item["arguments_validation"] != "valid_object"
    ]
    if invalid_calls:
        stream_diagnostics["termination_reason"] = (
            "output_limit_with_invalid_tool_arguments"
            if normalized_finish == "length"
            else "invalid_tool_arguments"
        )
        _update_tool_stream_diagnostics(stream_diagnostics, tool_calls)
        raise ModelStreamError(
            "invalid_tool_arguments",
            "The model stream ended with invalid tool arguments.",
            stream_diagnostics,
        )
    stream_diagnostics["stream_completed"] = True
    stream_diagnostics["termination_reason"] = (
        "provider_finish_reason"
        if normalized_finish
        else "provider_terminal_event"
        if stream_diagnostics.get("terminal_event_seen")
        else "done_marker"
    )
    if protocol_trace is not None:
        try:
            await protocol_trace({
                "type": "response_end",
                "status": "completed",
                "diagnostics": dict(stream_diagnostics),
            })
        except Exception:
            pass

    if not started and callback:
        await callback({"type": "reply_start"})
    if reasoning_started and callback:
        await callback({"type": "reasoning_done", "response": "".join(reasoning_parts)})
    if callback:
        await callback({"type": "reply_done", "response": "".join(text_parts)})
    return _completed_stream_message(
        text_parts, reasoning_parts, tool_calls, usage, finish,
        response_id, returned_model, stream_diagnostics,
    )


__all__ = [
    "NATIVE_PROTOCOL_ADAPTERS",
    "OPENAI_CHAT_ADAPTERS",
    "ModelStreamError",
    "PreparedRequest",
    "discovery_request",
    "handle_stream",
    "next_discovery_page",
    "parse_discovery_response",
    "parse_response",
    "prepare_request",
    "protocol_endpoints",
    "runtime_adapter_for_provider",
]
