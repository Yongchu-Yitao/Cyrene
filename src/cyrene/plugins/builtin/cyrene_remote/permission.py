"""0.7.13-compatible permission boundaries for resolved remote operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.core.plugin.core_impl.permission_boundaries import bash_boundary


_READ_FILE_OPERATIONS = {"stat", "list", "manifest", "download"}
_DESTRUCTIVE_FILE_OPERATIONS = {"delete", "delete_tree"}


def _absolute_remote_path(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    return bool(text) and (text.startswith(("/", "~/")) or ":" in text.split("/", 1)[0])


def _exact_reason(args: Mapping[str, Any], fallback: str) -> str:
    rendered = json.dumps(
        dict(args),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return (
        str(args.get("reason") or fallback)
        + "\n精确操作："
        + rendered[:8_000]
        + "\nSHA-256："
        + digest
    )


def remote_permission_request(
    tool_name: str,
    args: Mapping[str, Any],
    context: PluginContext,
    *,
    device_id: str,
    project_id: str = "",
) -> tuple[dict[str, Any] | None, bool]:
    """Return the exact boundary and whether it is destructively classified."""

    operation = str(args.get("operation") or args.get("command") or "").strip()
    common = {
        "path_hint": str(device_id),
        "reason": _exact_reason(args, "执行用户请求的远程操作"),
        "requires_human": False,
        "device_id": str(device_id),
        "project_id": str(project_id),
    }
    if tool_name in {"RemoteCyreneAction", "RunRemoteCyrene"}:
        return ({
            **common,
            "kind": "scope_elevation",
            "operation": (
                f"操作远程 Cyrene：{operation}"
                if tool_name == "RemoteCyreneAction"
                else "在远程 Cyrene 创建对话并启动 Agent"
            ),
            "scope_hint": "远程设备上的 ",
            "options": ["允许执行这一次", "拒绝"],
        }, False)
    if tool_name == "RemoteCyreneJobs":
        if operation not in {"start", "interrupt", "cancel"}:
            return None, False
        return ({
            **common,
            "kind": "remote_job_control",
            "operation": f"远程作业 {operation}",
            "scope_hint": "远程设备上的 ",
            "options": ["允许执行这一次", "拒绝"],
        }, False)
    if tool_name == "RemoteCyreneFiles":
        payload = args.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        remote_outside = any(_absolute_remote_path(value) for value in (
            args.get("remote_path"),
            args.get("source"),
            args.get("destination"),
            payload.get("path"),
            payload.get("source"),
            payload.get("destination"),
        ))
        irreversible_overwrite = (
            operation == "upload"
            and str(args.get("conflict_policy") or "fail")
            in {"overwrite", "overwrite_if_unchanged"}
        ) or (
            operation in {"copy", "move"}
            and bool(payload.get("overwrite"))
        )
        destructive = (
            operation in _DESTRUCTIVE_FILE_OPERATIONS
            or operation == "sync"
            or irreversible_overwrite
        )
        local_path = str(args.get("local_path") or "") if operation in {"upload", "sync"} else ""
        path_hint = local_path or (
            f"remote://{device_id}/"
            f"{str(args.get('remote_path') or payload.get('path') or '')}"
        )
        if destructive:
            return ({
                **common,
                "kind": "destructive_confirmation",
                "operation": (
                    "同步远程目录（可能覆盖或删除已有内容）"
                    if operation == "sync"
                    else "覆盖远程项目已有内容"
                    if irreversible_overwrite
                    else f"删除远程项目内容：{operation}"
                ),
                "path_hint": path_hint,
                "requires_human": True,
                "scope_hint": "破坏性/不可逆的 ",
                "options": ["允许这次", "本次会话内总是允许", "拒绝"],
            }, True)
        if operation in _READ_FILE_OPERATIONS and not remote_outside:
            return None, False
        return ({
            **common,
            "kind": (
                "read_elevation"
                if local_path
                else "remote_path_read"
                if operation in _READ_FILE_OPERATIONS
                else "remote_file_write"
            ),
            "operation": (
                f"读取远程设备项目外路径：{operation}"
                if operation in _READ_FILE_OPERATIONS
                else f"写入远程设备文件：{operation}"
            ),
            "path_hint": path_hint,
            "scope_hint": "远程项目中的 ",
            "options": ["允许执行这一次", "拒绝"],
        }, False)
    if tool_name == "RemoteHarness":
        if operation != "invoke":
            return None, False
        capability_id = str(args.get("capability_id") or "").strip()
        invoke_arguments = args.get("arguments")
        invoke_arguments = (
            dict(invoke_arguments) if isinstance(invoke_arguments, Mapping) else {}
        )
        shell_text = str(
            invoke_arguments.get("input")
            or invoke_arguments.get("command")
            or invoke_arguments.get("cmd")
            or ""
        )
        shell_request = bash_boundary({"command": shell_text}, context) if shell_text else None
        destructive = (
            shell_request
            if isinstance(shell_request, Mapping)
            and str(shell_request.get("kind") or "") == "destructive_confirmation"
            else None
        )
        capability_lower = capability_id.lower()
        if destructive is None and any(
            marker in capability_lower
            for marker in (".delete", ".delete_", ".remove", ".uninstall", ".format")
        ):
            destructive = {
                "operation": f"远程破坏性能力 {capability_id}",
                "kind": "remote_destructive_capability",
            }
        if destructive is not None:
            return ({
                **common,
                "kind": "destructive_confirmation",
                "operation": str(destructive.get("operation") or f"远程调用 {capability_id}"),
                "requires_human": True,
                "scope_hint": "破坏性/不可逆的 ",
                "options": ["允许这次", "本次会话内总是允许", "拒绝"],
            }, True)
        return ({
            **common,
            "kind": "remote_harness_invoke",
            "operation": f"在远程设备直接调用 {capability_id}",
            "scope_hint": "远程工具调用的 ",
            "options": ["允许执行这一次", "拒绝"],
        }, False)
    return None, False


async def authorize_remote(
    tool_name: str,
    args: Mapping[str, Any],
    context: PluginContext,
    *,
    device_id: str,
    project_id: str = "",
) -> tuple[dict[str, Any] | None, bool]:
    request, destructive = remote_permission_request(
        tool_name,
        args,
        context,
        device_id=device_id,
        project_id=project_id,
    )
    if request is None:
        return None, False
    permission_service = context.services.get("permission")
    review = getattr(permission_service, "request_dynamic_permission", None)
    if not callable(review):
        return {
            "status": "denied",
            "error": "Remote operation denied: the permission service is unavailable.",
        }, False
    result = await review(
        tool_name=tool_name,
        arguments=dict(args),
        request=request,
    )
    return result, destructive and result is None


__all__ = ["authorize_remote", "remote_permission_request"]
