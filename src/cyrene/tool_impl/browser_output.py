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


def file_chooser_instruction(result: dict[str, Any]) -> str:
    """Return an agent-actionable message for a securely intercepted chooser."""
    if str(result.get("code") or "") != "FILE_CHOOSER_INTERCEPTED":
        return ""
    chooser_id = str(result.get("chooserId") or "").strip()
    target = result.get("uploadTarget") if isinstance(result.get("uploadTarget"), dict) else {}
    origin = str(target.get("origin") or target.get("frameUrl") or result.get("url") or "")
    accept = str(target.get("accept") or "") or "(not declared)"
    multiple = bool(target.get("multiple"))
    return (
        "FILE_CHOOSER_INTERCEPTED: the native system picker was suppressed.\n"
        f"chooser_id: {chooser_id}\n"
        f"receiving_origin: {origin}\n"
        f"accept: {accept}\n"
        f"multiple: {multiple}\n"
        "Next action: call browser_upload_files with this chooser_id and the exact local file paths. "
        "That tool will pause for a human, single-use external-upload approval."
    )


def page_link_lines(result: dict[str, Any]) -> list[str]:
    """Format readable anchors returned by browser navigation."""
    links = result.get("links")
    if not isinstance(links, list):
        return []
    rows: list[str] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        text = " ".join(str(link.get("text") or "").split()).strip()
        url = str(link.get("url") or link.get("href") or "").strip()
        ref = str(link.get("ref") or "").strip()
        if text and url:
            prefix = f"[{ref}] " if ref else ""
            rows.append(f"- {prefix}{text!r} -> {url}")
    if not rows:
        return []
    return ["Text links on this page:\n" + "\n".join(rows)]


__all__ = ["page_signal_lines", "page_observation_lines", "page_link_lines", "file_chooser_instruction"]
