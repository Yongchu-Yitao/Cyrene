"""Unified LLM calling — candidates, streaming, tools, thinking, token recording.

Replaces the independent implementations previously scattered across agent.py,
search.py, scheduler.py, attachments.py, and onboarding.py.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time as _time
import uuid
import weakref
from typing import Any, Callable, Awaitable

import httpx

from cyrene.model_runtime.errors import format_httpx_error as _format_httpx_error
from cyrene.config import (
    DB_PATH,
    DEFAULT_OPENAI_BASE_URL,
    strip_wrapping_quotes,
)
from cyrene.observability.context_trace import strip_context_metadata, summarize_context_trace
from cyrene.runtime.config_store import effective_ctx_limit_for_model
from cyrene.runtime.settings_store import (
    get as get_setting,
    get_models,
    get_secondary_model,
    get_vision_models,
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
    asyncio.AbstractEventLoop, dict[float, tuple[Any, httpx.AsyncClient]]
] = weakref.WeakKeyDictionary()
_HTTP_MAX_CONNECTIONS = 40
_HTTP_MAX_KEEPALIVE_CONNECTIONS = 20
_LAST_SUCCESS_SETTING = "llm_last_success_endpoints"
_SESSION_AFFINITY_PREFIX = "session:"
_MAX_SESSION_AFFINITIES = 2048
_last_success_cache: dict[str, dict[str, str]] | None = None


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


def _get_http_client(timeout: float) -> tuple[httpx.AsyncClient, str, bool]:
    loop = asyncio.get_running_loop()
    timeout_key = float(timeout)
    per_loop = _http_clients.setdefault(loop, {})
    factory = httpx.AsyncClient
    existing = per_loop.get(timeout_key)
    if existing is not None and existing[0] is factory:
        return existing[1], f"loop:{id(loop)}:timeout:{timeout_key:g}", True
    transport = httpx.AsyncHTTPTransport(
        retries=0,
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
    per_loop[timeout_key] = (factory, client)
    return client, f"loop:{id(loop)}:timeout:{timeout_key:g}", False


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


def _prioritize_last_success(
    candidates: list[dict[str, Any]], model_type: str, session_id: str = ""
) -> list[dict[str, Any]]:
    affinity_key = _session_affinity_key(model_type, session_id)
    affinity = (
        _last_success_map().get(affinity_key) or {}
        if affinity_key
        else {}
    )
    prepared: list[dict[str, Any]] = []
    for configured_rank, original in enumerate(candidates):
        candidate = dict(original)
        candidate["_configured_rank"] = configured_rank
        endpoints = list(candidate.get("endpoints") or [])
        candidate["_endpoint_ranks"] = {endpoint: rank for rank, endpoint in enumerate(endpoints)}
        if (
            str(candidate.get("id") or "") == str(affinity.get("candidate_id") or "")
            and str(candidate.get("model") or "") == str(affinity.get("model") or "")
            and _base_root(candidate.get("base_url") or "") == _base_root(affinity.get("base_url") or "")
        ):
            preferred_endpoint = str(affinity.get("endpoint") or "")
            if preferred_endpoint in endpoints:
                endpoints.remove(preferred_endpoint)
                endpoints.insert(0, preferred_endpoint)
        candidate["endpoints"] = endpoints
        prepared.append(candidate)
    preferred_id = str(affinity.get("candidate_id") or "")
    preferred_model = str(affinity.get("model") or "")
    preferred_root = _base_root(affinity.get("base_url") or "")
    prepared.sort(
        key=lambda candidate: 0
        if (
            str(candidate.get("id") or "") == preferred_id
            and str(candidate.get("model") or "") == preferred_model
            and _base_root(candidate.get("base_url") or "") == preferred_root
        )
        else 1
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
_candidate_cooldowns: dict[tuple[str, str, str], float] = {}

# A Workbench round can call the LLM several times (decision, tool rounds,
# wrap-up) while the configured primary remains in cooldown.  Remember the
# model transition already surfaced for that round so one outage produces one
# user-facing notice instead of one notice per internal LLM call.
_MAX_FALLBACK_NOTICE_KEYS = 4096
_published_fallback_notices: dict[tuple[str, str, str, str], None] = {}

# httpx 连接超时与读超时分开：对不可达主机快速失败，而不是吃满整个调用超时。
_CONNECT_TIMEOUT_SECONDS = 5.0

# A model request may be dropped before the provider sends response headers.
# Retry transport failures locally before surfacing them to the user. HTTP
# responses (including 4xx/5xx) are deliberately excluded from this budget.
NETWORK_RETRY_LIMIT = 3
_NETWORK_RETRY_BASE_DELAY_SECONDS = 0.5
# Bounded same-endpoint retry for transient upstream 5xx (incl. non-standard
# overload codes like 550 / 529) before rotating to the next endpoint/candidate.
# 4xx is a real client error and is never retried here.
SERVER_ERROR_RETRY_LIMIT = 2
_SERVER_ERROR_RETRY_BASE_DELAY_SECONDS = 1.0


def _candidate_key(
    candidate: dict[str, Any], session_id: str = ""
) -> tuple[str, str, str]:
    return (
        str(session_id or "").strip(),
        str(candidate.get("model") or ""),
        str(candidate.get("base_url") or ""),
    )


def _candidate_cooling(key: tuple[str, str, str]) -> bool:
    return _candidate_cooldowns.get(key, 0.0) > _time.monotonic()


def _set_candidate_cooldown(key: tuple[str, str, str]) -> None:
    _candidate_cooldowns[key] = _time.monotonic() + _CANDIDATE_COOLDOWN_SECONDS


def _clear_candidate_cooldown(key: tuple[str, str, str]) -> None:
    _candidate_cooldowns.pop(key, None)


def _claim_model_fallback_notice(
    *,
    session_id: str,
    round_id: str,
    failed_model: str,
    fallback_model: str,
) -> bool:
    """Claim one fallback notice per model transition in a runtime round."""
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
        str(fallback_model or "").strip(),
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


def _normalized_llm_endpoints(base_url: str) -> list[str]:
    normalized_base = str(base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/") or DEFAULT_OPENAI_BASE_URL
    endpoints = [f"{normalized_base}/chat/completions"]
    if not normalized_base.endswith("/v1"):
        endpoints.append(f"{normalized_base}/v1/chat/completions")
    return list(dict.fromkeys(endpoints))


def _normalized_candidate(raw: dict[str, Any], index: int = 0, *, active_model: str, active_base_url: str, active_api_key: str) -> dict[str, Any]:
    model = str(raw.get("model") or raw.get("name") or raw.get("id") or "").strip()
    if not model:
        model = active_model
    base_url = str(raw.get("base_url") or active_base_url or DEFAULT_OPENAI_BASE_URL).strip() or DEFAULT_OPENAI_BASE_URL
    raw_api_key = strip_wrapping_quotes(str(raw.get("api_key") or "").strip())
    if raw_api_key:
        api_key = raw_api_key
    elif base_url.rstrip("/") == (active_base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/"):
        api_key = active_api_key
    else:
        api_key = ""
    return {
        "id": str(raw.get("id") or f"candidate-{index + 1}").strip() or f"candidate-{index + 1}",
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "endpoints": _normalized_llm_endpoints(base_url),
    }


def _base_root(url: str) -> str:
    """Normalize a base URL for equality checks ("…/v1" 与不带 /v1 视为同端点)."""
    normalized = str(url or "").strip().rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[: -len("/v1")].rstrip("/")
    return normalized.lower()


def _inherit_sibling_keys(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keyless candidates inherit the key of the first same-endpoint candidate
    that has one ("…/v1" 与不带 /v1 视为同端点)。跨提供商不继承；本地端点可以
    始终无 key（请求时不会带 Authorization 头）。"""
    keyed_roots: dict[str, str] = {}
    for candidate in candidates:
        root = _base_root(candidate.get("base_url") or "")
        if candidate.get("api_key") and root not in keyed_roots:
            keyed_roots[root] = candidate["api_key"]
    for candidate in candidates:
        if not candidate.get("api_key"):
            candidate["api_key"] = keyed_roots.get(_base_root(candidate.get("base_url") or ""), "")
    return candidates


def _resolve_llm_candidates() -> list[dict[str, Any]]:
    """模型列表是唯一事实来源：每个候选自带「标识符 + API Key + Base URL」，
    按列表顺序逐个尝试、失败回退下一条。env 里的 OPENAI_* 只是「保存模型设置时
    镜像 models[0]」的派生值，仅用于补全某条目缺失的 base_url/key，本身不作为
    独立候选参与调用。列表为空（从未配置过任何模型）时返回空——调用方会抛出一个
    明确的「未配置模型」错误，而不是用空 key 撞一个必然 401 的默认端点。"""
    active_model = str(os.environ.get("OPENAI_MODEL", "deepseek-chat") or "").strip() or "deepseek-chat"
    active_base_url = str(os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL) or "").strip() or DEFAULT_OPENAI_BASE_URL
    active_api_key = strip_wrapping_quotes(str(os.environ.get("OPENAI_API_KEY", "") or "").strip())

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(get_models() or []):
        candidate = _normalized_candidate(raw, index, active_model=active_model, active_base_url=active_base_url, active_api_key=active_api_key)
        key = (candidate["model"], candidate["base_url"], candidate["api_key"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    return _inherit_sibling_keys(candidates)


def _resolve_secondary_candidates() -> list[dict[str, Any]]:
    secondary = get_secondary_model()
    model = str(secondary.get("model") or "").strip()
    if not model:
        return []
    base_url = str(secondary.get("base_url") or "").strip()
    if not base_url:
        base_url = str(os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL) or "").strip() or DEFAULT_OPENAI_BASE_URL
    api_key = strip_wrapping_quotes(str(secondary.get("api_key") or "").strip())
    if not api_key:
        primary_base = (os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL) or "").strip().rstrip("/") or DEFAULT_OPENAI_BASE_URL.rstrip("/")
        if base_url.rstrip("/") == primary_base:
            api_key = strip_wrapping_quotes(str(os.environ.get("OPENAI_API_KEY", "") or "").strip())
    ctx_limit = int(secondary.get("ctx_limit") or 0)
    max_concurrency = int(secondary.get("max_concurrency") or 0)
    return [{
        "id": "secondary",
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "endpoints": _normalized_llm_endpoints(base_url),
        "ctx_limit": ctx_limit,
        "max_concurrency": max_concurrency,
    }]


def _resolve_vision_candidates() -> list[dict[str, Any]]:
    """Dedicated vision entries first, then the primary chain as fallback.

    A user configures a vision model precisely because it handles images, so it
    must be tried before the primary chat model. Trying a text-only primary
    first (e.g. DeepSeek, which 400s on ``image_url`` content) wastes a failed
    round-trip on *every* image — and serialized over many docs it was enough to
    push startup past Electron's boot timeout. When no vision model is
    configured this degrades to the primary chain alone, so a vision-capable
    primary still works. Same per-entry key semantics as the primary list."""
    active_model = str(os.environ.get("OPENAI_MODEL", "deepseek-chat") or "").strip() or "deepseek-chat"
    active_base_url = str(os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL) or "").strip() or DEFAULT_OPENAI_BASE_URL
    active_api_key = strip_wrapping_quotes(str(os.environ.get("OPENAI_API_KEY", "") or "").strip())

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for index, raw in enumerate(get_vision_models() or []):
        candidate = _normalized_candidate(raw, index, active_model=active_model, active_base_url=active_base_url, active_api_key=active_api_key)
        key = (candidate["model"], candidate["base_url"], candidate["api_key"])
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    for candidate in _resolve_llm_candidates():
        key = (candidate["model"], candidate["base_url"], candidate["api_key"])
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
    # Per-response metadata we attach to the returned message for callers to
    # inspect (e.g. detecting a max_tokens truncation), but which must not be
    # echoed back upstream when the message is replayed in history.
    "finish_reason",
    # Past-turn chain-of-thought must never be echoed back upstream: it bloats the
    # context (accelerating cache-breaking compaction) and DeepSeek's reasoner API
    # rejects inputs that carry reasoning_content. It stays in the stored history
    # for the UI; this strip only applies to the payload sent to the model.
    "reasoning_content",
})


def _strip_internal_fields(message: dict) -> dict:
    """Remove Cyrene-internal fields that must not be sent to the LLM."""
    return {k: v for k, v in message.items() if k not in _INTERNAL_MSG_KEYS}


def _sanitize_messages_for_llm(messages: list[dict]) -> list[dict]:
    """Ensure valid tool_calls/tool message pairing with unique tool_call_ids."""
    import uuid as _uuid

    messages = [_strip_internal_fields(m) for m in strip_context_metadata(messages)]
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
                has_dupes = any(oid in seen_ids for oid in old_ids)

                if has_dupes:
                    new_msg = dict(msg)
                    new_tc_list = []
                    new_ids = []
                    for tc in tc_list:
                        new_tc = dict(tc)
                        new_id = f"call_{_uuid.uuid4().hex[:12]}"
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


# ---------------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------------

def _approx_token_count(text: str) -> int:
    """Estimate token count with CJK-aware heuristic.

    CJK characters average ~1 token each; runs of ASCII word chars
    average ~0.25 tokens/char (4 chars per token); punctuation/other
    are counted individually.
    """
    source = str(text or "")
    if not source.strip():
        return 0
    units = re.findall(r"[一-鿿]|[A-Za-z0-9_]+|[^\s]", source)
    total = 0
    for unit in units:
        if re.fullmatch(r"[A-Za-z0-9_]+", unit):
            total += max(1, (len(unit) + 3) // 4)
        else:
            total += 1
    return total


def approx_token_count(text: str) -> int:
    """Public deterministic text-token estimate used for local budgets."""
    return _approx_token_count(text)


def _message_token_estimate(message: dict[str, Any]) -> int:
    total = 4
    content = message.get("content")
    if isinstance(content, str):
        total += _approx_token_count(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                total += _approx_token_count(block.get("text") or "")
    else:
        total += _approx_token_count(content or "")
    total += _approx_token_count(message.get("role") or "")
    total += _approx_token_count(message.get("reasoning_content") or "")
    for tc in message.get("tool_calls") or []:
        total += _approx_token_count(tc.get("function", {}).get("name") or "")
        total += _approx_token_count(tc.get("function", {}).get("arguments") or "")
    total += _approx_token_count(message.get("tool_call_id") or "")
    return total


def message_token_estimate(message: dict[str, Any]) -> int:
    """Public deterministic message-token estimate used for context budgets."""
    return _message_token_estimate(message)


def _request_token_estimate(messages: list[dict], tools: list | None = None) -> int:
    """Conservative input-token estimate used for per-candidate context gates."""
    total = sum(_message_token_estimate(message) for message in messages)
    if tools:
        total += _approx_token_count(
            json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
        )
    return total


def _candidate_ctx_limit(candidate: dict[str, Any]) -> int:
    """Resolve the candidate's configured window, falling back only if unset."""
    explicit = int(candidate.get("ctx_limit") or 0)
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
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": _sanitize_messages_for_llm(messages),
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    # Constrained JSON mode (OpenAI/DeepSeek `response_format`). Only meaningful
    # without tools — providers reject/ignore it alongside function calling — so
    # callers pass it on tool-less "just emit JSON" rounds.
    if response_format is not None and not tools:
        payload["response_format"] = response_format
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

    if thinking == "auto":
        if "deepseek" in model.lower():
            payload["thinking"] = {"type": "enabled"}
    elif thinking == "enabled":
        payload["thinking"] = {"type": "enabled"}
    elif thinking == "disabled":
        # Keep DeepSeek thinking enabled even for callers that request the
        # legacy "disabled" mode. Other OpenAI-compatible providers may reject
        # this extension, so keep it provider/model-specific.
        if "deepseek" in model.lower():
            payload["thinking"] = {"type": "enabled"}
    return payload


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
    latency_events = [latency_event] if latency_event is not None else []
    if not token_events and not latency_events:
        return
    _bg_token_task(asyncio.create_task(record_llm_telemetry_batch(
        str(DB_PATH),
        token_events=token_events,
        latency_events=latency_events,
    )))


def _record_latency_faf(event: dict[str, Any]) -> None:
    """Persist a request-attempt span without delaying the model loop."""
    from cyrene.runtime.database import record_llm_latency

    _bg_token_task(asyncio.create_task(record_llm_latency(str(DB_PATH), **event)))


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
    session_id: str = "",
    round_id: str = "",
    status: str = "completed",
) -> None:
    from cyrene.observability import debug

    event = {
        "type": "llm_call",
        "caller": caller,
        "phase": phase,
        "model": model,
        "tools": [t.get("function", {}).get("name") for t in (tools or [])],
        "messages": _sanitize_messages_for_llm(messages),
        "context_trace": summarize_context_trace(messages),
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


# ---------------------------------------------------------------------------
# The unified call_llm function
# ---------------------------------------------------------------------------


async def call_llm(
    messages: list[dict],
    *,
    tools: list | None = None,
    model_type: str = "primary",
    candidates: list[dict] | None = None,
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
) -> dict | str:
    """Unified LLM calling entry point.

    Args:
        messages: The conversation history.
        tools: Optional tool definitions (triggers ``tool_choice="auto"``).
        model_type: ``"primary"``, ``"secondary"``, or ``"vision"``.
        candidates: Explicit candidate list (overrides ``model_type``).
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

    resolved = candidates if candidates is not None else _resolve_candidates(model_type)
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
        resolved = [
            {
                **candidate,
                "_configured_rank": index,
                "_endpoint_ranks": {
                    endpoint: rank
                    for rank, endpoint in enumerate(candidate.get("endpoints") or [])
                },
            }
            for index, candidate in enumerate(resolved)
        ]

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
    if resolved and resolved[0].get("id") == "secondary":
        ctx_limit = int(resolved[0].get("ctx_limit") or 0)
        if ctx_limit > 0:
            total_tokens = sum(_message_token_estimate(m) for m in messages)
            if total_tokens > ctx_limit:
                resolved = resolved[1:] if len(resolved) > 1 else _resolve_llm_candidates()

    # Skip candidates that recently failed (dead endpoint / bad key). If that
    # would leave nothing, ignore cooldowns and try the full list anyway.
    available = [
        c for c in resolved
        if not _candidate_cooling(_candidate_key(c, session_id))
    ]
    skipped_cooling = [
        c for c in resolved
        if _candidate_cooling(_candidate_key(c, session_id))
    ]
    if not available:
        available = resolved
        skipped_cooling = []
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
        # Cooldown means the configured primary recently failed; a context
        # rejection means it cannot accept this request.  Both are genuine
        # fallback reasons. Merely promoting a last-success affinity is not.
        failed_primary_model = str(configured_primary.get("model") or "")
    fallback_notice_sent = False
    attempt_number = 0
    retry_backoff_ms = 0.0

    def _candidate_label(c: dict[str, Any]) -> str:
        return f"{c.get('id')}({c.get('model')}@{c.get('base_url')})"

    client, connection_pool_key, client_pool_reused = _get_http_client(timeout)
    try:
        last_error: Exception | None = None

        for candidate_position, candidate in enumerate(available):
            is_secondary = candidate.get("id") == "secondary"
            max_conc = int(candidate.get("max_concurrency") or 0)

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
                model = str(candidate.get("model") or "").strip()
                payload = _build_payload(messages, tools, max_tokens, stream, model, thinking, response_format)

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
                            session_id=session_id, round_id=round_id, status="started",
                        )
                    try:
                        network_retries = 0
                        server_error_retries = 0
                        while True:
                            attempt_number += 1
                            attempt_started = _time.monotonic()
                            stream_timing: dict[str, float] = {}
                            stream_event_emitted = False

                            async def _tracked_stream_callback(event: dict[str, Any]) -> None:
                                nonlocal stream_event_emitted
                                stream_event_emitted = True
                                if stream_callback:
                                    await stream_callback(event)

                            try:
                                if stream:
                                    msg = await _handle_stream(
                                        client,
                                        endpoint,
                                        payload,
                                        headers,
                                        _tracked_stream_callback,
                                        stream_timing,
                                    )
                                else:
                                    resp = await client.post(endpoint, json=payload, headers=headers)
                                    if resp.status_code != 200:
                                        resp.raise_for_status()
                                    data = resp.json()
                                    msg = _message_from_upstream_payload(data)
                                    msg["usage"] = _normalized_usage(data.get("usage"), messages, msg)
                                    _choices = data.get("choices")
                                    if isinstance(_choices, list) and _choices and isinstance(_choices[0], dict):
                                        _finish = _choices[0].get("finish_reason")
                                        if _finish:
                                            msg["finish_reason"] = str(_finish)
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
                                    })
                                # Restarting a stream after visible deltas would
                                # duplicate text in the UI. Only retry before the
                                # first stream event reaches the caller.
                                if stream_event_emitted or network_retries >= NETWORK_RETRY_LIMIT:
                                    raise
                                network_retries += 1
                                delay = _NETWORK_RETRY_BASE_DELAY_SECONDS * (2 ** (network_retries - 1))
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
                                await asyncio.sleep(delay)
                            except httpx.HTTPStatusError as exc:
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
                                    "attempt": attempt_number, "outcome": "http_error",
                                    "status_code": exc.response.status_code,
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
                                delay = _SERVER_ERROR_RETRY_BASE_DELAY_SECONDS * (2 ** (server_error_retries - 1))
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
                                await asyncio.sleep(delay)

                        msg = _normalize_dsml_tool_calls(msg, tools)
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
                            }

                        _clear_candidate_cooldown(
                            _candidate_key(candidate, session_id)
                        )
                        if candidates is None:
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
                                session_id=session_id, round_id=round_id, status="completed",
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
                        return msg

                    except httpx.HTTPError as exc:
                        candidate_error = exc
                        last_error = exc
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
                last_error = exc
                _set_candidate_cooldown(_candidate_key(candidate, session_id))
                failed_this_call.append(f"{_candidate_label(candidate)}: {exc.__class__.__name__}: {exc}")
                if int(candidate.get("_configured_rank") or 0) == 0:
                    failed_primary_model = str(candidate.get("model") or "")
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

# Textual DSML tool-call markers can be streamed as ordinary content deltas
# (DeepSeek's fallback when it cannot use the structured tool-call channel).
# The block is parsed back into real tool calls *after* the stream completes
# (`_normalize_dsml_tool_calls`), but the raw deltas would otherwise reach the
# UI verbatim mid-stream. `_DsmlStreamFilter` withholds the markup from the
# forwarded stream while the caller keeps the raw text for normalization.
_DSML_STREAM_OPENERS = ("<｜｜DSML｜｜tool_calls>", "<||DSML||tool_calls>")
_DSML_STREAM_CLOSERS = ("</｜｜DSML｜｜tool_calls>", "</||DSML||tool_calls>")
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
    """Strip DSML tool-call blocks from a forwarded delta stream incrementally."""

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


def _accumulate_tool_call_deltas(
    deltas: Any, fragments: dict[int, dict[str, Any]]
) -> None:
    """Merge OpenAI streamed ``delta.tool_calls`` fragments by index."""
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
) -> dict[str, Any]:
    accumulated: list[str] = []  # raw content — kept for tool-call normalization
    reasoning_parts: list[str] = []
    tool_call_fragments: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    started = False
    reasoning_started = False
    dsml_filter = _DsmlStreamFilter()
    request_started = _time.monotonic()

    async def _forward(text: str) -> None:
        nonlocal started
        if not text:
            return
        if not started and stream_callback:
            await stream_callback({"type": "reply_start"})
            started = True
        if stream_callback:
            await stream_callback({"type": "reply_delta", "delta": text})

    async with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
        if timing is not None:
            timing["response_headers_ms"] = (_time.monotonic() - request_started) * 1000
        if resp.status_code != 200:
            resp.raise_for_status()
        async for raw_line in resp.aiter_lines():
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
                delta = choice.get("delta") or {}
                rc = delta.get("reasoning_content")
                if isinstance(rc, str) and rc.strip():
                    if stream_callback and not reasoning_started:
                        await stream_callback({"type": "reasoning_start"})
                        reasoning_started = True
                    reasoning_parts.append(rc)
                    if stream_callback:
                        await stream_callback({"type": "reasoning_delta", "delta": rc})
                _accumulate_tool_call_deltas(delta.get("tool_calls"), tool_call_fragments)
                text = _extract_stream_delta_text(delta)
                if not text:
                    continue
                accumulated.append(text)
                await _forward(dsml_filter.feed(text))
    await _forward(dsml_filter.flush())

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
    if reasoning_parts:
        msg["reasoning_content"] = "".join(reasoning_parts)
    tool_calls = _finalize_tool_call_fragments(tool_call_fragments)
    if tool_calls:
        msg["tool_calls"] = tool_calls
    msg["usage"] = _normalized_usage(usage, payload.get("messages", []), msg)
    return msg
