"""Submit image, video, and music generation to the media daemon."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from .definitions import get_native_tool_def
from agent.plugin import PluginContext
from agent.plugin.native_runtime import (
    json_result,
    resolve_tool_path,
    run_context_data,
    run_context_value,
)

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


def _text(value: Any, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return text


def _string_list(
    value: Any,
    *,
    field: str,
    limit: int,
    item_limit: int = 4096,
) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    if len(value) > limit:
        raise ValueError(f"{field} supports at most {limit} items")
    result: list[str] = []
    for raw in value:
        item = _text(raw, field=field, limit=item_limit)
        if not item:
            raise ValueError(f"{field} cannot contain empty values")
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
) -> tuple[list[str], list[str], list[str], list[str]]:
    requested_paths = _string_list(
        request.get("reference_paths"),
        field="reference_paths",
        limit=30,
    )
    attachment_ids = _string_list(
        request.get("reference_attachment_ids"),
        field="reference_attachment_ids",
        limit=30,
        item_limit=512,
    )
    reference_urls = _string_list(
        request.get("reference_urls"),
        field="reference_urls",
        limit=30,
        item_limit=4096,
    )
    if len(requested_paths) + len(attachment_ids) + len(reference_urls) > 30:
        raise ValueError("a media request supports at most 30 references")

    resolved_paths: list[str] = []
    for raw_path in requested_paths:
        path = resolve_tool_path(raw_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"media reference not found: {raw_path}")
        resolved_paths.append(str(path.resolve()))

    for attachment_id in attachment_ids:
        resolved_paths.append(
            _resolve_attachment_reference(
                attachment_id,
                chat_attachment_ids=chat_attachment_ids,
            )
        )

    for reference_url in reference_urls:
        parsed = urlparse(reference_url)
        host = str(parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment or host == "localhost" or host.endswith(".localhost"):
            raise ValueError("reference_urls must contain public HTTPS URLs")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("reference_urls must not point to a private address")

    roles = _string_list(
        request.get("reference_roles"),
        field="reference_roles",
        limit=30,
        item_limit=32,
    )
    invalid_roles = [role for role in roles if role not in _REFERENCE_ROLES]
    if invalid_roles:
        raise ValueError(f"unsupported reference role: {invalid_roles[0]}")
    if roles and len(roles) != len(resolved_paths) + len(reference_urls):
        raise ValueError("reference_roles must have the same length as reference_paths plus reference_attachment_ids plus reference_urls")
    return resolved_paths, attachment_ids, reference_urls, roles


def _resolve_attachment_reference(
    attachment_id: str,
    *,
    chat_attachment_ids: set[str],
) -> str:
    from cyrene.runtime.attachments import resolve_managed_attachment_id

    if attachment_id not in chat_attachment_ids:
        raise ValueError(f"attachment references may only name files attached to the current conversation: {attachment_id}")
    path = resolve_managed_attachment_id(attachment_id)
    if path is None:
        raise FileNotFoundError(f"managed media reference is unavailable: {attachment_id}")
    return str(path.resolve())


def _normalize_request(
    raw: Any,
    *,
    index: int,
    chat_attachment_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"requests[{index}] must be an object")
    kind = _text(raw.get("kind"), field=f"requests[{index}].kind", limit=16).lower()
    if kind not in _KINDS:
        raise ValueError(f"requests[{index}].kind must be image, video, or music")
    provider = _text(
        raw.get("provider") or "auto",
        field=f"requests[{index}].provider",
        limit=32,
    ).lower()
    if provider not in _PROVIDERS:
        raise ValueError(f"unsupported media provider: {provider}")

    prompt = _text(raw.get("prompt"), field=f"requests[{index}].prompt", limit=100_000)
    lyrics = _text(raw.get("lyrics"), field=f"requests[{index}].lyrics", limit=100_000)
    if not prompt and not (kind == "music" and lyrics):
        raise ValueError(f"requests[{index}] requires prompt or music lyrics")

    reference_paths, attachment_ids, reference_urls, reference_roles = _resolve_references(
        raw,
        chat_attachment_ids=chat_attachment_ids,
    )
    parameters = raw.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError(f"requests[{index}].parameters must be an object")

    parameters = dict(parameters)
    if any(key in parameters for key in ("mask", "mask_path")):
        raise ValueError(f"requests[{index}].mask_path must be supplied as the safe top-level field")

    mask_path = _text(
        raw.get("mask_path"),
        field=f"requests[{index}].mask_path",
        limit=4096,
    )
    mask_attachment_id = _text(
        raw.get("mask_attachment_id"),
        field=f"requests[{index}].mask_attachment_id",
        limit=512,
    )
    if mask_path and mask_attachment_id:
        raise ValueError(f"requests[{index}] must use only one of mask_path or mask_attachment_id")
    resolved_mask = ""
    if mask_path:
        mask = resolve_tool_path(mask_path)
        if not mask.exists() or not mask.is_file():
            raise FileNotFoundError(f"media mask not found: {mask_path}")
        resolved_mask = str(mask.resolve())
    elif mask_attachment_id:
        resolved_mask = _resolve_attachment_reference(
            mask_attachment_id,
            chat_attachment_ids=chat_attachment_ids,
        )

    request: dict[str, Any] = {
        "kind": kind,
        "provider": provider,
        "prompt": prompt,
        "model": _text(raw.get("model"), field=f"requests[{index}].model", limit=240),
        "name": _text(raw.get("name"), field=f"requests[{index}].name", limit=255),
        "reference_paths": reference_paths,
        "reference_attachment_ids": attachment_ids,
        "reference_urls": reference_urls,
        "reference_roles": reference_roles,
        "parameters": parameters,
    }
    if resolved_mask:
        if kind != "image":
            raise ValueError(f"requests[{index}].mask_path is only valid for image jobs")
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
        value = _text(raw.get(field), field=f"requests[{index}].{field}", limit=limit)
        if value:
            request[field] = value
    if lyrics:
        request["lyrics"] = lyrics
    for field in ("is_instrumental", "generate_audio"):
        if field in raw:
            request[field] = bool(raw[field])
    if raw.get("duration") is not None:
        duration = float(raw["duration"])
        if not math.isfinite(duration) or (duration != -1 and not 1 <= duration <= 600):
            raise ValueError(f"requests[{index}].duration must be -1 (provider auto) or between 1 and 600 seconds")
        request["duration"] = duration
    if raw.get("number_of_outputs") is not None:
        numeric_count = float(raw["number_of_outputs"])
        if not math.isfinite(numeric_count) or not numeric_count.is_integer():
            raise ValueError(f"requests[{index}].number_of_outputs must be an integer")
        count = int(numeric_count)
        if not 1 <= count <= 8:
            raise ValueError(f"requests[{index}].number_of_outputs must be between 1 and 8")
        request["number_of_outputs"] = count
    if raw.get("seed") is not None:
        numeric_seed = float(raw["seed"])
        if not math.isfinite(numeric_seed) or not numeric_seed.is_integer() or numeric_seed < 0:
            raise ValueError(f"requests[{index}].seed must be a non-negative integer")
        seed = int(numeric_seed)
        request["seed"] = seed
    return request


async def _tool_start_media_generation(
    args: dict[str, Any],
    context: PluginContext,
) -> str:
    from cyrene.media.manager import get_media_job_manager
    from cyrene.media.settings import get_media_settings

    try:
        agent_id = str(run_context_value(context, "agent_id", "main") or "main")
        caller = str(run_context_value(context, "caller", "main_agent") or "main_agent")
        if agent_id != "main" or caller not in {
            "main",
            "main_agent",
            "execution_agent",
        }:
            raise PermissionError(
                "Only the main conversation Agent can create media jobs."
            )
        chat_id = str(run_context_value(context, "session_id", "") or "").strip()
        if not chat_id:
            raise ValueError("Media generation requires an active conversation.")
        project_id = str(context.data.get("project_id") or "").strip()
        if not project_id:
            raise ValueError(
                "Media generation requires a conversation attached to a project."
            )
        raw_requests = args.get("requests")
        if not isinstance(raw_requests, list) or not 1 <= len(raw_requests) <= 8:
            raise ValueError("requests must contain between 1 and 8 media jobs")
        visible_attachment_ids = _chat_attachment_ids(context)
        requests = [
            _normalize_request(
                raw,
                index=index,
                chat_attachment_ids=visible_attachment_ids,
            )
            for index, raw in enumerate(raw_requests)
        ]
        idempotency_key = _text(
            args.get("idempotency_key"),
            field="idempotency_key",
            limit=160,
        )
        if idempotency_key and len(idempotency_key) < 8:
            raise ValueError("idempotency_key must contain at least 8 characters")
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
        settings = get_media_settings()
        created = await asyncio.to_thread(
            get_media_job_manager().create_batch,
            chat_id=chat_id,
            project_id=project_id,
            requests=requests,
            wake_note="",
            idempotency_key=idempotency_key,
            owner_tool_call_id=owner_call_id,
            max_attempts=int(settings.get("max_attempts") or 2),
        )
    except (FileNotFoundError, LookupError, PermissionError, TypeError, ValueError) as exc:
        return f"Error: {exc}"

    wake_status = str(created.get("wake_status") or "")
    already_settled = str(created.get("status") or "") == "existing" and wake_status in {"delivered", "cancelled"}
    wake_hint = (
        "This exact media batch was already delivered or cancelled. Do not end the turn waiting for another wake; continue using the visible result."
        if already_settled
        else (
            "The media jobs are running independently. End this turn now; do not "
            "wait, poll, inspect the media database, or start a terminal watcher. "
            "Completed image, video, and music outputs will be attached directly "
            "to this chat before one internal wake resumes the Agent for the batch."
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
