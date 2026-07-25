"""
LLM helper utilities: text extraction, truncation, and constants.

These are pure functions with no dependencies on agent.py or tools.py.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_TOOL_OUTPUT_CHARS = 12000


def _truncate(text: str, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def truncate(text: str, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    """Public bounded-output helper used at transport and tool boundaries."""
    return _truncate(text, limit)


def _assistant_text(message: dict[str, Any]) -> str:
    """Extract text content from an assistant message."""
    content = message.get("content")
    if isinstance(content, str):
        if content.strip():
            return content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        text = "".join(parts)
        if text.strip():
            return text
    # Fallback: some models (Qwen-style) put a final, user-facing answer in
    # ``reasoning_content`` instead of ``content``. Only honor that fallback on
    # *terminal* turns. When the message also carries ``tool_calls`` (e.g.
    # ``quit``), the reasoning is internal scratch work — the model's
    # deliberation about whether/what to act — NOT a reply, and must never be
    # surfaced to the user (this is what leaked chain-of-thought into proactive
    # messages). Return "" so callers reconstruct a proper final reply or, for
    # system-initiated rounds, stay silent.
    if message.get("tool_calls"):
        return ""
    reasoning = message.get("reasoning_content")
    if reasoning and isinstance(reasoning, str):
        return reasoning.strip()
    return ""


def assistant_text(message: dict[str, Any]) -> str:
    """Public response-text normalization for service boundaries."""
    return _assistant_text(message)


__all__ = ["assistant_text", "truncate"]
