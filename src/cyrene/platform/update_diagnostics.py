"""Preserve installer failures after the desktop process has exited."""

import hashlib
import json
import logging
from pathlib import Path

import httpx

from cyrene.localization import localized

logger = logging.getLogger(__name__)


def describe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return localized("The update server request timed out.", "连接更新服务器超时，请稍后重试。")
    if isinstance(exc, httpx.HTTPStatusError):
        return localized("Update server returned HTTP {status}.", "更新服务器返回 HTTP {status}。", status=exc.response.status_code)
    if isinstance(exc, httpx.RequestError):
        return localized("Unable to connect to the update server ({kind}).", "无法连接更新服务器（{kind}），请检查网络和代理设置。", kind=type(exc).__name__)
    if isinstance(exc, PermissionError):
        return localized("Permission denied while accessing the update package.", "无法读写更新包：权限不足或文件被占用。")
    if isinstance(exc, OSError):
        return localized("Update file operation failed (OS error {code}).", "更新文件操作失败（系统错误码 {code}），请检查磁盘空间和目录权限。", code=exc.errno)
    return str(exc) or type(exc).__name__


def windows_report_script(report_literal: str) -> str:
    return f"""
$reportPath = {report_literal}
$stage = 'waiting_for_exit'
function Write-UpdateResult {{
    param([string]$Status, [string]$Message, [int]$ExitCode = 0)
    @{{status=$Status; stage=$stage; error=$Message; exit_code=$ExitCode;
       package=$updatePath; log_path=$logPath; time=(Get-Date).ToString('o')}} |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $reportPath -Encoding UTF8
}}
"""


def notify_install_failure(report_path: Path) -> None:
    """Deliver a persisted failure once, without depending on network access."""
    try:
        if not report_path.is_file():
            return
        raw = report_path.read_text(encoding="utf-8-sig")
        report = json.loads(raw)
        if not isinstance(report, dict) or report.get("status") != "failed":
            return
        from cyrene.platform import settings_store
        from cyrene.workbench.application.notifications import append_notification

        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        if settings_store.get("update_install_failure_notified", "") == fingerprint:
            return
        append_notification(
            title=localized("The previous update failed", "上次更新未完成"),
            body=localized(
                "Stage: {stage}. Error: {error}. Exit code: {code}. Log: {log}",
                "失败阶段：{stage}。原因：{error}。退出码：{code}。日志：{log}",
                stage=report.get("stage", ""), error=report.get("error", ""),
                code=report.get("exit_code", ""), log=report.get("log_path", ""),
            ),
            tab="system", source="updater", source_label=localized("Update", "应用更新"),
            meta={"category": "app_update", "stage": "install_failed", "reportPath": str(report_path)},
        )
        settings_store.set_("update_install_failure_notified", fingerprint)
    except Exception:
        logger.warning("Unable to report previous installer failure", exc_info=True)
