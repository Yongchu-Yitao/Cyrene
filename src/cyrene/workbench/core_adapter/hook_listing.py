"""Read-only projection of persisted Hook bindings across Agent trees."""

from __future__ import annotations

import logging
import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from cyrene.core.hook import HOOK_EVENTS, Hook, HookEvent, refresh_active_hook_overrides
from cyrene.platform.settings_store import get as get_setting, set_ as set_setting

from .chat_runtime import workbench_agent_data_directory

logger = logging.getLogger(__name__)
_SYSTEM_HOOK_OVERRIDES_KEY = "system_hook_overrides"


def _override_key(hook_id: str, plugin_id: str) -> str:
    return f"{str(hook_id)}::{str(plugin_id)}"


def runtime_hook_override(hook_id: str, plugin_id: str) -> dict[str, Any] | None:
    raw = get_setting(_SYSTEM_HOOK_OVERRIDES_KEY, {})
    if not isinstance(raw, Mapping):
        return None
    value = raw.get(_override_key(hook_id, plugin_id))
    return dict(value) if isinstance(value, Mapping) else None


def _normalize_action(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"type": "plugin"}
    if not isinstance(raw, Mapping):
        raise ValueError("action must be an object")
    action_type = str(raw.get("type") or "plugin").strip().lower()
    if action_type == "plugin":
        return {"type": "plugin"}
    if action_type not in {"command", "script"}:
        raise ValueError("action type must be plugin, command, or script")
    target_key = "executable" if action_type == "command" else "path"
    target = str(raw.get(target_key) or "").strip()
    if not target:
        raise ValueError(f"action {target_key} is required")
    arguments = raw.get("args", [])
    if not isinstance(arguments, list) or not all(
        isinstance(item, str) for item in arguments
    ):
        raise ValueError("action args must be an array of strings")
    environment = raw.get("env", {})
    if not isinstance(environment, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in environment.items()
    ):
        raise ValueError("action env must contain string keys and values")
    try:
        timeout = float(raw.get("timeout_seconds", 10))
    except (TypeError, ValueError) as exc:
        raise ValueError("action timeout_seconds must be numeric") from exc
    if not 0.1 <= timeout <= 60:
        raise ValueError("action timeout_seconds must be between 0.1 and 60")
    return {
        "type": action_type,
        target_key: target,
        "args": list(arguments),
        "env": dict(environment),
        "timeout_seconds": timeout,
    }


def runtime_hook_action(hook: Hook):
    """Resolve an optional command/script action for a live system Hook."""

    override = runtime_hook_override(hook.id, hook.plugin_id)
    action = _normalize_action(override.get("action") if override else None)
    if action["type"] == "plugin":
        return None

    async def execute(event: HookEvent):
        from cyrene.plugins.builtin.cyrene_cli.hooks import (
            CliHookService,
            event_payload,
        )

        action_type = str(action["type"])
        target_key = "executable" if action_type == "command" else "path"
        executable_hook = {
            "id": hook.id,
            "name": hook.id,
            "event": hook.event,
            "failure_policy": hook.failure_policy,
            "timeout_seconds": action["timeout_seconds"],
            "runner": {
                "type": action_type,
                target_key: action[target_key],
                "args": list(action["args"]),
                "env": dict(action["env"]),
            },
        }
        return await CliHookService().execute(
            executable_hook,
            event_payload(event),
        )

    return execute


def runtime_hook_listing(db_path: str) -> list[dict[str, Any]]:
    """Return a newest-first, de-duplicated view of all persisted bindings."""

    normalized_db_path = str(db_path or "").strip()
    if not normalized_db_path:
        return []
    context_root = workbench_agent_data_directory(normalized_db_path) / "context"
    index_path = context_root / "index.sqlite3"
    if not index_path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{index_path}?mode=ro", uri=True) as index:
            rows = index.execute(
                "SELECT tree_id, database_path FROM context_tree_index "
                "ORDER BY created_at DESC, tree_id DESC"
            ).fetchall()
        if not rows:
            return []
    except sqlite3.Error:
        logger.debug("Unable to read runtime Hook bindings", exc_info=True)
        return []

    context_root_resolved = context_root.resolve()
    newest_tree_id = str(rows[0][0])
    bindings: dict[tuple[str, str, str], dict[str, Any]] = {}
    for tree_id_value, database_path in rows:
        tree_id = str(tree_id_value)
        tree_path = (context_root / str(database_path)).resolve()
        if context_root_resolved not in tree_path.parents or not tree_path.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{tree_path}?mode=ro", uri=True) as tree:
                tree.row_factory = sqlite3.Row
                records = tree.execute(
                    "SELECT hook_id, event, plugin_id, root_only, matcher, "
                    "failure_policy, config_json, enabled, created_at FROM hook_bindings "
                    "ORDER BY created_at, hook_id"
                ).fetchall()
        except sqlite3.Error:
            logger.debug("Unable to read Hook bindings from %s", tree_path, exc_info=True)
            continue
        for record in records:
            key = (
                str(record["hook_id"]),
                str(record["event"]),
                str(record["plugin_id"]),
            )
            existing = bindings.get(key)
            if existing is not None:
                existing["tree_count"] += 1
                continue
            bindings[key] = {
                "id": key[0],
                "event": key[1],
                "plugin_id": key[2],
                "root_only": bool(record["root_only"]),
                "matcher": str(record["matcher"]) if record["matcher"] is not None else "",
                "failure_policy": str(record["failure_policy"]),
                "config": json.loads(str(record["config_json"])),
                "enabled": bool(record["enabled"]),
                "created_at": str(record["created_at"]),
                "readonly": True,
                "source": "system",
                "tree_id": tree_id,
                "tree_count": 1,
                "current": tree_id == newest_tree_id,
            }
            override = runtime_hook_override(key[0], key[2])
            bindings[key]["action"] = _normalize_action(
                override.get("action") if override else None
            )
    return sorted(
        bindings.values(),
        key=lambda item: (
            not bool(item["current"]),
            str(item["event"]),
            str(item["id"]),
        ),
    )


def update_runtime_hook(
    db_path: str,
    hook_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist one acknowledged override across matching current/history trees."""

    if payload.get("acknowledge_risk") is not True:
        raise ValueError("system Hook risk acknowledgement is required")
    normalized_id = str(hook_id or "").strip()
    event = str(payload.get("event") or "").strip()
    plugin_id = str(payload.get("plugin_id") or "").strip()
    if not normalized_id or not event or not plugin_id:
        raise ValueError("hook id, event, and plugin id are required")
    current = next(
        (
            item for item in runtime_hook_listing(db_path)
            if item["id"] == normalized_id
            and item["event"] == event
            and item["plugin_id"] == plugin_id
        ),
        None,
    )
    if current is None:
        raise LookupError("system Hook was not found")

    new_hook_id = str(payload.get("new_hook_id", current["id"])).strip()
    new_event = str(payload.get("new_event", current["event"])).strip()
    new_plugin_id = str(payload.get("new_plugin_id", current["plugin_id"])).strip()
    if not new_hook_id or len(new_hook_id) > 200:
        raise ValueError("new hook id must contain 1-200 characters")
    if new_event not in HOOK_EVENTS:
        raise ValueError("new event is not supported")
    if not new_plugin_id or len(new_plugin_id) > 200:
        raise ValueError("new plugin id must contain 1-200 characters")
    created_at = str(payload.get("created_at", current["created_at"])).strip()
    try:
        datetime.fromisoformat(created_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("created_at must be an ISO 8601 date-time") from exc

    enabled = payload.get("enabled", current["enabled"])
    root_only = payload.get("root_only", current["root_only"])
    if type(enabled) is not bool or type(root_only) is not bool:
        raise ValueError("enabled and root_only must be booleans")
    matcher = payload.get("matcher", current["matcher"])
    if matcher is not None and not isinstance(matcher, str):
        raise ValueError("matcher must be a string or null")
    matcher = str(matcher).strip()[:200] if matcher is not None else None
    failure_policy = str(
        payload.get("failure_policy", current["failure_policy"])
    ).strip().lower()
    if failure_policy not in {"open", "block", "closed"}:
        raise ValueError("failure_policy must be open, block, or closed")
    if failure_policy == "block" and new_event != "PreToolUse":
        raise ValueError("only PreToolUse Hooks may block on failure")
    config = payload.get("config", current.get("config") or {})
    if not isinstance(config, Mapping):
        raise ValueError("config must be an object")
    normalized_config = json.loads(
        json.dumps(dict(config), ensure_ascii=False, allow_nan=False)
    )
    action = _normalize_action(payload.get("action", current.get("action")))
    override = {
        "id": new_hook_id,
        "event": new_event,
        "plugin_id": new_plugin_id,
        "enabled": enabled,
        "root_only": root_only,
        "matcher": matcher,
        "failure_policy": failure_policy,
        "config": normalized_config,
        "created_at": created_at,
        "action": action,
    }

    context_root = workbench_agent_data_directory(str(db_path or "")) / "context"
    context_root_resolved = context_root.resolve()
    index_path = context_root / "index.sqlite3"
    updated = 0
    with sqlite3.connect(f"file:{index_path}?mode=ro", uri=True) as index:
        rows = index.execute(
            "SELECT database_path FROM context_tree_index ORDER BY created_at DESC"
        ).fetchall()
    tree_paths = []
    for (database_path,) in rows:
        tree_path = (context_root / str(database_path)).resolve()
        if context_root_resolved not in tree_path.parents or not tree_path.is_file():
            continue
        tree_paths.append(tree_path)
        if new_hook_id != normalized_id:
            with sqlite3.connect(f"file:{tree_path}?mode=ro", uri=True) as tree:
                conflict = tree.execute(
                    "SELECT 1 FROM hook_bindings WHERE hook_id = ? LIMIT 1",
                    (new_hook_id,),
                ).fetchone()
            if conflict is not None:
                raise ValueError(f"Hook id already exists: {new_hook_id}")

    config_json = json.dumps(
        normalized_config, ensure_ascii=False, separators=(",", ":")
    )
    for tree_path in tree_paths:
        with sqlite3.connect(tree_path) as tree:
            record = tree.execute(
                "SELECT 1 FROM hook_bindings WHERE hook_id = ? AND event = ? AND plugin_id = ?",
                (normalized_id, event, plugin_id),
            ).fetchone()
            if record is None:
                continue
            if new_hook_id == normalized_id:
                tree.execute(
                    "UPDATE hook_bindings SET event = ?, plugin_id = ?, root_only = ?, "
                    "matcher = ?, failure_policy = ?, config_json = ?, enabled = ?, "
                    "created_at = ? WHERE hook_id = ? AND event = ? AND plugin_id = ?",
                    (
                        new_event, new_plugin_id, int(root_only), matcher,
                        failure_policy, config_json, int(enabled), created_at,
                        normalized_id, event, plugin_id,
                    ),
                )
                tree.execute(
                    "UPDATE hook_queue SET event = ? WHERE hook_id = ?",
                    (new_event, normalized_id),
                )
            else:
                tree.execute(
                    "INSERT INTO hook_bindings(hook_id, event, plugin_id, root_only, "
                    "matcher, failure_policy, config_json, enabled, created_at) "
                    "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ? FROM hook_bindings "
                    "WHERE hook_id = ? AND event = ? AND plugin_id = ?",
                    (
                        new_hook_id, new_event, new_plugin_id, int(root_only), matcher,
                        failure_policy, config_json, int(enabled), created_at,
                        normalized_id, event, plugin_id,
                    ),
                )
                tree.execute(
                    "UPDATE hook_queue SET hook_id = ?, event = ? WHERE hook_id = ?",
                    (new_hook_id, new_event, normalized_id),
                )
                tree.execute(
                    "DELETE FROM hook_bindings WHERE hook_id = ? AND event = ? AND plugin_id = ?",
                    (normalized_id, event, plugin_id),
                )
            updated += 1
    if updated < 1:
        raise LookupError("system Hook was not found")

    raw_overrides = get_setting(_SYSTEM_HOOK_OVERRIDES_KEY, {})
    overrides = dict(raw_overrides) if isinstance(raw_overrides, Mapping) else {}
    previous_override = runtime_hook_override(normalized_id, plugin_id) or {}
    origin_id = str(previous_override.get("origin_id") or normalized_id)
    origin_plugin_id = str(previous_override.get("origin_plugin_id") or plugin_id)
    override["origin_id"] = origin_id
    override["origin_plugin_id"] = origin_plugin_id
    for alias_id, alias_plugin_id in {
        (origin_id, origin_plugin_id),
        (normalized_id, plugin_id),
        (new_hook_id, new_plugin_id),
    }:
        overrides[_override_key(alias_id, alias_plugin_id)] = override
    set_setting(_SYSTEM_HOOK_OVERRIDES_KEY, overrides)
    live_updated = refresh_active_hook_overrides(normalized_id, plugin_id)
    refreshed = next(
        item for item in runtime_hook_listing(db_path)
        if item["id"] == new_hook_id
        and item["event"] == new_event
        and item["plugin_id"] == new_plugin_id
    )
    return {
        "ok": True,
        "hook": refreshed,
        "updated_bindings": updated,
        "updated_live_bindings": live_updated,
    }


__all__ = [
    "runtime_hook_listing",
    "runtime_hook_action",
    "runtime_hook_override",
    "update_runtime_hook",
]
