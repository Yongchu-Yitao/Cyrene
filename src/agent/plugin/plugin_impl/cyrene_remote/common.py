"""Shared authorization boundary for remote Cyrene Agent tools."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from agent.plugin import PluginContext
from agent.plugin.native_runtime import plugin_localized, run_context_value
from .control import (
    RemoteControlStore,
    get_remote_gateway,
)


logger = logging.getLogger(__name__)


def remote_tool_error(
    exc: Exception,
    context: PluginContext,
) -> dict[str, Any]:
    """Return a stable controller-side error instead of an ambiguous string."""
    message = str(exc).strip() or exc.__class__.__name__
    logger.warning(
        "Remote Plugin operation failed",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    if isinstance(exc, sqlite3.OperationalError) and (
        "locked" in message.lower() or "busy" in message.lower()
    ):
        return {
            "ok": False,
            "code": "remote_controller_database_busy",
            "error": plugin_localized(
                context,
                "The remote controller database is busy. Try again shortly.",
                "远程控制器数据库正忙，请稍后重试。",
            ),
            "error_origin": "controller",
            "retryable": True,
        }
    if isinstance(exc, asyncio.TimeoutError):
        return {
            "ok": False,
            "code": "remote_command_timeout",
            "error": plugin_localized(
                context,
                "The remote command timed out.",
                "远程命令执行超时。",
            ),
            "error_origin": "transport",
            "retryable": True,
        }
    if isinstance(exc, PermissionError):
        return {
            "ok": False,
            "code": "remote_controller_permission_denied",
            "error": plugin_localized(
                context,
                "The remote device is not authorized for this operation.",
                "远程设备未获授权执行此操作。",
            ),
            "error_origin": "controller",
            "retryable": False,
        }
    if isinstance(exc, ValueError):
        return {
            "ok": False,
            "code": "invalid_remote_request",
            "error": plugin_localized(
                context,
                "The remote command request is invalid.",
                "远程命令请求无效。",
            ),
            "error_origin": "controller",
            "retryable": False,
        }
    if isinstance(exc, (ConnectionError, OSError)):
        return {
            "ok": False,
            "code": "remote_transport_unavailable",
            "error": plugin_localized(
                context,
                "The remote connection is unavailable.",
                "远程连接不可用。",
            ),
            "error_origin": "transport",
            "retryable": True,
        }
    return {
        "ok": False,
        "code": "remote_controller_error",
        "error": plugin_localized(
            context,
            "The remote operation failed.",
            "远程操作失败。",
        ),
        "error_origin": "controller",
        "retryable": False,
    }


def selected_remote_devices(
    context: PluginContext,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the current chat and only its still-trusted selected devices."""
    chat_id = str(run_context_value(context, "session_id", "") or "").strip()
    if not chat_id:
        raise ValueError("active conversation is unavailable")
    raw_device_ids = context.data.get("remote_device_ids")
    if not isinstance(raw_device_ids, Sequence) or isinstance(
        raw_device_ids,
        (str, bytes, bytearray),
    ):
        raw_device_ids = ()
    chat = {
        "id": chat_id,
        "remoteDeviceIds": [
            str(item or "").strip()
            for item in raw_device_ids
            if str(item or "").strip()
        ],
    }
    db_path = str(context.data.get("db_path") or "").strip()
    if not db_path:
        raise ValueError("remote database context is unavailable")

    store = RemoteControlStore(db_path)
    selected: list[dict[str, Any]] = []
    for raw_device_id in chat.get("remoteDeviceIds") or []:
        device_id = str(raw_device_id or "").strip()
        peer = store.get_peer(device_id)
        if (
            peer is not None
            and not str(peer.get("revoked_at") or "")
            and bool(peer.get("received_capabilities"))
            and bool(peer.get("received_project_scopes"))
        ):
            selected.append(peer)
    return chat, selected


def resolve_selected_remote_device(
    args: dict[str, Any],
    context: PluginContext,
) -> tuple[dict[str, Any], dict[str, Any]]:
    chat, devices = selected_remote_devices(context)
    requested = str(args.get("device_id") or "").strip()
    if requested:
        for device in devices:
            if str(device.get("device_id") or "") == requested:
                return chat, device
        raise PermissionError(
            "requested remote device is not authorized for this conversation"
        )
    if not devices:
        raise PermissionError("no authorized remote device is attached")
    if len(devices) > 1:
        raise ValueError("device_id is required when multiple devices are attached")
    return chat, devices[0]


async def request_remote_command(
    args: dict[str, Any],
    context: PluginContext,
) -> dict[str, Any]:
    _chat, device = resolve_selected_remote_device(
        args,
        context,
    )
    db_path = str(context.data.get("db_path") or "").strip()
    if not db_path:
        raise ValueError("remote database context is unavailable")
    gateway = get_remote_gateway(db_path)
    if gateway is None:
        raise RuntimeError("remote gateway is not running")
    timeout = max(1.0, min(float(args.get("timeout_seconds") or 30), 120.0))
    result = await gateway.request(
        str(device["device_id"]),
        command=str(args.get("command") or ""),
        project_id=str(args.get("project_id") or ""),
        idempotency_key=str(args.get("idempotency_key") or uuid4().hex),
        payload=dict(args.get("payload") or {}),
        timeout=timeout,
    )
    if result.get("ok") is False and not result.get("error_origin"):
        result = {**result, "error_origin": "remote"}
    return result


__all__ = [
    "remote_tool_error",
    "request_remote_command",
    "resolve_selected_remote_device",
    "selected_remote_devices",
]
