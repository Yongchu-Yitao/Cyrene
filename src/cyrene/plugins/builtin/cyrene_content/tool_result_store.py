"""Session-scoped storage and bounded projection for large Plugin results."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from cyrene.runtime.paths import TEMP_DIR

_RESULT_SCHEME = "tool-result://"
_RESULT_ROOT = TEMP_DIR / "tool-results"
_RESULT_TTL_SECONDS = 24 * 60 * 60
_UNKNOWN_CONTEXT_FALLBACK_TOKENS = 128_000
_MAX_READ_CHARS = 100_000
_MAX_BATCH_TOKENS = 20_000
_REF_RE = re.compile(r"^tool-result://([0-9a-f]{24})/([0-9a-f]{32})$")


@dataclass(frozen=True, slots=True)
class ProjectedToolResult:
    content: str
    truncated: bool
    original_tokens: int
    original_bytes: int
    content_ref: str | None = None


class ToolResultReferenceError(ValueError):
    """The requested result is invalid, expired, or owned by another session."""


def _token_count(text: str) -> int:
    from cyrene.observability.context_trace import approx_token_count

    return approx_token_count(text)


def _session_key(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        raise ToolResultReferenceError("A session_id is required for tool results.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _context_limit(context_limit_tokens: int | None) -> int:
    explicit = max(0, int(context_limit_tokens or 0))
    if explicit:
        return explicit
    try:
        from cyrene.plugins.model_catalog import configured_context_limit

        configured = max(0, int(configured_context_limit() or 0))
        if configured:
            return configured
    except Exception:
        pass
    return _UNKNOWN_CONTEXT_FALLBACK_TOKENS


def tool_result_token_limit(*, context_limit_tokens: int | None = None) -> int:
    """Reserve at most one fiftieth of the active context for one result."""

    return max(1, _context_limit(context_limit_tokens) // 50)


def _cleanup_expired(root: Path, *, now: float | None = None) -> None:
    cutoff = (time.time() if now is None else now) - _RESULT_TTL_SECONDS
    if not root.exists():
        return
    for path in root.rglob("*.txt"):
        try:
            if path.stat().st_mtime <= cutoff:
                path.unlink()
        except OSError:
            continue
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass


def store_tool_result(text: str, *, session_id: str) -> str:
    """Persist a complete result and return an opaque session-bound reference."""

    session_key = _session_key(session_id)
    result_id = uuid4().hex
    _RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    _cleanup_expired(_RESULT_ROOT)
    session_directory = _RESULT_ROOT / session_key
    session_directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(session_directory, 0o700)
    except OSError:
        pass
    target = session_directory / f"{result_id}.txt"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".pending-",
        dir=session_directory,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(str(text))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return f"{_RESULT_SCHEME}{session_key}/{result_id}"


def _resolve_reference(content_ref: str, *, session_id: str) -> Path:
    match = _REF_RE.fullmatch(str(content_ref or "").strip())
    if match is None:
        raise ToolResultReferenceError("Invalid tool result reference.")
    expected_session_key = _session_key(session_id)
    session_key, result_id = match.groups()
    if session_key != expected_session_key:
        raise ToolResultReferenceError("Tool result reference belongs to another session.")
    path = _RESULT_ROOT / session_key / f"{result_id}.txt"
    try:
        stat = path.stat()
    except OSError as exc:
        raise ToolResultReferenceError(
            "Tool result reference was not found or has expired."
        ) from exc
    if stat.st_mtime <= time.time() - _RESULT_TTL_SECONDS:
        try:
            path.unlink()
        except OSError:
            pass
        raise ToolResultReferenceError("Tool result reference has expired.")
    try:
        os.utime(path, None)
    except OSError:
        pass
    return path


def _search_page(text: str, query: str, *, offset: int, limit: int) -> dict:
    folded = text.casefold()
    needle = query.casefold()
    cursor = max(0, min(offset, len(text)))
    snippets: list[str] = []
    used = 0
    next_offset = cursor
    while len(snippets) < 50:
        index = folded.find(needle, next_offset)
        if index < 0:
            break
        line_start = text.rfind("\n", 0, index) + 1
        line_end = text.find("\n", index)
        if line_end < 0:
            line_end = len(text)
        rendered = f"{line_start}:{text[line_start:line_end]}"
        if snippets and used + len(rendered) + 1 > limit:
            break
        chunk = rendered[: max(0, limit - used)]
        snippets.append(chunk)
        used += len(chunk) + 1
        next_offset = max(index + len(query), line_end + 1)
        if used >= limit:
            break
    return {
        "content": "\n".join(snippets),
        "offset": cursor,
        "next_offset": next_offset,
        "has_more": folded.find(needle, next_offset) >= 0,
        "matches": len(snippets),
    }


def read_tool_result(
    content_ref: str,
    *,
    session_id: str,
    offset: int = 0,
    limit: int = 4_000,
    query: str = "",
) -> str:
    """Read or search one bounded page of a stored result."""

    path = _resolve_reference(content_ref, session_id=session_id)
    text = path.read_text(encoding="utf-8")
    start = max(0, min(int(offset or 0), len(text)))
    requested = min(_MAX_READ_CHARS, max(1, int(limit or 4_000)))
    normalized_query = str(query or "")
    if normalized_query:
        page = _search_page(
            text,
            normalized_query,
            offset=start,
            limit=requested,
        )
    else:
        end = min(len(text), start + requested)
        page = {
            "content": text[start:end],
            "offset": start,
            "next_offset": end,
            "has_more": end < len(text),
        }
    return json.dumps(
        {
            "status": "success",
            "content_ref": content_ref,
            "total_chars": len(text),
            "query": normalized_query or None,
            **page,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _preview(
    text: str,
    *,
    content_ref: str | None,
    original_tokens: int,
    original_bytes: int,
    token_limit: int,
) -> str:
    def render(character_budget: int) -> str:
        head_size = (character_budget * 3) // 5
        tail_size = max(0, character_budget - head_size)
        return json.dumps(
            {
                "truncated": True,
                "original_tokens": original_tokens,
                "original_bytes": original_bytes,
                "preview_head": text[:head_size],
                "preview_tail": text[-tail_size:] if tail_size else "",
                "content_ref": content_ref,
                "next_action": (
                    "Call read_tool_result with content_ref and offset/limit or query."
                    if content_ref
                    else "Use a narrower tool query."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    low, high = 0, len(text)
    best = render(0)
    while low <= high:
        middle = (low + high) // 2
        candidate = render(middle)
        if _token_count(candidate) <= token_limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def project_tool_result_for_model(
    result: object,
    *,
    tool_name: str,
    tool_call_id: str,
    session_id: str,
    context_limit_tokens: int | None = None,
    token_limit_tokens: int | None = None,
) -> ProjectedToolResult:
    """Keep small results intact and externalize large results generically."""

    del tool_name, tool_call_id
    text = str(result)
    original_tokens = _token_count(text)
    original_bytes = len(text.encode("utf-8"))
    token_limit = (
        max(1, int(token_limit_tokens))
        if token_limit_tokens is not None
        else tool_result_token_limit(context_limit_tokens=context_limit_tokens)
    )
    if original_tokens <= token_limit:
        return ProjectedToolResult(text, False, original_tokens, original_bytes)
    try:
        content_ref = store_tool_result(text, session_id=session_id)
    except OSError:
        content_ref = None
    return ProjectedToolResult(
        _preview(
            text,
            content_ref=content_ref,
            original_tokens=original_tokens,
            original_bytes=original_bytes,
            token_limit=token_limit,
        ),
        True,
        original_tokens,
        original_bytes,
        content_ref,
    )


def _shared_budgets(token_counts: list[int], total: int) -> list[int]:
    budgets = [0] * len(token_counts)
    remaining = max(0, int(total))
    active = set(range(len(token_counts)))
    while active and remaining:
        share = max(1, remaining // len(active))
        completed = {index for index in active if token_counts[index] <= share}
        if completed:
            for index in completed:
                budgets[index] = token_counts[index]
                remaining -= token_counts[index]
            active.difference_update(completed)
            continue
        ordered = sorted(active)
        base, extra = divmod(remaining, len(ordered))
        for position, index in enumerate(ordered):
            budgets[index] = base + (position < extra)
        break
    return budgets


def project_tool_result_batch_for_model(
    results: list[tuple[object, str, str]],
    *,
    session_id: str,
    context_limit_tokens: int | None = None,
) -> list[ProjectedToolResult]:
    """Share one bounded context budget fairly across a call batch."""

    if not results:
        return []
    total_limit = min(
        _MAX_BATCH_TOKENS,
        tool_result_token_limit(context_limit_tokens=context_limit_tokens),
    )
    token_counts = [_token_count(str(result)) for result, _name, _id in results]
    budgets = (
        token_counts
        if sum(token_counts) <= total_limit
        else _shared_budgets(token_counts, total_limit)
    )
    return [
        project_tool_result_for_model(
            result,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            session_id=session_id,
            context_limit_tokens=context_limit_tokens,
            token_limit_tokens=max(1, budget),
        )
        for (result, tool_name, tool_call_id), budget in zip(results, budgets)
    ]


class ToolResultStore:
    """Service facade exposed by the content Plugin pack."""

    def store(self, text: str, *, session_id: str) -> str:
        return store_tool_result(text, session_id=session_id)

    def read(self, content_ref: str, *, session_id: str, **page: object) -> str:
        return read_tool_result(content_ref, session_id=session_id, **page)

    def project(self, result: object, **options: object) -> ProjectedToolResult:
        return project_tool_result_for_model(result, **options)

    def project_batch(
        self,
        results: list[tuple[object, str, str]],
        **options: object,
    ) -> list[ProjectedToolResult]:
        return project_tool_result_batch_for_model(results, **options)


_tool_result_store: ToolResultStore | None = None


def get_tool_result_store() -> ToolResultStore:
    global _tool_result_store
    if _tool_result_store is None:
        _tool_result_store = ToolResultStore()
    return _tool_result_store


__all__ = [
    "ProjectedToolResult",
    "ToolResultReferenceError",
    "ToolResultStore",
    "get_tool_result_store",
    "project_tool_result_batch_for_model",
    "project_tool_result_for_model",
    "read_tool_result",
    "store_tool_result",
    "tool_result_token_limit",
]
