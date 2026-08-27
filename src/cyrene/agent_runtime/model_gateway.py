"""Loopback-only, short-lived model gateway for external Agents.

The gateway exposes the small OpenAI-compatible surface commonly accepted by
ACP Agents while keeping Cyrene's real provider credentials in-process.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cyrene.config import CACHE_DIR, WEB_PORT
from cyrene.localization import localized

logger = logging.getLogger(__name__)

# ACP sessions outlive an individual prompt and may wait on a human for a long
# time. Tokens live only in this backend process, are loopback-only, and are
# cleared on shutdown; this is an idle safety bound rather than a turn timeout.
_TOKEN_TTL_SECONDS = 12 * 60 * 60
_TOKENS: dict[str, dict[str, Any]] = {}
_CURRENT_SESSIONS: dict[tuple[str, str], str] = {}
_TOKEN_LOCK = threading.RLock()
_GATEWAY_PORT = int(WEB_PORT)


def configure_model_gateway(port: int) -> None:
    global _GATEWAY_PORT
    _GATEWAY_PORT = int(port)


_PI_AGENT_CONFIG_ROOT = CACHE_DIR / "pi-agent-config"


def _pi_agent_config_dir(model_id: str) -> Path:
    """Per-model override directory: same model shares, different models never collide."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(model_id or "cyrene-managed"))[:80] or "cyrene-managed"
    return _PI_AGENT_CONFIG_ROOT / safe


def _atomic_write_json(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _ensure_pi_agent_config(gateway_url: str, model_id: str, model_name: str) -> str | None:
    """Idempotently write Pi's config pointing its default model at Cyrene.

    Pi picks its model from settings (defaultProvider/defaultModel) before
    falling back to the first provider whose API key env is present, so the
    override directory must both define the Cyrene model in ``models.json``
    and pin it in ``settings.json``.  The model id/name is the selected Cyrene
    model candidate (e.g. deepseek-v4-flash), never a Pi built-in like gpt-5.4.

    The override directory is keyed by the model id so concurrent chats using
    different candidates never rewrite each other's config, and both files are
    replaced atomically so a reader never observes a torn pair.

    Returns the agent config directory to pass as ``PI_CODING_AGENT_DIR``, or
    None when the cache directory cannot be written (the binding then simply
    omits the override and Pi falls back to its own configuration).
    """
    config_dir = _pi_agent_config_dir(model_id)
    models_path = config_dir / "models.json"
    settings_path = config_dir / "settings.json"
    models_content = json.dumps(
        {
            "providers": {
                "openai": {
                    "baseUrl": gateway_url,
                    "models": [
                        {
                            "id": model_id,
                            "name": model_name,
                            "api": "openai-responses",
                        }
                    ],
                }
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    settings_content = json.dumps(
        {"defaultProvider": "openai", "defaultModel": model_id},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        try:
            current_models = models_path.read_text("utf-8") if models_path.is_file() else ""
            current_settings = settings_path.read_text("utf-8") if settings_path.is_file() else ""
        except (OSError, UnicodeDecodeError):
            # Unreadable/corrupt leftovers (e.g. from a crashed earlier write)
            # are treated as a mismatch and atomically rewritten.
            current_models = current_settings = ""
        if current_models == models_content and current_settings == settings_content:
            return str(config_dir)
        config_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(models_path, models_content)
        _atomic_write_json(settings_path, settings_content)
    except OSError:
        return None
    return str(config_dir)


def issue_model_gateway_binding(_model_access: Any, context: dict[str, Any]) -> dict[str, str]:
    """Return an allowlisted environment containing a chat-scoped token.

    OpenCode persists the provider attached to an ACP session and can return to
    it after tools or permission prompts. Reissuing a different credential for
    every Cyrene turn therefore makes a loaded OpenCode session alternate
    between valid and revoked credentials. Keep one credential for the same
    chat, installation and exact model identity; it remains loopback-only and
    expires after an idle window.
    """
    from cyrene.agent_runtime.errors import AgentRuntimeError
    from agent.plugin.model_catalog import (
        resolve_model_profile_candidate,
        resolve_session_model_candidate,
    )

    profile_id = str(getattr(_model_access, "profile_id", "") or "primary").strip()
    chat_id = str(context.get("chat_id") or "")
    candidate = (
        resolve_session_model_candidate(chat_id)
        if profile_id in {"", "primary"}
        else resolve_model_profile_candidate(profile_id)
    )
    if candidate is None:
        raise AgentRuntimeError(
            "model_gateway_unavailable",
            (
                localized(
                    "No Cyrene model is configured for this Agent",
                    "尚未为此智能体配置 Cyrene 模型",
                )
                if profile_id in {"", "primary"}
                else localized(
                    f"Cyrene model profile {profile_id!r} is unavailable",
                    f"Cyrene 模型配置 {profile_id!r} 不可用",
                )
            ),
        )
    installation_id = str(context.get("installation_id") or "")
    model_identity = {
        "candidateId": str(candidate.get("id") or ""),
        "profileId": str(candidate.get("profile_id") or candidate.get("id") or ""),
        "adapter": str(candidate.get("adapter") or candidate.get("provider") or ""),
        "provider": str(candidate.get("provider") or "openai_compatible"),
        "model": str(candidate.get("model") or ""),
        "baseUrl": str(candidate.get("base_url") or ""),
        "reasoningEffort": str(candidate.get("reasoning_effort") or ""),
    }
    now = time.monotonic()
    token = ""
    with _TOKEN_LOCK:
        expired = [key for key, row in _TOKENS.items() if float(row.get("expires", 0)) <= now]
        for key in expired:
            _TOKENS.pop(key, None)
        for existing_token, record in list(_TOKENS.items()):
            same_owner = (
                str(record.get("chatId") or "") == chat_id
                and str(record.get("installationId") or "") == installation_id
            )
            if not same_owner:
                continue
            if record.get("modelIdentity") == model_identity:
                token = existing_token
                record["expires"] = now + _TOKEN_TTL_SECONDS
                record["runId"] = str(context.get("run_id") or "")
            else:
                # A model change must not leave the previous model capability
                # usable by an already-running external process.
                _TOKENS.pop(existing_token, None)
                _CURRENT_SESSIONS.pop((chat_id, installation_id), None)
        if not token:
            token = secrets.token_urlsafe(32)
            _TOKENS[token] = {
                "expires": now + _TOKEN_TTL_SECONDS,
                "chatId": chat_id,
                "runId": str(context.get("run_id") or ""),
                "installationId": installation_id,
                "modelIdentity": model_identity,
            }
    gateway_url = f"http://127.0.0.1:{_GATEWAY_PORT}/api/agent-model-gateway/v1"
    result = {
        "OPENAI_API_KEY": token,
        "OPENAI_BASE_URL": gateway_url,
    }
    # OpenCode does not treat generic OPENAI_* variables as a command-line
    # model override. Give its ACP process an ephemeral, highest-precedence
    # custom provider so the selected model is guaranteed to call Cyrene's
    # scoped gateway instead of a previously logged-in provider. No durable
    # provider credential or user config file is modified.
    if str(context.get("agent_id") or "") == "opencode":
        model_id = str(candidate.get("model") or "cyrene-managed").strip() or "cyrene-managed"
        result["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "model": f"cyrene-gateway/{model_id}",
            "provider": {
                "cyrene-gateway": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Cyrene Model Gateway",
                    "options": {
                        "baseURL": "{env:OPENAI_BASE_URL}",
                        "apiKey": "{env:OPENAI_API_KEY}",
                    },
                    "models": {
                        model_id: {
                            "name": str(candidate.get("model") or model_id),
                        },
                    },
                },
            },
        }, ensure_ascii=False, separators=(",", ":"))
    # Pi resolves its model the same way OpenCode does not: it picks the first
    # provider whose API key env is present (OPENAI_API_KEY) and sends requests
    # to that provider's baseUrl. Pi ignores generic OPENAI_BASE_URL, so point
    # its openai provider at Cyrene's gateway through an ephemeral config dir
    # (PI_CODING_AGENT_DIR redirects the whole ~/.pi/agent layout, keeping the
    # user's own auth/sessions untouched and isolated per Cyrene install).
    if str(context.get("agent_id") or "") == "pi-acp":
        # Use the model name the user configured in Cyrene (e.g. deepseek-v4-flash),
        # not the entry id (deepseek-chat) or a Pi built-in.
        pi_model_id = str(candidate.get("name") or candidate.get("model") or "cyrene-managed").strip() or "cyrene-managed"
        pi_config_dir = _ensure_pi_agent_config(gateway_url, pi_model_id, pi_model_id)
        if pi_config_dir:
            result["PI_CODING_AGENT_DIR"] = pi_config_dir
        else:
            # Without the redirect dir Pi ignores OPENAI_BASE_URL and would send
            # the gateway token to its own provider's endpoint; drop the token
            # so it falls back to the user's own ~/.pi configuration cleanly.
            result.pop("OPENAI_API_KEY", None)
            result.pop("OPENAI_BASE_URL", None)
    return result


def authorize_model_gateway(authorization: str) -> dict[str, Any] | None:
    auth = str(authorization or "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    now = time.monotonic()
    with _TOKEN_LOCK:
        record = _TOKENS.get(token)
        if record is None or float(record.get("expires", 0)) <= now:
            if token:
                _TOKENS.pop(token, None)
            return None
        # A token follows one chat/Agent/model binding and slides while that
        # ACP session is actively making model calls.
        record["expires"] = now + _TOKEN_TTL_SECONDS
        return dict(record)


def revoke_model_gateway_scope(*, chat_id: str = "", run_id: str = "") -> None:
    """Explicitly revoke matching bindings (chat removal/model migration)."""
    chat = str(chat_id or "")
    run = str(run_id or "")
    with _TOKEN_LOCK:
        for token, record in list(_TOKENS.items()):
            if run and str(record.get("runId") or "") == run:
                _TOKENS.pop(token, None)
            elif chat and not run and str(record.get("chatId") or "") == chat:
                _TOKENS.pop(token, None)
        if chat and not run:
            for owner in list(_CURRENT_SESSIONS):
                if owner[0] == chat:
                    _CURRENT_SESSIONS.pop(owner, None)


def touch_model_gateway_scope(*, chat_id: str, installation_id: str = "") -> None:
    """Keep a live session valid when its human permission wait completes."""
    chat = str(chat_id or "")
    installation = str(installation_id or "")
    now = time.monotonic()
    with _TOKEN_LOCK:
        for record in _TOKENS.values():
            if str(record.get("chatId") or "") != chat:
                continue
            if installation and str(record.get("installationId") or "") != installation:
                continue
            record["expires"] = now + _TOKEN_TTL_SECONDS


def mark_model_gateway_session_current(
    *,
    chat_id: str,
    installation_id: str,
    session_id: str,
) -> None:
    """Remember that an external session was created with this process token."""
    chat = str(chat_id or "")
    installation = str(installation_id or "")
    session = str(session_id or "")
    if not chat or not installation or not session:
        return
    with _TOKEN_LOCK:
        _CURRENT_SESSIONS[(chat, installation)] = session


def is_model_gateway_session_current(
    *,
    chat_id: str,
    installation_id: str,
    session_id: str,
) -> bool:
    """Whether a persisted Agent session is safe for this backend lifetime."""
    with _TOKEN_LOCK:
        return _CURRENT_SESSIONS.get(
            (str(chat_id or ""), str(installation_id or ""))
        ) == str(session_id or "")


def revoke_all_model_gateway_scopes() -> None:
    """Revoke every external-Agent model capability during app shutdown."""
    with _TOKEN_LOCK:
        _TOKENS.clear()
        _CURRENT_SESSIONS.clear()


def _openai_response(message: dict[str, Any], requested_model: str = "") -> dict[str, Any]:
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    return {
        "id": f"chatcmpl_{secrets.token_hex(8)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(message.get("model") or requested_model or "cyrene-managed"),
        "choices": [{
            "index": 0,
            "message": {
                key: value
                for key, value in message.items()
                if key in {"role", "content", "tool_calls", "reasoning_content"}
            },
            "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
        }],
        "usage": usage,
    }


async def call_model_gateway(body: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any] | str:
    logger.info("gateway call_model_gateway entered [messages=%s]", len(body.get("messages") or []))
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages must be an array")
    tools = body.get("tools") if isinstance(body.get("tools"), list) else None
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
    try:
        max_tokens = int(max_tokens) if max_tokens is not None else None
    except (TypeError, ValueError):
        max_tokens = None
    from cyrene.agent_runtime.errors import AgentRuntimeError
    from agent.plugin.model_catalog import resolve_exact_model_candidate

    identity = scope.get("modelIdentity") if isinstance(scope.get("modelIdentity"), dict) else {}
    candidate = resolve_exact_model_candidate(identity)
    if candidate is None:
        raise AgentRuntimeError(
            "model_binding_unsupported",
            localized(
                "The Cyrene model selected for this Agent is no longer available",
                "为此智能体选择的 Cyrene 模型已不可用",
            ),
        )
    from agent.plugin import active_plugin_service

    gateway = active_plugin_service("model")
    if gateway is None:
        raise AgentRuntimeError(
            "model_gateway_unavailable",
            localized(
                "Model Provider Plugins are not available",
                "模型提供商插件不可用",
            ),
        )
    logger.info(
        "gateway call_model_gateway calling Provider Plugin "
        "[candidate=%s model=%s]",
        candidate.get("id"),
        candidate.get("model"),
    )
    result = await gateway.complete(
        messages,
        tools=tools,
        tool_choice=body.get("tool_choice"),
        max_tokens=max_tokens,
        caller="external_agent_model_gateway",
        session_id=str(scope.get("chatId") or ""),
        model_identity=identity,
    )
    logger.info(
        "gateway call_model_gateway Provider Plugin returned [kind=%s]",
        type(result).__name__,
    )
    message = dict(result)
    message.setdefault("role", "assistant")
    normalized_calls: list[dict[str, Any]] = []
    for raw in message.get("tool_calls") or ():
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        source = function if isinstance(function, dict) else raw
        arguments = source.get("arguments", {})
        normalized_calls.append(
            {
                "id": str(raw.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(source.get("name") or ""),
                    "arguments": (
                        arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments, ensure_ascii=False)
                    ),
                },
            }
        )
    if normalized_calls:
        message["tool_calls"] = normalized_calls
    return message


__all__ = [
    "authorize_model_gateway",
    "call_model_gateway",
    "configure_model_gateway",
    "issue_model_gateway_binding",
    "is_model_gateway_session_current",
    "mark_model_gateway_session_current",
    "revoke_model_gateway_scope",
    "touch_model_gateway_scope",
    "revoke_all_model_gateway_scopes",
    "_openai_response",
]
