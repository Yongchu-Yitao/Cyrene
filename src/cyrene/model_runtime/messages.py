"""
LLM helper utilities: text extraction, truncation, and constants.

These are pure functions with no dependencies on agent.py or tools.py.
"""

import ast
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_MAX_TOOL_OUTPUT_CHARS = 12000
_JSON_FENCE_RE = re.compile(
    r"^\s*```(?:json|javascript|js)?\s*(?P<body>.*?)\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TRAILING_JSON_COMMA_RE = re.compile(r",(?=\s*[}\]])")


def _truncate(text: str, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def truncate(text: str, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    """Public bounded-output helper used at transport and tool boundaries."""
    return _truncate(text, limit)


def parse_tool_arguments(value: Any) -> dict[str, Any]:
    """Parse common OpenAI-compatible tool argument representations.

    The OpenAI wire contract uses a JSON string, while local runtimes commonly
    return an object directly, fenced JSON, Python-style object literals, or
    otherwise-valid JSON with a trailing comma. Accept those unambiguous forms
    at the compatibility boundary and keep schema validation authoritative.
    """
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        raise ValueError("Tool arguments must be a JSON object or object value.")

    source = value.strip()
    if not source:
        return {}
    fenced = _JSON_FENCE_RE.match(source)
    if fenced:
        source = fenced.group("body").strip()

    attempts = [source]
    without_trailing_commas = _TRAILING_JSON_COMMA_RE.sub("", source)
    if without_trailing_commas != source:
        attempts.append(without_trailing_commas)

    last_error: Exception | None = None
    for candidate in attempts:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            return dict(parsed)
        raise ValueError("Tool arguments must decode to a JSON object.")

    try:
        parsed = ast.literal_eval(without_trailing_commas)
    except (SyntaxError, ValueError) as exc:
        last_error = exc
    else:
        if isinstance(parsed, dict):
            return dict(parsed)
        raise ValueError("Tool arguments must decode to an object.")

    raise ValueError(f"Invalid tool arguments: {last_error}")


def canonical_tool_arguments(value: Any) -> str:
    """Return canonical JSON for every supported tool-argument representation."""
    return json.dumps(
        parse_tool_arguments(value),
        ensure_ascii=False,
        separators=(",", ":"),
    )


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


__all__ = [
    "assistant_text",
    "canonical_tool_arguments",
    "parse_tool_arguments",
    "truncate",
]
