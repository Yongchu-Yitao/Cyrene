"""Durable conversation-plan state owned by the control Plugin pack."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from cyrene.core.plugin import (
    PluginContext,
    plugin_session_state,
    with_plugin_session_state,
)

PACK_ID = "cyrene_control"
PLAN_SCHEMA_VERSION = 1
_PLAN_FILE_LIMIT = 250_000


def _valid_plan_document(plan: Mapping[str, Any]) -> bool:
    if not str(plan.get("planId") or "").strip() or not str(plan.get("title") or "").strip():
        return False
    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        return False
    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, Mapping):
            return False
        step_id = str(step.get("id") or "").strip()
        if not step_id or step_id in seen or not str(step.get("title") or "").strip():
            return False
        dependencies = step.get("dependsOn")
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list) or any(str(item) not in seen for item in dependencies):
            return False
        seen.add(step_id)
    return True


def _context_session_id(context: PluginContext) -> str:
    direct = context.data.get("session_id")
    run_context = context.data.get("run_context")
    nested = run_context.get("session_id") if isinstance(run_context, Mapping) else ""
    return str(direct or nested or context.tree_id or "").strip()


def plan_relative_path(session_id: str) -> str:
    identity = str(session_id or "").strip()
    if not identity:
        raise ValueError("session_id is required for plan file storage")
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", identity).strip("-.")[:64] or "chat"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f".cyrene/plan/{readable}-{digest}.json"


def plan_file_path(workspace: str | Path, session_id: str) -> Path:
    root = Path(workspace).expanduser().resolve()
    return root / plan_relative_path(session_id)


def read_plan_file(
    workspace: str | Path,
    session_id: str,
    *,
    expected_plan_id: str = "",
) -> dict[str, Any] | None:
    target = plan_file_path(workspace, session_id)
    try:
        if not target.is_file() or target.stat().st_size > _PLAN_FILE_LIMIT:
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    plan = deepcopy(dict(payload))
    plan_id = str(plan.get("planId") or "").strip()
    if expected_plan_id and plan_id != str(expected_plan_id):
        return None
    if not plan_id or not _valid_plan_document(plan):
        return None
    plan["planPath"] = plan_relative_path(session_id)
    return plan


def write_plan_file(
    workspace: str | Path,
    session_id: str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    target = plan_file_path(workspace, session_id)
    normalized = deepcopy(dict(plan))
    normalized["schemaVersion"] = PLAN_SCHEMA_VERSION
    normalized["planPath"] = plan_relative_path(session_id)
    if not _valid_plan_document(normalized):
        raise ValueError("invalid_plan")
    encoded = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > _PLAN_FILE_LIMIT:
        raise ValueError("plan_too_large")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return normalized


def current_plan(context: PluginContext) -> dict[str, Any] | None:
    if context.tree is None or not context.tree_id or not context.node_id:
        return None
    try:
        path = context.tree.get_path(context.tree_id, context.node_id)
    except Exception:
        return None
    if not path or not isinstance(path[0].value, Mapping):
        return None
    plan = plugin_session_state(path[0].value, PACK_ID).get("plan")
    durable = deepcopy(dict(plan)) if isinstance(plan, Mapping) else None
    session_id = _context_session_id(context)
    if context.workspace is not None and session_id:
        file_plan = read_plan_file(
            context.workspace,
            session_id,
            expected_plan_id=str((durable or {}).get("planId") or ""),
        )
        if file_plan is not None:
            return file_plan
    return durable


def persist_plan(context: PluginContext, plan: Mapping[str, Any]) -> bool:
    if context.tree is None or not context.tree_id or not context.node_id:
        return False
    try:
        path = context.tree.get_path(context.tree_id, context.node_id)
    except Exception:
        return False
    if not path or not isinstance(path[0].value, Mapping):
        return False
    root = path[0]
    previous = plugin_session_state(root.value, PACK_ID)
    normalized = deepcopy(dict(plan))
    session_id = _context_session_id(context)
    if context.workspace is not None and session_id:
        try:
            normalized = write_plan_file(context.workspace, session_id, normalized)
        except (OSError, ValueError):
            return False
    state = {
        **previous,
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan": normalized,
        "public_snapshot": {
            **(
                dict(previous.get("public_snapshot") or {})
                if isinstance(previous.get("public_snapshot"), Mapping)
                else {}
            ),
            "activePlan": normalized,
        },
    }
    context.tree.update_node(
        context.tree_id,
        root.id,
        with_plugin_session_state(root.value, PACK_ID, state),
    )
    return True


__all__ = [
    "PACK_ID",
    "PLAN_SCHEMA_VERSION",
    "current_plan",
    "persist_plan",
    "plan_file_path",
    "plan_relative_path",
    "read_plan_file",
    "write_plan_file",
]
