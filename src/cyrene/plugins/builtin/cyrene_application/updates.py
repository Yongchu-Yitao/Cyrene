from __future__ import annotations

from dataclasses import asdict
from typing import Any
from cyrene.core.plugin import PluginContext

from cyrene.runtime import updater
from cyrene.runtime.host_actions import schedule_action
from cyrene.runtime.host_bridge import HostBridgeError, call_host
from cyrene.plugins.native_runtime import json_result
from cyrene.workbench.application.app_control import audit, authorization_decision, authorize, canonical_hash, envelope, publish_result, remember_idempotent, replay_idempotent

TOOL_NAME = "CyreneUpdateControl"
TOOL_DEF = {"type": "function", "function": {
    "name": TOOL_NAME,
    "description": "Check update status, download a checksum-pinned package, or schedule verified installation after reply finalization.",
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["check", "status", "download", "install"]},
            "reason": {"type": "string", "maxLength": 500},
            "idempotency_key": {"type": "string", "maxLength": 160},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
}}
TOOL_METADATA = {"read_only": False, "resource_keys": ("cyrene:update",), "requires_order": True}


async def handler(args: dict[str, Any], _context: PluginContext) -> str:
    operation = str(args.get("operation") or "")
    if operation == "check":
        info = await updater.check_for_update()
        updater.set_cached_update_info(info)
        return json_result(envelope("success", "cyrene.update.check", "Update check completed.", update=asdict(info)))
    if operation == "status":
        info = updater.get_cached_update_info()
        return json_result(envelope("success", "cyrene.update.check", "Update state read.", update=asdict(info) if info else None, download=updater.get_download_progress()))

    op_id = "cyrene.update.download" if operation == "download" else "cyrene.update.install"
    key = str(args.get("idempotency_key") or "")
    if not key:
        return json_result(envelope("error", op_id, "idempotency_key is required.", error_code="idempotency_required"))
    info = updater.get_cached_update_info()
    op_args = {"operation": operation, "version": str(info.latest_version if info else "")}
    fingerprint = canonical_hash(op_id, op_args)
    replay = replay_idempotent(op_id, key, fingerprint)
    if replay is not None:
        return json_result(replay)
    approval = await authorize(
        op_id, op_args,
        reason=str(args.get("reason") or ""),
    )
    if approval:
        return approval
    try:
        if operation == "download":
            if not info or not info.available or not info.download_url:
                raise ValueError(info.error if info and info.error else "no compatible update is available")
            if not info.asset_sha256:
                raise ValueError("release asset has no SHA-256 digest")
            if updater.is_download_in_progress():
                # 已有下载在跑（后台自动下载/手动下载）时不重置共享进度。
                raise ValueError("update download already in progress")
            progress = updater._download_progress
            progress.update({
                "downloaded": 0, "total": info.asset_size, "done": False,
                "path": "", "expected_sha256": info.asset_sha256,
                "actual_sha256": "", "verified": False, "verification_error": "",
            })
            downloaded = await updater.download_update(
                info.download_url,
                lambda current, total: progress.update({"downloaded": current, "total": total}),
            )
            if downloaded is None:
                raise ValueError("update download failed")
            progress.update({
                "downloaded": downloaded.size,
                "done": True,
                "path": str(downloaded.path),
                "actual_sha256": downloaded.sha256,
            })
            if info.asset_size and downloaded.size != info.asset_size:
                progress["verification_error"] = "downloaded package size does not match the release asset"
                raise ValueError(progress["verification_error"])
            if downloaded.sha256.lower() != info.asset_sha256.lower():
                progress["verification_error"] = "downloaded package SHA-256 verification failed"
                raise ValueError(progress["verification_error"])
            progress["verified"] = True
            result = envelope("success", op_id, "Verified update package downloaded.", effects=[{"version": info.latest_version, "size": downloaded.size, "sha256": downloaded.sha256}])
        elif operation == "install":
            progress = updater.get_download_progress()
            if not progress.get("done") or not progress.get("verified"):
                raise ValueError("a verified update package must be downloaded first")
            host_status = await call_host("host.status")
            if host_status.get("ok") is False or host_status.get("hostKind") != "electron":
                raise ValueError("Electron host is unavailable")
            decision = authorization_decision(op_id, op_args)
            action = schedule_action(
                "update_install",
                idempotency_key=key,
                parameter_hash=fingerprint,
                expected_app_version=str(host_status.get("appVersion") or ""),
                approval_receipt=str(decision.get("receipt") or fingerprint),
                revalidation={
                    "version": str(info.latest_version if info else ""),
                    "sha256": str(progress.get("actual_sha256") or ""),
                    "size": int(progress.get("total") or progress.get("downloaded") or 0),
                },
            )
            result = envelope("scheduled", op_id, "Verified update installation scheduled after final reply persistence.", action_id=action["action_id"], apply_mode="deferred", restart_required=True)
        else:
            raise ValueError("unsupported update operation")
        result["audit_id"] = audit(op_id, op_args, status=result["status"], risk="R2" if operation == "download" else "R3")
    except (HostBridgeError, ValueError) as exc:
        result = envelope("error", op_id, str(exc), error_code="update_error")
    remember_idempotent(op_id, key, fingerprint, result)
    await publish_result(result)
    return json_result(result)


__all__ = ["TOOL_NAME", "TOOL_DEF", "TOOL_METADATA", "handler"]
