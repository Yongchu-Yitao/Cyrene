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
_POWERPOINT_ARGUMENT_REFS_KEY = "_externalized_powerpoint_arguments"
_POWERPOINT_ARGUMENT_MIN_BYTES = 0
_POWERPOINT_RESULT_MIN_BYTES = 1_024
_POWERPOINT_MUTATION_TOOLS = frozenset({
    "PowerPointApplyBatch",
    "PowerPointCreateSlides",
    "PowerPointToolSearch",
})


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


def _json_object(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _powerpoint_mutation_arguments(
    tool_name: str,
    arguments: str,
) -> tuple[dict, dict] | None:
    if str(tool_name or "") not in _POWERPOINT_MUTATION_TOOLS:
        return None
    decoded = _json_object(arguments)
    if decoded is None:
        return None
    if tool_name in {"PowerPointApplyBatch", "PowerPointCreateSlides"}:
        mutation = decoded
    else:
        capability_id = str(decoded.get("capability_id") or "")
        if str(decoded.get("operation") or "") != "invoke" or not capability_id.startswith("ppt."):
            return None
        mutation = decoded.get("arguments")
        if not isinstance(mutation, dict):
            return None
    if len(arguments.encode("utf-8")) < _POWERPOINT_ARGUMENT_MIN_BYTES:
        return None
    heavy_keys = {"operations", "slideSpecs", "elements", "slides", "spec"}
    if not heavy_keys.intersection(mutation):
        encoded = json.dumps(mutation, ensure_ascii=False, separators=(",", ":"))
        if not any(f'"{key}"' in encoded for key in ("box", "geometry", "fillColor")):
            return None
    return decoded, mutation


def _powerpoint_argument_summary(tool_name: str, decoded: dict, mutation: dict) -> dict:
    operations = mutation.get("operations")
    slide_specs = mutation.get("slideSpecs")
    if not isinstance(slide_specs, list):
        slide_specs = mutation.get("slides") if isinstance(mutation.get("slides"), list) else []
    element_count = sum(
        len(spec.get("elements") or [])
        for spec in slide_specs
        if isinstance(spec, dict) and isinstance(spec.get("elements"), list)
    )
    return {
        "tool": tool_name,
        "capability_id": str(decoded.get("capability_id") or "") or None,
        "mode": str(mutation.get("mode") or "") or None,
        "slide_id": str(mutation.get("slideId") or "") or None,
        "expected_revision": mutation.get("expectedRevision"),
        "operation_count": len(operations) if isinstance(operations, list) else 0,
        "slide_spec_count": len(slide_specs),
        "element_count": element_count,
    }


def externalize_powerpoint_tool_arguments(
    message: dict,
    *,
    session_id: str = "",
) -> dict:
    """Store mutation arguments for an epoch-boundary PowerPoint receipt.

    Runtime tool loops do not call this helper. The compactor applies it to a
    copied, completed episode immediately before replacing that episode, so no
    live or previously provider-visible message is modified in place.
    """
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return message
    refs = dict(message.get(_POWERPOINT_ARGUMENT_REFS_KEY) or {})
    changed = False
    for call in calls:
        if not isinstance(call, dict):
            continue
        call_id = str(call.get("id") or "")
        function = call.get("function")
        if not call_id or not isinstance(function, dict) or call_id in refs:
            continue
        tool_name = str(function.get("name") or "")
        arguments = str(function.get("arguments") or "")
        parsed = _powerpoint_mutation_arguments(tool_name, arguments)
        if parsed is None:
            continue
        decoded, mutation = parsed
        try:
            content_ref = store_tool_result(arguments, session_id=session_id)
        except OSError:
            continue
        refs[call_id] = {
            "content_ref": content_ref,
            "original_bytes": len(arguments.encode("utf-8")),
            "summary": _powerpoint_argument_summary(tool_name, decoded, mutation),
        }
        changed = True
    if changed or refs:
        message[_POWERPOINT_ARGUMENT_REFS_KEY] = refs
    return message


def _compact_handles(items: object, *, limit: int = 64) -> list[dict]:
    if not isinstance(items, list):
        return []
    handles: list[dict] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        compact = {
            key: item[key]
            for key in ("index", "slideId", "id", "ref", "name", "type")
            if item.get(key) is not None
        }
        if compact:
            handles.append(compact)
    return handles


def _powerpoint_result_projection(
    text: str,
    *,
    tool_name: str,
    content_ref: str,
) -> str | None:
    decoded = _json_object(text)
    if decoded is None:
        return None
    mutation = decoded.get("result") if isinstance(decoded.get("result"), dict) else decoded
    capability_id = str(decoded.get("capability_id") or mutation.get("capability_id") or "")
    status = str(mutation.get("status") or decoded.get("status") or "").lower()
    is_mutation = tool_name in {"PowerPointApplyBatch", "PowerPointCreateSlides"} or (
        tool_name == "PowerPointToolSearch"
        and capability_id.startswith("ppt.")
        and isinstance(decoded.get("result"), dict)
    )
    if not is_mutation or status not in {"applied", "success", "completed"}:
        return None
    if len(text.encode("utf-8")) < _POWERPOINT_RESULT_MIN_BYTES and not any(
        isinstance(item, dict) and item.get("stages")
        for item in (mutation.get("created") or [])
    ):
        return None
    created = _compact_handles(mutation.get("created"))
    changed = _compact_handles(mutation.get("changed"))
    deleted = _compact_handles(mutation.get("deleted"))
    slide_ids = [
        str(item.get("slideId"))
        for item in created
        if str(item.get("slideId") or "")
    ]
    warnings = mutation.get("warnings") if isinstance(mutation.get("warnings"), list) else []
    envelope = {
        "status": status,
        "capability_id": capability_id or None,
        "operation": mutation.get("operation"),
        "mode": mutation.get("mode"),
        "revision": mutation.get("revision"),
        "created_slide_ids": slide_ids,
        "created": created,
        "changed": changed,
        "deleted": deleted,
        "created_count": len(mutation.get("created") or []),
        "changed_count": len(mutation.get("changed") or []),
        "deleted_count": len(mutation.get("deleted") or []),
        "warnings": warnings[:12],
        "content_ref": content_ref,
        "full_result_externalized": True,
        "next_action": "Use revision and handles above; call read_tool_result only for omitted details.",
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def _completed_tool_episode(messages: list[dict], index: int) -> tuple[list[dict], int] | None:
    assistant = messages[index]
    calls = assistant.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    results: list[dict] = []
    for offset, call in enumerate(calls, start=1):
        result_index = index + offset
        if result_index >= len(messages):
            return None
        result = messages[result_index]
        if (
            result.get("role") != "tool"
            or str(result.get("tool_call_id") or "") != str(call.get("id") or "")
        ):
            return None
        results.append(result)
    return results, index + len(calls)


def _powerpoint_episode_receipt(assistant: dict, results: list[dict]) -> dict:
    refs = assistant.get(_POWERPOINT_ARGUMENT_REFS_KEY) or {}
    result_by_id = {str(item.get("tool_call_id") or ""): item for item in results}
    calls = []
    for call in assistant.get("tool_calls") or []:
        call_id = str(call.get("id") or "")
        function = call.get("function") if isinstance(call, dict) else {}
        result_text = str((result_by_id.get(call_id) or {}).get("content") or "")
        ref = refs.get(call_id) if isinstance(refs, dict) else None
        try:
            result_ref = store_tool_result(
                result_text,
                session_id=_current_session_id(),
            )
        except OSError:
            result_ref = None
        calls.append({
            "tool": str((function or {}).get("name") or ""),
            "payload_ref": (ref or {}).get("content_ref") if isinstance(ref, dict) else None,
            "payload_summary": (ref or {}).get("summary") if isinstance(ref, dict) else None,
            "result": _powerpoint_receipt_result(result_text),
            "result_ref": result_ref,
        })
    content = json.dumps({
        "type": "powerpoint_tool_episode_receipt",
        "status": "completed",
        "calls": calls,
    }, ensure_ascii=False, separators=(",", ":"))
    receipt = {
        "role": "system",
        "content": content,
        "compacted_block": True,
        "powerpoint_episode_receipt": True,
    }
    if assistant.get("round_id"):
        receipt["round_id"] = assistant["round_id"]
    return receipt


def _powerpoint_receipt_result(text: str) -> dict:
    """Return the small state needed after a completed PowerPoint episode."""
    decoded = _json_object(text)
    if decoded is None:
        return {"summary": str(text or "")[:240]}
    nested = decoded.get("result")
    result = nested if isinstance(nested, dict) else decoded
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    capabilities = result.get("capabilities")
    created = result.get("created")
    changed = result.get("changed")
    deleted = result.get("deleted")
    slides = result.get("slides")
    shapes = result.get("shapes")
    compact: dict[str, object] = {
        "status": result.get("status") or decoded.get("status"),
        "capability_id": decoded.get("capability_id") or result.get("capability_id"),
        "operation": result.get("operation"),
        "mode": result.get("mode"),
        "revision": result.get("revision"),
    }
    if error:
        compact["error"] = {
            key: error.get(key)
            for key in ("type", "code", "message")
            if error.get(key) is not None
        }
    elif result.get("error_code") or result.get("message"):
        compact["error"] = {
            key: result.get(key)
            for key in ("error_code", "message")
            if result.get(key) is not None
        }
    if isinstance(capabilities, list):
        compact["capability_ids"] = [
            str(item.get("id") or "")
            for item in capabilities[:20]
            if isinstance(item, dict) and str(item.get("id") or "")
        ]
    for key, items in (
        ("created", created),
        ("changed", changed),
        ("deleted", deleted),
        ("slides", slides),
        ("shapes", shapes),
    ):
        handles = _compact_handles(items, limit=24)
        if handles:
            compact[key] = handles
        if isinstance(items, list):
            compact[f"{key}_count"] = len(items)
    presentation = result.get("presentation")
    if isinstance(presentation, dict):
        compact["presentation"] = {
            key: presentation.get(key)
            for key in ("name", "title", "slideCount")
            if presentation.get(key) is not None
        }
    return {key: value for key, value in compact.items() if value is not None}


def _is_powerpoint_completed_episode(assistant: dict) -> bool:
    for call in assistant.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "")
        if name.startswith("PowerPoint"):
            return True
        if name != "toolbox":
            continue
        arguments = _json_object(str(function.get("arguments") or "")) or {}
        if str(arguments.get("capability_id") or "").startswith("ppt."):
            return True
    return False


def compact_powerpoint_tool_episodes_for_epoch(messages: list[dict]) -> list[dict]:
    """Convert completed PowerPoint episodes at an explicit epoch boundary.

    Normal provider projection must remain append-only, so this transformation
    is called only by lane compaction.  The input is copied before externalized
    references are attached.  A completed episode at the live protocol tail is
    kept byte-exact because the provider may still require it for continuation.
    """
    source = [dict(message) for message in messages if isinstance(message, dict)]
    completed: dict[int, tuple[list[dict], int]] = {}
    for index, message in enumerate(source):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        episode = _completed_tool_episode(source, index)
        if (
            episode is not None
            and _is_powerpoint_completed_episode(message)
            and episode[1] < len(source) - 1
        ):
            externalize_powerpoint_tool_arguments(message)
            completed[index] = episode
    projected: list[dict] = []
    index = 0
    while index < len(source):
        message = source[index]
        episode = completed.get(index)
        if episode is not None:
            results, end_index = episode
            projected.append(_powerpoint_episode_receipt(message, results))
            index = end_index + 1
            continue
        projected.append(message)
        index += 1
    return projected


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
    powerpoint_ref: str | None = None
    if str(tool_name or "") in _POWERPOINT_MUTATION_TOOLS:
        powerpoint_candidate = _powerpoint_result_projection(
            text,
            tool_name=str(tool_name or ""),
            content_ref="",
        )
        if powerpoint_candidate is not None:
            try:
                powerpoint_ref = store_tool_result(text, session_id=session_id)
            except OSError:
                powerpoint_ref = None
        if powerpoint_ref is not None:
            powerpoint_projection = _powerpoint_result_projection(
                text,
                tool_name=str(tool_name or ""),
                content_ref=powerpoint_ref,
            )
            if powerpoint_projection is not None:
                return ProjectedToolResult(
                    powerpoint_projection,
                    True,
                    original_tokens,
                    original_bytes,
                    powerpoint_ref,
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
        content_ref = powerpoint_ref
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
    "compact_powerpoint_tool_episodes_for_epoch",
    "externalize_powerpoint_tool_arguments",
    "project_tool_result_for_model",
    "read_tool_result",
    "store_tool_result",
    "tool_result_token_limit",
]
