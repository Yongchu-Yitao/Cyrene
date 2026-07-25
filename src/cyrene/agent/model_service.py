"""Public model-call service for the agent runtime and its consumers."""

from __future__ import annotations

from typing import Any

from cyrene.agent import state as _state
from cyrene.agent.context import bind_run_context


async def call_agent_model(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    *,
    caller: str | None = None,
    secondary: bool = False,
    thinking: str = "auto",
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the configured model with agent trace context attached."""
    binding = bind_run_context(caller=caller) if caller is not None else None
    try:
        optional: dict[str, Any] = {}
        if secondary:
            optional["secondary"] = True
        if thinking != "auto":
            optional["thinking"] = thinking
        if response_format is not None:
            optional["response_format"] = response_format
        return await _state._call_llm(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            **optional,
        )
    finally:
        if binding is not None:
            binding.reset()


async def stream_agent_model(
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    *,
    caller: str | None = None,
    secondary: bool = False,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stream a model response through the active reply writer."""
    binding = bind_run_context(caller=caller) if caller is not None else None
    try:
        return await _state._call_llm_stream(
            messages,
            max_tokens=max_tokens,
            secondary=secondary,
            tools=tools,
        )
    finally:
        if binding is not None:
            binding.reset()


def streaming_reply_requested() -> bool:
    """Return whether the active run has a reply stream writer."""
    return _state._streaming_reply_requested()


def set_final_reply_usage(usage: dict[str, Any] | None) -> None:
    """Store usage for the final reply generated in the active run."""
    _state._last_final_reply_usage.set(dict(usage) if usage else None)


def take_final_reply_usage() -> dict[str, Any] | None:
    """Return and clear the active run's final-reply usage."""
    usage = _state._last_final_reply_usage.get()
    _state._last_final_reply_usage.set(None)
    return dict(usage) if isinstance(usage, dict) else None


__all__ = [
    "call_agent_model",
    "set_final_reply_usage",
    "stream_agent_model",
    "streaming_reply_requested",
    "take_final_reply_usage",
]
