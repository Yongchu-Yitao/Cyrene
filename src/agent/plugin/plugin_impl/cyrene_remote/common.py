"""Shared authorization boundary for remote Cyrene Agent tools."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from agent.plugin import PluginContext
from agent.plugin.native_runtime import run_context_value
from cyrene.runtime.remote_control import (
    RemoteControlStore,
    get_remote_gateway,
)


def remote_tool_error(exc: Exception) -> dict[str, Any]:
    """Return a stable controller-side error instead of an ambiguous string."""
    message = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, sqlite3.OperationalError) and (
        "locked" in message.lower() or "busy" in message.lower()
    ):
        return {
            "ok": False,
            "code": "remote_controller_database_busy",
            "error": message,
            "error_origin": "controller",
            "retryable": True,
        }
    if isinstance(exc, asyncio.TimeoutError):
        return {
            "ok": False,
            "code": "remote_command_timeout",
            "error": "remote command timed out",
            "error_origin": "transport",
            "retryable": True,
        }
    if isinstance(exc, PermissionError):
        return {
            "ok": False,
            "code": "remote_controller_permission_denied",
            "error": message,
            "error_origin": "controller",
            "retryable": False,
        }
    if isinstance(exc, (ConnectionError, OSError)):
        return {
            "ok": False,
            "code": "remote_transport_unavailable",
            "error": message,
            "error_origin": "transport",
            "retryable": True,
        }
    return {
        "ok": False,
        "code": "remote_controller_error",
        "error": message,
        "error_origin": "controller",
        "retryable": False,
    }


def selected_remote_devices(
    context: PluginContext,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the current chat and only its still-trusted selected devices."""
    chat_id = str(run_context_value(context, "session_id", "") or "").strip()
    if not chat_id:
        raise ValueError("当前没有活动对话，无法解析远程设备上下文")
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
        raise ValueError("远程设备工具缺少数据库上下文")

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
            "目标远程设备未添加到当前对话上下文，或授权已被撤销"
        )
    if not devices:
        raise PermissionError("当前对话尚未添加可控制的远程设备")
    if len(devices) > 1:
        raise ValueError("当前对话有多个远程设备，请明确提供 device_id")
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
        raise ValueError("远程设备工具缺少数据库上下文")
    gateway = get_remote_gateway(db_path)
    if gateway is None:
        raise RuntimeError("远程连接尚未启动，请先在设置中启用远程控制")
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
