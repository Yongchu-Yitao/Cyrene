"""Strict text decoding and content-free errors for provider streams."""

import codecs
from typing import Any, AsyncIterator, Mapping

import httpx


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
) -> AsyncIterator[str]:
    """Decode the provider stream without allowing lossy text replacement.

    SSE is defined as UTF-8. HTTPX's public ``aiter_lines`` API deliberately
    decodes malformed byte sequences with ``errors='replace'``; by that point
    U+FFFD is indistinguishable from model output and would be persisted as if
    it were valid. Keep content-decoding in HTTPX, but own the text boundary so
    a damaged provider response cannot silently become conversation content.
    """

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    buffered = ""

    def complete_line() -> tuple[str, str] | None:
        newline_indexes = [
            index for index in (buffered.find("\r"), buffered.find("\n"))
            if index >= 0
        ]
        if not newline_indexes:
            return None
        index = min(newline_indexes)
        if buffered[index] == "\r" and index + 1 == len(buffered):
            return None
        separator_length = (
            2 if buffered[index:index + 2] == "\r\n" else 1
        )
        return buffered[:index], buffered[index + separator_length:]

    try:
        async for chunk in response.aiter_bytes():
            buffered += decoder.decode(chunk, final=False)
            while (line_and_rest := complete_line()) is not None:
                line, buffered = line_and_rest
                yield line
        buffered += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        _raise_invalid_stream_encoding(diagnostics, exc)

    while True:
        newline_indexes = [
            index for index in (buffered.find("\r"), buffered.find("\n"))
            if index >= 0
        ]
        if not newline_indexes:
            break
        index = min(newline_indexes)
        separator_length = 2 if buffered[index:index + 2] == "\r\n" else 1
        yield buffered[:index]
        buffered = buffered[index + separator_length:]
    if buffered:
        yield buffered
