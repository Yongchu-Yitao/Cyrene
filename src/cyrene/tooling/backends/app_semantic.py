"""Semantic-only external application control with leased nodes and actions."""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cyrene.config import DATA_DIR
from cyrene.tooling.backends.app_use import electron_app_rpc, format_app_use_result

_AUDIT_PATH = Path(DATA_DIR) / "app_semantic_audit.jsonl"
_IDEMPOTENCY_PATH = Path(DATA_DIR) / "app_semantic_idempotency.json"
_SESSION_TTL_SECONDS = 5 * 60
_MAX_SNAPSHOTS = 4


@dataclass
class SemanticSession:
    session_id: str
    target: dict[str, Any]
    profile: dict[str, Any]
    last_used: float = field(default_factory=time.monotonic)
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[str, dict[str, Any]] = field(default_factory=dict)
    current_snapshot_id: str = ""


_SESSIONS: dict[str, SemanticSession] = {}


def _read_persistent_idempotency() -> dict[str, Any]:
    try:
        data = json.loads(_IDEMPOTENCY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _persist_idempotency(key: str, fingerprint: str, result: dict[str, Any]) -> None:
    try:
        _IDEMPOTENCY_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = _read_persistent_idempotency()
        data[key] = {"fingerprint": fingerprint, "result": result, "at": time.time()}
        if len(data) > 1000:
            data = dict(sorted(data.items(), key=lambda item: float(item[1].get("at") or 0))[-1000:])
        temporary = _IDEMPOTENCY_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_IDEMPOTENCY_PATH)
    except OSError:
        pass


def _error(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "type": kind, "message": message, **extra}


def _visual_handoff(result: dict[str, Any], target: dict[str, Any] | None = None) -> dict[str, Any]:
    platform = str((target or result.get("target") or {}).get("platform") or sys.platform)
    if platform.startswith("linux"):
        return {**result, "next_valid_actions": ["disconnect"]}
    return {
        **result,
        "alternate_scheme": {"tool": "app_use", "operation": "list_targets", "mode": "visual"},
        "next_valid_actions": ["disconnect", "switch:visual"],
    }


def _expire_sessions() -> None:
    now = time.monotonic()
    for session_id, session in list(_SESSIONS.items()):
        if now - session.last_used > _SESSION_TTL_SECONDS:
            _SESSIONS.pop(session_id, None)


def _session(session_id: Any) -> SemanticSession | None:
    _expire_sessions()
    session = _SESSIONS.get(str(session_id or "").strip())
    if session:
        session.last_used = time.monotonic()
    return session


def _opaque(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _semantic_target(target: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(target or {})
    return {
        key: source[key]
        for key in ("target_id", "app_name", "application_id", "pid", "window_title", "platform", "foreground", "minimized")
        if key in source
    }


def _action_family(kind: str) -> str:
    return {
        "press": "click", "select": "click", "toggle": "click",
        "double_click": "double_click", "set_value": "type", "type_text": "type",
        "scroll": "scroll", "drag": "drag",
    }.get(kind, "")


def _risk(kind: str, node: dict[str, Any]) -> str:
    text = " ".join(str(node.get(key) or "") for key in ("name", "description", "role")).lower()
    if any(word in text for word in ("delete", "remove", "purchase", "pay", "send", "submit")):
        return "R3"
    return "R2" if kind not in {"scroll"} else "R1"


def _page_snapshot(session: SemanticSession, snapshot_id: str, start: int, page_size: int) -> dict[str, Any]:
    record = session.snapshots[snapshot_id]
    nodes = record["nodes"]
    page_size = max(1, min(int(page_size or 120), 200))
    page = nodes[start:start + page_size]
    next_cursor = ""
    if start + page_size < len(nodes):
        next_cursor = json.dumps({"snapshot_id": snapshot_id, "offset": start + page_size}).encode().hex()
    coverage = dict(record["raw"].get("semantic_coverage") or {})
    return {
        "status": "success", "session_id": session.session_id, "snapshot_id": snapshot_id,
        "revision": record["revision"], "semantic_profile": session.profile, "target": session.target,
        "semantic_coverage": coverage,
        "visual_recommended": coverage.get("visual_recommended") is True,
        "nodes": page, "total_nodes": len(nodes), "cursor": next_cursor or None,
        "truncated": bool(next_cursor) or record["raw"].get("truncated") is True,
    }


def _public_snapshot(session: SemanticSession, raw: dict[str, Any], *, page_size: int = 120) -> dict[str, Any]:
    revision = int(raw.get("snapshot_revision") or 0)
    snapshot_id = f"app_snapshot_{secrets.token_hex(12)}"
    raw_nodes = list(raw.get("nodes") or [])
    ref_to_node_id = {
        str(node.get("ref") or ""): _opaque("node", session.session_id, node.get("ref"))
        for node in raw_nodes if node.get("ref")
    }
    nodes: list[dict[str, Any]] = []
    actions: dict[str, dict[str, Any]] = {}
    refs: dict[str, str] = {}
    for raw_node in raw_nodes:
        ref = str(raw_node.get("ref") or "")
        if not ref:
            continue
        node_id = ref_to_node_id[ref]
        refs[node_id] = ref
        public_node = {
            "node_id": node_id,
            "parent_node_id": ref_to_node_id.get(str(raw_node.get("parent_ref") or "")),
            "role": str(raw_node.get("role") or "unknown"),
            "name": str(raw_node.get("name") or ""),
            "description": str(raw_node.get("description") or ""),
            "value": raw_node.get("value"),
            "enabled": raw_node.get("enabled", True),
            "child_count": max(0, int(raw_node.get("childCount") or 0)),
            "expandable": int(raw_node.get("childCount") or 0) > 0,
            "actions": [],
        }
        native_actions = list(raw_node.get("actions") or [])
        for kind in native_actions:
            kind = str(kind)
            family = _action_family(kind)
            if not family:
                continue
            action_id = _opaque("action", session.session_id, snapshot_id, revision, ref, kind)
            descriptor = {
                "action_id": action_id,
                "kind": kind,
                "tool": f"AppUI{family.title().replace('_', '')}",
                "risk": _risk(kind, raw_node),
            }
            public_node["actions"].append(descriptor)
            actions[action_id] = {"node_id": node_id, "ref": ref, "kind": kind, "family": family}
        nodes.append(public_node)
    record = {"revision": revision, "refs": refs, "actions": actions, "nodes": nodes, "raw": raw}
    session.snapshots[snapshot_id] = record
    session.current_snapshot_id = snapshot_id
    while len(session.snapshots) > _MAX_SNAPSHOTS:
        session.snapshots.pop(next(iter(session.snapshots)))
    session.profile = dict(raw.get("semantic_profile") or session.profile)
    return _page_snapshot(session, snapshot_id, 0, page_size)


async def _take_snapshot(session: SemanticSession, args: dict[str, Any]) -> dict[str, Any]:
    cursor = str(args.get("cursor") or "")
    if cursor:
        try:
            decoded = json.loads(bytes.fromhex(cursor).decode())
            snapshot_id = str(decoded["snapshot_id"])
            start = int(decoded["offset"])
            if snapshot_id not in session.snapshots or start < 0:
                raise ValueError
            return _page_snapshot(session, snapshot_id, start, int(args.get("page_size") or 120))
        except Exception:
            return _error("invalid_cursor", "The semantic snapshot cursor is invalid or expired.")
    raw = await electron_app_rpc("call", {
        "session_id": session.session_id,
        "capability": "snapshot",
        "parameters": {
            "max_nodes": max(1, min(int(args.get("max_nodes") or 200), 500)),
            "max_depth": max(1, min(int(args.get("max_depth") or 12), 24)),
        },
    })
    if raw.get("status") != "success":
        if raw.get("type") in {"provider_error", "unsupported_capability", "permission_required"}:
            return _visual_handoff(raw, session.target)
        return raw
    result = _public_snapshot(session, raw, page_size=int(args.get("page_size") or 120))
    if result.get("semantic_profile", {}).get("status") in {"unavailable", "provider_error", "permission_required"}:
        return _visual_handoff(result, session.target)
    if result.get("visual_recommended") is True:
        return _visual_handoff(result, session.target)
    return result


async def execute_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    operation = str(args.get("operation") or "snapshot")
    valid_operations = {"list_targets", "connect", "snapshot", "reprobe", "status", "disconnect"}
    if operation not in valid_operations:
        return _error(
            "invalid_arguments",
            "operation must be list_targets, connect, snapshot, reprobe, status, or disconnect",
        )
    if operation == "list_targets":
        result = await electron_app_rpc("list_targets", {})
        if isinstance(result.get("targets"), list):
            result = {**result, "targets": [_semantic_target(item) for item in result["targets"]]}
        return result
    if operation == "connect":
        result = await electron_app_rpc("connect", {
            "target_id": str(args.get("target_id") or ""),
            "parameters": {"mode": "semantic", "focus_policy": "never", "selection": str(args.get("selection") or "")},
        })
        if result.get("status") == "success":
            session_id = str(result.get("session_id") or "")
            _SESSIONS[session_id] = SemanticSession(
                session_id=session_id,
                target=_semantic_target(result.get("target")),
                profile=dict(result.get("semantic_profile") or {}),
            )
            result = {**result, "target": _SESSIONS[session_id].target}
            if _SESSIONS[session_id].profile.get("status") in {"unavailable", "provider_error", "permission_required"}:
                result = _visual_handoff(result, _SESSIONS[session_id].target)
        elif result.get("type") in {"provider_error", "unsupported_mode", "permission_required"}:
            result = _visual_handoff(result)
        return result
    session_id = str(args.get("session_id") or "").strip()
    if not session_id:
        return _error(
            "invalid_arguments",
            f"session_id is required for AppUISnapshot operation={operation}.",
            missing_arguments=["session_id"],
            next_valid_actions=["retry_with_session_id"],
        )
    session = _session(session_id)
    if not session:
        return _error("stale_session", "The semantic App Use session expired; reconnect before continuing.")
    if operation in {"snapshot", "reprobe"}:
        return await _take_snapshot(session, args)
    if operation == "status":
        result = await electron_app_rpc("status", {"session_id": session.session_id})
        return {**result, "semantic_profile": session.profile}
    if operation == "disconnect":
        _SESSIONS.pop(session.session_id, None)
        return await electron_app_rpc("disconnect", {"session_id": session.session_id})
    raise AssertionError(f"Unhandled AppUISnapshot operation: {operation}")


def _lease(args: dict[str, Any], family: str) -> tuple[SemanticSession | None, dict[str, Any] | None, dict[str, Any] | None]:
    session = _session(args.get("session_id"))
    if not session:
        return None, None, _error("stale_session", "The semantic App Use session expired.")
    snapshot_id = str(args.get("snapshot_id") or "")
    snapshot = session.snapshots.get(snapshot_id)
    if not snapshot:
        return session, None, _error("stale_snapshot", "The snapshot lease expired; take a fresh AppUISnapshot.")
    if session.current_snapshot_id != snapshot_id:
        return session, None, _error("stale_snapshot", "A newer semantic snapshot invalidated this action lease.")
    if int(args.get("revision") or -1) != snapshot["revision"]:
        return session, None, _error("revision_conflict", "The supplied revision does not match the leased snapshot.")
    action = snapshot["actions"].get(str(args.get("action_id") or ""))
    if not action or action["node_id"] != str(args.get("node_id") or "") or action["family"] != family:
        return session, None, _error("invalid_action_lease", "node_id and action_id must come from the same current snapshot and tool family.")
    return session, action, None


def _audit(payload: dict[str, Any]) -> None:
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        safe = {key: value for key, value in payload.items() if key not in {"text", "value"}}
        with _AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": time.time(), **safe}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _effect_verified(
    snapshot: dict[str, Any], action: dict[str, Any], args: dict[str, Any], result: dict[str, Any],
) -> bool:
    if result.get("status") != "success":
        return False
    before_nodes = list((snapshot.get("raw") or {}).get("nodes") or [])
    after_nodes = list((result.get("verification") or {}).get("nodes") or [])
    if not after_nodes:
        return False
    before = next((node for node in before_nodes if str(node.get("ref") or "") == action["ref"]), None)
    after = next((node for node in after_nodes if str(node.get("ref") or "") == action["ref"]), None)
    if action["family"] == "type":
        if not after:
            return False
        observed = str(after.get("value") or "")
        requested = str(args.get("text") or "")
        if action["kind"] == "set_value" or bool(args.get("replace", True)):
            return observed == requested
        return observed.endswith(requested)
    if before and not after:
        return True
    fields = ("role", "name", "description", "value", "enabled", "selected", "expanded", "checked", "actions")
    if before and after and any(before.get(field) != after.get(field) for field in fields):
        return True
    def tree_fingerprint(nodes: list[dict[str, Any]]) -> str:
        normalized = [
            [node.get(field) for field in ("ref", *fields)]
            for node in nodes
        ]
        return hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    return bool(before_nodes) and tree_fingerprint(before_nodes) != tree_fingerprint(after_nodes)


async def execute_action(family: str, args: dict[str, Any]) -> dict[str, Any]:
    reason = str(args.get("reason") or "").strip()
    key = str(args.get("idempotency_key") or "").strip()
    if not reason or not key:
        return _error("invalid_arguments", "reason and idempotency_key are required for semantic actions.")
    session, action, failure = _lease(args, family)
    if failure:
        return failure
    assert session is not None and action is not None
    fingerprint = hashlib.sha256(json.dumps({
        "session_id": session.session_id, "snapshot_id": args.get("snapshot_id"),
        "revision": args.get("revision"), "node_id": args.get("node_id"),
        "action_id": args.get("action_id"), "family": family,
        "text": args.get("text"), "direction": args.get("direction"), "amount": args.get("amount"),
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if key in session.idempotency:
        prior = session.idempotency[key]
        if prior.get("fingerprint") != fingerprint:
            return _error("idempotency_conflict", "This idempotency key was already used for a different semantic action.")
        return {**prior["result"], "idempotent_replay": True}
    persisted = _read_persistent_idempotency().get(key)
    if isinstance(persisted, dict):
        if persisted.get("fingerprint") != fingerprint:
            return _error("idempotency_conflict", "This idempotency key was already used for a different semantic action.")
        prior = persisted.get("result")
        if isinstance(prior, dict):
            session.idempotency[key] = {"fingerprint": fingerprint, "result": prior}
            return {**prior, "idempotent_replay": True, "persistent_replay": True}
    parameters: dict[str, Any] = {"ref": action["ref"]}
    capability = {"double_click": "semantic_double_click", "drag": "semantic_drag"}.get(action["kind"], action["kind"])
    if family == "type":
        parameters["value" if capability == "set_value" else "text"] = str(args.get("text") or "")
        if capability == "type_text":
            parameters["replace"] = bool(args.get("replace", True))
    elif family == "scroll":
        parameters.update({"direction": str(args.get("direction") or "down"), "amount": int(args.get("amount") or 1)})
    result = await electron_app_rpc("call", {
        "session_id": session.session_id, "capability": capability, "parameters": parameters,
    })
    snapshot = session.snapshots[str(args.get("snapshot_id") or "")]
    verified = _effect_verified(snapshot, action, args, result)
    normalized = {
        **result,
        "session_id": session.session_id,
        "snapshot_id": str(args.get("snapshot_id") or ""),
        "revision": int(args.get("revision") or 0),
        "node_id": str(args.get("node_id") or ""),
        "action_id": str(args.get("action_id") or ""),
        "effect_verified": verified,
        "verification": {"status": "success" if verified else "uncertain", "effect_verified": verified},
    }
    if result.get("status") == "success" and not normalized["effect_verified"]:
        normalized["status"] = "uncertain"
    if normalized.get("status") == "error" and normalized.get("type") in {
        "provider_error", "unsupported_capability", "unsupported_action", "permission_required",
    } and not normalized.get("action_may_have_run"):
        normalized = _visual_handoff(normalized, session.target)
    session.idempotency[key] = {"fingerprint": fingerprint, "result": normalized}
    _persist_idempotency(key, fingerprint, normalized)
    _audit({
        "session_id": session.session_id, "snapshot_id": args.get("snapshot_id"),
        "revision": args.get("revision"), "node_id": args.get("node_id"), "action_id": args.get("action_id"),
        "capability": capability, "reason": reason, "idempotency_key": key, "status": normalized.get("status"),
    })
    return normalized


async def execute_inspect(args: dict[str, Any]) -> dict[str, Any]:
    session = _session(args.get("session_id"))
    if not session:
        return _error("stale_session", "The semantic App Use session expired.")
    snapshot = session.snapshots.get(str(args.get("snapshot_id") or ""))
    if not snapshot or int(args.get("revision") or -1) != snapshot["revision"]:
        return _error("stale_snapshot", "Take a fresh AppUISnapshot before inspecting.")
    ref = snapshot["refs"].get(str(args.get("node_id") or ""))
    if not ref:
        return _error("stale_node", "The node is not leased by this snapshot.")
    result = await electron_app_rpc("call", {
        "session_id": session.session_id, "capability": "inspect",
        "parameters": {
            "ref": ref,
            "max_nodes": max(1, min(int(args.get("max_nodes") or 200), 500)),
            "max_depth": max(1, min(int(args.get("max_depth") or 12), 24)),
        },
    })
    if result.get("status") != "success":
        if result.get("type") in {"provider_error", "unsupported_capability", "permission_required"}:
            return _visual_handoff(result, session.target)
        return result
    public = _public_snapshot(session, result, page_size=int(args.get("page_size") or 80))
    if public.get("visual_recommended") is True:
        return _visual_handoff(public, session.target)
    return public


def format_result(result: dict[str, Any]) -> str:
    return format_app_use_result(result)


__all__ = ["execute_action", "execute_inspect", "execute_snapshot", "format_result"]
