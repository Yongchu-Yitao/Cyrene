"""Validate and launch a downloaded desktop application update."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from cyrene.runtime import updater

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
        return False, "Update download has not completed. Download the update before restarting.", "update_download_incomplete", 409

    dest_str = str(download_progress.get("path") or "").strip()
    if not dest_str:
        return False, "No downloaded update package found. Download the update before restarting.", "update_package_missing", 409

    dest = Path(dest_str)
    try:
        if not dest.is_file():
            return False, f"Downloaded update package is missing: {dest}", "update_package_missing", 409
        file_size = dest.stat().st_size
    except OSError as exc:
        return False, f"Unable to inspect downloaded update package: {exc}", "update_package_unreadable", 409
    if file_size <= 0:
        return False, f"Downloaded update package is empty: {dest}", "update_package_empty", 409
    try:
        expected_size = int(download_progress.get("total") or 0)
    except (TypeError, ValueError):
        expected_size = 0
    if expected_size > 0 and file_size != expected_size:
        return False, f"Downloaded update package size mismatch: {file_size} of {expected_size} bytes.", "update_package_size_mismatch", 409
    expected_sha256 = str(download_progress.get("expected_sha256") or "").strip().lower()
    actual_sha256 = str(download_progress.get("actual_sha256") or "").strip().lower()
    if not expected_sha256:
        return False, "Downloaded update package cannot be verified because the release has no sha256 checksum.", "update_checksum_missing", 409
    if not actual_sha256 or actual_sha256 != expected_sha256:
        return False, "Downloaded update package checksum mismatch.", "update_checksum_mismatch", 409
    if not bool(download_progress.get("verified")):
        message = str(download_progress.get("verification_error") or "Downloaded update package has not passed verification.")
        return False, message, "update_package_unverified", 409

    try:
        current_sha256 = updater._hash_file(dest).lower()
    except OSError as exc:
        return False, f"Unable to verify downloaded update package: {exc}", "update_package_unreadable", 409
    download_progress["actual_sha256"] = current_sha256
    if current_sha256 != expected_sha256:
        download_progress["verified"] = False
        download_progress["verification_error"] = (
            "Downloaded update package changed after verification."
        )
        return False, "Downloaded update package checksum mismatch.", "update_checksum_mismatch", 409

    if validate_only:
        return True, "", "", 200

    get_restart_script_fn = get_restart_script_fn or updater.get_restart_script
    popen_fn = popen_fn or subprocess.Popen

    try:
        script = get_restart_script_fn(dest)
        if not str(script or "").strip():
            return False, "Updater script generation returned an empty script.", "update_restart_script_empty", 500

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
    except Exception as exc:
        logger.warning("Failed to spawn updater script", exc_info=True)
        return False, f"Failed to launch updater script: {exc}", "update_restart_launch_failed", 500

    return True, "", "", 200


__all__ = ["launch_update_restart"]
