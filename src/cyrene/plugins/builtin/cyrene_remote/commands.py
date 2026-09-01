"""Typed Remote Plugin command application adapter.

The encrypted gateway calls this adapter after trust, capability, project
scope, replay, and idempotency checks.  It deliberately maps a fixed command
enum to existing Workbench application services; it never exposes arbitrary
HTTP routes, native tools, or Python calls. Project-scoped Shell sessions are
exposed only through a dedicated, ownership-checked command family.
"""

from __future__ import annotations

import asyncio
import base64
import getpass
import hashlib
import json
import logging
import math
import mimetypes
import os
import platform
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
from cyrene.core.plugin import (
    PluginContext,
    TOOLBOX_PLUGIN_NAME,
    application_plugin_scope,
    application_plugin_service,
)
from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory
from cyrene.config import WORKSPACE_DIR
from cyrene.localization import localized
from cyrene.observability.context_trace import approx_token_count
from .control import (
    DIRECT_PAIRING_PORT,
    REMOTE_CAPABILITIES,
    REMOTE_PLUGIN_PACK_PREFIX,
    REMOTE_PROTOCOL_VERSION,
    RemoteControlStore,
    RemoteGateway,
    register_remote_gateway,
    remote_plugin_pack_ids,
    unregister_remote_gateway,
)
from .pairing import DirectPairingServer
from .workspace import RemoteJobManager, RemoteWorkspaceFiles
from cyrene.platform.settings_store import get_all as get_web_settings
from cyrene.platform.settings_store import set_ as set_setting
from cyrene.workbench.projects import project_runtime
from cyrene.workbench.projects import project_repository
from cyrene.workbench.workspaces.workspace_changes import (
    get_chat_file_change,
    list_chat_change_sets,
)
from cyrene.workbench.control.control_services import ControlServiceError
from cyrene.workbench.control.control_event_projection import (
    public_run_event as public_remote_event,
)

_DEFAULT_TRANSFER_CHUNK_BYTES = 512 * 1024
_MAX_TRANSFER_CHUNK_BYTES = 1024 * 1024
_THUMBNAIL_MAX_DIMENSION = 960
_THUMBNAIL_WEBP_QUALITY = 72
_REMOTE_SETTING_SECTIONS = (
    {"id": "general", "label": "Timezone", "label_zh": "时区"},
    {"id": "agent", "label": "Agent", "label_zh": "Agent"},
    {"id": "context", "label": "Context & permissions", "label_zh": "上下文与权限"},
    {"id": "models", "label": "Models", "label_zh": "模型"},
    {"id": "execution", "label": "Sub-agent execution", "label_zh": "子 Agent 执行"},
    {"id": "discussion", "label": "Multi-agent discussion", "label_zh": "多 Agent 讨论"},
    {"id": "skills", "label": "Skills", "label_zh": "技能"},
    {"id": "channels", "label": "Channels", "label_zh": "频道"},
    {"id": "updates", "label": "Updates", "label_zh": "更新"},
    {"id": "budget", "label": "Budget", "label_zh": "预算"},
    {"id": "plugin_packs", "label": "Plugin packs", "label_zh": "插件包"},
    {"id": "plugins", "label": "Standalone Plugins", "label_zh": "独立插件"},
)
logger = logging.getLogger(__name__)

_REMOTE_ERROR_MESSAGES: dict[str, tuple[str, str]] = {
    "artifact_not_found": ("Artifact not found.", "未找到产物。"),
    "artifact_unavailable": ("The artifact is unavailable.", "产物不可用。"),
    "attachment_not_found": ("Attachment not found.", "未找到附件。"),
    "attachment_unavailable": ("The attachment is unavailable.", "附件不可用。"),
    "attachment_variant_invalid": ("The attachment variant is invalid.", "附件版本无效。"),
    "change_not_found": ("Workspace change not found.", "未找到工作区变更。"),
    "goal_not_found": ("Goal not found.", "未找到目标。"),
    "chat_interrupt_timeout": ("The chat is still stopping. Try again shortly.", "对话仍在停止中，请稍后重试。"),
    "invalid_status_transition": ("This status change is not allowed.", "不允许进行此状态变更。"),
    "model_plugin_unavailable": ("The model Plugin is unavailable.", "模型插件不可用。"),
    "plugin_host_unavailable": ("The Plugin application host is unavailable.", "插件应用宿主不可用。"),
    "remote_authorization_invalid": ("Remote authorization is invalid.", "远程授权无效。"),
    "remote_command_failed": ("The remote command failed.", "远程命令执行失败。"),
    "remote_command_in_progress": ("An identical remote command is still running.", "相同的远程命令仍在执行。"),
    "remote_command_invalid": ("The remote command is invalid.", "远程命令无效。"),
    "remote_command_unsupported": ("This remote command is not supported.", "不支持此远程命令。"),
    "remote_file_channel_required": ("Use the remote file channel for this operation.", "请使用远程文件通道执行此操作。"),
    "remote_file_not_found": ("Remote file not found.", "未找到远程文件。"),
    "remote_idempotency_conflict": ("The idempotency key conflicts with another request.", "幂等键与另一请求冲突。"),
    "remote_idempotency_key_required": ("An idempotency key is required for remote actions.", "远程操作必须提供幂等键。"),
    "remote_permission_denied": ("The remote operation is not authorized.", "远程操作未获授权。"),
    "remote_plugin_call_failed": ("The remote Plugin call failed.", "远程插件调用失败。"),
    "remote_plugin_not_in_pack": ("The requested Plugin is not available through this pack.", "请求的插件不在此插件包中。"),
    "remote_plugin_pack_denied": ("Access to this remote Plugin pack was not granted.", "未授予对此远程插件包的访问权限。"),
    "remote_plugin_pack_unavailable": ("The remote Plugin pack is unavailable.", "远程插件包不可用。"),
    "remote_plugin_pack_unsupported": ("This Plugin pack cannot be called remotely.", "此插件包不支持远程调用。"),
    "remote_plugin_unavailable": ("The remote Plugin is unavailable.", "远程插件不可用。"),
    "remote_project_mismatch": ("The resource does not belong to the authorized project.", "资源不属于已授权项目。"),
    "remote_project_not_found": ("The authorized project no longer exists.", "已授权项目已不存在。"),
    "remote_target_approval_required": ("This operation requires approval on the target device.", "此操作需要在目标设备上批准。"),
    "run_not_found": ("Run not found.", "未找到运行记录。"),
    "thumbnail_unavailable": ("The thumbnail is unavailable.", "缩略图不可用。"),
    "thumbnail_unsupported": ("Thumbnail previews are available only for images.", "仅图片支持缩略图预览。"),
    "transfer_offset_invalid": ("The transfer offset is invalid.", "传输偏移量无效。"),
}

def _localized_remote_error(result: dict[str, Any]) -> dict[str, Any]:
    """Replace implementation details with a stable localized protocol error."""

    if result.get("ok") is not False:
        return result
    code = str(result.get("code") or "remote_command_failed")
    en, zh = _REMOTE_ERROR_MESSAGES.get(
        code,
        _REMOTE_ERROR_MESSAGES["remote_command_failed"],
    )
    return {**result, "code": code, "error": localized(en, zh)}

def _remote_project(project_id: str) -> dict[str, Any] | None:
    return project_repository.find_workbench_project_lightweight(project_id)

def _remote_project_workspace(project: dict[str, Any]) -> str:
    root = project_runtime._workbench_workspace_root(project)
    if root is None:
        root = Path(WORKSPACE_DIR).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return str(root)

def _remote_pack_is_available(pack_id: str) -> bool:
    """Resolve pack availability from the same live host used for dispatch.

    Keeping this lookup local to the command adapter avoids consulting a
    second, stale host accessor during lightweight embeds and test hosts.
    Production registries expose ``list_packs``; the plugin-list fallback is
    intentionally structural for minimal hosts and does not broaden the
    callable surface.
    """
    host = application_plugin_scope()
    if host is None:
        return False
    registry = getattr(host, "registry", None)
    list_packs = getattr(registry, "list_packs", None)
    if callable(list_packs):
        for pack in list_packs():
            if str(getattr(pack, "id", "") or "") != pack_id:
                continue
            locked = getattr(registry, "pack_locked", None)
            enabled = getattr(registry, "pack_enabled", None)
            return not (callable(locked) and locked(pack_id)) and not (
                callable(enabled) and not enabled(pack_id)
            )
        return False
    list_plugins = getattr(registry, "list_plugins", None)
    if not callable(list_plugins):
        return False
    return any(
        str(getattr(item, "pack_id", "") or "") == pack_id
        for item in list_plugins()
    )

def _remote_setting_field(
    key: str,
    section: str,
    value_type: str,
    label: str,
    label_zh: str,
    *,
    description: str = "",
    description_zh: str = "",
    default: Any = None,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
    options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    field = {
        "key": key,
        "section": section,
        "type": value_type,
        "label": label,
        "label_zh": label_zh,
        "description": description,
        "description_zh": description_zh,
        "default": default,
    }
    if minimum is not None:
        field["minimum"] = minimum
    if maximum is not None:
        field["maximum"] = maximum
    if options is not None:
        field["options"] = options
    return field

def _option(value: Any, label: str, label_zh: str) -> dict[str, Any]:
    return {"value": value, "label": label, "label_zh": label_zh}

_REMOTE_SETTING_FIELDS = (
    _remote_setting_field(
        "timezone", "general", "enum", "Timezone", "时区",
        description="Timezone used for dates, schedules, and activity times.",
        description_zh="日期、日程和活动时间所使用的时区。",
        default="Asia/Shanghai",
        options=[
            _option(value, value, value)
            for value in (
                "Pacific/Honolulu", "America/Los_Angeles", "America/Denver",
                "America/Chicago", "America/New_York", "America/Sao_Paulo",
                "UTC", "Europe/London", "Europe/Paris", "Africa/Cairo",
                "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai",
                "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
            )
        ],
    ),
    _remote_setting_field(
        "agent_proactive", "agent", "boolean", "Proactive agent", "Agent 主动工作",
        description="Allow the desktop agent to initiate relevant work and notifications.",
        description_zh="允许桌面 Agent 主动发起相关工作与通知。", default=True,
    ),
    _remote_setting_field(
        "spawn_policy", "agent", "enum", "Sub-agent policy", "子 Agent 策略",
        default="conservative",
        options=[
            _option("off", "Off", "关闭"),
            _option("conservative", "Conservative", "保守"),
            _option("aggressive", "Aggressive", "积极"),
        ],
    ),
    _remote_setting_field(
        "heartbeat_interval", "agent", "integer", "Heartbeat interval", "Agent 心跳间隔",
        description="Seconds between proactive heartbeat checks.",
        description_zh="主动心跳检查之间的秒数。", default=1800, minimum=60, maximum=86400,
    ),
    _remote_setting_field(
        "soul_active", "context", "boolean", "Use SOUL.md", "启用 SOUL.md",
        description="Include the desktop personality context in agent runs.",
        description_zh="在 Agent 运行中加入桌面端人格上下文。", default=True,
    ),
    _remote_setting_field(
        "workspace_active", "context", "boolean", "Use workspace context", "启用工作区上下文",
        default=True,
    ),
    _remote_setting_field(
        "write_permission_mode", "context", "enum", "Write permission", "写入权限",
        default="workspace_only",
        options=[
            _option("workspace_only", "Workspace only", "仅工作区"),
            _option("full_access", "Full access", "完全访问"),
        ],
    ),
    _remote_setting_field(
        "subagent_execution_max_tool_calls", "execution", "integer",
        "Maximum tool calls", "最大工具调用次数", default=200, minimum=1, maximum=5000,
    ),
    _remote_setting_field(
        "subagent_execution_max_wall_seconds", "execution", "integer",
        "Maximum run time (seconds)", "最长运行时间（秒）", default=1800, minimum=30, maximum=86400,
    ),
    _remote_setting_field(
        "subagent_execution_no_progress_turns", "execution", "integer",
        "No-progress turns", "无进展轮数", default=3, minimum=1, maximum=20,
    ),
    _remote_setting_field(
        "subagent_execution_checkpoint_calls", "execution", "integer",
        "Checkpoint frequency", "检查点频率", default=20, minimum=1, maximum=500,
    ),
    _remote_setting_field(
        "subagent_execution_max_cost_usd", "execution", "number",
        "Maximum cost (USD)", "最高成本（美元）", default=5.0, minimum=0, maximum=1000,
    ),
    _remote_setting_field(
        "subagent_execution_max_context_tokens", "execution", "integer",
        "Maximum context tokens", "最大上下文 Token", description="0 uses the model limit.",
        description_zh="设为 0 时使用模型自身限制。", default=0, minimum=0, maximum=4_000_000,
    ),
    _remote_setting_field(
        "subagent_discussion_max_rounds", "discussion", "integer",
        "Maximum rounds", "最大轮数", default=5, minimum=1, maximum=50,
    ),
    _remote_setting_field(
        "subagent_discussion_max_messages_per_agent", "discussion", "integer",
        "Messages per agent", "每个 Agent 的消息数", default=4, minimum=1, maximum=50,
    ),
    _remote_setting_field(
        "subagent_discussion_max_total_messages", "discussion", "integer",
        "Total messages", "消息总数", default=20, minimum=1, maximum=500,
    ),
    _remote_setting_field(
        "subagent_discussion_max_message_chars", "discussion", "integer",
        "Characters per message", "每条消息字符数", default=2000, minimum=100, maximum=20000,
    ),
    _remote_setting_field(
        "subagent_discussion_max_wall_seconds", "discussion", "integer",
        "Maximum discussion time (seconds)", "最长讨论时间（秒）",
        default=600, minimum=30, maximum=86400,
    ),
    _remote_setting_field(
        "subagent_discussion_max_tool_calls", "discussion", "integer",
        "Maximum discussion tool calls", "讨论最大工具调用次数",
        default=50, minimum=1, maximum=1000,
    ),
    _remote_setting_field(
        "subagent_discussion_no_new_info_rounds", "discussion", "integer",
        "No-new-information rounds", "无新信息轮数", default=2, minimum=1, maximum=20,
    ),
    _remote_setting_field(
        "notify_telegram", "channels", "boolean", "Telegram notifications", "Telegram 通知",
        description="Allow Cyrene to send notifications through Telegram.",
        description_zh="允许 Cyrene 通过 Telegram 发送通知。",
        default=True,
    ),
    _remote_setting_field(
        "notify_wechat", "channels", "boolean", "WeChat notifications", "微信通知",
        description="Allow Cyrene to send notifications through WeChat.",
        description_zh="允许 Cyrene 通过微信发送通知。",
        default=True,
    ),
    _remote_setting_field(
        "redact_secrets", "context", "boolean", "Redact secrets", "隐藏敏感信息",
        description="Remove detected credentials from logs and remote output.",
        description_zh="从日志和远程输出中移除检测到的凭据。", default=True,
    ),
    _remote_setting_field(
        "beta_updates", "updates", "boolean", "Beta updates", "测试版更新", default=False,
    ),
    _remote_setting_field(
        "auto_update", "updates", "boolean", "Automatic updates", "自动更新", default=True,
    ),
    _remote_setting_field(
        "budget_enabled", "budget", "boolean", "API budget", "API 预算", default=False,
    ),
    _remote_setting_field(
        "budget_monthly", "budget", "number", "Monthly limit", "每月限额",
        default=50.0, minimum=0, maximum=1_000_000,
    ),
    _remote_setting_field(
        "budget_currency", "budget", "enum", "Currency", "币种", default="CNY",
        options=[_option("CNY", "CNY", "人民币"), _option("USD", "USD", "美元")],
    ),
    _remote_setting_field(
        "budget_action", "budget", "enum", "When limit is reached", "达到限额时",
        default="warn",
        options=[_option("warn", "Warn", "警告"), _option("block", "Block", "阻止")],
    ),
    _remote_setting_field(
        "budget_start_day", "budget", "integer", "Billing cycle start day", "账期起始日",
        default=1, minimum=1, maximum=28,
    ),
)

def _public_model_settings() -> dict[str, Any]:
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    if service is None:
        raise RuntimeError("Model Plugin is disabled or unavailable")
    return service.public_model_configuration(service.get_model_configuration())

def _update_remote_model_settings(raw: Any) -> None:
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("model_configuration")
    if service is None:
        raise RuntimeError("Model Plugin is disabled or unavailable")

    if not isinstance(raw, dict):
        raise ValueError("models must be a canonical model configuration object")
    expected_revision = raw.get("revision")
    if expected_revision is not None and (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ValueError("model configuration revision must be a non-negative integer")

    connections: list[dict[str, Any]] = []
    for item in raw.get("connections") or []:
        if not isinstance(item, dict):
            raise ValueError("each model connection must be an object")
        connections.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"api_key_configured", "secret_configured"}
            }
        )
    configuration = {
        key: value
        for key, value in raw.items()
        if key in {"version", "profiles", "routes"}
    }
    configuration["connections"] = connections
    service.save_model_configuration(
        configuration,
        expected_revision=expected_revision,
    )

def _public_pending_question(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key]
        for key in (
            "id",
            "questionId",
            "kind",
            "questionKind",
            "text",
            "prompt",
            "question",
            "title",
            "options",
            "choices",
            "allowCustom",
            "allow_custom",
        )
        if key in value
    }
    return result or None

def _require_text(
    payload: dict[str, Any],
    field: str,
    *,
    max_length: int = 200_000,
) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    if len(value) > max_length:
        raise ValueError(f"{field} is too long")
    return value

def _permission_mode(
    payload: dict[str, Any],
    *,
    allowed: frozenset[str],
    default: str = "default",
) -> str:
    value = str(payload.get("permission_mode") or default)
    if value not in allowed:
        expected = ", ".join(sorted(allowed))
        raise ValueError(f"permission_mode must be one of: {expected}")
    return value

def _attachment_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    result = {
        key: item[key]
        for key in (
            "id",
            "name",
            "type",
            "mediaType",
            "content_type",
            "kind",
            "size",
            "width",
            "height",
        )
        if key in item
    }
    return result or None

def _store_remote_attachments(value: Any) -> list[dict[str, Any]]:
    """Persist a bounded, encrypted mobile upload as a regular chat attachment."""
    if not isinstance(value, list):
        return []
    from cyrene.platform.attachments import (
        UPLOADS_DIR,
        attachment_kind_from_meta,
        safe_attachment_filename,
    )

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    uploaded: list[dict[str, Any]] = []
    total_bytes = 0
    try:
        for index, item in enumerate(value[:5]):
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name") or f"mobile-{index + 1}.bin")
            safe_name = safe_attachment_filename(raw_name, f"mobile-{index + 1}")
            content_type = str(
                item.get("content_type")
                or mimetypes.guess_type(safe_name)[0]
                or "application/octet-stream"
            )
            encoded = str(item.get("content_base64") or "")
            try:
                content = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ValueError(f"invalid attachment encoding: {raw_name}") from exc
            total_bytes += len(content)
            if total_bytes > 8 * 1024 * 1024:
                raise ValueError("mobile attachments exceed the 8 MB total limit")
            target = UPLOADS_DIR / f"{uuid.uuid4().hex}_{safe_name}"
            target.write_bytes(content)
            uploaded.append(
                {
                    "id": target.name,
                    "name": raw_name,
                    "path": str(target.resolve()),
                    "content_type": content_type,
                    "size": len(content),
                    "kind": attachment_kind_from_meta(content_type, safe_name),
                    "url": f"/api/workbench/uploads/{target.name}",
                }
            )
    except Exception:
        for attachment in uploaded:
            Path(str(attachment.get("path") or "")).unlink(missing_ok=True)
        raise
    return uploaded

def _remote_chat_usage(chat: dict[str, Any]) -> dict[str, int]:
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
    }
    direct = chat.get("usage")
    if isinstance(direct, dict):
        for key in totals:
            try:
                totals[key] = max(0, int(direct.get(key) or 0))
            except (TypeError, ValueError):
                continue
        if totals["total_tokens"] <= 0:
            totals["total_tokens"] = (
                totals["prompt_tokens"] + totals["completion_tokens"]
            )
        return totals
    for message in chat.get("messages") or []:
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in totals:
            try:
                totals[key] += max(0, int(usage.get(key) or 0))
            except (TypeError, ValueError):
                continue
    if totals["total_tokens"] <= 0:
        totals["total_tokens"] = (
            totals["prompt_tokens"] + totals["completion_tokens"]
        )
    return totals

def _remote_chat_model(chat: dict[str, Any]) -> str:
    direct = str(chat.get("lastModel") or chat.get("model") or "").strip()
    if direct:
        return direct
    for message in reversed(chat.get("messages") or []):
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if isinstance(usage, dict):
            model = str(usage.get("model") or "").strip()
            if model:
                return model
        model = str(message.get("model") or "").strip()
        if model:
            return model
    return ""

def _chat_summary(chat: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "id": str(chat.get("id") or ""),
        "project_id": str(chat.get("projectId") or ""),
        "parent_chat_id": str(chat.get("parentChatId") or ""),
        "forked_from_chat_id": str(chat.get("forkedFromChatId") or ""),
        "forked_at_message_id": str(chat.get("forkedAtMessageId") or ""),
        "title": str(chat.get("title") or ""),
        "status": str(chat.get("status") or "idle"),
        "model": _remote_chat_model(chat),
        "permission_mode": str(chat.get("permissionMode") or "default"),
        "created_at": str(chat.get("createdAt") or ""),
        "updated_at": str(chat.get("updatedAt") or ""),
        "message_count": int(
            chat.get("messageCount") or len(chat.get("messages") or [])
        ),
        "usage": _remote_chat_usage(chat),
        "awaiting_user": isinstance(chat.get("pendingQuestion"), dict),
    }
    active_plan = chat.get("activePlan")
    if isinstance(active_plan, dict):
        summary["active_plan"] = active_plan
    return summary

def _chat_detail(chat: dict[str, Any]) -> dict[str, Any]:
    messages = []
    for raw in chat.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        attachments = [
            summary
            for item in raw.get("attachments") or []
            if (summary := _attachment_summary(item)) is not None
        ]
        message = {
                "id": str(raw.get("id") or ""),
                "role": str(raw.get("role") or ""),
                "content": str(raw.get("content") or ""),
                "created_at": str(raw.get("createdAt") or ""),
                "question_id": str(raw.get("questionId") or ""),
                "question_kind": str(raw.get("questionKind") or ""),
                "attachments": attachments,
            }
        # Preserve the durable Workbench timeline. A paired mobile controller
        # needs these fields to render saved activity cards and to replace its
        # live runtime without losing tool history.
        for key in ("activityCard", "intermediate", "model", "reasoning"):
            if key in raw:
                message[key] = raw[key]
        if isinstance(raw.get("trace"), list):
            message["trace"] = raw["trace"]
        messages.append(message)
    return {
        **_chat_summary(chat),
        "messages": messages,
        "pending_question": _public_pending_question(chat.get("pendingQuestion")),
    }

def _context_value_text(value: Mapping[str, Any]) -> str:
    role = str(value.get("role") or "")
    if role == "tool_results":
        return json.dumps(value.get("results") or [], ensure_ascii=False, default=str)
    content = value.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    else:
        text = str(content or "")
    if role == "assistant" and value.get("tool_calls"):
        text += json.dumps(value.get("tool_calls"), ensure_ascii=False, default=str)
    return text

def _remote_agent_plugin_usage(nodes: list[Any]) -> tuple[list[str], list[str]]:
    packs: list[str] = []
    standalone: list[str] = []
    seen_packs: set[str] = set()
    seen_standalone: set[str] = set()
    seen_calls: set[tuple[str, str]] = set()

    def record(result: Any, owner_id: str) -> None:
        if not isinstance(result, Mapping) or result.get("success") is not True:
            return
        call_id = str(result.get("call_id") or "").strip()
        key = (owner_id, call_id)
        if call_id and key in seen_calls:
            return
        if call_id:
            seen_calls.add(key)
        value = result.get("value")
        if (
            str(result.get("name") or "") != TOOLBOX_PLUGIN_NAME
            or not isinstance(value, Mapping)
            or value.get("operation") != "invoke"
        ):
            return
        pack_id = str(value.get("pack") or "").strip()
        plugin_name = str(value.get("name") or "").strip()
        if pack_id and pack_id not in seen_packs:
            seen_packs.add(pack_id)
            packs.append(pack_id)
        elif plugin_name and plugin_name not in seen_standalone:
            seen_standalone.add(plugin_name)
            standalone.append(plugin_name)

    for node in nodes:
        value = node.value if isinstance(node.value, Mapping) else {}
        effects = value.get("effect_results")
        for result in effects.values() if isinstance(effects, Mapping) else ():
            record(result, str(node.id))
        results = value.get("results") if value.get("role") == "tool_results" else ()
        for result in results if isinstance(results, list) else ():
            record(result, str(node.parent_id or node.id))
    return packs, standalone

def _remote_agent_context(
    chat_id: str,
    db_path: str,
    model_name: str,
    context_limit: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Project one Chat's authoritative ContextTree for remote clients."""

    from cyrene.core.context.compaction import COMPACT_TRIGGER_RATIO

    router = ContextStoreRouter(
        workbench_agent_data_directory(db_path) / "context"
    )
    try:
        tree = router.get_tree(str(chat_id))
        nodes = router.get_subtree(tree.id, tree.root_id)
        dialogue = [
            node
            for node in nodes
            if isinstance(node.value, Mapping)
            and str(node.value.get("role") or "")
            in {
                "system",
                "context",
                "context_compaction",
                "context_reflection",
                "user",
                "assistant",
                "tool_results",
            }
        ]
        leaf = max(dialogue, key=lambda item: (item.created_at, item.id))
        path = router.get_path(tree.id, leaf.id)
    except TreeNotFoundError:
        empty_metrics = {
            "model": str(model_name or ""),
            "actualModel": "",
            "usage": {},
            "ctxLimit": max(0, int(context_limit or 0)),
            "ctxUsed": 0,
            "ratio": 0.0 if context_limit else None,
            "compactTriggerRatio": COMPACT_TRIGGER_RATIO,
            "messageCount": 0,
            "segments": [],
            "compaction": {
                "active": False,
                "blocks": 0,
                "tokens": 0,
                "distilled": False,
            },
        }
        return (
            empty_metrics,
            {"layers": [], "totalTokensEst": 0, "messageTokens": 0},
            {"rounds": [], "activeRoundId": "", "agents": [], "messages": []},
        )
    finally:
        router.close()

    values = [
        dict(node.value)
        for node in path
        if isinstance(node.value, Mapping)
        and str(node.value.get("role") or "")
        in {
            "system",
            "context",
            "context_compaction",
            "context_reflection",
            "user",
            "assistant",
            "tool_results",
        }
    ]
    segments = {key: 0 for key in ("compacted", "system", "user", "assistant", "tool")}
    usage: dict[str, int] = {}
    actual_model = ""
    compacted_blocks = 0
    for value in values:
        role = str(value.get("role") or "")
        tokens = 4 + approx_token_count(role) + approx_token_count(
            _context_value_text(value)
        )
        if role in {"context_compaction", "context_reflection"}:
            bucket = "compacted"
            compacted_blocks += 1
        elif role in {"system", "context"}:
            bucket = "system"
        elif role == "tool_results":
            bucket = "tool"
        else:
            bucket = role
        segments[bucket] += tokens
        if role != "assistant":
            continue
        raw_usage = value.get("usage")
        if isinstance(raw_usage, dict):
            for key, raw in raw_usage.items():
                if isinstance(raw, bool):
                    continue
                try:
                    usage[str(key)] = usage.get(str(key), 0) + int(raw or 0)
                except (TypeError, ValueError):
                    pass
        identity = value.get("model_identity")
        if isinstance(identity, dict):
            actual_model = str(
                identity.get("model")
                or identity.get("model_name")
                or actual_model
            )
        actual_model = str(value.get("model") or actual_model)

    message_total = sum(segments.values())
    limit = max(0, int(context_limit or 0))
    message_blocks = [
        {
            "id": f"segment.{key}",
            "type": key,
            "tokens_est": int(tokens),
        }
        for key, tokens in segments.items()
        if tokens > 0
    ]
    used_packs, used_standalone = _remote_agent_plugin_usage(path)
    blocks = {
        "layers": (
            [{
                "id": "context_tree",
                "label": "ContextTree",
                "blocks": message_blocks,
                "totalTokens": message_total,
            }]
            if message_blocks
            else []
        ),
        "totalTokensEst": message_total,
        "messageTokens": message_total,
        "usedPluginPacks": used_packs,
        "usedStandalonePlugins": used_standalone,
    }
    metrics = {
        "model": str(model_name or actual_model),
        "actualModel": actual_model,
        "usage": usage,
        "ctxLimit": limit,
        "ctxUsed": message_total,
        "ratio": (message_total / limit) if limit else None,
        "compactTriggerRatio": COMPACT_TRIGGER_RATIO,
        "messageCount": len(values),
        "segments": [
            {"key": key, "tokens": tokens}
            for key, tokens in segments.items()
        ],
        "compaction": {
            "active": compacted_blocks > 0,
            "blocks": compacted_blocks,
            "tokens": segments["compacted"],
            "distilled": compacted_blocks > 0,
        },
    }

    root = next(
        (
            node
            for node in nodes
            if str(node.id) == str(tree.root_id)
            and isinstance(node.value, Mapping)
        ),
        None,
    )
    from cyrene.core.plugin import plugin_public_session_snapshot

    records = (
        plugin_public_session_snapshot(root.value).get("subagents")
        if root is not None
        else None
    )
    public_agents: list[dict[str, Any]] = []
    if isinstance(records, Mapping):
        for agent_id, raw in records.items():
            if not isinstance(raw, Mapping):
                continue
            public_agents.append({
                "id": str(agent_id),
                "name": str(agent_id),
                "task": str(raw.get("task") or ""),
                "status": str(raw.get("status") or "running"),
                "result": str(raw.get("result") or ""),
                "error": str(raw.get("error") or ""),
                "roundId": str(raw.get("round_id") or ""),
                "runId": str(raw.get("current_run_id") or ""),
                "treeId": str(raw.get("tree_id") or ""),
            })
    round_ids = list(dict.fromkeys(
        str(item.get("roundId") or "") for item in public_agents
        if str(item.get("roundId") or "")
    ))
    active_round_id = next(
        (
            round_id
            for round_id in reversed(round_ids)
            if any(
                item.get("roundId") == round_id
                and item.get("status") not in {"done", "failed", "cancelled"}
                for item in public_agents
            )
        ),
        round_ids[-1] if round_ids else "",
    )
    subagents = {
        "rounds": [
            {
                "id": round_id,
                "title": round_id,
                "status": (
                    "running"
                    if any(
                        item.get("roundId") == round_id
                        and item.get("status") not in {"done", "failed", "cancelled"}
                        for item in public_agents
                    )
                    else "done"
                ),
                "agentCount": sum(
                    1 for item in public_agents if item.get("roundId") == round_id
                ),
                "activeCount": sum(
                    1
                    for item in public_agents
                    if item.get("roundId") == round_id
                    and item.get("status") not in {"done", "failed", "cancelled"}
                ),
            }
            for round_id in round_ids
        ],
        "activeRoundId": active_round_id,
        "agents": [
            item for item in public_agents if item.get("roundId") == active_round_id
        ],
        "messages": [],
    }
    return metrics, blocks, subagents

def _remote_map_data(chat_id: str) -> dict[str, Any]:
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("maps")
    if service is None:
        return {"pins": [], "routes": []}
    try:
        data = service.snapshot(chat_id)
    except RuntimeError:
        # Map is an optional sibling Plugin. A remote chat snapshot remains
        # usable while that Plugin is disabled or still starting.
        return {"pins": [], "routes": []}
    return {
        "pins": [
            dict(item)
            for item in data.get("pins") or []
            if isinstance(item, dict)
        ],
        "routes": [
            dict(item)
            for item in data.get("routes") or []
            if isinstance(item, dict)
        ],
    }

def _remote_inbox_snapshot(chat_id: str, run_manager: Any) -> dict[str, Any]:
    run = run_manager.get(chat_id) if run_manager is not None else None
    live = run.inbox.live_snapshot() if run is not None else {
        "queueDepth": 0,
        "pendingGuidance": 0,
        "activeTasks": 0,
        "persistenceTasks": 0,
        "closed": True,
        "events": [],
        "tools": [],
    }
    events = [
        dict(item)
        for item in live.get("events") or []
        if isinstance(item, dict)
    ]
    tools = [
        dict(item)
        for item in live.get("tools") or []
        if isinstance(item, dict)
        and str(item.get("state") or "") in {"queued", "running", "ready"}
    ]
    return {
        "sessionId": chat_id,
        "runId": str(run.run_id if run is not None else ""),
        "active": bool(
            run is not None and str(run.status) in {"running", "finishing"}
        ),
        "runStatus": str(run.status if run is not None else "idle"),
        "counts": {
            "queued": sum(1 for item in events if item.get("status") == "queued"),
            "claimed": sum(1 for item in events if item.get("status") == "claimed"),
            "total": len(events),
        },
        "events": events,
        "tools": tools,
        "live": live,
    }

def _file_chunk(file_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Read one bounded transport chunk without limiting the complete file."""
    size = file_path.stat().st_size
    offset = max(0, int(payload.get("offset") or 0))
    requested = int(payload.get("limit") or _DEFAULT_TRANSFER_CHUNK_BYTES)
    limit = max(1, min(requested, _MAX_TRANSFER_CHUNK_BYTES))
    if offset > size:
        return {
            "ok": False,
            "code": "transfer_offset_invalid",
            "error": "transfer offset is beyond the end of the file",
            "size": size,
            "offset": offset,
        }
    with file_path.open("rb") as handle:
        handle.seek(offset)
        content = handle.read(limit)
    next_offset = offset + len(content)
    return {
        "size": size,
        "offset": offset,
        "chunk_size": len(content),
        "next_offset": next_offset,
        "eof": next_offset >= size,
        "progress": 1.0 if size == 0 else min(1.0, next_offset / size),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }

def _image_thumbnail(file_path: Path) -> tuple[Path, int, int]:
    """Return a cached, bounded WebP preview without altering the source."""
    from cyrene.config import DATA_DIR

    stat = file_path.stat()
    fingerprint = hashlib.sha256(
        (
            f"{file_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:"
            f"{_THUMBNAIL_MAX_DIMENSION}:{_THUMBNAIL_WEBP_QUALITY}"
        ).encode("utf-8")
    ).hexdigest()
    directory = DATA_DIR / "attachment_thumbnails"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{fingerprint}.webp"
    if target.is_file() and target.stat().st_size > 0:
        with Image.open(target) as cached:
            return target, int(cached.width), int(cached.height)

    temporary = directory / f".{fingerprint}.{uuid.uuid4().hex}.tmp"
    try:
        with Image.open(file_path) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail(
                (_THUMBNAIL_MAX_DIMENSION, _THUMBNAIL_MAX_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            width, height = int(image.width), int(image.height)
            image.save(
                temporary,
                format="WEBP",
                quality=_THUMBNAIL_WEBP_QUALITY,
                method=4,
            )
        os.replace(temporary, target)
        return target, width, height
    finally:
        temporary.unlink(missing_ok=True)

def referenced_chat_attachment_target(
    chat: dict[str, Any],
    attachment_id: str,
) -> tuple[dict[str, Any], Path]:
    """Resolve only a file explicitly referenced by a chat transcript."""
    attachment = next(
        (
            item
            for message in chat.get("messages") or []
            if isinstance(message, dict)
            for item in message.get("attachments") or []
            if isinstance(item, dict)
            and str(item.get("id") or "") == str(attachment_id)
        ),
        None,
    )
    if attachment is None:
        raise LookupError("attachment is not referenced by this chat")

    referenced_path = str(attachment.get("path") or "").strip()
    if referenced_path:
        candidate = Path(referenced_path).expanduser().resolve()
        if candidate.exists() and candidate.is_file():
            return attachment, candidate

    from cyrene.platform.attachments import EXPORTS_DIR, UPLOADS_DIR

    url = str(attachment.get("url") or "")
    if url.startswith("/api/workbench/uploads/"):
        candidate_roots = (UPLOADS_DIR,)
    elif url.startswith("/api/workbench/exports/"):
        candidate_roots = (EXPORTS_DIR,)
    else:
        candidate_roots = (EXPORTS_DIR, UPLOADS_DIR)
    route_name = Path(url).name if url else Path(str(attachment_id)).name
    for root in candidate_roots:
        candidate = (root / route_name).resolve()
        if (
            candidate.exists()
            and candidate.is_file()
            and candidate.parent == root.resolve()
        ):
            return attachment, candidate
    raise FileNotFoundError("referenced attachment file is unavailable")

class RemoteCommandExecutor:
    """Execute the protocol's fixed command set against local Workbench state."""

    def __init__(
        self,
        *,
        store: RemoteControlStore,
        chat: Any = None,
        projects: Any = None,
        bot: Any = None,
        db_path: str = "",
    ) -> None:
        self.store = store
        self.bot = bot
        self.db_path = str(db_path or "")
        self.chat = chat
        self.projects = projects
        self._remote_shell_owners: dict[str, tuple[str, str]] = {}
        self._remote_files = RemoteWorkspaceFiles(store)
        self._remote_jobs = RemoteJobManager(store)

    def set_remote_event_sender(self, sender: Any) -> None:
        self._remote_jobs.set_event_sender(sender)

    async def execute_scoped_file(
        self,
        peer_device_id: str,
        command: str,
        scope_id: str,
        root: Path,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._remote_files.execute_scoped(
            peer_device_id,
            command,
            scope_id,
            root,
            payload,
        )

    async def __call__(
        self,
        peer_device_id: str,
        command: str,
        payload: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        try:
            result = await self._execute(peer_device_id, command, payload, project_id)
        except ControlServiceError as exc:
            result = {"ok": False, "status_code": exc.status_code, **exc.payload}
        except LookupError as exc:
            result = {"ok": False, "code": "goal_not_found", "error": str(exc)}
        except ValueError as exc:
            result = {"ok": False, "code": "invalid_status_transition", "error": str(exc)}
        return _localized_remote_error(result)

    async def _execute(
        self,
        peer_device_id: str,
        command: str,
        payload: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        command = str(command or "")
        payload = dict(payload or {})

        if command == "capabilities.read":
            return {
                "ok": True,
                "protocol_version": REMOTE_PROTOCOL_VERSION,
                "capabilities": sorted(REMOTE_CAPABILITIES),
                "remote_plugin_packs": list(remote_plugin_pack_ids()),
                "features": {
                    "workspace_files_v1": True,
                    "remote_jobs_v1": True,
                    "remote_authorization_v1": True,
                    "remote_desktop_v1": application_plugin_service("remote_desktop") is not None,
                },
            }
        if command == "projects.list":
            return await self._projects_list(peer_device_id)
        workbench = await self._execute_workbench(command, project_id, payload)
        if workbench is not None:
            return workbench
        if command.startswith("files."):
            allow_outside = self._remote_path_authorization(
                command,
                project_id,
                payload,
            )
            return await self._remote_files.execute(
                peer_device_id,
                command,
                project_id,
                payload,
                allow_outside=allow_outside,
            )
        if command.startswith("jobs."):
            allow_outside = self._remote_path_authorization(
                command,
                project_id,
                payload,
            )
            return await self._remote_jobs.execute(
                peer_device_id,
                command,
                project_id,
                payload,
                allow_outside=allow_outside,
            )
        settings = await self._execute_settings(command, payload)
        if settings is not None:
            return settings
        if command.startswith("shell."):
            return await self._shell_command(
                peer_device_id, command, project_id, payload
            )
        if command.startswith("harness."):
            return await self._harness_command(
                peer_device_id, command, project_id, payload
            )
        if command.startswith("desktop."):
            service = application_plugin_service("remote_desktop")
            if service is None:
                return {
                    "ok": False,
                    "code": "remote_desktop_plugin_unavailable",
                    "error": "The Remote Desktop Plugin is disabled or unavailable.",
                }
            return await service.handle_remote_command(
                peer_device_id,
                command,
                payload,
            )
        return {
            "ok": False,
            "code": "remote_command_unsupported",
            "error": f"unsupported remote command: {command}",
        }

    async def _execute_workbench(
        self, command: str, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        no_payload = {
            "chats.list": self._chats_list,
        }
        with_payload = {
            "chats.create": self._chats_create, "chats.update": self._chats_update,
            "chats.delete": self._chats_delete, "chats.read": self._chats_read,
            "changes.read": self._changes_read, "chats.send": self._chats_send,
            "runs.read": self._runs_read, "runs.events": self._runs_events,
            "runs.wait": self._runs_wait, "runs.guide": self._runs_guide,
            "runs.interrupt": self._runs_interrupt,
            "goals.read": self._goals_read,
            "goals.update": self._goals_update,
            "goals.confirm": self._goals_confirm,
            "approvals.respond": self._approvals_respond,
            "attachments.read": self._attachments_read,
        }
        if operation := no_payload.get(command):
            return await operation(project_id)
        if operation := with_payload.get(command):
            return await operation(project_id, payload)
        if command in {"goals.pause", "goals.resume", "goals.abort", "goals.accept"}:
            return await self._goals_control(command, project_id, payload)
        return None

    async def _execute_settings(
        self, command: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        if command == "settings.read":
            return self._settings_read()
        if command == "settings.models.copy":
            return self._settings_models_copy(payload)
        if command == "settings.update":
            return await self._settings_update(payload)
        operations = {
            "settings.openai_oauth.read": self._settings_openai_oauth_read,
            "settings.openai_oauth.login": self._settings_openai_oauth_login,
            "settings.openai_oauth.logout": self._settings_openai_oauth_logout,
        }
        operation = operations.get(command)
        return await operation() if operation is not None else None

    def _remote_path_authorization(
        self,
        command: str,
        project_id: str,
        payload: dict[str, Any],
    ) -> bool:
        """Verify a single-operation receipt before enabling absolute paths."""
        authorization = payload.pop("_authorization", None)
        if not isinstance(authorization, dict):
            return False
        arguments = {
            "device_id": self.store.identity.device_id,
            "project_id": project_id,
            "command": command,
            "payload": payload,
        }
        expected = hashlib.sha256(
            json.dumps(
                arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        valid = bool(
            authorization.get("approved") is True
            and int(authorization.get("version") or 0) == 1
            and str(authorization.get("scope") or "") == "single_operation"
            and str(authorization.get("arguments_sha256") or "") == expected
            and str(authorization.get("permission_mode") or "")
            in {"plan", "default", "auto", "full_access"}
        )
        outside = bool(authorization.get("outside_workspace"))
        if outside and not valid:
            raise PermissionError(
                "absolute remote paths require an exact controller authorization"
            )
        return bool(outside and valid)

    @staticmethod
    def _public_shell_snapshot(
        snapshot: dict[str, Any],
        *,
        cursor: int = 0,
    ) -> dict[str, Any]:
        safe_cursor = max(0, int(cursor or 0))
        terminal = dict(snapshot.get("terminal") or snapshot)
        next_cursor = int(terminal.get("nextSeq") or safe_cursor)
        screen_text = str(snapshot.get("screenText") or "")
        lines = [] if next_cursor <= safe_cursor else [
            {"seq": next_cursor, "kind": "screen", "text": screen_text}
        ]
        return {
            "ok": True,
            "shell_id": str(terminal.get("id") or ""),
            "status": str(terminal.get("status") or "closed"),
            "cwd": str(terminal.get("cwd") or "."),
            "exit_code": terminal.get("exitCode"),
            "next_cursor": next_cursor,
            "lines": lines,
        }

    @staticmethod
    def _desktop_shell_prompt(workspace_dir: str) -> str:
        environment = str(
            os.environ.get("CONDA_DEFAULT_ENV")
            or Path(str(os.environ.get("VIRTUAL_ENV") or "")).name
            or ""
        ).strip()
        environment_prefix = f"({environment}) " if environment else ""
        user = getpass.getuser()
        host = platform.node().split(".", 1)[0] or "desktop"
        cwd_label = Path(workspace_dir).name or "~"
        shell_name = Path(str(os.environ.get("SHELL") or "")).name.lower()
        symbol = "%" if shell_name == "zsh" else "$"
        return f"{environment_prefix}{user}@{host} {cwd_label} {symbol}"

    async def _owned_shell(
        self,
        shell_service: Any,
        peer_device_id: str,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        shell_id = _require_text(payload, "shell_id", max_length=160)
        if self._remote_shell_owners.get(shell_id) != (
            peer_device_id,
            project_id,
        ):
            raise ValueError("remote shell is unavailable for this device and project")
        try:
            snapshot = await shell_service.screen(shell_id)
        except Exception:
            self._remote_shell_owners.pop(shell_id, None)
            raise ValueError("remote shell no longer exists")
        return shell_id, snapshot

    async def _shell_command(
        self,
        peer_device_id: str,
        command: str,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        shell_service = application_plugin_service("remote_shell")
        if shell_service is None:
            return {
                "ok": False,
                "code": "remote_plugin_unavailable",
                "error": "the cyrene_code Plugin pack is disabled or unavailable",
            }
        project = _remote_project(project_id)
        if project is None:
            return {
                "ok": False,
                "code": "remote_project_not_found",
                "error": "authorized project no longer exists",
            }
        workspace_dir = _remote_project_workspace(project)
        if command == "shell.open":
            created = await shell_service.create(
                project_id,
                cwd=workspace_dir,
                title=f"Mobile Shell · {str(project.get('name') or project_id)}",
            )
            terminal = dict(created.get("terminal") or {})
            snapshot = await shell_service.screen(str(terminal.get("id") or ""))
            shell_id = str(terminal.get("id") or "")
            self._remote_shell_owners[shell_id] = (
                peer_device_id,
                project_id,
            )
            return {
                **self._public_shell_snapshot(snapshot),
                "prompt": self._desktop_shell_prompt(workspace_dir),
            }

        shell_id, snapshot = await self._owned_shell(
            shell_service, peer_device_id, project_id, payload
        )
        terminal_state = dict(snapshot.get("terminal") or snapshot)
        cursor = max(0, int(payload.get("cursor") or 0))
        if command == "shell.read":
            return self._public_shell_snapshot(snapshot, cursor=cursor)
        if command == "shell.write":
            if str(terminal_state.get("status") or "") != "running":
                raise ValueError("remote shell is not running")
            shell_input = _require_text(payload, "input", max_length=32_768)
            snapshot = await shell_service.input(
                shell_id, shell_input, actor="user",
            )
            return self._public_shell_snapshot(snapshot, cursor=cursor)
        if command == "shell.interrupt":
            if str(terminal_state.get("status") or "") != "running":
                raise ValueError("remote shell is not running")
            snapshot = await shell_service.interrupt(shell_id)
            return self._public_shell_snapshot(snapshot, cursor=cursor)
        if command == "shell.close":
            result = await shell_service.remove(shell_id)
            terminal = dict(result.get("terminal") or snapshot.get("terminal") or {})
            snapshot = {"terminal": terminal, "screenText": snapshot.get("screenText", "")}
            self._remote_shell_owners.pop(shell_id, None)
            return self._public_shell_snapshot(snapshot, cursor=cursor)
        raise ValueError("unsupported shell operation")

    def _settings_read(self) -> dict[str, Any]:
        from cyrene.platform.settings_service import setting_spec_by_key
        from cyrene.platform.settings_store import get_enabled_plugin_packs

        settings = get_web_settings()
        active_keys = set(setting_spec_by_key())
        fields = [
            dict(field)
            for field in _REMOTE_SETTING_FIELDS
            if field["key"] in active_keys
        ]
        values = {
            field["key"]: settings.get(field["key"], field.get("default"))
            for field in fields
        }
        enabled_packs = get_enabled_plugin_packs()
        host = application_plugin_scope()
        model_available = (
            host is not None
            and host.service("model_configuration") is not None
        )
        skills_service = host.service("skills") if host is not None else None
        packs = host.registry.list_packs() if host is not None else ()
        for pack in packs:
            pack_id = str(pack.id)
            if host is not None and host.registry.pack_locked(pack_id):
                continue
            key = f"pluginpack::{pack_id}"
            label, description = pack.localized("en")
            label_zh, description_zh = pack.localized("zh")
            fields.append(
                _remote_setting_field(
                    key,
                    "plugin_packs",
                    "boolean",
                    label,
                    label_zh,
                    description=description,
                    description_zh=description_zh,
                    default=True,
                )
            )
            values[key] = bool(enabled_packs.get(pack_id, True))
        if host is not None:
            activation = host.registry.activation.snapshot()
            for registered in host.registry.list_plugins():
                plugin = registered.plugin
                if registered.pack_id is not None or host.registry.plugin_locked(plugin.name):
                    continue
                key = f"plugin::{plugin.name}"
                fields.append(
                    _remote_setting_field(
                        key,
                        "plugins",
                        "boolean",
                        plugin.name,
                        plugin.name,
                        description=str(plugin.description or ""),
                        description_zh=str(plugin.description or ""),
                        default=True,
                    )
                )
                values[key] = bool(activation.plugins.get(plugin.name, True))

        skills = skills_service.catalog() if skills_service is not None else []
        for skill in skills:
            skill_id = str(skill.get("id") or "").strip()
            if not skill_id:
                continue
            key = f"skill::{skill_id}"
            fields.append(
                _remote_setting_field(
                    key,
                    "skills",
                    "boolean",
                    str(skill.get("name") or skill_id),
                    str(skill.get("name") or skill_id),
                    description=str(skill.get("desc") or ""),
                    description_zh=str(skill.get("desc") or ""),
                    default=True,
                )
            )
            values[key] = bool(skill.get("enabled", True))

        return {
            "ok": True,
            "settings": values,
            "models": _public_model_settings() if model_available else {},
            "schema": {
                "version": 2,
                "sections": [
                    dict(section)
                    for section in _REMOTE_SETTING_SECTIONS
                    if model_available or section.get("id") != "models"
                ],
                "fields": fields,
            },
        }

    @staticmethod
    def _settings_models_copy(payload: dict[str, Any]) -> dict[str, Any]:
        """Return the canonical model graph over the paired E2EE channel.

        This endpoint exists specifically for a trusted mobile controller that
        performs Provider Plugin calls on-device. It exports connection API
        keys, but never exports Codex OAuth tokens or unrelated secrets.
        Access is gated by the existing ``settings:read`` peer grant.
        """
        if payload:
            raise ValueError("settings.models.copy does not accept fields")
        host = application_plugin_scope()
        model_service = host.service("model_configuration") if host is not None else None
        if model_service is None:
            raise RuntimeError("Model Plugin is disabled or unavailable")
        from cyrene.platform import config_store

        graph = model_service.get_model_configuration()
        graph["revision"] = config_store.get_settings_revision()
        return {
            "ok": True,
            "models": graph,
        }

    async def _settings_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        from cyrene.platform.settings_service import setting_spec_by_key
        from cyrene.platform.settings_store import (
            get_enabled_plugin_packs,
            get_enabled_plugins,
            save_enabled_plugin_packs,
            save_enabled_plugins,
        )

        payload = dict(payload)
        model_payload = payload.pop("models", None)
        active_keys = set(setting_spec_by_key())
        field_by_key = {
            field["key"]: field
            for field in _REMOTE_SETTING_FIELDS
            if field["key"] in active_keys
        }
        current_plugins = get_enabled_plugins()
        current_packs = get_enabled_plugin_packs()
        host = application_plugin_scope()
        if host is None:
            raise RuntimeError("Plugin application host is unavailable")
        if model_payload is not None and host.service("model_configuration") is None:
            raise ValueError("Model Plugin is disabled or unavailable")
        skills_service = host.service("skills")
        if skills_service is None and any(
            str(key).startswith("skill::") for key in payload
        ):
            raise ValueError("Skills Plugin is disabled or unavailable")
        current_skill_ids = {
            str(skill.get("id") or "")
            for skill in (
                skills_service.catalog() if skills_service is not None else []
            )
            if str(skill.get("id") or "")
        }
        plugin_pack_ids = {
            str(pack.id)
            for pack in host.registry.list_packs()
            if not host.registry.pack_locked(str(pack.id))
        }
        standalone_plugin_ids = {
            registered.plugin.name
            for registered in host.registry.list_plugins()
            if registered.pack_id is None
            and not host.registry.plugin_locked(registered.plugin.name)
        }
        allowed = set(field_by_key)
        allowed.update(f"plugin::{name}" for name in standalone_plugin_ids)
        allowed.update(f"pluginpack::{name}" for name in plugin_pack_ids)
        allowed.update(f"skill::{skill_id}" for skill_id in current_skill_ids)
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(
                "unsupported remote setting(s): " + ", ".join(unknown)
            )

        if model_payload is not None:
            _update_remote_model_settings(model_payload)

        normalized: dict[str, Any] = {}
        for key, raw_value in payload.items():
            if (
                key.startswith("plugin::")
                or key.startswith("pluginpack::")
                or key.startswith("skill::")
            ):
                if not isinstance(raw_value, bool):
                    raise ValueError(f"{key} must be a boolean")
                normalized[key] = raw_value
                continue
            field = field_by_key[key]
            value_type = field["type"]
            if value_type == "boolean":
                if not isinstance(raw_value, bool):
                    raise ValueError(f"{key} must be a boolean")
                value = raw_value
            elif value_type == "integer":
                if isinstance(raw_value, bool):
                    raise ValueError(f"{key} must be an integer")
                try:
                    value = int(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be an integer") from exc
            elif value_type == "number":
                if isinstance(raw_value, bool):
                    raise ValueError(f"{key} must be a number")
                try:
                    value = float(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be a number") from exc
                if not math.isfinite(value):
                    raise ValueError(f"{key} must be a finite number")
            elif value_type == "enum":
                value = str(raw_value or "").strip()
                option_values = {
                    str(option["value"])
                    for option in field.get("options") or []
                }
                if value not in option_values:
                    raise ValueError(f"invalid {key}")
            else:
                raise ValueError(f"unsupported setting type for {key}")

            minimum = field.get("minimum")
            maximum = field.get("maximum")
            if minimum is not None and value < minimum:
                raise ValueError(f"{key} must be at least {minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"{key} must be at most {maximum}")
            normalized[key] = value

        changed: list[str] = []
        next_plugins = dict(current_plugins)
        next_packs = dict(current_packs)
        plugins_changed = False
        packs_changed = False
        for key, value in normalized.items():
            if key.startswith("plugin::"):
                next_plugins[key.removeprefix("plugin::")] = value
                plugins_changed = True
            elif key.startswith("pluginpack::"):
                next_packs[key.removeprefix("pluginpack::")] = value
                packs_changed = True
            elif key.startswith("skill::"):
                if skills_service is None:
                    raise ValueError("Skills Plugin is disabled or unavailable")
                if not skills_service.set_skill_enabled(
                    key.removeprefix("skill::"),
                    value,
                ):
                    raise ValueError("skill is no longer installed")
            else:
                set_setting(key, value)
            changed.append(key)
        if plugins_changed:
            save_enabled_plugins(next_plugins)
        if packs_changed:
            save_enabled_plugin_packs(next_packs)
        if plugins_changed or packs_changed:
            host.registry.configure_activation(
                plugins=next_plugins,
                packs=next_packs,
            )
            await host.reconcile_activation()

        result = self._settings_read()
        result["changed"] = (["models"] if model_payload is not None else []) + changed
        return result

    @staticmethod
    async def _settings_openai_oauth_read() -> dict[str, Any]:
        """Expose the safe OAuth account/model snapshot to remote controllers."""

        host = application_plugin_scope()
        model_service = host.service("model_configuration") if host is not None else None
        if model_service is None:
            return {
                "ok": False,
                "code": "model_plugin_unavailable",
                "available": False,
                "connected": False,
                "account": None,
                "models": [],
                "quota_enabled": True,
                "error": localized(
                    "The model Plugin is unavailable.",
                    "模型插件不可用。",
                ),
            }
        from cyrene.platform.settings_store import get as get_setting

        try:
            snapshot = await model_service.oauth_provider().snapshot(
                include_limits=False,
                include_models=True,
            )
            model_error = (
                snapshot.get("errors", {}).get("models", "")
                if isinstance(snapshot.get("errors"), dict)
                else ""
            )
            return {
                "ok": True,
                "available": snapshot.get("available", True),
                "connected": snapshot.get("connected", False),
                "account": snapshot.get("account"),
                "models": snapshot.get("models") or [],
                "quota_enabled": bool(get_setting("codex_budget_enabled", True)),
                "error": (
                    localized(
                        "Some model information is temporarily unavailable.",
                        "部分模型信息暂时不可用。",
                    )
                    if model_error
                    else ""
                ),
                "error_code": (
                    "model_catalog_partial"
                    if model_error
                    else ""
                ),
            }
        except (RuntimeError, OSError, TimeoutError) as exc:
            logger.info(
                "Remote model account snapshot is unavailable",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            return {
                "ok": True,
                "available": False,
                "connected": False,
                "account": None,
                "models": [],
                "quota_enabled": True,
                "error": localized(
                    "Model account information is temporarily unavailable.",
                    "模型账户信息暂时不可用。",
                ),
                "error_code": "model_account_unavailable",
            }

    @staticmethod
    async def _settings_openai_oauth_login() -> dict[str, Any]:
        host = application_plugin_scope()
        model_service = host.service("model_configuration") if host is not None else None
        if model_service is None:
            raise RuntimeError("Model Plugin is disabled or unavailable")

        set_setting("codex_budget_enabled", True)
        return await model_service.oauth_provider().start_login()

    @staticmethod
    async def _settings_openai_oauth_logout() -> dict[str, Any]:
        host = application_plugin_scope()
        model_service = host.service("model_configuration") if host is not None else None
        if model_service is None:
            raise RuntimeError("Model Plugin is disabled or unavailable")

        await model_service.oauth_provider().logout()
        return {"ok": True}

    async def _projects_list(self, peer_device_id: str) -> dict[str, Any]:
        projects = await self.projects.list_projects()
        peer = self.store.get_peer(peer_device_id)
        shared_project_ids = set(
            peer.get("granted_project_scopes") or [] if peer else []
        )
        return {
            "ok": True,
            "projects": [
                {
                    "id": str(project.get("id") or ""),
                    "name": str(project.get("name") or ""),
                    "status": str(project.get("status") or "active"),
                    "updated_at": str(project.get("updatedAt") or ""),
                }
                for project in projects
                if isinstance(project, dict)
                and str(project.get("id") or "") in shared_project_ids
            ],
        }

    async def _chats_list(self, project_id: str) -> dict[str, Any]:
        result = await self.chat.list(project_id)
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "chats": [
                _chat_summary(item)
                for item in result.get("chats") or []
                if isinstance(item, dict)
            ],
        }

    async def _chats_create(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self.chat.create({
            "project": project_id,
            "title": str(payload.get("title") or "")[:160],
        })
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _chats_update(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        title = _require_text(payload, "title", max_length=60)
        result = await self.chat.update(chat_id, {"title": title})
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _chats_delete(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        result = await self.chat.delete(chat_id)
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _chat_for_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        chat_id = _require_text(payload, "chat_id", max_length=200)
        result = await self.chat.get(chat_id)
        if result.get("ok") is False:
            return chat_id, result
        chat = dict(result.get("chat") or {})
        if str(chat.get("projectId") or "") != project_id:
            return chat_id, {
                "ok": False,
                "code": "remote_project_mismatch",
                "error": "chat does not belong to the authorized project",
            }
        return chat_id, chat

    async def _chats_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        from cyrene.plugins.model_catalog import configured_context_limit

        model_name = _remote_chat_model(chat)
        context_limit = configured_context_limit(chat_id)
        change_sets_call = (
            asyncio.to_thread(list_chat_change_sets, self.db_path, chat_id)
            if self.db_path
            else asyncio.sleep(0, result=[])
        )
        (
            agent_context,
            change_sets,
            map_data,
        ) = await asyncio.gather(
            asyncio.to_thread(
                _remote_agent_context,
                chat_id,
                self.db_path,
                model_name,
                context_limit,
            ),
            change_sets_call,
            asyncio.to_thread(_remote_map_data, chat_id),
        )
        context_metrics, context_blocks, subagents = agent_context
        changes = {
            "changeSets": change_sets,
            "fileCount": sum(
                int(item.get("fileCount") or 0)
                for item in change_sets
            ),
            "additions": sum(
                int(item.get("additions") or 0)
                for item in change_sets
            ),
            "deletions": sum(
                int(item.get("deletions") or 0)
                for item in change_sets
            ),
        }
        detail = _chat_detail(chat)
        detail.update(
            {
                "context_metrics": context_metrics,
                "context_blocks": context_blocks,
                "inbox": _remote_inbox_snapshot(
                    chat_id,
                    self.chat.run_manager,
                ),
                "used_plugin_packs": list(
                    context_blocks.get("usedPluginPacks") or []
                ),
                "used_standalone_plugins": list(
                    context_blocks.get("usedStandalonePlugins") or []
                ),
            "subagents": subagents,
                "changes": changes,
                "map": map_data,
            }
        )
        return {"ok": True, "chat": detail}

    async def _changes_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        change_set_id = _require_text(
            payload,
            "change_set_id",
            max_length=200,
        )
        file_path = _require_text(payload, "file_path", max_length=2_000)
        change = await asyncio.to_thread(
            get_chat_file_change,
            self.db_path,
            chat_id,
            change_set_id,
            file_path,
        )
        if change is None:
            return {
                "ok": False,
                "code": "change_not_found",
                "error": "workspace change not found",
            }
        return {
            "ok": True,
            "change": {
                key: value
                for key, value in change.items()
                if key != "beforeText" and key != "afterText"
            },
        }

    async def _chats_send(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        attachments = _store_remote_attachments(payload.get("attachments"))
        send_body = {
            "message": _require_text(payload, "message"),
            "mode": _permission_mode(
                payload,
                allowed=frozenset({"auto", "default", "plan", "full_access"}),
                default="auto",
            ),
            "lang": str(payload.get("language") or ""),
            "stream": True,
        }
        if attachments:
            send_body["attachments"] = attachments
        try:
            result = await self.chat.send(chat_id, send_body)
        except Exception:
            for attachment in attachments:
                Path(str(attachment.get("path") or "")).unlink(missing_ok=True)
            raise
        if result.get("ok") is False:
            for attachment in attachments:
                Path(str(attachment.get("path") or "")).unlink(missing_ok=True)
            return result
        return {"ok": True, **result}

    async def _run_for_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[Any, dict[str, Any] | None]:
        run_id = _require_text(payload, "run_id", max_length=200)
        run = self.chat.run_manager.get_replayable_by_run_id(run_id)
        if run is None:
            return None, {
                "ok": False,
                "code": "run_not_found",
                "error": "run not found",
            }
        result = await self.chat.get(run.chat_id)
        if result.get("ok") is False:
            return None, result
        chat = dict(result.get("chat") or {})
        if str(chat.get("projectId") or "") != project_id:
            return None, {
                "ok": False,
                "code": "remote_project_mismatch",
                "error": "run does not belong to the authorized project",
            }
        return run, None

    async def _runs_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        run, error = await self._run_for_project(project_id, payload)
        if error:
            return error
        return {
            "ok": True,
            "run": {
                "run_id": run.run_id,
                "chat_id": run.chat_id,
                "status": run.status,
                "created_at": run.created_at,
                "completed": run.done.is_set(),
                "termination_reason": run.termination_reason,
            },
        }

    async def _runs_events(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        run, error = await self._run_for_project(project_id, payload)
        if error:
            return error
        cursor = max(0, int(payload.get("cursor") or 0))
        limit = max(1, min(int(payload.get("limit") or 200), 1000))
        raw_events = [
            event
            for event in run.events
            if int(event.get("_seq") or 0) > cursor
        ][:limit]
        events = [
            public
            for event in raw_events
            if (public := public_remote_event(event)) is not None
        ]
        return {
            "ok": True,
            "events": events,
            "next_cursor": max(
                [cursor, *[int(event.get("_seq") or 0) for event in raw_events]]
            ),
            "completed": run.done.is_set(),
        }

    async def _runs_wait(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        run, error = await self._run_for_project(project_id, payload)
        if error:
            return error
        cursor = max(0, int(payload.get("cursor") or 0))
        timeout = max(0.1, min(float(payload.get("timeout_seconds") or 25), 55.0))
        if not run.done.is_set() and not any(
            int(event.get("_seq") or 0) > cursor for event in run.events
        ):
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            run.subscribers.add(queue)
            try:
                if not run.done.is_set() and not any(
                    int(event.get("_seq") or 0) > cursor for event in run.events
                ):
                    try:
                        await asyncio.wait_for(queue.get(), timeout=timeout)
                    except asyncio.TimeoutError:
                        pass
            finally:
                run.subscribers.discard(queue)
        return await self._runs_events(project_id, payload)

    async def _harness_command(
        self,
        peer_device_id: str,
        command: str,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        pack_id = _require_text(payload, "plugin_pack", max_length=100)
        if not _remote_pack_is_available(pack_id):
            # Do not disclose pack availability to an untrusted peer.  A
            # syntactically valid but ungranted pack is reported as denied;
            # only a peer that already holds the grant can learn that a pack
            # is currently unavailable.
            peer = self.store.get_peer(peer_device_id)
            grant = REMOTE_PLUGIN_PACK_PREFIX + pack_id
            if peer is None or grant not in (peer.get("granted_capabilities") or []):
                return {
                    "ok": False,
                    "code": "remote_plugin_pack_denied",
                    "error": f"remote access to {pack_id} is not granted",
                }
            return {
                "ok": False,
                "code": "remote_plugin_pack_unsupported",
                "error": f"Plugin pack is not remotely callable: {pack_id}",
            }
        peer = self.store.get_peer(peer_device_id)
        grant = REMOTE_PLUGIN_PACK_PREFIX + pack_id
        if peer is None or grant not in (peer.get("granted_capabilities") or []):
            return {
                "ok": False,
                "code": "remote_plugin_pack_denied",
                "error": f"remote access to {pack_id} is not granted",
            }
        project = _remote_project(project_id)
        if project is None:
            return {
                "ok": False,
                "code": "remote_project_not_found",
                "error": "authorized project no longer exists",
            }
        workspace_dir = _remote_project_workspace(project)
        operation = command.removeprefix("harness.")
        arguments: dict[str, Any] = {"operation": operation}
        if operation == "list":
            arguments.update({
                "query": str(payload.get("query") or ""),
                "limit": max(1, min(int(payload.get("limit") or 20), 50)),
            })
        elif operation == "describe":
            capability_ids = payload.get("capability_ids")
            if not isinstance(capability_ids, list):
                capability_ids = [str(payload.get("capability_id") or "")]
            arguments["capability_ids"] = [
                str(item) for item in capability_ids if str(item).strip()
            ][:20]
        elif operation == "invoke":
            arguments.update({
                "capability_id": _require_text(
                    payload, "capability_id", max_length=200
                ),
                "arguments": dict(payload.get("arguments") or {}),
            })
            capability_lower = str(payload.get("capability_id") or "").lower()
            invoked_arguments = dict(payload.get("arguments") or {})
            shell_input = str(
                invoked_arguments.get("input")
                or invoked_arguments.get("command")
                or invoked_arguments.get("cmd")
                or ""
            ).lower()
            if (
                "shell" in capability_lower
                and len(shell_input) > 256
                and any(
                    marker in shell_input
                    for marker in (
                        "base64 -d",
                        "base64 --decode",
                        "b64decode(",
                        "frombase64string",
                    )
                )
            ):
                return {
                    "ok": False,
                    "status": "denied",
                    "code": "remote_file_channel_required",
                    "error": (
                        "manual base64 file transfer through a remote shell is disabled; "
                        "use RemoteCyreneFiles"
                    ),
                }
        else:
            raise ValueError("unsupported harness operation")

        authorization = payload.get("authorization")
        if not isinstance(authorization, dict):
            authorization = {}
        authorization_arguments = {
            "device_id": self.store.identity.device_id,
            "project_id": project_id,
            "plugin_pack": pack_id,
            "operation": operation,
            "capability_id": str(payload.get("capability_id") or ""),
            "arguments": dict(payload.get("arguments") or {}),
        }
        expected_authorization_hash = hashlib.sha256(
            json.dumps(
                authorization_arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        authorized_invocation = bool(
            operation == "invoke"
            and authorization.get("approved") is True
            and int(authorization.get("version") or 0) == 1
            and str(authorization.get("arguments_sha256") or "")
            == expected_authorization_hash
        )
        if operation == "invoke" and not authorized_invocation:
            return {
                "ok": False,
                "status": "approval_required",
                "code": "remote_authorization_invalid",
                "error": "exact controller authorization is required",
                "error_origin": "controller",
                "delegable": True,
            }
        controller_mode = str(authorization.get("permission_mode") or "default")
        if controller_mode not in {"plan", "default", "auto", "full_access"}:
            controller_mode = "default"
        # The controller already evaluated this exact, argument-hashed invoke.
        # Mirror its local permission mode for this one bounded call; destructive
        # and external-delivery boundaries remain independently guarded.
        execution_mode = controller_mode

        host = application_plugin_scope()
        if host is None:
            return {
                "ok": False,
                "code": "plugin_host_unavailable",
                "error": "the Plugin application host is unavailable",
            }
        if operation == "list":
            toolbox_arguments: dict[str, Any] = {
                "operation": "list",
            }
        elif operation == "describe":
            toolbox_arguments = {
                "operation": "describe",
                "names": list(arguments.get("capability_ids") or []),
            }
        else:
            capability_id = str(arguments.get("capability_id") or "")
            registered = next(
                (
                    item
                    for item in host.registry.list_plugins()
                    if item.plugin.name == capability_id
                ),
                None,
            )
            if registered is None or registered.pack_id != pack_id:
                return {
                    "ok": False,
                    "code": "remote_plugin_not_in_pack",
                    "error": (
                        f"Plugin {capability_id!r} is not available through {pack_id}"
                    ),
                }
            toolbox_arguments = {
                "operation": "invoke",
                "name": capability_id,
                "arguments": dict(arguments.get("arguments") or {}),
            }

        session_id = f"remote_harness:{peer_device_id}:{project_id}"
        round_id = str(payload.get("call_id") or f"remote-{peer_device_id}")
        run_context = {
            "agent_id": "main",
            "caller": "remote_harness",
            "conversation_source": "remote_harness",
            "round_id": round_id,
            "session_id": session_id,
            "workspace_dir": workspace_dir,
            "permission_mode": execution_mode,
            "temporary_full_access": False,
            "bounded_remote_authorization": authorized_invocation,
            "destructive_confirmation_allow_all": bool(
                authorized_invocation
                and authorization.get("destructive_approved") is True
            ),
        }
        context = PluginContext(
            workspace=Path(workspace_dir),
            data={
                "bot": self.bot,
                "chat_id": 0,
                "db_path": self.db_path,
                "session_id": session_id,
                "run_context": run_context,
            },
            services=getattr(host, "active_services", None)
            or getattr(host, "services", {}),
        )
        timeout = max(
            1.0, min(float(payload.get("timeout_seconds") or 120), 300.0)
        )
        called = await asyncio.wait_for(
            host.runtime.call(
                TOOLBOX_PLUGIN_NAME,
                toolbox_arguments,
                context,
                call_id=round_id,
            ),
            timeout=timeout,
        )
        if not called.success:
            logger.error(
                "Remote Plugin invocation failed: %s",
                called.error,
            )
            return {
                "ok": False,
                "status": "error",
                "code": "remote_plugin_call_failed",
                "error": localized(
                    "The remote Plugin call failed.",
                    "远程插件调用失败。",
                ),
                "plugin_pack": pack_id,
                "operation": operation,
            }
        if operation == "list":
            listed = called.value if isinstance(called.value, Mapping) else {}
            if pack_id not in (listed.get("packs") or []):
                return {
                    "ok": False,
                    "status": "error",
                    "code": "remote_plugin_pack_unavailable",
                    "error": f"Plugin pack {pack_id!r} is unavailable",
                    "plugin_pack": pack_id,
                    "operation": operation,
                }
            called = await asyncio.wait_for(
                host.runtime.call(
                    TOOLBOX_PLUGIN_NAME,
                    {"operation": "describe", "name": pack_id},
                    context,
                    call_id=f"{round_id}:describe",
                ),
                timeout=timeout,
            )
            if not called.success:
                logger.error(
                    "Remote Plugin description failed: %s",
                    called.error,
                )
                return {
                    "ok": False,
                    "status": "error",
                    "code": "remote_plugin_call_failed",
                    "error": localized(
                        "The remote Plugin call failed.",
                        "远程插件调用失败。",
                    ),
                    "plugin_pack": pack_id,
                    "operation": operation,
                }
        result: Any = called.value
        if isinstance(result, dict) and operation in {"list", "describe"}:
            plugins = [
                dict(item)
                for item in result.get("plugins") or []
                if isinstance(item, dict) and str(item.get("pack") or "") == pack_id
            ]
            if operation == "list":
                query = str(arguments.get("query") or "").strip().casefold()
                if query:
                    plugins = [
                        item
                        for item in plugins
                        if query in (
                            str(item.get("name") or "")
                            + " "
                            + str(item.get("description") or "")
                        ).casefold()
                    ]
                plugins = plugins[: int(arguments.get("limit") or 20)]
            result = {
                "operation": operation,
                "pack": pack_id,
                "plugins": plugins,
            }
        effective_result: Mapping[str, Any] = (
            result if isinstance(result, Mapping) else {}
        )
        if operation == "invoke" and isinstance(result, Mapping):
            nested_result = result.get("result")
            if isinstance(nested_result, str):
                try:
                    decoded_result = json.loads(nested_result)
                except (TypeError, ValueError, json.JSONDecodeError):
                    decoded_result = None
                if isinstance(decoded_result, Mapping):
                    nested_result = decoded_result
            if isinstance(nested_result, Mapping):
                effective_result = nested_result
        result_status = str(effective_result.get("status") or "").lower()
        awaiting = result_status == "awaiting_user"
        failed = (
            effective_result.get("ok") is False
            or result_status in {"error", "denied", "cancelled"}
            or awaiting
        )
        return {
            "ok": not failed,
            "status": (
                "approval_required"
                if awaiting
                else (result_status or ("error" if failed else "completed"))
            ),
            **({
                "code": "remote_target_approval_required",
                "error": "the target requires a non-delegable local or OS approval",
                "error_origin": "target",
                "delegable": False,
            } if awaiting else {}),
            "plugin_pack": pack_id,
            "operation": operation,
            "authorization": {
                "mode": controller_mode,
                "arguments_sha256": expected_authorization_hash,
                "scope": "single_invocation",
            },
            "result": result,
        }

    async def _runs_guide(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        result = await self.chat.guide(chat_id, {
            "message": _require_text(payload, "message"),
            "clientRequestId": str(payload.get("request_id") or ""),
        })
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _runs_interrupt(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        run_manager = self.chat.run_manager
        run = run_manager.get(chat_id)
        interrupted = run_manager.interrupt(chat_id)
        if (
            interrupted
            and run is not None
            and run.task is not None
            and not run.done.is_set()
        ):
            try:
                await asyncio.wait_for(asyncio.shield(run.done.wait()), timeout=8.0)
            except asyncio.TimeoutError:
                return {
                    "ok": False,
                    "interrupted": False,
                    "code": "chat_interrupt_timeout",
                    "error": "chat interruption is still settling",
                }
        return {
            "ok": interrupted,
            "interrupted": interrupted,
            "code": "" if interrupted else "chat_not_running",
        }

    async def _conversation_goal(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, Any] | tuple[None, dict[str, Any]]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return None, dict(chat or {"ok": False, "error": "chat not found"})
        service = application_plugin_service("goal")
        if service is None:
            return None, {
                "ok": False,
                "code": "plugin_host_unavailable",
                "error": "Conversation Goal service is unavailable",
            }
        return chat_id, service

    async def _goals_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, service = await self._conversation_goal(project_id, payload)
        if chat_id is None:
            return dict(service)
        goal = await service.get(chat_id)
        return {"ok": True, "goal": service.public(goal) if goal else None}

    async def _goals_update(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, service = await self._conversation_goal(project_id, payload)
        if chat_id is None:
            return dict(service)
        goal = await service.update(
            chat_id,
            {key: value for key, value in payload.items() if key != "chat_id"},
        )
        return {"ok": True, "goal": service.public(goal)}

    async def _goals_confirm(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, service = await self._conversation_goal(project_id, payload)
        if chat_id is None:
            return dict(service)
        goal = await service.confirm(
            chat_id,
            {key: value for key, value in payload.items() if key != "chat_id"},
        )
        return {"ok": True, "goal": service.public(goal)}

    async def _goals_control(
        self,
        command: str,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, service = await self._conversation_goal(project_id, payload)
        if chat_id is None:
            return dict(service)
        action = command.removeprefix("goals.")
        goal = await getattr(service, action)(chat_id)
        return {"ok": True, "goal": service.public(goal)}

    async def _approvals_respond(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        result = await self.chat.answer(chat_id, {
            "question_id": _require_text(payload, "question_id", max_length=500),
            "answer": _require_text(payload, "answer"),
            "mode": _permission_mode(payload, allowed=frozenset({"auto", "default"})),
        })
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _attachments_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Read one chunk of a file explicitly referenced by a shared chat."""
        _chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        attachment_id = _require_text(
            payload,
            "attachment_id",
            max_length=240,
        )
        try:
            attachment, file_path = referenced_chat_attachment_target(
                chat,
                attachment_id,
            )
        except LookupError as exc:
            logger.info(
                "Remote attachment was not found",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            return {
                "ok": False,
                "code": "attachment_not_found",
                "error": localized(
                    "Attachment not found.",
                    "未找到附件。",
                ),
            }
        except FileNotFoundError as exc:
            logger.info(
                "Remote attachment file is unavailable",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            return {
                "ok": False,
                "code": "attachment_unavailable",
                "error": localized(
                    "The attachment is unavailable.",
                    "附件不可用。",
                ),
            }
        variant = str(payload.get("variant") or "original").strip().lower()
        if variant not in {"original", "thumbnail"}:
            return {
                "ok": False,
                "code": "attachment_variant_invalid",
                "error": "attachment variant must be original or thumbnail",
            }
        transfer_path = file_path
        media_type = str(
            attachment.get("content_type")
            or attachment.get("mediaType")
            or mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream"
        )
        preview_width = None
        preview_height = None
        if variant == "thumbnail":
            if not media_type.lower().startswith("image/"):
                return {
                    "ok": False,
                    "code": "thumbnail_unsupported",
                    "error": "thumbnail previews are only available for images",
                }
            try:
                transfer_path, preview_width, preview_height = await asyncio.to_thread(
                    _image_thumbnail,
                    file_path,
                )
            except Exception as exc:
                logger.info(
                    "Remote attachment thumbnail is unavailable",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                return {
                    "ok": False,
                    "code": "thumbnail_unavailable",
                    "error": localized(
                        "The thumbnail is unavailable.",
                        "缩略图不可用。",
                    ),
                }
            media_type = "image/webp"
        return {
            "ok": True,
            "attachment": _attachment_summary(attachment),
            "filename": transfer_path.name,
            "media_type": media_type,
            "variant": variant,
            "original_size": file_path.stat().st_size,
            **({"width": preview_width} if preview_width is not None else {}),
            **({"height": preview_height} if preview_height is not None else {}),
            **_file_chunk(transfer_path, payload),
        }

class RemoteControlRuntime:
    """Own the LAN listener and encrypted gateway lifecycle."""

    def __init__(
        self,
        *,
        db_path: str,
        store: RemoteControlStore,
        executor: RemoteCommandExecutor,
        lan_host: str = "0.0.0.0",
        lan_port: int | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.store = store
        self.executor = executor
        self.lan_host = str(lan_host)
        self.requested_lan_port = (
            int(lan_port) if lan_port is not None else None
        )
        self.lan_port = int(
            lan_port
            if lan_port is not None
            else store.get_settings().get("listen_port") or 37841
        )
        self.used_fallback_port = False
        self.gateway: RemoteGateway | None = None
        self.pairing_server: DirectPairingServer | None = None
        self._running = False
        self._lock: Any = None
        self.last_error = ""

    async def start(self) -> None:
        import asyncio

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            self._running = True
            await self._apply_locked()

    async def reload(self) -> None:
        if not self._running:
            return
        if self._lock is None:
            await self.start()
            return
        async with self._lock:
            await self._apply_locked()

    async def stop(self) -> None:
        if self._lock is None:
            self._running = False
            return
        async with self._lock:
            self._running = False
            await self._stop_gateway_locked()
            await self._stop_pairing_server_locked()

    async def _apply_locked(self) -> None:
        await self._stop_gateway_locked()
        await self._stop_pairing_server_locked()
        settings = self.store.get_settings()
        if not settings["enabled"]:
            self.last_error = ""
            return
        try:
            requested_port = int(
                self.requested_lan_port
                if self.requested_lan_port is not None
                else settings.get("listen_port") or 37841
            )
            pairing_server = DirectPairingServer(
                self.store, host=self.lan_host, port=requested_port
            )
            await pairing_server.start()
            self.pairing_server = pairing_server
            self.lan_port = pairing_server.port
            self.used_fallback_port = pairing_server.used_fallback_port
            if self.requested_lan_port is None:
                await asyncio.to_thread(
                    self.store.update_listen_port,
                    self.lan_port,
                )
        except Exception:
            logger.error("LAN control listener failed", exc_info=True)
            self.last_error = localized(
                "The LAN control listener could not start.",
                "局域网控制监听器无法启动。",
            )
            self.store.audit(
                "direct_pairing_listener_failed",
                outcome="error",
                detail={"code": "remote_listener_start_failed"},
            )
            return
        try:
            gateway = RemoteGateway(self.store, pairing_server, self.executor)
            if hasattr(self.executor, "set_remote_event_sender"):
                self.executor.set_remote_event_sender(gateway.send_event)
            await gateway.start()
        except Exception:
            logger.error("Remote gateway failed to start", exc_info=True)
            if hasattr(self.executor, "set_remote_event_sender"):
                self.executor.set_remote_event_sender(None)
            self.last_error = localized(
                "The remote gateway could not start.",
                "远程网关无法启动。",
            )
            self.store.audit(
                "remote_gateway_start_failed",
                outcome="error",
                detail={"code": "remote_gateway_start_failed"},
            )
            return
        self.gateway = gateway
        self.last_error = ""
        register_remote_gateway(self.db_path, gateway)

    async def _stop_gateway_locked(self) -> None:
        gateway, self.gateway = self.gateway, None
        if gateway is None:
            return
        unregister_remote_gateway(self.db_path, gateway)
        if hasattr(self.executor, "set_remote_event_sender"):
            self.executor.set_remote_event_sender(None)
        await gateway.stop()

    async def _stop_pairing_server_locked(self) -> None:
        server, self.pairing_server = self.pairing_server, None
        if server is not None:
            await server.stop()

    def status(self) -> dict[str, Any]:
        settings = self.store.get_settings()
        gateway = self.gateway
        if not settings["enabled"]:
            state = "disabled"
            detail = localized("Remote access is disabled.", "远程访问已停用。")
        elif self.last_error:
            state = "error"
            detail = self.last_error
        elif gateway is not None and gateway.connected:
            state = "connected"
            detail = localized(
                "LAN end-to-end encrypted control is ready.",
                "局域网端到端加密控制已就绪。",
            )
        elif gateway is not None and gateway.started:
            state = "connecting"
            detail = localized(
                "Starting the LAN end-to-end encrypted control listener.",
                "正在启动局域网端到端加密控制监听器。",
            )
        else:
            state = "configured"
            detail = localized(
                "LAN control will start with the Cyrene runtime.",
                "局域网控制将在 Cyrene 运行时启动。",
            )
        return {
            "status": state,
            "connected": bool(gateway and gateway.connected),
            "detail": detail,
            "direct_pairing": bool(self.pairing_server and self.pairing_server.running),
            "lan_port": self.lan_port,
            "requested_lan_port": (
                self.requested_lan_port
                if self.requested_lan_port is not None
                else int(settings.get("listen_port") or 37841)
            ),
            "port_fallback": self.lan_port != DIRECT_PAIRING_PORT,
        }

__all__ = [
    "RemoteCommandExecutor",
    "RemoteControlRuntime",
    "public_remote_event",
]
