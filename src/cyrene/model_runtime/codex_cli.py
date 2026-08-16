"""On-demand Codex CLI runtime downloader.

Release builds no longer bundle the Codex CLI binary.  The runtime is
downloaded to the user cache on first use of the OpenAI OAuth provider:
the latest published wheel by default, with an automatic fallback to the
version pinned by the installed openai-codex SDK when the latest CLI speaks
an incompatible app-server protocol.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
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

PACKAGE_NAME = "openai-codex-cli-bin"
SDK_PACKAGE_NAME = "openai-codex"
PYPI_JSON_URL = "https://pypi.org/pypi/openai-codex-cli-bin/json"
PYPI_VERSION_JSON_URL = "https://pypi.org/pypi/openai-codex-cli-bin/{version}/json"

CODEX_CLI_ROOT = Path(CACHE_DIR) / "codex_cli"
_INSTALLED_MARKER = "installed.json"
_DOWNLOADS_DIR = ".downloads"
_VERSIONS_DIR = "versions"
_BIN_RELATIVE = Path("codex_cli_bin") / "bin"
_BIN_NAME = "codex.exe" if sys.platform == "win32" else "codex"

_TASKS: dict[str, asyncio.Task] = {}
_PROGRESS: dict[str, Any] = {}
_DOWNLOAD_LOCK = asyncio.Lock()


class CodexCliMissingError(RuntimeError):
    """The Codex CLI runtime is not installed and must be downloaded."""


def _platform_wheel_tag() -> str:
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if sys.platform == "darwin":
        return "macosx_11_0_arm64" if arm else "macosx_10_9_x86_64"
    if sys.platform == "win32":
        return "win_arm64" if arm else "win_amd64"
    if sys.platform.startswith("linux"):
        return "manylinux_2_17_aarch64" if arm else "manylinux_2_17_x86_64"
    raise RuntimeError(f"Codex CLI is not available for this platform: {sys.platform}")


_SDK_PINNED_VERSION: str | None = None


def sdk_pinned_version() -> str:
    """Return the Codex CLI version pinned by the installed openai-codex SDK.

    The pinned version is constant for the process lifetime, so it is cached
    after the first importlib metadata read.
    """
    global _SDK_PINNED_VERSION
    if _SDK_PINNED_VERSION is None:
        try:
            _SDK_PINNED_VERSION = importlib.metadata.version(SDK_PACKAGE_NAME)
        except importlib.metadata.PackageNotFoundError:
            _SDK_PINNED_VERSION = ""
    return _SDK_PINNED_VERSION


def _bin_path(version: str) -> Path:
    return CODEX_CLI_ROOT / _VERSIONS_DIR / version / _BIN_RELATIVE / _BIN_NAME


def _marker() -> Path:
    return CODEX_CLI_ROOT / _INSTALLED_MARKER


def installed_version() -> str:
    marker = _marker()
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("version") or "")


def installed_cli_path() -> Path | None:
    """Return the installed CLI binary, or None when it is unavailable."""
    return _installed_cli_path_for_version(installed_version())


def _installed_cli_path_for_version(version: str) -> Path | None:
    if not version:
        return None
    candidate = _bin_path(version)
    if not candidate.is_file():
        return None
    if sys.platform != "win32" and not os.access(candidate, os.X_OK):
        return None
    return candidate


def ensure_cli() -> Path:
    """Return the installed CLI path or raise CodexCliMissingError."""
    candidate = installed_cli_path()
    if candidate is None:
        raise CodexCliMissingError("Codex CLI runtime is not downloaded")
    return candidate


def status() -> dict[str, Any]:
    task = _TASKS.get("cli")
    progress = _PROGRESS.get("cli", {})
    # One installed.json read per call, shared by both fields below.
    version = installed_version()
    return {
        "installed": _installed_cli_path_for_version(version) is not None,
        "version": version,
        "sdk_pinned_version": sdk_pinned_version(),
        "downloading": bool(task and not task.done()),
        "downloaded_bytes": int(progress.get("downloaded_bytes") or 0),
        "total_bytes": int(progress.get("total_bytes") or 0),
        "error": str(progress.get("error") or ""),
    }


def _latest_pypi_payload(client: httpx.Client) -> dict[str, Any]:
    response = client.get(PYPI_JSON_URL, follow_redirects=True, timeout=20)
    response.raise_for_status()
    return response.json()


def _wheel_for_version(payload: dict[str, Any], version: str, tag: str) -> dict[str, Any]:
    releases = payload.get("releases") or {}
    candidates = [
        file for file in releases.get(version) or []
        if str(file.get("filename") or "").endswith(f"-{tag}.whl")
    ]
    if not candidates:
        raise RuntimeError(
            f"no {tag} wheel for {PACKAGE_NAME}=={version}"
        )
    candidates.sort(key=lambda file: int(file.get("size") or 0))
    return candidates[-1]


def _resolve_wheel(version: str | None) -> tuple[str, str, int]:
    """Resolve (version, wheel_url, wheel_bytes) without a running loop."""
    tag = _platform_wheel_tag()
    with httpx.Client() as client:
        if version:
            response = client.get(
                PYPI_VERSION_JSON_URL.format(version=version),
                follow_redirects=True,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        else:
            payload = _latest_pypi_payload(client)
            version = str((payload.get("info") or {}).get("version") or "")
            if not version:
                raise RuntimeError("PyPI did not report a Codex CLI version")
        wheel = _wheel_for_version(payload, version, tag)
    return version, str(wheel["url"]), int(wheel.get("size") or 0)


async def _stream_wheel(url: str, destination: Path, total_bytes: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    progress = _PROGRESS["cli"]
    async with httpx.AsyncClient() as client:
        async with client.stream("GET", url, follow_redirects=True, timeout=None) as response:
            response.raise_for_status()
            with part.open("wb") as handle:
                async for block in response.aiter_bytes(1024 * 1024):
                    handle.write(block)
                    progress["downloaded_bytes"] += len(block)
    if not part.is_file() or not part.stat().st_size:
        raise RuntimeError("Codex CLI download produced an empty archive")
    os.replace(part, destination)


def _install_wheel(wheel: Path, version: str) -> Path:
    """Extract a validated wheel into a fresh versioned directory."""
    staging = CODEX_CLI_ROOT / f".staging-{version}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(staging)
        binary = staging / _BIN_RELATIVE / _BIN_NAME
        if not binary.is_file():
            raise RuntimeError(
                f"Codex CLI wheel is missing {_BIN_RELATIVE / _BIN_NAME}"
            )
        if sys.platform != "win32":
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        versioned = CODEX_CLI_ROOT / _VERSIONS_DIR / version
        versioned.parent.mkdir(parents=True, exist_ok=True)
        if versioned.exists():
            shutil.rmtree(versioned)
        os.replace(staging, versioned)
        return _bin_path(version)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _remove_other_versions(keep: str) -> None:
    versions_root = CODEX_CLI_ROOT / _VERSIONS_DIR
    if not versions_root.is_dir():
        return
    for entry in versions_root.iterdir():
        if entry.is_dir() and entry.name != keep:
            shutil.rmtree(entry, ignore_errors=True)


def _legacy_bundle_root() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _legacy_cli_version(source: Path) -> str:
    """Read the version from the bundled codex-package.json when present."""
    try:
        payload = json.loads(
            (source / "codex-package.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ""
    for key in ("version", "package_version", "codex_version"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def migrate_legacy_bundle() -> bool:
    """Copy the bundled Codex CLI from a pre-0.8 release into the cache.

    Releases before the on-demand runtime shipped codex_cli_bin inside the
    python-bundle.  The bundle is replaced (and the old files lost) during an
    update, so the migration must run while the old release is still alive —
    the updater invokes this right before it spawns the restart script.
    """
    root = _legacy_bundle_root()
    if root is None:
        return False
    if installed_cli_path() is not None:
        return False
    source = root / "codex_cli_bin"
    binary = source / "bin" / _BIN_NAME
    if not binary.is_file():
        return False
    version = _legacy_cli_version(source) or sdk_pinned_version() or "legacy"
    staging = CODEX_CLI_ROOT / f".migrate-{version}"
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staging / "codex_cli_bin")
        staged_binary = staging / _BIN_RELATIVE / _BIN_NAME
        if not staged_binary.is_file():
            raise RuntimeError("bundled Codex CLI binary is missing")
        if sys.platform != "win32" and not os.access(staged_binary, os.X_OK):
            staged_binary.chmod(
                staged_binary.stat().st_mode
                | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
        versioned = CODEX_CLI_ROOT / _VERSIONS_DIR / version
        versioned.parent.mkdir(parents=True, exist_ok=True)
        if versioned.exists():
            shutil.rmtree(versioned)
        os.replace(staging, versioned)
        _marker().write_text(
            json.dumps({"version": version}), encoding="utf-8"
        )
        # Only remove older versions after the migrated runtime is recorded,
        # so a marker-write failure cannot leave the cache without any CLI.
        _remove_other_versions(version)
        logger.info("Migrated bundled Codex CLI %s into the cache", version)
        return True
    except Exception as exc:
        logger.warning("Bundled Codex CLI migration failed: %s", exc)
        return False
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


async def _download(version: str | None = None) -> Path:
    """Download and install the Codex CLI.  Returns the installed binary."""
    # The progress cell must exist before any network I/O so the frontend
    # poll never races a request that escaped initialization.
    _PROGRESS["cli"] = {"downloaded_bytes": 0, "total_bytes": 0, "error": ""}
    wheel: Path | None = None
    try:
        resolved_version, url, total_bytes = await asyncio.to_thread(
            _resolve_wheel, version
        )
        _PROGRESS["cli"]["total_bytes"] = total_bytes
        wheel = CODEX_CLI_ROOT / _DOWNLOADS_DIR / f"{PACKAGE_NAME}-{resolved_version}.whl"
        if not wheel.is_file() or not wheel.stat().st_size:
            await _stream_wheel(url, wheel, total_bytes)
        installed = await asyncio.to_thread(_install_wheel, wheel, resolved_version)
        _marker().write_text(
            json.dumps({"version": resolved_version}), encoding="utf-8"
        )
        # Older versions may only be removed after the new runtime verifies:
        # a failed verification keeps the previous CLI usable.
        if installed_version() != resolved_version:
            raise RuntimeError(
                "installed Codex CLI version "
                f"{installed_version()!r} does not match {resolved_version!r}"
            )
        await asyncio.to_thread(_remove_other_versions, resolved_version)
        _PROGRESS.pop("cli", None)
        return installed
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _PROGRESS["cli"]["error"] = str(exc)
        # A corrupt cached wheel would otherwise be reused forever and make
        # every retry fail without downloading anything.
        if wheel is not None:
            wheel.unlink(missing_ok=True)
        raise


async def _schedule_download(version: str | None) -> asyncio.Task | None:
    """Create the background download task exactly once, under the lock."""
    async with _DOWNLOAD_LOCK:
        task = _TASKS.get("cli")
        if task is not None and not task.done():
            return task
        if installed_cli_path() is not None and (
            version is None or installed_version() == version
        ):
            return None
        task = asyncio.create_task(_download(version))
        task.add_done_callback(
            lambda finished: finished.exception()
            if not finished.cancelled()
            else None
        )
        _TASKS["cli"] = task
        return task


def _wipe_installed() -> None:
    """Remove the installed CLI so a forced reinstall cannot short-circuit."""
    marker = _marker()
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    versions_root = CODEX_CLI_ROOT / _VERSIONS_DIR
    if versions_root.is_dir():
        shutil.rmtree(versions_root, ignore_errors=True)


def start_download(version: str | None = None, *, force: bool = False) -> dict[str, Any]:
    """Start (or return the state of) a Codex CLI download in the background.

    ``force`` is the reinstall path for a broken-but-installed runtime
    (snapshot ``cli.broken``): the current install is wiped and the download
    targets the SDK-pinned version, which is the one known to speak the SDK's
    protocol.
    """
    installed = installed_cli_path()
    if installed is not None and not force:
        return status()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return status()
    task = _TASKS.get("cli")
    if task is not None and not task.done():
        return status()
    if force:
        version = sdk_pinned_version() or version
        _wipe_installed()
    # Reset stale progress from a previous failed attempt so the frontend
    # never reads a leftover error while the retry is in flight.
    _PROGRESS["cli"] = {"downloaded_bytes": 0, "total_bytes": 0, "error": ""}
    loop.create_task(_schedule_download(version))
    return status()


async def wait_for_inflight_download() -> None:
    """Await the in-flight CLI download, if any; never starts a new one.

    Concurrent start attempts join the single background download instead of
    failing with a stale "CLI missing" error while it runs.
    """
    task = _TASKS.get("cli")
    if task is None or task.done():
        return
    try:
        await asyncio.shield(task)
    except BaseException:
        pass


async def download_and_wait(version: str | None = None) -> Path:
    """Download (when missing or when ``version`` differs) and return the CLI.

    Used by the protocol-mismatch fallback, which must swap the installed
    runtime for the SDK-pinned version before retrying.
    """
    installed = installed_cli_path()
    current_version = installed_version()
    if installed is not None and (
        version is None or current_version == version
    ):
        return installed
    task = await _schedule_download(version)
    if task is None:
        return installed_cli_path()  # raced with a completed install
    try:
        return await task
    except BaseException:
        _TASKS.pop("cli", None)
        raise
