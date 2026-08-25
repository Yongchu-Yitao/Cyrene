"""Unified LLM calling — candidates, streaming, tools, thinking, token recording.

Replaces the independent implementations previously scattered across agent.py,
search.py, scheduler.py, attachments.py, and onboarding.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time as _time
import uuid
import weakref
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Awaitable, Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx

from cyrene.model_runtime.cache_invalidation import register_model_cache_invalidator
from cyrene.model_runtime.constants import NETWORK_RETRY_LIMIT
from cyrene.model_runtime.errors import (
    format_httpx_error as _format_httpx_error,
    httpx_error_body_for_persistence,
)
from cyrene.model_runtime.messages import (
    canonical_tool_arguments,
    parse_tool_arguments,
)
from cyrene.model_runtime.transcript_policy import (
    ProviderFamily,
    ProviderFamilyError,
    TranscriptLane,
    prompt_cache_key_for_lane,
    require_single_provider_family,
)
from cyrene.model_runtime.status import persist_model_status
from cyrene.config import (
    DB_PATH,
    DEFAULT_OPENAI_BASE_URL,
    strip_wrapping_quotes,
)
from cyrene.observability.context_trace import strip_context_metadata, summarize_context_trace
from cyrene.runtime.config_store import effective_ctx_limit_for_model
from cyrene.runtime.model_configuration import candidates_for_route
from cyrene.runtime.settings_store import (
    get as get_setting,
    set_ as set_setting,
)
from cyrene.runtime.task_lifecycle import drain_or_cancel, track_task

logger = logging.getLogger(__name__)
_strip_wrapping_quotes = strip_wrapping_quotes

# ---------------------------------------------------------------------------
# Background task tracking — prevent GC from collecting fire-and-forget tasks
# ---------------------------------------------------------------------------
_pending_token_tasks: set[asyncio.Task] = set()

# One keep-alive pool per event loop and timeout profile. This keeps production
# calls on persistent HTTP/1.1 or HTTP/2 connections while avoiding cross-loop
# reuse in tests, desktop restarts, and embedded runtimes.
_http_clients: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    dict[float | tuple[float, str], tuple[Any, httpx.AsyncClient]],
] = weakref.WeakKeyDictionary()
_HTTP_MAX_CONNECTIONS = 40
_HTTP_MAX_KEEPALIVE_CONNECTIONS = 20
_MINIMAX_STREAM_IDLE_TIMEOUT_SECONDS = 240.0
_LAST_SUCCESS_SETTING = "llm_last_success_endpoints"
_SESSION_MODEL_PREFERENCE_SETTING = "llm_session_model_preferences"
_SESSION_AFFINITY_PREFIX = "session:"
_MAX_SESSION_AFFINITIES = 2048
_last_success_cache: dict[str, dict[str, str]] | None = None
_session_model_preference_cache: dict[str, dict[str, str]] | None = None


def _bg_token_task(task: asyncio.Task) -> None:
    track_task(
        task,
        _pending_token_tasks,
        logger=logger,
        label="token usage persistence",
    )


async def shutdown_background_tasks() -> None:
    """Flush short usage writes and cancel any stalled database operation."""
    clients = [
        client
        for per_loop in list(_http_clients.values())
        for _factory, client in per_loop.values()
    ]
    _http_clients.clear()
    if clients:
        await asyncio.gather(
            *(client.aclose() for client in clients if hasattr(client, "aclose")),
            return_exceptions=True,
        )
    await drain_or_cancel(_pending_token_tasks, grace_seconds=2.0)
    _pending_token_tasks.clear()
    from cyrene.model_runtime.codex_provider import get_codex_provider
    await get_codex_provider().close()


async def reset_runtime_state() -> None:
    """Drop process-local model state after settings are factory-reset.

    A data reset used to replace the persisted model configuration while the
    HTTP pools, endpoint affinity, per-session preferences, and failure
    cooldowns from the old configuration remained alive.  The first request
    after configuring a new model could therefore use stale state until the
    process was restarted.  Keep this boundary explicit so a reset behaves
    like a fresh process without requiring one.
    """
    await shutdown_background_tasks()
    invalidate_model_configuration()


def invalidate_model_configuration() -> None:
    """Force the next request to resolve the just-persisted model settings."""
    global _last_success_cache, _session_model_preference_cache

    _last_success_cache = None
    _session_model_preference_cache = None
    _candidate_cooldowns.clear()
    _published_fallback_notices.clear()


def _get_http_client(
    timeout: float,
    proxy_url: str = "",
) -> tuple[httpx.AsyncClient, str, bool]:
    loop = asyncio.get_running_loop()
    timeout_key = float(timeout)
    normalized_proxy = str(proxy_url or "").strip()
    cache_key: float | tuple[float, str] = (
        (timeout_key, normalized_proxy) if normalized_proxy else timeout_key
    )
    per_loop = _http_clients.setdefault(loop, {})
    factory = httpx.AsyncClient
    existing = per_loop.get(cache_key)
    if existing is not None and existing[0] is factory:
        pool_key = f"loop:{id(loop)}:timeout:{timeout_key:g}"
        if normalized_proxy:
            pool_key += ":proxy:configured"
        return existing[1], pool_key, True
    transport = httpx.AsyncHTTPTransport(
        retries=0,
        proxy=normalized_proxy or None,
        limits=httpx.Limits(
            max_connections=_HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=_HTTP_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=30.0,
        ),
    )
    client_timeout = httpx.Timeout(
        timeout, connect=min(_CONNECT_TIMEOUT_SECONDS, timeout)
    )
    client = factory(transport=transport, timeout=client_timeout, http2=False)
    per_loop[cache_key] = (factory, client)
    pool_key = f"loop:{id(loop)}:timeout:{timeout_key:g}"
    if normalized_proxy:
        pool_key += ":proxy:configured"
    return client, pool_key, False


def _last_success_map() -> dict[str, dict[str, str]]:
    global _last_success_cache
    if _last_success_cache is None:
        raw = get_setting(_LAST_SUCCESS_SETTING, {})
        _last_success_cache = dict(raw) if isinstance(raw, dict) else {}
    return _last_success_cache


def _session_affinity_key(model_type: str, session_id: str) -> str:
    """Persistent affinity key scoped to one conversation and model role."""
    session = str(session_id or "").strip()
    if not session:
        return ""
    return f"{_SESSION_AFFINITY_PREFIX}{session}:{str(model_type or 'primary')}"


def _session_model_preferences() -> dict[str, dict[str, str]]:
    global _session_model_preference_cache
    if _session_model_preference_cache is None:
        raw = get_setting(_SESSION_MODEL_PREFERENCE_SETTING, {})
        _session_model_preference_cache = dict(raw) if isinstance(raw, dict) else {}
    return _session_model_preference_cache


def set_session_model_preference(
    session_id: str,
    candidate: dict[str, Any],
    reasoning_effort: str = "",
) -> None:
    """Pin a configured primary candidate (and optional effort) to one chat."""
    global _session_model_preference_cache

    session = str(session_id or "").strip()
    if not session:
        return
    preference = {
        "candidate_id": str(candidate.get("id") or "").strip(),
        "adapter": str(candidate.get("adapter") or candidate.get("provider") or "").strip(),
        "model": str(candidate.get("model") or candidate.get("name") or "").strip(),
        "base_url": str(candidate.get("base_url") or "").strip(),
        "reasoning_effort": str(
            reasoning_effort or candidate.get("reasoning_effort") or ""
        ).strip().lower(),
    }
    if str(candidate.get("provider") or "") == "cyrene_plugin":
        preference.update({
            "provider": "cyrene_plugin",
            "plugin_id": str(candidate.get("plugin_id") or ""),
            "plugin_provider_id": str(candidate.get("plugin_provider_id") or ""),
            "plugin_method": str(candidate.get("plugin_method") or ""),
            "project_id": str(candidate.get("project_id") or ""),
            "name": str(candidate.get("name") or candidate.get("model") or ""),
            "capabilities": list(candidate.get("capabilities") or ["chat"]),
            "context_limit": int(candidate.get("context_limit") or candidate.get("ctx_limit") or 0),
        })
    saved = _session_model_preferences()
    if saved.get(session) == preference:
        return
    saved[session] = preference
    while len(saved) > _MAX_SESSION_AFFINITIES:
        saved.pop(next(iter(saved)))
    _session_model_preference_cache = saved
    set_setting(_SESSION_MODEL_PREFERENCE_SETTING, dict(saved))


def _plugin_preference_candidate(preference: dict[str, Any]) -> dict[str, Any]:
    return {
            "id": str(preference.get("candidate_id") or ""),
            "profile_id": str(preference.get("candidate_id") or ""),
            "connection_id": "plugin:" + str(preference.get("plugin_id") or ""),
            "model": str(preference.get("model") or ""),
            "name": str(preference.get("name") or preference.get("model") or ""),
            "provider": "cyrene_plugin",
            "adapter": "cyrene_plugin",
            "plugin_id": str(preference.get("plugin_id") or ""),
            "plugin_provider_id": str(preference.get("plugin_provider_id") or ""),
            "plugin_method": str(preference.get("plugin_method") or ""),
            "project_id": str(preference.get("project_id") or ""),
            "base_url": str(preference.get("base_url") or "plugin://local"),
            "api_key": "",
            "capabilities": list(preference.get("capabilities") or ["chat"]),
            "context_limit": int(preference.get("context_limit") or 0),
            "ctx_limit": int(preference.get("context_limit") or 0),
            "reasoning_effort": str(preference.get("reasoning_effort") or ""),
            "endpoints": ["plugin://" + str(preference.get("plugin_id") or "")],
    }


def _inject_plugin_preference(
    candidates: list[dict[str, Any]],
    model_type: str,
    preference: dict[str, Any],
) -> list[dict[str, Any]]:
    if not (
        model_type == "primary"
        and preference.get("provider") == "cyrene_plugin"
        and preference.get("plugin_id")
        and preference.get("plugin_method")
    ):
        return candidates
    plugin_candidate = _plugin_preference_candidate(preference)
    return [plugin_candidate] + [
        item for item in candidates
        if str(item.get("id") or "") != plugin_candidate["id"]
    ]


def _candidate_matches_saved(
    candidate: dict[str, Any], saved: dict[str, Any]
) -> bool:
    return (
        str(candidate.get("id") or "") == str(saved.get("candidate_id") or "")
        and (
            not str(saved.get("adapter") or "")
            or str(candidate.get("adapter") or candidate.get("provider") or "")
            == str(saved.get("adapter") or "")
        )
        and str(candidate.get("model") or "") == str(saved.get("model") or "")
        and _base_root(candidate.get("base_url") or "")
        == _base_root(saved.get("base_url") or "")
    )


def _prepare_prioritized_candidate(
    original: dict[str, Any],
    configured_rank: int,
    affinity: dict[str, Any],
    preference: dict[str, Any],
) -> dict[str, Any]:
    candidate = dict(original)
    candidate["_configured_rank"] = configured_rank
    endpoints = list(candidate.get("endpoints") or [])
    candidate["_endpoint_ranks"] = {
        endpoint: rank for rank, endpoint in enumerate(endpoints)
    }
    if _candidate_matches_saved(candidate, affinity):
        preferred_endpoint = str(affinity.get("endpoint") or "")
        if (
            preferred_endpoint in endpoints
            and not (
                _is_official_deepseek_base_url(candidate.get("base_url") or "")
                and preferred_endpoint == "https://api.deepseek.com/chat/completions"
            )
        ):
            endpoints.remove(preferred_endpoint)
            endpoints.insert(0, preferred_endpoint)
    if _candidate_matches_saved(candidate, preference):
        requested_effort = str(preference.get("reasoning_effort") or "").strip()
        if requested_effort:
            candidate["reasoning_effort"] = requested_effort
    candidate["endpoints"] = endpoints
    return candidate


def _prioritize_last_success(
    candidates: list[dict[str, Any]], model_type: str, session_id: str = ""
) -> list[dict[str, Any]]:
    affinity_key = _session_affinity_key(model_type, session_id)
    affinity = _last_success_map().get(affinity_key) or {} if affinity_key else {}
    preference = (
        _session_model_preferences().get(str(session_id or "").strip()) or {}
        if model_type == "primary" and session_id
        else {}
    )
    candidates = _inject_plugin_preference(candidates, model_type, preference)
    prepared = [
        _prepare_prioritized_candidate(original, rank, affinity, preference)
        for rank, original in enumerate(candidates)
    ]
    # A remembered success may optimize the endpoint order *inside* the same
    # profile, but it must never promote a fallback model ahead of the primary
    # route configured in Settings.  Only an explicit per-conversation model
    # selection is allowed to override the global route order.
    prepared.sort(
        key=lambda candidate: 0 if _candidate_matches_saved(candidate, preference) else 1
    )
    return prepared


def _remember_success(
    model_type: str,
    candidate: dict[str, Any],
    endpoint: str,
    session_id: str = "",
) -> None:
    """Remember a successful candidate only for the conversation that used it.

    Calls without a session id deliberately have no affinity: they always start
    from the configured primary order on their next invocation.
    """
    global _last_success_cache

    affinity_key = _session_affinity_key(model_type, session_id)
    if not affinity_key:
        return
    affinity = {
        "candidate_id": str(candidate.get("id") or ""),
        "adapter": str(candidate.get("adapter") or candidate.get("provider") or ""),
        "model": str(candidate.get("model") or ""),
        "base_url": str(candidate.get("base_url") or ""),
        "endpoint": str(endpoint or ""),
    }
    saved = _last_success_map()
    if saved.get(affinity_key) == affinity:
        return
    # Retire the old process-wide keys as soon as scoped state is written. They
    # must never influence a new conversation after this migration.
    saved.pop("primary", None)
    saved.pop("secondary", None)
    saved.pop("vision", None)
    saved[affinity_key] = affinity
    scoped_keys = [
        key for key in saved
        if str(key).startswith(_SESSION_AFFINITY_PREFIX)
    ]
    while len(scoped_keys) > _MAX_SESSION_AFFINITIES:
        oldest = scoped_keys.pop(0)
        saved.pop(oldest, None)
    _last_success_cache = saved
    set_setting(_LAST_SUCCESS_SETTING, dict(saved))


# ---------------------------------------------------------------------------
# Secondary model concurrency guard
# ---------------------------------------------------------------------------
_secondary_in_flight: int = 0

# ---------------------------------------------------------------------------
# Candidate failure cooldown — a dead endpoint must not slow down every call
# ---------------------------------------------------------------------------
# 连不上的候选（如下线的本地模型机器）会让每次调用都先撞一遍超时。失败后把该候选
# 冷却一段时间，期间在同一对话内直接跳过；新对话使用不同的 session key，
# 因此仍会从设置中的 primary 重新尝试。无 session 的后台调用共享一个空作用域。
_CANDIDATE_COOLDOWN_SECONDS = 120.0
_candidate_cooldowns: dict[tuple[str, str, str, str], float] = {}

# A Workbench round can call the LLM several times (decision, tool rounds,
# wrap-up) while the configured primary remains in cooldown.  Remember the
# model transition already surfaced for that round so one outage produces one
# user-facing notice instead of one notice per internal LLM call.
_MAX_FALLBACK_NOTICE_KEYS = 4096
_published_fallback_notices: dict[tuple[str, str, str, str], None] = {}


def _invalidate_registered_model_cache() -> None:
    invalidate_model_configuration()


register_model_cache_invalidator(_invalidate_registered_model_cache)

# httpx 连接超时与读超时分开：对不可达主机快速失败，而不是吃满整个调用超时。
_CONNECT_TIMEOUT_SECONDS = 5.0

# A model request may be dropped before the provider sends response headers.
# Retry transport failures locally before surfacing them to the user. HTTP
# responses (including 4xx/5xx) are deliberately excluded from this budget.
_NETWORK_RETRY_BASE_DELAY_SECONDS = 10.0
# Bounded same-endpoint retry for transient upstream 5xx (incl. non-standard
# overload codes like 550 / 529) before rotating to the next endpoint/candidate.
# 4xx is a real client error and is never retried here.
SERVER_ERROR_RETRY_LIMIT = 5
_SERVER_ERROR_RETRY_BASE_DELAY_SECONDS = 10.0


def _llm_failure_priority(exc: Exception) -> int:
    """Rank exhausted-candidate failures by how actionable their root is."""
    if str(getattr(exc, "kind", "") or getattr(exc, "code", "") or "").strip():
        return 500
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            status = int(exc.response.status_code)
        except Exception:
            status = 0
        if status in (401, 403):
            return 450
        if 400 <= status < 500:
            return 400
        return 300
    if isinstance(exc, httpx.TransportError):
        return 100
    return 200


def _prefer_llm_failure(current: Exception | None, incoming: Exception) -> Exception:
    """Keep a precise provider response from being masked by a later disconnect."""
    if current is None or _llm_failure_priority(incoming) >= _llm_failure_priority(current):
        return incoming
    return current


def _candidate_key(
    candidate: dict[str, Any], session_id: str = ""
) -> tuple[str, str, str, str]:
    return (
        str(session_id or "").strip(),
        str(candidate.get("adapter") or candidate.get("provider") or "openai_compatible"),
        str(candidate.get("model") or ""),
        str(candidate.get("base_url") or ""),
    )


def _candidate_cooling(key: tuple[str, str, str, str]) -> bool:
    return _candidate_cooldowns.get(key, 0.0) > _time.monotonic()


def _set_candidate_cooldown(key: tuple[str, str, str, str]) -> None:
    _candidate_cooldowns[key] = _time.monotonic() + _CANDIDATE_COOLDOWN_SECONDS


def _clear_candidate_cooldown(key: tuple[str, str, str, str]) -> None:
    _candidate_cooldowns.pop(key, None)


def _claim_model_fallback_notice(
    *,
    session_id: str,
    round_id: str,
    failed_model: str,
    fallback_model: str,
) -> bool:
    """Claim one fallback notice per model transition in a runtime round."""
    return _claim_model_notice(
        session_id=session_id,
        round_id=round_id,
        failed_model=failed_model,
        notice_kind="fallback",
        target=fallback_model,
    )


def _claim_model_availability_notice(
    *,
    session_id: str,
    round_id: str,
    failed_model: str,
    failure_kind: str,
) -> bool:
    """Claim one actionable provider notice per failure kind and runtime round."""
    return _claim_model_notice(
        session_id=session_id,
        round_id=round_id,
        failed_model=failed_model,
        notice_kind="availability",
        target=failure_kind,
    )


def _claim_model_notice(
    *,
    session_id: str,
    round_id: str,
    failed_model: str,
    notice_kind: str,
    target: str,
) -> bool:
    session = str(session_id or "").strip()
    round_key = str(round_id or "").strip()
    if not session or not round_key:
        # Calls outside a round keep the historical per-call notification
        # behavior because there is no reliable lifecycle boundary to use.
        return True

    key = (
        session,
        round_key,
        str(failed_model or "").strip(),
        f"{str(notice_kind or '').strip()}:{str(target or '').strip()}",
    )
    if key in _published_fallback_notices:
        return False
    _published_fallback_notices[key] = None
    while len(_published_fallback_notices) > _MAX_FALLBACK_NOTICE_KEYS:
        _published_fallback_notices.pop(next(iter(_published_fallback_notices)))
    return True

# ---------------------------------------------------------------------------
# Helpers moved from agent.py / attachments.py
# ---------------------------------------------------------------------------


def _is_official_deepseek_base_url(base_url: str) -> bool:
    normalized_base = str(base_url or "").strip().rstrip("/")
    return normalized_base.lower() in {
        "https://api.deepseek.com",
        "https://api.deepseek.com:443",
        "https://api.deepseek.com/v1",
        "https://api.deepseek.com:443/v1",
    }


def _normalized_llm_endpoints(base_url: str) -> list[str]:
    normalized_base = str(base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/") or DEFAULT_OPENAI_BASE_URL
    from cyrene.model_runtime.protocol_adapters import official_versioned_chat_endpoint

    official_endpoint = official_versioned_chat_endpoint(normalized_base)
    if official_endpoint:
        # DeepSeek and MiniMax expose their OpenAI-compatible API below /v1.
        # Never rotate to an unversioned route: it is not a useful fallback and
        # can replace the actionable error returned by the configured endpoint.
        return [official_endpoint]
    endpoints = [f"{normalized_base}/chat/completions"]
    if not normalized_base.endswith("/v1"):
        endpoints.append(f"{normalized_base}/v1/chat/completions")
    return list(dict.fromkeys(endpoints))


def _normalized_candidate(raw: dict[str, Any], index: int = 0, *, active_model: str, active_base_url: str, active_api_key: str) -> dict[str, Any]:
    from cyrene.model_runtime.codex_provider import CODEX_BASE_URL, CODEX_PROVIDER
    from cyrene.model_runtime.protocol_adapters import runtime_adapter_for_provider

    model = str(raw.get("model") or raw.get("name") or raw.get("id") or "").strip()
    if not model:
        model = active_model
    options = (
        dict(raw.get("options") or {})
        if isinstance(raw.get("options"), dict)
        else {}
    )
    provider = str(raw.get("provider") or "openai_compatible").strip()
    adapter = str(raw.get("adapter") or provider).strip().lower()
    provider_preset = str(options.get("provider_preset") or "").strip().lower()
    adapter = runtime_adapter_for_provider(
        adapter,
        model,
        provider_preset=provider_preset,
    )
    if provider_preset == "opencode_go":
        provider = "opencode_go"
    base_url = (
        str(raw.get("base_url") or "plugin://local")
        if provider == "cyrene_plugin"
        else CODEX_BASE_URL
        if provider == CODEX_PROVIDER
        else str(raw.get("base_url") or active_base_url or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
    )
    raw_api_key = strip_wrapping_quotes(str(raw.get("api_key") or "").strip())
    if provider in {CODEX_PROVIDER, "cyrene_plugin"}:
        api_key = ""
    elif raw_api_key:
        api_key = raw_api_key
    elif base_url.rstrip("/") == (active_base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/"):
        api_key = active_api_key
    else:
        api_key = ""
    explicit_ctx_limit = raw.get("context_limit", raw.get("ctx_limit", 0))
    try:
        explicit_ctx_limit = int(explicit_ctx_limit or 0)
    except (TypeError, ValueError):
        explicit_ctx_limit = 0
    if explicit_ctx_limit <= 0:
        configured_ctx = str(raw.get("ctx") or "").strip().upper()
        multiplier = 1
        if configured_ctx.endswith("K"):
            configured_ctx, multiplier = configured_ctx[:-1], 1_000
        elif configured_ctx.endswith("M"):
            configured_ctx, multiplier = configured_ctx[:-1], 1_000_000
        try:
            explicit_ctx_limit = int(float(configured_ctx) * multiplier)
        except (TypeError, ValueError):
            explicit_ctx_limit = 0
    if provider == "cyrene_plugin":
        endpoints = list(raw.get("endpoints") or [base_url])
    elif provider == CODEX_PROVIDER:
        endpoints = [CODEX_BASE_URL]
    elif adapter in {"anthropic", "openai", "openai_responses", "gemini", "ollama"}:
        from cyrene.model_runtime.protocol_adapters import protocol_endpoints

        endpoints = protocol_endpoints(adapter, base_url, model)
    else:
        endpoints = _normalized_llm_endpoints(base_url)
    normalized = {
        "id": str(raw.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}",
        "profile_id": str(raw.get("profile_id") or raw.get("id") or f"candidate-{index + 1}").strip(),
        "connection_id": str(raw.get("connection_id") or "").strip(),
        "model": model,
        "name": str(raw.get("name") or model).strip() or model,
        "provider": provider,
        "reasoning_effort": str(raw.get("reasoning_effort") or "").strip().lower(),
        # Codex app-server accepts image and localImage turn inputs. Treat the
        # provider capability as authoritative so candidates saved by older
        # Cyrene versions (which persisted False) are upgraded in memory.
        "vision_capable": (
            True
            if provider == CODEX_PROVIDER
            else (
                raw.get("vision_capable")
                if isinstance(raw.get("vision_capable"), bool)
                else None
            )
        ),
        "base_url": base_url,
        "api_key": api_key,
        "adapter": adapter,
        "capabilities": list(raw.get("capabilities") or []),
        "use_proxy": raw.get("use_proxy") is True,
        # Provider-specific transport features are opt-in for generic
        # OpenAI-compatible endpoints.  Keep the options attached to the
        # runtime candidate so the wire layer can make that decision without
        # guessing from a model name.
        "options": options,
        "ctx": str(raw.get("ctx") or "").strip(),
        "ctx_limit": max(0, int(explicit_ctx_limit or 0)),
        "context_limit": max(0, int(explicit_ctx_limit or 0)),
        "endpoints": endpoints,
    }
    if provider == "cyrene_plugin":
        normalized.update({
            "plugin_id": str(raw.get("plugin_id") or ""),
            "plugin_provider_id": str(raw.get("plugin_provider_id") or ""),
            "plugin_method": str(raw.get("plugin_method") or ""),
            "project_id": str(raw.get("project_id") or ""),
        })
    return normalized


def primary_candidate_supports_vision(session_id: str = "") -> bool:
    """Whether the candidate that would start this conversation supports images."""
    candidates = _prioritize_last_success(
        _resolve_llm_candidates(),
        "primary",
        session_id,
    )
    candidate = (candidates or [{}])[0]
    return candidate.get("vision_capable") is True


def _base_root(url: str) -> str:
    """Normalize a base URL for equality checks ("…/v1" 与不带 /v1 视为同端点)."""
    normalized = str(url or "").strip().rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[: -len("/v1")].rstrip("/")
    return normalized.lower()


def _public_base_url(url: str) -> str:
    """Return an origin identity without userinfo, path, query, or fragments."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname + (f":{parsed.port}" if parsed.port else "")
        return urlunsplit((parsed.scheme, netloc, "", "", "")).rstrip("/")
    except (ValueError, TypeError):
        return ""


_OPENAI_SHAPED_CACHE_ADAPTERS = frozenset({
    "openai",
    "openai_compatible",
    "openai_responses",
    "ollama",
})
_AUTOMATIC_PREFIX_CACHE_PRESETS = frozenset({"deepseek", "minimax"})


def _candidate_provider_preset(candidate: dict[str, Any]) -> str:
    options = candidate.get("options")
    options = options if isinstance(options, dict) else {}
    return str(
        candidate.get("provider_preset")
        or options.get("provider_preset")
        or ""
    ).strip().lower()


def _is_known_automatic_prefix_cache_provider(
    candidate: dict[str, Any],
) -> bool:
    """Whether the provider caches matching prefixes without a request key."""
    if _candidate_provider_preset(candidate) in _AUTOMATIC_PREFIX_CACHE_PRESETS:
        return True
    try:
        host = (
            urlsplit(str(candidate.get("base_url") or "")).hostname or ""
        ).lower()
    except ValueError:
        host = ""
    return host in {
        "api.deepseek.com",
        "api.minimax.com",
        "api.minimax.io",
        "api.minimaxi.com",
    }


def _explicit_prompt_cache_key_support(
    candidate: dict[str, Any],
) -> bool | None:
    """Return an explicit transport declaration, if the candidate has one."""
    options = candidate.get("options")
    options = options if isinstance(options, dict) else {}
    for source in (candidate, options):
        if "prompt_cache_key_supported" in source:
            value = source.get("prompt_cache_key_supported")
            if isinstance(value, bool):
                return value
    capabilities = {
        str(value or "").strip().lower()
        for value in candidate.get("capabilities") or []
    }
    if "prompt_cache_key" in capabilities:
        return True
    return None


def _is_official_openai_endpoint(candidate: dict[str, Any]) -> bool:
    try:
        parsed = urlsplit(str(candidate.get("base_url") or ""))
        return (
            parsed.scheme.lower() == "https"
            and (parsed.hostname or "").lower() == "api.openai.com"
            and parsed.port in {None, 443}
        )
    except ValueError:
        return False


def _candidate_accepts_prompt_cache_key(candidate: dict[str, Any]) -> bool:
    """Use the OpenAI field only where support is known or explicit.

    DeepSeek and MiniMax intentionally never receive this field: their
    OpenAI-compatible APIs cache stable prefixes automatically.  Unknown
    compatibility endpoints default to omission so an optional optimization
    can never turn into a provider 4xx.
    """
    provider = str(candidate.get("provider") or "openai_compatible").lower()
    adapter = str(candidate.get("adapter") or provider).strip().lower()
    if provider in {"codex_oauth", "cyrene_plugin"}:
        return False
    if adapter not in _OPENAI_SHAPED_CACHE_ADAPTERS:
        return False
    if _is_known_automatic_prefix_cache_provider(candidate):
        return False
    declared = _explicit_prompt_cache_key_support(candidate)
    if declared is not None:
        return declared
    return _is_official_openai_endpoint(candidate)


def _stable_system_prompt_prefix(
    messages: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only the stable leading system/developer prompt prefix."""
    prefix: list[dict[str, Any]] = []
    for message in messages:
        if str(message.get("role") or "") not in {"system", "developer"}:
            break
        prefix.append(message)
    return prefix


def _provider_prompt_cache_route_key(
    candidate: dict[str, Any],
    *,
    model: str,
    cache_scope: str,
    message_units: Sequence[dict[str, Any]],
    tool_schema: Any,
    cache_epoch: str | int = "",
) -> str:
    """Return a stable lane route even for providers using automatic caching."""
    try:
        lane = TranscriptLane(str(cache_scope or "").strip())
    except ValueError:
        return ""
    provider = str(candidate.get("provider") or "openai_compatible").lower()
    adapter = str(candidate.get("adapter") or provider).strip().lower()
    if provider in {"codex_oauth", "cyrene_plugin"}:
        return ""
    if adapter not in _OPENAI_SHAPED_CACHE_ADAPTERS:
        return ""
    provider_profile = {
        "profile_id": str(candidate.get("profile_id") or candidate.get("id") or ""),
        "connection_id": str(candidate.get("connection_id") or ""),
        "provider": provider,
        "adapter": adapter,
        "base_url": _public_base_url(candidate.get("base_url") or ""),
    }
    return prompt_cache_key_for_lane(
        provider_profile=provider_profile,
        model=model,
        lane=lane,
        system_prompt=_stable_system_prompt_prefix(message_units),
        tool_schema=tool_schema or [],
        cache_epoch=cache_epoch,
    )


def _inherit_sibling_keys(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Share a key only between legacy rows from the same logical service.

    Graph-derived candidates carry ``connection_id`` and therefore never
    borrow credentials from a different service, even if two services happen
    to use the same URL.  The endpoint identity remains only as a compatibility
    scope for old, connection-less candidate payloads.
    """
    keyed_roots: dict[tuple[str, str], str] = {}
    for candidate in candidates:
        connection_id = str(candidate.get("connection_id") or "").strip()
        root = (
            "connection" if connection_id else str(candidate.get("adapter") or candidate.get("provider") or ""),
            connection_id or _base_root(candidate.get("base_url") or ""),
        )
        if candidate.get("api_key") and root not in keyed_roots:
            keyed_roots[root] = candidate["api_key"]
    for candidate in candidates:
        if not candidate.get("api_key"):
            connection_id = str(candidate.get("connection_id") or "").strip()
            root = (
                "connection" if connection_id else str(candidate.get("adapter") or candidate.get("provider") or ""),
                connection_id or _base_root(candidate.get("base_url") or ""),
            )
            candidate["api_key"] = keyed_roots.get(root, "")
    return candidates


def _resolve_llm_candidates() -> list[dict[str, Any]]:
    """Resolve the UI-configured primary route in its exact saved order.

    ``model_configuration.routes.primary`` is the sole source of truth.  The
    environment mirrors never contribute candidates or credentials.
    """
    candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates_for_route("primary")):
        candidate = _normalized_candidate(
            raw,
            index,
            active_model=str(raw.get("model") or ""),
            active_base_url=str(raw.get("base_url") or DEFAULT_OPENAI_BASE_URL),
            active_api_key=str(raw.get("api_key") or ""),
        )
        candidates.append(candidate)

    return _inherit_sibling_keys(candidates)


def resolve_llm_candidates() -> list[dict[str, Any]]:
    """Return the configured primary model candidates in fallback order."""
    return _resolve_llm_candidates()


def model_candidate_identity_for_response(
    session_id: str,
    model_name: str,
) -> dict[str, str]:
    """Return a secret-free identity for the candidate that produced a reply.

    The response protocol carries the actual model name but not the configured
    candidate id.  Prefer the conversation's explicit candidate when it still
    matches, then the session-prioritized candidate chain.
    """
    model = str(model_name or "").strip()
    candidates = _prioritize_last_success(
        _resolve_llm_candidates(), "primary", str(session_id or "")
    )
    match = next(
        (candidate for candidate in candidates if str(candidate.get("model") or "") == model),
        candidates[0] if candidates and not model else None,
    )
    if match is None:
        return {"candidateId": "", "adapter": "", "provider": "", "model": model, "baseUrl": "", "reasoningEffort": ""}
    return {
        "candidateId": str(match.get("id") or ""),
        "adapter": str(match.get("adapter") or match.get("provider") or ""),
        "provider": str(match.get("provider") or "openai_compatible"),
        "model": str(match.get("model") or model),
        "baseUrl": _public_base_url(match.get("base_url") or ""),
        "reasoningEffort": str(match.get("reasoning_effort") or "").strip().lower(),
    }


def resolve_session_model_candidate(session_id: str) -> dict[str, Any] | None:
    """Resolve the exact configured primary candidate selected for a session."""
    candidates = _prioritize_last_success(
        _resolve_llm_candidates(), "primary", str(session_id or "")
    )
    return dict(candidates[0]) if candidates else None


def resolve_model_profile_candidate(profile_id: str) -> dict[str, Any] | None:
    """Resolve one durable profile id, including profiles outside primary order."""

    from cyrene.runtime.model_configuration import candidate_for_profile

    raw = candidate_for_profile(str(profile_id or "").strip())
    if raw is None or "chat" not in (raw.get("capabilities") or []):
        return None
    candidate = _normalized_candidate(
        raw,
        active_model=str(raw.get("model") or ""),
        active_base_url=str(raw.get("base_url") or DEFAULT_OPENAI_BASE_URL),
        active_api_key=str(raw.get("api_key") or ""),
    )
    return _inherit_sibling_keys([candidate])[0]


def resolve_exact_model_candidate(identity: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve one prior candidate identity without allowing model fallback."""
    candidate_id = str(identity.get("candidateId") or "").strip()
    adapter = str(identity.get("adapter") or "").strip()
    provider = str(identity.get("provider") or "").strip()
    model = str(identity.get("model") or "").strip()
    base_url = str(identity.get("baseUrl") or "").strip()
    profile_id = str(identity.get("profileId") or identity.get("profile_id") or "").strip()
    profile_candidate = resolve_model_profile_candidate(profile_id) if profile_id else None
    search_candidates = _resolve_llm_candidates()
    if profile_candidate is not None and not any(
        str(item.get("id") or "") == str(profile_candidate.get("id") or "")
        for item in search_candidates
    ):
        search_candidates.append(profile_candidate)
    matches = []
    for candidate in search_candidates:
        if candidate_id and str(candidate.get("id") or "") != candidate_id:
            continue
        if adapter and str(candidate.get("adapter") or candidate.get("provider") or "") != adapter:
            continue
        if provider and str(candidate.get("provider") or "") != provider:
            continue
        if model and str(candidate.get("model") or "") != model:
            continue
        if base_url and _base_root(
            _public_base_url(candidate.get("base_url") or "")
        ) != _base_root(base_url):
            continue
        matches.append(dict(candidate))
    if len(matches) != 1:
        return None
    effort = str(identity.get("reasoningEffort") or "").strip().lower()
    if effort:
        matches[0]["reasoning_effort"] = effort
    return matches[0]


def _resolve_secondary_candidates() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates_for_route("secondary")):
        model = str(raw.get("model") or "").strip()
        if not model:
            continue
        candidate = _normalized_candidate(
            raw,
            index,
            active_model=model,
            active_base_url=str(raw.get("base_url") or DEFAULT_OPENAI_BASE_URL),
            active_api_key=str(raw.get("api_key") or ""),
        )
        candidate["profile_id"] = str(raw.get("profile_id") or raw.get("id") or "")
        candidate["max_concurrency"] = int(raw.get("max_concurrency") or 0)
        candidate["route_role"] = "secondary"
        result.append(candidate)
    return _inherit_sibling_keys(result)


def _resolve_vision_candidates() -> list[dict[str, Any]]:
    """Dedicated vision entries first, then the primary chain as fallback.

    A user configures a vision model precisely because it handles images, so it
    must be tried before the primary chat model. Trying a text-only primary
    first (e.g. DeepSeek, which 400s on ``image_url`` content) wastes a failed
    round-trip on *every* image — and serialized over many docs it was enough to
    push startup past Electron's boot timeout. When no vision model is
    configured this degrades to the primary chain alone, so a vision-capable
    primary still works. Same per-entry key semantics as the primary list."""
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for index, raw in enumerate(candidates_for_route("vision")):
        candidate = _normalized_candidate(
            raw,
            index,
            active_model=str(raw.get("model") or ""),
            active_base_url=str(raw.get("base_url") or DEFAULT_OPENAI_BASE_URL),
            active_api_key=str(raw.get("api_key") or ""),
        )
        if candidate.get("vision_capable") is False:
            continue
        key = (
            candidate["provider"], candidate.get("adapter", ""),
            candidate["model"], candidate["base_url"], candidate["api_key"],
        )
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    for candidate in _resolve_llm_candidates():
        if candidate.get("vision_capable") is False:
            continue
        key = (
            candidate["provider"], candidate.get("adapter", ""),
            candidate["model"], candidate["base_url"], candidate["api_key"],
        )
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    return _inherit_sibling_keys(candidates)


def _resolve_candidates(model_type: str) -> list[dict[str, Any]]:
    """Return ordered candidate list for the given model_type.

    * ``"primary"``   -> ``_resolve_llm_candidates()``
    * ``"secondary"`` -> secondary first, primary fallback appended
    * ``"vision"``    -> ``_resolve_vision_candidates()``
    """
    if model_type == "primary":
        return _resolve_llm_candidates()
    if model_type == "secondary":
        secondary = _resolve_secondary_candidates()
        primary = _resolve_llm_candidates()
        if secondary:
            return secondary + primary
        return primary
    if model_type == "vision":
        return _resolve_vision_candidates()
    return _resolve_llm_candidates()


# ---------------------------------------------------------------------------
# Message sanitisation
# ---------------------------------------------------------------------------


_INTERNAL_MSG_KEYS = frozenset({
    "message_id", "round_id", "round_title", "client_request_id",
    "hidden_from_ui", "system_initiated", "usage", "attachments",
    "compacted_block", "llm_compacted", "distill_attempts", "report_expanded_for_turn",
    "report_ref", "report_archive_session_id", "report_round_id",
    "report_title", "deep_reflection_record", "reflection_id",
    "subagent_flow_snapshot", "proactive",
    "runtime_guidance",
    "volatile_context_version",
    "ephemeral_model_observation",
    "_candidate_identity",
    # Canonical lane/protocol metadata belongs to the local session store. The
    # stable protocol JSON, when needed by a model, remains in ``content``.
    "lane_refs", "record_kind", "persist_model_record",
    "event_id", "turn_id", "owner_lane", "attempt",
    "_externalized_powerpoint_arguments",
    "powerpoint_episode_receipt",
    # Per-response metadata we attach to the returned message for callers to
    # inspect (e.g. detecting a max_tokens truncation), but which must not be
    # echoed back upstream when the message is replayed in history.
    "finish_reason",
})


def _strip_internal_fields(message: dict) -> dict:
    """Remove Cyrene-internal fields that must not be sent to the LLM."""
    return {k: v for k, v in message.items() if k not in _INTERNAL_MSG_KEYS}


def _materialize_internal_content(message: dict[str, Any]) -> dict[str, Any]:
    """Resolve transient local media references only for the provider request."""
    content = message.get("content")
    if not isinstance(content, list):
        return message
    from cyrene.tooling.mcp_content import (
        MCP_IMAGE_BLOCK_TYPE,
        materialize_model_content_block,
    )

    if not any(
        isinstance(block, dict) and block.get("type") == MCP_IMAGE_BLOCK_TYPE
        for block in content
    ):
        return message
    prepared = dict(message)
    prepared["content"] = [
        materialize_model_content_block(block)
        if isinstance(block, dict)
        else {"type": "text", "text": str(block)}
        for block in content
    ]
    return prepared


def _deduplicated_tool_call_id(
    original_id: str,
    *,
    message_index: int,
    call_index: int,
    occupied_ids: set[str],
) -> str:
    """Return a stable replacement for one reused tool-call ID.

    Tool-call IDs are transport correlation keys, not secrets or user-facing
    identifiers.  Deriving the replacement from its durable position keeps a
    replay byte-stable while the occupied-ID check avoids colliding with IDs
    supplied by the provider elsewhere in the same transcript.
    """
    seed = f"cyrene-tool-call-v1\0{message_index}\0{call_index}\0{original_id}"
    collision = 0
    while True:
        material = seed if collision == 0 else f"{seed}\0{collision}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
        candidate = f"call_{digest}"
        if candidate not in occupied_ids:
            occupied_ids.add(candidate)
            return candidate
        collision += 1


_DEEPSEEK_RECOVERY_RESULT_PREVIEW_CHARS = 6000


def _deepseek_recovery_result(content: Any) -> Any:
    """Keep useful tool evidence while bounding an anomalous recovery receipt."""
    if isinstance(content, str):
        text = content
    else:
        try:
            text = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(content or "")
    if len(text) <= _DEEPSEEK_RECOVERY_RESULT_PREVIEW_CHARS:
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return text
    return {
        "preview": text[:_DEEPSEEK_RECOVERY_RESULT_PREVIEW_CHARS],
        "truncated": True,
        "original_chars": len(text),
    }


def _deepseek_tool_recovery_receipt(
    assistant: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    reason: str,
) -> dict[str, Any]:
    """Convert an unreplayable provider tool episode into ordinary context.

    DeepSeek requires ``reasoning_content`` to be present on every replayed
    thinking-mode assistant tool turn, but accepts an empty string.  This
    receipt is therefore reserved for structurally incomplete episodes whose
    assistant/tool protocol cannot be replayed as one contiguous unit.
    """
    result_by_id = {
        str(result.get("tool_call_id") or ""): result for result in results
    }
    calls: list[dict[str, Any]] = []
    for raw_call in assistant.get("tool_calls") or []:
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        function = function if isinstance(function, dict) else {}
        call_id = str(raw_call.get("id") or "")
        result = result_by_id.get(call_id)
        call_receipt: dict[str, Any] = {
            "tool": str(function.get("name") or ""),
            "tool_call_id": call_id,
            "result_available": result is not None,
        }
        if result is not None:
            call_receipt["result"] = _deepseek_recovery_result(
                result.get("content")
            )
        calls.append(call_receipt)
    content = json.dumps(
        {
            "type": "deepseek_tool_episode_recovery",
            "reason": reason,
            "assistant_content": str(assistant.get("content") or "")[:2000],
            "calls": calls,
            "instruction": (
                "Treat these tool results as ordinary evidence and continue "
                "without attempting to resume the removed provider reasoning."
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {"role": "system", "content": content}


def _repair_deepseek_tool_history(messages: list[dict]) -> list[dict]:
    """Make DeepSeek thinking-mode tool history structurally replay-safe.

    Completed episodes retain string reasoning byte for byte.  Older Cyrene
    builds could drop a present-but-empty stream field, so absent/null values
    are restored to the provider-valid empty string.  Episodes whose results
    are missing or no longer contiguous are collapsed as a whole into a
    recovery receipt, avoiding orphaned tool messages while retaining bounded
    tool evidence.
    """
    source = [message for message in messages if isinstance(message, dict)]
    projected: list[dict] = []
    recovered_call_ids: set[str] = set()
    index = 0
    while index < len(source):
        message = source[index]
        calls = message.get("tool_calls")
        if (
            message.get("role") != "assistant"
            or not isinstance(calls, list)
            or not calls
        ):
            if (
                message.get("role") == "tool"
                and str(message.get("tool_call_id") or "") in recovered_call_ids
            ):
                index += 1
                continue
            projected.append(message)
            index += 1
            continue

        call_ids = [
            str(call.get("id") or "")
            for call in calls
            if isinstance(call, dict)
        ]
        results: list[dict] = []
        cursor = index + 1
        complete = len(call_ids) == len(calls) and bool(call_ids)
        for call_id in call_ids:
            if cursor >= len(source):
                complete = False
                break
            result = source[cursor]
            if (
                result.get("role") != "tool"
                or str(result.get("tool_call_id") or "") != call_id
            ):
                complete = False
                break
            results.append(result)
            cursor += 1

        if complete:
            replayable_message = message
            if not isinstance(message.get("reasoning_content"), str):
                replayable_message = dict(message)
                replayable_message["reasoning_content"] = ""
            projected.append(replayable_message)
            projected.extend(results)
            index = cursor
            continue

        reason = "incomplete_or_noncontiguous_tool_results"
        # Collect only immediately adjacent matching tool results.  Any later
        # orphan with the same id is suppressed below rather than reordered
        # across an intervening semantic message.
        results = []
        cursor = index + 1
        expected_ids = set(call_ids)
        while cursor < len(source):
            result = source[cursor]
            result_id = str(result.get("tool_call_id") or "")
            if result.get("role") != "tool" or result_id not in expected_ids:
                break
            results.append(result)
            cursor += 1
        recovered_call_ids.update(call_ids)
        logger.warning(
            "Recovered unreplayable DeepSeek tool episode [reason=%s calls=%s]",
            reason,
            ",".join(call_ids),
        )
        projected.append(
            _deepseek_tool_recovery_receipt(message, results, reason=reason)
        )
        index = cursor
    return projected


def sanitize_messages_for_llm(
    messages: list[dict],
    *,
    materialize_internal_media: bool = True,
    preserve_tool_reasoning: bool = False,
    preserve_all_reasoning: bool = False,
) -> list[dict]:
    """Normalize model messages without leaking internal transport metadata.

    Local media artifacts are encoded only for an actual provider request.
    Callers preparing a durable replay snapshot can keep the managed local
    references by setting ``materialize_internal_media=False``.
    """
    messages = [
        (
            _materialize_internal_content(_strip_internal_fields(m))
            if materialize_internal_media
            else _strip_internal_fields(m)
        )
        for m in strip_context_metadata(messages)
    ]
    if not preserve_all_reasoning and not preserve_tool_reasoning:
        for message in messages:
            message.pop("reasoning_content", None)
            message.pop("reasoning_details", None)
    elif not preserve_all_reasoning:
        # Reasoning providers require the complete assistant tool-call turn to
        # be replayed. DeepSeek, Kimi, and GLM use ``reasoning_content`` while
        # MiniMax's split-thinking OpenAI format uses ``reasoning_details``.
        # Reasoning from ordinary assistant turns is unnecessary unless the
        # provider explicitly supports preserved thinking.
        for message in messages:
            if not (
                message.get("role") == "assistant"
                and message.get("tool_calls")
            ):
                message.pop("reasoning_content", None)
                message.pop("reasoning_details", None)
    # Reserve every upstream ID, including IDs in later turns, so deterministic
    # replacements cannot accidentally shadow a valid provider-supplied ID.
    occupied_ids = {
        str(tool_call.get("id") or "")
        for message in messages
        for tool_call in (message.get("tool_calls") or [])
        if isinstance(tool_call, dict)
    }
    seen_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = str(msg.get("role", ""))

        if role == "assistant" and msg.get("tool_calls"):
            tc_list = msg["tool_calls"]
            all_valid = True
            for j, tc in enumerate(tc_list):
                idx = i + 1 + j
                if idx >= len(messages):
                    all_valid = False
                    break
                tm = messages[idx]
                if tm.get("role") != "tool" or tm.get("tool_call_id") != tc.get("id", ""):
                    all_valid = False
                    break

            if all_valid:
                old_ids = [tc.get("id", "") for tc in tc_list]
                has_dupes = (
                    any(oid in seen_ids for oid in old_ids)
                    or len(set(old_ids)) != len(old_ids)
                )

                if has_dupes:
                    new_msg = dict(msg)
                    new_tc_list = []
                    new_ids = []
                    for call_index, tc in enumerate(tc_list):
                        new_tc = dict(tc)
                        new_id = _deduplicated_tool_call_id(
                            str(tc.get("id") or ""),
                            message_index=i,
                            call_index=call_index,
                            occupied_ids=occupied_ids,
                        )
                        new_tc["id"] = new_id
                        new_tc_list.append(new_tc)
                        new_ids.append(new_id)
                        seen_ids.add(new_id)
                    new_msg["tool_calls"] = new_tc_list
                    result.append(new_msg)
                    for j, new_id in enumerate(new_ids):
                        tool_msg = dict(messages[i + 1 + j])
                        tool_msg["tool_call_id"] = new_id
                        result.append(tool_msg)
                else:
                    for oid in old_ids:
                        seen_ids.add(oid)
                    result.append(msg)
                    for j in range(len(tc_list)):
                        result.append(messages[i + 1 + j])

                i += 1 + len(tc_list)
            else:
                i += 1
        elif role == "tool":
            i += 1
        else:
            result.append(msg)
            i += 1

    return result


# Backward-compatible alias for integrations that imported the historical
# private helper. Application code should use the public name above.
_sanitize_messages_for_llm = sanitize_messages_for_llm


# ---------------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------------


def _is_minimax_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return "minimax" in normalized or normalized == "m2-her"


def _stream_timeout_profile(
    model: str,
    timeout: float,
    *,
    stream: bool,
) -> tuple[float, float | None]:
    """Return the transport and first-event timeouts for one request.

    MiniMax can keep a healthy streaming request open without emitting bytes
    for close to two minutes while it finishes a long reasoning/tool turn.  A
    single 120-second HTTP read timeout therefore races normal provider work.
    Keep the existing timeout for response headers and the first stream event,
    then let HTTPX enforce a longer per-read idle window after streaming has
    demonstrably started.
    """
    requested = float(timeout)
    if stream and _is_minimax_model(model):
        return max(requested, _MINIMAX_STREAM_IDLE_TIMEOUT_SECONDS), requested
    return requested, None


def _scan_approx_token_count(source: str) -> int:
    """Single-pass equivalent of the historical three-regex heuristic."""
    total = 0
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if "\u4e00" <= char <= "\u9fff":
            total += 1
            index += 1
            continue
        if char.isascii() and (char.isalnum() or char == "_"):
            end = index + 1
            while end < length:
                current = source[end]
                if not current.isascii() or not (current.isalnum() or current == "_"):
                    break
                end += 1
            total += max(1, (end - index + 3) // 4)
            index = end
            continue
        total += 1
        index += 1
    return total


@lru_cache(maxsize=4096)
def _cached_approx_token_count(source: str) -> int:
    return _scan_approx_token_count(source)


def _approx_token_count(text: str) -> int:
    """Estimate token count with CJK-aware heuristic.

    CJK characters average ~1 token each; runs of ASCII word chars
    average ~0.25 tokens/char (4 chars per token); punctuation/other
    are counted individually.
    """
    source = str(text or "")
    if not source.strip():
        return 0
    if len(source) <= 65_536:
        return _cached_approx_token_count(source)
    return _scan_approx_token_count(source)


def approx_token_count(text: str) -> int:
    """Public deterministic text-token estimate used for local budgets."""
    return _approx_token_count(text)


def _message_token_signature(
    message: dict[str, Any],
) -> tuple[tuple[str, ...], int]:
    text_parts: list[str] = []
    image_tokens = 0
    content = message.get("content")
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block_type in {"image_url", "cyrene_mcp_image_file"}:
                try:
                    width = max(0, int(block.get("width") or 0))
                    height = max(0, int(block.get("height") or 0))
                except (TypeError, ValueError):
                    width = height = 0
                # Provider-specific image accounting varies. Use a bounded,
                # conservative estimate for local context gating.
                image_tokens += min(4096, max(1024, (width * height + 1023) // 1024))
    else:
        text_parts.append(str(content or ""))
    text_parts.append(str(message.get("role") or ""))
    text_parts.append(str(message.get("reasoning_content") or ""))
    for tc in message.get("tool_calls") or []:
        function = tc.get("function", {})
        text_parts.append(str(function.get("name") or ""))
        text_parts.append(str(function.get("arguments") or ""))
    text_parts.append(str(message.get("tool_call_id") or ""))
    return tuple(text_parts), image_tokens


def _estimate_message_signature(signature: tuple[tuple[str, ...], int]) -> int:
    text_parts, image_tokens = signature
    return 4 + image_tokens + sum(_approx_token_count(part) for part in text_parts)


@lru_cache(maxsize=256)
def _cached_message_token_estimate(
    signature: tuple[tuple[str, ...], int],
) -> int:
    return _estimate_message_signature(signature)


def _message_token_estimate(message: dict[str, Any]) -> int:
    signature = _message_token_signature(message)
    if sum(len(part) for part in signature[0]) <= 1_000_000:
        return _cached_message_token_estimate(signature)
    return _estimate_message_signature(signature)


def message_token_estimate(message: dict[str, Any]) -> int:
    """Public deterministic message-token estimate used for context budgets."""
    return _message_token_estimate(message)


def _request_token_estimate(messages: list[dict], tools: list | None = None) -> int:
    """Conservative input-token estimate used for per-candidate context gates."""
    total = sum(_message_token_estimate(message) for message in messages)
    if tools:
        encoded_tools = json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
        if len(encoded_tools) <= 2_000_000:
            total += _cached_tools_token_estimate(encoded_tools)
        else:
            total += _approx_token_count(encoded_tools)
    return total


@lru_cache(maxsize=64)
def _cached_tools_token_estimate(encoded_tools: str) -> int:
    return _approx_token_count(encoded_tools)


def _candidate_ctx_limit(candidate: dict[str, Any]) -> int:
    """Resolve the candidate's configured window, falling back only if unset."""
    explicit = int(candidate.get("context_limit") or candidate.get("ctx_limit") or 0)
    if explicit > 0:
        return explicit
    return effective_ctx_limit_for_model(str(candidate.get("model") or ""))


def _build_payload(
    messages: list[dict],
    tools: list | None,
    max_tokens: int | None,
    stream: bool,
    model: str,
    thinking: str,
    response_format: dict[str, Any] | None = None,
    reasoning_effort: str = "",
    provider_preset: str = "",
    tool_choice: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider_model = str(model or "").strip().lower().rsplit("/", 1)[-1]
    preset = str(provider_preset or "").strip().lower()
    # Aggregators expose a provider-neutral parameter surface. Do not leak an
    # upstream vendor's private extensions merely because its id appears after
    # the catalog namespace (for example ``moonshotai/kimi-k2.5``).
    use_upstream_extensions = preset not in {"openrouter", "amd_gpu_cloud"}
    is_deepseek = use_upstream_extensions and "deepseek" in provider_model
    is_minimax = use_upstream_extensions and _is_minimax_model(model)
    is_kimi = use_upstream_extensions and provider_model.startswith("kimi-")
    is_kimi_k3 = is_kimi and provider_model.startswith("kimi-k3")
    is_kimi_k27 = is_kimi and provider_model.startswith("kimi-k2.7")
    is_kimi_k26 = is_kimi and provider_model.startswith("kimi-k2.6")
    is_glm = use_upstream_extensions and provider_model.startswith("glm-")
    provider_messages = (
        _repair_deepseek_tool_history(messages) if is_deepseek else messages
    )
    prepared_messages = sanitize_messages_for_llm(
        provider_messages,
        preserve_tool_reasoning=is_deepseek or is_minimax or is_kimi or is_glm,
        preserve_all_reasoning=is_kimi_k3 or is_kimi_k27 or is_kimi_k26 or is_glm,
    )
    for message in prepared_messages:
        if is_minimax:
            # MiniMax's OpenAI-compatible API replays split thinking through
            # reasoning_details, not Cyrene's canonical reasoning_content.
            # Agent loops intentionally persist the provider-neutral field, so
            # reconstruct MiniMax's wire shape when the raw provider object is
            # no longer present.
            canonical_reasoning = message.get("reasoning_content")
            if canonical_reasoning and not message.get("reasoning_details"):
                message["reasoning_details"] = [
                    {
                        "type": "reasoning.text",
                        "text": str(canonical_reasoning),
                    }
                ]
            message.pop("reasoning_content", None)
        elif is_deepseek:
            message.pop("reasoning_details", None)
    payload: dict[str, Any] = {
        "model": model,
        "messages": prepared_messages,
    }
    if is_minimax:
        # Without this MiniMax embeds hidden thinking in content as
        # <think>...</think>, which leaks into replies and generated titles.
        payload["reasoning_split"] = True
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
    # Constrained JSON mode (OpenAI/DeepSeek `response_format`). Only meaningful
    # without tools — providers reject/ignore it alongside function calling — so
    # callers pass it on tool-less "just emit JSON" rounds.
    if response_format is not None and not tools:
        payload["response_format"] = response_format
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

    if thinking == "auto":
        if is_deepseek:
            payload["thinking"] = {"type": "enabled"}
    elif thinking == "enabled":
        # Kimi K3 and K2.7 Code always think and reject an explicit thinking
        # switch. Other compatible reasoning models accept the common shape.
        if use_upstream_extensions and not (is_kimi_k3 or is_kimi_k27):
            payload["thinking"] = {"type": "enabled"}
    elif thinking == "disabled":
        # Keep DeepSeek thinking enabled even for callers that request the
        # legacy "disabled" mode. Other OpenAI-compatible providers may reject
        # this extension, so keep it provider/model-specific.
        if is_deepseek:
            payload["thinking"] = {"type": "enabled"}
        elif is_glm or (is_kimi and not (is_kimi_k3 or is_kimi_k27)):
            payload["thinking"] = {"type": "disabled"}
    if is_glm and thinking != "disabled":
        # GLM's interleaved/preserved thinking contract requires both the
        # original reasoning_content and clear_thinking=false on agent turns.
        payload["thinking"] = {"type": "enabled", "clear_thinking": False}
    elif is_kimi_k26 and thinking != "disabled":
        payload["thinking"] = {"type": "enabled", "keep": "all"}
    if is_deepseek and payload.get("thinking", {}).get("type") == "enabled":
        effort = str(reasoning_effort or "").strip().lower()
        if effort in {"low", "medium", "high"}:
            effort = "high"
        elif effort in {"xhigh", "max"}:
            effort = "max"
        else:
            # DeepSeek defaults ordinary thinking-mode requests to high.
            effort = "high"
        payload["reasoning_effort"] = effort
    if is_kimi_k3 and reasoning_effort:
        effort = str(reasoning_effort or "").strip().lower()
        if effort == "low":
            payload["reasoning_effort"] = "low"
        elif effort in {"medium", "high"}:
            payload["reasoning_effort"] = "high"
        elif effort in {"xhigh", "max"}:
            payload["reasoning_effort"] = "max"
    return payload


def _stable_request_fingerprint(value: Any) -> str:
    """Return a short deterministic hash without retaining request content."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class _RequestFingerprintSnapshot:
    """Content-free request hashes reusable across transport attempts."""

    message_fingerprints: tuple[str, ...]
    messages_fingerprint: str
    tools_fingerprint: str
    payload_fingerprint: str


def _prepare_request_fingerprints(
    candidate_lease: Any,
    *,
    message_units: Sequence[Any],
    tools_material: Any,
    payload_material: Any,
) -> _RequestFingerprintSnapshot | None:
    """Hash immutable request material once per candidate payload.

    Endpoints and network retries change transport identity, not request
    content.  Keeping those hashes outside both retry loops avoids repeatedly
    serializing a long transcript while retaining per-attempt diagnostics.
    """
    if candidate_lease is None or not hasattr(candidate_lease, "observe_request"):
        return None
    message_fingerprints = tuple(
        _stable_request_fingerprint(unit) for unit in message_units
    )
    return _RequestFingerprintSnapshot(
        message_fingerprints=message_fingerprints,
        messages_fingerprint=hashlib.sha256(
            "\n".join(message_fingerprints).encode("utf-8")
        ).hexdigest()[:24],
        tools_fingerprint=_stable_request_fingerprint(tools_material or []),
        payload_fingerprint=_stable_request_fingerprint(payload_material),
    )


def _request_cache_diagnostics(
    candidate_lease: Any,
    *,
    model_type: str,
    identity: dict[str, Any],
    fingerprints: _RequestFingerprintSnapshot | None,
    cache_scope: str = "",
) -> dict[str, Any]:
    """Observe one attempt using a precomputed, metadata-only snapshot."""
    if (
        fingerprints is None
        or candidate_lease is None
        or not hasattr(candidate_lease, "observe_request")
    ):
        return {}
    return candidate_lease.observe_request(
        model_type,
        identity=identity,
        message_fingerprints=fingerprints.message_fingerprints,
        messages_fingerprint=fingerprints.messages_fingerprint,
        tools_fingerprint=fingerprints.tools_fingerprint,
        payload_fingerprint=fingerprints.payload_fingerprint,
        cache_scope=cache_scope,
    )


# ---------------------------------------------------------------------------
# Response processing
# ---------------------------------------------------------------------------


def _message_from_upstream_payload(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        message = first.get("message")
        if isinstance(message, dict):
            return message
    if isinstance(data.get("message"), dict):
        return dict(data["message"])
    output = data.get("output")
    if isinstance(output, dict):
        if isinstance(output.get("message"), dict):
            return dict(output["message"])
        if isinstance(output.get("text"), str):
            return {"role": "assistant", "content": output["text"]}
    if isinstance(data.get("response"), dict):
        return dict(data["response"])
    error_text = (
        data.get("error")
        or data.get("message")
        or data.get("detail")
        or data.get("msg")
        or json.dumps(data, ensure_ascii=False)[:400]
    )
    raise ValueError(f"Upstream response missing choices/message payload: {error_text}")


_THINK_BLOCK_RE = re.compile(
    r"<think\b[^>]*>(?P<reasoning>.*?)</think\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _reasoning_details_text(details: Any) -> str:
    if isinstance(details, str):
        return details
    parts: list[str] = []
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        text = detail.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _split_embedded_thinking(content: str) -> tuple[str, str]:
    source = str(content or "")
    reasoning_parts: list[str] = []

    def _capture(match: re.Match[str]) -> str:
        reasoning_parts.append(match.group("reasoning"))
        return ""

    visible = _THINK_BLOCK_RE.sub(_capture, source)
    # A truncated response can end while the model is still inside <think>.
    # Treat that tail as reasoning instead of ever exposing it as an answer.
    dangling = re.search(
        r"<think\b[^>]*>(?P<reasoning>.*)$",
        visible,
        re.IGNORECASE | re.DOTALL,
    )
    if dangling:
        reasoning_parts.append(dangling.group("reasoning"))
        visible = visible[:dangling.start()]
    return visible.lstrip(), "".join(reasoning_parts)


def _normalize_minimax_message(message: dict[str, Any]) -> dict[str, Any]:
    """Keep MiniMax thinking replayable while exposing only answer content."""
    normalized = dict(message)
    content = normalized.get("content")
    embedded_reasoning = ""
    if isinstance(content, str):
        normalized["content"], embedded_reasoning = _split_embedded_thinking(content)

    split_reasoning = _reasoning_details_text(normalized.get("reasoning_details"))
    reasoning = split_reasoning or embedded_reasoning
    if reasoning:
        normalized["reasoning_content"] = reasoning
        if not normalized.get("reasoning_details"):
            # Compatibility fallback for gateways that ignored reasoning_split
            # but still returned MiniMax's legacy <think> wrapper.
            normalized["reasoning_details"] = [
                {"type": "reasoning.text", "text": reasoning}
            ]
    return normalized


_DSML_MARKER = r"(?:｜｜|\|\|)DSML(?:｜｜|\|\|)"
_DSML_TOOL_BLOCK_RE = re.compile(
    rf"<{_DSML_MARKER}tool_calls>(?P<body>.*?)</{_DSML_MARKER}tool_calls>",
    re.DOTALL,
)
_DSML_INVOKE_RE = re.compile(
    rf"<{_DSML_MARKER}invoke\s+name=(?P<quote>[\"'])(?P<name>.*?)(?P=quote)\s*(?:/>|>(?P<body>.*?)</{_DSML_MARKER}invoke>)",
    re.DOTALL,
)
_DSML_PARAMETER_RE = re.compile(
    rf"<{_DSML_MARKER}parameter\s+name=(?P<quote>[\"'])(?P<name>.*?)(?P=quote)(?P<attrs>[^>]*)>(?P<value>.*?)</{_DSML_MARKER}parameter>",
    re.DOTALL,
)


def _dsml_parameter_value(value: str, attrs: str) -> Any:
    text = html.unescape(str(value or "").strip())
    string_match = re.search(r"""\bstring\s*=\s*["'](?P<value>true|false)["']""", str(attrs or ""), re.IGNORECASE)
    if string_match and string_match.group("value").lower() == "true":
        return text
    try:
        return json.loads(text)
    except Exception:
        return text


def _normalize_dsml_tool_calls(message: dict[str, Any], tools: list | None) -> dict[str, Any]:
    """Convert DeepSeek's textual DSML fallback into OpenAI-style tool calls."""
    if not tools or message.get("tool_calls"):
        return message
    content = message.get("content")
    if not isinstance(content, str) or "DSML" not in content:
        return message

    allowed_names = {
        str(tool.get("function", {}).get("name") or "").strip()
        for tool in tools
        if isinstance(tool, dict)
    }
    blocks = list(_DSML_TOOL_BLOCK_RE.finditer(content))
    if not blocks:
        return message

    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        invocations = list(_DSML_INVOKE_RE.finditer(block.group("body")))
        if not invocations:
            return message
        for invocation in invocations:
            name = html.unescape(invocation.group("name")).strip()
            if not name or name not in allowed_names:
                return message
            arguments: dict[str, Any] = {}
            for parameter in _DSML_PARAMETER_RE.finditer(invocation.group("body") or ""):
                parameter_name = html.unescape(parameter.group("name")).strip()
                if parameter_name:
                    arguments[parameter_name] = _dsml_parameter_value(parameter.group("value"), parameter.group("attrs"))
            tool_calls.append({
                "index": len(tool_calls),
                "id": f"call_dsml_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            })

    normalized = dict(message)
    normalized["content"] = _DSML_TOOL_BLOCK_RE.sub("", content).strip()
    normalized["tool_calls"] = tool_calls
    return normalized


_TEXT_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call\b[^>]*>(?P<body>.*?)</tool_call>",
    re.IGNORECASE | re.DOTALL,
)
_TEXT_FUNCTION_RE = re.compile(
    r"<function\s*=\s*(?P<quote>[\"']?)(?P<name>[^>\"']+)(?P=quote)\s*>"
    r"(?P<body>.*?)</function>",
    re.IGNORECASE | re.DOTALL,
)
_TEXT_PARAMETER_RE = re.compile(
    r"<parameter\s*=\s*(?P<quote>[\"']?)(?P<name>[^>\"']+)(?P=quote)\s*>"
    r"(?P<value>.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)


def _tool_name_lookup(tools: list | None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = str((tool.get("function") or {}).get("name") or "").strip()
        if name:
            lookup.setdefault(name.casefold(), name)
    return lookup


def _canonical_provider_tool_call(
    raw: Any,
    *,
    index: int,
    tools: list | None,
    require_allowed: bool,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    function = raw.get("function")
    if isinstance(function, dict):
        name = str(function.get("name") or raw.get("name") or "").strip()
        arguments = function.get(
            "arguments",
            function.get("arguments_json", function.get("parameters", {})),
        )
    else:
        name = str(raw.get("name") or function or "").strip()
        arguments = raw.get(
            "arguments",
            raw.get("arguments_json", raw.get("parameters", {})),
        )
    if not name:
        return None

    lookup = _tool_name_lookup(tools)
    canonical_name = lookup.get(name.casefold())
    if require_allowed and canonical_name is None:
        return None
    name = canonical_name or name

    try:
        arguments_text = canonical_tool_arguments(arguments)
    except ValueError:
        # Preserve an irreparable string so the execution loop can return a
        # precise invalid-arguments result instead of silently changing it.
        arguments_text = (
            arguments
            if isinstance(arguments, str)
            else json.dumps(arguments, ensure_ascii=False, default=str)
        )
    call_id = str(
        raw.get("id")
        or raw.get("tool_call_id")
        or f"call_compat_{uuid.uuid4().hex[:16]}"
    )
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments_text,
        },
    }


def _text_parameter_value(value: str) -> Any:
    source = html.unescape(str(value or "").strip())
    try:
        return json.loads(source)
    except (TypeError, ValueError, json.JSONDecodeError):
        return source


def _tool_calls_from_text_block(
    body: str,
    tools: list | None,
) -> list[dict[str, Any]]:
    source = html.unescape(str(body or "").strip())
    if source.startswith("```") and source.endswith("```"):
        source = re.sub(r"^\s*```(?:json|javascript|js)?\s*", "", source, flags=re.IGNORECASE)
        source = re.sub(r"\s*```\s*$", "", source)

    decoded: Any = None
    try:
        decoded = json.loads(source)
    except (TypeError, ValueError, json.JSONDecodeError):
        try:
            decoded = parse_tool_arguments(source)
        except ValueError:
            decoded = None
    raw_calls: list[Any] = []
    if isinstance(decoded, list):
        raw_calls = decoded
    elif isinstance(decoded, dict):
        nested_calls = decoded.get("tool_calls")
        raw_calls = nested_calls if isinstance(nested_calls, list) else [decoded]

    calls: list[dict[str, Any]] = []
    for raw in raw_calls:
        call = _canonical_provider_tool_call(
            raw,
            index=len(calls),
            tools=tools,
            require_allowed=True,
        )
        if call is not None:
            calls.append(call)
    if calls:
        return calls

    # Compatibility with older Qwen/Hermes XML-like templates:
    # <tool_call><function=Read><parameter=path>...</parameter></function></tool_call>
    for function_match in _TEXT_FUNCTION_RE.finditer(source):
        arguments: dict[str, Any] = {}
        for parameter in _TEXT_PARAMETER_RE.finditer(function_match.group("body")):
            name = html.unescape(parameter.group("name")).strip()
            if name:
                arguments[name] = _text_parameter_value(parameter.group("value"))
        call = _canonical_provider_tool_call(
            {
                "name": html.unescape(function_match.group("name")).strip(),
                "arguments": arguments,
            },
            index=len(calls),
            tools=tools,
            require_allowed=True,
        )
        if call is not None:
            calls.append(call)
    return calls


def _normalize_provider_tool_calls(
    message: dict[str, Any],
    tools: list | None,
) -> dict[str, Any]:
    """Normalize OpenAI, legacy, Qwen, and Hermes tool-call representations."""
    if not tools:
        return message
    normalized = dict(message)
    calls: list[dict[str, Any]] = []

    raw_calls = normalized.get("tool_calls")
    if isinstance(raw_calls, dict):
        raw_calls = [raw_calls]
    if isinstance(raw_calls, list):
        for raw in raw_calls:
            call = _canonical_provider_tool_call(
                raw,
                index=len(calls),
                tools=tools,
                require_allowed=False,
            )
            if call is not None:
                calls.append(call)

    if not calls:
        legacy = normalized.get("function_call") or normalized.get("tool_call")
        call = _canonical_provider_tool_call(
            legacy,
            index=0,
            tools=tools,
            require_allowed=False,
        )
        if call is not None:
            calls.append(call)

    content = normalized.get("content")
    if not calls and isinstance(content, str):
        kept: list[str] = []
        cursor = 0
        for match in _TEXT_TOOL_CALL_BLOCK_RE.finditer(content):
            parsed = _tool_calls_from_text_block(match.group("body"), tools)
            if not parsed:
                continue
            kept.append(content[cursor:match.start()])
            cursor = match.end()
            for parsed_call in parsed:
                parsed_call["index"] = len(calls)
                calls.append(parsed_call)
        if calls:
            kept.append(content[cursor:])
            normalized["content"] = "".join(kept).strip()
        else:
            # Some local templates emit a bare JSON action without markers.
            parsed = _tool_calls_from_text_block(content, tools)
            if parsed:
                calls = parsed
                normalized["content"] = ""

    normalized.pop("function_call", None)
    normalized.pop("tool_call", None)
    if calls:
        normalized["tool_calls"] = calls
    elif "tool_calls" in normalized:
        normalized.pop("tool_calls", None)
    return normalized


def _normalize_tool_call_protocol(
    message: dict[str, Any],
    tools: list | None,
) -> dict[str, Any]:
    normalized = _normalize_dsml_tool_calls(message, tools)
    return _normalize_provider_tool_calls(normalized, tools)


def _normalized_usage(usage: Any, messages: list[dict[str, Any]], response_message: dict[str, Any]) -> dict[str, int]:
    if isinstance(usage, dict) and any(
        isinstance(usage.get(key), int)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens")
    ):
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        normalized: dict[str, int] = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }
        for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            if isinstance(usage.get(key), int):
                normalized[key] = int(usage.get(key))
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict) and isinstance(prompt_details.get("cached_tokens"), int):
            cached = int(prompt_details.get("cached_tokens") or 0)
            normalized["prompt_cache_hit_tokens"] = cached
            normalized.setdefault("prompt_cache_miss_tokens", max(0, prompt - cached))
        if isinstance(usage.get("cache_read_input_tokens"), int):
            normalized["prompt_cache_hit_tokens"] = int(usage.get("cache_read_input_tokens") or 0)
        if isinstance(usage.get("cache_creation_input_tokens"), int):
            normalized["prompt_cache_miss_tokens"] = int(usage.get("cache_creation_input_tokens") or 0)
        return normalized
    prompt = sum(_message_token_estimate(message) for message in messages) + 8
    completion = _message_token_estimate(response_message) + 8
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _extract_stream_delta_text(delta: dict[str, Any]) -> str:
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _looks_like_vision_capability_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return any(token in detail for token in ("image", "vision", "multimodal", "unsupported", "invalid content", "input_image"))


# ---------------------------------------------------------------------------
# Token recording
# ---------------------------------------------------------------------------


def _record_token_usage_faf(
    model: str,
    usage: dict,
    duration_ms: int,
    caller: str,
    *,
    round_id: str = "",
    session_id: str = "",
) -> None:
    """Fire-and-forget token usage recording."""
    _record_success_telemetry_faf(
        model,
        usage,
        duration_ms,
        caller,
        round_id=round_id,
        session_id=session_id,
    )


def _record_success_telemetry_faf(
    model: str,
    usage: dict,
    duration_ms: int,
    caller: str,
    *,
    round_id: str = "",
    session_id: str = "",
    latency_event: dict[str, Any] | None = None,
    record_usage: bool = True,
) -> None:
    """Persist the normal success-path telemetry with a single SQLite commit."""
    from cyrene.observability.trace import current_trace_context
    from cyrene.runtime.database import record_llm_telemetry_batch

    token_events = []
    if record_usage:
        token_events.append({
            "model": model,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens") or 0),
            "cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens") or 0),
            "duration_ms": duration_ms,
            "round_id": round_id,
            "session_id": session_id,
            "caller": caller,
        })
    latency_events = []
    if latency_event is not None:
        trace = current_trace_context()
        attempt = int(latency_event.get("attempt") or 1)
        call_id = str(latency_event.get("call_id") or "")
        latency_events.append({
            **latency_event,
            "trace_id": trace.trace_id,
            "run_id": trace.run_id,
            "parent_span_id": trace.parent_span_id,
            "span_id": f"{call_id}.attempt.{attempt}" if call_id else "",
        })
    if not token_events and not latency_events:
        return
    _bg_token_task(asyncio.create_task(record_llm_telemetry_batch(
        str(DB_PATH),
        token_events=token_events,
        latency_events=latency_events,
    )))


def _record_latency_faf(event: dict[str, Any]) -> None:
    """Persist a request-attempt span without delaying the model loop."""
    from cyrene.observability.trace import current_trace_context
    from cyrene.runtime.database import record_llm_latency

    trace = current_trace_context()
    attempt = int(event.get("attempt") or 1)
    call_id = str(event.get("call_id") or "")
    _bg_token_task(asyncio.create_task(record_llm_latency(
        str(DB_PATH),
        **event,
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        parent_span_id=trace.parent_span_id,
        span_id=f"{call_id}.attempt.{attempt}" if call_id else "",
    )))


# ---------------------------------------------------------------------------
# SSE event publishing
# ---------------------------------------------------------------------------


async def _publish_llm_event(
    caller: str,
    phase: str,
    messages: list[dict],
    tools: list | None,
    response: dict,
    model: str,
    duration_ms: int,
    provider: str = "",
    session_id: str = "",
    round_id: str = "",
    status: str = "completed",
    prepared_messages: list[dict[str, Any]] | None = None,
    prepared_context_trace: dict[str, Any] | None = None,
) -> None:
    from cyrene.observability import debug

    event = {
        "type": "llm_call",
        "caller": caller,
        "phase": phase,
        "model": model,
        "provider": str(provider or ""),
        "tools": [t.get("function", {}).get("name") for t in (tools or [])],
        "messages": (
            prepared_messages
            if prepared_messages is not None
            else sanitize_messages_for_llm(messages)
        ),
        "context_trace": (
            prepared_context_trace
            if prepared_context_trace is not None
            else summarize_context_trace(messages)
        ),
        "response": response,
        "usage": response.get("usage") or {},
        "duration_ms": duration_ms,
        "status": status,
    }
    if round_id:
        event["round_id"] = round_id
    if session_id:
        await debug.publish_event(event, session_id=session_id)
    else:
        await debug.publish_event(event)


async def _publish_model_fallback_event(
    *,
    session_id: str,
    round_id: str,
    failed_model: str,
    fallback_model: str,
) -> None:
    from cyrene.observability import debug

    if session_id and round_id and fallback_model:
        try:
            await persist_model_status(
                session_id,
                round_id,
                status="switched",
                model=fallback_model,
            )
        except Exception:
            logger.exception(
                "Failed to persist model fallback card [session=%s round=%s]",
                session_id,
                round_id,
            )

    event = {
        "type": "phase_transition",
        "from": "primary_model",
        "to": "fallback_model",
        "detail": "Primary model unavailable, switching to a fallback model.",
        "detail_key": "phase.modelFallback",
        "detail_params": {
            "failedModel": str(failed_model or ""),
            "fallbackModel": str(fallback_model or ""),
        },
    }
    if round_id:
        event["round_id"] = round_id
    await debug.publish_event(event, session_id=session_id)


async def _publish_model_retry_event(
    *,
    session_id: str,
    round_id: str,
    model: str,
    retry_count: int,
    retry_limit: int,
) -> None:
    """Publish and checkpoint the current retry count for a chat round."""
    from cyrene.observability import debug

    if session_id and round_id and model:
        try:
            await persist_model_status(
                session_id,
                round_id,
                status="retry",
                model=model,
                retry_count=retry_count,
                retry_limit=retry_limit,
            )
        except Exception:
            logger.exception(
                "Failed to persist model retry card [session=%s round=%s]",
                session_id,
                round_id,
            )
    event = {
        "type": "phase_transition",
        "from": "model_request",
        "to": "model_retry",
        "detail": "Retrying the current model.",
        "detail_key": "phase.modelRetry",
        "detail_params": {
            "model": str(model or ""),
            "retryCount": max(0, int(retry_count or 0)),
            "retryLimit": max(0, int(retry_limit or 0)),
        },
    }
    if round_id:
        event["round_id"] = round_id
    await debug.publish_event(event, session_id=session_id)


async def _publish_codex_availability_event(
    *,
    session_id: str,
    round_id: str,
    model: str,
    failure_kind: str,
) -> None:
    """Surface actionable Codex failures even when another model can recover."""
    from cyrene.model_runtime.codex_provider import (
        CODEX_AUTHENTICATION_EXPIRED,
        CODEX_MODEL_UNAVAILABLE,
        CODEX_QUOTA_EXHAUSTED,
    )
    from cyrene.observability import debug

    detail_by_kind = {
        CODEX_QUOTA_EXHAUSTED: (
            "Codex quota is exhausted. Wait for the quota window to reset "
            "or switch models."
        ),
        CODEX_AUTHENTICATION_EXPIRED: (
            "Codex authentication has expired. Sign in to Codex again."
        ),
        CODEX_MODEL_UNAVAILABLE: (
            "The selected Codex model is unavailable. Choose another Codex model."
        ),
    }
    detail_key_by_kind = {
        CODEX_QUOTA_EXHAUSTED: "phase.codexQuotaExhausted",
        CODEX_AUTHENTICATION_EXPIRED: "phase.codexAuthenticationExpired",
        CODEX_MODEL_UNAVAILABLE: "phase.codexModelUnavailable",
    }
    if failure_kind not in detail_by_kind:
        return
    event = {
        "type": "phase_transition",
        "from": "codex_model",
        "to": "model_attention",
        "detail": detail_by_kind[failure_kind],
        "detail_key": detail_key_by_kind[failure_kind],
        "detail_params": {"model": str(model or "")},
        "failed": True,
        "alert": True,
        "alert_level": "warning",
        "failure_kind": failure_kind,
    }
    if round_id:
        event["round_id"] = round_id
    await debug.publish_event(event, session_id=session_id)


async def _publish_llm_transport_event(
    *,
    session_id: str,
    round_id: str,
    caller: str,
    phase: str,
    model: str,
    event: dict[str, Any],
) -> None:
    """Publish provider connection/retry state without exposing hidden reasoning."""
    from cyrene.observability import debug

    payload = {
        "type": "llm_transport",
        "provider": str(event.get("provider") or ""),
        "transport": str(event.get("transport") or ""),
        "status": str(event.get("status") or ""),
        "error_kind": str(event.get("error_kind") or ""),
        "will_retry": bool(event.get("will_retry")),
        "message": str(event.get("message") or "")[:1000],
        "thread_id": str(event.get("thread_id") or ""),
        "turn_id": str(event.get("turn_id") or ""),
        "reasoning_effort": str(event.get("reasoning_effort") or ""),
        "caller": caller,
        "phase": phase,
        "model": model,
    }
    if round_id:
        payload["round_id"] = round_id
    await debug.publish_event(payload, session_id=session_id)


# ---------------------------------------------------------------------------
# The unified call_llm function
# ---------------------------------------------------------------------------


def _rank_explicit_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **candidate,
            "_configured_rank": candidate.get("_configured_rank", index),
            "_endpoint_ranks": {
                endpoint: (candidate.get("_endpoint_ranks") or {}).get(endpoint, rank)
                for rank, endpoint in enumerate(candidate.get("endpoints") or [])
            },
        }
        for index, candidate in enumerate(candidates)
    ]


def _candidate_label(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('id')}({candidate.get('model')}@{candidate.get('base_url')})"


async def call_llm(
    messages: list[dict],
    *,
    tools: list | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    model_type: str = "primary",
    candidates: list[dict] | None = None,
    candidate_lease: Any = None,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    stream: bool = False,
    stream_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    thinking: str = "auto",
    response_format: dict[str, Any] | None = None,
    caller: str = "unknown",
    phase: str = "unknown",
    return_text: bool = False,
    publish_events: bool = True,
    record_usage: bool = True,
    record_latency: bool | None = None,
    round_id: str = "",
    session_id: str = "",
    cache_scope: str = "",
    cache_epoch: str | int = "",
) -> dict | str:
    """Unified LLM calling entry point.

    Args:
        messages: The conversation history.
        tools: Optional tool definitions.
        tool_choice: Optional provider tool-selection policy. When omitted,
            requests carrying tools use ``"auto"``.
        model_type: ``"primary"``, ``"secondary"``, or ``"vision"``.
        candidates: Explicit candidate list (overrides ``model_type``).
        candidate_lease: Optional run-owned lease used to retain candidate
            affinity and compare final provider request fingerprints.
        max_tokens: If ``None``, omit from payload (let the model decide).
        timeout: HTTP client timeout in seconds.
        stream: If ``True``, emit ``reply_start`` / ``reply_delta`` / ``reply_done``
            events via ``stream_callback`` and return the accumulated text.
        stream_callback: Called with events when *stream* is ``True``.
        thinking: ``"auto"`` (enable for DeepSeek models), ``"enabled"``, ``"disabled"``.
        caller: Identifier used in SSE events and token recording.
        phase: Execution phase tag for SSE events.
        return_text: Return plain ``str`` instead of a message ``dict``.
        publish_events: Whether to publish ``llm_call`` SSE events.
        record_usage: Whether to record token usage to the database.
        record_latency: Whether to record per-attempt latency spans. Defaults to
            the value of ``record_usage`` so lightweight/test calls can disable
            all persistence with one flag.
        cache_scope: Optional dual-lane cache partition. ``decision`` and
            ``execution`` produce independent stable provider cache routes;
            the empty default preserves the historical transport payload.
        cache_epoch: Optional lane-local cache generation reserved for
            coordinator-driven context compaction. The empty default is the
            initial epoch.

    Returns:
        Message ``dict`` with keys ``role``, ``content``, ``usage``, ``model``
        (and optionally ``tool_calls``, ``reasoning_content``).
        If ``return_text=True``, returns the content as ``str`` instead.

    Raises:
        httpx.HTTPError: When all candidates and endpoints fail.
    """
    global _secondary_in_flight

    _t0 = _time.monotonic()
    call_id = f"llm_{uuid.uuid4().hex}"
    latency_enabled = record_usage if record_latency is None else bool(record_latency)

    expected_family = getattr(candidate_lease, "provider_family", None)
    resolved = candidates if candidates is not None else _resolve_candidates(model_type)
    if not resolved and expected_family is not None:
        family = ProviderFamily(str(expected_family))
        raise ProviderFamilyError(
            f"{model_type} model route has no {family.value} candidates"
        )
    if not resolved:
        resolved = _resolve_llm_candidates()
    if not resolved:
        # No model configured at all (fresh install, never onboarded / saved a
        # model). Keep the historical "return empty string" contract so callers
        # degrade exactly as before, but log it — previously a phantom env
        # candidate masked this by 401'ing on a default endpoint instead.
        logger.error(
            "call_llm has no model candidates [caller=%s phase=%s]; configure one in Settings → Models.",
            caller, phase,
        )
        return ""
    if candidates is None:
        resolved = _prioritize_last_success(resolved, model_type, session_id)
    else:
        resolved = _rank_explicit_candidates(resolved)
    active_family = require_single_provider_family(
        resolved,
        expected=expected_family,
        context=f"{model_type} model route",
    )

    # Context capacity belongs to the candidate that will receive the request,
    # not only to the configured primary.  Reject undersized candidates locally
    # and continue down the chain without cooling them: they are healthy, merely
    # incompatible with this request.  Automatic compaction follows the active
    # model's configured window; this gate separately protects every fallback
    # candidate from receiving a request beyond its own capacity.
    request_tokens = _request_token_estimate(messages, tools)
    output_reserve = max(int(max_tokens or 0), 0)
    required_tokens = request_tokens + output_reserve
    context_rejected: list[dict[str, Any]] = []
    context_eligible: list[dict[str, Any]] = []
    for candidate in resolved:
        limit = _candidate_ctx_limit(candidate)
        if limit > 0 and required_tokens > limit:
            context_rejected.append(candidate)
            logger.warning(
                "call_llm skipped candidate beyond context window "
                "[caller=%s phase=%s model=%s required=%d limit=%d]",
                caller, phase, candidate.get("model"), required_tokens, limit,
            )
        else:
            context_eligible.append(candidate)
    if not context_eligible:
        limits = ", ".join(
            f"{candidate.get('model')}={_candidate_ctx_limit(candidate)}"
            for candidate in context_rejected
        )
        raise ValueError(
            f"LLM request requires about {required_tokens} tokens, exceeding all "
            f"candidate context windows ({limits})"
        )
    resolved = context_eligible

    # ctx_limit check for secondary model: if messages exceed the limit,
    # skip secondary and fall through to primary candidates
    if resolved and resolved[0].get("route_role") == "secondary":
        ctx_limit = int(resolved[0].get("ctx_limit") or 0)
        if ctx_limit > 0:
            total_tokens = sum(_message_token_estimate(m) for m in messages)
            if total_tokens > ctx_limit:
                resolved = resolved[1:] if len(resolved) > 1 else _resolve_llm_candidates()
                require_single_provider_family(
                    resolved,
                    expected=active_family,
                    context=f"{model_type} model fallback route",
                )

    # Always evaluate candidates in the saved primary-route order.  Cooldown
    # state remains useful for diagnostics, but skipping a configured entry on
    # a later call makes actual model use diverge from the Settings ordering.
    available = resolved
    skipped_cooling: list[dict[str, Any]] = []
    failed_this_call: list[str] = []
    rejected_primary = next(
        (
            candidate for candidate in context_rejected
            if int(candidate.get("_configured_rank") or 0) == 0
        ),
        None,
    )
    configured_primary = rejected_primary or next(
        (candidate for candidate in resolved if int(candidate.get("_configured_rank") or 0) == 0),
        resolved[0],
    )
    failed_primary_model = ""
    if any(
        int(candidate.get("_configured_rank") or 0) == 0
        for candidate in [*skipped_cooling, *context_rejected]
    ):
        # A context rejection means the configured primary cannot accept this
        # request and is therefore a genuine fallback reason.
        failed_primary_model = str(configured_primary.get("model") or "")
    fallback_notice_sent = False
    attempt_number = 0
    retry_backoff_ms = 0.0

    # Debug events historically normalized and traced the complete history for
    # every endpoint start and again on success.  Both projections are stable
    # for the lifetime of this logical call, so prepare them once.
    event_messages = sanitize_messages_for_llm(messages) if publish_events else None
    event_context_trace = summarize_context_trace(messages) if publish_events else None

    try:
        last_error: Exception | None = None

        for candidate_position, candidate in enumerate(available):
            is_secondary = candidate.get("route_role") == "secondary"
            max_conc = int(candidate.get("max_concurrency") or 0)
            provider = str(candidate.get("provider") or "openai_compatible")
            adapter = str(candidate.get("adapter") or provider).strip().lower()
            candidate_model = str(candidate.get("model") or "").strip()
            transport_timeout, first_event_timeout = _stream_timeout_profile(
                candidate_model,
                timeout,
                stream=stream,
            )
            from cyrene.runtime.network_proxy import configured_proxy_url

            candidate_proxy_url = configured_proxy_url(
                opt_in=candidate.get("use_proxy") is True
            )
            if candidate_proxy_url:
                client, connection_pool_key, client_pool_reused = _get_http_client(
                    transport_timeout,
                    candidate_proxy_url,
                )
            else:
                # Preserve the one-argument call for direct connections and
                # test doubles written against the original client factory.
                client, connection_pool_key, client_pool_reused = _get_http_client(
                    transport_timeout
                )

            # Concurrency guard for secondary model
            if is_secondary and max_conc > 0 and _secondary_in_flight >= max_conc:
                continue

            try:
                if (
                    int(candidate.get("_configured_rank") or 0) > 0
                    and failed_primary_model
                    and not fallback_notice_sent
                    and model_type == "primary"
                ):
                    fallback_model = str(candidate.get("model") or "")
                    if _claim_model_fallback_notice(
                        session_id=session_id,
                        round_id=round_id,
                        failed_model=failed_primary_model,
                        fallback_model=fallback_model,
                    ):
                        await _publish_model_fallback_event(
                            session_id=session_id,
                            round_id=round_id,
                            failed_model=failed_primary_model,
                            fallback_model=fallback_model,
                        )
                    fallback_notice_sent = True
                if is_secondary and max_conc > 0:
                    _secondary_in_flight += 1
                model = candidate_model
                from cyrene.model_runtime.protocol_adapters import (
                    NATIVE_PROTOCOL_ADAPTERS,
                    prepare_request as prepare_protocol_request,
                )

                native_request = None
                if adapter in NATIVE_PROTOCOL_ADAPTERS:
                    native_messages = sanitize_messages_for_llm(messages)
                    native_request = prepare_protocol_request(
                        adapter,
                        api_key=str(candidate.get("api_key") or "").strip(),
                        messages=native_messages,
                        tools=tools,
                        model=model,
                        max_tokens=max_tokens,
                        stream=stream,
                        response_format=response_format,
                        reasoning_effort=str(candidate.get("reasoning_effort") or ""),
                        tool_choice=tool_choice,
                    )
                    payload = native_request.payload
                else:
                    payload = _build_payload(
                        messages,
                        tools,
                        max_tokens,
                        stream,
                        model,
                        thinking,
                        response_format,
                        reasoning_effort=str(candidate.get("reasoning_effort") or ""),
                        provider_preset=_candidate_provider_preset(candidate),
                        tool_choice=tool_choice,
                    )

                provider_message_units = (
                    native_messages
                    if native_request is not None
                    else payload.get("messages") or []
                )
                provider_tools_material = payload.get("tools") or []
                provider_cache_route_key = _provider_prompt_cache_route_key(
                    candidate,
                    model=model,
                    cache_scope=cache_scope,
                    message_units=provider_message_units,
                    tool_schema=provider_tools_material,
                    cache_epoch=cache_epoch,
                )
                if (
                    provider_cache_route_key
                    and _candidate_accepts_prompt_cache_key(candidate)
                ):
                    # Chat Completions and Responses use the same OpenAI field.
                    # ``PreparedRequest`` is frozen, but intentionally owns a
                    # mutable payload dict that is still local to this call.
                    payload["prompt_cache_key"] = provider_cache_route_key
                codex_request_material: dict[str, Any] | None = None
                if provider == "codex_oauth":
                    from cyrene.model_runtime.codex_provider import (
                        provider_request_cache_material,
                    )

                    codex_request_material = provider_request_cache_material(
                        messages=messages,
                        tools=tools,
                        model=model,
                        phase=phase,
                        reasoning_effort=str(
                            candidate.get("reasoning_effort") or ""
                        ),
                    )

                request_material = (
                    {
                        "thread_params": codex_request_material["thread_params"],
                        "turn_params": codex_request_material["turn_params"],
                    }
                    if codex_request_material is not None
                    else payload
                )
                request_message_units = (
                    codex_request_material["message_units"]
                    if codex_request_material is not None
                    else provider_message_units
                )
                request_tools_material = (
                    codex_request_material["action_tools"]
                    if codex_request_material is not None
                    else provider_tools_material
                )
                request_fingerprint_material = (
                    {
                        "provider_request": request_material,
                        "cache_route_key": provider_cache_route_key,
                    }
                    if provider_cache_route_key
                    else request_material
                )
                request_fingerprints = _prepare_request_fingerprints(
                    candidate_lease,
                    message_units=request_message_units,
                    tools_material=request_tools_material,
                    payload_material=request_fingerprint_material,
                )

                if native_request is not None:
                    headers = dict(native_request.headers)
                else:
                    headers = {"Content-Type": "application/json"}
                    api_key = str(candidate.get("api_key") or "").strip()
                    if api_key and api_key.lower() not in ("lmstudio", "dummy", ""):
                        headers["Authorization"] = f"Bearer {api_key}"

                endpoints = list(candidate.get("endpoints") or [])
                candidate_error: Exception | None = None

                for endpoint_position, endpoint in enumerate(endpoints):
                    if publish_events:
                        await _publish_llm_event(
                            caller, phase, messages, tools, {}, model, 0,
                            provider=provider, session_id=session_id,
                            round_id=round_id, status="started",
                            prepared_messages=event_messages,
                            prepared_context_trace=event_context_trace,
                        )
                    try:
                        network_retries = 0
                        server_error_retries = 0
                        while True:
                            attempt_number += 1
                            attempt_started = _time.monotonic()
                            stream_timing: dict[str, float] = {}
                            stream_event_emitted = False
                            request_identity = {
                                "candidateId": str(candidate.get("id") or ""),
                                "adapter": str(candidate.get("adapter") or provider),
                                "provider": provider,
                                "model": model,
                                "endpoint": endpoint,
                                "reasoningEffort": str(
                                    candidate.get("reasoning_effort") or ""
                                ).strip().lower(),
                                "cacheRouteKey": provider_cache_route_key,
                            }
                            request_diagnostics = _request_cache_diagnostics(
                                candidate_lease,
                                model_type=model_type,
                                identity=request_identity,
                                fingerprints=request_fingerprints,
                                cache_scope=cache_scope,
                            )

                            async def _tracked_stream_callback(event: dict[str, Any]) -> None:
                                nonlocal stream_event_emitted
                                stream_event_emitted = True
                                if stream_callback:
                                    await stream_callback({
                                        **event,
                                        "caller": caller,
                                        "phase": phase,
                                        "model": model,
                                        "provider": provider,
                                    })

                            async def _tracked_transport_callback(
                                event: dict[str, Any],
                            ) -> None:
                                await _publish_llm_transport_event(
                                    session_id=session_id,
                                    round_id=round_id,
                                    caller=caller,
                                    phase=phase,
                                    model=model,
                                    event=event,
                                )

                            try:
                                if provider == "cyrene_plugin":
                                    from cyrene.plugins.integrations import complete_chat_candidate

                                    msg = await complete_chat_candidate(
                                        candidate,
                                        messages=messages,
                                        tools=tools,
                                        max_tokens=max_tokens,
                                        stream=stream,
                                        thinking=thinking,
                                        response_format=response_format,
                                        caller=caller,
                                        phase=phase,
                                        session_id=session_id,
                                        timeout=timeout,
                                    )
                                    plugin_events = msg.pop("_plugin_stream_events", [])
                                    if stream:
                                        if plugin_events:
                                            for plugin_event in plugin_events:
                                                if isinstance(plugin_event, dict):
                                                    await _tracked_stream_callback(plugin_event)
                                        else:
                                            await _tracked_stream_callback({"type": "reply_start"})
                                            reasoning_text = str(msg.get("reasoning_content") or "")
                                            if reasoning_text:
                                                await _tracked_stream_callback({"type": "reasoning_start"})
                                                await _tracked_stream_callback({"type": "reasoning_delta", "delta": reasoning_text})
                                            content_text = str(msg.get("content") or "")
                                            if content_text:
                                                await _tracked_stream_callback({"type": "reply_delta", "delta": content_text})
                                            await _tracked_stream_callback({"type": "reply_done", "response": content_text})
                                    msg["usage"] = _normalized_usage(msg.get("usage"), messages, msg)
                                elif provider == "codex_oauth":
                                    from cyrene.model_runtime.codex_provider import (
                                        CODEX_QUOTA_EXHAUSTED,
                                        CodexAvailabilityError,
                                        get_codex_provider,
                                    )

                                    codex = get_codex_provider()
                                    if bool(get_setting("codex_budget_enabled", True)):
                                        if not await codex.quota_available():
                                            raise CodexAvailabilityError(
                                                CODEX_QUOTA_EXHAUSTED,
                                                "Codex quota is exhausted; wait for the quota window to reset"
                                            )
                                    msg = await codex.complete(
                                        messages=messages,
                                        tools=tools,
                                        model=model,
                                        phase=phase,
                                        reasoning_effort=str(
                                            candidate.get("reasoning_effort") or ""
                                        ),
                                        timeout=timeout,
                                        stream_callback=(
                                            _tracked_stream_callback if stream else None
                                        ),
                                        transport_callback=_tracked_transport_callback,
                                    )
                                elif adapter in NATIVE_PROTOCOL_ADAPTERS and stream:
                                    from cyrene.model_runtime.protocol_adapters import handle_stream as handle_protocol_stream

                                    msg = await handle_protocol_stream(
                                        adapter,
                                        client,
                                        endpoint,
                                        native_request,
                                        _tracked_stream_callback,
                                        stream_timing,
                                    )
                                elif stream:
                                    msg = await _handle_stream(
                                        client,
                                        endpoint,
                                        payload,
                                        headers,
                                        _tracked_stream_callback,
                                        stream_timing,
                                        first_event_timeout=first_event_timeout,
                                    )
                                else:
                                    resp = await client.post(endpoint, json=payload, headers=headers)
                                    if resp.status_code != 200:
                                        resp.raise_for_status()
                                    data = resp.json()
                                    if adapter in NATIVE_PROTOCOL_ADAPTERS:
                                        from cyrene.model_runtime.protocol_adapters import parse_response as parse_protocol_response

                                        msg = parse_protocol_response(adapter, data)
                                    else:
                                        msg = _message_from_upstream_payload(data)
                                        msg["usage"] = _normalized_usage(data.get("usage"), messages, msg)
                                        _choices = data.get("choices")
                                        if isinstance(_choices, list) and _choices and isinstance(_choices[0], dict):
                                            _finish = _choices[0].get("finish_reason")
                                            if _finish:
                                                _finish = str(_finish)
                                                msg["finish_reason"] = _finish
                                                if _finish == "length":
                                                    logger.warning(
                                                        "LLM response truncated by max_tokens (caller=%s, phase=%s)",
                                                        caller, phase,
                                                    )
                                request_ms = (_time.monotonic() - attempt_started) * 1000
                                break
                            except httpx.TransportError as exc:
                                request_ms = (_time.monotonic() - attempt_started) * 1000
                                if latency_enabled:
                                    _record_latency_faf({
                                    "call_id": call_id, "session_id": session_id,
                                    "round_id": round_id, "caller": caller, "phase": phase,
                                    "model_type": model_type,
                                    "candidate_id": candidate.get("id"), "model": model,
                                    "endpoint": endpoint,
                                    "candidate_rank": candidate.get("_configured_rank", candidate_position),
                                    "endpoint_rank": (candidate.get("_endpoint_ranks") or {}).get(endpoint, endpoint_position),
                                    "attempt": attempt_number, "outcome": "transport_error",
                                    "error_type": exc.__class__.__name__,
                                    "queue_wait_ms": (attempt_started - _t0) * 1000,
                                    "pre_attempt_wait_ms": (attempt_started - _t0) * 1000,
                                    "request_ms": request_ms,
                                    "retry_backoff_ms": retry_backoff_ms,
                                    "total_call_ms": (_time.monotonic() - _t0) * 1000,
                                    "fallback_used": bool(
                                        failed_primary_model
                                        and int(candidate.get("_configured_rank") or 0) > 0
                                    ),
                                    "client_pool_reused": client_pool_reused,
                                    "connection_pool_key": connection_pool_key,
                                    **request_diagnostics,
                                    })
                                # Restarting a stream after visible deltas would
                                # duplicate text in the UI. Only retry before the
                                # first stream event reaches the caller.
                                if stream_event_emitted or network_retries >= NETWORK_RETRY_LIMIT:
                                    raise
                                network_retries += 1
                                delay = _NETWORK_RETRY_BASE_DELAY_SECONDS
                                retry_backoff_ms += delay * 1000
                                logger.warning(
                                    "call_llm transient network failure; retrying "
                                    "[caller=%s phase=%s model=%s endpoint=%s retry=%d/%d delay=%.1fs]: %s",
                                    caller,
                                    phase,
                                    model,
                                    endpoint,
                                    network_retries,
                                    NETWORK_RETRY_LIMIT,
                                    delay,
                                    _format_httpx_error(exc),
                                )
                                if model_type == "primary" and session_id and round_id:
                                    await _publish_model_retry_event(
                                        session_id=session_id,
                                        round_id=round_id,
                                        model=model,
                                        retry_count=network_retries,
                                        retry_limit=NETWORK_RETRY_LIMIT,
                                    )
                                await asyncio.sleep(delay)
                            except httpx.HTTPStatusError as exc:
                                request_ms = (_time.monotonic() - attempt_started) * 1000
                                error_body, error_body_truncated = (
                                    httpx_error_body_for_persistence(exc)
                                )
                                if latency_enabled:
                                    _record_latency_faf({
                                    "call_id": call_id, "session_id": session_id,
                                    "round_id": round_id, "caller": caller, "phase": phase,
                                    "model_type": model_type,
                                    "candidate_id": candidate.get("id"), "model": model,
                                    "endpoint": endpoint,
                                    "candidate_rank": candidate.get("_configured_rank", candidate_position),
                                    "endpoint_rank": (candidate.get("_endpoint_ranks") or {}).get(endpoint, endpoint_position),
                                    "attempt": attempt_number, "outcome": "http_error",
                                    "status_code": exc.response.status_code,
                                    "error_type": exc.__class__.__name__,
                                    "error_body": error_body,
                                    "error_body_truncated": error_body_truncated,
                                    "queue_wait_ms": (attempt_started - _t0) * 1000,
                                    "pre_attempt_wait_ms": (attempt_started - _t0) * 1000,
                                    "request_ms": request_ms,
                                    "retry_backoff_ms": retry_backoff_ms,
                                    "total_call_ms": (_time.monotonic() - _t0) * 1000,
                                    "fallback_used": bool(
                                        failed_primary_model
                                        and int(candidate.get("_configured_rank") or 0) > 0
                                    ),
                                    "client_pool_reused": client_pool_reused,
                                    "connection_pool_key": connection_pool_key,
                                    **request_diagnostics,
                                    })
                                # Transient upstream 5xx (incl. non-standard overload
                                # codes like 550 / 529): back off and retry the same
                                # endpoint before rotating. A 4xx is a real client
                                # error, so let it propagate. Never retry once stream
                                # deltas have already reached the caller.
                                status = exc.response.status_code
                                if (
                                    status < 500
                                    or stream_event_emitted
                                    or server_error_retries >= SERVER_ERROR_RETRY_LIMIT
                                ):
                                    raise
                                server_error_retries += 1
                                delay = _SERVER_ERROR_RETRY_BASE_DELAY_SECONDS
                                retry_backoff_ms += delay * 1000
                                logger.warning(
                                    "call_llm transient upstream error; retrying "
                                    "[caller=%s phase=%s model=%s endpoint=%s status=%d retry=%d/%d delay=%.1fs]",
                                    caller,
                                    phase,
                                    model,
                                    endpoint,
                                    status,
                                    server_error_retries,
                                    SERVER_ERROR_RETRY_LIMIT,
                                    delay,
                                )
                                if model_type == "primary" and session_id and round_id:
                                    await _publish_model_retry_event(
                                        session_id=session_id,
                                        round_id=round_id,
                                        model=model,
                                        retry_count=server_error_retries,
                                        retry_limit=SERVER_ERROR_RETRY_LIMIT,
                                    )
                                await asyncio.sleep(delay)

                        if _is_minimax_model(model):
                            msg = _normalize_minimax_message(msg)
                        msg = _normalize_tool_call_protocol(msg, tools)
                        msg.setdefault("role", "assistant")
                        msg.setdefault("content", "")
                        if msg.get("usage"):
                            msg["usage"]["model"] = model

                        duration_ms = round((_time.monotonic() - _t0) * 1000)
                        ttft_ms = stream_timing.get("ttft_ms") if stream else None
                        response_headers_ms = (
                            stream_timing.get("response_headers_ms") if stream else None
                        )
                        first_token_after_headers_ms = (
                            max(0.0, ttft_ms - response_headers_ms)
                            if ttft_ms is not None and response_headers_ms is not None
                            else None
                        )
                        generation_ms = max(0.0, request_ms - ttft_ms) if ttft_ms is not None else None
                        usage = msg.get("usage") or {}
                        completion_tokens = int(usage.get("completion_tokens") or 0)
                        tokens_per_second = (
                            completion_tokens / (generation_ms / 1000)
                            if generation_ms and completion_tokens > 0
                            else None
                        )

                        success_latency_event = None
                        if latency_enabled:
                            success_latency_event = {
                            "call_id": call_id, "session_id": session_id,
                            "round_id": round_id, "caller": caller, "phase": phase,
                            "model_type": model_type,
                            "candidate_id": candidate.get("id"), "model": model,
                            "endpoint": endpoint,
                            "candidate_rank": candidate.get("_configured_rank", candidate_position),
                            "endpoint_rank": (candidate.get("_endpoint_ranks") or {}).get(endpoint, endpoint_position),
                            "attempt": attempt_number, "outcome": "success", "status_code": 200,
                            "queue_wait_ms": (attempt_started - _t0) * 1000,
                            "pre_attempt_wait_ms": (attempt_started - _t0) * 1000,
                            "request_ms": request_ms,
                            "response_headers_ms": response_headers_ms,
                            "ttft_ms": ttft_ms,
                            "first_token_after_headers_ms": first_token_after_headers_ms,
                            "generation_ms": generation_ms,
                            "retry_backoff_ms": retry_backoff_ms,
                            "total_call_ms": duration_ms,
                            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                            "completion_tokens": completion_tokens,
                            "output_tokens_per_second": tokens_per_second,
                            "fallback_used": bool(
                                failed_primary_model
                                and int(candidate.get("_configured_rank") or 0) > 0
                            ),
                            "client_pool_reused": client_pool_reused,
                            "connection_pool_key": connection_pool_key,
                            "prompt_cache_hit_tokens": int(
                                usage.get("prompt_cache_hit_tokens") or 0
                            ),
                            "prompt_cache_miss_tokens": int(
                                usage.get("prompt_cache_miss_tokens") or 0
                            ),
                            **request_diagnostics,
                            }

                        _clear_candidate_cooldown(
                            _candidate_key(candidate, session_id)
                        )
                        if candidates is None or candidate_lease is not None:
                            _remember_success(
                                model_type, candidate, endpoint, session_id
                            )
                        if failed_this_call or skipped_cooling:
                            logger.warning(
                                "call_llm succeeded on %s after %d failed and %d cooled-down candidate(s)%s "
                                "— check the model settings for stale entries",
                                _candidate_label(candidate), len(failed_this_call), len(skipped_cooling),
                                (": " + "; ".join(failed_this_call)) if failed_this_call else "",
                            )

                        # Success — publish events, record usage, return
                        from cyrene.observability import debug as cy_debug

                        if cy_debug.VERBOSE:
                            cy_debug.log_llm_call(caller, phase, messages, tools, msg, duration_ms)

                        if publish_events:
                            await _publish_llm_event(
                                caller, phase, messages, tools, msg, model, duration_ms,
                                provider=provider, session_id=session_id,
                                round_id=round_id, status="completed",
                                prepared_messages=event_messages,
                                prepared_context_trace=event_context_trace,
                            )

                        if record_usage or success_latency_event is not None:
                            _record_success_telemetry_faf(
                                model, msg.get("usage") or {}, duration_ms, caller,
                                round_id=round_id,
                                session_id=session_id,
                                latency_event=success_latency_event,
                                record_usage=record_usage,
                            )

                        if return_text:
                            return msg.get("content", "")
                        msg["model"] = model
                        # Secret-free identity of the candidate that actually
                        # produced this response. Consumers such as the
                        # Workbench Memory Agent must not infer it from a model
                        # name shared by several providers/candidates.
                        msg["_candidate_identity"] = {
                            "candidateId": str(candidate.get("id") or ""),
                            "adapter": str(candidate.get("adapter") or provider),
                            "provider": provider,
                            "model": model,
                            "baseUrl": _public_base_url(candidate.get("base_url") or ""),
                            "endpoint": endpoint,
                            "reasoningEffort": str(candidate.get("reasoning_effort") or "").strip().lower(),
                        }
                        return msg

                    except httpx.HTTPError as exc:
                        candidate_error = _prefer_llm_failure(candidate_error, exc)
                        last_error = _prefer_llm_failure(last_error, exc)
                        if endpoint != endpoints[-1]:
                            continue
                        logger.warning(
                            "call_llm candidate failed [caller=%s phase=%s model=%s endpoint=%s candidate=%s]: %s",
                            caller, phase, model, endpoint, candidate.get("id"), _format_httpx_error(exc),
                        )

                if candidate_error:
                    # All endpoints for this candidate failed — cool it down and
                    # try the next one. The error is preserved in last_error and
                    # re-raised only after all candidates are exhausted.
                    _set_candidate_cooldown(
                        _candidate_key(candidate, session_id)
                    )
                    failed_this_call.append(
                        f"{_candidate_label(candidate)}: {_format_httpx_error(candidate_error)}"
                    )
                    if int(candidate.get("_configured_rank") or 0) == 0:
                        failed_primary_model = model
                    continue

            except Exception as exc:
                should_cooldown = True
                if provider == "codex_oauth":
                    from cyrene.model_runtime.codex_provider import (
                        codex_availability_error,
                        codex_error_should_cooldown,
                    )

                    normalized_error = codex_availability_error(exc)
                    if normalized_error is not None:
                        exc = normalized_error
                    should_cooldown = codex_error_should_cooldown(exc)
                last_error = _prefer_llm_failure(last_error, exc)
                if should_cooldown:
                    _set_candidate_cooldown(
                        _candidate_key(candidate, session_id)
                    )
                failed_this_call.append(f"{_candidate_label(candidate)}: {exc.__class__.__name__}: {exc}")
                if int(candidate.get("_configured_rank") or 0) == 0:
                    failed_primary_model = str(candidate.get("model") or "")
                failure_kind = str(getattr(exc, "kind", "") or "")
                if (
                    provider == "codex_oauth"
                    and failure_kind
                    and _claim_model_availability_notice(
                        session_id=session_id,
                        round_id=round_id,
                        failed_model=str(candidate.get("model") or ""),
                        failure_kind=failure_kind,
                    )
                ):
                    await _publish_codex_availability_event(
                        session_id=session_id,
                        round_id=round_id,
                        model=str(candidate.get("model") or ""),
                        failure_kind=failure_kind,
                    )
                if model_type == "vision" and _looks_like_vision_capability_error(exc):
                    continue
                continue
            finally:
                if is_secondary and max_conc > 0:
                    _secondary_in_flight -= 1

        if last_error:
            logger.exception(
                "call_llm all candidates exhausted [caller=%s phase=%s]: %s",
                caller, phase, _format_httpx_error(last_error),
            )
            raise last_error
    finally:
        # The shared client belongs to the process/loop pool and is closed by
        # runtime_lifecycle.shutdown_background_work, not after each request.
        pass
    return ""


# ---------------------------------------------------------------------------
# Streaming handler
# ---------------------------------------------------------------------------

# Textual tool-call markers can be streamed as ordinary content deltas. DSML
# blocks are normalized into structured calls after the stream; legacy
# ``<tool_call>`` blocks are invalid but must still never leak into the UI.
# `_DsmlStreamFilter` withholds both forms while the caller keeps raw text for
# normalization and terminal validation.
_DSML_STREAM_OPENERS = (
    "<｜｜DSML｜｜tool_calls>",
    "<||DSML||tool_calls>",
    "<tool_call>",
)
_DSML_STREAM_CLOSERS = (
    "</｜｜DSML｜｜tool_calls>",
    "</||DSML||tool_calls>",
    "</tool_call>",
)
_DSML_STREAM_MAX_OPENER = max(len(opener) for opener in _DSML_STREAM_OPENERS)


def _first_marker(text: str, markers: tuple[str, ...]) -> tuple[int, int]:
    """Earliest (index, length) of any marker in ``text``; (-1, 0) if none."""
    best_index = -1
    best_len = 0
    for marker in markers:
        index = text.find(marker)
        if index >= 0 and (best_index < 0 or index < best_index):
            best_index, best_len = index, len(marker)
    return best_index, best_len


def _trailing_marker_prefix(text: str, markers: tuple[str, ...]) -> int:
    """Length of the longest suffix of ``text`` that is a prefix of any marker."""
    limit = min(len(text), _DSML_STREAM_MAX_OPENER)
    for k in range(limit, 0, -1):
        suffix = text[-k:]
        if any(marker.startswith(suffix) for marker in markers):
            return k
    return 0


class _DsmlStreamFilter:
    """Strip textual tool-call blocks from a forwarded delta stream incrementally."""

    def __init__(self) -> None:
        self._buf = ""
        self._suppressing = False
        self._emitted: list[str] = []

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self._buf += text
        out: list[str] = []
        while self._buf:
            if self._suppressing:
                close_index, close_len = _first_marker(self._buf, _DSML_STREAM_CLOSERS)
                if close_index < 0:
                    # Still inside the block — drop everything but retain a
                    # possible partial closer at the tail for the next chunk.
                    keep = _trailing_marker_prefix(self._buf, _DSML_STREAM_CLOSERS)
                    self._buf = self._buf[len(self._buf) - keep:] if keep else ""
                    break
                self._buf = self._buf[close_index + close_len:]
                self._suppressing = False
                continue
            open_index, open_len = _first_marker(self._buf, _DSML_STREAM_OPENERS)
            if open_index < 0:
                keep = _trailing_marker_prefix(self._buf, _DSML_STREAM_OPENERS)
                emit_upto = len(self._buf) - keep
                if emit_upto > 0:
                    out.append(self._buf[:emit_upto])
                self._buf = self._buf[emit_upto:]
                break
            if open_index > 0:
                out.append(self._buf[:open_index])
            self._buf = self._buf[open_index + open_len:]
            self._suppressing = True
        result = "".join(out)
        if result:
            self._emitted.append(result)
        return result

    def flush(self) -> str:
        # A held buffer is, by construction, only ever a partial opener prefix
        # (ambiguous tail). If the stream ended there it was an incomplete DSML
        # opener, so dropping it is correct; emit only genuine leftover text.
        out = ""
        if not self._suppressing and self._buf:
            if _trailing_marker_prefix(self._buf, _DSML_STREAM_OPENERS) != len(self._buf):
                out = self._buf
        self._buf = ""
        if out:
            self._emitted.append(out)
        return out

    def emitted(self) -> str:
        return "".join(self._emitted)


class _ThinkTagStreamFilter:
    """Split legacy <think> wrappers across arbitrary stream boundaries."""

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._thinking = False

    @staticmethod
    def _partial_marker_length(text: str, marker: str) -> int:
        lowered = text.lower()
        for size in range(min(len(text), len(marker) - 1), 0, -1):
            if marker.startswith(lowered[-size:]):
                return size
        return 0

    def feed(self, text: str) -> tuple[str, str]:
        if text:
            self._buf += text
        visible: list[str] = []
        reasoning: list[str] = []
        while self._buf:
            marker = self._CLOSE if self._thinking else self._OPEN
            index = self._buf.lower().find(marker)
            target = reasoning if self._thinking else visible
            if index >= 0:
                if index:
                    target.append(self._buf[:index])
                self._buf = self._buf[index + len(marker):]
                self._thinking = not self._thinking
                continue
            keep = self._partial_marker_length(self._buf, marker)
            emit_upto = len(self._buf) - keep
            if emit_upto:
                target.append(self._buf[:emit_upto])
            self._buf = self._buf[emit_upto:]
            break
        return "".join(visible), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        if self._thinking:
            result = ("", self._buf)
        else:
            result = (self._buf, "")
        self._buf = ""
        return result


def _accumulate_tool_call_deltas(
    deltas: Any, fragments: dict[int, dict[str, Any]]
) -> None:
    """Merge OpenAI streamed ``delta.tool_calls`` fragments by index."""
    if isinstance(deltas, dict):
        deltas = [deltas]
    if not isinstance(deltas, list):
        return
    for delta in deltas:
        if not isinstance(delta, dict):
            continue
        index = delta.get("index")
        if not isinstance(index, int):
            index = len(fragments)
        fragment = fragments.setdefault(
            index, {"id": None, "type": "function", "name": "", "arguments": ""}
        )
        if delta.get("id"):
            fragment["id"] = delta["id"]
        if delta.get("type"):
            fragment["type"] = delta["type"]
        function = delta.get("function")
        if isinstance(function, dict):
            if function.get("name"):
                fragment["name"] = function["name"]
            if isinstance(function.get("arguments"), str):
                fragment["arguments"] += function["arguments"]
            elif isinstance(function.get("arguments"), dict):
                fragment["arguments"] += json.dumps(
                    function["arguments"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )


def _finalize_tool_call_fragments(
    fragments: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for index in sorted(fragments):
        fragment = fragments[index]
        name = str(fragment.get("name") or "").strip()
        if not name:
            continue
        calls.append({
            "index": len(calls),
            "id": fragment.get("id") or f"call_stream_{uuid.uuid4().hex[:16]}",
            "type": fragment.get("type") or "function",
            "function": {
                "name": name,
                "arguments": fragment.get("arguments") or "{}",
            },
        })
    return calls


async def _handle_stream(
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    stream_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    timing: dict[str, float] | None = None,
    *,
    first_event_timeout: float | None = None,
) -> dict[str, Any]:
    accumulated: list[str] = []  # visible content, before DSML normalization
    reasoning_parts: list[str] = []
    saw_reasoning_content = False
    tool_call_fragments: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    finished_reason: str | None = None
    started = False
    reasoning_started = False
    is_minimax = _is_minimax_model(str(payload.get("model") or ""))
    content_snapshot = ""
    reasoning_snapshot = ""
    reasoning_details: Any = None
    saw_split_reasoning = False
    dsml_filter = _DsmlStreamFilter()
    think_filter = _ThinkTagStreamFilter()
    request_started = _time.monotonic()
    first_upstream_line_seen = False

    async def _forward(text: str) -> None:
        nonlocal started
        if not text:
            return
        accumulated.append(text)
        if not started and stream_callback:
            await stream_callback({"type": "reply_start"})
            started = True
        if stream_callback:
            filtered = dsml_filter.feed(text)
            if filtered:
                await stream_callback({"type": "reply_delta", "delta": filtered})
        else:
            dsml_filter.feed(text)

    async def _forward_reasoning(text: str) -> None:
        nonlocal reasoning_started
        if not text:
            return
        reasoning_parts.append(text)
        if stream_callback and not reasoning_started:
            await stream_callback({"type": "reasoning_start"})
            reasoning_started = True
        if stream_callback:
            await stream_callback({"type": "reasoning_delta", "delta": text})

    def _snapshot_delta(previous: str, current: str) -> tuple[str, str]:
        if not current:
            return "", previous
        if current.startswith(previous):
            return current[len(previous):], current
        if previous.startswith(current):
            return "", previous
        # Some OpenAI-compatible gateways convert MiniMax snapshots back to
        # ordinary deltas. Accept both forms without duplicating snapshots.
        return current, previous + current

    try:
        async with asyncio.timeout(first_event_timeout) as first_event_guard:
            async with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
                if timing is not None:
                    timing["response_headers_ms"] = (_time.monotonic() - request_started) * 1000
                if resp.status_code != 200:
                    # ``httpx`` does not expose ``response.text`` for a streaming
                    # response until the body has been consumed.  Preserve provider
                    # validation details (for example DeepSeek's tool-continuation
                    # reason) before raising so shared diagnostics can report the real
                    # 4xx/5xx cause instead of only "Bad Request".
                    try:
                        await resp.aread()
                    except httpx.HTTPError:
                        pass
                    resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not first_upstream_line_seen:
                        first_upstream_line_seen = True
                        # HTTPX now owns the longer per-read idle deadline.  The
                        # initial watchdog must not cap the rest of the stream.
                        first_event_guard.reschedule(None)
                    line = str(raw_line or "").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if not line:
                        continue
                    if line == "[DONE]":
                        break
                    if timing is not None and "ttft_ms" not in timing:
                        timing["ttft_ms"] = (_time.monotonic() - request_started) * 1000
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data.get("usage"), dict):
                        usage = data["usage"]
                    for choice in data.get("choices") or []:
                        finish = choice.get("finish_reason")
                        if finish:
                            finished_reason = str(finish)
                        delta = choice.get("delta") or {}
                        if "reasoning_content" in delta:
                            saw_reasoning_content = True
                            rc = delta.get("reasoning_content")
                            if isinstance(rc, str):
                                if is_minimax:
                                    reasoning_delta, reasoning_snapshot = _snapshot_delta(
                                        reasoning_snapshot,
                                        rc,
                                    )
                                    if rc:
                                        saw_split_reasoning = True
                                    await _forward_reasoning(reasoning_delta)
                                else:
                                    await _forward_reasoning(rc)
                        details = delta.get("reasoning_details")
                        if details:
                            reasoning_details = details
                            current_reasoning = _reasoning_details_text(details)
                            reasoning_delta, reasoning_snapshot = _snapshot_delta(
                                reasoning_snapshot,
                                current_reasoning,
                            )
                            if current_reasoning:
                                saw_split_reasoning = True
                            await _forward_reasoning(reasoning_delta)
                        delta_calls = delta.get("tool_calls")
                        if delta_calls is None:
                            legacy_function = delta.get("function_call")
                            singular_call = delta.get("tool_call")
                            if isinstance(legacy_function, dict):
                                delta_calls = {
                                    "index": 0,
                                    "function": legacy_function,
                                }
                            elif isinstance(singular_call, dict):
                                delta_calls = (
                                    singular_call
                                    if isinstance(singular_call.get("function"), dict)
                                    else {
                                        "index": singular_call.get("index", 0),
                                        "id": singular_call.get("id"),
                                        "function": singular_call,
                                    }
                                )
                        _accumulate_tool_call_deltas(
                            delta_calls,
                            tool_call_fragments,
                        )
                        text = _extract_stream_delta_text(delta)
                        if not text:
                            continue
                        if is_minimax:
                            text, content_snapshot = _snapshot_delta(content_snapshot, text)
                            visible, embedded_reasoning = think_filter.feed(text)
                            if embedded_reasoning and not saw_split_reasoning:
                                await _forward_reasoning(embedded_reasoning)
                            await _forward(visible)
                        else:
                            await _forward(text)
    except TimeoutError as exc:
        if first_event_timeout is None or first_upstream_line_seen:
            raise
        raise httpx.ReadTimeout(
            "MiniMax stream produced no upstream data before the initial "
            f"{float(first_event_timeout):g}-second timeout",
            request=httpx.Request("POST", endpoint),
        ) from exc
    if is_minimax:
        visible_tail, reasoning_tail = think_filter.flush()
        if reasoning_tail and not saw_split_reasoning:
            await _forward_reasoning(reasoning_tail)
        await _forward(visible_tail)
    filtered_tail = dsml_filter.flush()
    if filtered_tail and stream_callback:
        await stream_callback({"type": "reply_delta", "delta": filtered_tail})

    if reasoning_started and stream_callback:
        await stream_callback({
            "type": "reasoning_done",
            "response": "".join(reasoning_parts),
        })

    full_text = "".join(accumulated)
    if not started and stream_callback:
        await stream_callback({"type": "reply_start"})
    if stream_callback:
        # The UI accumulates filtered deltas, so the final payload must match
        # what was forwarded — never the raw text (which may carry DSML markup).
        await stream_callback({"type": "reply_done", "response": dsml_filter.emitted()})

    msg: dict[str, Any] = {"role": "assistant", "content": full_text}
    if saw_reasoning_content or reasoning_parts:
        msg["reasoning_content"] = "".join(reasoning_parts)
        if is_minimax:
            complete_reasoning = msg["reasoning_content"]
            if _reasoning_details_text(reasoning_details) == complete_reasoning:
                msg["reasoning_details"] = reasoning_details
            else:
                msg["reasoning_details"] = [
                    {"type": "reasoning.text", "text": complete_reasoning}
                ]
    tool_calls = _finalize_tool_call_fragments(tool_call_fragments)
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if finished_reason:
        msg["finish_reason"] = finished_reason
        if finished_reason == "length":
            logger.warning("LLM stream truncated by max_tokens (finish_reason=length)")
    msg["usage"] = _normalized_usage(usage, payload.get("messages", []), msg)
    return msg
