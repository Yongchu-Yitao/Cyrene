"""Capability normalization for the unified Agent Runtime.

Capability state is per Agent/Session data, never a global constant
(handoff §20).  States support ``supported`` / ``unsupported`` /
``unknown`` / ``degraded`` plus ``agent_defined`` where applicable
(interaction.permission).  Unknown state values are coerced conservatively;
unknown capabilities are treated as unavailable by consumers with side
effects while display-only capabilities may light up when a real event
arrives (§13).
"""

from __future__ import annotations

from typing import Any, Literal

CapabilityState = Literal[
    "supported",
    "unsupported",
    "unknown",
    "degraded",
    "agent_defined",
]

CAPABILITY_STATES: tuple[str, ...] = (
    "supported",
    "unsupported",
    "unknown",
    "degraded",
    "agent_defined",
)

# Known capability groups/keys consumed by the Workbench UI (handoff §6.2).
KNOWN_CAPABILITY_GROUPS: dict[str, tuple[str, ...]] = {
    "session": ("load", "fork", "close"),
    "input": ("text", "image", "file", "audio"),
    "output": ("streaming", "reasoning", "toolLifecycle", "artifacts", "diff"),
    "interaction": ("permission", "elicitation", "steer", "cancel"),
    "model": ("agentManaged", "cyreneManaged", "switchDuringSession", "reasoningEffort"),
}

_MODEL_PROTOCOL_KEY = "cyreneManaged"


def normalize_capability_state(value: Any, *, default: str = "unknown") -> str:
    """Coerce a raw capability marker to a stable state string."""
    if isinstance(value, str) and value.strip().lower() in CAPABILITY_STATES:
        return value.strip().lower()
    return default


def _normalize_protocol_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalize_capabilities(
    raw: dict[str, Any] | None,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a raw capability dict into a stable shape.

    Known groups/keys are validated; the special ``model.cyreneManaged`` key
    is normalized to a list of protocol names.  Unknown groups are kept only
    when every value is a valid state or a list of non-empty strings so
    agent-specific capability data is not silently discarded.  An empty/None
    input normalizes to ``{}`` (external agents start unprobed).
    """
    raw = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {}
    if isinstance(base, dict):
        for group, items in base.items():
            if isinstance(items, dict):
                result[group] = {key: value for key, value in items.items()}
    for group, items in raw.items():
        if not isinstance(items, dict):
            continue
        known_keys = KNOWN_CAPABILITY_GROUPS.get(group)
        target: dict[str, Any] = {}
        if known_keys is None:
            for key, value in items.items():
                if isinstance(value, list) and all(str(item).strip() for item in value):
                    target[key] = [str(item).strip() for item in value]
                elif isinstance(value, str) and value.strip().lower() in CAPABILITY_STATES:
                    target[key] = value.strip().lower()
        else:
            for key, value in items.items():
                if key not in known_keys:
                    continue
                if group == "model" and key == _MODEL_PROTOCOL_KEY:
                    target[key] = _normalize_protocol_list(value)
                else:
                    target[key] = normalize_capability_state(value)
        if target:
            result[group] = target
    return result


def merge_capabilities(
    *sources: dict[str, Any] | None,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deep-merge capability sources; later sources win per group/key.

    Sources follow the handoff priority: live handshake, run probe, verified
    Profile, Manifest declaration (§6.2).
    """
    merged = normalize_capabilities(base)
    for source in sources:
        normalized = normalize_capabilities(source)
        for group, items in normalized.items():
            target = merged.setdefault(group, {})
            target.update(items)
    return merged


def with_conservative_defaults(
    caps: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a full-shape capability dict; missing keys become ``unknown``.

    Consumers use this when deciding what UI/behavior to offer: input and
    side-effect interactions treat ``unknown`` as unsupported; display-only
    capabilities may enable on real events (§13).
    """
    normalized = normalize_capabilities(caps)
    result: dict[str, Any] = {}
    for group, keys in KNOWN_CAPABILITY_GROUPS.items():
        items = normalized.get(group) if isinstance(normalized.get(group), dict) else {}
        out: dict[str, Any] = {}
        for key in keys:
            if group == "model" and key == _MODEL_PROTOCOL_KEY:
                value = items.get(key)
                out[key] = _normalize_protocol_list(value)
            else:
                out[key] = normalize_capability_state(items.get(key), default="unknown")
        result[group] = out
    return result


def capability_state(
    caps: dict[str, Any] | None,
    group: str,
    key: str,
    *,
    default: str = "unknown",
) -> str:
    """Read one capability state with a conservative default."""
    if isinstance(caps, dict):
        items = caps.get(group)
        if isinstance(items, dict) and key in items:
            return normalize_capability_state(items.get(key), default=default)
    return default


def is_capability_available(state: str) -> bool:
    """Display/flow usable (including degraded and agent-defined)."""
    return state in {"supported", "degraded", "agent_defined"}


def is_capability_supported(state: str) -> bool:
    return state == "supported"


def capability_available(caps: dict[str, Any] | None, group: str, key: str) -> bool:
    return is_capability_available(capability_state(caps, group, key))
