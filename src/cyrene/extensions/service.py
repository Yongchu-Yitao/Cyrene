"""Extension Center aggregate state, search, installation, and recovery.

The service deliberately keeps declarations separate from downloaded runtimes.
Declarations live in the encrypted settings store and are portable; binaries,
caches, and staging data remain under Cyrene-owned runtime directories.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from cyrene.config import CACHE_DIR, DATA_DIR, INSTALL_RESOURCES_DIR, TEMP_DIR
from cyrene.extensions.catalog import (
    ALLOWED_MISE_BACKENDS,
    CURATED_CLIS,
    DEFAULT_SOURCE_SETTINGS,
    HIGH_RISK_MISE_BACKENDS,
    MISE_VERSION,
    RECOMMENDED_ORDER,
    TOOLCHAINS,
    UV_VERSION,
)
from cyrene.learning.skills import build_skills, install_skill_from_path, set_skill_enabled
from cyrene.runtime.settings_store import get as get_setting, set_ as set_setting

logger = logging.getLogger(__name__)

_ROOT = DATA_DIR / "extensions"
_BIN_DIR = _ROOT / "bin"
_MISE_DATA = _ROOT / "mise"
_MISE_CONFIG = _ROOT / "mise-config"
_MISE_CACHE = CACHE_DIR / "extensions" / "mise"
_UV_PYTHON_DIR = _ROOT / "python"
_UV_BIN_DIR = _ROOT / "python-bin"
_TEX_DIR = _ROOT / "tex"
_STAGING_DIR = TEMP_DIR / "extension-installs"
_TASK_FILE = DATA_DIR / "extension_install_tasks.json"
_AUDIT_FILE = DATA_DIR / "extension_audit.jsonl"
_AQUA_REGISTRY_TREE_URL = "https://api.github.com/repos/aquaproj/aqua-registry/git/trees/main?recursive=1"

_COMMON_PATHS = {
    "darwin": ("/opt/homebrew/bin", "/usr/local/bin", "/Library/TeX/texbin"),
    "linux": ("/usr/local/bin", "/usr/bin", "/snap/bin", str(Path.home() / ".local/bin")),
    "win32": (),
}

_AGENT_EXTENSION_ENV_KEYS = (
    "MISE_DATA_DIR",
    "MISE_CONFIG_DIR",
    "MISE_CACHE_DIR",
    "MISE_STATE_DIR",
    "MISE_GLOBAL_CONFIG_FILE",
    "MISE_GLOBAL_CONFIG_ROOT",
    "MISE_CEILING_PATHS",
    "MISE_OVERRIDE_CONFIG_FILENAMES",
    "MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES",
    "UV_PYTHON_INSTALL_DIR",
    "UV_PYTHON_BIN_DIR",
    "UV_CACHE_DIR",
    "UV_PYTHON_DOWNLOADS",
    "NPM_CONFIG_REGISTRY",
    "UV_INDEX_URL",
    "PIP_INDEX_URL",
    "MISE_GPG_VERIFY",
    "MISE_SLSA",
    "MISE_AQUA_SLSA",
    "MISE_AQUA_COSIGN",
    "MISE_AQUA_MINISIGN",
    "MISE_AQUA_GITHUB_ATTESTATIONS",
    "MISE_DISABLE_TOOLS",
)

# Runtime search exposes only mise's built-in language and SDK plugins. This
# keeps the much larger CLI registry out of the Runtime Environments category
# and makes install validation independent of client-provided metadata.
_ALLOWED_MISE_CORE_TOOLCHAINS = frozenset({
    "bun", "deno", "dotnet", "elixir", "erlang", "go", "java", "node",
    "python", "ruby", "rust", "swift", "zig",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_secrets(value: Any) -> Any:
    """Return a JSON-safe copy without credentials in tasks or audit logs."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "", str(key).casefold())
            if normalized in {"env", "headers"} and isinstance(item, dict):
                result[str(key)] = {str(child_key): "[redacted]" for child_key in item}
            elif any(marker in normalized for marker in ("token", "secret", "password", "authorization", "privatekey", "apikey", "cookie", "credential")):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _redact_secrets(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_version_text(value: str) -> str:
    line = next((line.strip() for line in str(value or "").splitlines() if line.strip()), "")
    return line[:240]


def _command_version(path: Path, args: tuple[str, ...], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run([str(path), *args], capture_output=True, text=True, timeout=timeout, env={**os.environ, "NO_COLOR": "1"})
    except (OSError, subprocess.SubprocessError):
        return ""
    return _safe_version_text((result.stdout or "") + "\n" + (result.stderr or ""))


def _is_under(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        base = root.resolve()
        return resolved == base or base in resolved.parents
    except OSError:
        return False


def _extract_verified_tar(archive: Path, destination: Path) -> None:
    """Extract a tar archive while allowing only links confined to its root."""
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError("Archive contains an unsafe path")
            if member.issym() or member.islnk():
                link = Path(member.linkname)
                if link.is_absolute():
                    raise RuntimeError("Archive contains an unsafe link")
                link_target = ((target.parent if member.issym() else destination) / link).resolve()
                if link_target != root and root not in link_target.parents:
                    raise RuntimeError("Archive contains an unsafe link")
        try:
            # Python's data filter performs an additional extraction-time
            # check, including paths that traverse a symlink created by an
            # earlier archive member. TinyTeX legitimately contains internal
            # executable symlinks, so rejecting every link breaks its official
            # macOS and Linux archives.
            handle.extractall(destination, filter="data")
        except tarfile.FilterError as exc:
            raise RuntimeError("Archive failed the safe extraction policy") from exc


def _which_candidates(names: tuple[str, ...]) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for name in names:
        found = shutil.which(name)
        if found:
            candidate = Path(found)
            key = str(candidate.resolve())
            if key not in seen:
                candidates.append(candidate)
                seen.add(key)
        for directory in _COMMON_PATHS.get(os.sys.platform, ()):
            candidate = Path(directory) / (name + (".exe" if os.name == "nt" and not name.endswith(".exe") else ""))
            if candidate.is_file():
                key = str(candidate.resolve())
                if key not in seen:
                    candidates.append(candidate)
                    seen.add(key)
    return candidates


def _platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    system = "windows" if os.name == "nt" else "macos" if os.sys.platform == "darwin" else "linux"
    return f"{system}-{arch}"


def _bundled_binary(name: str) -> Path | None:
    executable = name + (".exe" if os.name == "nt" else "")
    candidates = (
        INSTALL_RESOURCES_DIR / "runtime-tools" / _platform_key() / executable,
        INSTALL_RESOURCES_DIR / "runtime-tools" / executable,
        _BIN_DIR / executable,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Source checkouts use the developer's matching binary while packaged
    # builds always provide runtime-tools through Electron extraResources.
    found = shutil.which(name)
    return Path(found) if found else None


def extension_environment() -> dict[str, str]:
    """Return the isolated manager environment used by installers and shells."""
    _ROOT.mkdir(parents=True, exist_ok=True)
    _MISE_CONFIG.mkdir(parents=True, exist_ok=True)
    _MISE_CACHE.mkdir(parents=True, exist_ok=True)
    _UV_PYTHON_DIR.mkdir(parents=True, exist_ok=True)
    _UV_BIN_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "MISE_DATA_DIR": str(_MISE_DATA),
        "MISE_CONFIG_DIR": str(_MISE_CONFIG),
        "MISE_CACHE_DIR": str(_MISE_CACHE),
        "MISE_STATE_DIR": str(_ROOT / "mise-state"),
        "MISE_GLOBAL_CONFIG_FILE": str(_MISE_CONFIG / "config.toml"),
        "MISE_GLOBAL_CONFIG_ROOT": str(_ROOT),
        "MISE_CEILING_PATHS": str(Path.cwd().resolve()),
        "MISE_OVERRIDE_CONFIG_FILENAMES": "cyrene-managed.mise.toml",
        "MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES": "none",
        "MISE_YES": "1",
        "UV_PYTHON_INSTALL_DIR": str(_UV_PYTHON_DIR),
        "UV_PYTHON_BIN_DIR": str(_UV_BIN_DIR),
        "UV_CACHE_DIR": str(CACHE_DIR / "extensions" / "uv"),
        "UV_PYTHON_DOWNLOADS": "manual",
    })
    sources = source_settings(include_secret=True)
    if sources.get("npm_registry"):
        env["NPM_CONFIG_REGISTRY"] = str(sources["npm_registry"])
    if sources.get("pip_index_url"):
        env["UV_INDEX_URL"] = str(sources["pip_index_url"])
        env["PIP_INDEX_URL"] = str(sources["pip_index_url"])
    if sources.get("github_token"):
        env["GITHUB_TOKEN"] = str(sources["github_token"])
    verify = "true" if sources.get("verify_signatures", True) else "false"
    env.update({
        "MISE_GPG_VERIFY": verify,
        "MISE_SLSA": verify,
        "MISE_AQUA_SLSA": verify,
        "MISE_AQUA_COSIGN": verify,
        "MISE_AQUA_MINISIGN": verify,
        "MISE_AQUA_GITHUB_ATTESTATIONS": verify,
    })
    return env


def agent_extension_paths() -> list[str]:
    """Enabled paths appended to Agent Shell PATH; system commands win."""
    result = []
    candidates = []
    bundled_uv = _bundled_binary("uv")
    if bundled_uv and extension_is_enabled("toolchain", "uv"):
        candidates.append(bundled_uv.parent)
    managed_toolchains = _managed_extension_records("extension_toolchains")
    if any(str(record.get("id") or "") == "python" for record in managed_toolchains) and extension_is_enabled("toolchain", "python"):
        candidates.append(_UV_BIN_DIR)
    managed_mise_enabled = any(
        extension_is_enabled(kind, str(record.get("id") or ""))
        for kind, records in (("cli", _managed_extension_records("extension_clis")), ("toolchain", managed_toolchains))
        for record in records
        if str((record.get("source") or {}).get("type") or "") == "mise"
    )
    if managed_mise_enabled:
        candidates.append(_MISE_DATA / "shims")
    managed_tex = any(str(record.get("id") or "") == "tex" for record in managed_toolchains)
    if managed_tex and extension_is_enabled("toolchain", "tex"):
        candidates.append(_TEX_DIR / "bin")
    for candidate in candidates:
        if candidate.is_dir():
            result.append(str(candidate))
    # TinyTeX archives contain an architecture-specific bin directory.
    if managed_tex and extension_is_enabled("toolchain", "tex") and _TEX_DIR.is_dir():
        for tlmgr in sorted(_TEX_DIR.rglob("tlmgr" + (".bat" if os.name == "nt" else ""))):
            parent = str(tlmgr.parent)
            if parent not in result:
                result.append(parent)
                break
    return result


def agent_process_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Build the environment shared by Cyrene-launched Agent processes.

    Installer credentials deliberately stay in ``extension_environment`` and
    are not copied here. Managed command paths are appended so an existing
    system executable always keeps precedence over the Cyrene fallback.
    """
    env = dict(os.environ if base is None else base)
    try:
        managed_env = extension_environment()
        disabled_mise_tools = []
        for kind, setting_key in (("cli", "extension_clis"), ("toolchain", "extension_toolchains")):
            for record in _managed_extension_records(setting_key):
                extension_id = str(record.get("id") or "")
                source = record.get("source") or {}
                spec = record.get("spec") or {}
                if (
                    extension_id
                    and str(source.get("type") or "") == "mise"
                    and not extension_is_enabled(kind, extension_id)
                ):
                    # Custom mise backends are keyed by their full ref in the
                    # generated config (for example
                    # ``github:BurntSushi/ripgrep``), while core tools use a
                    # short name such as ``node``.  MISE_DISABLE_TOOLS must
                    # receive that same key or the shared shim directory can
                    # accidentally expose a disabled custom tool.
                    tool = str(source.get("ref") or spec.get("tool") or extension_id).strip()
                    if tool:
                        disabled_mise_tools.append(tool)
        if disabled_mise_tools:
            managed_env["MISE_DISABLE_TOOLS"] = ",".join(dict.fromkeys(disabled_mise_tools))
        for key in _AGENT_EXTENSION_ENV_KEYS:
            if managed_env.get(key):
                env[key] = managed_env[key]
        fallbacks = [path for path in agent_extension_paths() if path]
        if fallbacks:
            current_path = env.get("PATH", "")
            env["PATH"] = (
                os.pathsep.join([current_path, *fallbacks])
                if current_path
                else os.pathsep.join(fallbacks)
            )
    except Exception:
        # A damaged optional extension must never prevent the Agent from
        # launching a normal process with its original environment.
        logger.exception("Unable to prepare the Cyrene extension environment")
    # Electron development launches can inject this; nvm treats it as an
    # incompatible override and prints a warning in every child process.
    env.pop("npm_config_prefix", None)
    env.pop("NPM_CONFIG_PREFIX", None)
    return env


def _extension_enabled_settings() -> dict[str, bool]:
    value = get_setting("extension_enabled", {})
    if not isinstance(value, dict):
        return {}
    return {str(key): bool(enabled) for key, enabled in value.items()}


def extension_is_enabled(kind: str, extension_id: str) -> bool:
    """Return the persisted Cyrene activation state; installed items default on."""
    key = f"{str(kind).strip().lower()}:{str(extension_id).strip()}"
    return _extension_enabled_settings().get(key, True)


def _save_extension_enabled(kind: str, extension_id: str, enabled: bool) -> None:
    values = _extension_enabled_settings()
    values[f"{kind}:{extension_id}"] = bool(enabled)
    set_setting("extension_enabled", values)


def _forget_extension_enabled(kind: str, extension_id: str) -> None:
    values = _extension_enabled_settings()
    if values.pop(f"{kind}:{extension_id}", None) is not None:
        set_setting("extension_enabled", values)


def _managed_extension_records(key: str) -> list[dict[str, Any]]:
    value = get_setting(key, [])
    return value if isinstance(value, list) else []


def source_settings(*, include_secret: bool = False) -> dict[str, Any]:
    saved = get_setting("extension_sources", {})
    result = {**DEFAULT_SOURCE_SETTINGS, **(saved if isinstance(saved, dict) else {})}
    if not include_secret and result.get("github_token"):
        result["github_token"] = "••••••••" + str(result["github_token"])[-4:]
        result["github_token_configured"] = True
    else:
        result["github_token_configured"] = bool(result.get("github_token"))
    return result


def update_source_settings(changes: dict[str, Any]) -> dict[str, Any]:
    current = source_settings(include_secret=True)
    if changes.get("clear_github_token") is True:
        current["github_token"] = ""
    allowed = set(DEFAULT_SOURCE_SETTINGS)
    for key, value in changes.items():
        if key not in allowed:
            continue
        if key == "github_token" and (not value or str(value).startswith("••••")):
            continue
        if key in {"auto_mirror", "verify_signatures"}:
            current[key] = bool(value)
            continue
        text = str(value or "").strip()
        if key == "network_mode" and text not in {"auto", "direct", "china"}:
            raise ValueError("network_mode must be auto, direct, or china")
        if key.endswith("_url") or key in {"github_mirror", "npm_registry", "skill_catalog_url"}:
            if text:
                parsed = urllib.parse.urlparse(text)
                local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                if parsed.scheme != "https" and not local_http:
                    raise ValueError(f"{key} must use HTTPS (local loopback HTTP is allowed)")
                if parsed.username or parsed.password:
                    raise ValueError(f"{key} must not embed credentials")
        current[key] = text
    set_setting("extension_sources", {key: current.get(key, value) for key, value in DEFAULT_SOURCE_SETTINGS.items()})
    _audit("user", "source.update", "sources", {"changed": sorted(key for key in changes if key != "github_token"), "github_token_changed": "github_token" in changes})
    return source_settings()


def _audit(actor: str, action: str, target: str, detail: dict[str, Any], result: str = "ok") -> None:
    _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = _redact_secrets(detail)
    record = {"at": _now(), "actor": actor, "action": action, "target": target, "result": result, "detail": clean}
    with _AUDIT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def audit_records(limit: int = 200) -> list[dict[str, Any]]:
    if not _AUDIT_FILE.is_file():
        return []
    lines = _AUDIT_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-max(1, min(limit, 1000)):]
    result = []
    for line in reversed(lines):
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


class InstallTaskStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._async_tasks: dict[str, asyncio.Task[Any]] = {}
        self._manager_locks = {"uv": asyncio.Lock(), "mise": asyncio.Lock(), "tinytex": asyncio.Lock(), "skill": asyncio.Lock()}
        self._load()

    def _load(self) -> None:
        if _STAGING_DIR.is_dir():
            for child in _STAGING_DIR.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        try:
            data = json.loads(_TASK_FILE.read_text(encoding="utf-8")) if _TASK_FILE.is_file() else {}
        except Exception:
            data = {}
        for task_id, task in (data if isinstance(data, dict) else {}).items():
            if task.get("status") in {"queued", "running", "cancelling"}:
                task["status"] = "interrupted"
                task["error"] = "Cyrene stopped before the installation completed. Retry the installation."
                task["finished_at"] = _now()
            self._tasks[str(task_id)] = task
        self._persist()

    def _persist(self) -> None:
        _TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
        pending = _TASK_FILE.with_suffix(".tmp")
        pending.write_text(json.dumps(self._tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(pending, _TASK_FILE)

    def create(self, *, kind: str, extension_id: str, action: str, actor: str, request: dict[str, Any]) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        task = {
            "id": task_id, "kind": kind, "extension_id": extension_id, "action": action,
            "actor": actor, "request": _redact_secrets(request), "status": "queued", "progress": 0,
            "message": "Waiting to start", "created_at": _now(), "started_at": "", "finished_at": "", "error": "", "result": None,
        }
        with self._lock:
            self._tasks[task_id] = task
            self._persist()
        return dict(task)

    def update(self, task_id: str, **changes: Any) -> None:
        with self._lock:
            if task_id not in self._tasks:
                return
            # Task state is durable and user-visible.  Apply the same recursive
            # secret filtering to worker results/errors as to the original
            # request so a connector can never persist credentials by returning
            # its configuration as part of the install result.
            self._tasks[task_id].update(_redact_secrets(changes))
            self._persist()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted((dict(task) for task in self._tasks.values()), key=lambda item: item.get("created_at", ""), reverse=True)

    def start(self, task: dict[str, Any], manager: str, worker: Any) -> None:
        async def run() -> None:
            task_id = task["id"]
            self.update(task_id, status="running", started_at=_now(), progress=2, message="Preparing installation")
            try:
                async with self._manager_locks.setdefault(manager, asyncio.Lock()):
                    result = await worker(task_id)
                current = self.get(task_id) or {}
                if current.get("status") == "cancelling":
                    self.update(task_id, status="cancelled", finished_at=_now(), message="Cancelled")
                else:
                    self.update(task_id, status="completed", finished_at=_now(), progress=100, message="Installed", result=result)
            except asyncio.CancelledError:
                self.update(task_id, status="cancelled", finished_at=_now(), message="Cancelled")
            except Exception as exc:
                logger.exception("Extension install task failed: %s", task_id)
                self.update(task_id, status="failed", finished_at=_now(), error=str(exc), message="Installation failed")
                _audit(task.get("actor", "user"), f"{task.get('action')}.finish", task.get("extension_id", ""), {"error": str(exc)}, "failed")
            finally:
                self._async_tasks.pop(task_id, None)
                staging = _STAGING_DIR / task_id
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)

        async_task = asyncio.create_task(run())
        self._async_tasks[task["id"]] = async_task

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task or task.get("status") not in {"queued", "running"}:
            return False
        self.update(task_id, status="cancelling", message="Cancelling")
        running = self._async_tasks.get(task_id)
        if running:
            running.cancel()
        return True


_TASKS: InstallTaskStore | None = None


def _get_task_store() -> InstallTaskStore:
    global _TASKS
    if _TASKS is None:
        _TASKS = InstallTaskStore()
    return _TASKS


class ExtensionService:
    def __init__(self) -> None:
        self.tasks = _get_task_store()

    def _system_observation(self, extension_id: str, spec: dict[str, Any]) -> dict[str, Any] | None:
        bindings = get_setting("extension_system_bindings", {})
        bound_path = str(bindings.get(extension_id) or "") if isinstance(bindings, dict) else ""
        if bound_path:
            candidate = Path(bound_path).expanduser()
            version = _command_version(candidate, tuple(spec.get("version_args") or ("--version",))) if candidate.is_file() else ""
            if version:
                return {"path": str(candidate.resolve()), "version": version, "ownership": "system", "observed_state": "installed", "health": "healthy", "source": {"type": "system", "binding": "manual"}, "manual_binding": True}
        for candidate in _which_candidates(tuple(spec.get("executables") or ())):
            if _is_under(candidate, _ROOT):
                continue
            version = _command_version(candidate, tuple(spec.get("version_args") or ("--version",)))
            if version:
                return {"path": str(candidate.resolve()), "version": version, "ownership": "system", "observed_state": "installed", "health": "healthy", "source": {"type": "system", "binding": "detected"}}
        return None

    def _managed_records(self, key: str) -> list[dict[str, Any]]:
        return _managed_extension_records(key)

    def _extension_card(self, extension_id: str, spec: dict[str, Any], managed: dict[str, Any] | None = None) -> dict[str, Any]:
        system = self._system_observation(extension_id, spec)
        observed = system or managed
        ownership = str((observed or {}).get("ownership") or "none")
        state = str((observed or {}).get("observed_state") or "missing")
        enabled = extension_is_enabled(str(spec.get("kind") or ""), extension_id) if observed else False
        capabilities = ["install"] if state == "missing" else []
        if ownership == "cyrene":
            capabilities = ["install", "uninstall", "enable", "disable"]
            if spec.get("kind") == "toolchain" and spec.get("manager") == "mise":
                capabilities.append("set_default")
        elif system and managed:
            capabilities = ["uninstall_managed", "enable", "disable"]
        elif system:
            capabilities = ["enable", "disable"]
        # Manual executable binding currently has deterministic validation only
        # for declared catalog entries. Dynamic mise results such as fd or RTK
        # must not advertise an action that the binding endpoint will reject.
        if not system and (extension_id in TOOLCHAINS or extension_id in CURATED_CLIS):
            capabilities.append("bind_system")
            if bool((observed or {}).get("manual_binding")):
                capabilities.append("unbind_system")
        return {
            "key": f"{spec.get('kind')}:{extension_id}", "id": extension_id, "kind": spec.get("kind"),
            "name": spec.get("name"), "description": spec.get("description", ""), "ownership": ownership,
            "desired_state": ("enabled" if enabled else "disabled") if observed else "missing", "observed_state": state,
            "version": str((observed or {}).get("version") or ""), "path": str((observed or {}).get("path") or ""),
            "health": str((observed or {}).get("health") or ("missing" if not observed else "healthy")),
            "source": (observed or {}).get("source", {}), "capabilities": capabilities,
            "recommended": bool(spec.get("recommended")), "recommended_version": spec.get("recommended_version") or spec.get("version") or "",
            "versions": list((managed or {}).get("versions") or ([managed.get("version")] if managed and managed.get("version") else [])),
            "default_version": str((managed or {}).get("default_version") or (managed or {}).get("version") or ""),
            "manual_binding": bool((observed or {}).get("manual_binding")),
            "managed_available": bool(managed),
            "managed_path": str((managed or {}).get("path") or ""),
            "managed_version": str((managed or {}).get("version") or ""),
            "risk": spec.get("risk", "low" if spec.get("kind") == "toolchain" else "medium"),
            "enabled": enabled,
        }

    def bind_system_executable(self, extension_id: str, path: str) -> dict[str, Any]:
        extension_id = str(extension_id or "").strip()
        spec = TOOLCHAINS.get(extension_id) or CURATED_CLIS.get(extension_id)
        if not spec:
            raise ValueError("Only supported CLI and toolchains can be manually bound")
        candidate = Path(str(path or "")).expanduser()
        if not candidate.is_file():
            raise ValueError("Executable path does not exist")
        if candidate.is_symlink():
            candidate = candidate.resolve()
        if os.name != "nt" and not os.access(candidate, os.X_OK):
            raise ValueError("Selected path is not executable")
        version = _command_version(candidate, tuple(spec.get("version_args") or ("--version",)))
        if not version:
            raise ValueError("The selected executable did not return a supported version")
        bindings = get_setting("extension_system_bindings", {})
        bindings = dict(bindings) if isinstance(bindings, dict) else {}
        bindings[extension_id] = str(candidate.resolve())
        set_setting("extension_system_bindings", bindings)
        _audit("user", "system.bind", f"{spec.get('kind')}:{extension_id}", {"path": str(candidate.resolve()), "version": version})
        return {"ok": True, "path": str(candidate.resolve()), "version": version}

    def unbind_system_executable(self, extension_id: str) -> dict[str, Any]:
        bindings = get_setting("extension_system_bindings", {})
        bindings = dict(bindings) if isinstance(bindings, dict) else {}
        existed = extension_id in bindings
        bindings.pop(extension_id, None)
        set_setting("extension_system_bindings", bindings)
        if existed:
            _audit("user", "system.unbind", extension_id, {})
        return {"ok": existed}

    async def list_versions(self, kind: str, extension_id: str) -> dict[str, Any]:
        if kind == "toolchain" and extension_id == "python":
            return {"versions": [TOOLCHAINS["python"]["recommended_version"]], "recommended": TOOLCHAINS["python"]["recommended_version"]}
        if kind == "toolchain" and extension_id == "tex":
            return {"versions": ["tinytex", "texlive-full"], "recommended": "tinytex"}
        spec = TOOLCHAINS.get(extension_id) if kind == "toolchain" else CURATED_CLIS.get(extension_id)
        if not spec:
            raise ValueError("Unknown extension")
        mise = _bundled_binary("mise")
        if not mise:
            raise RuntimeError("Bundled mise is missing")
        ref = str(spec.get("ref") or spec.get("tool") or extension_id)
        proc = await asyncio.create_subprocess_exec(str(mise), "ls-remote", ref, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=extension_environment())
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-1000:] or "Unable to list versions")
        versions = [line.strip() for line in stdout.decode(errors="replace").splitlines() if line.strip()]
        versions = list(reversed(versions[-100:]))
        return {"versions": versions, "recommended": str(spec.get("version") or "latest")}

    async def set_default_version(self, extension_id: str, version: str, *, actor: str = "user") -> dict[str, Any]:
        records = self._managed_records("extension_toolchains")
        record = next((item for item in records if str(item.get("id")) == extension_id), None)
        if not record or version not in record.get("versions", []):
            raise ValueError("The requested Cyrene-managed version is not installed")
        if extension_id == "python":
            # uv's --default links point at the installed interpreter. Re-run a
            # no-download install to atomically update those links.
            uv = _bundled_binary("uv")
            if not uv:
                raise RuntimeError("Bundled uv is missing")
            proc = await asyncio.create_subprocess_exec(str(uv), "python", "install", version, "--default", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=extension_environment())
        else:
            mise = _bundled_binary("mise")
            if not mise:
                raise RuntimeError("Bundled mise is missing")
            ref = str((record.get("source") or {}).get("ref") or extension_id)
            proc = await asyncio.create_subprocess_exec(str(mise), "use", "--global", f"{ref}@{version}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=extension_environment())
        _stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-1000:])
        record["default_version"] = version
        set_setting("extension_toolchains", [item for item in records if str(item.get("id")) != extension_id] + [record])
        _audit(actor, "default.set", f"toolchain:{extension_id}", {"version": version})
        return {"ok": True, "version": version}

    def list_extensions(self) -> dict[str, Any]:
        skill_cards = []
        for skill in build_skills():
            skill_cards.append({
                "key": f"skill:{skill.get('id')}", "id": skill.get("id"), "kind": "skill", "name": skill.get("name"),
                "description": skill.get("desc", ""), "ownership": "cyrene", "desired_state": "enabled" if skill.get("enabled", True) else "disabled",
                "observed_state": "installed", "version": skill.get("version", "snapshot"), "path": skill.get("stored_path", ""),
                "health": "healthy", "source": {"kind": skill.get("source_kind"), "path": skill.get("source_path"), "url": skill.get("source_url")},
                "capabilities": ["enable", "disable", "uninstall", "inspect"], "size_bytes": skill.get("size_bytes", 0),
                "enabled": skill.get("enabled", True), "files": skill.get("files", []),
                "installed_at": skill.get("installed_at", ""), "content_hash": skill.get("content_hash", ""),
                "entrypoint_name": skill.get("entrypoint_name", "SKILL.md"), "preview": skill.get("preview", ""),
            })

        from cyrene.tooling.backends.mcp_manager import get_manager, get_mcp_servers, redact_mcp_servers
        statuses = {str(item.get("name")): item for item in get_manager().get_server_status()}
        mcp_cards = []
        raw_mcp_configs = get_mcp_servers()
        safe_mcp_configs = redact_mcp_servers(raw_mcp_configs)
        for config in safe_mcp_configs:
            name = str(config.get("name") or "")
            live = statuses.get(name, {})
            status = str(live.get("status") or "disconnected")
            mcp_cards.append({
                "key": f"mcp:{name}", "id": name, "kind": "mcp", "name": name, "description": str(config.get("description") or "MCP server"),
                "ownership": "cyrene", "desired_state": "enabled" if config.get("enabled", True) else "disabled",
                "observed_state": "installed", "version": str(config.get("version") or ""),
                "path": str(config.get("command") or config.get("url") or ""), "health": "healthy" if status == "connected" else status,
                "source": config.get("source", {"transport": config.get("transport", "stdio")}),
                "capabilities": ["enable", "disable", "remove", "test"], "enabled": config.get("enabled", True),
                "connection_status": status, "tool_count": int(live.get("tool_count") or 0), "config": config,
            })

        cli_records = {str(item.get("id")): item for item in self._managed_records("extension_clis")}
        cli_ids = list(dict.fromkeys([*CURATED_CLIS, *cli_records]))
        cli_cards = []
        for cli_id in cli_ids:
            spec = dict(CURATED_CLIS.get(cli_id) or cli_records[cli_id].get("spec") or {})
            spec.setdefault("name", cli_id)
            spec.setdefault("kind", "cli")
            cli_cards.append(self._extension_card(cli_id, spec, cli_records.get(cli_id)))

        toolchain_records = {str(item.get("id")): item for item in self._managed_records("extension_toolchains")}
        toolchain_ids = list(dict.fromkeys([*TOOLCHAINS, *toolchain_records]))
        toolchain_cards = []
        for tool_id in toolchain_ids:
            spec = dict(TOOLCHAINS.get(tool_id) or toolchain_records[tool_id].get("spec") or {})
            spec.setdefault("name", tool_id)
            spec.setdefault("kind", "toolchain")
            toolchain_cards.append(self._extension_card(tool_id, spec, toolchain_records.get(tool_id)))

        uv_path = _bundled_binary("uv")
        uv_version = _command_version(uv_path, ("--version",)) if uv_path else ""
        uv_enabled = extension_is_enabled("toolchain", "uv") if uv_version else False
        uv_card = {
            "key": "internal:uv", "id": "uv", "kind": "toolchain", "name": "uv", "description": "Built-in Python installer and package manager.",
            "ownership": "builtin", "desired_state": "enabled" if uv_enabled else "disabled", "observed_state": "installed" if uv_version else "unhealthy",
            "version": uv_version or UV_VERSION, "path": str(uv_path or ""), "health": "healthy" if uv_version else "missing_bundle",
            "source": {"type": "bundled", "version": UV_VERSION}, "capabilities": ["enable", "disable"] if uv_version else [], "recommended": True, "recommended_version": UV_VERSION,
            "enabled": uv_enabled,
        }
        recommended_lookup = {item["id"]: item for item in [*toolchain_cards, *cli_cards, uv_card]}
        recommended = [recommended_lookup[key] for key in RECOMMENDED_ORDER if key in recommended_lookup]
        return {
            "recommended": recommended, "skills": skill_cards, "mcp": mcp_cards, "cli": cli_cards, "toolchains": toolchain_cards,
            "infrastructure": {"uv": uv_card, "mise": self.infrastructure_status("mise")}, "tasks": self.tasks.list(),
            "python_prompt_required": next((item["observed_state"] == "missing" for item in toolchain_cards if item["id"] == "python"), False),
        }

    async def set_mcp_enabled(self, extension_id: str, enabled: bool, *, actor: str = "user") -> dict[str, Any]:
        from cyrene.tooling.backends.mcp_manager import get_mcp_servers, restart_mcp, save_mcp_servers

        servers = get_mcp_servers()
        found = False
        for server in servers:
            if str(server.get("name") or "") == extension_id:
                server["enabled"] = bool(enabled)
                found = True
                break
        if not found:
            raise ValueError("MCP server not found")
        save_mcp_servers(servers)
        await restart_mcp()
        _audit(actor, "mcp.enable" if enabled else "mcp.disable", f"mcp:{extension_id}", {})
        return {"ok": True, "enabled": bool(enabled)}

    async def set_extension_enabled(self, kind: str, extension_id: str, enabled: bool, *, actor: str = "user") -> dict[str, Any]:
        """Activate or deactivate an installed extension without uninstalling it."""
        kind = str(kind or "").strip().lower()
        extension_id = str(extension_id or "").strip()
        if kind == "skill":
            if not set_skill_enabled(extension_id, enabled):
                raise ValueError("Skill not found")
            action = "skill.enable" if enabled else "skill.disable"
        elif kind == "mcp":
            return await self.set_mcp_enabled(extension_id, enabled, actor=actor)
        elif kind in {"cli", "toolchain"}:
            if kind == "toolchain" and extension_id == "uv":
                if not _bundled_binary("uv"):
                    raise ValueError("Installed extension not found")
                _save_extension_enabled(kind, extension_id, enabled)
                action = "extension.enable" if enabled else "extension.disable"
                _audit(actor, action, f"{kind}:{extension_id}", {})
                return {"ok": True, "enabled": bool(enabled)}
            setting_key = "extension_clis" if kind == "cli" else "extension_toolchains"
            records = self._managed_records(setting_key)
            record = next((item for item in records if str(item.get("id")) == extension_id), None)
            spec = dict((CURATED_CLIS if kind == "cli" else TOOLCHAINS).get(extension_id) or (record or {}).get("spec") or {})
            if not spec or not (record or self._system_observation(extension_id, spec)):
                raise ValueError("Installed extension not found")
            _save_extension_enabled(kind, extension_id, enabled)
            action = "extension.enable" if enabled else "extension.disable"
        else:
            raise ValueError("Unsupported extension kind")
        _audit(actor, action, f"{kind}:{extension_id}", {})
        return {"ok": True, "enabled": bool(enabled)}

    def infrastructure_status(self, name: str) -> dict[str, Any]:
        path = _bundled_binary(name)
        version = _command_version(path, ("--version",)) if path else ""
        expected = UV_VERSION if name == "uv" else MISE_VERSION
        return {"name": name, "path": str(path or ""), "version": version or expected, "healthy": bool(version), "expected_version": expected}

    async def search(self, kind: str, query: str, *, advanced: bool = False, cursor: str = "") -> dict[str, Any]:
        kind = str(kind or "").strip().lower()
        query = str(query or "").strip()
        if kind == "cli":
            return await self._search_cli(query, advanced=advanced)
        if kind == "mcp":
            return await self._search_mcp(query, cursor=cursor)
        if kind == "skill":
            return await self._search_skill_repositories(query)
        if kind == "toolchain":
            results: list[dict[str, Any]] = []
            for tool_id, spec in TOOLCHAINS.items():
                if query.casefold() in f"{tool_id} {spec.get('name')} {spec.get('description')}".casefold():
                    item = {"id": tool_id, **spec, "source": "cyrene-catalog", "verified": True}
                    if spec.get("manager") == "mise":
                        item["ref"] = str(spec.get("ref") or spec.get("tool") or tool_id)
                        item["backend"] = "core"
                    results.append(item)
            mise = _bundled_binary("mise")
            if mise:
                command = [str(mise), "registry", "--json"]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=extension_environment(),
                )
                stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode == 0:
                    try:
                        registry = json.loads(stdout.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        registry = []
                    entries = registry if isinstance(registry, list) else []
                    known_ids = {str(item.get("id") or "").casefold() for item in results}
                    known_refs = {
                        str(item.get("ref") or item.get("source") or "").casefold()
                        for item in results
                    }
                    for value in entries:
                        if not isinstance(value, dict):
                            continue
                        name = str(value.get("short") or "").strip()
                        if not name or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
                            continue
                        core_refs = [
                            str(ref) for ref in value.get("backends", [])
                            if str(ref).startswith("core:")
                            and re.fullmatch(r"core:[a-z0-9][a-z0-9._-]*", str(ref))
                        ]
                        if not core_refs or query.casefold() not in (
                            f"{name} {value.get('description') or ''} {' '.join(core_refs)}"
                        ).casefold():
                            continue
                        # Prefer Cyrene's catalog declaration for known
                        # runtimes. It carries platform-specific detection and
                        # version selectors that the generic registry does not.
                        ref = core_refs[0]
                        item_id = ref.split(":", 1)[1]
                        if item_id not in _ALLOWED_MISE_CORE_TOOLCHAINS:
                            continue
                        if item_id.casefold() in known_ids or ref.casefold() in known_refs:
                            continue
                        results.append({
                            "id": item_id,
                            "name": name,
                            "kind": "toolchain",
                            "description": str(value.get("description") or ref),
                            "manager": "mise",
                            "tool": item_id,
                            "executables": [item_id],
                            "version_args": ["--version"],
                            "ref": ref,
                            "version": "latest",
                            "source": ref,
                            "backend": "core",
                            "risk": "low",
                            "verified": True,
                        })
                        known_ids.add(item_id.casefold())
                        known_refs.add(ref.casefold())
            return {"results": results[:100], "source": "cyrene-catalog+mise-core", "next_cursor": ""}
        raise ValueError(f"unsupported extension kind: {kind}")

    async def _search_cli(self, query: str, *, advanced: bool) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for cli_id, spec in CURATED_CLIS.items():
            if query.casefold() in f"{cli_id} {spec.get('name')} {spec.get('description')}".casefold():
                results.append({"id": cli_id, **spec, "source": spec.get("ref"), "verified": True})
        mise = _bundled_binary("mise")
        if mise:
            command = [str(mise), "registry", "--json"]
            proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=extension_environment())
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                try:
                    data = json.loads(stdout.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    data = {}
                if isinstance(data, list):
                    iterable = ((str(item.get("short") or ""), item) for item in data if isinstance(item, dict))
                elif isinstance(data, dict):
                    iterable = data.items()
                else:
                    iterable = ()
                for name, value in iterable:
                    refs = value.get("backends", []) if isinstance(value, dict) else value if isinstance(value, list) else [value]
                    description = str(value.get("description") or "") if isinstance(value, dict) else ""
                    for ref in refs:
                        ref = str(ref or "")
                        backend = ref.split(":", 1)[0] if ":" in ref else "core"
                        if backend not in ALLOWED_MISE_BACKENDS or (backend in HIGH_RISK_MISE_BACKENDS and not advanced):
                            continue
                        if query.casefold() not in f"{name} {ref}".casefold():
                            continue
                        item_id = re.sub(r"[^a-z0-9._-]+", "-", str(name).casefold()).strip("-")
                        if any(item.get("id") == item_id and item.get("source") == ref for item in results):
                            continue
                        results.append({
                            "id": item_id, "name": name, "kind": "cli", "description": description or ref, "manager": "mise", "tool": name,
                            "ref": ref, "version": "latest", "source": ref, "backend": backend,
                            "risk": "high" if backend in HIGH_RISK_MISE_BACKENDS else "medium", "verified": backend not in HIGH_RISK_MISE_BACKENDS,
                        })
                        if len(results) >= 100:
                            break
        # mise's shorthand registry is intentionally smaller than the Aqua
        # Standard Registry it can install from. Search Aqua as a discovery
        # fallback so packages do not need a Cyrene-specific catalog entry.
        if query and len(results) < 100:
            try:
                aqua_results = await self._search_aqua_registry(query)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.warning("Aqua Registry search failed: %s", exc)
                aqua_results = []
            known_refs = {str(item.get("ref") or item.get("source") or "") for item in results}
            for item in aqua_results:
                if str(item.get("ref") or "") not in known_refs:
                    results.append(item)
                    known_refs.add(str(item.get("ref") or ""))
                if len(results) >= 100:
                    break
        if advanced and query and len(results) < 100:
            ecosystem_searches = (
                self._search_npm_registry(query),
                self._search_pypi_cli(query),
                self._search_rubygems(query),
            )
            ecosystem_results = await asyncio.gather(*ecosystem_searches, return_exceptions=True)
            known_refs = {str(item.get("ref") or item.get("source") or "") for item in results}
            for found in ecosystem_results:
                if isinstance(found, BaseException):
                    logger.warning("Community CLI registry search failed: %s", found)
                    continue
                for item in found:
                    if str(item.get("ref") or "") not in known_refs:
                        results.append(item)
                        known_refs.add(str(item.get("ref") or ""))
                    if len(results) >= 100:
                        break
        return {"results": results[:100], "source": "mise-registry", "next_cursor": ""}

    @staticmethod
    def _ecosystem_cli_result(*, backend: str, name: str, version: str, description: str = "", publisher: str = "") -> dict[str, Any]:
        ref = f"{backend}:{name}"
        return {
            "id": re.sub(r"[^a-z0-9._-]+", "-", name.casefold()).strip("-"),
            "name": name,
            "kind": "cli",
            "description": description or ref,
            "manager": "mise",
            "tool": name,
            "ref": ref,
            "version": version or "latest",
            "source": ref,
            "backend": backend,
            "publisher": publisher,
            "risk": "high",
            "verified": False,
        }

    async def _search_npm_registry(self, query: str) -> list[dict[str, Any]]:
        sources = source_settings(include_secret=True)
        base = str(sources.get("npm_registry") or "https://registry.npmjs.org").rstrip("/")
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(base + "/-/v1/search", params={"text": query, "size": "20"})
            response.raise_for_status()
            payload = response.json()
        results = []
        for wrapper in payload.get("objects", []):
            package = wrapper.get("package") or {} if isinstance(wrapper, dict) else {}
            name = str(package.get("name") or "").strip()
            if not name:
                continue
            publisher = package.get("publisher") or {}
            results.append(self._ecosystem_cli_result(
                backend="npm", name=name, version=str(package.get("version") or "latest"),
                description=str(package.get("description") or ""), publisher=str(publisher.get("username") or ""),
            ))
        return results

    async def _search_pypi_cli(self, query: str) -> list[dict[str, Any]]:
        # PyPI has no supported full-text JSON search API. An exact metadata
        # lookup still provides a reliable path for the usual CLI package name.
        package = query.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package):
            return []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(f"https://pypi.org/pypi/{urllib.parse.quote(package, safe='')}/json")
            if response.status_code == 404:
                return []
            response.raise_for_status()
            info = response.json().get("info") or {}
        name = str(info.get("name") or "").strip()
        if not name:
            return []
        author = str(info.get("author") or info.get("maintainer") or "")
        return [self._ecosystem_cli_result(
            backend="pipx", name=name, version=str(info.get("version") or "latest"),
            description=str(info.get("summary") or ""), publisher=author,
        )]

    async def _search_rubygems(self, query: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get("https://rubygems.org/api/v1/search.json", params={"query": query})
            response.raise_for_status()
            payload = response.json()
        results = []
        for package in payload[:20] if isinstance(payload, list) else []:
            name = str(package.get("name") or "").strip() if isinstance(package, dict) else ""
            if not name:
                continue
            results.append(self._ecosystem_cli_result(
                backend="gem", name=name, version=str(package.get("version") or "latest"),
                description=str(package.get("info") or "").strip(), publisher=str(package.get("authors") or ""),
            ))
        return results

    async def _search_aqua_registry(self, query: str) -> list[dict[str, Any]]:
        """Discover installable CLI packages from Aqua's standard registry."""
        sources = source_settings(include_secret=True)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = str(sources.get("github_token") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            response = await client.get(_AQUA_REGISTRY_TREE_URL)
            response.raise_for_status()
            payload = response.json()
        needle = query.casefold()
        packages: list[str] = []
        for entry in payload.get("tree", []):
            path = str(entry.get("path") or "") if isinstance(entry, dict) else ""
            if not path.startswith("pkgs/") or not path.endswith("/registry.yaml"):
                continue
            package = path[len("pkgs/"):-len("/registry.yaml")]
            if needle not in package.casefold() or package in packages:
                continue
            packages.append(package)
            if len(packages) >= 30:
                break
        return [{
            "id": re.sub(r"[^a-z0-9._-]+", "-", package.rsplit("/", 1)[-1].casefold()).strip("-"),
            "name": package.rsplit("/", 1)[-1],
            "kind": "cli",
            "description": f"Aqua Standard Registry package: {package}",
            "manager": "mise",
            "tool": package.rsplit("/", 1)[-1],
            "ref": f"aqua:{package}",
            "version": "latest",
            "source": f"aqua:{package}",
            "backend": "aqua",
            "publisher": package.split("/", 1)[0],
            "risk": "medium",
            "verified": True,
        } for package in packages]

    async def _search_mcp(self, query: str, *, cursor: str = "") -> dict[str, Any]:
        sources = source_settings(include_secret=True)
        base = str(sources.get("mcp_registry_url") or DEFAULT_SOURCE_SETTINGS["mcp_registry_url"]).rstrip("/")
        params = {"search": query, "version": "latest", "limit": "30"}
        if cursor:
            params["cursor"] = cursor
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(base + "/v0.1/servers", params=params)
            response.raise_for_status()
            payload = response.json()
        results = []
        for wrapper in payload.get("servers", []):
            server = wrapper.get("server", wrapper)
            packages = server.get("packages") or []
            remotes = server.get("remotes") or []
            installable_remotes = [
                remote for remote in remotes
                if remote.get("url")
                and not any(header.get("isRequired") for header in remote.get("headers") or [])
            ]
            installable_packages = [
                package for package in packages
                if str(package.get("registryType") or "").lower() == "npm"
                and str(package.get("version") or "")
                and not any(variable.get("isRequired") for variable in package.get("environmentVariables") or [])
            ]
            results.append({
                "id": server.get("name"), "name": server.get("title") or server.get("name"), "kind": "mcp",
                "description": server.get("description", ""), "version": server.get("version", ""), "repository": server.get("repository") or {},
                "packages": packages, "remotes": remotes, "installable_remotes": installable_remotes,
                "installable_packages": installable_packages,
                "verified": True, "source": base, "risk": "medium",
                "installable": bool(installable_remotes or installable_packages),
            })
        metadata = payload.get("metadata") or {}
        return {"results": results, "source": base, "next_cursor": metadata.get("nextCursor", "")}

    async def _search_skill_repositories(self, query: str) -> dict[str, Any]:
        sources = source_settings(include_secret=True)
        catalog = str(sources.get("skill_catalog_url") or "").rstrip("/")
        if catalog:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(catalog, params={"q": query, "limit": "30"})
                response.raise_for_status()
                payload = response.json()
            raw_items = payload.get("results") or payload.get("items") or [] if isinstance(payload, dict) else []
            results = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                repository = str(item.get("clone_url") or item.get("repository") or item.get("url") or "")
                if not repository.startswith("https://"):
                    continue
                results.append({
                    "id": str(item.get("id") or item.get("name") or repository), "name": str(item.get("name") or item.get("id") or "Skill"),
                    "kind": "skill", "description": str(item.get("description") or ""), "repository": repository,
                    "clone_url": repository, "publisher": str(item.get("publisher") or ""), "source": catalog,
                    "verified": bool(item.get("verified", False)), "risk": "prompt",
                })
            return {"results": results, "source": catalog, "next_cursor": ""}
        token = str(sources.get("github_token") or "")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params = {"q": f"{query} skill SKILL.md", "sort": "stars", "order": "desc", "per_page": "30"}
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            response = await client.get("https://api.github.com/search/repositories", params=params)
            response.raise_for_status()
            payload = response.json()
        results = [{
            "id": item.get("full_name"), "name": item.get("name"), "kind": "skill", "description": item.get("description") or "",
            "repository": item.get("html_url"), "clone_url": item.get("clone_url"), "default_branch": item.get("default_branch"),
            "publisher": (item.get("owner") or {}).get("login"), "stars": item.get("stargazers_count", 0), "source": "github", "risk": "prompt",
        } for item in payload.get("items", [])]
        return {"results": results, "source": "github", "next_cursor": ""}

    async def inspect_skill_source(self, url: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="cyrene-skill-inspect-", dir=TEMP_DIR) as tmp:
            root, metadata = await self._checkout_skill_source(url, Path(tmp))
            candidates = []
            for entrypoint in sorted(root.rglob("SKILL.md")):
                if any(part == ".git" for part in entrypoint.parts):
                    continue
                from cyrene.learning.skills import extract_skill_summary, validate_skill_directory
                error = validate_skill_directory(entrypoint.parent)
                if error:
                    continue
                name, desc, _text = extract_skill_summary(entrypoint)
                candidates.append({"path": entrypoint.parent.relative_to(root).as_posix(), "name": name, "description": desc})
            return {"candidates": candidates, "source": metadata}

    async def _checkout_skill_source(self, url: str, destination: Path) -> tuple[Path, dict[str, Any]]:
        parsed = urllib.parse.urlparse(str(url or "").strip())
        if parsed.scheme not in {"https"}:
            raise ValueError("Skill repository URL must use HTTPS")
        root = destination / "repo"
        proc = await asyncio.create_subprocess_exec("git", "clone", "--depth", "1", "--", url, str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[-1000:] or "Git clone failed")
        revision_proc = await asyncio.create_subprocess_exec("git", "-C", str(root), "rev-parse", "HEAD", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        revision, _ = await revision_proc.communicate()
        return root, {"source_url": url, "source_commit": revision.decode().strip()}

    def start_install(self, kind: str, extension_id: str, request: dict[str, Any], *, actor: str = "user") -> dict[str, Any]:
        kind = str(kind or "").strip().lower()
        extension_id = str(extension_id or "").strip()
        if kind not in {"cli", "toolchain", "skill", "mcp"} or not extension_id:
            raise ValueError("kind and extension_id are required")
        manager = "skill" if kind == "skill" else "mise"
        if kind == "toolchain":
            spec = TOOLCHAINS.get(extension_id)
            if not spec:
                request_spec = dict(request.get("spec") or {})
                ref = str(request.get("ref") or request_spec.get("ref") or "").strip()
                if (
                    not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", extension_id)
                    or extension_id not in _ALLOWED_MISE_CORE_TOOLCHAINS
                    or ref != f"core:{extension_id}"
                    or str(request_spec.get("kind") or "") != "toolchain"
                    or str(request_spec.get("manager") or "") != "mise"
                ):
                    raise ValueError(f"unknown toolchain: {extension_id}")
                spec = request_spec
            manager = str(spec.get("manager") or "mise")
        task = self.tasks.create(kind=kind, extension_id=extension_id, action="install", actor=actor, request=request)
        self.tasks.start(task, manager, lambda task_id: self._install_worker(task_id, kind, extension_id, request, actor))
        _audit(actor, "install.start", f"{kind}:{extension_id}", {"request": request})
        return task

    async def _install_worker(self, task_id: str, kind: str, extension_id: str, request: dict[str, Any], actor: str) -> dict[str, Any]:
        staging = _STAGING_DIR / task_id
        staging.mkdir(parents=True, exist_ok=True)
        self.tasks.update(task_id, progress=8, message="Resolving exact version and source")
        if kind == "skill":
            return await self._install_skill(task_id, extension_id, request, staging, actor)
        if kind == "mcp":
            return await self._install_mcp(task_id, extension_id, request, actor)
        if kind == "toolchain" and extension_id == "python":
            return await self._install_python(task_id, request, actor)
        if kind == "toolchain" and extension_id == "tex":
            return await self._install_tex(task_id, request, staging, actor)
        return await self._install_mise(task_id, kind, extension_id, request, actor)

    async def _run_manager(self, task_id: str, command: list[str], *, env: dict[str, str], timeout: float = 1800) -> tuple[str, str]:
        if task_id:
            self.tasks.update(task_id, progress=35, message="Downloading and installing")
        proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.CancelledError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
            raise
        if proc.returncode != 0:
            raise RuntimeError((stderr or stdout).decode("utf-8", errors="replace")[-2000:])
        return stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")

    async def _mise_exact_version(self, executable: Path, ref: str, requested: str) -> str:
        if requested and requested not in {"latest", "lts", "stable"} and re.search(r"\d", requested):
            return requested
        selector = ref if requested == "latest" else f"{ref}@{requested}"
        proc = await asyncio.create_subprocess_exec(str(executable), "latest", selector, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=extension_environment())
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[-1000:] or f"Unable to resolve {ref} version")
        exact = stdout.decode().strip().splitlines()[-1].strip()
        if not exact:
            raise RuntimeError(f"Unable to resolve an exact version for {ref}")
        return exact

    async def _install_mise(self, task_id: str, kind: str, extension_id: str, request: dict[str, Any], actor: str) -> dict[str, Any]:
        mise = _bundled_binary("mise")
        if not mise:
            raise RuntimeError("Bundled mise is missing. Reinstall Cyrene or repair the application bundle.")
        base_spec = TOOLCHAINS.get(extension_id) if kind == "toolchain" else CURATED_CLIS.get(extension_id)
        spec = {**(base_spec or {}), **dict(request.get("spec") or {})}
        ref = str(request.get("ref") or spec.get("ref") or spec.get("tool") or extension_id).strip()
        backend = ref.split(":", 1)[0] if ":" in ref else "core"
        if backend not in ALLOWED_MISE_BACKENDS:
            raise ValueError(f"mise backend is not allowed: {backend}")
        if kind == "toolchain" and backend != "core":
            raise ValueError("Runtime environments may only use the trusted mise core backend")
        requested = str(request.get("version") or spec.get("version") or "latest")
        exact = await self._mise_exact_version(mise, ref, requested)
        self.tasks.update(task_id, progress=18, message=f"Resolved {exact}")
        env = extension_environment()
        await self._run_manager(task_id, [str(mise), "install", f"{ref}@{exact}"], env=env)
        self.tasks.update(task_id, progress=78, message="Activating Cyrene-managed version")
        await self._run_manager(task_id, [str(mise), "use", "--global", "--pin", f"{ref}@{exact}"], env=env, timeout=120)
        path_proc = await asyncio.create_subprocess_exec(str(mise), "where", f"{ref}@{exact}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        stdout, _stderr = await path_proc.communicate()
        install_path = stdout.decode().strip()
        record = {
            "id": extension_id, "name": spec.get("name") or extension_id, "kind": kind, "ownership": "cyrene", "observed_state": "installed",
            "version": exact, "default_version": exact, "versions": [exact], "path": install_path, "source": {"type": "mise", "ref": ref, "backend": backend},
            "health": "healthy", "installed_at": _now(), "spec": spec,
        }
        setting_key = "extension_toolchains" if kind == "toolchain" else "extension_clis"
        records = self._managed_records(setting_key)
        previous = next((item for item in records if str(item.get("id")) == extension_id), None)
        if previous:
            record["versions"] = list(dict.fromkeys([*previous.get("versions", []), exact]))
        set_setting(setting_key, [item for item in records if str(item.get("id")) != extension_id] + [record])
        _save_extension_enabled(kind, extension_id, True)
        _audit(actor, "install.finish", f"{kind}:{extension_id}", {"version": exact, "source": record["source"], "path": install_path})
        if kind == "cli":
            # Installation is already durable at this point. Hook assessment is
            # intentionally detached so downloads never wait for a model call
            # or an approval decision.
            from cyrene.hooks.config_agent import schedule_cli_configuration

            schedule_cli_configuration(
                {**record, "key": f"cli:{extension_id}"},
                trigger="install",
            )
        return record

    async def _install_python(self, task_id: str, request: dict[str, Any], actor: str) -> dict[str, Any]:
        uv = _bundled_binary("uv")
        if not uv:
            raise RuntimeError("Bundled uv is missing. Reinstall Cyrene or repair the application bundle.")
        version = str(request.get("version") or TOOLCHAINS["python"]["recommended_version"])
        if version != TOOLCHAINS["python"]["recommended_version"]:
            raise ValueError(f"Cyrene supports the recommended Python {TOOLCHAINS['python']['recommended_version']} line")
        env = extension_environment()
        command = [str(uv), "python", "install", version, "--default", "--no-config"]
        await self._run_manager(task_id, command, env=env)
        find_proc = await asyncio.create_subprocess_exec(str(uv), "python", "find", version, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        stdout, stderr = await find_proc.communicate()
        if find_proc.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace") or "Installed Python could not be located")
        path = Path(stdout.decode().strip())
        actual = _command_version(path, ("--version",))
        record = {
            "id": "python", "name": "Python", "kind": "toolchain", "ownership": "cyrene", "observed_state": "installed",
            "version": actual or version, "default_version": actual or version, "versions": [actual or version], "path": str(path),
            "source": {"type": "uv", "version": UV_VERSION}, "health": "healthy", "installed_at": _now(),
        }
        records = self._managed_records("extension_toolchains")
        set_setting("extension_toolchains", [item for item in records if str(item.get("id")) != "python"] + [record])
        _save_extension_enabled("toolchain", "python", True)
        _audit(actor, "install.finish", "toolchain:python", {"version": record["version"], "path": str(path), "source": record["source"]})
        return record

    async def _github_release(self, repo: str, tag: str = "latest") -> dict[str, Any]:
        sources = source_settings(include_secret=True)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if sources.get("github_token"):
            headers["Authorization"] = f"Bearer {sources['github_token']}"
        url = f"https://api.github.com/repos/{repo}/releases/{'latest' if tag == 'latest' else 'tags/' + urllib.parse.quote(tag)}"
        async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def _download(self, task_id: str, url: str, destination: Path, expected_sha256: str = "") -> str:
        sources = source_settings(include_secret=True)
        mirror = str(sources.get("github_mirror") or "").rstrip("/")
        mode = str(sources.get("network_mode") or "auto")
        candidates = [url]
        if url.startswith("https://github.com/"):
            configured = mirror + "/" + url if mirror else ""
            automatic = "https://ghfast.top/" + url
            if mode == "china":
                candidates = [configured or automatic, url]
            elif mode == "auto" and sources.get("auto_mirror", True):
                candidates = [url, configured or automatic]
            elif configured:
                candidates = [configured, url]
        last_error: Exception | None = None
        digest = hashlib.sha256()
        for actual_url in dict.fromkeys(candidates):
            digest = hashlib.sha256()
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), follow_redirects=True) as client:
                    async with client.stream("GET", actual_url) as response:
                        response.raise_for_status()
                        total = int(response.headers.get("content-length") or 0)
                        written = 0
                        with destination.open("wb") as handle:
                            async for chunk in response.aiter_bytes():
                                handle.write(chunk)
                                digest.update(chunk)
                                written += len(chunk)
                                if total:
                                    self.tasks.update(task_id, progress=min(70, 20 + int(50 * written / total)), message=f"Downloading {written // 1048576} / {total // 1048576} MB")
                last_error = None
                break
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                destination.unlink(missing_ok=True)
        if last_error is not None:
            raise RuntimeError(f"All configured download sources failed: {last_error}") from last_error
        actual = digest.hexdigest()
        if sources.get("verify_signatures", True) and expected_sha256 and actual.lower() != expected_sha256.lower().removeprefix("sha256:"):
            raise RuntimeError("Downloaded file checksum does not match the publisher digest")
        return actual

    async def _install_tex(self, task_id: str, request: dict[str, Any], staging: Path, actor: str) -> dict[str, Any]:
        distribution = str(request.get("distribution") or "tinytex").lower()
        bundle = "TinyTeX-2" if distribution in {"texlive-full", "full", "tinytex-2"} else "TinyTeX-1"
        release = await self._github_release("rstudio/tinytex-releases", "daily" if bundle == "TinyTeX-2" else "latest")
        tag = str(release.get("tag_name") or "")
        platform_fragment = "windows" if os.name == "nt" else "darwin" if os.sys.platform == "darwin" else ("linux-arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "linux-x86_64")
        assets = [asset for asset in release.get("assets", []) if str(asset.get("name", "")).startswith(f"{bundle}-{platform_fragment}")]
        asset = next((item for item in assets if str(item.get("name", "")).endswith((".tar.xz", ".exe"))), None)
        if not asset:
            raise RuntimeError(f"No {bundle} archive is available for {_platform_key()}")
        archive = staging / str(asset["name"])
        checksum = await self._download(task_id, str(asset["browser_download_url"]), archive, str(asset.get("digest") or ""))
        self.tasks.update(task_id, progress=75, message="Verifying and extracting TeX")
        install_root = staging / "installed"
        install_root.mkdir()
        if archive.suffix.lower() == ".exe":
            await self._run_manager(task_id, [str(archive), "-y", f"-o{install_root}"], env=extension_environment(), timeout=1800)
        else:
            _extract_verified_tar(archive, install_root)
        children = [child for child in install_root.iterdir()]
        extracted = children[0] if len(children) == 1 and children[0].is_dir() else install_root
        if _TEX_DIR.exists():
            shutil.rmtree(_TEX_DIR)
        shutil.move(str(extracted), str(_TEX_DIR))
        tlmgr = next(iter(_TEX_DIR.rglob("tlmgr" + (".bat" if os.name == "nt" else ""))), None)
        if not tlmgr:
            raise RuntimeError("Installed TeX distribution does not contain tlmgr")
        version = _command_version(tlmgr, ("--version",), timeout=10)
        record = {
            "id": "tex", "name": "TeX", "kind": "toolchain", "ownership": "cyrene", "observed_state": "installed",
            "version": version or tag, "default_version": version or tag, "versions": [version or tag], "path": str(tlmgr.parent),
            "source": {"type": "github-release", "repo": "rstudio/tinytex-releases", "tag": tag, "asset": asset["name"], "sha256": checksum, "bundle": bundle},
            "health": "healthy", "installed_at": _now(),
        }
        records = self._managed_records("extension_toolchains")
        set_setting("extension_toolchains", [item for item in records if str(item.get("id")) != "tex"] + [record])
        _save_extension_enabled("toolchain", "tex", True)
        _audit(actor, "install.finish", "toolchain:tex", {"version": record["version"], "source": record["source"], "path": record["path"]})
        return record

    async def _install_skill(self, task_id: str, extension_id: str, request: dict[str, Any], staging: Path, actor: str) -> dict[str, Any]:
        url = str(request.get("url") or "").strip()
        selected = [str(item) for item in request.get("subdirs", []) if str(item).strip()]
        if not url:
            raise ValueError("url is required for remote Skill installation")
        root, metadata = await self._checkout_skill_source(url, staging)
        if not selected:
            entries = sorted(root.rglob("SKILL.md"))
            if len(entries) != 1:
                raise ValueError("The repository contains multiple Skills; inspect it and select one or more subdirectories")
            selected = [entries[0].parent.relative_to(root).as_posix()]
        installed = []
        self.tasks.update(task_id, progress=55, message=f"Installing {len(selected)} Skill snapshot(s)")
        for relative in selected:
            candidate = (root / relative).resolve()
            if candidate != root.resolve() and root.resolve() not in candidate.parents:
                raise ValueError("selected Skill path escapes the repository")
            result = install_skill_from_path(candidate, source_metadata={**metadata, "source_subdir": relative})
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or "Skill installation failed"))
            installed.append(result.get("skill"))
        _audit(actor, "install.finish", f"skill:{extension_id}", {"source": metadata, "subdirs": selected, "count": len(installed)})
        return {"skills": installed}

    async def _install_mcp(self, task_id: str, extension_id: str, request: dict[str, Any], actor: str) -> dict[str, Any]:
        config = dict(request.get("config") or {})
        if not config:
            remote = dict(request.get("remote") or {})
            if remote.get("url"):
                if any(header.get("isRequired") for header in remote.get("headers") or []):
                    raise ValueError("This MCP remote requires credentials; add it through Manual MCP after configuring its authentication")
                remote_type = str(remote.get("type") or remote.get("transport") or "streamable_http").lower().replace("-", "_")
                transport = "sse" if remote_type == "sse" else "streamable_http"
                config = {"name": extension_id, "transport": transport, "url": remote["url"], "enabled": True}
        package = dict(request.get("package") or {})
        if not config and package:
            registry_type = str(package.get("registryType") or "").lower()
            identifier = str(package.get("identifier") or "").strip()
            version = str(package.get("version") or request.get("version") or "").strip()
            if registry_type != "npm" or not identifier or not version or version == "latest":
                raise ValueError("Only fixed-version npm MCP packages can be installed directly")
            if any(variable.get("isRequired") for variable in package.get("environmentVariables") or []):
                raise ValueError("This MCP package requires environment configuration; use Manual MCP after installing its executable")
            mise = _bundled_binary("mise")
            if not mise:
                raise RuntimeError("Bundled mise is missing")
            ref = f"npm:{identifier}"
            env = extension_environment()
            await self._run_manager(task_id, [str(mise), "install", f"{ref}@{version}"], env=env)
            await self._run_manager(task_id, [str(mise), "use", "--global", "--pin", f"{ref}@{version}"], env=env, timeout=120)
            where_out, _ = await self._run_manager(task_id, [str(mise), "where", f"{ref}@{version}"], env=env, timeout=120)
            install_root = Path(where_out.strip())
            manifests = sorted(install_root.rglob("package.json"), key=lambda path: len(path.parts))
            binary_name = ""
            for manifest in manifests:
                try:
                    package_json = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    continue
                bin_value = package_json.get("bin")
                if isinstance(bin_value, str):
                    binary_name = str(package_json.get("name") or identifier).rsplit("/", 1)[-1]
                elif isinstance(bin_value, dict) and bin_value:
                    binary_name = str(next(iter(bin_value)))
                if binary_name:
                    break
            if not binary_name:
                raise RuntimeError("The installed MCP package does not declare an executable")
            proc = await asyncio.create_subprocess_exec(str(mise), "which", binary_name, f"--tool={ref}@{version}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0 or not stdout.decode().strip():
                raise RuntimeError(stderr.decode(errors="replace") or "Unable to locate the installed MCP executable")
            package_arguments = [str(item.get("value")) for item in package.get("packageArguments") or [] if item.get("value") is not None]
            config = {"name": extension_id, "transport": "stdio", "command": stdout.decode().strip(), "args": package_arguments, "enabled": True}
            request = {**request, "source": {"type": "mcp-registry-package", "registry": "npm", "identifier": identifier, "version": version, "managed_ref": ref}}
        if not config:
            raise ValueError("Select a registry remote transport or provide a fixed local MCP configuration")
        config["name"] = str(config.get("name") or extension_id)
        config["version"] = str(request.get("version") or config.get("version") or "")
        config["source"] = dict(request.get("source") or {"type": "registry"})
        if config.get("transport", "stdio") == "stdio":
            command = str(config.get("command") or "")
            args = [str(arg) for arg in config.get("args", [])]
            dynamic = " ".join([command, *args]).lower()
            if "@latest" in dynamic or (Path(command).name in {"npx", "uvx"} and ("-y" in args or not config.get("version"))):
                raise ValueError("Runtime dynamic downloads and @latest are not allowed for MCP servers")
            if not Path(command).is_absolute():
                resolved = shutil.which(command)
                if not resolved:
                    raise ValueError("Local MCP command must resolve to an installed deterministic executable")
                config["command"] = str(Path(resolved).resolve())
        from cyrene.tooling.backends.mcp_manager import get_manager, get_mcp_servers, restart_mcp, save_mcp_servers
        previous_servers = get_mcp_servers()
        servers = [server for server in previous_servers if str(server.get("name")) != config["name"]]
        save_mcp_servers([*servers, config])
        self.tasks.update(task_id, progress=75, message="Connecting MCP server")
        try:
            await restart_mcp()
            if config.get("enabled", True):
                status = next((item for item in get_manager().get_server_status() if str(item.get("name")) == config["name"]), None)
                if not status or status.get("status") != "connected":
                    raise RuntimeError("MCP server could not be connected; its configuration was not saved")
        except BaseException:
            # Installation is transactional: a failed or cancelled connection
            # attempt must not leave a registered but unusable server behind.
            save_mcp_servers(previous_servers)
            try:
                await restart_mcp()
            except BaseException:
                logger.exception("Failed to restore MCP connections after installation rollback")
            raise
        _audit(actor, "install.finish", f"mcp:{extension_id}", {"version": config.get("version"), "source": config.get("source"), "transport": config.get("transport")})
        return config

    async def uninstall(self, kind: str, extension_id: str, *, version: str = "", actor: str = "user") -> dict[str, Any]:
        kind = str(kind).lower()
        if kind == "skill":
            from cyrene.learning.skills import uninstall_skill
            ok = uninstall_skill(extension_id)
            _forget_extension_enabled(kind, extension_id)
            _audit(actor, "uninstall", f"skill:{extension_id}", {}, "ok" if ok else "missing")
            return {"ok": ok}
        if kind == "mcp":
            from cyrene.tooling.backends.mcp_manager import get_mcp_servers, restart_mcp, save_mcp_servers
            servers = get_mcp_servers()
            existing = next((server for server in servers if str(server.get("name")) == extension_id), None)
            remaining = [server for server in servers if str(server.get("name")) != extension_id]
            if len(remaining) == len(servers):
                return {"ok": False, "error": "MCP server not found"}
            save_mcp_servers(remaining)
            await restart_mcp()
            source = (existing or {}).get("source") or {}
            managed_ref = str(source.get("managed_ref") or "")
            managed_version = str(source.get("version") or "")
            mise = _bundled_binary("mise")
            if managed_ref and managed_version and mise:
                await self._run_manager("", [str(mise), "unuse", "--global", f"{managed_ref}@{managed_version}"], env=extension_environment(), timeout=300)
            _forget_extension_enabled(kind, extension_id)
            _audit(actor, "uninstall", f"mcp:{extension_id}", {})
            return {"ok": True}
        setting_key = "extension_toolchains" if kind == "toolchain" else "extension_clis"
        records = self._managed_records(setting_key)
        record = next((item for item in records if str(item.get("id")) == extension_id), None)
        if not record:
            return {"ok": False, "error": "Cyrene-managed extension not found"}
        if kind == "toolchain" and extension_id == "python":
            uv = _bundled_binary("uv")
            target = version or str(record.get("default_version") or record.get("version") or "")
            if uv:
                await self._run_manager("", [str(uv), "python", "uninstall", target], env=extension_environment())
        elif kind == "toolchain" and extension_id == "tex":
            shutil.rmtree(_TEX_DIR, ignore_errors=True)
        else:
            mise = _bundled_binary("mise")
            ref = str((record.get("source") or {}).get("ref") or extension_id)
            target = version or str(record.get("default_version") or record.get("version") or "")
            if mise:
                proc = await asyncio.create_subprocess_exec(str(mise), "unuse", "--global", f"{ref}@{target}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=extension_environment())
                _out, err = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(err.decode(errors="replace")[-1000:])
        set_setting(setting_key, [item for item in records if str(item.get("id")) != extension_id])
        _forget_extension_enabled(kind, extension_id)
        _audit(actor, "uninstall", f"{kind}:{extension_id}", {"version": version or record.get("version")})
        return {"ok": True}


_SERVICE: ExtensionService | None = None


def get_extension_service() -> ExtensionService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ExtensionService()
    return _SERVICE


__all__ = [
    "ExtensionService", "agent_extension_paths", "agent_process_environment",
    "audit_records", "extension_environment", "extension_is_enabled",
    "get_extension_service", "source_settings", "update_source_settings",
]
