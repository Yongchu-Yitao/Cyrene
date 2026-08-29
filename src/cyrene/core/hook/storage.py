"""Persistence protocol and serialization for queued Hook deliveries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .hook import CONTEXT_CHANGE, CONTEXT_USED, ContextUsed, Hook, HookEvent


@dataclass(frozen=True, slots=True)
class QueuedHookEvent:
    """One durable, ordered delivery to one Hook binding."""

    sequence: int
    hook: Hook
    event: HookEvent
    attempts: int


class HookPersistence(Protocol):
    def recover(self) -> None: ...

    def list_hooks(self) -> tuple[Hook, ...]: ...

    def save_hook(self, hook: Hook) -> None: ...

    def delete_hook(self, hook_id: str) -> bool: ...

    def claim_next(self) -> QueuedHookEvent | None: ...

    def complete(self, sequence: int) -> None: ...

    def fail(self, sequence: int, error: str) -> None: ...

    def block(self, sequence: int, error: str) -> None: ...

    def release(self, sequence: int) -> None: ...

    def requeue_blocked(self, plugin_id: str) -> int: ...

    def retry_failed(self) -> int: ...

    def has_work(self) -> bool: ...


def encode_event_payload(event: HookEvent) -> str:
    payload = event.payload
    if event.name == CONTEXT_CHANGE:
        data: Any = {
            "tree_id": payload.tree_id,
            "node_id": payload.node_id,
            "action": payload.action,
            "time": payload.time.isoformat(),
            "deleted_node_ids": list(payload.deleted_node_ids),
            "parent_id": payload.parent_id,
        }
    elif event.name == CONTEXT_USED:
        data = {
            "tree_id": payload.tree_id,
            "node_id": payload.node_id,
            "tokens": payload.tokens,
            "token_limit": payload.token_limit,
            "usage_ratio": payload.usage_ratio,
            "node_tokens": dict(payload.node_tokens),
            "time": payload.time.isoformat(),
        }
    else:
        data = payload
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def decode_event_payload(name: str, raw: str) -> Any:
    data = json.loads(raw)
    if name == CONTEXT_CHANGE:
        # Kept local to avoid a package import cycle during Context startup.
        from ..context.tree import ContextChange

        return ContextChange(
            tree_id=str(data["tree_id"]),
            node_id=str(data["node_id"]),
            action=str(data["action"]),
            time=datetime.fromisoformat(str(data["time"])),
            deleted_node_ids=tuple(str(item) for item in data.get("deleted_node_ids", ())),
            parent_id=str(data["parent_id"]) if data.get("parent_id") is not None else None,
        )
    if name == CONTEXT_USED:
        node_tokens = data.get("node_tokens")
        return ContextUsed(
            tree_id=str(data["tree_id"]),
            node_id=str(data["node_id"]),
            tokens=int(data["tokens"]),
            token_limit=int(data["token_limit"]),
            usage_ratio=float(data["usage_ratio"]),
            node_tokens={str(key): int(value) for key, value in (node_tokens or {}).items()},
            time=datetime.fromisoformat(str(data["time"])),
        )
    return data


def validate_hook_config(config: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    # Round-trip now so a registration cannot fail much later during persistence.
    return json.loads(
        json.dumps(normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    )
