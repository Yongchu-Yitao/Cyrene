"""Strict text decoding and content-free errors for provider streams."""

import codecs
import re
from typing import Any, AsyncIterator, Mapping

import httpx

from .stream_buffer import TextBuffer


_LINE_ENDINGS = re.compile(r"\r\n|\r|\n")


class ModelStreamError(RuntimeError):
    """A failed streaming response with content-free protocol diagnostics."""

    def __init__(
        self,
        kind: str,
        message: str,
        diagnostics: Mapping[str, Any],
    ) -> None:
        super().__init__(message)
        self.kind = str(kind)
        self.diagnostics = dict(diagnostics)


def _raise_invalid_stream_encoding(
    diagnostics: dict[str, Any] | None,
    exc: UnicodeDecodeError,
) -> None:
    if diagnostics is not None:
        diagnostics["termination_reason"] = "invalid_utf8_stream"
        diagnostics["stream_completed"] = False
        diagnostics["invalid_utf8_sequence_count"] = int(
            diagnostics.get("invalid_utf8_sequence_count") or 0
        ) + 1
    raise ModelStreamError(
        "protocol_invalid_utf8",
        "The model stream was not valid UTF-8.",
        diagnostics or {},
    ) from exc


async def strict_utf8_lines(
    response: httpx.Response,
    diagnostics: dict[str, Any] | None,
    byte_stream: AsyncIterator[bytes] | None = None,
) -> AsyncIterator[str]:
    """Decode the provider stream without allowing lossy text replacement.

    SSE is defined as UTF-8. HTTPX's public ``aiter_lines`` API deliberately
    decodes malformed byte sequences with ``errors='replace'``; by that point
    U+FFFD is indistinguishable from model output and would be persisted as if
    it were valid. Keep content-decoding in HTTPX, but own the text boundary so
    a damaged provider response cannot silently become conversation content.
    """

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    pending = TextBuffer()
    pending_cr = False

    try:
        async for chunk in byte_stream if byte_stream is not None else response.aiter_bytes():
            # Decode the entire transport chunk before exposing any of its
            # lines, preserving the strict decoder's failure boundary.
            text = decoder.decode(chunk, final=False)
            if not text:
                continue
            cursor = 0
            if pending_cr:
                yield str(pending)
                pending = TextBuffer()
                pending_cr = False
                if text.startswith("\n"):
                    cursor = 1
            for match in _LINE_ENDINGS.finditer(text, cursor):
                pending.append(text[cursor:match.start()])
                cursor = match.end()
                if match.group() == "\r" and cursor == len(text):
                    pending_cr = True
                    break
                yield str(pending)
                pending = TextBuffer()
            pending.append(text[cursor:])
        # UTF-8 produces no residual text on successful finalization. An
        # incomplete sequence must fail before a pending CR/last line is sent.
        decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        _raise_invalid_stream_encoding(diagnostics, exc)

    if pending_cr or pending:
        yield str(pending)
