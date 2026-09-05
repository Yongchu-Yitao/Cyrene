"""Application queries for Workbench conversation context and live inboxes."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from cyrene.core.plugin import plugin_public_session_snapshot
from cyrene.core.context.projection import (
    context_is_turn,
    project_context_message,
    selected_context_node_ids,
)
from cyrene.localization import localized

logger = logging.getLogger(__name__)


class ConversationNotFoundError(LookupError):
    pass


class ConversationContextUpdateError(ValueError):
    pass


_AGENT_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)
_AGENT_CONTEXT_SEGMENT_KEYS = (
    "compacted",
    "system",
    "user",
    "assistant",
    "tool",
)
_AGENT_INBOX_EVENT_LIMIT = 100


def _agent_current_run_id(nodes: list[Any]) -> str:
    return next(
        (
            str(node.value.get("run_id") or "")
            for node in reversed(nodes)
            if isinstance(node.value, Mapping)
            and node.value.get("role") == "user"
        ),
        "",
    )


def _agent_active_context_nodes(nodes: list[Any]) -> list[Any]:
    current_run_id = _agent_current_run_id(nodes)
    current_context_by_kind = {
        str(node.value.get("context_kind") or node.id): node.id
        for node in nodes
        if isinstance(node.value, Mapping)
        and node.value.get("role") == "context"
        and str(node.value.get("run_id") or "") == current_run_id
    }
    selected = set(current_context_by_kind.values())
    return [node for node in nodes if node.id in selected]


def _agent_path_messages(nodes: list[Any]) -> list[dict[str, Any]]:
    """Project one ContextTree path into the messages sent to the model."""

    messages: list[dict[str, Any]] = []
    current_run_id = _agent_current_run_id(nodes)
    active_context_ids = selected_context_node_ids(nodes, current_run_id)
    for node in nodes:
        value = node.value if isinstance(node.value, Mapping) else {}
        role = str(value.get("role") or "")
        if role in {"context_compaction", "context_reflection"}:
            compacted = value.get("messages")
            if isinstance(compacted, list):
                messages = [
                    dict(message)
                    for message in compacted
                    if isinstance(message, Mapping)
                ]
            continue
        if role in {"system", "user"}:
            messages.append({"role": role, "content": str(value.get("content") or "")})
            continue
        if role == "context":
            if node.id not in active_context_ids:
                continue
            content = str(value.get("content") or "").strip()
            if not content:
                continue
            project_context_message(messages, value)
            continue
        if role == "assistant":
            message: dict[str, Any] = {
                "role": "assistant",
                "content": str(value.get("content") or ""),
            }
            calls = value.get("tool_calls")
            if isinstance(calls, list) and calls:
                message["tool_calls"] = [
                    {
                        "id": str(call.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(call.get("name") or ""),
                            "arguments": json.dumps(
                                call.get("arguments") or {},
                                ensure_ascii=False,
                                default=str,
                            ),
                        },
                    }
                    for call in calls
                    if isinstance(call, Mapping)
                ]
                reasoning_details = value.get("reasoning_details")
                if isinstance(reasoning_details, list) and reasoning_details:
                    message["reasoning_details"] = reasoning_details
            messages.append(message)
            continue
        if role != "tool_results":
            continue
        results = value.get("results")
        for result in results if isinstance(results, list) else ():
            if not isinstance(result, Mapping):
                continue
            messages.append({
                "role": "tool",
                "tool_call_id": str(result.get("call_id") or ""),
                "name": str(result.get("name") or ""),
                "content": json.dumps(
                    {
                        "success": bool(result.get("success")),
                        "value": result.get("value"),
                        "error": str(result.get("error") or ""),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            })
    return messages


def _agent_path_usage(nodes: list[Any]) -> dict[str, int]:
    totals = {key: 0 for key in _AGENT_USAGE_KEYS}

    def integer(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def add_usage(usage: Any) -> None:
        if not isinstance(usage, Mapping):
            return
        prompt_tokens = integer(usage.get("prompt_tokens"))
        completion_tokens = integer(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
        details = details if isinstance(details, Mapping) else {}
        cache_hit_value = next(
            (
                value
                for value in (
                    usage.get("prompt_cache_hit_tokens"),
                    usage.get("cache_hit_tokens"),
                    usage.get("cached_prompt_tokens"),
                    usage.get("cached_tokens"),
                    usage.get("cached_input_tokens"),
                    usage.get("cache_read_input_tokens"),
                    details.get("cached_tokens"),
                )
                if value is not None
            ),
            None,
        )
        cache_miss_value = next(
            (
                value
                for value in (
                    usage.get("prompt_cache_miss_tokens"),
                    usage.get("cache_miss_tokens"),
                    usage.get("cache_creation_input_tokens"),
                )
                if value is not None
            ),
            None,
        )
        cache_hit_tokens = (
            integer(cache_hit_value) if cache_hit_value is not None else 0
        )
        cache_miss_tokens = (
            integer(cache_miss_value)
            if cache_miss_value is not None
            else max(0, prompt_tokens - cache_hit_tokens)
            if cache_hit_value is not None
            else 0
        )
        totals["prompt_tokens"] += prompt_tokens
        totals["completion_tokens"] += completion_tokens
        totals["total_tokens"] += (
            integer(usage.get("total_tokens"))
            or prompt_tokens + completion_tokens
        )
        totals["prompt_cache_hit_tokens"] += cache_hit_tokens
        totals["prompt_cache_miss_tokens"] += cache_miss_tokens

    for node in nodes:
        value = node.value if isinstance(node.value, Mapping) else {}
        add_usage(value.get("usage"))
        auxiliary = value.get("auxiliary_usage")
        for record in auxiliary if isinstance(auxiliary, list) else ():
            if isinstance(record, Mapping):
                add_usage(record.get("usage"))
    return totals


def _agent_context_segments(
    messages: list[dict[str, Any]],
    approx_token_count: Callable[[str], int],
) -> dict[str, int]:
    """Estimate the next model input directly from the Agent message path."""

    segments = {key: 0 for key in _AGENT_CONTEXT_SEGMENT_KEYS}

    def tokens(value: Any) -> int:
        return max(0, int(approx_token_count(str(value or "")) or 0))

    for message in messages:
        role = str(message.get("role") or "")
        base = 4 + tokens(role)
        content = message.get("content")
        if isinstance(content, list):
            content_tokens = sum(
                tokens(block.get("text"))
                for block in content
                if isinstance(block, Mapping) and block.get("type") == "text"
            )
        else:
            content_tokens = tokens(content)

        if message.get("compacted_block"):
            segments["compacted"] += base + content_tokens
            continue
        if role == "user":
            segments["user"] += base + content_tokens
            continue
        if role == "assistant":
            segments["assistant"] += (
                base + content_tokens + tokens(message.get("reasoning_content"))
            )
            for call in message.get("tool_calls") or ():
                function = call.get("function") if isinstance(call, Mapping) else {}
                function = function if isinstance(function, Mapping) else {}
                segments["tool"] += tokens(function.get("name"))
                segments["tool"] += tokens(function.get("arguments"))
            continue
        if role == "tool":
            segments["tool"] += (
                base + content_tokens + tokens(message.get("tool_call_id"))
            )
            continue
        segments["system"] += base + content_tokens
    return segments


def _agent_ephemeral_tokens(
    messages: list[dict[str, Any]],
    ephemeral_context: str,
    approx_token_count: Callable[[str], int],
) -> int:
    """Measure the exact delta added by ``_model_messages`` for this turn."""

    extra = str(ephemeral_context or "").strip()
    if not extra:
        return 0
    user = next(
        (
            message
            for message in reversed(messages)
            if str(message.get("role") or "") == "user"
        ),
        None,
    )
    if user is None:
        return sum(
            _agent_context_segments(
                [{"role": "user", "content": extra}],
                approx_token_count,
            ).values()
        )
    current = str(user.get("content") or "").strip()
    merged = "\n\n".join(part for part in (current, extra) if part)
    return max(
        0,
        int(approx_token_count(merged) or 0)
        - int(approx_token_count(current) or 0),
    )


def _agent_ephemeral_is_mounted(
    state: Mapping[str, Any],
    ephemeral_context: str,
) -> bool:
    """Return whether the durable Hook mount already contains this turn tail."""

    extra = str(ephemeral_context or "").strip()
    if not extra:
        return False
    mounts = state.get("contextMounts")
    return any(
        extra in str(mount.get("content") or "")
        for mount in (mounts if isinstance(mounts, list) else ())
        if isinstance(mount, Mapping)
    )


def _agent_turn_context_layer(
    state: Mapping[str, Any],
    messages: list[dict[str, Any]],
    approx_token_count: Callable[[str], int],
) -> tuple[dict[str, Any] | None, int]:
    mounts = [
        dict(mount)
        for mount in state.get("contextMounts") or ()
        if isinstance(mount, Mapping)
        and context_is_turn({
            "context_lifecycle": mount.get("lifecycle"),
            "context_kind": mount.get("kind"),
            "context_source": mount.get("source"),
        })
        and str(mount.get("content") or "").strip()
    ]
    if not mounts:
        return None, 0
    tail = "\n\n".join(str(mount["content"]).strip() for mount in mounts)
    user = next(
        (
            message
            for message in reversed(messages)
            if str(message.get("role") or "") == "user"
        ),
        None,
    )
    merged = str(user.get("content") or "").strip() if user is not None else tail
    base = merged
    if merged == tail:
        base = ""
    elif merged.endswith("\n\n" + tail):
        base = merged[: -(len(tail) + 2)].rstrip()
    tokens = max(
        0,
        int(approx_token_count(merged) or 0)
        - int(approx_token_count(base) or 0),
    )
    if tokens <= 0:
        return None, 0
    weights = [
        max(1, int(approx_token_count(str(mount["content"])) or 0))
        for mount in mounts
    ]
    total_weight = sum(weights)
    allocations = [tokens * weight // total_weight for weight in weights]
    for index in range(tokens - sum(allocations)):
        allocations[index % len(allocations)] += 1
    blocks = [
        {
            "id": f"context.{mount.get('kind') or 'turn'}",
            "nodeId": str(mount.get("id") or ""),
            "type": "ephemeral",
            "tokens_est": allocation,
            "chars": len(str(mount.get("content") or "")),
            "contextKind": str(mount.get("kind") or "turn_context"),
            "source": str(mount.get("source") or "TurnStart"),
            "reason": str(mount.get("kind") or "turn_context"),
            "lifecycle": str(mount.get("lifecycle") or ""),
            "createdAt": str(mount.get("createdAt") or ""),
            "updatedAt": str(mount.get("updatedAt") or ""),
            "metadata": (
                copy.deepcopy(dict(mount.get("metadata") or {}))
                if isinstance(mount.get("metadata"), Mapping)
                else {}
            ),
        }
        for mount, allocation in zip(mounts, allocations, strict=True)
        if allocation > 0
    ]
    return ({
        "id": "turn_context",
        "label": localized("Turn Context", "每轮上下文"),
        "sublabel": None,
        "blocks": blocks,
        "totalTokens": tokens,
    }, tokens)


def _agent_latest_ephemeral_context(path: list[Any]) -> str:
    latest_user = next(
        (
            node
            for node in reversed(path)
            if isinstance(node.value, Mapping)
            and node.value.get("role") == "user"
        ),
        None,
    )
    if latest_user is None:
        return ""
    current_run_id = str(latest_user.value.get("run_id") or "")
    turn_user = next(
        (
            node
            for node in reversed(path)
            if isinstance(node.value, Mapping)
            and node.value.get("role") == "user"
            and str(node.value.get("run_id") or "") == current_run_id
            and isinstance(node.value.get("metadata"), Mapping)
            and "ephemeral_context" in node.value["metadata"]
        ),
        None,
    )
    return (
        str(turn_user.value["metadata"].get("ephemeral_context") or "")
        if turn_user is not None
        else ""
    )


def _active_plugin_owner(name: str) -> tuple[str | None, str] | None:
    """Resolve one executed direct tool to its live Plugin owner."""

    normalized = str(name or "").strip()
    if not normalized:
        return None
    try:
        from cyrene.core.plugin import application_plugin_scope

        host = application_plugin_scope()
        if host is None:
            return None
        registered = host.registry.registered(normalized)
    except Exception:
        return None
    pack_id = str(registered.pack_id or "").strip() or None
    return pack_id, str(registered.plugin.name or normalized).strip() or normalized


def _agent_path_plugin_usage(
    nodes: list[Any],
    plugin_owner: Callable[[str], tuple[str | None, str] | None] | None = None,
) -> tuple[list[str], list[str]]:
    packs: list[str] = []
    standalone: list[str] = []
    seen_packs: set[str] = set()
    seen_standalone: set[str] = set()
    seen_calls: set[tuple[str, str]] = set()

    def record_result(result: Any, *, owner_id: str) -> None:
        if not isinstance(result, Mapping) or result.get("success") is not True:
            return
        call_id = str(result.get("call_id") or "").strip()
        call_key = (str(owner_id or ""), call_id)
        if call_id and call_key in seen_calls:
            return
        if call_id:
            seen_calls.add(call_key)
        result_name = str(result.get("name") or "").strip()
        toolbox_value = result.get("value")
        if (
            result_name == "toolbox"
            and isinstance(toolbox_value, Mapping)
            and toolbox_value.get("operation") == "invoke"
        ):
            pack_id = str(toolbox_value.get("pack") or "").strip()
            plugin_name = str(toolbox_value.get("name") or "").strip()
        else:
            owner = plugin_owner(result_name) if plugin_owner is not None else None
            if owner is None:
                return
            raw_pack_id, raw_plugin_name = owner
            pack_id = str(raw_pack_id or "").strip()
            plugin_name = str(raw_plugin_name or result_name).strip()
        if not pack_id and not plugin_name:
            return
        if pack_id:
            if pack_id not in seen_packs:
                seen_packs.add(pack_id)
                packs.append(pack_id)
            return
        if plugin_name and plugin_name not in seen_standalone:
            seen_standalone.add(plugin_name)
            standalone.append(plugin_name)

    for node in nodes:
        value = node.value if isinstance(node.value, Mapping) else {}
        effect_results = value.get("effect_results")
        for result in (
            effect_results.values()
            if isinstance(effect_results, Mapping)
            else ()
        ):
            record_result(result, owner_id=node.id)
        if value.get("role") == "tool_results":
            results = value.get("results")
            for result in results if isinstance(results, list) else ():
                record_result(
                    result,
                    owner_id=str(getattr(node, "parent_id", "") or node.id),
                )
    return packs, standalone


def _timeline_tool_result(
    result: Mapping[str, Any],
    plugin_owner: Callable[[str], tuple[str | None, str] | None] | None = None,
) -> dict[str, Any]:
    value = result.get("value")
    invocation = value if isinstance(value, Mapping) else {}
    result_name = str(result.get("name") or "").strip()
    operation = str(invocation.get("operation") or "").strip()
    pack_id = ""
    plugin_name = ""
    if result_name == "toolbox":
        if operation == "invoke":
            pack_id = str(invocation.get("pack") or "").strip()
            plugin_name = str(invocation.get("name") or "").strip()
        else:
            plugin_name = "toolbox"
    else:
        owner = plugin_owner(result_name) if plugin_owner is not None else None
        if owner is not None:
            raw_pack_id, raw_plugin_name = owner
            pack_id = str(raw_pack_id or "").strip()
            plugin_name = str(raw_plugin_name or result_name).strip()
        else:
            plugin_name = result_name
    return {
        "call_id": str(result.get("call_id") or ""),
        "name": result_name,
        "success": bool(result.get("success")),
        "error": str(result.get("error") or ""),
        "operation": operation,
        "plugin_pack": pack_id,
        "plugin_name": plugin_name,
    }


def _timeline_content(role: str, value: Mapping[str, Any]) -> str:
    content = value.get("content")
    if isinstance(content, str):
        return content
    if role == "tool_results":
        source = value.get("results") or []
    elif role in {"context_compaction", "context_reflection"}:
        source = value.get("messages") or []
    else:
        return ""
    return json.dumps(source, ensure_ascii=False, default=str)


def _timeline_technical_details(
    value: Mapping[str, Any],
    plugin_owner: Callable[[str], tuple[str | None, str] | None] | None,
) -> dict[str, Any]:
    technical: dict[str, Any] = {}
    for key in (
        "metadata",
        "run_id",
        "model",
        "model_identity",
        "usage",
        "finish_reason",
        "before_tokens",
        "after_tokens",
        "context_limit",
        "distilled",
    ):
        if key in value:
            technical[key] = copy.deepcopy(value[key])
    calls = value.get("tool_calls")
    if isinstance(calls, list):
        technical["tool_calls"] = [
            {
                "id": str(call.get("id") or ""),
                "name": str(call.get("name") or ""),
                "arguments": copy.deepcopy(call.get("arguments") or {}),
            }
            for call in calls
            if isinstance(call, Mapping)
        ]
    results = value.get("results")
    if isinstance(results, list):
        technical["tool_results"] = [
            _timeline_tool_result(result, plugin_owner)
            for result in results
            if isinstance(result, Mapping)
        ]
    effect_results = value.get("effect_results")
    if isinstance(effect_results, Mapping):
        technical["effect_results"] = [
            _timeline_tool_result(result, plugin_owner)
            for result in effect_results.values()
            if isinstance(result, Mapping)
        ]
    technical["raw_value"] = {
        str(key): copy.deepcopy(item)
        for key, item in value.items()
        if key != "content"
    }
    return technical


def _agent_path_timeline(
    nodes: list[Any],
    plugin_owner: Callable[[str], tuple[str | None, str] | None] | None = None,
) -> list[dict[str, Any]]:
    """Project the active path into chronological, content-safe audit records."""

    records: list[dict[str, Any]] = []
    for order, node in enumerate(nodes):
        value = node.value if isinstance(node.value, Mapping) else {}
        role = str(value.get("role") or "").strip()
        if not role:
            continue
        records.append({
            "id": str(node.id),
            "parentId": str(node.parent_id or ""),
            "role": role,
            "contextKind": str(value.get("context_kind") or ""),
            "source": str(value.get("context_source") or "context_tree"),
            "lifecycle": str(value.get("context_lifecycle") or ""),
            "createdAt": node.created_at.isoformat(),
            "updatedAt": node.updated_at.isoformat(),
            "order": order,
            "technical": _timeline_technical_details(value, plugin_owner),
            "_content": _timeline_content(role, value),
            "_contentFormat": (
                "json"
                if role in {
                    "tool_results",
                    "context_compaction",
                    "context_reflection",
                }
                else "text"
            ),
        })
    return records


def _agent_effective_timeline(
    timeline: Sequence[Mapping[str, Any]],
    context_mounts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Present context nodes in the same structural order as model messages.

    Session-scoped mounts are durable snapshots under every user turn, but the
    model projection selects only the current turn's stable snapshot and merges
    it into the leading system message.  Keep that current snapshot in mount
    order immediately after the system marker, while leaving turn-scoped mounts
    beside their original user messages.  This changes presentation only; the
    model projection remains owned by ``_agent_path_messages``.
    """

    records = [copy.deepcopy(dict(item)) for item in timeline]
    stable_mount_ids = [
        str(mount.get("id") or "")
        for mount in context_mounts
        if str(mount.get("id") or "")
        and not context_is_turn({
            "context_lifecycle": mount.get("lifecycle"),
            "context_kind": mount.get("kind"),
            "context_source": mount.get("source"),
        })
    ]
    stable_id_set = set(stable_mount_ids)
    stable_by_id = {
        str(item.get("id") or ""): item
        for item in records
        if str(item.get("id") or "") in stable_id_set
    }
    stable_records = [
        stable_by_id[item_id]
        for item_id in stable_mount_ids
        if item_id in stable_by_id
    ]
    remaining = [
        item
        for item in records
        if not (
            str(item.get("role") or "") == "context"
            and not context_is_turn({
                "context_lifecycle": item.get("lifecycle"),
                "context_kind": item.get("contextKind"),
                "context_source": item.get("source"),
            })
        )
    ]
    system_index = next(
        (
            index
            for index, item in enumerate(remaining)
            if str(item.get("role") or "") == "system"
        ),
        -1,
    )
    insertion_index = system_index + 1 if system_index >= 0 else 0
    return [
        *remaining[:insertion_index],
        *stable_records,
        *remaining[insertion_index:],
    ]


def _agent_context_limit_key(state: Mapping[str, Any], fallback: str) -> str:
    identity = state.get("modelIdentity")
    identity = identity if isinstance(identity, Mapping) else {}
    return str(
        identity.get("candidateId")
        or identity.get("model")
        or state.get("model")
        or fallback
        or ""
    ).strip()


def _agent_context_mounts(path: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": node.id,
            "parentId": str(node.parent_id or ""),
            "kind": str(node.value.get("context_kind") or "context"),
            "content": str(node.value.get("content") or ""),
            "source": str(node.value.get("context_source") or "context_tree"),
            "lifecycle": str(node.value.get("context_lifecycle") or ""),
            "metadata": (
                copy.deepcopy(dict(node.value.get("metadata") or {}))
                if isinstance(node.value.get("metadata"), Mapping)
                else {}
            ),
            "createdAt": node.created_at.isoformat(),
            "updatedAt": node.updated_at.isoformat(),
        }
        for node in _agent_active_context_nodes(path)
        if isinstance(node.value, Mapping)
    ]


class AgentContextRepository:
    """Read the new Agent kernel's durable ContextTree without opening a session."""

    def __init__(
        self,
        context_directory: str | Path,
        *,
        plugin_owner: Callable[
            [str], tuple[str | None, str] | None
        ] | None = None,
    ) -> None:
        self.context_directory = Path(context_directory).expanduser().resolve()
        self.plugin_owner = plugin_owner or _active_plugin_owner

    def read(self, tree_id: str) -> dict[str, Any]:
        target = str(tree_id or "").strip()
        return self.read_many((target,)).get(target, {})

    def read_many(self, tree_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Read existing trees with one index pass and one shared router."""

        if not (self.context_directory / "index.sqlite3").is_file():
            return {}
        from cyrene.core.context import ContextStoreRouter, TreeNotFoundError

        targets = tuple(
            dict.fromkeys(
                target
                for tree_id in tree_ids
                if (target := str(tree_id or "").strip())
            )
        )
        if not targets:
            return {}
        states: dict[str, dict[str, Any]] = {}
        with ContextStoreRouter(self.context_directory) as router:
            existing = router.existing_tree_ids(targets)
            for tree_id in targets:
                if tree_id not in existing:
                    continue
                try:
                    states[tree_id] = self._read_tree(router, tree_id)
                except TreeNotFoundError:
                    # The index and tree database can change between the batch
                    # lookup and projection; a concurrent deletion is benign.
                    continue
                except Exception:
                    logger.debug(
                        "Could not project ContextTree %s",
                        tree_id,
                        exc_info=True,
                    )
        return states

    def write_plugin_session_state(
        self,
        tree_id: str,
        pack_id: str,
        patch: Mapping[str, Any],
    ) -> bool:
        """Merge trusted application state into one ContextTree root Plugin slot."""

        target = str(tree_id or "").strip()
        owner = str(pack_id or "").strip()
        if not target or not owner or not (self.context_directory / "index.sqlite3").is_file():
            return False
        from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
        from cyrene.core.plugin import plugin_session_state, with_plugin_session_state

        try:
            with ContextStoreRouter(self.context_directory) as router:
                tree = router.get_tree(target)
                root = router.get_node(tree.id, tree.root_id)
                if not isinstance(root.value, Mapping):
                    return False
                previous = plugin_session_state(root.value, owner)
                incoming = copy.deepcopy(dict(patch))
                previous_public = previous.get("public_snapshot")
                incoming_public = incoming.get("public_snapshot")
                if isinstance(incoming_public, Mapping):
                    incoming["public_snapshot"] = {
                        **(
                            dict(previous_public)
                            if isinstance(previous_public, Mapping)
                            else {}
                        ),
                        **dict(incoming_public),
                    }
                router.update_node(
                    tree.id,
                    root.id,
                    with_plugin_session_state(
                        root.value,
                        owner,
                        {**previous, **incoming},
                    ),
                )
            return True
        except TreeNotFoundError:
            return False

    def update_node_content(
        self,
        tree_id: str,
        node_id: str,
        content: str,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        """Replace one persisted timeline node's content without changing identity."""

        target_tree = str(tree_id or "").strip()
        target_node = str(node_id or "").strip()
        replacement = str(content or "")
        if not target_tree or not target_node:
            raise ConversationContextUpdateError("context node is required")
        if len(replacement) > 1_000_000:
            raise ConversationContextUpdateError("context node content is too large")
        from cyrene.core.context import (
            ContextStoreRouter,
            NodeNotFoundError,
            TreeNotFoundError,
        )

        try:
            with ContextStoreRouter(self.context_directory) as router:
                node = router.get_node(target_tree, target_node)
                expected = str(expected_updated_at or "").strip()
                if expected and node.updated_at.isoformat() != expected:
                    raise ConversationContextUpdateError(
                        "context node changed before this update"
                    )
                value = node.value if isinstance(node.value, Mapping) else {}
                role = str(value.get("role") or "")
                if not role:
                    raise ConversationContextUpdateError(
                        "context node does not expose editable content"
                    )
                updated_value = copy.deepcopy(dict(value))
                if role == "tool_results":
                    try:
                        results = json.loads(replacement)
                    except json.JSONDecodeError as exc:
                        raise ConversationContextUpdateError(
                            "tool results must be valid JSON"
                        ) from exc
                    if not isinstance(results, list):
                        raise ConversationContextUpdateError(
                            "tool results must be a JSON array"
                        )
                    updated_value["results"] = results
                elif role in {"context_compaction", "context_reflection"}:
                    try:
                        messages = json.loads(replacement)
                    except json.JSONDecodeError as exc:
                        raise ConversationContextUpdateError(
                            "compacted messages must be valid JSON"
                        ) from exc
                    if not isinstance(messages, list):
                        raise ConversationContextUpdateError(
                            "compacted messages must be a JSON array"
                        )
                    updated_value["messages"] = messages
                else:
                    updated_value["content"] = replacement
                updated = router.update_node(
                    target_tree,
                    target_node,
                    updated_value,
                )
        except (NodeNotFoundError, TreeNotFoundError) as exc:
            raise ConversationContextUpdateError(
                "context node was not found"
            ) from exc
        return {
            "nodeId": str(updated.id),
            "updatedAt": updated.updated_at.isoformat(),
            "chars": len(replacement),
        }

    def update_system_prompt(
        self,
        tree_id: str,
        node_id: str,
        content: str,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        """Backward-compatible system prompt update entrypoint."""

        target_tree = str(tree_id or "").strip()
        target_node = str(node_id or "").strip()
        prompt = str(content or "")
        if not prompt.strip():
            raise ConversationContextUpdateError("system prompt cannot be empty")
        from cyrene.core.context import (
            ContextStoreRouter,
            NodeNotFoundError,
            TreeNotFoundError,
        )

        try:
            with ContextStoreRouter(self.context_directory) as router:
                node = router.get_node(target_tree, target_node)
                value = node.value if isinstance(node.value, Mapping) else {}
                role = str(value.get("role") or "")
                kind = str(value.get("context_kind") or "")
                if role != "system" and not (
                    role == "context" and kind == "system_prompt"
                ):
                    raise ConversationContextUpdateError(
                        "context node is not an editable system prompt"
                    )
        except (NodeNotFoundError, TreeNotFoundError) as exc:
            raise ConversationContextUpdateError(
                "context node was not found"
            ) from exc
        return self.update_node_content(
            target_tree,
            target_node,
            prompt,
            expected_updated_at,
        )

    def _read_tree(self, router: Any, tree_id: str) -> dict[str, Any]:
        tree = router.get_tree(tree_id)
        nodes = list(router.get_subtree(tree.id, tree.root_id))
        dialogue = [
            node
            for node in nodes
            if isinstance(node.value, Mapping)
            and node.value.get("role")
            in {
                "system",
                "user",
                "context",
                "assistant",
                "tool_results",
                "context_compaction",
                "context_reflection",
            }
        ]
        if not dialogue:
            return {}
        leaf = max(dialogue, key=lambda item: (item.created_at, item.id))
        by_id = {node.id: node for node in nodes}
        path = []
        current = leaf
        while current is not None:
            path.append(current)
            current = by_id.get(str(current.parent_id or ""))
        path.reverse()

        latest_model_node = next(
            (
                node
                for node in reversed(path)
                if isinstance(node.value, Mapping)
                and node.value.get("role") == "assistant"
                and str(node.value.get("model") or "").strip()
            ),
            None,
        )
        model = (
            str(latest_model_node.value.get("model") or "").strip()
            if latest_model_node is not None
            else ""
        )
        model_identity = (
            dict(latest_model_node.value.get("model_identity") or {})
            if latest_model_node is not None
            and isinstance(latest_model_node.value.get("model_identity"), Mapping)
            else {}
        )
        ephemeral_context = _agent_latest_ephemeral_context(path)
        used_packs, used_standalone = _agent_path_plugin_usage(
            path,
            self.plugin_owner,
        )
        context_mounts = _agent_context_mounts(path)
        root_value = path[0].value if path and isinstance(path[0].value, Mapping) else {}
        mounted_system_prompt = next(
            (
                str(mount.get("content") or "")
                for mount in context_mounts
                if str(mount.get("kind") or "") == "system_prompt"
            ),
            "",
        )
        plugin_snapshot = plugin_public_session_snapshot(root_value)
        raw_subagents = plugin_snapshot.get("subagents")
        subagents = {
            str(agent_id): dict(record)
            for agent_id, record in raw_subagents.items()
            if isinstance(record, Mapping)
        } if isinstance(raw_subagents, Mapping) else {}
        from cyrene.workbench.core_adapter.bridge import project_tool_activity_messages

        activity_messages = project_tool_activity_messages({
            "nodes": [
                {
                    "id": node.id,
                    "parent_id": node.parent_id,
                    "created_at": node.created_at.isoformat(),
                    "value": node.value,
                }
                for node in path
            ]
        })
        compaction_nodes = [
            node
            for node in path
            if isinstance(node.value, Mapping)
            and node.value.get("role") in {"context_compaction", "context_reflection"}
        ]
        latest_compaction = compaction_nodes[-1] if compaction_nodes else None
        latest_compaction_value = (
            latest_compaction.value
            if latest_compaction is not None
            and isinstance(latest_compaction.value, Mapping)
            else {}
        )
        state = {
            "treeId": tree.id,
            "rootId": tree.root_id,
            "leafId": leaf.id,
            "messages": _agent_path_messages(path),
            "systemPrompt": (
                mounted_system_prompt
                or str(root_value.get("content") or "")
            ),
            "rootSystemPrompt": str(root_value.get("content") or ""),
            "usage": _agent_path_usage(path),
            "model": model,
            "modelIdentity": model_identity,
            "ephemeralContext": ephemeral_context,
            "contextMounts": context_mounts,
            "timeline": _agent_path_timeline(path, self.plugin_owner),
            "usedPluginPacks": used_packs,
            "usedStandalonePlugins": used_standalone,
            "activityMessages": [dict(message) for message in activity_messages],
            "compaction": {
                "active": bool(compaction_nodes),
                "count": sum(
                    1
                    for node in compaction_nodes
                    if node.value.get("role") == "context_compaction"
                ),
                "blocks": sum(
                    1
                    for message in _agent_path_messages(path)
                    if message.get("compacted_block")
                ),
                "beforeTokens": int(
                    latest_compaction_value.get("before_tokens") or 0
                ),
                "afterTokens": int(
                    latest_compaction_value.get("after_tokens") or 0
                ),
                "contextLimit": int(
                    latest_compaction_value.get("context_limit") or 0
                ),
                "distilled": bool(latest_compaction_value.get("distilled")),
                "updatedAt": (
                    latest_compaction.updated_at.isoformat()
                    if latest_compaction is not None
                    else ""
                ),
            },
            "subagents": subagents,
            "subagentHistory": [
                dict(item) for item in plugin_snapshot.get("subagent_history", ())
                if isinstance(item, Mapping)
            ],
            "subagentDiscussions": {
                str(key): dict(item)
                for key, item in (
                    plugin_snapshot.get("subagent_discussions", {}).items()
                    if isinstance(plugin_snapshot.get("subagent_discussions"), Mapping)
                    else ()
                )
                if isinstance(item, Mapping)
            },
            "createdAt": tree.created_at.isoformat(),
            "updatedAt": max(
                (node.updated_at for node in nodes),
                default=leaf.updated_at,
            ).isoformat(),
        }
        from cyrene.workbench.core_adapter.conversation_runtime import context_checkpoint_from_nodes

        checkpoint = context_checkpoint_from_nodes(nodes)
        if isinstance(checkpoint, Mapping):
            state["checkpoint"] = dict(checkpoint)
        active_plan = plugin_snapshot.get("activePlan")
        if isinstance(active_plan, Mapping):
            state["activePlan"] = copy.deepcopy(dict(active_plan))
        active_goal = plugin_snapshot.get("activeGoal")
        if isinstance(active_goal, Mapping):
            state["activeGoal"] = copy.deepcopy(dict(active_goal))
        return state


def _message_layer(
    segments: dict[str, int],
    total: int,
) -> dict[str, Any] | None:
    blocks = []
    for key in ("compacted", "system", "user", "assistant", "tool"):
        tokens = int(segments.get(key, 0) or 0)
        if tokens > 0:
            blocks.append({
                "id": "segment." + key,
                "type": key,
                "tokens_est": tokens,
                "source": "",
                "reason": "",
            })
    if not blocks:
        return None
    return {
        "id": "messages",
        "label": localized("Conversation Messages", "对话消息"),
        "sublabel": None,
        "blocks": blocks,
        "totalTokens": total,
    }


def _system_prompt_blocks(
    content: str,
    total_tokens: int,
    approx_token_count: Callable[[str], int],
) -> list[dict[str, Any]]:
    """Split the durable system prompt into stable, user-facing categories."""

    sections: dict[str, list[str]] = {
        "identity": [],
        "behavior": [],
        "tools": [],
        "workspace": [],
    }
    mode = "behavior"
    for index, line in enumerate(str(content or "").splitlines(keepends=True)):
        stripped = line.strip()
        lowered = stripped.lower()
        if index == 0 and lowered.startswith(("you are ", "you’re ", "you're ")):
            section = "identity"
        elif lowered.startswith(("the workspace is ", "workspace: ")):
            section = "workspace"
        elif (
            "toolbox." in lowered
            or lowered.startswith("bash, read, write")
            or "tools exposed directly" in lowered
        ):
            mode = "tools"
            section = "tools"
        else:
            section = mode
        sections[section].append(line)

    populated = [
        (key, "".join(lines))
        for key, lines in sections.items()
        if "".join(lines).strip()
    ]
    if not populated or total_tokens <= 0:
        return []

    weights = [max(1, int(approx_token_count(text) or 0)) for _, text in populated]
    weight_total = sum(weights)
    allocations = [total_tokens * weight // weight_total for weight in weights]
    for index in range(total_tokens - sum(allocations)):
        allocations[index % len(allocations)] += 1

    type_by_key = {
        "identity": "identity",
        "behavior": "instructions",
        "tools": "tools",
        "workspace": "workspace",
    }
    return [
        {
            "id": f"system.{key}",
            "type": type_by_key[key],
            "tokens_est": tokens,
            "chars": len(text),
            "source": "context_tree",
            "reason": key,
            "content": text,
        }
        for (key, text), tokens in zip(populated, allocations, strict=True)
        if tokens > 0
    ]


def _persistent_context_blocks(
    state: Mapping[str, Any],
    approx_token_count: Callable[[str], int],
) -> tuple[list[dict[str, Any]], int]:
    blocks: list[dict[str, Any]] = []
    total_tokens = 0
    raw_mounts = state.get("contextMounts")
    for mount in raw_mounts if isinstance(raw_mounts, list) else ():
        if not isinstance(mount, Mapping) or context_is_turn({
            "context_lifecycle": mount.get("lifecycle"),
            "context_kind": mount.get("kind"),
            "context_source": mount.get("source"),
        }):
            continue
        content = str(mount.get("content") or "")
        tokens = max(0, int(approx_token_count(content) or 0))
        if tokens <= 0:
            continue
        kind = str(mount.get("kind") or "context")
        total_tokens += tokens
        common = {
            "nodeId": str(mount.get("id") or ""),
            "contextKind": kind,
            "source": str(mount.get("source") or "context_tree"),
            "lifecycle": str(mount.get("lifecycle") or ""),
            "createdAt": str(mount.get("createdAt") or ""),
            "updatedAt": str(mount.get("updatedAt") or ""),
            "metadata": (
                copy.deepcopy(dict(mount.get("metadata") or {}))
                if isinstance(mount.get("metadata"), Mapping)
                else {}
            ),
        }
        if kind == "system_prompt":
            mounted = _system_prompt_blocks(content, tokens, approx_token_count)
            for block in mounted:
                block.update({
                    **common,
                    "nodeContent": content,
                    "editable": bool(mount.get("id")),
                })
            blocks.extend(mounted)
            continue
        blocks.append({
            "id": f"context.{kind}" if kind != "context" else "context",
            "type": (
                "memory" if "memory" in kind
                else "runtime" if kind == "plugin_session"
                else "system"
            ),
            "tokens_est": tokens,
            "chars": len(content),
            "reason": kind,
            **common,
        })
    return blocks, total_tokens


def _agent_system_prefix_layer(
    state: Mapping[str, Any],
    messages: list[dict[str, Any]],
    approx_token_count: Callable[[str], int],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not messages or messages[0].get("role") != "system":
        return None, messages
    system_message = messages[0]
    system_tokens = sum(
        _agent_context_segments([system_message], approx_token_count).values()
    )
    if system_tokens <= 0:
        return None, messages[1:]
    context_blocks, context_tokens = _persistent_context_blocks(
        state,
        approx_token_count,
    )
    root_content = str(
        state.get("rootSystemPrompt")
        if "rootSystemPrompt" in state
        else state.get("systemPrompt") or ""
    )
    root_content_tokens = min(
        max(0, system_tokens - context_tokens),
        max(0, int(approx_token_count(root_content) or 0)),
    )
    overhead_tokens = max(
        0,
        system_tokens - context_tokens - root_content_tokens,
    )
    system_blocks = _system_prompt_blocks(
        root_content,
        root_content_tokens,
        approx_token_count,
    )
    raw_timeline = state.get("timeline")
    timeline = raw_timeline if isinstance(raw_timeline, list) else []
    root_id = str(state.get("rootId") or "")
    root_record = next(
        (
            item
            for item in timeline
            if isinstance(item, Mapping) and str(item.get("id") or "") == root_id
        ),
        {},
    )
    root_technical = root_record.get("technical")
    root_technical = root_technical if isinstance(root_technical, Mapping) else {}
    root_metadata = root_technical.get("metadata")
    root_metadata = root_metadata if isinstance(root_metadata, Mapping) else {}
    for block in system_blocks:
        block.update({
            "nodeId": root_id,
            "contextKind": "system_prompt",
            "source": str(root_record.get("source") or "context_tree"),
            "lifecycle": str(root_record.get("lifecycle") or "persistent"),
            "createdAt": str(
                root_record.get("createdAt") or state.get("createdAt") or ""
            ),
            "updatedAt": str(
                root_record.get("updatedAt") or state.get("updatedAt") or ""
            ),
            "nodeContent": root_content,
            "editable": bool(root_id),
            "metadata": copy.deepcopy(dict(root_metadata)),
        })
    system_blocks.extend(context_blocks)
    if overhead_tokens > 0:
        system_blocks.append({
            "id": "system.message_overhead",
            "type": "overhead",
            "tokens_est": overhead_tokens,
            "chars": 0,
            "source": "message_envelope",
            "reason": "message_overhead",
        })
    return ({
        "id": "system_prefix",
        "label": localized("System Prefix", "系统前缀"),
        "sublabel": None,
        "blocks": system_blocks,
        "totalTokens": system_tokens,
    }, messages[1:])


class ConversationContextQueryService:
    """Project the new Agent ContextTree into the Workbench conversation panel."""

    def __init__(
        self,
        *,
        chats: Any,
        agent_states: AgentContextRepository,
        default_model: Callable[[], str],
        context_limit: Callable[[str], int],
        approx_token_count: Callable[[str], int],
        compact_agent: Callable[[str, int], Awaitable[Mapping[str, Any]]] | None = None,
    ) -> None:
        self.chats = chats
        self.agent_states = agent_states
        self.default_model = default_model
        self.context_limit = context_limit
        self.approx_token_count = approx_token_count
        self.compact_agent = compact_agent

    async def _chat(self, chat_id: str) -> dict[str, Any]:
        chat = await asyncio.to_thread(self.chats.get, chat_id)
        if not isinstance(chat, dict):
            raise ConversationNotFoundError("chat not found")
        return chat

    async def activity_messages(self, chat_id: str) -> list[dict[str, Any]]:
        """Return durable tool-history cards reconstructed from the active tree."""

        await self._chat(chat_id)
        state = await self._agent_state(chat_id)
        raw_messages = state.get("activityMessages")
        return [
            dict(message)
            for message in raw_messages
            if isinstance(message, Mapping)
        ] if isinstance(raw_messages, list) else []

    async def subagents(self, chat_id: str, round_id: str) -> dict[str, Any]:
        await self._chat(chat_id)
        state = await self._agent_state(chat_id)
        records = state.get("subagents") if isinstance(state, Mapping) else {}
        records = records if isinstance(records, Mapping) else {}
        history = state.get("subagentHistory") if isinstance(state, Mapping) else []
        history = history if isinstance(history, list) else []
        requested_round = str(round_id or "").strip()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for agent_id, raw in records.items():
            if not isinstance(raw, Mapping):
                continue
            record_round = str(raw.get("round_id") or "").strip()
            grouped.setdefault(record_round, []).append({
                "id": str(agent_id),
                "name": str(agent_id),
                "task": str(raw.get("task") or ""),
                "status": str(raw.get("status") or "running"),
                "result": str(raw.get("result") or ""),
                "error": str(raw.get("error") or ""),
                "roundId": record_round,
            })
        for raw in history:
            if not isinstance(raw, Mapping):
                continue
            record_round = str(raw.get("round_id") or "").strip()
            grouped.setdefault(record_round, []).append({
                "id": str(raw.get("agent_id") or ""),
                "name": str(raw.get("agent_id") or ""),
                "task": str(raw.get("task") or ""),
                "status": str(raw.get("status") or "done"),
                "outcome": str(raw.get("outcome") or ""),
                "mode": str(raw.get("mode") or "execution"),
                "role": str(raw.get("role") or ""),
                "result": str(raw.get("result") or ""),
                "error": str(raw.get("error") or ""),
                "roundId": record_round,
            })
        rounds = [
            {
                "id": item_round,
                "title": item_round or "Subagents",
                "status": "running" if any(
                    agent["status"] not in {"done", "failed", "cancelled"}
                    for agent in agents
                ) else "done",
                "agentCount": len(agents),
                "activeCount": sum(
                    agent["status"] not in {"done", "failed", "cancelled"}
                    for agent in agents
                ),
            }
            for item_round, agents in grouped.items()
        ]
        active_round = (
            requested_round
            if requested_round in grouped
            else next(
                (item["id"] for item in rounds if item["status"] == "running"),
                rounds[0]["id"] if rounds else "",
            )
        )
        discussions = (
            state.get("subagentDiscussions")
            if isinstance(state, Mapping) else {}
        )
        discussions = discussions if isinstance(discussions, Mapping) else {}
        messages = [
            {
                "id": str(message.get("id") or ""),
                "from": str(message.get("from") or ""),
                "to": str(message.get("to") or ""),
                "content": str(message.get("content") or ""),
                "type": str(message.get("type") or "message"),
                "createdAt": str(message.get("created_at") or ""),
                "roundId": str(message.get("round_id") or ""),
                "discussionId": str(message.get("discussion_id") or ""),
            }
            for discussion in discussions.values()
            if isinstance(discussion, Mapping)
            and (not active_round or str(discussion.get("round_id") or "") == active_round)
            for message in discussion.get("transcript", ())
            if isinstance(message, Mapping)
        ]
        messages.sort(key=lambda item: (item["createdAt"], item["id"]))
        return {
            "rounds": rounds,
            "activeRoundId": active_round,
            "agents": grouped.get(active_round, []),
            "messages": messages,
        }

    async def summary(
        self,
        chat_id: str,
    ) -> dict[str, Any]:
        chat = await self._chat(chat_id)
        configured = str(chat.get("model") or self.default_model() or "")
        model_name = str(chat.get("lastModel") or configured)
        selection = str(chat.get("modelSelectionId") or configured).strip()
        agent_state = await self._agent_state(chat_id)
        return self._agent_summary(
            agent_state,
            model_name=model_name,
            ctx_limit=self.context_limit(
                _agent_context_limit_key(agent_state, selection)
            ),
        )

    async def compact(self, chat_id: str) -> dict[str, Any]:
        chat = await self._chat(chat_id)
        configured = str(chat.get("model") or self.default_model() or "")
        model_name = str(chat.get("lastModel") or configured)
        selection = str(chat.get("modelSelectionId") or configured).strip()
        state = await self._agent_state(chat_id)
        ctx_limit = self.context_limit(_agent_context_limit_key(state, selection))
        before_tokens = self._agent_summary(
            state,
            model_name=model_name,
            ctx_limit=ctx_limit,
        )["ctxUsed"]
        if self.compact_agent is None:
            return {
                "ok": False,
                "compacted": False,
                "reason": "unavailable",
                "beforeTokens": before_tokens,
                "afterTokens": before_tokens,
                "ctxLimit": ctx_limit,
                "triggerRatio": 0.6,
            }
        result = dict(await self.compact_agent(str(chat_id), int(ctx_limit)))
        after_state = await self._agent_state(chat_id)
        after_tokens = self._agent_summary(
            after_state,
            model_name=model_name,
            ctx_limit=ctx_limit,
        )["ctxUsed"]
        return {
            "ok": bool(result.get("ok", True)),
            "compacted": bool(result.get("compacted")),
            "reason": str(result.get("reason") or "compacted"),
            "beforeTokens": int(
                result.get("beforeTokens")
                or result.get("before_tokens")
                or before_tokens
            ),
            "afterTokens": int(
                result.get("afterTokens")
                or result.get("after_tokens")
                or after_tokens
            ),
            "ctxLimit": int(
                result.get("ctxLimit")
                or result.get("context_limit")
                or ctx_limit
            ),
            "triggerRatio": float(result.get("triggerRatio") or 0.6),
            "distilled": bool(result.get("distilled")),
        }

    async def blocks(
        self,
        chat_id: str,
    ) -> dict[str, Any]:
        chat = await self._chat(chat_id)
        return self._agent_blocks(chat, await self._agent_state(chat_id))

    async def update_system_prompt(
        self,
        chat_id: str,
        node_id: str,
        content: str,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        await self._chat(chat_id)
        updated = await asyncio.to_thread(
            self.agent_states.update_system_prompt,
            chat_id,
            node_id,
            content,
            expected_updated_at,
        )
        return {"ok": True, **updated}

    async def update_node_content(
        self,
        chat_id: str,
        node_id: str,
        content: str,
        expected_updated_at: str = "",
    ) -> dict[str, Any]:
        await self._chat(chat_id)
        updated = await asyncio.to_thread(
            self.agent_states.update_node_content,
            chat_id,
            node_id,
            content,
            expected_updated_at,
        )
        return {"ok": True, **updated}

    async def _agent_state(self, chat_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.agent_states.read, chat_id)

    def _agent_segments(
        self,
        state: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        raw_messages = state.get("messages")
        messages = [
            dict(message)
            for message in raw_messages if isinstance(message, Mapping)
        ] if isinstance(raw_messages, list) else []
        segments = _agent_context_segments(messages, self.approx_token_count)
        ephemeral = str(state.get("ephemeralContext") or "").strip()
        if ephemeral and not _agent_ephemeral_is_mounted(state, ephemeral):
            segments["user"] = int(segments.get("user") or 0) + (
                _agent_ephemeral_tokens(
                    messages,
                    ephemeral,
                    self.approx_token_count,
                )
            )
        return messages, segments

    def _agent_summary(
        self,
        state: Mapping[str, Any],
        *,
        model_name: str,
        ctx_limit: int,
    ) -> dict[str, Any]:
        messages, segments = self._agent_segments(state)
        used = sum(max(0, int(value or 0)) for value in segments.values())
        limit = max(0, int(ctx_limit or 0))
        actual_model = str(state.get("model") or "").strip()
        selected_model = str(model_name or "").strip()
        raw_identity = state.get("modelIdentity")
        model_identity = (
            dict(raw_identity) if isinstance(raw_identity, Mapping) else {}
        )
        raw_compaction = state.get("compaction")
        compaction = (
            dict(raw_compaction)
            if isinstance(raw_compaction, Mapping)
            else {}
        )
        return {
            "model": actual_model or selected_model,
            "selectedModel": selected_model,
            "actualModel": actual_model,
            "modelIdentity": model_identity,
            "usage": dict(state.get("usage") or {}),
            "ctxLimit": limit,
            "ctxUsed": used,
            "ratio": (used / limit) if limit > 0 else None,
            "compactTriggerRatio": 0.6,
            "messageCount": len(messages),
            "segments": [
                {"key": key, "tokens": int(segments.get(key) or 0)}
                for key in _AGENT_CONTEXT_SEGMENT_KEYS
            ],
            "compaction": {
                "active": bool(compaction.get("active")),
                "count": max(0, int(compaction.get("count") or 0)),
                "blocks": max(0, int(compaction.get("blocks") or 0)),
                "tokens": int(segments.get("compacted") or 0),
                "distilled": bool(compaction.get("distilled")),
            },
            "usedPluginPacks": list(state.get("usedPluginPacks") or []),
            "usedStandalonePlugins": list(state.get("usedStandalonePlugins") or []),
            "compositionSource": "agent_tree",
        }

    def _agent_blocks(
        self,
        chat: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_messages = state.get("messages")
        messages = [
            dict(message)
            for message in raw_messages if isinstance(message, Mapping)
        ] if isinstance(raw_messages, list) else []
        system_layer, conversation_messages = _agent_system_prefix_layer(
            state,
            messages,
            self.approx_token_count,
        )
        layers: list[dict[str, Any]] = []
        if system_layer is not None:
            layers.append(system_layer)
        turn_layer, turn_tokens = _agent_turn_context_layer(
            state,
            messages,
            self.approx_token_count,
        )
        if turn_layer is not None:
            layers.append(turn_layer)
        ephemeral = str(state.get("ephemeralContext") or "").strip()
        if ephemeral and not _agent_ephemeral_is_mounted(state, ephemeral):
            tokens = _agent_ephemeral_tokens(
                messages,
                ephemeral,
                self.approx_token_count,
            )
            layers.append({
                "id": "ephemeral",
                "label": localized("Ephemeral Tail", "临时上下文尾部"),
                "sublabel": None,
                "blocks": [{
                    "id": "ephemeral.run",
                    "type": "ephemeral",
                    "tokens_est": tokens,
                    "chars": len(ephemeral),
                    "source": "turn_metadata",
                    "reason": "ephemeral_context",
                    "lifecycle": "turn",
                    "createdAt": str(state.get("updatedAt") or ""),
                }],
                "totalTokens": tokens,
            })
        segments = _agent_context_segments(
            conversation_messages,
            self.approx_token_count,
        )
        segments["user"] = max(0, int(segments.get("user") or 0) - turn_tokens)
        message_total = sum(segments.values())
        message_layer = _message_layer(segments, message_total)
        if message_layer is not None:
            layers.append(message_layer)
        configured = str(chat.get("model") or self.default_model() or "")
        selection = str(chat.get("modelSelectionId") or configured).strip()
        total = sum(int(layer.get("totalTokens") or 0) for layer in layers)
        payload = self._blocks_payload(
            layers,
            message_total,
            context_used=total,
            context_limit=self.context_limit(
                _agent_context_limit_key(state, selection)
            ),
            used_plugin_packs=list(state.get("usedPluginPacks") or []),
            used_standalone_plugins=list(state.get("usedStandalonePlugins") or []),
            message_count=len(messages),
            updated_at=str(state.get("updatedAt") or ""),
        )
        raw_identity = state.get("modelIdentity")
        raw_compaction = state.get("compaction")
        payload.update({
            "treeId": str(state.get("treeId") or ""),
            "rootId": str(state.get("rootId") or ""),
            "leafId": str(state.get("leafId") or ""),
            "createdAt": str(state.get("createdAt") or ""),
            "selectedModel": selection,
            "actualModel": str(state.get("model") or ""),
            "modelIdentity": (
                dict(raw_identity) if isinstance(raw_identity, Mapping) else {}
            ),
            "compaction": (
                dict(raw_compaction) if isinstance(raw_compaction, Mapping) else {}
            ),
            "contextMounts": self._context_mounts_payload(state),
            "timeline": self._timeline_payload(state),
            "chronology": "model_input",
        })
        return payload

    def _context_mounts_payload(
        self,
        state: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        raw_mounts = state.get("contextMounts")
        mounts = raw_mounts if isinstance(raw_mounts, list) else []
        records = []
        for order, mount in enumerate(mounts):
            if not isinstance(mount, Mapping):
                continue
            content = str(mount.get("content") or "")
            records.append({
                "id": str(mount.get("id") or ""),
                "parentId": str(mount.get("parentId") or ""),
                "kind": str(mount.get("kind") or "context"),
                "source": str(mount.get("source") or "context_tree"),
                "lifecycle": str(mount.get("lifecycle") or ""),
                "tokensEst": max(0, int(self.approx_token_count(content) or 0)),
                "chars": len(content),
                "createdAt": str(mount.get("createdAt") or ""),
                "updatedAt": str(mount.get("updatedAt") or ""),
                "metadata": (
                    copy.deepcopy(dict(mount.get("metadata") or {}))
                    if isinstance(mount.get("metadata"), Mapping)
                    else {}
                ),
                "order": order,
            })
        return sorted(records, key=lambda item: (item["createdAt"], item["order"]))

    def _timeline_payload(
        self,
        state: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        raw_timeline = state.get("timeline")
        timeline = raw_timeline if isinstance(raw_timeline, list) else []
        raw_mounts = state.get("contextMounts")
        mounts = raw_mounts if isinstance(raw_mounts, list) else []
        mounted_ids = {
            str(mount.get("id") or "")
            for mount in mounts
            if isinstance(mount, Mapping) and str(mount.get("id") or "")
        }
        records = []
        for fallback_order, item in enumerate(timeline):
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("id") or "")
            content = str(item.get("_content") or "")
            record = {
                key: copy.deepcopy(value)
                for key, value in item.items()
                if key not in {"_content", "_contentFormat"}
            }
            record["order"] = max(0, int(item.get("order") or fallback_order))
            record["tokensEst"] = max(
                0,
                int(self.approx_token_count(content) or 0),
            )
            record["chars"] = len(content)
            record["type"] = str(record.get("role") or "")
            record["nodeId"] = str(record.get("id") or "")
            record["tokens_est"] = record["tokensEst"]
            record["reason"] = str(
                record.get("contextKind") or record.get("role") or ""
            )
            technical = record.get("technical")
            technical = technical if isinstance(technical, Mapping) else {}
            metadata = technical.get("metadata")
            record["metadata"] = (
                copy.deepcopy(dict(metadata))
                if isinstance(metadata, Mapping)
                else {}
            )
            record["content"] = content
            record["nodeContent"] = content
            record["contentFormat"] = str(item.get("_contentFormat") or "text")
            record["editable"] = bool(record.get("id"))
            record["activeMount"] = item_id in mounted_ids
            raw_tool_results = technical.get("tool_results")
            record["toolAttributions"] = [
                {
                    "pluginPack": str(result.get("plugin_pack") or ""),
                    "pluginName": str(result.get("plugin_name") or ""),
                    "operation": str(result.get("operation") or ""),
                }
                for result in (
                    raw_tool_results
                    if isinstance(raw_tool_results, list)
                    else []
                )
                if isinstance(result, Mapping)
            ]
            records.append(record)
        ordered = _agent_effective_timeline(records, mounts)
        for order, record in enumerate(ordered):
            record["order"] = order
        return ordered

    @staticmethod
    def _blocks_payload(
        layers: list[dict[str, Any]],
        message_total: int,
        *,
        context_used: int,
        context_limit: int,
        used_plugin_packs: list[str] | None = None,
        used_standalone_plugins: list[str] | None = None,
        message_count: int = 0,
        updated_at: str = "",
    ) -> dict[str, Any]:
        return {
            "layers": layers,
            "totalTokensEst": sum(layer["totalTokens"] for layer in layers),
            "messageTokens": message_total,
            "compositionSource": "agent_tree",
            "contextUsed": int(context_used),
            "contextLimit": int(context_limit),
            "usedPluginPacks": list(used_plugin_packs or []),
            "usedStandalonePlugins": list(used_standalone_plugins or []),
            "messageCount": max(0, int(message_count or 0)),
            "updatedAt": str(updated_at or ""),
        }


class ConversationInboxQueryService:
    """Project the durable Agent inbox for the active or latest round."""

    def __init__(
        self,
        *,
        chats: Any,
        run_manager: Any,
        utc_now: Callable[[], str],
        agent_messages: Callable[
            [str, str, int],
            Awaitable[Mapping[str, Any]],
        ] | None = None,
    ) -> None:
        self.chats = chats
        self.run_manager = run_manager
        self.utc_now = utc_now
        self.agent_messages = agent_messages

    async def _agent_events(
        self,
        chat_id: str,
        round_id: str = "",
    ) -> tuple[list[dict[str, Any]], str, bool, bool]:
        if self.agent_messages is None:
            return [], str(round_id or "").strip(), False, False
        try:
            snapshot = await self.agent_messages(
                chat_id,
                str(round_id or "").strip(),
                _AGENT_INBOX_EVENT_LIMIT,
            )
        except Exception:
            logger.exception("Failed to read Agent inbox for %s", chat_id)
            return [], str(round_id or "").strip(), False, False
        snapshot = snapshot if isinstance(snapshot, Mapping) else {}
        raw_messages = snapshot.get("messages")
        messages = list(raw_messages) if isinstance(raw_messages, list) else []
        target_round = str(
            snapshot.get("roundId") or round_id or ""
        ).strip()
        if not target_round:
            return (
                [],
                "",
                bool(snapshot.get("eventsTruncated")),
                bool(snapshot.get("historyWindowTruncated")),
            )
        messages = [
            message
            for message in messages
            if isinstance(message, Mapping)
            and str(message.get("round_id") or "").strip() == target_round
        ][-_AGENT_INBOX_EVENT_LIMIT:]
        events: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            message_id = str(message.get("message_id") or "").strip()
            if not message_id:
                continue
            sender = str(message.get("from") or "unknown")
            content = str(message.get("summary") or message.get("content") or "")
            preview = f"{sender}: {content}" if sender else content
            events.append({
                "eventId": f"agent-inbox:{message_id}",
                "type": "agent_message",
                "messageType": str(message.get("type") or "message"),
                "status": "consumed" if message.get("read") is True else "ready",
                "createdAt": str(message.get("timestamp") or ""),
                "preview": preview[:600],
                "fromAgent": sender,
                "toAgent": str(message.get("to") or "main"),
                "roundId": str(message.get("round_id") or ""),
            })
        return (
            events,
            target_round,
            bool(snapshot.get("eventsTruncated")),
            bool(snapshot.get("historyWindowTruncated")),
        )

    async def snapshot(self, chat_id: str) -> dict[str, Any]:
        started = time.monotonic()
        run = self.run_manager.get(chat_id)
        if run is None:
            if not await asyncio.to_thread(self.chats.get, chat_id):
                raise ConversationNotFoundError("chat not found")
            run = self.run_manager.get(chat_id)
        (
            agent_events,
            agent_round_id,
            events_truncated,
            history_window_truncated,
        ) = await self._agent_events(
            chat_id,
            str(run.run_id if run is not None else ""),
        )
        queue_depth = sum(
            item.get("status") == "ready" for item in agent_events
        )
        snapshot = self._snapshot_payload(
            chat_id,
            run,
            agent_events,
            queue_depth=queue_depth,
            agent_round_id=agent_round_id,
            events_truncated=events_truncated,
            history_window_truncated=history_window_truncated,
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= 1000:
            logger.warning(
                "Slow Workbench inbox snapshot [chat_id=%s active=%s duration_ms=%.1f]",
                chat_id,
                run is not None,
                elapsed_ms,
            )
        return snapshot

    def _snapshot_payload(
        self,
        chat_id: str,
        run: Any,
        events: list[dict[str, Any]],
        *,
        queue_depth: int,
        agent_round_id: str,
        events_truncated: bool,
        history_window_truncated: bool,
    ) -> dict[str, Any]:
        timestamps = [str(item.get("createdAt") or "") for item in events]
        return {
            "sessionId": chat_id,
            "runId": str(run.run_id if run is not None else agent_round_id),
            "agentRoundId": agent_round_id,
            "active": bool(run is not None and run.status in {"running", "finishing"}),
            "runStatus": str(run.status if run is not None else "idle"),
            "countsScope": "visible_events",
            "eventLimit": _AGENT_INBOX_EVENT_LIMIT,
            "eventsTruncated": bool(events_truncated),
            "historyWindowTruncated": bool(history_window_truncated),
            "counts": {
                "ready": sum(1 for item in events if item.get("status") == "ready"),
                "consumed": sum(
                    1 for item in events if item.get("status") == "consumed"
                ),
                "total": len(events),
            },
            "events": events,
            "queueDepth": max(0, int(queue_depth or 0)),
            "updatedAt": max((stamp for stamp in timestamps if stamp), default=""),
            "observedAt": self.utc_now(),
        }


__all__ = [
    "AgentContextRepository",
    "ConversationContextQueryService",
    "ConversationInboxQueryService",
    "ConversationNotFoundError",
]
