"""Validate and launch a downloaded desktop application update."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from cyrene.localization import localized
from cyrene.platform import updater

logger = logging.getLogger(__name__)


def launch_update_restart(
    download_progress: dict[str, Any],
    *,
    get_restart_script_fn: Callable[[Path], str] | None = None,
    popen_fn: Any | None = None,
    validate_only: bool = False,
) -> tuple[bool, str, str, int]:
    """Validate the downloaded package and optionally spawn its updater script."""
    if not bool(download_progress.get("done")):
        return False, localized(
            "Update download has not completed. Download the update before restarting.",
            "更新包尚未下载完成，请先完成下载再重启。",
        ), "update_download_incomplete", 409

    dest_str = str(download_progress.get("path") or "").strip()
    if not dest_str:
        return False, localized(
            "No downloaded update package was found. Download the update before restarting.",
            "未找到已下载的更新包，请先下载再重启。",
        ), "update_package_missing", 409

    dest = Path(dest_str)
    try:
        if not dest.is_file():
            return False, localized(
                "The downloaded update package is missing.",
                "已下载的更新包不存在。",
            ), "update_package_missing", 409
        file_size = dest.stat().st_size
    except OSError:
        logger.warning("Unable to inspect downloaded update package", exc_info=True)
        return False, localized(
            "Unable to inspect the downloaded update package.",
            "无法检查已下载的更新包。",
        ), "update_package_unreadable", 409
    if file_size <= 0:
        return False, localized(
            "The downloaded update package is empty.",
            "已下载的更新包为空。",
        ), "update_package_empty", 409
    try:
        expected_size = int(download_progress.get("total") or 0)
    except (TypeError, ValueError):
        expected_size = 0
    if expected_size > 0 and file_size != expected_size:
        return False, localized(
            "Downloaded update package size mismatch: {actual} of {expected} bytes.",
            "已下载的更新包大小不匹配：{actual}/{expected} 字节。",
            actual=file_size,
            expected=expected_size,
        ), "update_package_size_mismatch", 409
    expected_sha256 = str(download_progress.get("expected_sha256") or "").strip().lower()
    actual_sha256 = str(download_progress.get("actual_sha256") or "").strip().lower()
    if not expected_sha256:
        return False, localized(
            "The downloaded update package cannot be verified because the release has no SHA-256 checksum.",
            "发布资产没有 SHA-256 校验值，无法验证已下载的更新包。",
        ), "update_checksum_missing", 409
    if not actual_sha256 or actual_sha256 != expected_sha256:
        return False, localized(
            "Downloaded update package checksum mismatch.",
            "已下载的更新包校验值不匹配。",
        ), "update_checksum_mismatch", 409
    if not bool(download_progress.get("verified")):
        message = str(download_progress.get("verification_error") or localized(
            "The downloaded update package has not passed verification.",
            "已下载的更新包尚未通过验证。",
        ))
        return False, message, "update_package_unverified", 409

    try:
        current_sha256 = updater._hash_file(dest).lower()
    except OSError:
        logger.warning("Unable to verify downloaded update package", exc_info=True)
        return False, localized(
            "Unable to verify the downloaded update package.",
            "无法验证已下载的更新包。",
        ), "update_package_unreadable", 409
    download_progress["actual_sha256"] = current_sha256
    if current_sha256 != expected_sha256:
        download_progress["verified"] = False
        download_progress["verification_error"] = (
            localized(
                "The downloaded update package changed after verification.",
                "已下载的更新包在验证后发生了变化。",
            )
        )
        return False, localized(
            "Downloaded update package checksum mismatch.",
            "已下载的更新包校验值不匹配。",
        ), "update_checksum_mismatch", 409

    if validate_only:
        return True, "", "", 200

    get_restart_script_fn = get_restart_script_fn or updater.get_restart_script
    popen_fn = popen_fn or subprocess.Popen

    try:
        script = get_restart_script_fn(dest)
        if not str(script or "").strip():
            return False, localized(
                "The updater script could not be generated.",
                "无法生成更新器脚本。",
            ), "update_restart_script_empty", 500

        if sys.platform == "win32":
            script_path = dest.parent / "update.bat"
            script_path.write_text(script, encoding="utf-8")
            popen_fn(
                ["cmd", "/c", str(script_path)],
                creationflags=(
                    0x00000200  # CREATE_NEW_PROCESS_GROUP
                    | 0x00000008  # DETACHED_PROCESS
                ),
            )
        else:
            script_path = dest.parent / "update.sh"
            script_path.write_text(script, encoding="utf-8")
            script_path.chmod(0o755)
            popen_fn(["bash", str(script_path)], start_new_session=True)
    except Exception:
        logger.warning("Failed to spawn updater script", exc_info=True)
        return False, localized(
            "Failed to launch the updater.",
            "无法启动更新器。",
        ), "update_restart_launch_failed", 500

    return True, "", "", 200


__all__ = ["launch_update_restart"]
