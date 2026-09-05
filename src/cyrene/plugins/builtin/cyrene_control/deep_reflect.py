"""Plugin-owned deep-reflection service and Agent control tool."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from typing import Any

from cyrene.core.context import ContextNode
from cyrene.core.plugin import PluginContext

from .definitions import get_native_tool_def

TOOL_NAME = "DeepReflect"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "session_transition": "deep_reflection",
    "requires_order": True,
    "permission_review": False,
    "agent_exposure": "direct",
}

REFLECTION_SCHEMA = "cyrene.reflect_pack.v2"
SERVICE_ID = "deep_reflection"

_REFLECTION_PROMPT = """You are Cyrene's clean-context reflection worker.

You receive only the user-visible conversation messages between the user and
the Agent. Tool calls, tool results, and internal reasoning are intentionally
excluded. The user's messages are authoritative and already preserved verbatim;
never rewrite them. Diagnose why the Agent's current approach is not satisfying
the user, then produce a compact replacement for the Agent replies.

Return strict JSON only with this shape:
{
  "goal": "the user's actual goal",
  "hard_constraints": ["constraints that must remain true"],
  "verified_facts": ["facts supported by the supplied evidence"],
  "completed_work": ["work that remains valid"],
  "failure_diagnosis": [
    {
      "claim": "root-cause diagnosis",
      "evidence": "specific supplied evidence",
      "confidence": "high|medium|low"
    }
  ],
  "assumptions_to_drop": ["invalid or unverified assumptions"],
  "chosen_direction": "one materially better direction",
  "next_actions": ["ordered concrete actions"],
  "success_check": "an observable completion test",
  "compressed_agent_trace": [
    {"attempt": "what the Agent did", "result": "material outcome", "lesson": "what changes next"}
  ]
}

Do not merely summarize the conversation. Distinguish verified facts from
inference, do not invent a root cause, choose one direction, and make the first
next action executable by the main Agent. Do not include markdown or a prose
wrapper.
"""


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _extract_json_object(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:json)?\s*", "", source)
        source = re.sub(r"\s*```$", "", source)
    try:
        value = json.loads(source)
    except json.JSONDecodeError:
        start, end = source.find("{"), source.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            value = json.loads(source[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _normalized_reflection(value: Mapping[str, Any]) -> dict[str, Any]:
    def strings(name: str) -> list[str]:
        raw = value.get(name)
        if not isinstance(raw, list):
            raise ValueError(f"deep reflection field must be an array: {name}")
        return [str(item) for item in raw]

    goal = str(value.get("goal") or "").strip()
    chosen = str(value.get("chosen_direction") or "").strip()
    success = str(value.get("success_check") or "").strip()
    if not goal or not chosen or not success:
        raise ValueError(
            "deep reflection omitted goal, chosen_direction, or success_check"
        )
    diagnosis = value.get("failure_diagnosis")
    agent_trace = value.get("compressed_agent_trace")
    if not isinstance(diagnosis, list) or not all(
        isinstance(item, Mapping) for item in diagnosis
    ):
        raise ValueError("deep reflection failure_diagnosis is invalid")
    if not isinstance(agent_trace, list) or not all(
        isinstance(item, Mapping) for item in agent_trace
    ):
        raise ValueError("deep reflection compressed_agent_trace is invalid")
    return {
        "goal": goal,
        "hard_constraints": strings("hard_constraints"),
        "verified_facts": strings("verified_facts"),
        "completed_work": strings("completed_work"),
        "failure_diagnosis": [dict(item) for item in diagnosis],
        "assumptions_to_drop": strings("assumptions_to_drop"),
        "chosen_direction": chosen,
        "next_actions": strings("next_actions"),
        "success_check": success,
        "compressed_agent_trace": [dict(item) for item in agent_trace],
    }


def _node_record(node: ContextNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
        "value": (
            deepcopy(dict(node.value))
            if isinstance(node.value, Mapping)
            else node.value
        ),
    }


def _public_nodes(path: Sequence[ContextNode]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in path:
        value = node.value if isinstance(node.value, Mapping) else {}
        role = str(value.get("role") or "")
        if role == "context_reflection":
            embedded = value.get("public_nodes")
            if isinstance(embedded, list):
                result.extend(
                    deepcopy(dict(item))
                    for item in embedded
                    if isinstance(item, Mapping)
                )
        elif role in {"user", "assistant", "tool_results"}:
            result.append(_node_record(node))
    return result


def _archived_user_messages(context: PluginContext) -> list[dict[str, Any]]:
    try:
        from cyrene.plugins.builtin.cyrene_memory.archive import (
            load_session_conversation_entries,
        )

        entries = load_session_conversation_entries(
            str(context.tree_id or ""),
            context.workspace,
        )
    except Exception:
        return []
    return [
        {
            "source": "conversation_archive",
            "round_id": str(entry.get("round_id") or ""),
            "content": str(entry.get("user_body") or ""),
        }
        for entry in entries
    ]


def _user_messages(
    path: Sequence[ContextNode],
    context: PluginContext,
) -> list[dict[str, Any]]:
    result = _archived_user_messages(context)
    seen_rounds = {
        str(item.get("round_id") or "")
        for item in result
        if str(item.get("round_id") or "")
    }
    for node in path:
        value = node.value if isinstance(node.value, Mapping) else {}
        role = str(value.get("role") or "")
        if role == "context_reflection":
            model_context = value.get("model_context")
            model_context = (
                model_context if isinstance(model_context, Mapping) else {}
            )
            for raw in model_context.get("user_messages") or ():
                if not isinstance(raw, Mapping):
                    continue
                round_id = str(raw.get("round_id") or "")
                if round_id and round_id in seen_rounds:
                    continue
                result.append(deepcopy(dict(raw)))
                if round_id:
                    seen_rounds.add(round_id)
            continue
        if role != "user":
            continue
        metadata = value.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        round_id = str(value.get("run_id") or "")
        if round_id and round_id in seen_rounds:
            continue
        public = (
            metadata.get("public_user_message")
            if "public_user_message" in metadata
            else value.get("content")
        )
        result.append(
            {
                "source": "context_tree",
                "source_node_id": node.id,
                "round_id": round_id,
                "content": str(public or ""),
            }
        )
        if round_id:
            seen_rounds.add(round_id)
    return result


def build_reflection_messages(
    path: Sequence[ContextNode],
) -> list[dict[str, str]]:
    conversation_messages: list[dict[str, str]] = []
    seen_node_ids: set[str] = set()
    for record in _public_nodes(path):
        value = record.get("value")
        if not isinstance(value, Mapping):
            continue
        role = str(value.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        node_id = str(record.get("id") or "")
        if node_id and node_id in seen_node_ids:
            continue
        metadata = value.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        content = (
            metadata.get("public_user_message")
            if role == "user" and "public_user_message" in metadata
            else value.get("content")
        )
        content = str(content or "").strip()
        if not content:
            continue
        if node_id:
            seen_node_ids.add(node_id)
        conversation_messages.append(
            {
                "role": role,
                "content": content,
            }
        )
    return conversation_messages


class DeepReflectionService:
    """Generate a replacement pack without mutating the source ContextTree."""

    async def reflect(
        self,
        path: Sequence[ContextNode],
        arguments: Mapping[str, Any],
        context: PluginContext,
        *,
        attempts: int = 3,
    ) -> dict[str, Any]:
        gateway = context.services.get("model")
        complete = getattr(gateway, "complete", None)
        if not callable(complete):
            raise RuntimeError("secondary model gateway is unavailable")
        conversation_messages = build_reflection_messages(path)
        # Provider Plugins normally mount a model-observation child through the
        # supplied PluginContext.  Reflection must remain side-effect free until
        # AgentSession atomically commits the completed pack, so its worker call
        # deliberately keeps the session identity but has no writable tree.
        worker_context = replace(context, tree=None, node_id=None)
        last_error: Exception | None = None
        output: dict[str, Any] = {}
        reflection: dict[str, Any] | None = None
        completed_attempt = 0
        total_attempts = max(1, int(attempts))
        for attempt in range(1, total_attempts + 1):
            try:
                output = await complete(
                    [
                        {"role": "system", "content": _REFLECTION_PROMPT},
                        *conversation_messages,
                    ],
                    temperature=0.1,
                    route="secondary",
                    caller="deep_reflection",
                    session_id=str(context.tree_id or ""),
                    context=worker_context,
                )
                payload = _extract_json_object(str(output.get("content") or ""))
                reflection = _normalized_reflection(payload)
                completed_attempt = attempt
                break
            except Exception as exc:
                last_error = exc
                if attempt < total_attempts:
                    await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
        if reflection is None:
            raise RuntimeError(
                f"deep reflection failed after {total_attempts} attempts: {last_error}"
            ) from last_error

        model_context = {
            "schema": REFLECTION_SCHEMA,
            "user_messages": _user_messages(path, context),
            "reflection": reflection,
        }
        serialized_context = json.dumps(
            _json_safe(model_context),
            ensure_ascii=False,
            sort_keys=True,
        )
        return {
            "schema": REFLECTION_SCHEMA,
            "model_context": model_context,
            "public_nodes": _public_nodes(path),
            "source_node_ids": [node.id for node in path],
            "source_hash": hashlib.sha256(
                json.dumps(
                    [_node_record(node) for node in path],
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            "reflection_attempts": completed_attempt,
            "reflection_usage": _json_safe(output.get("usage") or {}),
            "reflection_model": str(output.get("model") or ""),
            "rendered_model_context": (
                "[Cyrene Reflect Pack]\n"
                "The user messages below are preserved verbatim and remain "
                "authoritative. Continue from the chosen direction and execute "
                "the first useful next action.\n"
                + serialized_context
            ),
        }


async def _tool_deep_reflect(
    _args: dict[str, Any],
    _context: PluginContext,
) -> str:
    raise RuntimeError(
        "DeepReflect must be executed as an AgentSession context transition"
    )


handler = _tool_deep_reflect

__all__ = [
    "DeepReflectionService",
    "REFLECTION_SCHEMA",
    "SERVICE_ID",
    "TOOL_DEF",
    "TOOL_METADATA",
    "TOOL_NAME",
    "build_reflection_messages",
    "handler",
]
