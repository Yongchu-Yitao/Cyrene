"""Provider-neutral message compaction for persistent ContextTree branches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

COMPACT_TRIGGER_RATIO = 0.6
COMPACT_RECENT_RATIO = 0.3
COMPACT_BLOCK_PREFIX = "[Compacted earlier context]"


def message_token_estimate(message: dict[str, Any]) -> int:
    encoded = json.dumps(
        message,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return max(1, (len(encoded) + 3) // 4)


def messages_token_estimate(messages: list[dict[str, Any]]) -> int:
    return sum(message_token_estimate(message) for message in messages)


def _compacted_text(message: dict[str, Any]) -> str:
    content = str(message.get("content") or "").strip()
    if content.startswith(COMPACT_BLOCK_PREFIX):
        return content[len(COMPACT_BLOCK_PREFIX) :].lstrip("\n")
    return content


def _render_prefix(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = _compacted_text(message)
        if message.get("compacted_block"):
            if content:
                lines.append(content)
            continue
        if role == "tool":
            if content:
                name = str(message.get("name") or "tool").strip() or "tool"
                preview = content[:500]
                suffix = " … [truncated]" if len(content) > len(preview) else ""
                lines.append(f"  [tool result] {name}: {preview}{suffix}")
            continue
        if role == "user" and content:
            lines.append(f"User: {content}")
            continue
        if role == "assistant":
            if content:
                lines.append(f"Assistant: {content}")
            for call in message.get("tool_calls") or ():
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                source = function if isinstance(function, dict) else call
                name = str(source.get("name") or "").strip()
                arguments = source.get("arguments")
                if name:
                    rendered = (
                        arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments or {}, ensure_ascii=False, default=str)
                    )
                    lines.append(f"  [tool] {name}({str(rendered)[:200]})")
            continue
        if role == "system" and content:
            lines.append(content)
    return "\n".join(lines).strip()


def _safe_recent_start(messages: list[dict[str, Any]], index: int) -> int:
    bounded = max(0, min(int(index), len(messages)))
    while bounded < len(messages) and messages[bounded].get("role") == "tool":
        bounded += 1
    return bounded


def _minimum_exact_tail_start(messages: list[dict[str, Any]]) -> int:
    """Keep the newest message and newest complete assistant/tool episode."""

    if not messages:
        return 0
    newest_message = len(messages) - 1
    for index in range(len(messages) - 2, -1, -1):
        message = messages[index]
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        if (
            index + 1 < len(messages)
            and messages[index + 1].get("role") == "tool"
        ):
            return min(index, newest_message)
    return newest_message


@dataclass(frozen=True, slots=True)
class ContextCompaction:
    messages: tuple[dict[str, Any], ...]
    before_tokens: int
    after_tokens: int
    context_limit: int
    compacted: bool
    needs_distillation: bool


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    context_limit: int,
    force: bool = False,
    reserved_tokens: int = 0,
) -> ContextCompaction:
    """Keep the exact recent 30% and fold the older prefix at 60% usage."""

    original = [dict(message) for message in messages]
    limit = max(0, int(context_limit or 0))
    reserved = max(0, int(reserved_tokens or 0))
    before = messages_token_estimate(original) + reserved
    if not original or (
        not force
        and (not limit or before <= int(limit * COMPACT_TRIGGER_RATIO))
    ):
        return ContextCompaction(tuple(original), before, before, limit, False, False)

    leading_system: list[dict[str, Any]] = []
    cursor = 0
    while (
        cursor < len(original)
        and original[cursor].get("role") == "system"
        and original[cursor].get("compacted_block") is not True
    ):
        leading_system.append(original[cursor])
        cursor += 1
    live = original[cursor:]
    if not live:
        return ContextCompaction(tuple(original), before, before, limit, False, False)

    minimum_tail_start = _minimum_exact_tail_start(live)
    if force:
        cut = minimum_tail_start
    else:
        recent_budget = max(1, int(limit * COMPACT_RECENT_RATIO))
        accumulated = 0
        cut = 0
        for index in range(len(live) - 1, -1, -1):
            accumulated += message_token_estimate(live[index])
            if accumulated > recent_budget:
                cut = index + 1
                break
        cut = min(cut, minimum_tail_start)
    cut = _safe_recent_start(live, cut)
    prefix = live[:cut]
    recent = live[cut:]
    if not prefix:
        return ContextCompaction(tuple(original), before, before, limit, False, False)

    summary = _render_prefix(prefix) or "Earlier context contained only tool results and was omitted."
    block = {
        "role": "system",
        "content": f"{COMPACT_BLOCK_PREFIX}\n{summary}",
        "compacted_block": True,
    }
    compacted_messages = [*leading_system, block, *recent]
    after = messages_token_estimate(compacted_messages) + reserved
    if after >= before:
        # The mechanical record intentionally keeps user/assistant text exact.
        # It can therefore be a little larger than the source when the prefix
        # contains no bulky tool result.  Return it as a distillation candidate
        # instead of suppressing compaction before the secondary model sees it.
        return ContextCompaction(
            tuple(compacted_messages),
            before,
            after,
            limit,
            True,
            True,
        )
    return ContextCompaction(
        tuple(compacted_messages),
        before,
        after,
        limit,
        True,
        bool(limit and after > int(limit * COMPACT_TRIGGER_RATIO)),
    )


def replace_compacted_summary(
    messages: list[dict[str, Any]],
    summary: str,
) -> list[dict[str, Any]]:
    replacement = str(summary or "").strip()
    if not replacement:
        return [dict(message) for message in messages]
    result = [dict(message) for message in messages]
    for message in result:
        if message.get("compacted_block"):
            message["content"] = f"{COMPACT_BLOCK_PREFIX}\n{replacement}"
            message["llm_compacted"] = True
            break
    return result


__all__ = [
    "COMPACT_BLOCK_PREFIX",
    "COMPACT_RECENT_RATIO",
    "COMPACT_TRIGGER_RATIO",
    "ContextCompaction",
    "compact_messages",
    "message_token_estimate",
    "messages_token_estimate",
    "replace_compacted_summary",
]
