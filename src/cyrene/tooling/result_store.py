"""Bounded model projection and session-scoped storage for large tool results."""

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
_REF_RE = re.compile(r"^tool-result://([0-9a-f]{24})/([0-9a-f]{32})$")


@dataclass(frozen=True, slots=True)
class ProjectedToolResult:
    content: str
    truncated: bool
    original_tokens: int
    original_bytes: int
    content_ref: str | None = None


class ToolResultReferenceError(ValueError):
    """Raised when a result reference is invalid, expired, or out of scope."""


def _token_count(text: str) -> int:
    from cyrene.model_runtime.client import approx_token_count

    return approx_token_count(text)


def _current_session_id() -> str:
    try:
        from cyrene.agent.context import current_session_id

        return str(current_session_id() or "")
    except Exception:
        return ""


def _session_key(session_id: str) -> str:
    material = str(session_id or "__cyrene_unscoped_session__").encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def _context_limit(*, context_limit_tokens: int | None = None, secondary: bool = False) -> int:
    explicit = max(0, int(context_limit_tokens or 0))
    if explicit:
        return explicit
    try:
        from cyrene.runtime.config_store import (
            ctx_limit_for_model,
            effective_ctx_limit_for_model,
            get_current_ctx_limit,
            get_models,
            get_secondary_model,
        )

        candidate_limits = []
        if secondary:
            configured = get_secondary_model() or {}
            secondary_limit = max(0, int(configured.get("ctx_limit") or 0))
            if not secondary_limit:
                secondary_name = str(
                    configured.get("model")
                    or configured.get("name")
                    or configured.get("id")
                    or ""
                ).strip()
                secondary_limit = ctx_limit_for_model(secondary_name)
            if secondary_limit:
                candidate_limits.append(secondary_limit)
        configured_models = get_models() or []
        for candidate in configured_models:
            if not isinstance(candidate, dict):
                continue
            model_name = str(
                candidate.get("model")
                or candidate.get("name")
                or candidate.get("id")
                or ""
            ).strip()
            if not model_name:
                continue
            limit = effective_ctx_limit_for_model(model_name, configured_models)
            if limit > 0:
                candidate_limits.append(limit)
        if candidate_limits:
            return min(candidate_limits)
        current = max(0, int(get_current_ctx_limit() or 0))
        if current:
            return current
    except Exception:
        pass
    return _UNKNOWN_CONTEXT_FALLBACK_TOKENS


def tool_result_token_limit(
    *,
    context_limit_tokens: int | None = None,
    secondary: bool = False,
) -> int:
    """Return the per-result model budget: one fiftieth of model context."""
    return max(1, _context_limit(
        context_limit_tokens=context_limit_tokens,
        secondary=secondary,
    ) // 50)


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


def store_tool_result(text: str, *, session_id: str = "") -> str:
    """Persist one complete result and return an opaque, session-bound reference."""
    sid = str(session_id or _current_session_id())
    session_key = _session_key(sid)
    result_id = uuid4().hex
    _RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    _cleanup_expired(_RESULT_ROOT)
    session_dir = _RESULT_ROOT / session_key
    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(session_dir, 0o700)
    except OSError:
        pass
    target = session_dir / f"{result_id}.txt"
    fd, temporary_name = tempfile.mkstemp(prefix=".pending-", dir=session_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    try:
        os.utime(_RESULT_ROOT, None)
    except OSError:
        pass
    return f"{_RESULT_SCHEME}{session_key}/{result_id}"


def _resolve_reference(content_ref: str, *, session_id: str = "") -> Path:
    match = _REF_RE.fullmatch(str(content_ref or "").strip())
    if match is None:
        raise ToolResultReferenceError("Invalid tool result reference.")
    expected_session_key = _session_key(str(session_id or _current_session_id()))
    session_key, result_id = match.groups()
    if session_key != expected_session_key:
        raise ToolResultReferenceError("Tool result reference belongs to another session.")
    path = _RESULT_ROOT / session_key / f"{result_id}.txt"
    try:
        stat = path.stat()
    except OSError as exc:
        raise ToolResultReferenceError("Tool result reference was not found or has expired.") from exc
    if stat.st_mtime <= time.time() - _RESULT_TTL_SECONDS:
        try:
            path.unlink()
        except OSError:
            pass
        raise ToolResultReferenceError("Tool result reference has expired.")
    try:
        os.utime(path, None)
        os.utime(_RESULT_ROOT, None)
    except OSError:
        pass
    return path


def _search_result(text: str, query: str, *, offset: int, limit: int) -> dict:
    folded = text.casefold()
    needle = query.casefold()
    cursor = max(0, min(offset, len(text)))
    snippets: list[str] = []
    used = 0
    matches = 0
    next_offset = cursor
    while matches < 50:
        index = folded.find(needle, next_offset)
        if index < 0:
            break
        line_start = text.rfind("\n", 0, index) + 1
        line_end = text.find("\n", index)
        if line_end < 0:
            line_end = len(text)
        snippet = text[line_start:line_end]
        rendered = f"{line_start}:{snippet}"
        if snippets and used + len(rendered) + 1 > limit:
            break
        snippets.append(rendered[: max(0, limit - used)])
        used += len(snippets[-1]) + 1
        matches += 1
        next_offset = max(index + len(query), line_end + 1)
        if used >= limit:
            break
    has_more = folded.find(needle, next_offset) >= 0
    return {
        "content": "\n".join(snippets),
        "offset": cursor,
        "next_offset": next_offset,
        "has_more": has_more,
        "matches": matches,
    }


def read_tool_result(
    content_ref: str,
    *,
    offset: int = 0,
    limit: int = 4_000,
    query: str = "",
    session_id: str = "",
) -> str:
    """Read or search a bounded portion of a stored tool result."""
    path = _resolve_reference(content_ref, session_id=session_id)
    text = path.read_text(encoding="utf-8")
    start = max(0, min(int(offset or 0), len(text)))
    requested = min(_MAX_READ_CHARS, max(1, int(limit or 4_000)))
    clean_query = str(query or "")
    if clean_query:
        page = _search_result(text, clean_query, offset=start, limit=requested)
    else:
        end = min(len(text), start + requested)
        page = {
            "content": text[start:end],
            "offset": start,
            "next_offset": end,
            "has_more": end < len(text),
        }
    return json.dumps({
        "status": "success",
        "content_ref": content_ref,
        "total_chars": len(text),
        "query": clean_query or None,
        **page,
    }, ensure_ascii=False, separators=(",", ":"))


def _preview_envelope(
    text: str,
    *,
    content_ref: str | None,
    original_tokens: int,
    original_bytes: int,
    token_limit: int,
) -> str:
    def render(char_budget: int) -> str:
        head_chars = (char_budget * 3) // 5
        tail_chars = max(0, char_budget - head_chars)
        return json.dumps({
            "truncated": True,
            "original_tokens": original_tokens,
            "original_bytes": original_bytes,
            "preview_head": text[:head_chars],
            "preview_tail": text[-tail_chars:] if tail_chars else "",
            "content_ref": content_ref,
            "next_action": (
                "Call read_tool_result with content_ref and offset/limit or query."
                if content_ref
                else "Full result could not be stored; use a narrower tool query."
            ),
        }, ensure_ascii=False, separators=(",", ":"))

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
    session_id: str = "",
    context_limit_tokens: int | None = None,
    secondary: bool = False,
) -> ProjectedToolResult:
    """Return the exact small result or a bounded, recoverable model projection."""
    text = str(result)
    original_tokens = _token_count(text)
    original_bytes = len(text.encode("utf-8"))
    token_limit = tool_result_token_limit(
        context_limit_tokens=context_limit_tokens,
        secondary=secondary,
    )
    if original_tokens <= token_limit:
        return ProjectedToolResult(text, False, original_tokens, original_bytes)

    content_ref: str | None = None
    # A paged read already points at the authoritative full result. Reuse that
    # reference if an unusually small model window requires further projection.
    if str(tool_name or "") == "read_tool_result":
        try:
            decoded = json.loads(text)
            candidate_ref = str(decoded.get("content_ref") or "")
            _resolve_reference(candidate_ref, session_id=session_id)
            content_ref = candidate_ref
        except (TypeError, ValueError, json.JSONDecodeError, ToolResultReferenceError):
            content_ref = None
    if content_ref is None:
        try:
            content_ref = store_tool_result(text, session_id=session_id)
        except OSError:
            content_ref = None
    content = _preview_envelope(
        text,
        content_ref=content_ref,
        original_tokens=original_tokens,
        original_bytes=original_bytes,
        token_limit=token_limit,
    )
    return ProjectedToolResult(
        content,
        True,
        original_tokens,
        original_bytes,
        content_ref,
    )


__all__ = [
    "ProjectedToolResult",
    "ToolResultReferenceError",
    "project_tool_result_for_model",
    "read_tool_result",
    "store_tool_result",
    "tool_result_token_limit",
]
