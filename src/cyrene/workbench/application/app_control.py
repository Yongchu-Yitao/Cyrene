"""Shared policy, result, idempotency and audit helpers for Cyrene tools."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from cyrene.core.plugin.execution import current_plugin_execution
from cyrene.plugins.native_runtime import (
    plugin_localized,
    publish_runtime_event,
    run_context_value,
)
from cyrene.config import DATA_DIR
from cyrene.localization import localized
from cyrene.workbench.application.app_operations import OPERATION_BY_ID

_AUDIT_PATH = DATA_DIR / "app_control_audit.jsonl"
_IDEMPOTENCY_PATH = DATA_DIR / "app_control_idempotency.json"
_STATE_LOCK = threading.RLock()

_ERROR_SUMMARIES = {
    "idempotency_required": (
        "An idempotency key is required.",
        "必须提供幂等键。",
    ),
    "idempotency_conflict": (
        "The idempotency key was reused with different arguments.",
        "同一幂等键被用于不同的参数。",
    ),
    "backup_not_found": ("The backup was not found.", "未找到备份。"),
    "backup_error": ("The backup operation failed.", "备份操作失败。"),
    "revision_conflict": (
        "Settings changed concurrently; read them again before retrying.",
        "设置已被并发修改，请重新读取后再试。",
    ),
    "action_not_available": (
        "The requested action is not available on the current component.",
        "当前组件不支持所请求的操作。",
    ),
    "gesture_not_available": (
        "The requested gesture is not available on the current component.",
        "当前组件不支持所请求的手势。",
    ),
    "requires_capability": (
        "This action must use its typed capability.",
        "此操作必须通过对应的类型化能力执行。",
    ),
    "invalid_action_risk": (
        "The component declared an invalid action risk.",
        "组件声明了无效的操作风险等级。",
    ),
    "stale_snapshot": (
        "The current UI snapshot is stale; read it again before retrying.",
        "当前界面快照已过期，请重新读取后再试。",
    ),
    "chat_error": ("The chat operation failed.", "对话操作失败。"),
    "project_error": ("The project operation failed.", "项目操作失败。"),
    "lifecycle_error": (
        "The application lifecycle operation failed.",
        "应用生命周期操作失败。",
    ),
    "update_error": ("The update operation failed.", "更新操作失败。"),
    "invalid_session_composer": (
        "The session composer request is invalid.",
        "会话编辑器请求无效。",
    ),
    "self_session_submit_forbidden": (
        "A session cannot submit this message to itself.",
        "会话不能向自身提交此消息。",
    ),
    "session_dispatch_error": (
        "The session message could not be sent.",
        "无法发送会话消息。",
    ),
    "surface_error": (
        "The current UI operation failed.",
        "当前界面操作失败。",
    ),
}

_SUMMARY_TRANSLATIONS = {
    "Backups listed.": "已列出备份。",
    "Backup validation completed.": "备份验证已完成。",
    "Cyrene backup created.": "Cyrene 备份已创建。",
    "Cyrene backup restore completed.": "Cyrene 备份恢复已完成。",
    "Cyrene backup restore failed.": "Cyrene 备份恢复失败。",
    "Cyrene backup deleted.": "Cyrene 备份已删除。",
    "Projects listed.": "已列出项目。",
    "Project read.": "已读取项目。",
    "Chats listed.": "已列出对话。",
    "Chat read.": "已读取对话。",
    "Chat groups listed.": "已列出对话分组。",
    "Cyrene settings read.": "已读取 Cyrene 设置。",
    "Cyrene settings schema described.": "已读取 Cyrene 设置结构。",
    "Pending host actions read.": "已读取待处理的宿主操作。",
    "Pending host action cancelled.": "已取消待处理的宿主操作。",
    "Host action scheduled after final reply persistence.": "宿主操作将在最终回复保存后执行。",
    "Current UI action completed.": "当前界面操作已完成。",
    "Current UI action was rejected.": "当前界面操作被拒绝。",
    "Current UI snapshot could not be read.": "无法读取当前界面快照。",
    "Update check completed.": "更新检查已完成。",
    "Update state read.": "已读取更新状态。",
    "Verified update package downloaded.": "已下载并验证更新包。",
    "Verified update installation scheduled after final reply persistence.": "已验证的更新将在最终回复保存后安装。",
    "Cyrene application status read.": "已读取 Cyrene 应用状态。",
}


def _active_context():
    execution = current_plugin_execution()
    return execution.context if execution is not None else None


def _text(english: str, chinese: str) -> str:
    context = _active_context()
    if context is not None:
        return plugin_localized(context, english, chinese)
    return localized(english, chinese)


def _public_summary(status: str, summary: str, error_code: str) -> str:
    normalized_status = str(status or "error")
    normalized_code = str(error_code or "")
    if normalized_status in {"error", "unsupported"}:
        pair = _ERROR_SUMMARIES.get(normalized_code)
        if pair is None:
            pair = (
                "The Cyrene operation failed.",
                "Cyrene 操作失败。",
            )
        return _text(pair[0], pair[1])

    english = str(summary or "")
    chinese = _SUMMARY_TRANSLATIONS.get(english)
    if chinese is None:
        dynamic = (
            ("Project operation ", "项目操作 "),
            ("Chat operation ", "对话操作 "),
            ("Current window action ", "当前窗口操作 "),
        )
        for prefix, translated in dynamic:
            if english.startswith(prefix) and english.endswith(" completed."):
                operation = english[len(prefix):-len(" completed.")]
                chinese = f"{translated}{operation} 已完成。"
                break
    return _text(english, chinese) if chinese is not None else english


def _localize_envelope(result: dict[str, Any]) -> dict[str, Any]:
    localized_result = dict(result)
    localized_result["summary"] = _public_summary(
        str(localized_result.get("status") or "error"),
        str(localized_result.get("summary") or ""),
        str(localized_result.get("error_code") or ""),
    )
    return localized_result


def _context_value(name: str, default: Any = "") -> Any:
    context = _active_context()
    if context is None:
        return default
    return run_context_value(context, name, default)


async def _publish(event: dict[str, Any]) -> None:
    context = _active_context()
    if context is not None:
        await publish_runtime_event(context, event)


def canonical_hash(operation_id: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        {"operation_id": operation_id, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def envelope(
    status: str,
    operation_id: str,
    summary: str,
    **extra: Any,
) -> dict[str, Any]:
    error_code = str(extra.get("error_code") or "")
    return {
        "status": status,
        "operation_id": operation_id,
        "summary": _public_summary(status, summary, error_code),
        "revision": extra.pop("revision", None),
        "apply_mode": extra.pop("apply_mode", "immediate"),
        "restart_required": bool(extra.pop("restart_required", False)),
        "action_id": str(extra.pop("action_id", "") or ""),
        "audit_id": str(extra.pop("audit_id", "") or ""),
        "effects": extra.pop("effects", []),
        "next_valid_actions": extra.pop("next_valid_actions", []),
        **extra,
    }


def audit(
    operation_id: str,
    arguments: dict[str, Any],
    *,
    status: str,
    risk: str,
    diff: dict[str, Any] | None = None,
    error_code: str = "",
) -> str:
    argument_hash = canonical_hash(operation_id, arguments)
    audit_id = f"audit_{uuid.uuid4().hex}"
    record = {
        "audit_id": audit_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation_id": operation_id,
        "schema_version": 1,
        "actor_type": str(_context_value("caller", "main_agent") or "main_agent"),
        "actor_id": str(_context_value("agent_id", "main") or "main"),
        "conversation_source": str(_context_value("conversation_source") or ""),
        "session_id": str(_context_value("session_id") or ""),
        "round_id": str(
            _context_value("round_id") or _context_value("run_id") or ""
        ),
        "argument_hash": argument_hash,
        "arguments": _redact(arguments),
        "diff": _redact(diff or {}),
        "risk": risk,
        "status": status,
        "error_code": str(error_code or ""),
        "decision_source": "plugin_pre_tool_review",
        "delegation_receipt": "",
    }
    with _STATE_LOCK:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return audit_id


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("secret", "token", "password", "api_key", "private_key", "auth")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + "…"
    return value


def _load_idempotency() -> dict[str, Any]:
    try:
        value = json.loads(_IDEMPOTENCY_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def replay_idempotent(operation_id: str, key: str, argument_hash: str) -> dict[str, Any] | None:
    if not key:
        return None
    with _STATE_LOCK:
        entry = _load_idempotency().get(f"{operation_id}:{key}")
    if not isinstance(entry, dict):
        return None
    if entry.get("argument_hash") != argument_hash:
        return envelope(
            "error",
            operation_id,
            localized(
                "The idempotency key was reused with different arguments.",
                "同一幂等键被用于不同的参数。",
            ),
            error_code="idempotency_conflict",
        )
    result = entry.get("result")
    return _localize_envelope(dict(result)) if isinstance(result, dict) else None


def remember_idempotent(operation_id: str, key: str, argument_hash: str, result: dict[str, Any]) -> None:
    if not key:
        return
    with _STATE_LOCK:
        state = _load_idempotency()
        state[f"{operation_id}:{key}"] = {
            "argument_hash": argument_hash,
            "result": _redact(result),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _IDEMPOTENCY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _IDEMPOTENCY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_IDEMPOTENCY_PATH)


async def authorize(
    operation_id: str,
    arguments: dict[str, Any],
    *,
    reason: str,
) -> str | None:
    """Apply deterministic application guards after Plugin PreToolUse review.

    The Plugin Runtime's PreToolUse Hook is the only semantic approval path.
    This function deliberately does not create pending questions, mint
    ContextVar receipts, or call a second reviewer. It only enforces invariant
    application boundaries that no model decision may widen.

    """

    spec = OPERATION_BY_ID.get(operation_id)
    if spec is None:
        return localized(
            "Tool unavailable: unclassified Cyrene operation.",
            "工具不可用：无法识别此 Cyrene 操作。",
        )
    if _active_context() is None:
        return localized(
            "Tool unavailable: Cyrene operations require the Plugin Runtime.",
            "工具不可用：Cyrene 操作需要插件运行时。",
        )

    fingerprint = canonical_hash(operation_id, arguments)
    await _publish({
        "type": "cyrene_operation_requested",
        "operation_id": operation_id,
        "argument_hash": fingerprint,
        "risk": spec.risk,
    })
    agent_id = str(_context_value("agent_id", "main") or "main")
    caller = str(_context_value("caller", "main_agent") or "main_agent")
    source = str(_context_value("conversation_source") or "")
    if agent_id != "main" or caller not in {"main_agent", "main"}:
        return localized(
            "Tool unavailable: Cyrene self-management is main-agent only.",
            "工具不可用：仅主智能体可管理 Cyrene。",
        )
    if "main" not in spec.actors:
        return localized(
            "Tool unavailable: this operation cannot be called by an agent.",
            "工具不可用：智能体不能调用此操作。",
        )
    if spec.risk == "R4" or spec.exposure == "forbidden":
        return localized(
            "Tool unavailable: this Cyrene self-management operation is permanently forbidden.",
            "工具不可用：此 Cyrene 管理操作被永久禁止。",
        )
    if spec.risk != "R0" and not str(reason or "").strip():
        return localized(
            "Tool unavailable: this change requires a reason grounded in the user's request.",
            "工具不可用：此更改需要基于用户请求的理由。",
        )
    if spec.risk == "R1" and source not in {"desktop_local", "webui"}:
        return localized(
            "Tool unavailable: current-app UI changes require a local Workbench turn.",
            "工具不可用：更改当前应用界面需要本地工作台对话。",
        )
    if spec.risk in {"R2", "R3"} and source != "desktop_local":
        return localized(
            "Tool unavailable: privileged Cyrene operations require a local desktop Workbench turn.",
            "工具不可用：Cyrene 特权操作需要本地桌面工作台对话。",
        )
    await _publish({
        "type": "cyrene_operation_approved",
        "operation_id": operation_id,
        "argument_hash": fingerprint,
        "risk": spec.risk,
        "decision_source": "plugin_pre_tool_review",
        "round_id": str(
            _context_value("round_id") or _context_value("run_id") or ""
        ),
    })
    return None


async def publish_result(result: dict[str, Any]) -> None:
    """Publish a secret-free status summary for local observability."""
    status = str(result.get("status") or "error")
    await _publish({
        "type": (
            "cyrene_operation_completed"
            if status in {"success", "scheduled"}
            else "cyrene_operation_failed"
        ),
        "operation_id": str(result.get("operation_id") or ""),
        "status": status,
        "audit_id": str(result.get("audit_id") or ""),
        "action_id": str(result.get("action_id") or ""),
        "revision": result.get("revision"),
        "error_code": str(result.get("error_code") or ""),
    })


def authorization_decision(operation_id: str, arguments: dict[str, Any]) -> dict[str, str]:
    """Return the deterministic receipt produced by Plugin PreToolUse review."""

    fingerprint = canonical_hash(operation_id, arguments)
    return {
        "source": "plugin_pre_tool_review",
        "receipt": fingerprint,
    }


__all__ = [
    "audit", "authorization_decision", "authorize", "canonical_hash", "envelope", "publish_result", "remember_idempotent",
    "replay_idempotent",
]
