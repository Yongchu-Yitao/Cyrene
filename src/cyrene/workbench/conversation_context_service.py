"""Application queries for Workbench conversation context and live inboxes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ConversationNotFoundError(LookupError):
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
    current_context_ids = {
        node.id for node in _agent_active_context_nodes(nodes)
    }
    for node in nodes:
        value = node.value if isinstance(node.value, Mapping) else {}
        role = str(value.get("role") or "")
        if role == "context_compaction":
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
            if node.id not in current_context_ids:
                continue
            content = str(value.get("content") or "").strip()
            if not content:
                continue
            system_message = next(
                (
                    message
                    for message in messages
                    if str(message.get("role") or "") == "system"
                ),
                None,
            )
            if system_message is None:
                messages.insert(0, {"role": "system", "content": content})
            else:
                current = str(system_message.get("content") or "").strip()
                system_message["content"] = "\n\n".join(
                    part for part in (current, content) if part
                )
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
    system = next(
        (
            message
            for message in messages
            if str(message.get("role") or "") == "system"
        ),
        None,
    )
    if system is None:
        return sum(
            _agent_context_segments(
                [{"role": "system", "content": extra}],
                approx_token_count,
            ).values()
        )
    current = str(system.get("content") or "").strip()
    merged = "\n\n".join(part for part in (current, extra) if part)
    return max(
        0,
        int(approx_token_count(merged) or 0)
        - int(approx_token_count(current) or 0),
    )


def _agent_path_plugin_usage(nodes: list[Any]) -> tuple[list[str], list[str]]:
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
        toolbox_value = result.get("value")
        if (
            str(result.get("name") or "") != "toolbox"
            or not isinstance(toolbox_value, Mapping)
            or toolbox_value.get("operation") != "invoke"
        ):
            return
        pack_id = str(toolbox_value.get("pack") or "").strip()
        plugin_name = str(toolbox_value.get("name") or "").strip()
        if pack_id and pack_id not in seen_packs:
            seen_packs.add(pack_id)
            packs.append(pack_id)
        elif plugin_name and plugin_name not in seen_standalone:
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


class AgentContextRepository:
    """Read the new Agent kernel's durable ContextTree without opening a session."""

    def __init__(self, context_directory: str | Path) -> None:
        self.context_directory = Path(context_directory).expanduser().resolve()

    def read(self, tree_id: str) -> dict[str, Any]:
        if not (self.context_directory / "index.sqlite3").is_file():
            return {}
        from agent.context import ContextStoreRouter, TreeNotFoundError

        router = ContextStoreRouter(self.context_directory)
        try:
            tree = router.get_tree(str(tree_id))
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
                }
            ]
            if not dialogue:
                return {}
            leaf = max(dialogue, key=lambda item: (item.created_at, item.id))
            path = list(router.get_path(tree.id, leaf.id))
        except TreeNotFoundError:
            return {}
        finally:
            router.close()

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
        latest_user = next(
            (
                node
                for node in reversed(path)
                if isinstance(node.value, Mapping)
                and node.value.get("role") == "user"
            ),
            None,
        )
        ephemeral_context = ""
        if latest_user is not None:
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
            if turn_user is not None:
                ephemeral_context = str(
                    turn_user.value["metadata"].get("ephemeral_context") or ""
                )
        used_packs, used_standalone = _agent_path_plugin_usage(path)
        context_mounts = [
            {
                "id": node.id,
                "kind": str(node.value.get("context_kind") or "context"),
                "content": str(node.value.get("content") or ""),
                "source": str(node.value.get("context_source") or "context_tree"),
            }
            for node in _agent_active_context_nodes(path)
            if isinstance(node.value, Mapping)
        ]
        root_value = path[0].value if path and isinstance(path[0].value, Mapping) else {}
        raw_subagents = root_value.get("_cyrene_subagents")
        subagents = {
            str(agent_id): dict(record)
            for agent_id, record in raw_subagents.items()
            if isinstance(record, Mapping)
        } if isinstance(raw_subagents, Mapping) else {}
        from agent.workbench.bridge import project_tool_activity_messages

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
            and node.value.get("role") == "context_compaction"
        ]
        latest_compaction = compaction_nodes[-1] if compaction_nodes else None
        latest_compaction_value = (
            latest_compaction.value
            if latest_compaction is not None
            and isinstance(latest_compaction.value, Mapping)
            else {}
        )
        return {
            "treeId": tree.id,
            "rootId": tree.root_id,
            "leafId": leaf.id,
            "messages": _agent_path_messages(path),
            "systemPrompt": str(root_value.get("content") or ""),
            "usage": _agent_path_usage(path),
            "model": model,
            "modelIdentity": model_identity,
            "ephemeralContext": ephemeral_context,
            "contextMounts": context_mounts,
            "usedPluginPacks": used_packs,
            "usedStandalonePlugins": used_standalone,
            "activityMessages": [dict(message) for message in activity_messages],
            "compaction": {
                "active": bool(compaction_nodes),
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
            "createdAt": tree.created_at.isoformat(),
            "updatedAt": max(
                (node.updated_at for node in nodes),
                default=leaf.updated_at,
            ).isoformat(),
        }


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
        "label": "Conversation Messages",
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
        }
        for (key, text), tokens in zip(populated, allocations, strict=True)
        if tokens > 0
    ]


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
        return {
            "rounds": rounds,
            "activeRoundId": active_round,
            "agents": grouped.get(active_round, []),
            "messages": [],
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
        if ephemeral:
            segments["system"] = int(segments.get("system") or 0) + (
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
        layers: list[dict[str, Any]] = []
        conversation_messages = messages
        if messages and messages[0].get("role") == "system":
            system_message = messages[0]
            conversation_messages = messages[1:]
            system_tokens = sum(
                _agent_context_segments(
                    [system_message],
                    self.approx_token_count,
                ).values()
            )
            if system_tokens > 0:
                context_blocks = []
                context_tokens = 0
                raw_mounts = state.get("contextMounts")
                for mount in raw_mounts if isinstance(raw_mounts, list) else ():
                    if not isinstance(mount, Mapping):
                        continue
                    content = str(mount.get("content") or "")
                    tokens = max(0, int(self.approx_token_count(content) or 0))
                    if tokens <= 0:
                        continue
                    kind = str(mount.get("kind") or "context")
                    context_tokens += tokens
                    context_blocks.append({
                        "id": f"context.{kind}" if kind != "context" else "context",
                        "type": (
                            "memory"
                            if "memory" in kind
                            else "runtime"
                            if kind == "plugin_session"
                            else "system"
                        ),
                        "tokens_est": tokens,
                        "chars": len(content),
                        "contextKind": kind,
                        "source": str(mount.get("source") or "context_tree"),
                        "reason": kind,
                    })
                root_tokens = max(0, system_tokens - context_tokens)
                system_blocks = _system_prompt_blocks(
                    str(
                        state.get("systemPrompt")
                        or system_message.get("content")
                        or ""
                    ),
                    root_tokens,
                    self.approx_token_count,
                )
                system_blocks.extend(context_blocks)
                layers.append({
                    "id": "system_prefix",
                    "label": "System Prefix",
                    "sublabel": None,
                    "blocks": system_blocks,
                    "totalTokens": system_tokens,
                })
        ephemeral = str(state.get("ephemeralContext") or "").strip()
        if ephemeral:
            tokens = _agent_ephemeral_tokens(
                messages,
                ephemeral,
                self.approx_token_count,
            )
            layers.append({
                "id": "ephemeral",
                "label": "Ephemeral Tail",
                "sublabel": None,
                "blocks": [{
                    "id": "ephemeral.run",
                    "type": "ephemeral",
                    "tokens_est": tokens,
                    "chars": len(ephemeral),
                }],
                "totalTokens": tokens,
            })
        segments = _agent_context_segments(
            conversation_messages,
            self.approx_token_count,
        )
        message_total = sum(segments.values())
        message_layer = _message_layer(segments, message_total)
        if message_layer is not None:
            layers.append(message_layer)
        configured = str(chat.get("model") or self.default_model() or "")
        selection = str(chat.get("modelSelectionId") or configured).strip()
        total = sum(int(layer.get("totalTokens") or 0) for layer in layers)
        return self._blocks_payload(
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
