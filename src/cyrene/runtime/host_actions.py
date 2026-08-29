"""Durable, deferred lifecycle coordinator for Cyrene host actions."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from cyrene.core.plugin.execution import current_plugin_execution
from cyrene.plugins.native_runtime import run_context_value
from cyrene.config import DATA_DIR
from cyrene.runtime.host_bridge import HostBridgeError, call_host

_STATE_PATH = DATA_DIR / "app_control_actions.json"
_LOCK = threading.RLock()
_ALLOWED_ACTIONS = frozenset({"restart_backend", "restart_app", "quit", "update_install"})


def _emit_status(record: dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
        from cyrene.observability import debug

        loop.create_task(debug.publish_event({
            "type": "host_action_status",
            "action_id": str(record.get("action_id") or ""),
            "operation_id": str(record.get("operation_id") or ""),
            "action": str(record.get("action") or ""),
            "status": str(record.get("status") or ""),
        }))
    except RuntimeError:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> dict[str, Any]:
    try:
        value = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"actions": []}
    except (OSError, json.JSONDecodeError):
        return {"actions": []}


def _write(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


def list_actions(*, include_terminal: bool = False) -> list[dict[str, Any]]:
    with _LOCK:
        actions = [dict(item) for item in _read().get("actions", []) if isinstance(item, dict)]
    if not include_terminal:
        actions = [item for item in actions if item.get("status") not in {"completed", "failed", "cancelled", "expired", "interrupted"}]
    return actions


def schedule_action(
    action: str,
    *,
    idempotency_key: str,
    parameter_hash: str,
    expected_app_version: str,
    approval_receipt: str = "",
    revalidation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = str(action or "").strip()
    if normalized not in _ALLOWED_ACTIONS:
        raise ValueError("unsupported lifecycle action")
    execution = current_plugin_execution()
    context = execution.context if execution is not None else None

    def context_value(name: str) -> str:
        if context is None:
            return ""
        return str(run_context_value(context, name) or "")
    with _LOCK:
        state = _read()
        actions = state.setdefault("actions", [])
        existing = next((item for item in actions if item.get("idempotency_key") == idempotency_key), None)
        if existing:
            if existing.get("parameter_hash") != parameter_hash:
                raise ValueError("idempotency key was reused for a different lifecycle action")
            return dict(existing)
        record = {
            "action_id": f"host_action_{uuid.uuid4().hex}",
            "operation_id": "cyrene.app.lifecycle" if normalized != "update_install" else "cyrene.update.install",
            "action": normalized,
            "idempotency_key": idempotency_key,
            "parameter_hash": parameter_hash,
            "origin_session_id": context_value("session_id"),
            "origin_run_id": context_value("client_request_id"),
            "origin_round_id": context_value("round_id"),
            "approval_fingerprint": str(approval_receipt or parameter_hash),
            "required_host_kind": "electron",
            "expected_app_version": str(expected_app_version or ""),
            "revalidation": dict(revalidation or {}),
            "requested_at": _now(),
            "approved_at": _now(),
            "status": "waiting_for_run_finalization",
            "outcome": "",
        }
        actions.append(record)
        _write(state)
        _emit_status(record)
        return dict(record)


def cancel_action(action_id: str) -> dict[str, Any]:
    with _LOCK:
        state = _read()
        for item in state.get("actions", []):
            if item.get("action_id") != action_id:
                continue
            if item.get("status") in {"executing", "completed"}:
                raise ValueError("host action can no longer be cancelled")
            item["status"] = "cancelled"
            item["cancelled_at"] = _now()
            _write(state)
            _emit_status(item)
            return dict(item)
    raise LookupError("host action not found")


async def finalize_origin(
    session_id: str,
    round_id: str,
    *,
    origin_run_id: str = "",
) -> None:
    """Mark actions ready only after the final assistant reply is durable."""
    ready: list[dict[str, Any]] = []
    with _LOCK:
        state = _read()
        for item in state.get("actions", []):
            if item.get("status") != "waiting_for_run_finalization":
                continue
            if item.get("origin_session_id") != session_id:
                continue
            if round_id and item.get("origin_round_id") != round_id:
                continue
            if origin_run_id and item.get("origin_run_id") != origin_run_id:
                continue
            item["status"] = "queued"
            item["run_finalized_at"] = _now()
            ready.append(dict(item))
        if ready:
            _write(state)
    if not ready:
        return
    # Give the HTTP/SSE response time to flush the already persisted terminal
    # payload. Electron itself adds a final short delay before process action.
    await asyncio.sleep(1.5)
    for item in ready:
        await _execute(item)


async def _execute(item: dict[str, Any]) -> None:
    action_id = str(item.get("action_id") or "")
    action = str(item.get("action") or "")
    try:
        host_status = await call_host("host.status")
    except HostBridgeError as exc:
        _settle(action_id, "failed", str(exc))
        return
    if (
        host_status.get("ok") is False
        or host_status.get("hostKind") != item.get("required_host_kind", "electron")
    ):
        _settle(action_id, "failed", "Electron host is unavailable")
        return
    if str(host_status.get("appVersion") or "") != str(item.get("expected_app_version") or ""):
        _settle(action_id, "failed", "Cyrene version changed after approval")
        return
    host_payload = {
        "actionId": action_id,
        "action": action,
        "parameterHash": str(item.get("parameter_hash") or ""),
        "expectedAppVersion": str(item.get("expected_app_version") or ""),
    }
    if action == "update_install":
        expected = dict(item.get("revalidation") or {})
        from cyrene.runtime.updater import get_download_progress
        from cyrene.runtime.update_install import launch_update_restart
        progress = get_download_progress()
        if (
            not progress.get("done")
            or not progress.get("verified")
            or str(progress.get("actual_sha256") or "") != str(expected.get("sha256") or "")
            or int(progress.get("total") or progress.get("downloaded") or 0) != int(expected.get("size") or 0)
        ):
            _settle(action_id, "failed", "verified update package changed after approval")
            return
        try:
            prepared = await call_host(
                "lifecycle.execute_approved",
                {**host_payload, "phase": "prepare"},
            )
        except HostBridgeError as exc:
            _settle(action_id, "failed", str(exc))
            return
        if prepared.get("ok") is False:
            _settle(action_id, "failed", str(prepared.get("error") or "host rejected update preparation"))
            return
        ok, message, code, _status = launch_update_restart(progress)
        if not ok:
            _settle(action_id, "failed", f"{code}: {message}")
            return
    try:
        result = await call_host(
            "lifecycle.execute_approved",
            {
                **host_payload,
                "phase": "commit" if action == "update_install" else "execute",
            },
        )
        if result.get("ok") is False:
            _settle(action_id, "failed", str(result.get("error") or "host rejected action"))
        else:
            # The host has accepted the delayed process action.  Completion is
            # reconciled by the next process startup, never guessed here.
            _settle(action_id, "executing", str(result.get("summary") or "accepted by host"))
    except HostBridgeError as exc:
        _settle(action_id, "failed", str(exc))


def _settle(action_id: str, status: str, outcome: str) -> None:
    with _LOCK:
        state = _read()
        for item in state.get("actions", []):
            if item.get("action_id") == action_id:
                item["status"] = status
                item["outcome"] = outcome
                item[f"{status}_at"] = _now()
                _emit_status(item)
                break
        _write(state)


def reconcile_startup() -> None:
    """Never replay an old unfinalized quit/restart after process startup."""
    with _LOCK:
        state = _read()
        changed = False
        for item in state.get("actions", []):
            if item.get("status") == "executing":
                item["status"] = "completed"
                item["outcome"] = "host action was accepted before this startup"
                item["completed_at"] = _now()
                changed = True
            elif item.get("status") in {"waiting_for_run_finalization", "queued"}:
                item["status"] = "interrupted"
                item["outcome"] = "application restarted before host acceptance"
                changed = True
        if changed:
            _write(state)


__all__ = ["cancel_action", "finalize_origin", "list_actions", "reconcile_startup", "schedule_action"]
