"""Package store and isolated lifecycle manager for project plugins."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

from cyrene.config import DATA_DIR
from cyrene.plugins.manifest import PluginManifest, load_manifest, require_plugin_id


class PluginError(RuntimeError):
    pass


_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")


def _project_id(value: Any) -> str:
    project_id = str(value or "").strip()
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise PluginError("project_id is invalid")
    return project_id


class PluginProcess:
    def __init__(
        self,
        manifest: PluginManifest,
        package_dir: Path,
        data_dir: Path,
        project_id: str,
        event_sink: Callable[[dict[str, Any]], Awaitable[None]],
        log_path: Path,
    ) -> None:
        self.manifest = manifest
        self.package_dir = package_dir
        self.data_dir = data_dir
        self.project_id = project_id
        self.event_sink = event_sink
        self.log_path = log_path
        self.process: asyncio.subprocess.Process | None = None
        self.contributions: list[dict[str, Any]] = []
        self.error = ""
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._ready: asyncio.Future[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._recent_logs: deque[str] = deque(maxlen=200)

    @property
    def state(self) -> str:
        if self.process is not None and self.process.returncode is None and self.contributions is not None:
            return "enabled"
        return "load-error" if self.error else "installed-disabled"

    async def start(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return
        self.error = ""
        self.contributions = []
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ready = asyncio.get_running_loop().create_future()
        env = dict(os.environ)
        env.update({
            "CYRENE_PLUGIN_ID": self.manifest.id,
            "CYRENE_PLUGIN_PROJECT_ID": self.project_id,
            "CYRENE_PLUGIN_PACKAGE_DIR": str(self.package_dir),
            "CYRENE_PLUGIN_DATA_DIR": str(self.data_dir),
        })
        host_command = (
            [sys.executable, "--launch-plugin-host"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "cyrene.plugins.host"]
        )
        source_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join(
            [source_root, str(env.get("PYTHONPATH") or "")]
        ).rstrip(os.pathsep)
        self.process = await asyncio.create_subprocess_exec(
            *host_command,
            "--package",
            str(self.package_dir),
            "--project",
            self.project_id,
            "--data",
            str(self.data_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        try:
            await asyncio.wait_for(self._ready, timeout=15.0)
        except Exception as exc:
            self.error = self.error or f"plugin did not become ready: {exc}"
            await self.stop(force=True)
            raise PluginError(self.error) from exc

    async def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self.error = "plugin host produced invalid protocol output"
                    continue
                message_type = message.get("type")
                if message_type == "ready":
                    contributions = message.get("contributions")
                    self.contributions = contributions if isinstance(contributions, list) else []
                    if self._ready is not None and not self._ready.done():
                        self._ready.set_result(None)
                elif message_type == "response":
                    future = self._pending.pop(str(message.get("id") or ""), None)
                    if future is not None and not future.done():
                        if message.get("ok") is False:
                            future.set_exception(PluginError(str(message.get("error") or "plugin call failed")))
                        else:
                            future.set_result(message.get("result"))
                elif message_type == "event":
                    await self.event_sink({
                        "type": "plugin-event",
                        "pluginId": self.manifest.id,
                        "projectId": self.project_id,
                        "event": str(message.get("event") or "event"),
                        "payload": message.get("payload"),
                    })
                elif message_type == "fatal":
                    self.error = str(message.get("error") or "plugin failed to load")
                    if self._ready is not None and not self._ready.done():
                        self._ready.set_exception(PluginError(self.error))
        finally:
            return_code = await process.wait()
            if return_code and not self.error:
                self.error = f"plugin process exited with code {return_code}"
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(PluginError(self.error or "plugin process exited"))
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(PluginError(self.error or "plugin process exited"))
            self._pending.clear()
            await self.event_sink({
                "type": "plugin-state",
                "pluginId": self.manifest.id,
                "projectId": self.project_id,
                "state": "load-error" if self.error else "installed-disabled",
                "error": self.error,
            })

    async def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        with self.log_path.open("a", encoding="utf-8") as log:
            while line := await process.stderr.readline():
                text = line.decode("utf-8", errors="replace").rstrip()
                self._recent_logs.append(text)
                log.write(text + "\n")
                log.flush()

    async def call(self, method: str, args: Any, *, timeout: float = 120.0) -> Any:
        await self.start()
        if self.process is None or self.process.stdin is None:
            raise PluginError("plugin process is unavailable")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = json.dumps({"id": request_id, "method": method, "args": args}, ensure_ascii=False)
        self.process.stdin.write((payload + "\n").encode("utf-8"))
        await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=max(1.0, min(timeout, 3600.0)))
        finally:
            self._pending.pop(request_id, None)

    async def stop(self, *, force: bool = False) -> None:
        process = self.process
        if process is None or process.returncode is not None:
            return
        if not force:
            try:
                await self.call("$shutdown", None, timeout=3.0)
            except Exception:
                pass
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        self.process = None

    def public_logs(self) -> list[str]:
        return list(self._recent_logs)


class PluginManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or DATA_DIR / "plugins").resolve()
        self.packages_dir = self.root / "packages"
        self.data_dir = self.root / "data"
        self.logs_dir = self.root / "logs"
        self.state_path = self.root / "state.json"
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._processes: dict[tuple[str, str], PluginProcess] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._state_lock = asyncio.Lock()

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"enabled": {}}
        except (OSError, json.JSONDecodeError):
            return {"enabled": {}}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.state_path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def _enabled_ids(self, project_id: str) -> set[str]:
        enabled = self._read_state().get("enabled")
        items = enabled.get(project_id, []) if isinstance(enabled, dict) else []
        return {str(item) for item in items if str(item)}

    def package_dir(self, plugin_id: str) -> Path:
        return self.packages_dir / require_plugin_id(plugin_id)

    def manifest(self, plugin_id: str) -> PluginManifest:
        package = self.package_dir(plugin_id)
        if not package.is_dir():
            raise PluginError("plugin is not installed")
        return load_manifest(package)

    def installed(self) -> list[tuple[PluginManifest, Path]]:
        result: list[tuple[PluginManifest, Path]] = []
        if not self.packages_dir.is_dir():
            return result
        for package in sorted(self.packages_dir.iterdir(), key=lambda item: item.name.lower()):
            if not package.is_dir():
                continue
            try:
                result.append((load_manifest(package), package))
            except ValueError:
                continue
        return result

    async def _publish(self, event: dict[str, Any]) -> None:
        project_id = str(event.get("projectId") or "")
        for queue in list(self._subscribers.get(project_id, set())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(dict(event))

    async def subscribe(self, project_id: str):
        project_id = _project_id(project_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.setdefault(project_id, set()).add(queue)
        try:
            yield queue
        finally:
            subscribers = self._subscribers.get(project_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(project_id, None)

    def _process(self, plugin_id: str, project_id: str) -> PluginProcess:
        key = (require_plugin_id(plugin_id), _project_id(project_id))
        existing = self._processes.get(key)
        if existing is not None:
            return existing
        manifest = self.manifest(key[0])
        process = PluginProcess(
            manifest,
            self.package_dir(key[0]),
            self.data_dir / key[0] / key[1],
            key[1],
            self._publish,
            self.logs_dir / key[0] / f"{key[1]}.log",
        )
        self._processes[key] = process
        return process

    async def ensure_started(self, plugin_id: str, project_id: str) -> PluginProcess:
        project_id = _project_id(project_id)
        if plugin_id not in self._enabled_ids(project_id):
            raise PluginError("plugin is disabled for this project")
        key = (plugin_id, project_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            process = self._process(plugin_id, project_id)
            await process.start()
            return process

    async def list_plugins(self, project_id: str = "") -> list[dict[str, Any]]:
        enabled_ids = self._enabled_ids(project_id) if project_id else set()
        result: list[dict[str, Any]] = []
        for manifest, package in self.installed():
            process = self._processes.get((manifest.id, project_id)) if project_id else None
            enabled = manifest.id in enabled_ids
            if enabled and process is None:
                try:
                    process = await self.ensure_started(manifest.id, project_id)
                except Exception:
                    process = self._processes.get((manifest.id, project_id))
            state = (
                "load-error" if enabled and process and process.error
                else "enabled" if enabled and process and process.process and process.process.returncode is None
                else "installed-disabled"
            )
            result.append({
                **manifest.public_dict(),
                "installed": True,
                "enabled": enabled,
                "state": state,
                "error": process.error if process else "",
                "packagePath": str(package),
                "dataPath": str(self.data_dir / manifest.id),
                "logPath": str(self.logs_dir / manifest.id / f"{project_id}.log") if project_id else "",
            })
        return result

    async def set_enabled(self, plugin_id: str, project_id: str, enabled: bool) -> dict[str, Any]:
        plugin_id = require_plugin_id(plugin_id)
        project_id = _project_id(project_id)
        self.manifest(plugin_id)
        async with self._state_lock:
            state = self._read_state()
            mapping = state.setdefault("enabled", {})
            current = {str(item) for item in mapping.get(project_id, [])}
            if enabled:
                current.add(plugin_id)
            else:
                current.discard(plugin_id)
            mapping[project_id] = sorted(current)
            self._write_state(state)
        process = self._processes.get((plugin_id, project_id))
        error = ""
        if enabled:
            try:
                process = await self.ensure_started(plugin_id, project_id)
            except Exception as exc:
                error = str(exc)
        elif process is not None:
            await process.stop()
            self._processes.pop((plugin_id, project_id), None)
        await self._publish({
            "type": "plugin-state",
            "pluginId": plugin_id,
            "projectId": project_id,
            "state": "load-error" if error else "enabled" if enabled else "installed-disabled",
            "error": error,
        })
        if error:
            raise PluginError(error)
        return {"ok": True, "pluginId": plugin_id, "projectId": project_id, "enabled": enabled}

    async def contributions(self, project_id: str, point: str = "") -> list[dict[str, Any]]:
        project_id = _project_id(project_id)
        result: list[dict[str, Any]] = []
        for plugin_id in sorted(self._enabled_ids(project_id)):
            try:
                process = await self.ensure_started(plugin_id, project_id)
            except Exception:
                continue
            manifest = process.manifest
            for raw in process.contributions:
                if not isinstance(raw, dict):
                    continue
                extension_point = str(raw.get("point") or "")
                if point and extension_point != point:
                    continue
                contribution = dict(raw)
                contribution.update({
                    "pluginId": plugin_id,
                    "pluginName": manifest.name,
                    "pluginVersion": manifest.version,
                })
                if extension_point == "cyrene.view" and manifest.frontend_entry:
                    contribution.setdefault("entry", manifest.frontend_entry)
                result.append(contribution)
        return result

    async def call(self, plugin_id: str, project_id: str, method: str, args: Any, timeout: float = 120.0) -> Any:
        project_id = _project_id(project_id)
        process = await self.ensure_started(plugin_id, project_id)
        return await process.call(str(method or ""), args, timeout=timeout)

    async def reload(self, plugin_id: str, project_id: str) -> dict[str, Any]:
        project_id = _project_id(project_id)
        process = self._processes.pop((plugin_id, project_id), None)
        if process is not None:
            await process.stop()
        restarted = await self.ensure_started(plugin_id, project_id)
        await self._publish({
            "type": "plugin-state", "pluginId": plugin_id,
            "projectId": project_id, "state": "enabled", "error": "",
        })
        return {"ok": True, "contributions": len(restarted.contributions)}

    def _extract_zip(self, archive: Path, target: Path) -> Path:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            total = sum(max(0, item.file_size) for item in members)
            if len(members) > 10_000 or total > 2 * 1024 * 1024 * 1024:
                raise PluginError("plugin archive is too large")
            for item in members:
                destination = (target / item.filename).resolve(strict=False)
                if destination != target and target not in destination.parents:
                    raise PluginError("plugin archive contains an unsafe path")
            bundle.extractall(target)
        if any((target / name).is_file() for name in ("plugin.json", "cyrene.plugin.json")):
            return target
        roots = [item for item in target.iterdir() if item.is_dir()]
        if len(roots) == 1:
            return roots[0]
        raise PluginError("plugin archive does not contain plugin.json at its root")

    async def install(self, source: Path, *, replace: bool = False) -> dict[str, Any]:
        source = Path(source).expanduser().resolve()
        if not source.exists():
            raise PluginError("plugin source does not exist")
        staging_parent = Path(tempfile.mkdtemp(prefix="cyrene-plugin-install-"))
        try:
            source_root = self._extract_zip(source, staging_parent) if source.is_file() else source
            manifest = load_manifest(source_root)
            destination = self.package_dir(manifest.id)
            if destination.exists() and not replace:
                raise PluginError("plugin is already installed")
            staging = self.packages_dir / f".{manifest.id}-{uuid.uuid4().hex}.staging"
            shutil.copytree(source_root, staging, symlinks=False)
            load_manifest(staging)
            if destination.exists():
                for key, process in list(self._processes.items()):
                    if key[0] == manifest.id:
                        await process.stop()
                        self._processes.pop(key, None)
                backup = self.packages_dir / f".{manifest.id}-{uuid.uuid4().hex}.backup"
                os.replace(destination, backup)
                try:
                    os.replace(staging, destination)
                except Exception:
                    os.replace(backup, destination)
                    raise
                shutil.rmtree(backup, ignore_errors=True)
            else:
                os.replace(staging, destination)
            return {"ok": True, "plugin": manifest.public_dict()}
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

    async def delete(self, plugin_id: str, *, delete_data: bool = False) -> dict[str, Any]:
        plugin_id = require_plugin_id(plugin_id)
        package = self.package_dir(plugin_id)
        if not package.is_dir():
            raise PluginError("plugin is not installed")
        for key, process in list(self._processes.items()):
            if key[0] == plugin_id:
                await process.stop()
                self._processes.pop(key, None)
        affected_projects: list[str] = []
        async with self._state_lock:
            state = self._read_state()
            mapping = state.get("enabled") if isinstance(state.get("enabled"), dict) else {}
            for project_id, items in list(mapping.items()):
                if plugin_id in {str(item) for item in items}:
                    affected_projects.append(str(project_id))
                mapping[project_id] = [item for item in items if str(item) != plugin_id]
            state["enabled"] = mapping
            self._write_state(state)
        shutil.rmtree(package)
        data_deleted = False
        if delete_data:
            plugin_data = self.data_dir / plugin_id
            if plugin_data.is_dir():
                shutil.rmtree(plugin_data)
            plugin_logs = self.logs_dir / plugin_id
            if plugin_logs.is_dir():
                shutil.rmtree(plugin_logs)
            data_deleted = True
        for project_id in affected_projects:
            await self._publish({"type": "plugin-removed", "pluginId": plugin_id, "projectId": project_id})
        return {"ok": True, "pluginId": plugin_id, "dataDeleted": data_deleted}

    def asset_path(self, plugin_id: str, project_id: str, relative: str) -> Path:
        project_id = _project_id(project_id)
        if plugin_id not in self._enabled_ids(project_id):
            raise PluginError("plugin is disabled for this project")
        root = self.package_dir(plugin_id).resolve()
        target = (root / str(relative or "")).resolve(strict=False)
        if target == root or root not in target.parents or not target.is_file():
            raise PluginError("plugin asset not found")
        return target

    def logs(self, plugin_id: str, project_id: str) -> dict[str, Any]:
        project_id = _project_id(project_id)
        process = self._processes.get((plugin_id, project_id))
        path = self.logs_dir / plugin_id / f"{project_id}.log"
        lines = process.public_logs() if process else []
        if not lines and path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        return {"lines": lines, "path": str(path)}

    async def close(self) -> None:
        for process in list(self._processes.values()):
            await process.stop()
        self._processes.clear()


_MANAGER: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = PluginManager()
    return _MANAGER


__all__ = ["PluginError", "PluginManager", "get_plugin_manager"]
