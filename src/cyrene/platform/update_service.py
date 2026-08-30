"""Application services for checking, downloading, and installing updates."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from cyrene.localization import localized
from cyrene.platform import settings_store, update_install, updater
from cyrene.platform.host_actions import finalize_origin, schedule_action
from cyrene.platform.host_bridge import HostBridgeError, call_host

logger = logging.getLogger(__name__)


class UpdateApplicationError(RuntimeError):
    def __init__(self, message: str, status_code: int, code: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ReleaseNotesRepository:
    """Persist release notes and read the packaged changelog."""

    def __init__(self, changelog_paths: tuple[Path, ...]):
        self.changelog_paths = changelog_paths

    def local_text(self) -> str:
        for path in self.changelog_paths:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        return ""

    def get(self) -> dict[str, Any]:
        value = settings_store.get("update_changelog", {}) or {}
        return value if isinstance(value, dict) else {}

    def save(self, info: updater.UpdateInfo, release_notes: str) -> dict[str, Any]:
        value = {
            "version": info.latest_version,
            "published_at": info.published_at,
            "release_notes": release_notes,
        }
        settings_store.set_("update_changelog", value)
        return value


class DownloadCoordinator:
    """Own the manual update download and verification state machine."""

    def __init__(
        self,
        progress: updater.DownloadProgressRepository,
        download: Callable[..., Awaitable[updater.DownloadResult | None]],
        cached_info: Callable[[], updater.UpdateInfo | None],
        is_in_progress: Callable[[], bool],
    ) -> None:
        self.progress = progress
        self.download_file = download
        self.cached_info = cached_info
        self.is_in_progress = is_in_progress

    async def download(self) -> dict[str, Any]:
        info = self.cached_info()
        if not info or not info.download_url:
            error = (
                info.error
                if info and info.error
                else localized("No update is available.", "暂无可用更新。")
            )
            return {"ok": False, "error": error}
        if not info.asset_sha256:
            error = self.progress.checksum_missing(info)
            return {"ok": False, "error": error, "code": "update_checksum_missing"}
        if self.is_in_progress():
            return self._in_progress_response()

        self.progress.begin(info)
        try:
            result = await self.download_file(info.download_url, self.progress.progress)
        except updater.UpdateDownloadInProgressError:
            return self._in_progress_response()
        except Exception:
            logger.warning("Update download failed", exc_info=True)
            self.progress.failure(
                localized("Update download failed.", "下载更新失败。")
            )
            result = None

        verified, error = self._verify(info, result)
        self.progress.complete(result, verified=verified, verification_error=error)
        if result and verified:
            return {
                "ok": True,
                "path": str(result.path),
                "size": result.size,
                "sha256": result.sha256,
                "verified": True,
            }
        snapshot = self.progress.current()
        return {
            "ok": False,
            "error": error or localized("Download failed.", "下载失败。"),
            "verified": False,
            "actual_sha256": snapshot["actual_sha256"],
            "expected_sha256": snapshot["expected_sha256"],
        }

    def _verify(
        self,
        info: updater.UpdateInfo,
        result: updater.DownloadResult | None,
    ) -> tuple[bool, str]:
        if result is None:
            return False, self.progress.current()["verification_error"] or localized(
                "Download failed.", "下载失败。"
            )
        if info.asset_size and result.size != info.asset_size:
            return False, localized(
                "Update package size mismatch: received {actual} bytes; expected {expected} bytes.",
                "更新包大小不一致：实际 {actual} 字节，预期 {expected} 字节。",
                actual=result.size,
                expected=info.asset_size,
            )
        if result.sha256.lower() != info.asset_sha256.lower():
            return False, localized(
                "Update package SHA-256 verification failed.",
                "更新包 SHA-256 校验失败。",
            )
        return True, ""

    @staticmethod
    def _in_progress_response() -> dict[str, Any]:
        return {
            "ok": False,
            "code": "update_download_in_progress",
            "error": localized(
                "The update package is already downloading in the background.",
                "更新包正在后台下载。",
            ),
        }


class InstallScheduler:
    """Validate an update package and queue host installation."""

    def __init__(
        self,
        validate: Callable[..., tuple[bool, str, str, int]] = update_install.launch_update_restart,
        host_status: Callable[..., Awaitable[dict[str, Any]]] = call_host,
        schedule: Callable[..., dict[str, Any]] = schedule_action,
        finalize: Callable[..., Awaitable[Any]] = finalize_origin,
    ) -> None:
        self.validate = validate
        self.host_status = host_status
        self.schedule_action = schedule
        self.finalize_origin = finalize

    async def schedule(
        self, progress_repository: updater.DownloadProgressRepository
    ) -> dict[str, Any]:
        ok, message, code, status_code = progress_repository.validate_install(
            self.validate
        )
        if not ok:
            raise UpdateApplicationError(message, status_code, code)
        progress = progress_repository.snapshot()
        try:
            host_status = await self.host_status("host.status")
        except HostBridgeError as exc:
            raise UpdateApplicationError(
                localized(
                    "The Electron host is unavailable.",
                    "Electron 宿主不可用。",
                ),
                409,
                "unsupported_host",
            ) from exc
        if host_status.get("hostKind") != "electron":
            raise UpdateApplicationError(
                localized(
                    "The Electron host is unavailable.",
                    "Electron 宿主不可用。",
                ),
                409,
                "unsupported_host",
            )
        action = self.schedule_action(
            "update_install",
            idempotency_key=f"ui-update-{uuid.uuid4().hex}",
            parameter_hash=self._parameter_hash(progress),
            expected_app_version=str(host_status.get("appVersion") or ""),
            approval_receipt="local_ui_update_restart",
            revalidation={
                "sha256": str(progress.get("actual_sha256") or ""),
                "size": int(progress.get("total") or 0),
            },
        )
        asyncio.create_task(self.finalize_origin("", ""))
        return {"ok": True, "status": "scheduled", "action_id": action["action_id"]}

    @staticmethod
    def _parameter_hash(progress: dict[str, Any]) -> str:
        payload = {
            "path": str(progress.get("path") or ""),
            "size": int(progress.get("total") or 0),
            "sha256": str(progress.get("actual_sha256") or ""),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class UpdateApplicationService:
    def __init__(
        self,
        release_notes: ReleaseNotesRepository,
        downloads: DownloadCoordinator,
        installer: InstallScheduler,
    ) -> None:
        self.release_notes = release_notes
        self.downloads = downloads
        self.installer = installer

    async def check(self) -> dict[str, Any]:
        info = await updater.check_for_update()
        updater.set_cached_update_info(info)
        release_notes = info.release_notes or self.release_notes.local_text()
        if release_notes or info.latest_version:
            self.release_notes.save(info, release_notes)
        return {
            "update_available": info.available,
            "current_version": info.current_version,
            "latest_version": info.latest_version,
            "published_at": info.published_at,
            "download_url": info.download_url,
            "release_notes": release_notes,
            "asset_name": info.asset_name,
            "asset_size": info.asset_size,
            "asset_sha256": info.asset_sha256,
            "checksum_available": bool(info.asset_sha256),
            "error": info.error,
        }

    async def changelog(self) -> dict[str, str]:
        changelog = self.release_notes.get()
        if not str(changelog.get("release_notes") or "").strip():
            info = await updater.check_for_update()
            release_notes = info.release_notes or self.release_notes.local_text()
            if release_notes or info.latest_version:
                changelog = self.release_notes.save(info, release_notes)
        return {
            "version": str(changelog.get("version") or ""),
            "published_at": str(changelog.get("published_at") or ""),
            "release_notes": str(changelog.get("release_notes") or ""),
        }

    async def download(self) -> dict[str, Any]:
        return await self.downloads.download()

    def progress(self) -> dict[str, Any]:
        return self.downloads.progress.snapshot()

    async def restart(self) -> dict[str, Any]:
        return await self.installer.schedule(self.downloads.progress)


def build_update_application_service(changelog_path: Path) -> UpdateApplicationService:
    """Compose update services using the current owner-module dependencies."""
    from cyrene.platform import host_actions, host_bridge

    progress = updater.DownloadProgressRepository()
    return UpdateApplicationService(
        ReleaseNotesRepository((changelog_path,)),
        DownloadCoordinator(
            progress,
            updater.download_update,
            updater.get_cached_update_info,
            updater.is_download_in_progress,
        ),
        InstallScheduler(
            validate=update_install.launch_update_restart,
            host_status=host_bridge.call_host,
            schedule=host_actions.schedule_action,
            finalize=host_actions.finalize_origin,
        ),
    )
