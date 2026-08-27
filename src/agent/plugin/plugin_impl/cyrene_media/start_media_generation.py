"""Submit image, video, and music generation to the media daemon."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import math
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from .definitions import get_native_tool_def
from agent.plugin import PluginContext
from agent.plugin.native_runtime import (
    json_result,
    plugin_language,
    plugin_localized,
    resolve_tool_path,
    run_context_data,
    run_context_value,
)
from cyrene.localization import localized

TOOL_NAME = "StartMediaGeneration"
TOOL_DEF = get_native_tool_def(TOOL_NAME)
TOOL_METADATA = {
    "read_only": False,
    # Submission itself is a short transactional queue write. The independent
    # jobs created by one call are claimed concurrently by MediaWorkers.
    "resource_keys": ("media:jobs",),
    "requires_order": False,
}

_PROVIDERS = frozenset(
    {
        "auto",
        "comfyui",
        "openai",
        "seedream",
        "seedance",
        "minimax",
        "google",
    }
)
_KINDS = frozenset({"image", "video", "music"})
_REFERENCE_ROLES = frozenset(
    {
        "first_frame",
        "last_frame",
        "reference",
        "subject",
        "audio",
        "reference_image",
        "reference_video",
        "reference_audio",
    }
)
_LOGGER = logging.getLogger(__name__)


def _media_localized(
    context: PluginContext | None,
    en: str,
    zh: str,
    **values: Any,
) -> str:
    if context is None or (not context.data and not context.services):
        # Helpers such as request normalization are also used outside an
        # Agent invocation (for example by the daemon queue boundary).  With
        # no invocation language there is no locale to honor; keep validation
        # errors deterministic in the protocol's canonical language instead
        # of consulting mutable process-wide UI settings.
        return en.format(**values)
    return plugin_localized(context, en, zh, **values)


def _text(
    value: Any,
    *,
    field: str,
    limit: int,
    context: PluginContext | None = None,
) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(_media_localized(
            context,
            "{field} exceeds {limit} characters",
            "{field} 超过 {limit} 个字符的限制",
            field=field,
            limit=limit,
        ))
    return text


def _string_list(
    value: Any,
    *,
    field: str,
    limit: int,
    item_limit: int = 4096,
    context: PluginContext | None = None,
) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(_media_localized(
            context,
            "{field} must be an array",
            "{field} 必须是数组",
            field=field,
        ))
    if len(value) > limit:
        raise ValueError(_media_localized(
            context,
            "{field} supports at most {limit} items",
            "{field} 最多支持 {limit} 项",
            field=field,
            limit=limit,
        ))
    result: list[str] = []
    for raw in value:
        item = _text(raw, field=field, limit=item_limit, context=context)
        if not item:
            raise ValueError(_media_localized(
                context,
                "{field} cannot contain empty values",
                "{field} 不能包含空值",
                field=field,
            ))
        result.append(item)
    return result


def _chat_attachment_ids(context: PluginContext) -> set[str]:
    """Return attachment ids explicitly exposed to this Plugin invocation."""

    values: set[str] = set()
    for raw_mapping in (
        context.data.get("attachment_paths"),
        run_context_data(context).get("attachment_paths"),
        context.services.get("attachment_paths"),
    ):
        if not isinstance(raw_mapping, Mapping):
            continue
        values.update(
            str(identifier).strip()
            for identifier in raw_mapping
            if str(identifier or "").strip()
        )
    return values


def _resolve_references(
    request: dict[str, Any],
    *,
    chat_attachment_ids: set[str],
    context: PluginContext | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    requested_paths = _string_list(
        request.get("reference_paths"),
        field="reference_paths",
        limit=30,
        context=context,
    )
    attachment_ids = _string_list(
        request.get("reference_attachment_ids"),
        field="reference_attachment_ids",
        limit=30,
        item_limit=512,
        context=context,
    )
    reference_urls = _string_list(
        request.get("reference_urls"),
        field="reference_urls",
        limit=30,
        item_limit=4096,
        context=context,
    )
    if len(requested_paths) + len(attachment_ids) + len(reference_urls) > 30:
        raise ValueError(_media_localized(
            context,
            "A media request supports at most 30 references.",
            "单个媒体请求最多支持 30 个参考项。",
        ))

    resolved_paths: list[str] = []
    for raw_path in requested_paths:
        try:
            path = resolve_tool_path(raw_path)
        except ValueError as exc:
            raise ValueError(_media_localized(
                context,
                "Media references must be inside the active workspace.",
                "媒体参考文件必须位于当前工作区内。",
            )) from exc
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(_media_localized(
                context,
                "Media reference not found: {path}",
                "未找到媒体参考文件：{path}",
                path=raw_path,
            ))
        resolved_paths.append(str(path.resolve()))

    for attachment_id in attachment_ids:
        attachment_kwargs: dict[str, Any] = {
            "chat_attachment_ids": chat_attachment_ids,
        }
        if context is not None:
            attachment_kwargs["context"] = context
        resolved_paths.append(
            _resolve_attachment_reference(attachment_id, **attachment_kwargs)
        )

    for reference_url in reference_urls:
        parsed = urlparse(reference_url)
        host = str(parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment or host == "localhost" or host.endswith(".localhost"):
            raise ValueError(_media_localized(
                context,
                "reference_urls must contain public HTTPS URLs",
                "reference_urls 必须包含公开的 HTTPS URL",
            ))
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError(_media_localized(
                context,
                "reference_urls must not point to a private address",
                "reference_urls 不能指向私有地址",
            ))

    roles = _string_list(
        request.get("reference_roles"),
        field="reference_roles",
        limit=30,
        item_limit=32,
        context=context,
    )
    invalid_roles = [role for role in roles if role not in _REFERENCE_ROLES]
    if invalid_roles:
        raise ValueError(_media_localized(
            context,
            "Unsupported reference role: {role}",
            "不支持的参考角色：{role}",
            role=invalid_roles[0],
        ))
    if roles and len(roles) != len(resolved_paths) + len(reference_urls):
        raise ValueError(_media_localized(
            context,
            "reference_roles must match the total number of reference sources",
            "reference_roles 的数量必须与所有参考来源的总数一致",
        ))
    return resolved_paths, attachment_ids, reference_urls, roles


def _resolve_attachment_reference(
    attachment_id: str,
    *,
    chat_attachment_ids: set[str],
    context: PluginContext | None = None,
) -> str:
    from cyrene.runtime.attachments import resolve_managed_attachment_id

    if attachment_id not in chat_attachment_ids:
        raise ValueError(_media_localized(
            context,
            "Attachment references may only use files attached to the current conversation: {attachment_id}",
            "附件参考只能使用当前会话中已附加的文件：{attachment_id}",
            attachment_id=attachment_id,
        ))
    try:
        path = resolve_managed_attachment_id(attachment_id)
    except (OSError, ValueError) as exc:
        raise FileNotFoundError(_media_localized(
            context,
            "Managed media reference is unavailable: {attachment_id}",
            "托管媒体参考不可用：{attachment_id}",
            attachment_id=attachment_id,
        )) from exc
    if path is None:
        raise FileNotFoundError(_media_localized(
            context,
            "Managed media reference is unavailable: {attachment_id}",
            "托管媒体参考不可用：{attachment_id}",
            attachment_id=attachment_id,
        ))
    return str(path.resolve())


def _normalize_request(
    raw: Any,
    *,
    index: int,
    chat_attachment_ids: set[str],
    context: PluginContext | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(_media_localized(
            context,
            "requests[{index}] must be an object",
            "requests[{index}] 必须是对象",
            index=index,
        ))
    kind = _text(
        raw.get("kind"),
        field=f"requests[{index}].kind",
        limit=16,
        context=context,
    ).lower()
    if kind not in _KINDS:
        raise ValueError(_media_localized(
            context,
            "requests[{index}].kind must be image, video, or music",
            "requests[{index}].kind 必须是 image、video 或 music",
            index=index,
        ))
    provider = _text(
        raw.get("provider") or "auto",
        field=f"requests[{index}].provider",
        limit=32,
        context=context,
    ).lower()
    if provider not in _PROVIDERS:
        raise ValueError(_media_localized(
            context,
            "Unsupported media provider: {provider}",
            "不支持的媒体提供方：{provider}",
            provider=provider,
        ))

    prompt = _text(
        raw.get("prompt"),
        field=f"requests[{index}].prompt",
        limit=100_000,
        context=context,
    )
    lyrics = _text(
        raw.get("lyrics"),
        field=f"requests[{index}].lyrics",
        limit=100_000,
        context=context,
    )
    if not prompt and not (kind == "music" and lyrics):
        raise ValueError(_media_localized(
            context,
            "requests[{index}] requires a prompt or music lyrics",
            "requests[{index}] 需要提示词或音乐歌词",
            index=index,
        ))

    reference_paths, attachment_ids, reference_urls, reference_roles = _resolve_references(
        raw,
        chat_attachment_ids=chat_attachment_ids,
        context=context,
    )
    parameters = raw.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError(_media_localized(
            context,
            "requests[{index}].parameters must be an object",
            "requests[{index}].parameters 必须是对象",
            index=index,
        ))

    parameters = dict(parameters)
    if any(key in parameters for key in ("mask", "mask_path")):
        raise ValueError(_media_localized(
            context,
            "requests[{index}].mask_path must use the safe top-level field",
            "requests[{index}].mask_path 必须使用安全的顶层字段",
            index=index,
        ))

    mask_path = _text(
        raw.get("mask_path"),
        field=f"requests[{index}].mask_path",
        limit=4096,
        context=context,
    )
    mask_attachment_id = _text(
        raw.get("mask_attachment_id"),
        field=f"requests[{index}].mask_attachment_id",
        limit=512,
        context=context,
    )
    if mask_path and mask_attachment_id:
        raise ValueError(_media_localized(
            context,
            "requests[{index}] must use only one of mask_path or mask_attachment_id",
            "requests[{index}] 只能使用 mask_path 或 mask_attachment_id 其中之一",
            index=index,
        ))
    resolved_mask = ""
    if mask_path:
        try:
            mask = resolve_tool_path(mask_path)
        except ValueError as exc:
            raise ValueError(_media_localized(
                context,
                "The media mask must be inside the active workspace.",
                "媒体遮罩必须位于当前工作区内。",
            )) from exc
        if not mask.exists() or not mask.is_file():
            raise FileNotFoundError(_media_localized(
                context,
                "Media mask not found: {path}",
                "未找到媒体遮罩：{path}",
                path=mask_path,
            ))
        resolved_mask = str(mask.resolve())
    elif mask_attachment_id:
        attachment_kwargs: dict[str, Any] = {
            "chat_attachment_ids": chat_attachment_ids,
        }
        if context is not None:
            attachment_kwargs["context"] = context
        resolved_mask = _resolve_attachment_reference(
            mask_attachment_id,
            **attachment_kwargs,
        )

    request: dict[str, Any] = {
        "kind": kind,
        "provider": provider,
        "prompt": prompt,
        "model": _text(raw.get("model"), field=f"requests[{index}].model", limit=240, context=context),
        "name": _text(raw.get("name"), field=f"requests[{index}].name", limit=255, context=context),
        "reference_paths": reference_paths,
        "reference_attachment_ids": attachment_ids,
        "reference_urls": reference_urls,
        "reference_roles": reference_roles,
        "parameters": parameters,
    }
    if resolved_mask:
        if kind != "image":
            raise ValueError(_media_localized(
                context,
                "requests[{index}].mask_path is only valid for image jobs",
                "requests[{index}].mask_path 仅适用于图像任务",
                index=index,
            ))
        request["mask_path"] = resolved_mask
        if mask_attachment_id:
            request["mask_attachment_id"] = mask_attachment_id
    optional_text = {
        "negative_prompt": 100_000,
        "size": 64,
        "aspect_ratio": 32,
        "resolution": 32,
        "quality": 32,
        "output_format": 32,
    }
    for field, limit in optional_text.items():
        value = _text(
            raw.get(field),
            field=f"requests[{index}].{field}",
            limit=limit,
            context=context,
        )
        if value:
            request[field] = value
    if lyrics:
        request["lyrics"] = lyrics
    for field in ("is_instrumental", "generate_audio"):
        if field in raw:
            request[field] = bool(raw[field])
    if raw.get("duration") is not None:
        try:
            duration = float(raw["duration"])
        except (TypeError, ValueError) as exc:
            raise ValueError(_media_localized(
                context,
                "requests[{index}].duration must be numeric",
                "requests[{index}].duration 必须是数值",
                index=index,
            )) from exc
        if not math.isfinite(duration) or (duration != -1 and not 1 <= duration <= 600):
            raise ValueError(_media_localized(
                context,
                "requests[{index}].duration must be -1 (provider auto) or between 1 and 600 seconds",
                "requests[{index}].duration 必须为 -1（提供方自动选择）或 1 到 600 秒",
                index=index,
            ))
        request["duration"] = duration
    if raw.get("number_of_outputs") is not None:
        try:
            numeric_count = float(raw["number_of_outputs"])
        except (TypeError, ValueError) as exc:
            raise ValueError(_media_localized(
                context,
                "requests[{index}].number_of_outputs must be an integer",
                "requests[{index}].number_of_outputs 必须是整数",
                index=index,
            )) from exc
        if not math.isfinite(numeric_count) or not numeric_count.is_integer():
            raise ValueError(_media_localized(
                context,
                "requests[{index}].number_of_outputs must be an integer",
                "requests[{index}].number_of_outputs 必须是整数",
                index=index,
            ))
        count = int(numeric_count)
        if not 1 <= count <= 8:
            raise ValueError(_media_localized(
                context,
                "requests[{index}].number_of_outputs must be between 1 and 8",
                "requests[{index}].number_of_outputs 必须在 1 到 8 之间",
                index=index,
            ))
        request["number_of_outputs"] = count
    if raw.get("seed") is not None:
        try:
            numeric_seed = float(raw["seed"])
        except (TypeError, ValueError) as exc:
            raise ValueError(_media_localized(
                context,
                "requests[{index}].seed must be a non-negative integer",
                "requests[{index}].seed 必须是非负整数",
                index=index,
            )) from exc
        if not math.isfinite(numeric_seed) or not numeric_seed.is_integer() or numeric_seed < 0:
            raise ValueError(_media_localized(
                context,
                "requests[{index}].seed must be a non-negative integer",
                "requests[{index}].seed 必须是非负整数",
                index=index,
            ))
        seed = int(numeric_seed)
        request["seed"] = seed
    return request


async def _tool_start_media_generation(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    from .settings import get_media_settings

    manager = context.services.get("media")
    create_batch = getattr(manager, "create_batch", None)
    if not callable(create_batch):
        raise RuntimeError(_media_localized(
            context,
            "cyrene_media application service is unavailable",
            "cyrene_media 应用服务不可用",
        ))
    try:
        agent_id = str(run_context_value(context, "agent_id", "main") or "main")
        caller = str(run_context_value(context, "caller", "main_agent") or "main_agent")
        if agent_id != "main" or caller not in {
            "main",
            "main_agent",
            "execution_agent",
        }:
            raise PermissionError(
                _media_localized(
                    context,
                    "Only the main conversation Agent can create media jobs.",
                    "只有主会话 Agent 可以创建媒体任务。",
                )
            )
        chat_id = str(run_context_value(context, "session_id", "") or "").strip()
        if not chat_id:
            raise ValueError(_media_localized(
                context,
                "Media generation requires an active conversation.",
                "媒体生成需要一个活跃会话。",
            ))
        project_id = str(context.data.get("project_id") or "").strip()
        if not project_id:
            raise ValueError(
                _media_localized(
                    context,
                    "Media generation requires a conversation attached to a project.",
                    "媒体生成需要一个已关联项目的会话。",
                )
            )
        raw_requests = args.get("requests")
        if not isinstance(raw_requests, list) or not 1 <= len(raw_requests) <= 8:
            raise ValueError(_media_localized(
                context,
                "requests must contain between 1 and 8 media jobs",
                "requests 必须包含 1 到 8 个媒体任务",
            ))
        visible_attachment_ids = _chat_attachment_ids(context)
        requests = [
            _normalize_request(
                raw,
                index=index,
                chat_attachment_ids=visible_attachment_ids,
                context=context,
            )
            for index, raw in enumerate(raw_requests)
        ]
        language = plugin_language(context)
        for request in requests:
            request["language"] = language
        idempotency_key = _text(
            args.get("idempotency_key"),
            field="idempotency_key",
            limit=160,
            context=context,
        )
        if idempotency_key and len(idempotency_key) < 8:
            raise ValueError(_media_localized(
                context,
                "idempotency_key must contain at least 8 characters",
                "idempotency_key 必须至少包含 8 个字符",
            ))
        owner_call_id = str(
            run_context_value(context, "round_id", "")
            or run_context_value(context, "client_request_id", "")
            or ""
        )
        if not idempotency_key and owner_call_id:
            canonical = json.dumps(
                requests,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            digest = hashlib.sha256(f"{owner_call_id}\0{canonical}".encode("utf-8")).hexdigest()
            idempotency_key = f"automatic-{digest}"
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        return _media_localized(
            context,
            "Error: {message}",
            "错误：{message}",
            message=str(exc),
        )
    except (LookupError, TypeError):
        _LOGGER.warning("media request validation failed", exc_info=True)
        return _media_localized(
            context,
            "Error: The media request is invalid.",
            "错误：媒体请求无效。",
        )
    except Exception:
        _LOGGER.exception("media request validation failed")
        return _media_localized(
            context,
            "Error: The media request is invalid.",
            "错误：媒体请求无效。",
        )

    try:
        settings = get_media_settings()
        created = await asyncio.to_thread(
            create_batch,
            chat_id=chat_id,
            project_id=project_id,
            requests=requests,
            wake_note="",
            idempotency_key=idempotency_key,
            owner_tool_call_id=owner_call_id,
            max_attempts=int(settings.get("max_attempts") or 2),
        )
    except Exception:
        _LOGGER.exception("media batch submission failed")
        return _media_localized(
            context,
            "Error: Media generation could not be started.",
            "错误：无法启动媒体生成。",
        )

    wake_status = str(created.get("wake_status") or "")
    already_settled = str(created.get("status") or "") == "existing" and wake_status in {"delivered", "cancelled"}
    wake_hint = (
        _media_localized(
            context,
            "This media batch was already delivered or cancelled. Continue using the visible result without waiting for another wake.",
            "该媒体批次已交付或取消，请直接使用可见结果，无需等待再次唤醒。",
        )
        if already_settled
        else _media_localized(
            context,
            "The media jobs are running independently. End this turn without polling; completed outputs will be attached to this chat before the Agent resumes for the batch.",
            "媒体任务正在独立运行。请结束本轮且不要轮询；完成的结果会附加到此会话，随后 Agent 将为该批次恢复运行。",
        )
    )
    return json_result(
        {
            "status": str(created.get("status") or "queued"),
            "batch_id": str(created.get("batch_id") or ""),
            "job_ids": list(created.get("job_ids") or []),
            "wake_id": str(created.get("wake_id") or ""),
            "wake_status": wake_status,
            "wake_agent": not already_settled,
            "wake_hint": wake_hint,
        }
    )


handler = _tool_start_media_generation

__all__ = [
    "TOOL_NAME",
    "TOOL_DEF",
    "TOOL_METADATA",
    "handler",
    "_tool_start_media_generation",
]
