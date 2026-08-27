"""Generic durable session state contributed by Plugin packs.

The Agent kernel owns only the envelope.  Each pack owns the contents of its
entry, while common hosts may discover child ContextTrees and public snapshot
fields without importing or understanding that pack.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


PLUGIN_SESSION_STATE_KEY = "_plugin_session_state"


def plugin_session_state(root_value: Any, pack_id: str) -> dict[str, Any]:
    """Return an isolated copy of one pack's durable session state."""

    if not isinstance(root_value, Mapping):
        return {}
    states = root_value.get(PLUGIN_SESSION_STATE_KEY)
    if not isinstance(states, Mapping):
        return {}
    state = states.get(str(pack_id or "").strip())
    return deepcopy(dict(state)) if isinstance(state, Mapping) else {}


def with_plugin_session_state(
    root_value: Any,
    pack_id: str,
    state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a root value with one pack entry atomically replaced."""

    if not isinstance(root_value, Mapping):
        raise TypeError("AgentSession root context must be a mapping")
    normalized = str(pack_id or "").strip()
    if not normalized:
        raise ValueError("pack_id is required")
    value = deepcopy(dict(root_value))
    raw_states = value.get(PLUGIN_SESSION_STATE_KEY)
    states = {
        str(key): deepcopy(dict(item))
        for key, item in raw_states.items()
        if str(key).strip() and isinstance(item, Mapping)
    } if isinstance(raw_states, Mapping) else {}
    if state is None:
        states.pop(normalized, None)
    else:
        states[normalized] = deepcopy(dict(state))
    if states:
        value[PLUGIN_SESSION_STATE_KEY] = states
    else:
        value.pop(PLUGIN_SESSION_STATE_KEY, None)
    return value


def without_plugin_session_state(root_value: Any) -> Any:
    """Copy a root value without runtime state from any Plugin pack."""

    if not isinstance(root_value, Mapping):
        return deepcopy(root_value)
    value = deepcopy(dict(root_value))
    value.pop(PLUGIN_SESSION_STATE_KEY, None)
    return value


def plugin_child_context_ids(root_value: Any) -> tuple[str, ...]:
    """Discover ContextTree children declared by all pack state entries."""

    if not isinstance(root_value, Mapping):
        return ()
    states = root_value.get(PLUGIN_SESSION_STATE_KEY)
    if not isinstance(states, Mapping):
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for state in states.values():
        if not isinstance(state, Mapping):
            continue
        raw_ids = state.get("child_context_ids")
        if not isinstance(raw_ids, (list, tuple)):
            continue
        for raw_id in raw_ids:
            context_id = str(raw_id or "").strip()
            if context_id and context_id not in seen:
                seen.add(context_id)
                found.append(context_id)
    return tuple(found)


def plugin_public_session_snapshot(root_value: Any) -> dict[str, Any]:
    """Merge public snapshot fields declared by durable pack state."""

    if not isinstance(root_value, Mapping):
        return {}
    states = root_value.get(PLUGIN_SESSION_STATE_KEY)
    if not isinstance(states, Mapping):
        return {}
    snapshot: dict[str, Any] = {}
    for pack_id in sorted(str(key) for key in states):
        state = states.get(pack_id)
        if not isinstance(state, Mapping):
            continue
        public = state.get("public_snapshot")
        if not isinstance(public, Mapping):
            continue
        for key, item in public.items():
            normalized = str(key or "").strip()
            if normalized and normalized not in snapshot:
                snapshot[normalized] = deepcopy(item)
    return snapshot


__all__ = [
    "PLUGIN_SESSION_STATE_KEY",
    "plugin_child_context_ids",
    "plugin_public_session_snapshot",
    "plugin_session_state",
    "with_plugin_session_state",
    "without_plugin_session_state",
]
