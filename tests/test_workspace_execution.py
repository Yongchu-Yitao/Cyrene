from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cyrene.plugins.builtin.cyrene_code import plugin_pack
from cyrene.plugins.builtin.cyrene_code import workspace_execution as execution_module
from cyrene.plugins.builtin.cyrene_code.workspace_execution import (
    WorkspaceExecutionError,
    WorkspaceExecutionService,
)
from cyrene.plugins.builtin.cyrene_code.terminal.client import TerminalRequestError
from cyrene.plugins.builtin.cyrene_code.workspace_action import TOOL_METADATA
from cyrene.plugins.contributions import workspace_actions, workbench_surfaces
from cyrene.plugins.contributions import workspace_project_types
from cyrene.plugins.builtin.cyrene_project_javascript import (
    plugin_pack as javascript_project_pack,
)
from cyrene.workbench.projects.project_execution import normalize_execution_actions


class FakeTerminal:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.status = "running"
        self.exit_code = None
        self.screen_text = ""
        self.interrupted: list[str] = []
        self.removed: list[str] = []
        self.on_create = None
        self.screen_error: Exception | None = None
        self.create_error: Exception | None = None
        self.terminals: list[dict] = []

    async def list(self, project_id: str):
        return {
            "terminals": [
                dict(item)
                for item in self.terminals
                if item.get("projectId") == project_id
            ]
        }

    async def create_agent_terminal(self, project_id: str, **arguments):
        if self.create_error is not None:
            raise self.create_error
        self.created.append({"projectId": project_id, **arguments})
        self.status = "running"
        self.exit_code = None
        if self.on_create is not None:
            self.on_create()
        terminal = {
            "id": f"terminal-{len(self.created)}",
            "projectId": project_id,
            "title": str(arguments.get("title") or ""),
            "ownerToolCallId": str(arguments.get("owner_tool_call_id") or ""),
            "status": "running",
        }
        self.terminals.append(terminal)
        return {"terminal": dict(terminal)}

    async def screen(self, terminal_id: str):
        if self.screen_error is not None:
            raise self.screen_error
        return {
            "terminal": {
                "id": terminal_id,
                "status": self.status,
                "exitCode": self.exit_code,
                "exitAt": "2026-08-30T08:00:00+00:00" if self.status == "exited" else "",
            },
            "screenText": self.screen_text,
        }

    async def interrupt(self, terminal_id: str):
        self.interrupted.append(terminal_id)
        self.status = "exited"
        self.exit_code = 130
        return {"terminal": {"id": terminal_id, "status": "exited", "exitCode": 130}}

    async def remove(self, terminal_id: str):
        self.removed.append(terminal_id)
        self.terminals = [
            item for item in self.terminals if item.get("id") != terminal_id
        ]
        return {"terminal": {"id": terminal_id, "status": "exited"}, "deleted": True}


def _service(
    tmp_path: Path,
    project: dict,
    terminal: FakeTerminal,
    *,
    project_type_provider=lambda: (),
) -> WorkspaceExecutionService:
    return WorkspaceExecutionService(
        db_path=str(tmp_path / "workbench.sqlite3"),
        state_path=tmp_path / "state" / "executions.json",
        terminal_client=terminal,
        find_project=lambda project_id: project if project_id == project["id"] else None,
        resolve_workspace=lambda value: value["workspacePath"],
        project_type_provider=project_type_provider,
    )


def test_execution_config_is_bounded_and_workspace_relative() -> None:
    actions = normalize_execution_actions([{
        "id": "app.preview",
        "label": "Preview app",
        "kind": "preview",
        "program": "npm",
        "args": ["run", "preview"],
        "cwd": "frontend",
        "previewPort": 4173,
        "artifactPatterns": ["dist/index.html"],
        "longRunning": True,
    }])

    assert actions[0]["previewPort"] == 4173
    assert actions[0]["artifactPatterns"] == ["dist/index.html"]
    assert actions[0]["source"] == "user"
    with pytest.raises(ValueError, match="inside the project workspace"):
        normalize_execution_actions([{
            "id": "escape", "kind": "run", "program": "python",
            "args": [], "cwd": "../outside",
        }])


@pytest.mark.asyncio
async def test_discovery_prefers_project_actions_and_respects_execution_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    app = workspace / "app"
    app.mkdir(parents=True)
    (app / "package.json").write_text(json.dumps({
        "scripts": {"dev": "vite", "build": "vite build", "test": "vitest"},
    }))
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionScope": "app",
        "executionActions": [{
            "id": "custom.check", "label": "Check", "kind": "test",
            "program": sys.executable, "args": ["-m", "compileall", "."], "cwd": "app",
        }],
    }
    project_type = workspace_project_types(javascript_project_pack)[0]
    service = _service(
        tmp_path,
        project,
        FakeTerminal(),
        project_type_provider=lambda: ((javascript_project_pack.id, project_type),),
    )

    payload = await service.discover("project-1")

    ids = {item["id"] for item in payload["actions"]}
    assert payload["currentPath"] == "app"
    assert "javascript.dev.app" in ids
    assert "javascript.build.app" in ids
    assert "custom.check" in ids
    assert payload["projectTypes"] == ["cyrene_project_javascript/javascript"]
    assert next(
        item for item in payload["actions"] if item["id"] == "javascript.dev.app"
    )["source"] == "project-plugin:cyrene_project_javascript/javascript"
    assert next(item for item in payload["actions"] if item["id"] == "custom.check")["available"]


@pytest.mark.asyncio
async def test_managed_execution_captures_diagnostics_changes_and_review(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.py"
    source.write_text("print('before')\n")
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionScope": ".",
        "executionActions": [{
            "id": "custom.test", "label": "Test", "kind": "test",
            "program": sys.executable, "args": ["-m", "pytest"], "cwd": ".",
            "artifactPatterns": ["report.txt"],
        }],
    }
    terminal = FakeTerminal()
    service = _service(tmp_path, project, terminal)

    running = await service.start(
        "project-1", "custom.test", current_path="main.py", chat_id="chat-1",
        goal_id="goal-1",
    )
    assert running["status"] == "running"
    assert running["owner"] == "goal"
    assert terminal.created[0]["wake_on_exit"] is True

    source.write_text("print('after')\n")
    (workspace / "report.txt").write_text("test report\n")
    terminal.status = "exited"
    terminal.exit_code = 1
    terminal.screen_text = "main.py:2:3: error: assertion failed"

    completed = await service.refresh(running["id"])
    review = await service.review("project-1", "chat-1")

    assert completed["status"] == "failed"
    assert completed["diagnostics"][0] == {
        "severity": "error",
        "message": "assertion failed",
        "file": "main.py",
        "line": 2,
        "column": 3,
        "source": "workspace-action",
    }
    assert completed["artifacts"][0]["path"] == "report.txt"
    assert completed["changeSet"]["fileCount"] == 2
    assert review["snapshot"]["fileCount"] == 2
    assert review["git"]["available"] is False


@pytest.mark.asyncio
async def test_rerunning_an_action_replaces_its_finished_one_shot_terminal(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionActions": [{
            "id": "run", "label": "Run", "kind": "run",
            "program": sys.executable, "args": [], "cwd": ".",
        }],
    }
    terminal = FakeTerminal()
    service = _service(tmp_path, project, terminal)

    first = await service.start("project-1", "run", chat_id="chat-1")
    terminal.status = "exited"
    terminal.exit_code = 1
    await service.refresh(first["id"])

    second = await service.start("project-1", "run", chat_id="chat-1")

    assert terminal.removed == ["terminal-1"]
    assert second["terminalId"] == "terminal-2"
    assert second["status"] == "running"
    assert terminal.created[0]["owner_tool_call_id"] == terminal.created[1]["owner_tool_call_id"]
    assert terminal.created[1]["owner_tool_call_id"].startswith("workspace-action:")


@pytest.mark.asyncio
async def test_action_terminal_identity_recovers_orphans_and_avoids_manual_titles(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionActions": [{
            "id": "run", "label": "Run", "kind": "run",
            "program": sys.executable, "args": [], "cwd": ".",
        }],
    }
    terminal = FakeTerminal()
    owner_id = WorkspaceExecutionService._terminal_owner_id("project-1:run:.")
    terminal.terminals.extend([
        {
            "id": "orphan-action",
            "projectId": "project-1",
            "title": "Old action",
            "ownerToolCallId": owner_id,
        },
        {
            "id": "manual-run",
            "projectId": "project-1",
            "title": "Run",
            "ownerToolCallId": "",
        },
    ])
    service = _service(tmp_path, project, terminal)

    started = await service.start("project-1", "run")

    assert terminal.removed == ["orphan-action"]
    assert terminal.created[0]["owner_tool_call_id"] == owner_id
    assert terminal.created[0]["title"] == "Run (2)"
    assert started["terminalOwnerId"] == owner_id


@pytest.mark.asyncio
async def test_terminal_protocol_errors_are_returned_as_workspace_errors(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionActions": [{
            "id": "run", "label": "Run", "kind": "run",
            "program": sys.executable, "args": [], "cwd": ".",
        }],
    }
    terminal = FakeTerminal()
    terminal.create_error = TerminalRequestError(
        "Terminal request is invalid.", code="bad_request"
    )
    service = _service(tmp_path, project, terminal)

    with pytest.raises(WorkspaceExecutionError) as captured:
        await service.start("project-1", "run")

    assert captured.value.code == "terminal_bad_request"
    assert captured.value.status_code == 409


@pytest.mark.asyncio
async def test_workspace_review_excludes_cyrene_managed_git_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "app.js"
    source.write_text("export const message = 'before'\n")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "app.js"], cwd=workspace, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Cyrene", "-c",
            "user.email=cyrene@example.test", "commit", "-qm", "initial",
        ],
        cwd=workspace,
        check=True,
    )
    source.write_text("export const message = 'after'\n")
    internal = workspace / ".cyrene" / "conversations" / "chat.md"
    internal.parent.mkdir(parents=True)
    internal.write_text("internal conversation state\n")
    project = {"id": "project-1", "workspacePath": str(workspace)}
    service = _service(tmp_path, project, FakeTerminal())

    review = await service.review("project-1", "chat-1")

    assert "app.js" in review["git"]["status"]
    assert "app.js" in review["git"]["diff"]
    assert ".cyrene" not in review["git"]["status"]
    assert ".cyrene" not in review["git"]["diff"]


@pytest.mark.asyncio
async def test_execution_baseline_is_captured_before_process_launch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.py"
    source.write_text("before\n")
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionActions": [{
            "id": "run", "label": "Run", "kind": "run",
            "program": sys.executable, "args": [], "cwd": ".",
        }],
    }
    terminal = FakeTerminal()
    terminal.on_create = lambda: source.write_text("changed during launch\n")
    service = _service(tmp_path, project, terminal)

    running = await service.start("project-1", "run", chat_id="chat-1")
    terminal.status = "exited"
    terminal.exit_code = 0
    completed = await service.refresh(running["id"])

    assert completed["changeSet"]["fileCount"] == 1


@pytest.mark.asyncio
async def test_terminal_unavailability_does_not_fabricate_completion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionActions": [{
            "id": "run", "label": "Run", "kind": "run",
            "program": sys.executable, "args": [], "cwd": ".",
        }],
    }
    terminal = FakeTerminal()
    service = _service(tmp_path, project, terminal)
    running = await service.start("project-1", "run")
    terminal.screen_error = RuntimeError("terminal disconnected")

    unavailable = await service.refresh(running["id"])

    assert unavailable["status"] == "running"
    assert unavailable["statusReason"] == "terminal_unavailable"

    terminal.screen_error = None
    recovered = await service.refresh(running["id"])
    assert recovered["status"] == "running"
    assert "statusReason" not in recovered
    assert "terminalUnavailableSince" not in recovered


@pytest.mark.asyncio
async def test_terminal_unavailability_eventually_interrupts_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionActions": [{
            "id": "run", "label": "Run", "kind": "run",
            "program": sys.executable, "args": [], "cwd": ".",
        }],
    }
    terminal = FakeTerminal()
    service = _service(tmp_path, project, terminal)
    running = await service.start("project-1", "run")
    terminal.screen_error = RuntimeError("terminal permanently unavailable")
    monkeypatch.setattr(execution_module, "_TERMINAL_UNAVAILABLE_GRACE_SECONDS", 0.0)

    interrupted = await service.refresh(running["id"])

    assert interrupted["status"] == "interrupted"
    assert interrupted["statusReason"] == "terminal_unavailable"
    assert interrupted["finishedAt"]
    assert interrupted["changeSet"]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_exited_terminal_without_exit_code_is_interrupted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionActions": [{
            "id": "run", "label": "Run", "kind": "run",
            "program": sys.executable, "args": [], "cwd": ".",
        }],
    }
    terminal = FakeTerminal()
    service = _service(tmp_path, project, terminal)
    running = await service.start("project-1", "run")
    terminal.status = "exited"

    interrupted = await service.refresh(running["id"])

    assert interrupted["status"] == "interrupted"
    assert interrupted["statusReason"] == "exit_code_unavailable"


@pytest.mark.asyncio
async def test_marker_only_project_plugin_is_reported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "package.json").write_text("{}")
    project = {"id": "project-1", "workspacePath": str(workspace)}
    project_type = workspace_project_types(javascript_project_pack)[0]
    service = _service(
        tmp_path,
        project,
        FakeTerminal(),
        project_type_provider=lambda: ((javascript_project_pack.id, project_type),),
    )

    payload = await service.discover("project-1")

    assert payload["actions"] == []
    assert payload["projectTypes"] == ["cyrene_project_javascript/javascript"]


@pytest.mark.asyncio
async def test_action_availability_uses_agent_process_path(tmp_path: Path, monkeypatch) -> None:
    from cyrene.plugins.builtin.cyrene_extensions import extension_service

    workspace = tmp_path / "workspace"
    binaries = tmp_path / "agent-bin"
    workspace.mkdir()
    binaries.mkdir()
    executable = binaries / "managed-runtime"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    project = {
        "id": "project-1",
        "workspacePath": str(workspace),
        "executionActions": [{
            "id": "managed", "label": "Managed", "kind": "run",
            "program": "managed-runtime", "args": [], "cwd": ".",
        }],
    }
    monkeypatch.setattr(
        extension_service,
        "agent_process_environment",
        lambda base=None: {"PATH": str(binaries)},
    )
    service = _service(tmp_path, project, FakeTerminal())

    payload = await service.discover("project-1")

    assert payload["actions"][0]["available"] is True


@pytest.mark.asyncio
async def test_missing_workspace_is_rejected_instead_of_using_process_cwd(tmp_path: Path) -> None:
    project = {"id": "project-1", "workspacePath": ""}
    service = _service(tmp_path, project, FakeTerminal())
    service.resolve_workspace = lambda _project: ""

    with pytest.raises(WorkspaceExecutionError) as caught:
        await service.discover("project-1")
    assert caught.value.code == "workspace_unavailable"


def test_code_pack_exposes_generic_workspace_surface_actions_and_deliberate_reveal() -> None:
    surfaces = {item.id: item for item in workbench_surfaces(plugin_pack)}
    actions = {item.id: item for item in workspace_actions(plugin_pack)}

    assert surfaces["file-editor"].renderer.id == "workspace-composite"
    assert set(actions) == {"build", "run", "test", "preview"}
    assert TOOL_METADATA["resource_effects"][0]["argument_path"] == ("current_path",)
