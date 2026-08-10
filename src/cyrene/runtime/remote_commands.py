"""Typed remote-command application adapter.

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
import math
import mimetypes
import os
import platform
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from PIL import Image, ImageOps

from cyrene.agent.context import bind_run_context
from cyrene.runtime.remote_control import (
    DIRECT_PAIRING_PORT,
    REMOTE_CAPABILITIES,
    REMOTE_TOOL_PACK_PREFIX,
    REMOTE_TOOL_PACK_WIRE_NAMES,
    RemoteControlStore,
    RemoteGateway,
    register_remote_gateway,
    unregister_remote_gateway,
)
from cyrene.tooling.gateway import execute_wire_tool_in_context
from cyrene.tooling.backends.shells import (
    close_shell,
    get_shell_snapshot,
    interrupt_shell,
    send_shell,
    start_shell,
)
from cyrene.tooling.snapshot import build_catalog_snapshot
from cyrene.tooling.types import ToolExecutionContext
from cyrene.runtime.remote_pairing import DirectPairingServer
from cyrene.runtime.settings_store import get_all as get_web_settings
from cyrene.runtime.settings_store import set_ as set_setting
from cyrene.workbench import runtime as workbench_runtime
from cyrene.workbench.chat import (
    chat_context_payload,
    context_segment_tokens,
    workbench_subagent_payload,
)
from cyrene.workbench.workspace_changes import (
    get_chat_file_change,
    list_chat_change_sets,
)
from route import schemas as api_models

_DEFAULT_TRANSFER_CHUNK_BYTES = 512 * 1024
_MAX_TRANSFER_CHUNK_BYTES = 1024 * 1024
_THUMBNAIL_MAX_DIMENSION = 960
_THUMBNAIL_WEBP_QUALITY = 72
_REMOTE_PUBLIC_EVENT_TYPES = {
    "ack",
    "auto_review",
    "awaiting_user",
    "error",
    "guidance_received",
    "intermediate_message",
    "interrupted",
    "permission_decision",
    "phase_transition",
    "plan",
    "plan_progress",
    "reasoning_delta",
    "reasoning_done",
    "reasoning_start",
    "reply_delta",
    "reply_done",
    "reply_start",
    "run_finalizing",
    "saved",
    "subagent_update",
    "tool_call_finished",
    "tool_call_progress",
    "tool_call_started",
    "user_question",
    "user_question_answered",
}

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
    {"id": "tool_packs", "label": "Tool packages", "label_zh": "工具包"},
)

_REMOTE_TOOL_PACK_LABELS = {
    "browser_tools": ("Browser tools", "浏览器工具"),
    "code_tools": ("Code tools", "代码工具"),
    "delivery_tools": ("Delivery tools", "交付工具"),
    "desktop_tools": ("Desktop tools", "桌面工具"),
    "entity_tools": ("Entity tools", "实体工具"),
    "integration_tools": ("Integration tools", "集成工具"),
    "knowledge_tools": ("Knowledge tools", "知识工具"),
    "map_tools": ("Map tools", "地图工具"),
    "memory_tools": ("Memory tools", "记忆工具"),
    "remote_tools": ("Remote Cyrene tools", "远程 Cyrene 工具"),
    "skill_tools": ("Skill tools", "技能工具"),
    "subagent_tools": ("Subagent tools", "子代理工具"),
    "task_tools": ("Task tools", "任务工具"),
}

_REMOTE_TOOL_PACK_DESCRIPTIONS_ZH = {
    "browser_tools": "启用浏览器导航、页面快照、截图、点击、输入、等待和网络检查工具。",
    "code_tools": "代码分析、Git、持久化终端以及 Claude Code 辅助能力。",
    "delivery_tools": "发送进度更新、通知、消息和文件。",
    "desktop_tools": "通过应用控制发现桌面应用并与其交互。",
    "entity_tools": "追踪、查询、更新、列出和删除持久实体。",
    "integration_tools": "使用动态连接的 MCP 与外部集成能力。",
    "knowledge_tools": "搜索项目知识文档，以及文献库内容与元数据。",
    "map_tools": "创建地图标记并连接地点。",
    "memory_tools": "检索和维护对话记忆、短期记忆与项目记忆。",
    "remote_tools": "操作当前对话中已选择的配对 Cyrene 设备。",
    "skill_tools": "发现、安装、移除、检查和运行 Agent Skills。",
    "subagent_tools": "创建、检查子代理并与其通信。",
    "task_tools": "管理定时任务、持久任务目标与计划状态。",
}

_REMOTE_TOOL_PACK_DESCRIPTIONS = {
    "browser_tools": "Enable browser navigation, page snapshots, screenshots, clicks, typing, waiting, and network inspection.",
    "code_tools": "Code analysis, Git, persistent shells, and Claude Code helpers.",
    "delivery_tools": "Send progress updates, notifications, messages, and files.",
    "desktop_tools": "Discover and interact with desktop applications through App Use.",
    "entity_tools": "Track, query, update, list, and delete durable entities.",
    "integration_tools": "Use dynamically connected MCP and external integration capabilities.",
    "knowledge_tools": "Search project knowledge documents and literature-library content and metadata.",
    "map_tools": "Create map pins and connect locations.",
    "memory_tools": "Retrieve and maintain conversation, short-term, and project memory.",
    "remote_tools": "Operate paired Cyrene devices selected in the current chat.",
    "skill_tools": "Discover, install, remove, inspect, and run Agent Skills.",
    "subagent_tools": "Spawn, inspect, and communicate with subagents.",
    "task_tools": "Manage scheduled tasks, durable task goals, and plan state.",
}


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
        "codex_budget_enabled", "budget", "boolean", "Codex quota guard", "Codex 配额保护",
        default=True,
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
        "budget_mode", "budget", "enum", "Budget mode", "预算模式", default="normal",
        options=[_option("economy", "Economy", "节省"), _option("normal", "Normal", "普通")],
    ),
    _remote_setting_field(
        "budget_start_day", "budget", "integer", "Billing cycle start day", "账期起始日",
        default=1, minimum=1, maximum=28,
    ),
)


def _public_model_candidate(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    model = str(raw.get("model") or raw.get("name") or raw.get("id") or "").strip()
    if not model:
        return None
    provider = str(raw.get("provider") or "openai_compatible").strip()
    return {
        "id": str(
            raw.get("id")
            or f"model-{uuid.uuid5(uuid.NAMESPACE_URL, model).hex[:10]}"
        ).strip(),
        "name": str(raw.get("name") or model).strip(),
        "model": model,
        "provider": provider,
        "reasoning_effort": str(raw.get("reasoning_effort") or "").strip(),
        "base_url": str(raw.get("base_url") or "").strip(),
        "description": str(raw.get("desc") or "").strip(),
        "context": str(raw.get("ctx") or "").strip(),
        "price": str(raw.get("price") or "").strip(),
        "api_key_configured": bool(str(raw.get("api_key") or "").strip()),
    }


def _public_model_settings() -> dict[str, Any]:
    from cyrene.runtime.settings_store import (
        get_codex_model,
        get_custom_models,
        get_model_source,
        get_secondary_model,
        get_vision_models,
    )

    custom = [
        item
        for raw in get_custom_models() or []
        if (item := _public_model_candidate(raw)) is not None
    ]
    codex = _public_model_candidate(get_codex_model())
    vision = [
        item
        for raw in get_vision_models() or []
        if (item := _public_model_candidate(raw)) is not None
    ]
    secondary_raw = get_secondary_model()
    secondary = _public_model_candidate(secondary_raw)
    if secondary is not None:
        secondary["context_limit"] = int(secondary_raw.get("ctx_limit") or 0)
        secondary["max_concurrency"] = int(
            secondary_raw.get("max_concurrency") or 0
        )
    return {
        "source": get_model_source(),
        "custom_models": custom,
        "codex_model": codex,
        "vision_models": vision,
        "secondary_model": secondary,
    }


def _model_match_key(raw: dict[str, Any]) -> tuple[str, str]:
    return (
        str(raw.get("id") or "").strip(),
        str(raw.get("model") or raw.get("name") or "").strip(),
    )


def _normalize_remote_model_candidate(
    raw: Any,
    *,
    previous: list[dict[str, Any]],
    allow_codex: bool,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("model candidate must be an object")
    model = str(raw.get("model") or raw.get("name") or "").strip()
    if not model or len(model) > 200:
        raise ValueError("model identifier is required and must be at most 200 characters")
    provider = str(raw.get("provider") or "openai_compatible").strip()
    if provider not in {"openai_compatible", "codex_oauth"}:
        raise ValueError("unsupported model provider")
    if provider == "codex_oauth" and not allow_codex:
        raise ValueError("Codex OAuth is not supported for this model role")
    model_id = str(raw.get("id") or f"model-{uuid.uuid4().hex[:10]}").strip()
    if not model_id or len(model_id) > 100:
        raise ValueError("invalid model id")

    prior = next(
        (
            candidate
            for candidate in previous
            if _model_match_key(candidate) in {
                (model_id, model),
                (model_id, str(candidate.get("model") or "")),
                (str(candidate.get("id") or ""), model),
            }
        ),
        {},
    )
    base_url = str(raw.get("base_url") or prior.get("base_url") or "").strip()
    if provider != "codex_oauth":
        parsed = urlsplit(base_url)
        if (
            not base_url
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("model Base URL must be an http(s) URL without credentials")
    else:
        from cyrene.model_runtime.codex_provider import CODEX_BASE_URL

        base_url = CODEX_BASE_URL

    submitted_key = str(raw.get("api_key") or "").strip()
    api_key = submitted_key or str(prior.get("api_key") or "").strip()
    reasoning = str(raw.get("reasoning_effort") or "").strip().lower()
    if reasoning not in {"", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("invalid reasoning effort")
    return {
        "id": model_id,
        "name": str(raw.get("name") or model).strip()[:200] or model,
        "model": model,
        "provider": provider,
        "reasoning_effort": reasoning,
        "base_url": base_url,
        "api_key": "" if provider == "codex_oauth" else api_key,
        "desc": str(raw.get("description") or raw.get("desc") or "").strip()[:500],
        "ctx": str(raw.get("context") or raw.get("ctx") or "").strip()[:40],
        "price": str(raw.get("price") or "").strip()[:80],
    }


def _update_remote_model_settings(raw: Any) -> None:
    from cyrene.runtime import config_store
    from cyrene.runtime.settings_store import (
        get_codex_model,
        get_custom_models,
        get_secondary_model,
        get_vision_models,
        save_codex_model,
        save_custom_models,
        save_model_source,
        save_models,
        save_secondary_model,
        save_vision_models,
    )

    if not isinstance(raw, dict):
        raise ValueError("models must be an object")
    source = str(raw.get("source") or "custom").strip().lower()
    if source not in {"custom", "codex"}:
        raise ValueError("model source must be custom or codex")

    previous_custom = [item for item in get_custom_models() or [] if isinstance(item, dict)]
    previous_vision = [item for item in get_vision_models() or [] if isinstance(item, dict)]
    previous_codex = get_codex_model()
    previous_secondary = get_secondary_model()

    custom_raw = raw.get("custom_models")
    if not isinstance(custom_raw, list) or not custom_raw or len(custom_raw) > 10:
        raise ValueError("custom_models must contain between 1 and 10 models")
    custom = [
        _normalize_remote_model_candidate(
            item,
            previous=previous_custom,
            allow_codex=False,
        )
        for item in custom_raw
    ]

    codex_raw = raw.get("codex_model")
    codex = (
        _normalize_remote_model_candidate(
            codex_raw,
            previous=[previous_codex] if isinstance(previous_codex, dict) else [],
            allow_codex=True,
        )
        if isinstance(codex_raw, dict)
        else None
    )
    if codex is not None and codex["provider"] != "codex_oauth":
        raise ValueError("Codex model must use the codex_oauth provider")
    if source == "codex" and codex is None:
        raise ValueError("a Codex model is required for the Codex source")

    vision_raw = raw.get("vision_models")
    if not isinstance(vision_raw, list) or not vision_raw or len(vision_raw) > 10:
        raise ValueError("vision_models must contain between 1 and 10 models")
    vision = [
        _normalize_remote_model_candidate(
            item,
            previous=previous_vision,
            allow_codex=False,
        )
        for item in vision_raw
    ]

    secondary_raw = raw.get("secondary_model")
    secondary = None
    if isinstance(secondary_raw, dict) and str(
        secondary_raw.get("model") or ""
    ).strip():
        secondary = _normalize_remote_model_candidate(
            secondary_raw,
            previous=[previous_secondary]
            if isinstance(previous_secondary, dict)
            else [],
            allow_codex=False,
        )
        secondary["ctx_limit"] = max(
            0, min(int(secondary_raw.get("context_limit") or 0), 4_000_000)
        )
        secondary["max_concurrency"] = max(
            0, min(int(secondary_raw.get("max_concurrency") or 0), 128)
        )

    save_custom_models(custom)
    if codex is not None:
        save_codex_model(codex)
    save_model_source(source)
    active_models = [codex] if source == "codex" and codex is not None else custom
    save_models(active_models)
    save_vision_models(vision)
    save_secondary_model(secondary or {})

    primary = active_models[0]
    env_updates = {"OPENAI_MODEL": str(primary["model"])}
    if primary["provider"] != "codex_oauth":
        env_updates.update(
            {
                "OPENAI_BASE_URL": str(primary["base_url"]),
                "OPENAI_API_KEY": str(primary["api_key"]),
            }
        )
    config_store.set_env_many(env_updates)


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


def _public_intermediate_message(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key]
        for key in (
            "id",
            "role",
            "content",
            "text",
            "kind",
            "status",
            "createdAt",
            "liveDedupeKey",
            "opensActivity",
        )
        if key in value
    }
    if isinstance(value.get("attachments"), list):
        result["attachments"] = [
            summary
            for item in value["attachments"]
            if (summary := _attachment_summary(item)) is not None
        ]
    if isinstance(value.get("trace"), list):
        result["trace"] = value["trace"]
    return result or None


def public_remote_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return the fixed public event DTO; unknown/internal events are omitted."""
    event_type = str(event.get("type") or "")
    if event_type not in _REMOTE_PUBLIC_EVENT_TYPES:
        return None
    result: dict[str, Any] = {
        "type": event_type,
        "cursor": int(event.get("_seq") or 0),
        "run_id": str(event.get("runId") or ""),
    }
    for key in (
        "chatId", "status", "code", "delta", "response", "message",
        "phase", "provider", "tool_call_id", "tool", "label", "current",
        "total", "progress", "failed", "from", "to", "detail",
        "detail_key", "step", "note", "round_id",
        "approved", "operation", "path_hint", "rationale", "agent_id",
        "caller", "task", "mode", "outcome", "stop_reason", "result_preview",
        "created_at", "updated_at", "message_count",
    ):
        value = event.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if key in event:
                result[key] = value
    for key in ("args", "detail_params", "plan"):
        value = event.get(key)
        if isinstance(value, (dict, list)):
            # Tool arguments reach this point only after the runtime has
            # redacted them. Their structure is needed for Workbench-equivalent
            # labels on a paired controller.
            result[key] = value
    if event_type == "awaiting_user":
        question = _public_pending_question(
            event.get("pending_question") or event.get("pendingQuestion")
        )
        if question is not None:
            result["pending_question"] = question
    if event_type == "intermediate_message":
        message = _public_intermediate_message(event.get("message"))
        if message is not None:
            result["message"] = message
    return result


def _json_response_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, JSONResponse):
        return dict(value or {}) if isinstance(value, dict) else {"data": value}
    try:
        payload = json.loads(bytes(value.body).decode("utf-8"))
    except Exception:
        payload = {"error": "remote command failed"}
    if not isinstance(payload, dict):
        payload = {"error": str(payload)}
    status_code = int(value.status_code)
    ok = 200 <= status_code < 300 and payload.get("ok") is not False
    return {"ok": ok, "status_code": status_code, **payload}


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


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    plan = [
        {
            key: item[key]
            for key in ("id", "title", "description", "status")
            if key in item
        }
        for item in task.get("plan") or []
        if isinstance(item, dict)
    ]
    return {
        "id": str(task.get("id") or ""),
        "project_id": str(task.get("projectId") or ""),
        "title": str(task.get("title") or ""),
        "goal": str(task.get("goal") or ""),
        "status": str(task.get("status") or "idle"),
        "priority": str(task.get("priority") or "medium"),
        "created_at": str(task.get("createdAt") or ""),
        "updated_at": str(task.get("updatedAt") or ""),
        "plan": plan,
        "pending_question": _public_pending_question(
            task.get("pendingQuestion")
        ),
        "artifact_count": len(task.get("artifacts") or []),
    }


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
    from cyrene.runtime.attachments import (
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
                    "url": f"/api/chat/upload/{target.name}",
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


_REMOTE_PROGRESSIVE_TOOL_PACKAGES = {
    "code_tools",
    "browser_tools",
    "desktop_tools",
    "memory_tools",
    "knowledge_tools",
    "task_tools",
    "entity_tools",
    "map_tools",
    "subagent_tools",
    "delivery_tools",
    "skill_tools",
    "remote_tools",
    "integration_tools",
}


def _remote_used_tool_packages(chat: dict[str, Any]) -> list[str]:
    used: list[str] = []
    seen: set[str] = set()
    aliases = {
        "pin_location": "map_tools",
        "connect_pins": "map_tools",
        "spawn_subagent": "subagent_tools",
        "send_message_to_subagent": "subagent_tools",
        "wait_for_subagents": "subagent_tools",
    }
    for message in chat.get("messages") or []:
        if not isinstance(message, dict):
            continue
        entries = [
            *(message.get("trace") or []),
            *(message.get("tools") or []),
        ]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw_name = str(entry.get("tool") or entry.get("name") or "").strip()
            wire_name = aliases.get(raw_name, raw_name)
            if (
                wire_name in _REMOTE_PROGRESSIVE_TOOL_PACKAGES
                and wire_name not in seen
            ):
                seen.add(wire_name)
                used.append(wire_name)
    return used


def _remote_context_blocks(chat_id: str) -> dict[str, Any]:
    from cyrene.agent.state import _session_state_file
    from cyrene.model_runtime.client import approx_token_count
    from cyrene.runtime.io import read_json_safe

    data = read_json_safe(_session_state_file(chat_id))
    if not isinstance(data, dict):
        return {"layers": [], "totalTokensEst": 0, "messageTokens": 0}
    messages = data.get("messages")
    if not isinstance(messages, list):
        messages = []
    segments = context_segment_tokens(messages)
    message_total = sum(segments.values())
    layers: list[dict[str, Any]] = []

    system_blocks = data.get("system_context_blocks")
    if isinstance(system_blocks, list) and system_blocks:
        public_blocks = [
            {
                key: block.get(key)
                for key in ("id", "type", "tokens_est", "chars", "source", "reason")
                if key in block
            }
            for block in system_blocks
            if isinstance(block, dict)
        ]
        system_tokens = sum(
            int(block.get("tokens_est") or 0)
            for block in public_blocks
        )
        layers.append(
            {
                "id": "system_prefix",
                "label": "System Prefix",
                "blocks": public_blocks,
                "totalTokens": system_tokens,
            }
        )

    ephemeral = data.get("ephemeral_context")
    if isinstance(ephemeral, str) and ephemeral.strip():
        tokens = approx_token_count(ephemeral)
        layers.append(
            {
                "id": "ephemeral",
                "label": "Ephemeral Tail",
                "blocks": [
                    {
                        "id": "ephemeral.run",
                        "type": "ephemeral",
                        "tokens_est": tokens,
                        "chars": len(ephemeral),
                    }
                ],
                "totalTokens": tokens,
            }
        )

    message_blocks = [
        {
            "id": f"segment.{key}",
            "type": key,
            "tokens_est": int(segments.get(key) or 0),
        }
        for key in ("compacted", "system", "user", "assistant", "tool")
        if int(segments.get(key) or 0) > 0
    ]
    if message_blocks:
        layers.append(
            {
                "id": "messages",
                "label": "Conversation Messages",
                "blocks": message_blocks,
                "totalTokens": message_total,
            }
        )
    return {
        "layers": layers,
        "totalTokensEst": sum(int(layer["totalTokens"]) for layer in layers),
        "messageTokens": message_total,
    }


def _remote_map_data(chat_id: str) -> dict[str, Any]:
    from cyrene.agent.state import _session_state_file
    from cyrene.runtime.io import read_json_safe

    data = read_json_safe(_session_state_file(chat_id))
    if not isinstance(data, dict):
        return {"pins": [], "routes": []}
    return {
        "pins": [
            dict(item)
            for item in data.get("map_pins") or []
            if isinstance(item, dict)
        ],
        "routes": [
            dict(item)
            for item in data.get("map_routes") or []
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


def _artifact_summary(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    artifact_id = str(item.get("id") or "")
    if not artifact_id:
        return None
    return {
        "id": artifact_id,
        "name": str(item.get("name") or ""),
        "type": str(item.get("type") or ""),
        "status": str(item.get("status") or ""),
        "created_at": str(item.get("createdAt") or ""),
        "size": int(item["size"]) if item.get("size") is not None else None,
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

    from cyrene.runtime.attachments import EXPORTS_DIR, UPLOADS_DIR

    url = str(attachment.get("url") or "")
    if url.startswith("/api/chat/upload/"):
        candidate_roots = (UPLOADS_DIR,)
    elif url.startswith("/api/chat/export/"):
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


def _task_detail(task: dict[str, Any]) -> dict[str, Any]:
    plan = [
        {
            key: item[key]
            for key in ("id", "title", "description", "status")
            if key in item
        }
        for item in task.get("plan") or []
        if isinstance(item, dict)
    ]
    artifacts = [
        summary
        for item in task.get("artifacts") or []
        if (summary := _artifact_summary(item)) is not None
    ]
    goal_loop = task.get("goalLoop")
    return {
        **_task_summary(task),
        "plan": plan,
        "pending_question": _public_pending_question(
            task.get("pendingQuestion")
        ),
        "artifacts": artifacts,
        "goal_loop": (
            {
                key: goal_loop[key]
                for key in (
                    "id",
                    "status",
                    "phase",
                    "currentStepId",
                    "stopReason",
                    "activeSeconds",
                    "maxActiveSeconds",
                    "repairRound",
                    "maxRepairRounds",
                    "updatedAt",
                )
                if key in goal_loop
            }
            if isinstance(goal_loop, dict)
            else None
        ),
    }


class RemoteCommandExecutor:
    """Execute the protocol's fixed command set against local Workbench state."""

    def __init__(
        self,
        *,
        store: RemoteControlStore,
        chat_adapter: dict[str, Any],
        project_adapter: dict[str, Any],
        task_adapter: dict[str, Any],
        goal_loop_adapter: dict[str, Any] | None = None,
        bot: Any = None,
        db_path: str = "",
    ) -> None:
        self.store = store
        self.bot = bot
        self.db_path = str(db_path or "")
        self.chat = chat_adapter
        self.project = project_adapter
        self.task = task_adapter
        self.goal_loop = goal_loop_adapter or {}
        self._remote_shell_owners: dict[str, tuple[str, str]] = {}

    async def __call__(
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
                "protocol_version": 1,
                "capabilities": sorted(REMOTE_CAPABILITIES),
                "remote_tool_packages": list(REMOTE_TOOL_PACK_WIRE_NAMES),
            }
        if command == "projects.list":
            return await self._projects_list(peer_device_id)
        if command == "chats.list":
            return await self._chats_list(project_id)
        if command == "chats.create":
            return await self._chats_create(project_id, payload)
        if command == "chats.update":
            return await self._chats_update(project_id, payload)
        if command == "chats.delete":
            return await self._chats_delete(project_id, payload)
        if command == "chats.read":
            return await self._chats_read(project_id, payload)
        if command == "changes.read":
            return await self._changes_read(project_id, payload)
        if command == "chats.send":
            return await self._chats_send(project_id, payload)
        if command == "runs.read":
            return await self._runs_read(project_id, payload)
        if command == "runs.events":
            return await self._runs_events(project_id, payload)
        if command == "runs.wait":
            return await self._runs_wait(project_id, payload)
        if command == "runs.guide":
            return await self._runs_guide(project_id, payload)
        if command == "runs.interrupt":
            return await self._runs_interrupt(project_id, payload)
        if command == "tasks.list":
            return await self._tasks_list(project_id)
        if command == "tasks.create":
            return await self._tasks_create(project_id, payload)
        if command == "tasks.read":
            return await self._tasks_read(project_id, payload)
        if command == "tasks.dispatch":
            return await self._tasks_dispatch(project_id, payload)
        if command == "tasks.approve_plan":
            return await self._tasks_approve_plan(project_id, payload)
        if command == "tasks.run_step":
            return await self._tasks_run_step(project_id, payload)
        if command in {"tasks.pause", "tasks.resume", "tasks.cancel"}:
            return await self._tasks_control(command, project_id, payload)
        if command == "approvals.respond":
            return await self._approvals_respond(project_id, payload)
        if command == "artifacts.list":
            return await self._artifacts_list(project_id, payload)
        if command == "artifacts.read":
            return await self._artifacts_read(project_id, payload)
        if command == "attachments.read":
            return await self._attachments_read(project_id, payload)
        if command == "settings.read":
            return self._settings_read()
        if command == "settings.models.copy":
            return self._settings_models_copy(payload)
        if command == "settings.update":
            return self._settings_update(payload)
        if command == "settings.openai_oauth.read":
            return await self._settings_openai_oauth_read()
        if command == "settings.openai_oauth.login":
            return await self._settings_openai_oauth_login()
        if command == "settings.openai_oauth.logout":
            return await self._settings_openai_oauth_logout()
        if command.startswith("shell."):
            return await self._shell_command(
                peer_device_id, command, project_id, payload
            )
        if command.startswith("harness."):
            return await self._harness_command(
                peer_device_id, command, project_id, payload
            )
        return {
            "ok": False,
            "code": "remote_command_unsupported",
            "error": f"unsupported remote command: {command}",
        }

    @staticmethod
    def _public_shell_snapshot(
        snapshot: dict[str, Any],
        *,
        cursor: int = 0,
    ) -> dict[str, Any]:
        safe_cursor = max(0, int(cursor or 0))
        lines = [
            {
                "seq": int(item.get("seq") or 0),
                "kind": str(item.get("kind") or "out"),
                "text": str(item.get("text") or ""),
            }
            for item in snapshot.get("lines") or []
            if isinstance(item, dict)
            and int(item.get("seq") or 0) > safe_cursor
        ]
        return {
            "ok": True,
            "shell_id": str(snapshot.get("id") or ""),
            "status": str(snapshot.get("status") or "closed"),
            "cwd": str(snapshot.get("cwd") or "."),
            "exit_code": snapshot.get("exitCode"),
            "next_cursor": int(snapshot.get("nextCursor") or safe_cursor),
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

    def _owned_shell(
        self,
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
        snapshot = get_shell_snapshot(shell_id)
        if snapshot is None:
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
        project = workbench_runtime._workbench_find_project_lightweight(project_id)
        if project is None:
            return {
                "ok": False,
                "code": "remote_project_not_found",
                "error": "authorized project no longer exists",
            }
        workspace_dir = (
            workbench_runtime._workbench_resolve_workspace_dir(project)
            or str(workbench_runtime.WORKSPACE_DIR)
        )
        if command == "shell.open":
            snapshot = await start_shell(
                cwd=workspace_dir,
                title=f"Mobile Shell · {str(project.get('name') or project_id)}",
                workspace_root=workspace_dir,
                interactive=False,
                survive_interrupt=True,
            )
            shell_id = str(snapshot.get("id") or "")
            self._remote_shell_owners[shell_id] = (
                peer_device_id,
                project_id,
            )
            return {
                **self._public_shell_snapshot(snapshot),
                "prompt": self._desktop_shell_prompt(workspace_dir),
            }

        shell_id, snapshot = self._owned_shell(
            peer_device_id, project_id, payload
        )
        cursor = max(0, int(payload.get("cursor") or 0))
        if command == "shell.read":
            return self._public_shell_snapshot(snapshot, cursor=cursor)
        if command == "shell.write":
            if str(snapshot.get("status") or "") != "running":
                raise ValueError("remote shell is not running")
            shell_input = _require_text(payload, "input", max_length=32_768)
            snapshot = await send_shell(
                shell_id,
                shell_input,
                wait_ms=max(0, min(int(payload.get("wait_ms") or 250), 1500)),
            )
            return self._public_shell_snapshot(snapshot, cursor=cursor)
        if command == "shell.interrupt":
            if str(snapshot.get("status") or "") != "running":
                raise ValueError("remote shell is not running")
            snapshot = await interrupt_shell(shell_id)
            return self._public_shell_snapshot(snapshot, cursor=cursor)
        if command == "shell.close":
            snapshot = await close_shell(shell_id)
            self._remote_shell_owners.pop(shell_id, None)
            return self._public_shell_snapshot(snapshot, cursor=cursor)
        raise ValueError("unsupported shell operation")

    def _settings_read(self) -> dict[str, Any]:
        from cyrene.learning.skills import build_skills
        from cyrene.runtime.settings_store import get_enabled_tool_packs
        from cyrene.tooling.packs import PACKS

        settings = get_web_settings()
        fields = [dict(field) for field in _REMOTE_SETTING_FIELDS]
        values = {
            field["key"]: settings.get(field["key"], field.get("default"))
            for field in fields
        }
        enabled_packs = get_enabled_tool_packs()
        for pack in PACKS:
            wire_name = str(pack.wire_name)
            key = f"toolpack::{wire_name}"
            label, label_zh = _REMOTE_TOOL_PACK_LABELS.get(
                wire_name,
                (wire_name.replace("_", " ").title(), wire_name.replace("_", " ")),
            )
            fields.append(
                _remote_setting_field(
                    key,
                    "tool_packs",
                    "boolean",
                    label,
                    label_zh,
                    description=_REMOTE_TOOL_PACK_DESCRIPTIONS.get(
                        wire_name, str(pack.description or "")
                    ),
                    description_zh=_REMOTE_TOOL_PACK_DESCRIPTIONS_ZH.get(
                        wire_name, ""
                    ),
                    default=True,
                )
            )
            values[key] = bool(enabled_packs.get(wire_name, True))

        for skill in build_skills():
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
            "models": _public_model_settings(),
            "schema": {
                "version": 2,
                "sections": [dict(section) for section in _REMOTE_SETTING_SECTIONS],
                "fields": fields,
            },
        }

    @staticmethod
    def _settings_models_copy(payload: dict[str, Any]) -> dict[str, Any]:
        """Return the complete model configuration over the paired E2EE channel.

        This endpoint exists specifically for a trusted mobile controller that
        performs Provider calls on-device. It exports API-compatible model
        credentials, but never exports Codex OAuth tokens or unrelated secrets.
        Access is gated by the existing ``settings:read`` peer grant.
        """
        if payload:
            raise ValueError("settings.models.copy does not accept fields")
        from cyrene.runtime.settings_store import (
            get_codex_model,
            get_custom_models,
            get_model_source,
            get_secondary_model,
            get_vision_models,
        )

        def candidate(raw: Any) -> dict[str, Any] | None:
            if not isinstance(raw, dict):
                return None
            model = str(raw.get("model") or raw.get("name") or "").strip()
            if not model:
                return None
            provider = str(raw.get("provider") or "openai_compatible").strip()
            result = {
                "id": str(raw.get("id") or "").strip(),
                "name": str(raw.get("name") or model).strip(),
                "model": model,
                "provider": provider,
                "reasoning_effort": str(raw.get("reasoning_effort") or "").strip(),
                "base_url": str(raw.get("base_url") or "").strip(),
                "description": str(raw.get("desc") or raw.get("description") or "").strip(),
                "context": str(raw.get("ctx") or raw.get("context") or "").strip(),
                "price": str(raw.get("price") or "").strip(),
            }
            # OAuth credentials remain in the desktop provider. Only ordinary
            # API keys, which the mobile Provider client can actually use, are
            # copied to the Android Keystore-backed store.
            if provider != "codex_oauth":
                result["api_key"] = str(raw.get("api_key") or "").strip()
            return result

        custom = [item for raw in get_custom_models() or [] if (item := candidate(raw))]
        vision = [item for raw in get_vision_models() or [] if (item := candidate(raw))]
        codex = candidate(get_codex_model())
        secondary = candidate(get_secondary_model())
        if secondary is not None:
            raw_secondary = get_secondary_model()
            secondary["context_limit"] = int(raw_secondary.get("ctx_limit") or 0)
            secondary["max_concurrency"] = int(raw_secondary.get("max_concurrency") or 0)
        return {
            "ok": True,
            "models": {
                "source": str(get_model_source() or "custom"),
                "custom_models": custom,
                "codex_model": codex,
                "vision_models": vision,
                "secondary_model": secondary,
            },
        }

    def _settings_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        from cyrene.learning.skills import build_skills, set_skill_enabled
        from cyrene.runtime.settings_store import (
            get_enabled_tool_packs,
            get_enabled_tools,
            save_enabled_tool_packs,
            save_enabled_tools,
        )
        from cyrene.tooling.packs import PACK_BY_WIRE_NAME

        payload = dict(payload)
        model_payload = payload.pop("models", None)
        field_by_key = {field["key"]: field for field in _REMOTE_SETTING_FIELDS}
        current_tools = get_enabled_tools()
        current_packs = get_enabled_tool_packs()
        current_skill_ids = {
            str(skill.get("id") or "")
            for skill in build_skills()
            if str(skill.get("id") or "")
        }
        allowed = set(field_by_key)
        allowed.update(f"tool::{name}" for name in current_tools)
        allowed.update(f"toolpack::{name}" for name in PACK_BY_WIRE_NAME)
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
                key.startswith("tool::")
                or key.startswith("toolpack::")
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
        next_tools = dict(current_tools)
        next_packs = dict(current_packs)
        tools_changed = False
        packs_changed = False
        for key, value in normalized.items():
            if key.startswith("tool::"):
                next_tools[key.removeprefix("tool::")] = value
                tools_changed = True
            elif key.startswith("toolpack::"):
                next_packs[key.removeprefix("toolpack::")] = value
                packs_changed = True
            elif key.startswith("skill::"):
                if not set_skill_enabled(key.removeprefix("skill::"), value):
                    raise ValueError("skill is no longer installed")
            else:
                set_setting(key, value)
            changed.append(key)
        if tools_changed:
            save_enabled_tools(next_tools)
        if packs_changed:
            save_enabled_tool_packs(next_packs)

        result = self._settings_read()
        result["changed"] = (["models"] if model_payload is not None else []) + changed
        return result

    @staticmethod
    async def _settings_openai_oauth_read() -> dict[str, Any]:
        """Expose the safe OAuth account/model snapshot to remote controllers."""
        from cyrene.model_runtime.codex_provider import get_codex_provider
        from cyrene.runtime.settings_store import get as get_setting

        try:
            snapshot = await get_codex_provider().snapshot(
                include_limits=False,
                include_models=True,
            )
            return {
                "ok": True,
                "available": snapshot.get("available", True),
                "connected": snapshot.get("connected", False),
                "account": snapshot.get("account"),
                "models": snapshot.get("models") or [],
                "quota_enabled": bool(get_setting("codex_budget_enabled", True)),
                "error": snapshot.get("errors", {}).get("models", "")
                if isinstance(snapshot.get("errors"), dict)
                else "",
            }
        except (RuntimeError, OSError, TimeoutError) as exc:
            return {
                "ok": True,
                "available": False,
                "connected": False,
                "account": None,
                "models": [],
                "quota_enabled": True,
                "error": str(exc),
            }

    @staticmethod
    async def _settings_openai_oauth_login() -> dict[str, Any]:
        from cyrene.model_runtime.codex_provider import get_codex_provider

        set_setting("codex_budget_enabled", True)
        return await get_codex_provider().start_login()

    @staticmethod
    async def _settings_openai_oauth_logout() -> dict[str, Any]:
        from cyrene.model_runtime.codex_provider import get_codex_provider

        await get_codex_provider().logout()
        return {"ok": True}

    async def _projects_list(self, peer_device_id: str) -> dict[str, Any]:
        store = workbench_runtime._read_workbench_store_lightweight()
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
                for project in store.get("projects") or []
                if isinstance(project, dict)
                and str(project.get("id") or "") in shared_project_ids
            ],
        }

    async def _chats_list(self, project_id: str) -> dict[str, Any]:
        result = _json_response_payload(
            await self.chat["list_chats"](project=project_id)
        )
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
        result = _json_response_payload(
            await self.chat["create_chat"](
                api_models.ChatCreateBody(
                    project=project_id,
                    title=str(payload.get("title") or "")[:160],
                )
            )
        )
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
        result = _json_response_payload(
            await self.chat["update_chat"](
                chat_id,
                api_models.ChatUpdateBody(title=title),
            )
        )
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _chats_delete(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        result = _json_response_payload(await self.chat["delete_chat"](chat_id))
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _chat_for_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        chat_id = _require_text(payload, "chat_id", max_length=200)
        result = _json_response_payload(await self.chat["get_chat"](chat_id))
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
        from cyrene.runtime.config_store import get_current_ctx_limit

        model_name = _remote_chat_model(chat)
        context_limit = get_current_ctx_limit()
        (
            context_metrics,
            context_blocks,
            subagents,
            change_sets,
            map_data,
        ) = await asyncio.gather(
            asyncio.to_thread(
                chat_context_payload,
                chat_id,
                model_name,
                ctx_limit=context_limit,
            ),
            asyncio.to_thread(_remote_context_blocks, chat_id),
            asyncio.to_thread(workbench_subagent_payload, chat_id, ""),
            asyncio.to_thread(list_chat_change_sets, self.db_path, chat_id),
            asyncio.to_thread(_remote_map_data, chat_id),
        )
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
                    self.chat.get("run_manager"),
                ),
                "used_tool_packages": _remote_used_tool_packages(chat),
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
                allowed=frozenset({"auto", "default", "plan"}),
                default="auto",
            ),
            "lang": str(payload.get("language") or ""),
            "stream": True,
        }
        if attachments:
            send_body["attachments"] = attachments
        try:
            result = _json_response_payload(
                await self.chat["send_chat_detached"](
                    chat_id,
                    send_body,
                    detached=True,
                )
            )
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
        run = self.chat["run_manager"].get_replayable_by_run_id(run_id)
        if run is None:
            return None, {
                "ok": False,
                "code": "run_not_found",
                "error": "run not found",
            }
        result = _json_response_payload(await self.chat["get_chat"](run.chat_id))
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
        wire_name = _require_text(payload, "tool_pack", max_length=100)
        if wire_name not in REMOTE_TOOL_PACK_WIRE_NAMES:
            return {
                "ok": False,
                "code": "remote_tool_pack_unsupported",
                "error": f"tool package is not remotely callable: {wire_name}",
            }
        peer = self.store.get_peer(peer_device_id)
        grant = REMOTE_TOOL_PACK_PREFIX + wire_name
        if peer is None or grant not in (peer.get("granted_capabilities") or []):
            return {
                "ok": False,
                "code": "remote_tool_pack_denied",
                "error": f"remote access to {wire_name} is not granted",
            }
        project = workbench_runtime._workbench_find_project_lightweight(project_id)
        if project is None:
            return {
                "ok": False,
                "code": "remote_project_not_found",
                "error": "authorized project no longer exists",
            }
        workspace_dir = workbench_runtime._workbench_resolve_workspace_dir(project)
        operation = command.removeprefix("harness.")
        arguments: dict[str, Any] = {"operation": operation}
        if operation == "discover":
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
        else:
            raise ValueError("unsupported harness operation")

        snapshot = build_catalog_snapshot("main")
        context = ToolExecutionContext(
            actor="main",
            session_id=f"remote_harness:{peer_device_id}:{project_id}",
            round_id=str(payload.get("call_id") or ""),
            workspace=Path(workspace_dir) if workspace_dir else None,
            bot=self.bot,
            chat_id=0,
            db_path=self.db_path,
            permission_mode="full_access",
            catalog_snapshot=snapshot,
        )
        binding = bind_run_context(
            agent_id="main",
            caller="remote_harness",
            conversation_source="remote_harness",
            round_id=context.round_id or f"remote-{peer_device_id}",
            session_id=context.session_id,
            workspace_dir=workspace_dir,
            permission_mode="full_access",
            temporary_full_access=True,
        )
        try:
            timeout = max(
                1.0, min(float(payload.get("timeout_seconds") or 120), 300.0)
            )
            raw = await asyncio.wait_for(
                execute_wire_tool_in_context(wire_name, arguments, context),
                timeout=timeout,
            )
        finally:
            binding.reset()
        try:
            result: Any = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            result = raw
        failed = (
            isinstance(result, dict)
            and str(result.get("status") or "").lower() == "error"
        )
        return {
            "ok": not failed,
            "tool_pack": wire_name,
            "operation": operation,
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
        result = _json_response_payload(
            await self.chat["guide_chat"](
                chat_id,
                api_models.ChatGuidanceBody(
                    message=_require_text(payload, "message"),
                    clientRequestId=str(payload.get("request_id") or ""),
                ),
            )
        )
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _runs_interrupt(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        interrupted = self.chat["run_manager"].interrupt(chat_id)
        return {
            "ok": interrupted,
            "interrupted": interrupted,
            "code": "" if interrupted else "chat_not_running",
        }

    async def _tasks_list(self, project_id: str) -> dict[str, Any]:
        result = _json_response_payload(
            await self.project["list_tasks"](project_id)
        )
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "tasks": [
                _task_summary(item)
                for item in result.get("sessions") or []
                if isinstance(item, dict) and str(item.get("kind") or "task") == "task"
            ],
        }

    async def _tasks_create(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = _json_response_payload(
            await self.project["create_task"](
                project_id,
                api_models.SessionCreateBody(
                    title=str(payload.get("title") or "")[:160],
                    goal=_require_text(payload, "goal", max_length=50_000),
                    priority=str(payload.get("priority") or "medium"),
                ),
            )
        )
        if result.get("ok") is False:
            return result
        return {"ok": True, "task": _task_summary(dict(result.get("session") or {}))}

    async def _task_for_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        task_id = _require_text(payload, "task_id", max_length=200)
        result = _json_response_payload(await self.task["get_task"](task_id))
        if result.get("ok") is False:
            return task_id, None, result
        task = dict(result.get("session") or {})
        if str(task.get("projectId") or result.get("projectId") or "") != project_id:
            return task_id, None, {
                "ok": False,
                "code": "remote_project_mismatch",
                "error": "task does not belong to the authorized project",
            }
        return task_id, task, None

    async def _tasks_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _task_id, task, error = await self._task_for_project(project_id, payload)
        return error or {"ok": True, "task": _task_detail(task or {})}

    async def _tasks_dispatch(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, _task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        attachments = _store_remote_attachments(payload.get("attachments"))
        try:
            result = _json_response_payload(
                await self.task["dispatch_task"](
                    task_id,
                    api_models.AgentInputBody(
                        input=_require_text(payload, "message"),
                        attachments=attachments,
                        mode=_permission_mode(
                            payload,
                            allowed=frozenset({"auto", "default"}),
                            default="auto",
                        ),
                        command=str(payload.get("command") or ""),
                    ),
                )
            )
        except Exception:
            for attachment in attachments:
                Path(str(attachment.get("path") or "")).unlink(missing_ok=True)
            raise
        if result.get("ok") is False:
            for attachment in attachments:
                Path(str(attachment.get("path") or "")).unlink(missing_ok=True)
            return result
        return {
            "ok": True,
            "task": _task_summary(dict(result.get("session") or {})),
            "reply_kind": str(result.get("replyKind") or ""),
        }

    async def _tasks_approve_plan(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        revision = int((task or {}).get("planDefinitionRevision") or 0)
        if not (task or {}).get("plan"):
            return {
                "ok": False,
                "code": "task_plan_empty",
                "error": "task plan is empty",
            }
        result = _json_response_payload(
            await self.task["update_task"](
                task_id,
                api_models.SessionUpdateBody(
                    status="waiting_for_approval",
                    approvedPlanDefinitionRevision=revision,
                ),
            )
        )
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "task": _task_detail(dict(result.get("session") or {})),
            "approved_plan_definition_revision": revision,
        }

    async def _tasks_run_step(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        step_id = _require_text(payload, "step_id", max_length=200)
        plan = [
            dict(item)
            for item in (task or {}).get("plan") or []
            if isinstance(item, dict)
        ]
        step = next(
            (item for item in plan if str(item.get("id") or "") == step_id),
            None,
        )
        if step is None:
            return {
                "ok": False,
                "code": "step_not_found",
                "error": "task step not found",
            }
        revision = int((task or {}).get("planDefinitionRevision") or 0)
        approved_revision = (task or {}).get("approvedPlanDefinitionRevision")
        if (
            approved_revision is None
            or int(approved_revision) != revision
        ):
            return {
                "ok": False,
                "code": "plan_not_approved",
                "error": "current task plan has not been approved",
            }
        for item in plan:
            if str(item.get("id") or "") == step_id:
                item["status"] = "running"
                item["currentAction"] = "Remote controller started this step."
        prepared = _json_response_payload(
            await self.task["update_task"](
                task_id,
                api_models.SessionUpdateBody(status="running", plan=plan),
            )
        )
        if prepared.get("ok") is False:
            return prepared
        result = _json_response_payload(
            await self.task["create_run"](
                task_id,
                api_models.AgentInputBody(
                    input=_require_text(payload, "message"),
                    mode=_permission_mode(
                        payload,
                        allowed=frozenset({"auto", "default"}),
                        default="auto",
                    ),
                    stepId=step_id,
                    stepTitle=str(step.get("title") or "")[:1000],
                    action="spawn_subagent",
                    meta={"scope": "plan_step", "continueAll": False},
                    planDefinitionRevision=revision,
                ),
            )
        )
        if result.get("ok") is False:
            return result
        updated = dict(result.get("session") or {})
        if str(updated.get("status") or "") == "waiting_for_user":
            return {"ok": True, "task": _task_detail(updated)}
        returned_plan = [
            dict(item)
            for item in updated.get("plan") or plan
            if isinstance(item, dict)
        ]
        for item in returned_plan:
            if str(item.get("id") or "") == step_id:
                item["status"] = "completed"
                item["currentAction"] = "Remote-controlled step completed."
        resolved = {"completed", "done", "skipped"}
        fully_done = bool(returned_plan) and all(
            str(item.get("status") or "") in resolved
            for item in returned_plan
        )
        finalized = _json_response_payload(
            await self.task["update_task"](
                task_id,
                api_models.SessionUpdateBody(
                    status="review" if fully_done else "paused",
                    plan=returned_plan,
                ),
            )
        )
        if finalized.get("ok") is False:
            return finalized
        return {
            "ok": True,
            "task": _task_detail(dict(finalized.get("session") or {})),
            "step_id": step_id,
            "fully_done": fully_done,
        }

    async def _tasks_control(
        self,
        command: str,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        if self.goal_loop:
            goal_state = _json_response_payload(
                await self.goal_loop["get"](task_id)
            )
            goal_loop = goal_state.get("goalLoop")
            if (
                isinstance(goal_loop, dict)
                and str(goal_loop.get("status") or "")
                not in {"completed", "failed", "cancelled"}
            ):
                action = command.removeprefix("tasks.")
                controlled = _json_response_payload(
                    await self.goal_loop[action](task_id)
                )
                if controlled.get("ok") is False:
                    return controlled
                return {
                    "ok": True,
                    "task": _task_detail(
                        dict(controlled.get("session") or {})
                    ),
                    "goal_loop": controlled.get("goalLoop"),
                }
        current = str((task or {}).get("status") or "")
        if command == "tasks.pause" and current not in {
            "running",
            "waiting_for_user",
        }:
            return {
                "ok": False,
                "code": "invalid_status_transition",
                "error": "only an active task can be paused",
            }
        if command == "tasks.resume" and current != "paused":
            return {
                "ok": False,
                "code": "invalid_status_transition",
                "error": "only a paused task can be resumed",
            }
        next_status = {
            "tasks.pause": "paused",
            "tasks.resume": "idle",
            "tasks.cancel": "cancelled",
        }[command]
        if command in {"tasks.pause", "tasks.cancel"}:
            from cyrene.agent import interrupt_active_run

            interrupt_active_run(session_id=task_id)
        result = _json_response_payload(
            await self.task["update_task"](
                task_id,
                api_models.SessionUpdateBody(status=next_status),
            )
        )
        if result.get("ok") is False:
            return result
        return {"ok": True, "task": _task_summary(dict(result.get("session") or {}))}

    async def _approvals_respond(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if str(payload.get("task_id") or "").strip():
            task_id, _task, error = await self._task_for_project(
                project_id,
                payload,
            )
            if error:
                return error
            result = _json_response_payload(
                await self.task["answer_task"](
                    task_id,
                    api_models.AnswerBody(
                        question_id=_require_text(
                            payload,
                            "question_id",
                            max_length=500,
                        ),
                        answer=_require_text(payload, "answer"),
                        mode=_permission_mode(
                            payload,
                            allowed=frozenset({"auto", "default"}),
                        ),
                    ),
                )
            )
            if result.get("ok") is False:
                return result
            return {
                "ok": True,
                "task": _task_detail(dict(result.get("session") or {})),
                "awaiting_user": bool(result.get("awaitingUser")),
            }
        chat_id, chat = await self._chat_for_project(project_id, payload)
        if chat is None or chat.get("ok") is False:
            return dict(chat or {"ok": False, "error": "chat not found"})
        result = _json_response_payload(
            await self.chat["answer_chat"](
                chat_id,
                api_models.AnswerBody(
                    question_id=_require_text(
                        payload,
                        "question_id",
                        max_length=500,
                    ),
                    answer=_require_text(payload, "answer"),
                    mode=_permission_mode(
                        payload,
                        allowed=frozenset({"auto", "default"}),
                    ),
                ),
            )
        )
        return {"ok": True, **result} if result.get("ok") is not False else result

    async def _artifacts_list(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, _task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        result = _json_response_payload(
            await self.task["task_artifacts"](task_id)
        )
        if result.get("ok") is False:
            return result
        return {
            "ok": True,
            "artifacts": [
                summary
                for item in result.get("artifacts") or []
                if (summary := _artifact_summary(item)) is not None
            ],
        }

    async def _artifacts_read(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _task_id, task, error = await self._task_for_project(project_id, payload)
        if error:
            return error
        artifact_id = _require_text(payload, "artifact_id", max_length=200)
        artifact = next(
            (
                item
                for item in (task or {}).get("artifacts") or []
                if isinstance(item, dict)
                and str(item.get("id") or "") == artifact_id
            ),
            None,
        )
        if artifact is None:
            return {
                "ok": False,
                "code": "artifact_not_found",
                "error": "artifact not found",
            }
        store = workbench_runtime._read_workbench_store()
        project, full_task = workbench_runtime._workbench_find_session(
            store,
            str((task or {}).get("id") or ""),
        )
        try:
            _artifact, path = workbench_runtime._workbench_artifact_download_target(
                project,
                full_task,
                artifact_id,
            )
        except (LookupError, ValueError, FileNotFoundError) as exc:
            return {"ok": False, "code": "artifact_unavailable", "error": str(exc)}
        file_path = Path(path)
        return {
            "ok": True,
            "artifact": _artifact_summary(artifact),
            "filename": file_path.name,
            "media_type": mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream",
            **_file_chunk(file_path, payload),
        }

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
            return {
                "ok": False,
                "code": "attachment_not_found",
                "error": str(exc),
            }
        except FileNotFoundError as exc:
            return {
                "ok": False,
                "code": "attachment_unavailable",
                "error": str(exc),
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
                return {
                    "ok": False,
                    "code": "thumbnail_unavailable",
                    "error": str(exc),
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
        except Exception as exc:
            self.last_error = f"LAN control listener failed: {exc}"
            self.store.audit(
                "direct_pairing_listener_failed",
                outcome="error",
                detail={"error": str(exc)},
            )
            return
        try:
            gateway = RemoteGateway(self.store, pairing_server, self.executor)
            await gateway.start()
        except Exception as exc:
            self.last_error = str(exc)
            self.store.audit(
                "remote_gateway_start_failed",
                outcome="error",
                detail={"error": self.last_error},
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
            detail = "Remote access is disabled."
        elif self.last_error:
            state = "error"
            detail = self.last_error
        elif gateway is not None and gateway.connected:
            state = "connected"
            detail = "LAN E2EE control is ready."
        elif gateway is not None and gateway.started:
            state = "connecting"
            detail = "Starting the LAN E2EE control listener."
        else:
            state = "configured"
            detail = "LAN control will start with the Cyrene runtime."
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
