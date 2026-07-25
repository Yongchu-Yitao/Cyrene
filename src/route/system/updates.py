"""Application update and shutdown routes."""

# ruff: noqa: F403,F405

from cyrene.workbench_runtime import *
from route.errors import error_response


def register_update_routes(router: APIRouter, bot: Any, db_path: str) -> None:
    global _bot, _db_path
    _bot = bot
    _db_path = db_path

    # ---- Update checker ----

    def _local_changelog_text() -> str:
        candidates = [
            Path(__file__).resolve().parents[2] / "CHANGELOG.md",
            BASE_DIR / "CHANGELOG.md",
        ]
        for path in candidates:
            try:
                if path.exists():
                    return path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
        return ""

    @router.get("/api/update/check")
    async def api_update_check():
        """Check for updates via GitHub Releases."""
        from cyrene.updater import check_for_update, set_cached_update_info
        from cyrene.settings_store import set_ as set_setting

        info = await check_for_update()
        set_cached_update_info(info)
        release_notes = info.release_notes or _local_changelog_text()
        if release_notes or info.latest_version:
            set_setting("update_changelog", {
                "version": info.latest_version,
                "published_at": info.published_at,
                "release_notes": release_notes,
            })

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
            # 有新版但无适配本平台的安装包时为非空，前端据此提示手动下载。
            "error": info.error,
        }

    @router.get("/api/update/changelog")
    async def api_update_changelog():
        """Return the latest locally saved release notes."""
        from cyrene.updater import check_for_update
        from cyrene.settings_store import get as get_setting, set_ as set_setting

        changelog = get_setting("update_changelog", {}) or {}
        if not isinstance(changelog, dict):
            changelog = {}
        if not str(changelog.get("release_notes") or "").strip():
            info = await check_for_update()
            release_notes = info.release_notes or _local_changelog_text()
            if release_notes or info.latest_version:
                changelog = {
                    "version": info.latest_version,
                    "published_at": info.published_at,
                    "release_notes": release_notes,
                }
                set_setting("update_changelog", changelog)
        return {
            "version": str(changelog.get("version") or ""),
            "published_at": str(changelog.get("published_at") or ""),
            "release_notes": str(changelog.get("release_notes") or ""),
        }

    @router.post("/api/update/download")
    async def api_update_download():
        """下载更新包。返回下载状态。"""
        from cyrene.updater import (
            get_cached_update_info,
            download_update,
            _download_progress,
        )

        info = get_cached_update_info()
        if not info or not info.download_url:
            # 区分“没有更新”与“有更新但无适配本平台的包”（info.error）。
            return {"ok": False, "error": (info.error if info and info.error else "No update available")}
        if not info.asset_sha256:
            _download_progress["downloaded"] = 0
            _download_progress["total"] = info.asset_size
            _download_progress["done"] = False
            _download_progress["path"] = ""
            _download_progress["expected_sha256"] = ""
            _download_progress["actual_sha256"] = ""
            _download_progress["verified"] = False
            _download_progress["verification_error"] = "无法验证更新包：发布资产缺少 sha256 校验值。"
            return {"ok": False, "error": _download_progress["verification_error"], "code": "update_checksum_missing"}

        def _progress(downloaded: int, total: int) -> None:
            _download_progress["downloaded"] = downloaded
            _download_progress["total"] = total

        _download_progress["downloaded"] = 0
        _download_progress["total"] = info.asset_size
        _download_progress["done"] = False
        _download_progress["path"] = ""
        _download_progress["expected_sha256"] = info.asset_sha256
        _download_progress["actual_sha256"] = ""
        _download_progress["verified"] = False
        _download_progress["verification_error"] = ""

        try:
            result = await download_update(info.download_url, _progress)
        except Exception as exc:
            logger.warning("Update download failed", exc_info=True)
            result = None
            _download_progress["verification_error"] = f"下载更新失败：{exc}"
        _download_progress["done"] = True
        _download_progress["path"] = str(result.path) if result else ""
        _download_progress["actual_sha256"] = result.sha256 if result else ""

        verified = False
        verification_error = ""
        if not result:
            verification_error = _download_progress["verification_error"] or "Download failed"
        elif info.asset_size and result.size != info.asset_size:
            verification_error = f"更新包大小不一致：实际 {result.size} 字节，期望 {info.asset_size} 字节。"
        elif result.sha256.lower() != info.asset_sha256.lower():
            verification_error = "更新包 sha256 校验失败。"
        else:
            verified = True
        _download_progress["downloaded"] = result.size if result else _download_progress["downloaded"]
        _download_progress["verified"] = verified
        _download_progress["verification_error"] = verification_error

        if result and verified:
            return {
                "ok": True,
                "path": str(result.path),
                "size": _download_progress["downloaded"],
                "sha256": result.sha256,
                "verified": True,
            }
        return {
            "ok": False,
            "error": verification_error or "Download failed",
            "verified": False,
            "actual_sha256": _download_progress["actual_sha256"],
            "expected_sha256": _download_progress["expected_sha256"],
        }

    @router.get("/api/update/progress")
    async def api_update_progress():
        """查询下载进度。"""
        from cyrene.updater import get_download_progress
        return get_download_progress()

    @router.post("/api/update/restart")
    async def api_update_restart():
        """写入重启脚本并退出进程（安装更新后调用）。"""
        from cyrene.updater import _download_progress

        ok, message, code, status_code = _launch_update_restart(_download_progress)
        if not ok:
            return error_response(message, status_code, code)

        # 只有 updater 脚本成功启动后才退出，通知 Electron 释放 single-instance lock。
        import os as _os
        _os._exit(42)
