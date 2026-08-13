"""Normalize operational Agent text into durable, structured notifications.

Some external Agents write transport diagnostics to the same ACP message stream
as their user-facing answer.  The protocol does not distinguish those lines,
so the Workbench must recognize only narrowly-shaped *leading* operational
notices and keep them out of assistant prose.

The classifier is deliberately Agent-agnostic: it keys off the warning format
and transport/network vocabulary, never an installation or executable name.
"""

from __future__ import annotations

import re
from typing import Any

_NOTICE_PREFIXES = ("warning:", "notice:")
_NOTICE_PREFIX_RE = re.compile(r"^\s*(warning|notice)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_OPERATIONAL_MARKER_RE = re.compile(
    r"(?:websockets?|https?\s+transport|transport|stream\s+disconnected|"
    r"request\s+timed\s+out|timed\s+out|peer\s+certificate|certificate\s+not\s+valid|"
    r"connection\s+(?:failed|closed|reset|timed\s+out)|network\s+(?:error|failure))",
    re.IGNORECASE,
)


def classify_operational_notice(text: str) -> dict[str, Any] | None:
    """Return a public notification payload for one operational warning line."""
    raw = str(text or "").strip()
    match = _NOTICE_PREFIX_RE.match(raw)
    if not match or not _OPERATIONAL_MARKER_RE.search(match.group(2)):
        return None

    detail = match.group(2).strip()
    lowered = detail.lower()
    if "certificate" in lowered:
        category = "tls_certificate"
    elif "falling back" in lowered or ("websocket" in lowered and "https" in lowered):
        category = "transport_fallback"
    elif "timed out" in lowered:
        category = "transport_timeout"
    else:
        category = "transport_warning"
    return {
        "severity": "warning",
        "category": category,
        "message": raw,
        "source": "agent_transport",
        "terminal": False,
    }


def _could_be_notice_prefix(text: str) -> bool:
    candidate = str(text or "").lstrip().lower()
    if not candidate:
        return True
    return any(prefix.startswith(candidate) or candidate.startswith(prefix) for prefix in _NOTICE_PREFIXES)


class LeadingOperationalNoticeFilter:
    """Incrementally split leading operational notices from streamed prose.

    Only the beginning of an assistant message is inspected.  Normal prose is
    released as soon as it cannot be a ``Warning:``/``Notice:`` prefix, so the
    filter does not add token-by-token latency to ordinary replies.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._resolved = False

    def feed(self, text: str) -> tuple[list[dict[str, Any]], str]:
        value = str(text or "")
        if not value:
            return [], ""
        if self._resolved:
            return [], value
        self._buffer += value
        return self._drain(final=False)

    def finish(self) -> tuple[list[dict[str, Any]], str]:
        if self._resolved or not self._buffer:
            return [], ""
        return self._drain(final=True)

    def complete(self, text: str) -> tuple[list[dict[str, Any]], str]:
        """Normalize an authoritative completed message and discard pending data."""
        self._buffer = ""
        self._resolved = True
        return split_leading_operational_notices(text)

    def _drain(self, *, final: bool) -> tuple[list[dict[str, Any]], str]:
        notices: list[dict[str, Any]] = []
        while self._buffer:
            line_end = self._buffer.find("\n")
            if line_end < 0:
                if not final and _could_be_notice_prefix(self._buffer):
                    return notices, ""
                notice = classify_operational_notice(self._buffer) if final else None
                if notice:
                    notices.append(notice)
                    self._buffer = ""
                    self._resolved = True
                    return notices, ""
                visible = self._buffer
                self._buffer = ""
                self._resolved = True
                return notices, visible

            first_line = self._buffer[:line_end].rstrip("\r")
            notice = classify_operational_notice(first_line)
            if not notice:
                visible = self._buffer
                self._buffer = ""
                self._resolved = True
                return notices, visible

            notices.append(notice)
            self._buffer = self._buffer[line_end + 1 :]
            # The diagnostic and answer are normally separated by one empty
            # line.  Consume only line breaks, not user-visible indentation.
            self._buffer = self._buffer.lstrip("\r\n")

        return notices, ""


def split_leading_operational_notices(
    text: str,
) -> tuple[list[dict[str, Any]], str]:
    """Normalize a complete assistant message using the streaming rules."""
    normalizer = LeadingOperationalNoticeFilter()
    notices, visible = normalizer.feed(str(text or ""))
    final_notices, tail = normalizer.finish()
    return [*notices, *final_notices], visible + tail
