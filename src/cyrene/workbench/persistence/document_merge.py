"""Tracked document values and deterministic three-way merge policy."""

from __future__ import annotations

import copy
from typing import Any, TypeVar

T = TypeVar("T")

_MISSING = object()

_COUNTER_FIELDS = {
    "mention_count",
    "planRevision",
    "planDefinitionRevision",
    "citation_count",
}

class TrackedDict(dict):
    """A normal dict with an out-of-band baseline used for three-way merges."""

    _workbench_base: Any
    _workbench_key: str

class TrackedList(list):
    """A normal list with an out-of-band baseline used for three-way merges."""

    _workbench_base: Any
    _workbench_key: str

def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)

def _tracked(value: T, key: str) -> T:
    baseline = _plain(value)
    if isinstance(value, dict):
        out = TrackedDict(_plain(value))
    elif isinstance(value, list):
        out = TrackedList(_plain(value))
    else:
        return value
    out._workbench_base = baseline
    out._workbench_key = key
    return out  # type: ignore[return-value]

def baseline(value: Any) -> Any | None:
    raw = getattr(value, "_workbench_base", None)
    return _plain(raw) if raw is not None else None

def _entity_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    raw = value.get("id")
    return str(raw).strip() if raw is not None else ""

def _merge_entity_list(base: list[Any], local: list[Any], remote: list[Any], path: tuple[str, ...]) -> list[Any]:
    base_by_id = {_entity_id(item): item for item in base if _entity_id(item)}
    local_by_id = {_entity_id(item): item for item in local if _entity_id(item)}
    remote_by_id = {_entity_id(item): item for item in remote if _entity_id(item)}

    # Keep the caller's ordering, then include entities committed since its read.
    order: list[str] = []
    ordered_ids: set[str] = set()
    for source in (local, remote):
        for item in source:
            item_id = _entity_id(item)
            if item_id and item_id not in ordered_ids:
                order.append(item_id)
                ordered_ids.add(item_id)

    merged: list[Any] = []
    for item_id in order:
        base_item = base_by_id.get(item_id, _MISSING)
        local_item = local_by_id.get(item_id, _MISSING)
        remote_item = remote_by_id.get(item_id, _MISSING)
        value = _three_way_merge(base_item, local_item, remote_item, path + (item_id,))
        if value is not _MISSING:
            merged.append(value)

    # Preserve unkeyed legacy values.  They are uncommon, but dropping them
    # during the migration would be worse than retaining a duplicate.
    for item in local:
        if not _entity_id(item) and item not in merged:
            merged.append(_plain(item))
    for item in remote:
        if not _entity_id(item) and item not in merged:
            merged.append(_plain(item))
    collection = path[-1] if path else ""
    if collection in {"projects", "sessions", "chats", "items"}:
        merged.sort(
            key=lambda item: str(item.get("createdAt") or "") if isinstance(item, dict) else "",
            reverse=True,
        )
    elif collection in {"messages", "events", "runs"}:
        merged.sort(
            key=lambda item: str(
                item.get("createdAt") or item.get("startedAt") or ""
            ) if isinstance(item, dict) else "",
        )
    return merged

def _merge_plain_list(base: list[Any], local: list[Any], remote: list[Any]) -> list[Any]:
    if local == base:
        return _plain(remote)
    if remote == base:
        return _plain(local)
    if local == remote:
        return _plain(local)

    # Concurrent append/set-like edits: retain the caller's order and append
    # remote-only values. Explicit local removals of baseline values stay removed.
    result = _plain(local)
    for item in remote:
        if item in base and item not in local:
            continue
        if item not in result:
            result.append(_plain(item))
    return result

def _three_way_merge(base: Any, local: Any, remote: Any, path: tuple[str, ...] = ()) -> Any:
    if local is _MISSING:
        if base is _MISSING:
            return _plain(remote)
        # An explicit local deletion wins over a concurrent edit of the same key.
        return _MISSING
    if remote is _MISSING:
        if base is _MISSING:
            return _plain(local)
        # A deletion committed after this caller's baseline must win over the
        # caller's stale edits. Preserving the edited entity here resurrects
        # tasks/chats that another request explicitly deleted.
        return _MISSING
    if base is _MISSING:
        if local == remote:
            return _plain(local)
        if isinstance(local, dict) and isinstance(remote, dict):
            base = {}
        elif isinstance(local, list) and isinstance(remote, list):
            base = []
        else:
            return _plain(local)

    if local == base:
        return _plain(remote)
    if remote == base:
        return _plain(local)

    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        result: dict[str, Any] = {}
        keys = set(base) | set(local) | set(remote)
        for key in keys:
            value = _three_way_merge(
                base.get(key, _MISSING),
                local.get(key, _MISSING),
                remote.get(key, _MISSING),
                path + (str(key),),
            )
            if value is not _MISSING:
                result[str(key)] = value
        return result

    if isinstance(base, list) and isinstance(local, list) and isinstance(remote, list):
        keyed = any(_entity_id(item) for item in base + local + remote)
        if keyed and all(not isinstance(item, dict) or _entity_id(item) for item in base + local + remote):
            return _merge_entity_list(base, local, remote, path)
        return _merge_plain_list(base, local, remote)

    # Counters and revisions are merged by delta, avoiding lost increments.
    if (
        path
        and path[-1] in _COUNTER_FIELDS
        and isinstance(base, (int, float))
        and not isinstance(base, bool)
        and isinstance(local, (int, float))
        and not isinstance(local, bool)
        and isinstance(remote, (int, float))
        and not isinstance(remote, bool)
    ):
        return remote + (local - base)

    if local == remote:
        return _plain(local)

    # Both writers changed the same scalar. The later commit applies its value.
    return _plain(local)

plain = _plain
tracked = _tracked
entity_id = _entity_id
three_way_merge = _three_way_merge

MISSING = _MISSING
