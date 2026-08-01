"""Mechanical, provider-neutral context compaction."""

from __future__ import annotations

from typing import Any

from cyrene.agent.message_utils import ensure_message_identity
from cyrene.config import ASSISTANT_NAME

COMPACT_TRIGGER_RATIO = 0.6
COMPACT_RECENT_RATIO = 0.3
COMPACT_BLOCK_PREFIX = "[Compacted earlier context]"


def is_compacted_block(message: dict[str, Any]) -> bool:
    return isinstance(message, dict) and bool(message.get("compacted_block"))


def _is_append_only_context_event(message: dict[str, Any]) -> bool:
    """Return context records whose exact payload must survive compaction."""
    return isinstance(message, dict) and bool(message.get("chat_group_context_event"))


def _strip_tool_episode_text(messages: list[dict[str, Any]]) -> list[str]:
    """Render messages compactly while omitting bulky tool results."""
    lines: list[str] = []
    for message in messages:
        if is_compacted_block(message):
            content = str(message.get("content") or "").strip()
            if content.startswith(COMPACT_BLOCK_PREFIX):
                content = content[len(COMPACT_BLOCK_PREFIX):].lstrip("\n")
            if content:
                lines.append(content)
            continue
        role = message.get("role")
        if role == "tool":
            continue
        content = str(message.get("content") or "").strip()
        if role == "user":
            if content:
                lines.append(f"User: {content}")
        elif role == "assistant":
            if content:
                lines.append(f"{ASSISTANT_NAME}: {content}")
            for tool_call in message.get("tool_calls") or []:
                function = (
                    tool_call.get("function", {})
                    if isinstance(tool_call, dict)
                    else {}
                )
                name = str(function.get("name") or "").strip()
                arguments = str(function.get("arguments") or "").strip()
                if name:
                    lines.append(f"  [tool] {name}({arguments[:200]})")
        elif role == "system" and content:
            lines.append(content[:300])
    return lines


def _safe_recent_start(live: list[dict[str, Any]], idx: int) -> int:
    """Move a boundary so the retained suffix never starts on a tool result."""
    bounded = max(0, min(idx, len(live)))
    while bounded < len(live) and live[bounded].get("role") == "tool":
        bounded += 1
    return bounded


def compact_messages_for_storage(
    messages: list[dict[str, Any]],
    *,
    ctx_limit: int | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Fold old messages into immutable compacted blocks within a token budget."""
    from cyrene.model_runtime.client import message_token_estimate
    from cyrene.runtime.config_store import get_current_ctx_limit

    if ctx_limit is None:
        ctx_limit = get_current_ctx_limit()
    if ctx_limit <= 0 and not force:
        return messages

    if not force:
        total = sum(message_token_estimate(message) for message in messages)
        if total <= int(ctx_limit * COMPACT_TRIGGER_RATIO):
            return messages

    if force:
        if len(messages) == 1 and is_compacted_block(messages[0]):
            return messages
        head_blocks: list[dict[str, Any]] = []
        to_compact = messages
        recent: list[dict[str, Any]] = []
    else:
        head_blocks = []
        index = 0
        while index < len(messages) and is_compacted_block(messages[index]):
            head_blocks.append(messages[index])
            index += 1
        live = messages[index:]

        recent_budget = int(ctx_limit * COMPACT_RECENT_RATIO)
        accumulated = 0
        cut = 0
        for index in range(len(live) - 1, -1, -1):
            accumulated += message_token_estimate(live[index])
            if accumulated > recent_budget:
                cut = index + 1
                break
        cut = _safe_recent_start(live, cut)
        to_compact = live[:cut]
        recent = live[cut:]

    if not to_compact:
        return messages

    # Membership records are an append-only authorization/context audit.  Keep
    # their exact JSON rather than folding it into prose (which would truncate
    # peer ids and paths and make a later revocation ambiguous).
    pinned_events = [message for message in to_compact if _is_append_only_context_event(message)]
    compactable = [message for message in to_compact if not _is_append_only_context_event(message)]
    block_lines = _strip_tool_episode_text(compactable)
    if not block_lines:
        if not force:
            return [*head_blocks, *pinned_events, *recent]
        block_lines = [
            "Earlier context contained only tool results and was omitted."
        ]
    block: dict[str, Any] = {
        "role": "system",
        "content": COMPACT_BLOCK_PREFIX + "\n" + "\n".join(block_lines),
        "compacted_block": True,
    }
    ensure_message_identity([block])
    return [*head_blocks, block, *pinned_events, *recent]
