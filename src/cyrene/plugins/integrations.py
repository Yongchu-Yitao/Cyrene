"""Small adapters from generic contributions to Cyrene-owned call sites."""

from __future__ import annotations

from typing import Any

from cyrene.plugins.manager import PluginError, get_plugin_manager


def _method_name(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("$method"), str):
        return value["$method"]
    return str(value or "").strip() if isinstance(value, str) else ""


def _string_list(value: Any, fallback: list[str] | None = None) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = list(fallback or [])
    return [str(item).strip() for item in items if str(item).strip()]


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


async def chat_model_candidates(project_id: str) -> list[dict[str, Any]]:
    """Materialize enabled ``cyrene.chatProvider`` contributions as candidates."""
    project_id = str(project_id or "").strip()
    if not project_id:
        return []
    manager = get_plugin_manager()
    providers = await manager.contributions(project_id, "cyrene.chatProvider")
    result: list[dict[str, Any]] = []
    for provider in providers:
        plugin_id = str(provider.get("pluginId") or "")
        provider_id = str(provider.get("id") or "")
        complete_method = _method_name(provider.get("complete"))
        if not plugin_id or not provider_id or not complete_method:
            continue
        models = provider.get("models")
        list_method = _method_name(provider.get("listModels") or provider.get("list_models"))
        if list_method:
            try:
                models = await manager.call(
                    plugin_id, project_id, list_method, {"projectId": project_id}, 30.0
                )
            except PluginError:
                continue
        if not isinstance(models, list):
            continue
        for raw in models:
            if isinstance(raw, str):
                raw = {"id": raw, "name": raw}
            if not isinstance(raw, dict):
                continue
            model_id = str(raw.get("id") or raw.get("model") or raw.get("name") or "").strip()
            if not model_id:
                continue
            capabilities = [
                item.lower()
                for item in _string_list(
                    raw.get("capabilities") or provider.get("capabilities"),
                    ["chat"],
                )
            ]
            context_limit = _nonnegative_int(
                raw.get("contextLimit") or raw.get("context_limit")
            )
            candidate_id = f"plugin:{plugin_id}:{provider_id}:{model_id}"
            result.append({
                "id": candidate_id,
                "profile_id": candidate_id,
                "connection_id": f"plugin:{plugin_id}:{provider_id}",
                "name": str(raw.get("name") or model_id),
                "model": model_id,
                "provider": "cyrene_plugin",
                "adapter": "cyrene_plugin",
                "plugin_id": plugin_id,
                "plugin_provider_id": provider_id,
                "plugin_method": complete_method,
                "project_id": project_id,
                "base_url": f"plugin://{plugin_id}/{provider_id}",
                "api_key": "",
                "endpoints": [f"plugin://{plugin_id}/{provider_id}/{model_id}"],
                "capabilities": capabilities,
                "vision_capable": "vision" in capabilities,
                "reasoning_effort": str(raw.get("reasoningEffort") or raw.get("reasoning_effort") or ""),
                "supported_reasoning_efforts": _string_list(raw.get("supportedReasoningEfforts") or raw.get("supported_reasoning_efforts")),
                "default_reasoning_effort": str(raw.get("defaultReasoningEffort") or raw.get("default_reasoning_effort") or ""),
                "desc": str(raw.get("description") or raw.get("desc") or provider.get("description") or ""),
                "ctx": str(raw.get("ctx") or raw.get("context") or ""),
                "ctx_limit": context_limit,
                "context_limit": context_limit,
                "price": str(raw.get("price") or ""),
            })
    return result


async def complete_chat_candidate(
    candidate: dict[str, Any],
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int | None,
    stream: bool,
    thinking: str,
    response_format: dict[str, Any] | None,
    caller: str,
    phase: str,
    session_id: str,
    timeout: float,
) -> dict[str, Any]:
    manager = get_plugin_manager()
    result = await manager.call(
        str(candidate.get("plugin_id") or ""),
        str(candidate.get("project_id") or ""),
        str(candidate.get("plugin_method") or ""),
        {
            "providerId": str(candidate.get("plugin_provider_id") or ""),
            "model": str(candidate.get("model") or ""),
            "messages": messages,
            "tools": tools or [],
            "maxTokens": max_tokens,
            "stream": bool(stream),
            "thinking": thinking,
            "responseFormat": response_format,
            "reasoningEffort": str(candidate.get("reasoning_effort") or ""),
            "caller": caller,
            "phase": phase,
            "sessionId": session_id,
        },
        timeout,
    )
    if isinstance(result, str):
        return {"role": "assistant", "content": result}
    if not isinstance(result, dict):
        raise PluginError("chat provider returned an invalid response")
    message = result.get("message") if isinstance(result.get("message"), dict) else result
    normalized = dict(message)
    normalized.setdefault("role", "assistant")
    normalized.setdefault("content", "")
    if "usage" not in normalized and isinstance(result.get("usage"), dict):
        normalized["usage"] = result["usage"]
    if isinstance(result.get("events"), list):
        normalized["_plugin_stream_events"] = result["events"]
    return normalized


__all__ = ["chat_model_candidates", "complete_chat_candidate"]
