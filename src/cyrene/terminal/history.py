"""Terminal-history text and OSC 133 command parsing helpers."""

from __future__ import annotations

import codecs
import re
from collections.abc import Callable
from typing import Any

from .shell_integration import OscMetadataParser


_OSC_RE = re.compile(rb"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_CSI_RE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC_RE = re.compile(rb"\x1b[ -/]*[@-~]")


class IncrementalPlainTextParser:
    """Normalize searchable terminal text across arbitrary PTY boundaries."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        saved = state or {}
        self._mode = str(saved.get("mode") or "normal")
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        utf8_buffer = bytes.fromhex(str(saved.get("utf8Buffer") or ""))
        self._decoder.setstate((utf8_buffer, int(saved.get("utf8Flag") or 0)))
        self._pending_cr = bool(saved.get("pendingCr"))
        self._pending_cr_end_seq = int(saved.get("pendingCrEndSeq") or 0)
        self._line = list(str(saved.get("line") or ""))
        self._line_number = max(1, int(saved.get("lineNumber") or 1))
        self._line_start_seq = max(0, int(saved.get("lineStartSeq") or 0))
        next_seq = saved.get("nextSeq")
        self._next_seq = None if next_seq is None else max(0, int(next_seq))

    def state(self) -> dict[str, Any]:
        utf8_buffer, utf8_flag = self._decoder.getstate()
        return {
            "mode": self._mode,
            "utf8Buffer": bytes(utf8_buffer).hex(),
            "utf8Flag": int(utf8_flag),
            "pendingCr": self._pending_cr,
            "pendingCrEndSeq": self._pending_cr_end_seq,
            "line": "".join(self._line),
            "lineNumber": self._line_number,
            "lineStartSeq": self._line_start_seq,
            "nextSeq": self._next_seq,
        }

    def feed(self, data: bytes, *, start_seq: int) -> list[dict[str, Any]]:
        payload = bytes(data or b"")
        absolute_start = max(0, int(start_seq))
        if self._next_seq is None:
            self._next_seq = absolute_start
            self._line_start_seq = absolute_start
        elif self._next_seq != absolute_start:
            raise ValueError("non-contiguous incremental terminal text")
        lines: list[dict[str, Any]] = []
        for offset, value in enumerate(payload):
            end_seq = absolute_start + offset + 1
            if self._consume_control_byte(value):
                continue
            decoded = self._decoder.decode(bytes((value,)), final=False)
            for character in decoded:
                self._consume_character(character, end_seq, lines)
        self._next_seq = absolute_start + len(payload)
        return lines

    def current_line(self) -> dict[str, Any]:
        return {
            "line": self._line_number,
            "startSeq": self._line_start_seq,
            "endSeq": int(self._next_seq or self._line_start_seq),
            "text": "".join(self._line),
            "complete": False,
        }

    def _consume_control_byte(self, value: int) -> bool:
        if self._mode == "normal":
            if value == 0x1B:
                self._mode = "escape"
                return True
            return False
        if self._mode == "escape":
            if value == ord("]"):
                self._mode = "osc"
            elif value == ord("["):
                self._mode = "csi"
            elif 0x20 <= value <= 0x2F:
                self._mode = "escape_intermediate"
            elif 0x30 <= value <= 0x7E:
                self._mode = "normal"
            else:
                self._mode = "normal"
                return False
            return True
        if self._mode == "csi":
            if 0x40 <= value <= 0x7E:
                self._mode = "normal"
            return True
        if self._mode == "escape_intermediate":
            if 0x30 <= value <= 0x7E:
                self._mode = "normal"
            return True
        if self._mode == "osc_escape":
            if value == ord("\\"):
                self._mode = "normal"
            elif value != 0x1B:
                self._mode = "osc"
            return True
        if value == 0x07:
            self._mode = "normal"
        elif value == 0x1B:
            self._mode = "osc_escape"
        return True

    def _consume_character(
        self, character: str, end_seq: int, lines: list[dict[str, Any]],
    ) -> None:
        if self._pending_cr:
            pending_end = self._pending_cr_end_seq
            self._pending_cr = False
            if character == "\n":
                self._finish_line(end_seq, lines)
                return
            self._finish_line(pending_end, lines)
        if character == "\r":
            self._pending_cr = True
            self._pending_cr_end_seq = end_seq
        elif character == "\n":
            self._finish_line(end_seq, lines)
        elif character == "\b":
            if self._line:
                self._line.pop()
        elif character == "\t" or ord(character) >= 32:
            self._line.append(character)

    def _finish_line(self, end_seq: int, lines: list[dict[str, Any]]) -> None:
        lines.append({
            "line": self._line_number,
            "startSeq": self._line_start_seq,
            "endSeq": end_seq,
            "text": "".join(self._line),
            "complete": True,
        })
        self._line.clear()
        self._line_number += 1
        self._line_start_seq = end_seq


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


__all__ = [
    "IncrementalPlainTextParser", "osc133_commands", "plain_terminal_text",
]
