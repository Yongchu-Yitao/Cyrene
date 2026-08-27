"""Knowledge-owned on-demand OpenCV (cv2) runtime downloader.

Release builds ship without OpenCV.  OCR (and any future FFmpeg-backed video
feature) downloads the full opencv-python wheel on first use — the complete
wheel keeps the FFmpeg video codecs, so one download covers both image
processing and video decoding.  The full OpenCV is a hard linker dependency
of OCR's cv2 use, so it cannot be slimmed down inside the bundle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx

from cyrene.config import CACHE_DIR

logger = logging.getLogger(__name__)

PACKAGE_NAME = "opencv-python"
PINNED_VERSION = "5.0.0.93"
PYPI_VERSION_JSON_URL = "https://pypi.org/pypi/opencv-python/{version}/json"

OPENCV_ROOT = Path(CACHE_DIR) / "opencv_runtime"
_INSTALLED_MARKER = "installed.json"
_DOWNLOADS_DIR = ".downloads"
_VERSIONS_DIR = "versions"

_TASKS: dict[str, asyncio.Task] = {}
_PROGRESS: dict[str, Any] = {}
_DOWNLOAD_LOCK = asyncio.Lock()


class OpencvRuntimeMissingError(RuntimeError):
    """OpenCV is not installed and must be downloaded before OCR can run."""


def _platform_wheel_tag() -> str:
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if sys.platform == "darwin":
        return "macosx_13_0_arm64" if arm else "macosx_14_0_x86_64"
    if sys.platform == "win32":
        if arm:
            raise RuntimeError(
                "Windows ARM uses the built-in OCR sidecar and needs no OpenCV"
            )
        return "win_amd64"
    if sys.platform.startswith("linux"):
        return "manylinux_2_17_aarch64" if arm else "manylinux_2_17_x86_64"
    raise RuntimeError(f"OpenCV is not available for this platform: {sys.platform}")


def _runtime_root(version: str) -> Path:
    return OPENCV_ROOT / _VERSIONS_DIR / version


def _marker() -> Path:
    return OPENCV_ROOT / _INSTALLED_MARKER


def installed_version() -> str:
    try:
        payload = json.loads(_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("version") or "")


def _ensure_on_path(version: str) -> bool:
    """Make the installed cv2 importable; returns whether it imports."""
    root = _runtime_root(version)
    if not (root / "cv2").is_dir():
        return False
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        import cv2  # noqa: F401
    except Exception:
        while root_str in sys.path:
            sys.path.remove(root_str)
        _drop_imported_cv2()
        return False
    # The import must resolve to the injected directory, not a coincidentally
    # installed system OpenCV.
    try:
        Path(cv2.__file__).resolve().relative_to(root.resolve())
    except (AttributeError, ValueError):
        while root_str in sys.path:
            sys.path.remove(root_str)
        return False
    return True


def installed_root() -> Path | None:
    """Return the importable runtime root, or None when unavailable."""
    version = installed_version()
    if not version:
        return None
    root = _runtime_root(version)
    if not (root / "cv2").is_dir():
        return None
    loaded = sys.modules.get("cv2")
    if loaded is not None:
        try:
            Path(loaded.__file__).resolve().relative_to(root.resolve())
            return root
        except (AttributeError, ValueError):
            pass
    # A path entry alone is not proof that cv2 imports. Always validate when
    # the managed module is not already loaded from this exact runtime.
    if not _ensure_on_path(version):
        return None
    return root


def ensure() -> Path:
    """Return the importable runtime root or raise OpencvRuntimeMissingError."""
    root = installed_root()
    if root is None:
        raise OpencvRuntimeMissingError(
            "OpenCV runtime is not downloaded (required by local OCR)"
        )
    return root


def status() -> dict[str, Any]:
    task = _TASKS.get("opencv")
    progress = _PROGRESS.get("opencv", {})
    return {
        "installed": installed_root() is not None,
        "version": installed_version(),
        "pinned_version": PINNED_VERSION,
        "downloading": bool(task and not task.done()),
        "downloaded_bytes": int(progress.get("downloaded_bytes") or 0),
        "total_bytes": int(progress.get("total_bytes") or 0),
        "error": str(progress.get("error") or ""),
    }


def _resolve_wheel(version: str) -> tuple[str, int]:
    """Resolve (wheel_url, wheel_bytes) for this platform."""
    tag = _platform_wheel_tag()
    with httpx.Client() as client:
        response = client.get(
            PYPI_VERSION_JSON_URL.format(version=version),
            follow_redirects=True,
            timeout=20,
        )
        response.raise_for_status()
        releases = (response.json().get("urls") or [])
        candidates = [
            file for file in releases
            if str(file.get("filename") or "").endswith(f"-{tag}.whl")
        ]
    if not candidates:
        raise RuntimeError(f"no {tag} wheel for {PACKAGE_NAME}=={version}")
    candidates.sort(key=lambda file: int(file.get("size") or 0))
    wheel = candidates[-1]
    return str(wheel["url"]), int(wheel.get("size") or 0)


async def _stream_wheel(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    progress = _PROGRESS["opencv"]
    async with httpx.AsyncClient() as client:
        async with client.stream("GET", url, follow_redirects=True, timeout=None) as response:
            response.raise_for_status()
            with part.open("wb") as handle:
                async for block in response.aiter_bytes(1024 * 1024):
                    handle.write(block)
                    progress["downloaded_bytes"] += len(block)
    if not part.is_file() or not part.stat().st_size:
        raise RuntimeError("OpenCV download produced an empty archive")
    os.replace(part, destination)


def _install_wheel(wheel: Path, version: str) -> Path:
    """Extract a validated wheel into a fresh versioned directory."""
    staging = OPENCV_ROOT / f".staging-{version}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(staging)
        if not (staging / "cv2").is_dir():
            raise RuntimeError("OpenCV wheel is missing the cv2 package")
        for binary in staging.rglob("*"):
            if binary.is_file() and not binary.name.endswith((".py", ".txt", ".json", ".yaml", ".xml")):
                binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        root = _runtime_root(version)
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists():
            shutil.rmtree(root)
        os.replace(staging, root)
        return root
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _remove_other_versions(keep: str) -> None:
    versions_root = OPENCV_ROOT / _VERSIONS_DIR
    if not versions_root.is_dir():
        return
    for entry in versions_root.iterdir():
        if entry.is_dir() and entry.name != keep:
            shutil.rmtree(entry, ignore_errors=True)


def _legacy_bundle_root() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _legacy_cv2_version() -> str:
    """Report the bundled cv2 version by importing it (best effort)."""
    try:
        import cv2

        return str(getattr(cv2, "__version__", "") or "").strip()
    except Exception:
        return ""


def _drop_imported_cv2() -> None:
    """Forget any in-process cv2 so the migrated copy is imported instead."""
    for key in [
        key for key in sys.modules
        if key == "cv2" or key.startswith("cv2.")
    ]:
        sys.modules.pop(key, None)


def migrate_legacy_bundle() -> bool:
    """Copy the bundled cv2 from a pre-0.8 release into the cache.

    Releases before the on-demand runtime shipped OpenCV inside the
    python-bundle.  The bundle is replaced (and the old files lost) during an
    update, so the migration must run while the old release is still alive —
    the updater invokes this right before it spawns the restart script.
    """
    root = _legacy_bundle_root()
    if root is None:
        return False
    if installed_root() is not None:
        return False
    source = root / "cv2"
    if not (source / "__init__.py").is_file() or not any(
        source.glob("cv2.*")
    ):
        return False
    version = _legacy_cv2_version() or "legacy"
    staging = OPENCV_ROOT / f".migrate-{version}"
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staging / "cv2")
        if not (staging / "cv2").is_dir():
            raise RuntimeError("bundled cv2 package is missing")
        versioned = _runtime_root(version)
        versioned.parent.mkdir(parents=True, exist_ok=True)
        if versioned.exists():
            shutil.rmtree(versioned)
        os.replace(staging, versioned)
        # The running process may already have imported the bundled cv2;
        # drop it so the validation below (and any later OCR) resolves the
        # migrated copy from the cache.
        _drop_imported_cv2()
        if not _ensure_on_path(version):
            raise RuntimeError("migrated OpenCV failed to import")
        _marker().write_text(
            json.dumps({"version": version}), encoding="utf-8"
        )
        # Only remove older versions after the migrated runtime is recorded,
        # so a marker-write failure cannot leave the cache without any cv2.
        _remove_other_versions(version)
        logger.info("Migrated bundled OpenCV %s into the cache", version)
        return True
    except Exception as exc:
        logger.warning("Bundled OpenCV migration failed: %s", exc)
        return False
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


async def _download(version: str = PINNED_VERSION) -> Path:
    """Download and install OpenCV.  Returns the importable runtime root."""
    # The progress cell must exist before any network I/O so the frontend
    # poll never races a request that escaped initialization.
    _PROGRESS["opencv"] = {"downloaded_bytes": 0, "total_bytes": 0, "error": ""}
    wheel: Path | None = None
    try:
        url, total_bytes = await asyncio.to_thread(_resolve_wheel, version)
        _PROGRESS["opencv"]["total_bytes"] = total_bytes
        wheel = OPENCV_ROOT / _DOWNLOADS_DIR / f"opencv-{version}.whl"
        if not wheel.is_file() or not wheel.stat().st_size:
            await _stream_wheel(url, wheel)
        root = await asyncio.to_thread(_install_wheel, wheel, version)
        _drop_imported_cv2()
        if not _ensure_on_path(version):
            if installed_version() == version:
                _marker().unlink(missing_ok=True)
            raise RuntimeError("installed OpenCV failed to import")
        _marker().write_text(json.dumps({"version": version}), encoding="utf-8")
        # Only after the new runtime verifies may older versions be removed;
        # a failed verification keeps the previous runtime usable.
        await asyncio.to_thread(_remove_other_versions, version)
        _PROGRESS.pop("opencv", None)
        return root
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _PROGRESS["opencv"]["error"] = str(exc)
        # A corrupt cached wheel would otherwise be reused forever and make
        # every retry fail without downloading anything.
        if wheel is not None:
            wheel.unlink(missing_ok=True)
        raise


async def _schedule_download(version: str) -> asyncio.Task | None:
    """Create the background download task exactly once, under the lock."""
    async with _DOWNLOAD_LOCK:
        task = _TASKS.get("opencv")
        if task is not None and not task.done():
            return task
        if installed_root() is not None and installed_version() == version:
            return None
        task = asyncio.create_task(_download(version))
        task.add_done_callback(
            lambda finished: finished.exception()
            if not finished.cancelled()
            else None
        )
        _TASKS["opencv"] = task
        return task


def start_download() -> dict[str, Any]:
    """Start (or return the state of) an OpenCV runtime download."""
    if installed_root() is not None:
        return status()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return status()
    task = _TASKS.get("opencv")
    if task is not None and not task.done():
        return status()
    # Reset stale progress from a previous failed attempt so the frontend
    # never reads a leftover error while the retry is in flight.
    _PROGRESS["opencv"] = {"downloaded_bytes": 0, "total_bytes": 0, "error": ""}
    loop.create_task(_schedule_download(PINNED_VERSION))
    return status()


async def download_and_wait() -> Path:
    """Download (when missing) and return the importable runtime root."""
    installed = installed_root()
    if installed is not None:
        return installed
    task = await _schedule_download(PINNED_VERSION)
    if task is None:
        root = installed_root()
        if root is None:
            raise OpencvRuntimeMissingError("OpenCV runtime is not downloaded")
        return root
    try:
        return await task
    except BaseException:
        _TASKS.pop("opencv", None)
        raise


async def delete_all() -> None:
    """Cancel downloads and remove the Knowledge-owned OpenCV cache."""

    task = _TASKS.pop("opencv", None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    _PROGRESS.pop("opencv", None)
    version_roots = OPENCV_ROOT / _VERSIONS_DIR
    for raw in tuple(sys.path):
        try:
            Path(raw).resolve().relative_to(version_roots.resolve())
        except (OSError, ValueError):
            continue
        while raw in sys.path:
            sys.path.remove(raw)
    _drop_imported_cv2()
    await asyncio.to_thread(shutil.rmtree, OPENCV_ROOT, True)
