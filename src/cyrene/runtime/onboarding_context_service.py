"""Application services for onboarding context and SOUL settings."""

from __future__ import annotations

import asyncio
import platform
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cyrene import config
from cyrene.runtime import settings_store


class SoulRepository:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        except OSError:
            return ""

    def write(self, content: str) -> None:
        self.path.write_text(content, encoding="utf-8")


class ProjectResolver:
    """Resolve the active project workspace from the public project-state port."""

    def __init__(self, read_state: Callable[[], dict[str, Any]], default_workspace: Path):
        self.read_state = read_state
        self.default_workspace = default_workspace

    def active_workspace(self) -> str:
        state = self.read_state()
        active_id = str(state.get("activeProjectId") or "").strip()
        project = next(
            (
                item
                for item in state.get("projects") or []
                if isinstance(item, dict) and str(item.get("id") or "") == active_id
            ),
            None,
        )
        workspace = str(project.get("workspacePath") or "").strip() if project else ""
        return workspace or str(self.default_workspace)


class OnboardingContextApplicationService:
    def __init__(
        self,
        soul: SoulRepository,
        projects: ProjectResolver,
        system_name: Callable[[], str] = platform.system,
        run_process: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.soul = soul
        self.projects = projects
        self.system_name = system_name
        self.run_process = run_process

    def context_state(self) -> dict[str, Any]:
        return {
            "soul_active": settings_store.is_soul_active(),
            "workspace_active": settings_store.is_workspace_active(),
            "workspace_dir": self.projects.active_workspace(),
            "workspace_history": settings_store.get_workspace_history(),
        }

    def set_soul_active(self, active: bool) -> dict:
        settings_store.set_soul_active(active)
        return {"ok": True}

    def set_workspace_active(self, active: bool) -> dict:
        settings_store.set_workspace_active(active)
        return {"ok": True}

    def activate_workspace(self, path: str) -> dict:
        settings_store.activate_workspace(str(path or "").strip())
        return {"ok": True}

    async def pick_directory(self) -> dict[str, Any]:
        system = self.system_name()
        if system != "Darwin":
            return {"path": "", "error": f"Directory picker not supported on {system}"}
        try:
            result = await asyncio.to_thread(
                self.run_process,
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "Select workspace directory")',
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {"path": "", "error": "Directory picker timed out"}
        path = result.stdout.strip()
        return {"path": path} if path else {"path": "", "cancelled": True}

    def get_soul(self) -> dict:
        return {"content": self.soul.read()}

    def update_soul(self, content: Any) -> dict:
        self.soul.write(str(content or ""))
        return {"ok": True}

    def get_keys(self) -> dict:
        return {"keys": config.get_env_keys_meta()}

    def update_keys(self, body: dict[str, Any]) -> dict:
        updates = {}
        for key, meta in config.editable_env_keys().items():
            value = body.get(key, "")
            if not value:
                continue
            if meta["masked"] and (value.startswith("••") or len(value) <= 8):
                continue
            updates[key] = value
        if not updates:
            return {"error": "no valid keys provided"}
        config.write_env_keys(updates)
        return {"ok": True, "updated": list(updates)}
