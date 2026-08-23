"""Terminal-history text and OSC 133 command parsing helpers."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .shell_integration import OscMetadataParser


_OSC_RE = re.compile(rb"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_CSI_RE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC_RE = re.compile(rb"\x1b[ -/]*[@-~]")


def plain_terminal_text(data: bytes) -> str:
    """Return searchable text while preserving terminal line boundaries."""
    value = _OSC_RE.sub(b"", bytes(data or b""))
    value = _CSI_RE.sub(b"", value)
    value = _ESC_RE.sub(b"", value)
    text = value.decode("utf-8", errors="replace").replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    characters: list[str] = []
    for character in text:
        if character == "\b":
            if characters and characters[-1] != "\n":
                characters.pop()
            continue
        if character in {"\n", "\t"} or ord(character) >= 32:
            characters.append(character)
    return "".join(characters)


def osc133_commands(
    data: bytes,
    *,
    base_seq: int = 0,
    timestamp_at: Callable[[int], str] | None = None,
) -> list[dict[str, Any]]:
    """Extract command text, output bounds, exit status, and timestamps."""
    payload = bytes(data or b"")
    commands: list[dict[str, Any]] = []
    command_start: int | None = None
    output_start: int | None = None
    command_text = ""

    for event in OscMetadataParser().feed(payload, start_seq=base_seq):
        kind = event["kind"]
        if kind == "command":
            command_start = int(event["endSeq"])
            output_start = None
            command_text = ""
            continue
        if kind == "output" and command_start is not None:
            command_text = plain_terminal_text(
                payload[
                    command_start - base_seq:int(event["startSeq"]) - base_seq
                ]
            ).strip()
            output_start = int(event["endSeq"])
            continue
        if kind != "finished" or output_start is None:
            continue
        absolute_start = output_start
        absolute_end = int(event["startSeq"])
        commands.append({
            "id": f"cmd_{absolute_start}",
            "command": command_text,
            "outputStartSeq": absolute_start,
            "outputEndSeq": absolute_end,
            "exitCode": event.get("exitCode"),
            "startedAt": timestamp_at(absolute_start) if timestamp_at else "",
            "finishedAt": timestamp_at(absolute_end) if timestamp_at else "",
            "running": False,
        })
        command_start = None
        output_start = None
        command_text = ""

    if output_start is not None:
        absolute_start = output_start
        absolute_end = base_seq + len(payload)
        commands.append({
            "id": f"cmd_{absolute_start}",
            "command": command_text,
            "outputStartSeq": absolute_start,
            "outputEndSeq": absolute_end,
            "exitCode": None,
            "startedAt": timestamp_at(absolute_start) if timestamp_at else "",
            "finishedAt": "",
            "running": True,
        })
    return commands


__all__ = ["osc133_commands", "plain_terminal_text"]
