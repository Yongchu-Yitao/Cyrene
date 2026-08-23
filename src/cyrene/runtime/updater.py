"""应用内更新检查器 — 通过 GitHub Releases API 检查、下载、安装更新。"""

import asyncio
import hashlib
import logging
import os
import platform
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from packaging.version import Version

from cyrene.runtime.paths import TEMP_DIR
from cyrene.runtime.version import get_version

logger = logging.getLogger(__name__)

# GitHub 仓库配置
_DEFAULT_REPO = "Yongchu-Yitao/Cyrene"
_UPDATE_REPO = os.environ.get("UPDATE_REPO", _DEFAULT_REPO)
_GITHUB_API = f"https://api.github.com/repos/{_UPDATE_REPO}/releases"
_DEFAULT_UPDATE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
_notified_update_keys: set[str] = set()
_download_in_progress = False
_auto_download_task: "asyncio.Task | None" = None
_UPDATE_STATE_KEY = "update_download_state"
_UPDATE_PENDING_KEY = "update_download_pending"
_state_restored = False


def _release_version(value: str) -> Version:
    """Parse public release labels, including the PEP 440 build alias for -fix."""
    normalized = value[:-4] + "+fix" if value.endswith("-fix") else value
    return Version(normalized)


def _current_version() -> str:
    """从 pyproject.toml 读取当前版本。"""
    return get_version()


def _beta_updates_enabled() -> bool:
    """读取用户是否选择接收测试版（pre-release）更新。"""
    try:
        from cyrene.runtime import settings_store
        return bool(settings_store.get("beta_updates", False))
    except Exception:
        return False


def _auto_update_enabled() -> bool:
    """读取用户是否开启自动下载更新（下载后仍需用户手动重启安装）。"""
    try:
        from cyrene.runtime import settings_store
        return bool(settings_store.get("auto_update", True))
    except Exception:
        return True


def _update_check_interval_seconds() -> int:
    raw = os.environ.get("CYRENE_UPDATE_CHECK_INTERVAL_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_UPDATE_CHECK_INTERVAL_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return _DEFAULT_UPDATE_CHECK_INTERVAL_SECONDS


@dataclass
class UpdateInfo:
    available: bool
    current_version: str
    latest_version: str
    published_at: str = ""
    download_url: str = ""
    release_notes: str = ""
    asset_name: str = ""
    asset_size: int = 0
    asset_sha256: str = ""
    # 有新版本但找不到适配当前平台/架构的安装包时的明确错误。非空即表示
    # “有更新但无法自动安装”，调用方据此提示用户手动下载，而不是装错平台的包。
    error: str = ""


@dataclass
class DownloadResult:
    path: Path
    size: int
    sha256: str


class UpdateDownloadInProgressError(ValueError):
    """请求下载时已有另一个下载任务正在进行（后台自动下载或手动下载）。"""


def _platform_filter() -> str:
    """返回当前平台 release asset 名称中应包含的匹配关键词（统一小写）。

    资产名遵循 CI 的 electron-builder ``artifactName`` 模板
    （见 electron/package.json 与 .github/workflows/release.yml）：

      - macOS:        ``Cyrene-<ver>-mac.dmg``
      - Windows x64:  ``Cyrene-<ver>-win-x64.exe`` (standard installer)
      - Windows ARM:  ``Cyrene-<ver>-win-arm64.exe`` (standard installer)
      - Linux:        ``Cyrene-<ver>-x64.AppImage``

    Windows 便携 Runtime 会设置 ``PORTABLE_EXECUTABLE_FILE``，此时匹配
    ``-portable.exe``；安装版则匹配标准安装器。Windows 自 0.6.0b0 起按架构
    区分文件名，所以这里依据 ``platform.machine()``（ARM64 / AMD64）选择
    x64 还是 arm64，而不是写死单一 token。

    调用方在 check_for_update() 中用 ``key in name.lower()`` 做大小写无关的子串
    比较，故此处一律返回小写（旧实现返回的 ``x64.AppImage`` 含大写，永远匹配
    不到小写后的资产名，这里一并修正）。
    """
    if sys.platform == "darwin":
        return ".dmg"
    elif sys.platform == "win32":
        machine = platform.machine().lower()
        suffix = "-portable.exe" if _is_windows_portable_runtime() else ".exe"
        if machine.startswith(("arm", "aarch")):
            return f"win-arm64{suffix}"
        return f"win-x64{suffix}"
    elif sys.platform.startswith("linux"):
        return "x64.appimage"
    return sys.platform.lower()


def _is_windows_portable_runtime() -> bool:
    """Return whether electron-builder launched the single-file portable app."""
    return bool(os.environ.get("PORTABLE_EXECUTABLE_FILE", "").strip())


def _sha256_from_asset(asset: dict) -> str:
    """Return a bare sha256 hex digest from a GitHub release asset, if present."""
    raw = str(asset.get("digest") or "").strip()
    if raw.lower().startswith("sha256:"):
        raw = raw.split(":", 1)[1].strip()
    raw = raw.lower()
    if len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw):
        return raw
    return ""


async def _fetch_target_release(
    client: httpx.AsyncClient, include_prerelease: bool
) -> dict | None:
    """选择目标 release。

    稳定版走 GitHub 的 /releases/latest（它会自动跳过 pre-release）；
    若用户接收测试版，则拉取 release 列表并选出版本号最高的一个（含 pre-release）。
    """
    if not include_prerelease:
        resp = await client.get(f"{_GITHUB_API}/latest")
        if resp.status_code != 200:
            logger.debug("GitHub API returned %d", resp.status_code)
            return None
        return resp.json()

    resp = await client.get(_GITHUB_API, params={"per_page": 30})
    if resp.status_code != 200:
        logger.debug("GitHub API returned %d", resp.status_code)
        return None

    best: dict | None = None
    best_v: Version | None = None
    for rel in resp.json():
        if rel.get("draft"):
            continue
        candidate = str(rel.get("tag_name", "")).lstrip("v")
        try:
            cand_v = _release_version(candidate)
        except ValueError:
            continue
        if best_v is None or cand_v > best_v:
            best_v, best = cand_v, rel
    return best


async def check_for_update(include_prerelease: bool | None = None) -> UpdateInfo:
    """查询 GitHub Releases API，比较版本。

    include_prerelease 为 None 时读取用户设置（beta_updates）；显式传入则覆盖。
    """
    current = _current_version()
    if include_prerelease is None:
        include_prerelease = _beta_updates_enabled()

    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False, follow_redirects=True) as client:
            data = await _fetch_target_release(client, include_prerelease)
            if not data:
                return UpdateInfo(available=False, current_version=current, latest_version="")

            tag: str = data.get("tag_name", "")
            latest = tag.lstrip("v")

            if not latest:
                return UpdateInfo(available=False, current_version=current, latest_version="")

            try:
                cur_v = _release_version(current)
                new_v = _release_version(latest)
            except ValueError:
                logger.debug("Invalid version format: cur=%s latest=%s", current, latest)
                return UpdateInfo(available=False, current_version=current, latest_version=latest)

            if new_v <= cur_v:
                return UpdateInfo(
                    available=False,
                    current_version=current,
                    latest_version=latest,
                    published_at=str(data.get("published_at") or ""),
                    release_notes=data.get("body", ""),
                )

            # 查找匹配当前平台的 asset。两侧统一小写做大小写无关的子串匹配，
            # 避免 token 大小写与资产名不一致而漏匹配（参见 _platform_filter）。
            platform_key = _platform_filter().lower()
            asset_url = ""
            asset_name = ""
            asset_size = 0
            asset_sha256 = ""

            for asset in data.get("assets", []):
                name: str = asset.get("name", "")
                if platform_key in name.lower():
                    asset_url = asset.get("browser_download_url", "")
                    asset_name = name
                    asset_size = asset.get("size", 0)
                    asset_sha256 = _sha256_from_asset(asset)
                    break

            # 找不到本平台/架构对应的包时，绝不回退到 assets[0]——那正是把
            # Windows 用户推去下载 macOS .dmg 的根因。返回明确的“无兼容包”错误，
            # 由调用方提示用户手动下载。
            if not asset_url:
                arch = platform.machine() or "unknown"
                msg = (
                    f"该版本（{latest}）暂无适配当前平台的安装包"
                    f"（{sys.platform}/{arch}，匹配关键词 {platform_key!r}）"
                )
                logger.warning("Update available but no matching asset: %s", msg)
                return UpdateInfo(
                    available=True,
                    current_version=current,
                    latest_version=latest,
                    published_at=str(data.get("published_at") or ""),
                    release_notes=data.get("body", ""),
                    error=msg,
                )

            return UpdateInfo(
                available=True,
                current_version=current,
                latest_version=latest,
                published_at=str(data.get("published_at") or ""),
                download_url=asset_url,
                release_notes=data.get("body", ""),
                asset_name=asset_name,
                asset_size=asset_size,
                asset_sha256=asset_sha256,
            )

    except Exception as exc:
        logger.debug("Update check failed: %s", exc)
        return UpdateInfo(available=False, current_version=current, latest_version="")


async def download_update(
    url: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> DownloadResult | None:
    """下载更新包到临时目录，支持断点续传。

    - 目标文件已存在部分内容时（上次失败/中断留下的），发 ``Range`` 请求续传
      而非从头下载；服务器不支持 Range（返回 200 全量）则回退为从头重下。
    - 本地部分与服务器不一致（416 且大小不符）时删除后从头下载。
    - 同一时间只允许一个下载任务（后台自动下载与手动下载并发时会竞争写同一个
      目标文件）。并发调用直接抛 UpdateDownloadInProgressError（ValueError 子类），
      由调用方（工具/路由）转为可读错误或转去展示已有下载的进度。
    """
    global _download_in_progress
    if _download_in_progress:
        raise UpdateDownloadInProgressError("update download already in progress")
    if not url:
        return None

    dest = TEMP_DIR / "updates" / Path(url).name
    dest.parent.mkdir(parents=True, exist_ok=True)

    _download_in_progress = True
    try:
        return await _download_to(dest, url, progress_callback)
    finally:
        _download_in_progress = False


def is_download_in_progress() -> bool:
    """是否有下载任务正在进行（后台自动下载或手动下载）。

    调用方（路由/工具）在重置共享下载进度前先检查它：已有下载在跑时不应
    清空/改写进度状态，而应转去展示正在进行的下载。
    """
    return _download_in_progress


def _content_range_total(resp) -> int:
    """Parse ``Content-Range: bytes start-end/total`` and return total bytes."""
    raw = str(resp.headers.get("content-range") or "")
    try:
        return int(raw.split("/")[-1])
    except (ValueError, IndexError):
        return 0


async def _download_to(
    dest: Path,
    url: str,
    progress_callback: Callable[[int, int], None] | None,
) -> DownloadResult:
    """单次下载会话：从已有部分续传，最终返回完整文件（含全文件 sha256）。"""
    resume_from = dest.stat().st_size if dest.exists() else 0

    async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
        headers = {"Range": f"bytes={resume_from}-"} if resume_from > 0 else None
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 416 and resume_from > 0:
                # Range 超界：本地文件已等于服务器总大小（上次下载已完整但未校验）。
                size = _content_range_total(resp)
                if size and dest.stat().st_size == size:
                    checksum = _hash_file(dest)
                    logger.info(
                        "Update already fully downloaded: %s (%d bytes), SHA256=%s",
                        dest, size, checksum,
                    )
                    return DownloadResult(path=dest, size=size, sha256=checksum)
                # 本地部分与服务器不一致，删掉从头下。
                logger.warning(
                    "Local partial %s (%d bytes) does not match server total %d; re-downloading",
                    dest, dest.stat().st_size, size,
                )
                dest.unlink(missing_ok=True)
                return await _download_to(dest, url, progress_callback)

            if resp.status_code == 206 and resume_from > 0:
                total = _content_range_total(resp)
                if not total:
                    total = resume_from + int(resp.headers.get("content-length", 0))
                mode = "ab"
            else:
                resp.raise_for_status()
                if resume_from > 0:
                    # 服务器忽略了 Range（200 全量返回）：覆盖重写。
                    dest.unlink(missing_ok=True)
                    resume_from = 0
                total = int(resp.headers.get("content-length", 0))
                mode = "wb"

            downloaded = resume_from
            hasher = hashlib.sha256()
            if mode == "ab":
                _update_hash_from_file(dest, hasher)
            with open(dest, mode) as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        progress_callback(downloaded, total)

            if total and downloaded != total:
                raise ValueError(
                    f"update download incomplete: {downloaded} of {total} bytes"
                )

    checksum = hasher.hexdigest()
    if resume_from > 0:
        logger.info(
            "Resumed update download: %s (%d bytes / %.1f MB, resumed from %d), SHA256=%s",
            dest, downloaded, downloaded / (1024 * 1024), resume_from, checksum,
        )
    else:
        logger.info(
            "Downloaded update: %s (%d bytes / %.1f MB), SHA256=%s",
            dest, downloaded, downloaded / (1024 * 1024), checksum,
        )
    return DownloadResult(path=dest, size=downloaded, sha256=checksum)


def _hash_file(path: Path) -> str:
    """Read a local file and return its bare sha256 hex digest."""
    hasher = hashlib.sha256()
    _update_hash_from_file(path, hasher)
    return hasher.hexdigest()


def _update_hash_from_file(path: Path, hasher) -> None:
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            hasher.update(block)


def get_restart_script(update_file: Path) -> str:
    """生成平台特定的重启更新脚本。"""
    if sys.platform == "darwin":
        return _restart_script_macos(update_file)
    elif sys.platform == "win32":
        return _restart_script_windows(update_file)
    else:
        return _restart_script_linux(update_file)


# 注意：不再提供旧版捆绑运行时（codex_cli_bin / cv2）的迁移。
# 捆绑运行时无法跨版本迁移：旧版本（0.7.x）进程没有迁移代码，且各平台重启脚本
# （macOS rm -rf / Windows NSIS / Linux mv）在新代码运行前已删除旧 bundle。
# 新版本依赖按需下载兜底（codex_cli.py / opencv_runtime.py 的 on-demand 下载器）。

def _current_app_executable() -> Path | None:
    raw = os.environ.get("CYRENE_APP_EXECUTABLE", "").strip()
    return Path(raw).expanduser() if raw else None


def _current_macos_app_bundle() -> Path:
    app_exe = _current_app_executable()
    if app_exe:
        for parent in app_exe.parents:
            if parent.suffix == ".app":
                return parent
    return Path("/Applications/Cyrene.app")


def _restart_script_macos(dmg_path: Path) -> str:
    """macOS: 挂载 DMG，替换 .app，重启。

    优先覆盖当前实际安装位置，而不是写死 /Applications。
    所有输出重定向到 /tmp/cyrene_update.log 用于诊断。
    """
    app_bundle = _current_macos_app_bundle()
    app_bundle_q = shlex.quote(str(app_bundle))
    dmg_path_q = shlex.quote(str(dmg_path))
    return (
        '#!/bin/bash\n'
        '# Cyrene updater — macOS\n'
        'set -e\n'
        'exec >>/tmp/cyrene_update.log 2>&1\n'
        'echo "=== Cyrene update $(date) ==="\n'
        f'echo "DMG: {dmg_path_q}"\n'
        f'echo "Target app: {app_bundle_q}"\n'
        'sleep 2\n'
        'echo "Mounting update..."\n'
        # Detach ALL existing Cyrene mounts first, then force mount at
        # /Volumes/Cyrene regardless of DMG's internal volume name
        'for vol in /Volumes/Cyrene*; do\n'
        '  [ -d "$vol" ] && hdiutil detach "$vol" -quiet 2>/dev/null\n'
        'done\n'
        f'hdiutil attach {dmg_path_q} -nobrowse -quiet -mountpoint /Volumes/Cyrene\n'
        'echo "attach exit code: $?"\n'
        'VOL="/Volumes/Cyrene"\n'
        'if [ -d "$VOL" ]; then\n'
        '    echo "Found volume, installing..."\n'
        f'    rm -rf {app_bundle_q}\n'
        '    echo "rm exit code: $?"\n'
        f'    mkdir -p {shlex.quote(str(app_bundle.parent))}\n'
        f'    cp -R "$VOL/Cyrene.app" {shlex.quote(str(app_bundle.parent))}/\n'
        '    echo "cp exit code: $?"\n'
        f'    ls -la {app_bundle_q}\n'
        '    hdiutil detach "$VOL" -quiet\n'
        '    echo "Update complete, restarting..."\n'
        f'    open {app_bundle_q}\n'
        'else\n'
        '    echo "Update failed: DMG not mounted"\n'
        '    echo "hdiutil attach result:"\n'
        '    ls -la /Volumes/ 2>&1\n'
        '    exit 1\n'
        'fi\n'
        f'rm -f {dmg_path_q}\n'
        'echo "Done."\n'
    )


def _restart_script_windows(exe_path: Path) -> str:
    """Windows: update a portable executable in place or run the NSIS installer.

    Portable builds replace their original single-file executable after the
    wrapper exits and require no elevation. Installed builds use PowerShell's
    Start-Process -Verb RunAs so the NSIS updater can request UAC correctly.
    """
    app_exe = _current_app_executable() or Path(r"%LOCALAPPDATA%\Programs\Cyrene\Cyrene.exe")
    if _is_windows_portable_runtime():
        return f"""@echo off
setlocal
:: Cyrene updater — Windows portable
set LOG="%TEMP%\\cyrene_update.log"
>>%LOG% echo === Cyrene portable update %date% %time% ===
>>%LOG% echo UPDATE: {exe_path}
>>%LOG% echo TARGET: {app_exe}

:: Wait for the portable wrapper and Electron child to release the original exe.
timeout /t 3 /nobreak >nul
copy /Y "{exe_path}" "{app_exe}.new" >>%LOG% 2>&1
if errorlevel 1 goto failed
move /Y "{app_exe}.new" "{app_exe}" >>%LOG% 2>&1
if errorlevel 1 goto failed
start "" "{app_exe}"
del "{exe_path}"
>>%LOG% echo Portable update complete.
exit /b 0

:failed
>>%LOG% echo Portable update failed with code %errorlevel%.
del "{app_exe}.new" 2>nul
exit /b 1
"""
    return f"""@echo off
setlocal
:: Cyrene updater — Windows
set LOG="%TEMP%\\cyrene_update.log"
>>%LOG% echo === Cyrene update %date% %time% ===
>>%LOG% echo EXE: {exe_path}
>>%LOG% echo TARGET: {app_exe}
>>%LOG% echo STARTED: %date% %time%

:: 等待主进程完全退出释放文件锁
timeout /t 3 /nobreak >nul

>>%LOG% echo Launching elevated installer via PowerShell...
:: PowerShell Start-Process -Verb RunAs 会正确弹出 UAC 提升提示
:: -Wait 让脚本等待安装完成再继续
powershell -Command "Start-Process -FilePath '{exe_path}' -ArgumentList '/S' -Verb RunAs -Wait -WindowStyle Hidden"
set RC=%errorlevel%
>>%LOG% echo PowerShell exit code: %RC%
>>%LOG% echo UPDATED: %date% %time%

if %RC% equ 0 (
    >>%LOG% echo Update installer completed, verifying...
    :: 额外等待确保文件写入完成
    timeout /t 1 /nobreak >nul
    >>%LOG% echo App start: {app_exe}
    start "" "{app_exe}"
    del "{exe_path}"
) else (
    >>%LOG% echo Update failed (error %RC%) — possible causes:
    >>%LOG% echo   - UAC elevation was cancelled by user
    >>%LOG% echo   - Installer failed to write to target directory
    >>%LOG% echo   - Antivirus blocked the installer
    timeout /t 5 /nobreak >nul
)
endlocal
"""


def _restart_script_linux(appimage_path: Path) -> str:
    """Linux: 覆盖当前 AppImage，重启。"""
    current_exe = _current_app_executable()
    target_path = current_exe if current_exe else Path.home() / ".local" / "bin" / "Cyrene.AppImage"
    appimage_path_q = shlex.quote(str(appimage_path))
    target_path_q = shlex.quote(str(target_path))
    target_parent_q = shlex.quote(str(target_path.parent))
    return f"""#!/bin/bash
# Cyrene updater — Linux
set -e
exec >>/tmp/cyrene_update.log 2>&1
echo "=== Cyrene update $(date) ==="
echo "AppImage: {appimage_path_q}"
echo "Target: {target_path_q}"
sleep 2
echo "Installing update..."
chmod +x {appimage_path_q}
mkdir -p {target_parent_q}
cp {appimage_path_q} {target_path_q}.new
chmod +x {target_path_q}.new
mv {target_path_q}.new {target_path_q}
echo "install exit code: $?"
if [ -f {target_path_q} ]; then
    rm -f {appimage_path_q}
    echo "Update complete, restarting..."
    {target_path_q} &
else
    echo "Update failed: target missing"
    exit 1
fi
"""


# ---- 内存中的更新状态（供 Web UI 查询）----

_latest_update_info: UpdateInfo | None = None
_download_progress: dict = {
    "downloaded": 0,
    "total": 0,
    "done": False,
    "path": "",
    "expected_sha256": "",
    "actual_sha256": "",
    "verified": False,
    "verification_error": "",
}


def get_cached_update_info() -> UpdateInfo | None:
    return _latest_update_info


def set_cached_update_info(info: UpdateInfo) -> None:
    global _latest_update_info
    _latest_update_info = info


def _restore_download_state() -> None:
    """进程重启后恢复上次自动下载的进度/结果。

    ``_download_progress`` 是进程内存状态，没有恢复的话每次启动都会把
    几百 MB 的更新包重新下载一遍。恢复顺序：先恢复已校验完成的包，否则
    恢复未完成的下载（部分文件 + 元数据），由检查循环续传。
    """
    global _state_restored
    if _state_restored:
        return
    _state_restored = True
    try:
        from cyrene.runtime import settings_store

        state = settings_store.get(_UPDATE_STATE_KEY, None) or {}
        if isinstance(state, dict) and state.get("verified"):
            path = Path(str(state.get("path") or ""))
            if path.is_file():
                expected_sha256 = str(state.get("sha256") or "").strip().lower()
                actual_sha256 = _hash_file(path).lower()
                verified = bool(
                    expected_sha256 and actual_sha256 == expected_sha256
                )
                file_size = path.stat().st_size
                _download_progress.update({
                    "downloaded": file_size,
                    "total": int(state.get("total") or state.get("size") or file_size),
                    "done": True,
                    "path": str(path),
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                    "verified": verified,
                    "verification_error": "" if verified else (
                        "Restored update package failed SHA-256 verification."
                    ),
                })
                if not verified:
                    settings_store.set_(_UPDATE_STATE_KEY, None)
                    logger.warning(
                        "Rejected restored update package with invalid SHA-256: %s",
                        path,
                    )
                    return
                logger.info(
                    "Restored and reverified update package from previous run: %s",
                    path,
                )
                return

        pending = settings_store.get(_UPDATE_PENDING_KEY, None) or {}
        if not isinstance(pending, dict):
            return
        path = Path(str(pending.get("path") or ""))
        total = int(pending.get("total") or 0)
        if not path.is_file() or total <= 0:
            return
        existing = path.stat().st_size
        if existing >= total:
            return  # 已完整但未校验：交给检查循环重新走下载-校验流程（416 分支直接复用）
        sha256 = str(pending.get("sha256") or "")
        _download_progress.update({
            "downloaded": existing,
            "total": total,
            "done": False,
            "path": str(path),
            "expected_sha256": sha256,
            "actual_sha256": "",
            "verified": False,
            "verification_error": "",
        })
        logger.info(
            "Restored interrupted update download: %s (%d / %d bytes)",
            path, existing, total,
        )
    except Exception:
        logger.debug("Failed to restore update download state", exc_info=True)


def _persist_pending_state(info: UpdateInfo) -> None:
    """下载开始时记录进行中状态，供进程重启后续传。"""
    try:
        from cyrene.runtime import settings_store

        settings_store.set_(_UPDATE_PENDING_KEY, {
            "version": info.latest_version,
            "url": info.download_url,
            "sha256": info.asset_sha256,
            "total": info.asset_size,
            "path": str(TEMP_DIR / "updates" / Path(info.download_url).name),
        })
    except Exception:
        logger.debug("Failed to persist pending update download", exc_info=True)


def _persist_download_state(info: UpdateInfo, result: DownloadResult) -> None:
    try:
        from cyrene.runtime import settings_store

        settings_store.set_(_UPDATE_STATE_KEY, {
            "version": info.latest_version,
            "sha256": result.sha256,
            "size": result.size,
            "downloaded": result.size,
            "total": info.asset_size,
            "path": str(result.path),
            "verified": True,
        })
        settings_store.set_(_UPDATE_PENDING_KEY, None)
    except Exception:
        logger.debug("Failed to persist update download state", exc_info=True)


def get_download_progress() -> dict:
    _restore_download_state()
    return dict(_download_progress)


class DownloadProgressRepository:
    """Public mutation boundary for the shared update-download state."""

    def snapshot(self) -> dict:
        return get_download_progress()

    def current(self) -> dict:
        return dict(_download_progress)

    def validate_install(
        self,
        validator: Callable[..., tuple[bool, str, str, int]],
    ) -> tuple[bool, str, str, int]:
        _restore_download_state()
        return validator(_download_progress, validate_only=True)

    def checksum_missing(self, info: UpdateInfo) -> str:
        message = "无法验证更新包：发布资产缺少 sha256 校验值。"
        _download_progress.update({
            "downloaded": 0,
            "total": info.asset_size,
            "done": False,
            "path": "",
            "expected_sha256": "",
            "actual_sha256": "",
            "verified": False,
            "verification_error": message,
        })
        return message

    def begin(self, info: UpdateInfo) -> None:
        _download_progress.update({
            "downloaded": 0,
            "total": info.asset_size,
            "done": False,
            "path": "",
            "expected_sha256": info.asset_sha256,
            "actual_sha256": "",
            "verified": False,
            "verification_error": "",
        })

    def progress(self, downloaded: int, total: int) -> None:
        _download_progress["downloaded"] = downloaded
        _download_progress["total"] = total

    def failure(self, message: str) -> None:
        _download_progress["verification_error"] = message

    def complete(
        self,
        result: DownloadResult | None,
        *,
        verified: bool,
        verification_error: str,
    ) -> None:
        _download_progress.update({
            "done": True,
            "path": str(result.path) if result else "",
            "actual_sha256": result.sha256 if result else "",
            "verified": verified,
            "verification_error": verification_error,
        })
        if result:
            _download_progress["downloaded"] = result.size


# ---- 后台任务 ----

def _format_bytes(size: int) -> str:
    n = int(size or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def _push_update_notification(info: UpdateInfo, stage: str, title: str, body: str) -> None:
    """推送一条更新相关通知，stage 参与去重 key（available/ready/failed 各自只发一次）。"""
    if not info.available or not info.latest_version:
        return
    key = f"{info.latest_version}:{info.asset_name or info.error}:{stage}"
    if key in _notified_update_keys:
        return
    _notified_update_keys.add(key)

    try:
        from cyrene.workbench.notifications import append_notification

        append_notification(
            title=title,
            body=body,
            tab="system",
            source="updater",
            source_label="更新检查",
            link_label="打开设置",
            meta={
                "category": "app_update",
                "stage": stage,
                "currentVersion": info.current_version,
                "latestVersion": info.latest_version,
                "publishedAt": info.published_at,
                "assetName": info.asset_name,
                "assetSize": info.asset_size,
                "checksumAvailable": bool(info.asset_sha256),
                "error": info.error,
            },
        )
    except Exception:
        logger.debug("Failed to append update notification", exc_info=True)


def _append_update_notification(info: UpdateInfo) -> None:
    version = f"v{info.latest_version}"
    if info.asset_name:
        body = f"发现新版本 {version}，安装包 {info.asset_name}"
        if info.asset_size:
            body += f"（{_format_bytes(info.asset_size)}）"
        if not info.asset_sha256:
            body += "。该版本缺少 sha256 校验值，无法自动安装。"
    else:
        body = f"发现新版本 {version}，但当前平台暂无可自动安装的更新包。"
        if info.error:
            body += f" {info.error}"
    _push_update_notification(info, "available", f"Cyrene {version} 可用", body)


def _append_update_ready_notification(info: UpdateInfo) -> None:
    if not info.asset_name:
        return
    version = f"v{info.latest_version}"
    body = f"更新包 {info.asset_name} 已自动下载并校验通过"
    if info.asset_size:
        body += f"（{_format_bytes(info.asset_size)}）"
    body += "。打开设置点击「重启更新」即可完成安装。"
    _push_update_notification(info, "ready", f"Cyrene {version} 已就绪", body)


def _append_update_failed_notification(info: UpdateInfo, reason: str) -> None:
    if not info.asset_name:
        return
    version = f"v{info.latest_version}"
    body = f"自动下载 {version} 更新包失败：{reason or '未知原因'}。可在设置中手动下载。"
    _push_update_notification(info, "failed", f"Cyrene {version} 下载失败", body)


async def _auto_download_latest(info: UpdateInfo) -> None:
    """后台自动下载并校验更新包，完成后推送就绪通知。

    安装仍是用户显式操作（设置页「重启更新」），这里只负责把包下载好、
    校验好并放进全局下载进度状态，让 UI/工具都能看到。
    """
    progress = _download_progress
    progress.update({
        "downloaded": 0, "total": info.asset_size, "done": False,
        "path": "", "expected_sha256": info.asset_sha256,
        "actual_sha256": "", "verified": False, "verification_error": "",
    })
    _persist_pending_state(info)
    try:
        result = await download_update(
            info.download_url,
            lambda current, total: progress.update({"downloaded": current, "total": total}),
        )
        if result is None:
            raise ValueError("update download failed")
        progress.update({
            "done": True,
            "path": str(result.path),
            "actual_sha256": result.sha256,
        })
        if info.asset_size and result.size != info.asset_size:
            raise ValueError("downloaded package size does not match the release asset")
        if result.sha256.lower() != (info.asset_sha256 or "").lower():
            raise ValueError("downloaded package SHA-256 verification failed")
        progress["verified"] = True
        # 清掉历史失败/冲突的残留文案，避免"验证失败"误显示在已就绪的包上。
        progress["verification_error"] = ""
        _persist_download_state(info, result)
        logger.info("Auto-downloaded update %s ready for install", info.latest_version)
        _append_update_ready_notification(info)
    except Exception as exc:
        progress["done"] = True
        progress["verification_error"] = str(exc)
        logger.warning("Auto-download of update failed: %s", exc)
        _append_update_failed_notification(info, str(exc))


def _maybe_auto_download(info: UpdateInfo) -> None:
    """检查到新版后，按 auto_update 设置启动后台自动下载（防重复/防并发）。"""
    global _auto_download_task
    if not info.available or not info.download_url or not info.asset_sha256:
        return
    if not _auto_update_enabled():
        return
    if _auto_download_task is not None and not _auto_download_task.done():
        return
    if is_download_in_progress():
        # 已有下载在跑（手动下载等）：跳过本次，避免清空其进度或撞锁失败。
        return
    _restore_download_state()
    progress = _download_progress
    if (
        progress.get("done")
        and progress.get("verified")
        and str(progress.get("actual_sha256") or "").lower() == info.asset_sha256.lower()
    ):
        return
    _auto_download_task = asyncio.create_task(_auto_download_latest(info))


async def _run_update_check_once() -> UpdateInfo:
    info = await check_for_update()
    set_cached_update_info(info)
    if info.available:
        _append_update_notification(info)
        logger.info(
            "Update available: %s → %s (%s)",
            info.current_version, info.latest_version, info.asset_name,
        )
        _maybe_auto_download(info)
    return info


async def background_check(
    *,
    interval_seconds: int | None = None,
    repeat: bool = True,
) -> None:
    """Run update checks in the background and notify Workbench when updates exist."""
    interval = _update_check_interval_seconds() if interval_seconds is None else max(60, int(interval_seconds))
    while True:
        try:
            await _run_update_check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Background update check failed", exc_info=True)
        if not repeat:
            return
        await asyncio.sleep(interval)
