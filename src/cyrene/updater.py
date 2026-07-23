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

from cyrene.app_paths import TEMP_DIR
from cyrene.version import get_version

logger = logging.getLogger(__name__)

# GitHub 仓库配置
_DEFAULT_REPO = "Yongchu-Yitao/Cyrene"
_UPDATE_REPO = os.environ.get("UPDATE_REPO", _DEFAULT_REPO)
_GITHUB_API = f"https://api.github.com/repos/{_UPDATE_REPO}/releases"
_DEFAULT_UPDATE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
_notified_update_keys: set[str] = set()


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
        from cyrene import settings_store
        return bool(settings_store.get("beta_updates", False))
    except Exception:
        return False


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


def _platform_filter() -> str:
    """返回当前平台 release asset 名称中应包含的匹配关键词（统一小写）。

    资产名遵循 CI 的 electron-builder ``artifactName`` 模板
    （见 electron/package.json 与 .github/workflows/release.yml）：

      - macOS:        ``Cyrene-<ver>-mac.dmg``
      - Windows x64:  ``Cyrene-<ver>-win-x64.exe``
      - Windows ARM:  ``Cyrene-<ver>-win-arm64.exe``
      - Linux:        ``Cyrene-<ver>-x64.AppImage``

    Windows 自 0.6.0b0 起按架构区分文件名，所以这里依据 ``platform.machine()``
    （ARM64 / AMD64）选择 x64 还是 arm64，而不是写死单一 token。

    调用方在 check_for_update() 中用 ``key in name.lower()`` 做大小写无关的子串
    比较，故此处一律返回小写（旧实现返回的 ``x64.AppImage`` 含大写，永远匹配
    不到小写后的资产名，这里一并修正）。
    """
    if sys.platform == "darwin":
        return ".dmg"
    elif sys.platform == "win32":
        machine = platform.machine().lower()
        if machine.startswith(("arm", "aarch")):
            return "win-arm64.exe"
        return "win-x64.exe"
    elif sys.platform.startswith("linux"):
        return "x64.appimage"
    return sys.platform.lower()


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
    """下载更新包到临时目录。"""
    if not url:
        return None

    dest = TEMP_DIR / "updates" / Path(url).name
    dest.parent.mkdir(parents=True, exist_ok=True)

    hasher = hashlib.sha256()
    downloaded = 0

    async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        progress_callback(downloaded, total)

    checksum = hasher.hexdigest()
    size_mb = downloaded / (1024 * 1024)
    logger.info(
        "Downloaded update: %s (%d bytes / %.1f MB), SHA256=%s",
        dest, downloaded, size_mb, checksum,
    )
    return DownloadResult(path=dest, size=downloaded, sha256=checksum)


def get_restart_script(update_file: Path) -> str:
    """生成平台特定的重启更新脚本。"""
    if sys.platform == "darwin":
        return _restart_script_macos(update_file)
    elif sys.platform == "win32":
        return _restart_script_windows(update_file)
    else:
        return _restart_script_linux(update_file)


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
    """Windows: 以管理员权限运行 NSIS 安装程序（静默模式）覆盖安装，重启。

    使用 PowerShell 的 Start-Process -Verb RunAs 请求 UAC 提升，
    解决 DETACHED_PROCESS 无法弹出 UAC 提示导致安装静默失败的问题。
    """
    app_exe = _current_app_executable() or Path(r"%LOCALAPPDATA%\Programs\Cyrene\Cyrene.exe")
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


def get_download_progress() -> dict:
    return dict(_download_progress)


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


def _append_update_notification(info: UpdateInfo) -> None:
    if not info.available or not info.latest_version:
        return
    key = f"{info.latest_version}:{info.asset_name or info.error}"
    if key in _notified_update_keys:
        return
    _notified_update_keys.add(key)

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

    try:
        from webui.workbench_notifications import append_notification

        append_notification(
            title=f"Cyrene {version} 可用",
            body=body,
            tab="system",
            source="updater",
            source_label="更新检查",
            link_label="打开设置",
            meta={
                "category": "app_update",
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


async def _run_update_check_once() -> UpdateInfo:
    info = await check_for_update()
    set_cached_update_info(info)
    if info.available:
        _append_update_notification(info)
        logger.info(
            "Update available: %s → %s (%s)",
            info.current_version, info.latest_version, info.asset_name,
        )
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
