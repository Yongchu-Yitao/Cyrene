"""Application boundary for Extension Center HTTP use cases."""

from __future__ import annotations

import asyncio
import platform
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from cyrene.extensions.service import ExtensionService


class ExtensionApplicationError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class ExtensionInstallInputService:
    """Acquire local install inputs and guarantee temporary-file cleanup."""

    def __init__(
        self,
        extensions: ExtensionService,
        temp_dir: Path,
        system_name: Callable[[], str] = platform.system,
        run_process: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.extensions = extensions
        self.temp_dir = temp_dir
        self.system_name = system_name
        self.run_process = run_process

    def install_path(self, path: str) -> dict:
        return self._install(path)

    async def install_upload(self, upload: Any) -> dict:
        if not upload:
            raise ExtensionApplicationError("No file provided")
        content = await upload.read()
        if len(content) > 8 * 1024 * 1024:
            raise ExtensionApplicationError("File too large (max 8 MB)")
        suffix = Path(upload.filename or "skill.tmp").suffix or ".tmp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=self.temp_dir
        ) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        try:
            return self._install(temporary_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    async def pick_and_install(self) -> dict:
        system = self.system_name()
        if system != "Darwin":
            raise ExtensionApplicationError(f"Skill picker not supported on {system}")
        try:
            result = await asyncio.to_thread(
                self.run_process,
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "Select skill folder containing SKILL.md")',
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExtensionApplicationError("Picker timed out — please try again") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExtensionApplicationError(f"Picker error: {exc}") from exc
        stderr = (result.stderr or "").strip()
        if stderr and "User cancelled" not in stderr:
            raise ExtensionApplicationError(f"Picker error: {stderr}")
        selected = result.stdout.strip()
        return self._install(selected) if selected else {"ok": False, "cancelled": True}

    def _install(self, path: str | Path) -> dict:
        try:
            return self.extensions.install_local_skill(path, actor="user")
        except Exception as exc:
            raise ExtensionApplicationError(str(exc)) from exc


class ExtensionApplicationService:
    def __init__(
        self,
        extensions: ExtensionService,
        install_inputs: ExtensionInstallInputService,
        *,
        source_get: Callable[..., dict],
        source_update: Callable[[dict], dict],
        audit_get: Callable[[int], list],
    ) -> None:
        self.extensions = extensions
        self.install_inputs = install_inputs
        self.source_get = source_get
        self.source_update = source_update
        self.audit_get = audit_get

    async def inspect_skill(self, url: str) -> dict:
        return await self._async(self.extensions.inspect_skill_source, url)

    def install_skill_path(self, path: str) -> dict:
        return self.install_inputs.install_path(path)

    async def install_skill_upload(self, upload: Any) -> dict:
        return await self.install_inputs.install_upload(upload)

    async def pick_and_install_skill(self) -> dict:
        return await self.install_inputs.pick_and_install()

    def list(self) -> dict:
        return self.extensions.list_extensions()

    async def search(self, kind: str, query: str, *, advanced: bool, cursor: str):
        try:
            return await self.extensions.search(kind, query, advanced=advanced, cursor=cursor)
        except httpx.HTTPError as exc:
            raise ExtensionApplicationError(
                f"Extension source request failed: {exc}", 502
            ) from exc
        except Exception as exc:
            raise ExtensionApplicationError(str(exc)) from exc

    async def versions(self, kind: str, extension_id: str):
        return await self._async(self.extensions.list_versions, kind, extension_id)

    async def propose_agent(self, source: Any, version: str):
        try:
            return await self.extensions.create_agent_install_proposal(
                source, version, actor="user"
            )
        except httpx.HTTPError as exc:
            raise ExtensionApplicationError(
                f"Agent manifest source request failed: {exc}", 502
            ) from exc
        except Exception as exc:
            raise ExtensionApplicationError(str(exc)) from exc

    async def confirm_agent(self, proposal_id: str):
        return await self._async(
            self.extensions.confirm_agent_install_proposal,
            proposal_id,
            actor="user",
        )

    def start_install(self, body: dict) -> dict:
        try:
            options = dict(body)
            kind = str(options.pop("kind", ""))
            extension_id = str(options.pop("extension_id", ""))
            task = self.extensions.start_install(kind, extension_id, options, actor="user")
            return {"ok": True, "task": task}
        except Exception as exc:
            raise ExtensionApplicationError(str(exc)) from exc

    async def uninstall(self, kind: str, extension_id: str, version: str):
        return await self._async(
            self.extensions.uninstall,
            kind,
            extension_id,
            version=version,
            actor="user",
        )

    async def set_default(self, extension_id: str, version: str):
        return await self._async(
            self.extensions.set_default_version,
            extension_id,
            version,
            actor="user",
        )

    async def set_enabled(self, kind: str, extension_id: str, enabled: Any):
        if not isinstance(enabled, bool):
            raise ExtensionApplicationError("enabled must be a boolean")
        return await self._async(
            self.extensions.set_extension_enabled,
            kind,
            extension_id,
            enabled,
            actor="user",
        )

    def bind(self, extension_id: str, path: str):
        return self._sync(self.extensions.bind_system_executable, extension_id, path)

    def unbind(self, extension_id: str):
        return self.extensions.unbind_system_executable(extension_id)

    def tasks(self) -> dict:
        return {"tasks": self.extensions.tasks.list()}

    def task(self, task_id: str) -> dict | None:
        return self.extensions.tasks.get(task_id)

    def cancel_task(self, task_id: str) -> dict:
        return {"ok": self.extensions.tasks.cancel(task_id)}

    def sources(self) -> dict:
        return self.source_get()

    def update_sources(self, body: dict) -> dict:
        return self._sync(self.source_update, body)

    async def test_sources(self) -> dict:
        settings = self.source_get(include_secret=True)
        targets = {
            "github": "https://api.github.com/rate_limit",
            "npm": str(settings.get("npm_registry") or "https://registry.npmjs.org").rstrip("/") + "/-/ping",
            "pip": str(settings.get("pip_index_url") or "https://pypi.org/simple").rstrip("/"),
            "mcp": str(settings.get("mcp_registry_url") or "https://registry.modelcontextprotocol.io").rstrip("/") + "/v0.1/health",
        }
        if settings.get("skill_catalog_url"):
            targets["skills"] = str(settings["skill_catalog_url"])
        headers = {"Authorization": f"Bearer {settings['github_token']}"} if settings.get("github_token") else {}
        checks = {}
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            for name, url in targets.items():
                try:
                    response = await client.get(url, headers=headers if name == "github" else None)
                    checks[name] = {"ok": response.status_code < 500, "status": response.status_code, "url": url}
                except httpx.HTTPError as exc:
                    checks[name] = {"ok": False, "error": str(exc), "url": url}
        return {"ok": all(item.get("ok") for item in checks.values()), "checks": checks}

    def audit(self, limit: int) -> dict:
        return {"records": self.audit_get(limit)}

    def _sync(self, function: Callable, *args: Any, **kwargs: Any):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            raise ExtensionApplicationError(str(exc)) from exc

    async def _async(self, function: Callable, *args: Any, **kwargs: Any):
        try:
            return await function(*args, **kwargs)
        except Exception as exc:
            raise ExtensionApplicationError(str(exc)) from exc
