"""Shared authorization boundary for remote Cyrene Agent tools."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from cyrene.runtime.remote_control import (
    RemoteControlStore,
    get_remote_gateway,
)


def selected_remote_devices(
    db_path: str,
    *,
    fallback_chat_id: object = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the current chat and only its still-trusted selected devices."""
    from cyrene.agent.context import get_current_session_id
    from cyrene.workbench import chat as chat_service

    chat_id = str(get_current_session_id() or fallback_chat_id or "").strip()
    if not chat_id:
        raise ValueError("当前没有活动对话，无法解析远程设备上下文")
    payload = chat_service._read_chats_store()
    chat = chat_service._find_chat(payload, chat_id)
    if chat is None:
        raise ValueError("当前对话不存在，无法解析远程设备上下文")

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
    db_path: str,
    *,
    fallback_chat_id: object = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    chat, devices = selected_remote_devices(
        db_path,
        fallback_chat_id=fallback_chat_id,
    )
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
    db_path: str,
    *,
    fallback_chat_id: object = "",
) -> dict[str, Any]:
    _chat, device = resolve_selected_remote_device(
        args,
        db_path,
        fallback_chat_id=fallback_chat_id,
    )
    gateway = get_remote_gateway(db_path)
    if gateway is None:
        raise RuntimeError("远程连接尚未启动，请先在设置中启用远程控制")
    timeout = max(1.0, min(float(args.get("timeout_seconds") or 30), 120.0))
    return await gateway.request(
        str(device["device_id"]),
        command=str(args.get("command") or ""),
        project_id=str(args.get("project_id") or ""),
        idempotency_key=str(args.get("idempotency_key") or uuid4().hex),
        payload=dict(args.get("payload") or {}),
        timeout=timeout,
    )


__all__ = [
    "request_remote_command",
    "resolve_selected_remote_device",
    "selected_remote_devices",
]
