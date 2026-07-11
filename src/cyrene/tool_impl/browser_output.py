"""Shared formatting for browser observations returned to the agent."""

from __future__ import annotations

from typing import Any


def page_signal_lines(result: dict[str, Any]) -> list[str]:
    signal = result.get("page_signal")
    if not isinstance(signal, dict):
        return []
    kind = str(signal.get("kind") or "").strip()
    if kind == "access_gate":
        cooldown_ms = int(signal.get("cooldown_ms") or signal.get("cooldownMs") or 10000)
        return [
            "PAGE_SIGNAL: access_gate",
            f"RECOVERY_ALLOWED: wait at least {cooldown_ms // 1000}s, then make at most one recovery attempt.",
            "IF_STILL_BLOCKED: call browser_request_takeover; do not continue retrying.",
            f"Reason: {signal.get('message') or 'The page requires user takeover.'}",
        ]
    if kind and kind != "normal":
        return [f"PAGE_SIGNAL: {kind}"]
    return []


def page_observation_lines(result: dict[str, Any]) -> list[str]:
    lines = page_signal_lines(result)
    if str((result.get("page_signal") or {}).get("kind") or "") == "access_gate":
        preview = str(result.get("text") or "").strip()
        if preview:
            lines.append(f"Page text preview: {preview[:1200]}")
    return lines


__all__ = ["page_signal_lines", "page_observation_lines"]
