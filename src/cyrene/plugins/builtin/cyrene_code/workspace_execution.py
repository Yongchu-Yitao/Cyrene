"""Plugin-owned workspace action discovery and managed execution service."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import inspect
import json
import logging
import re
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from cyrene.localization import app_language
from cyrene.core.plugin_boundary import PLUGIN_BOUNDARY_ERRORS
from cyrene.platform.paths import CYRENE_DIR_NAME
from cyrene.workbench.projects.project_execution import normalize_execution_actions
from cyrene.plugins import WorkspaceProjectTypeContribution
from cyrene.plugins.project_types import nearest_scope
from cyrene.workbench.workspaces.workspace_changes import (
    build_change_set,
    capture_workspace_snapshot,
    list_chat_change_sets,
    save_change_set,
)
from .terminal.client import TerminalNotFoundError, TerminalRequestError

_ACTIVE = frozenset({"starting", "running", "ready"})
_DIAGNOSTIC = re.compile(
    r"^(?P<file>[^:\n]+\.[A-Za-z0-9]+):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?:(?P<severity>error|warning|info|note)\s*:?\s*)?(?P<message>.+)$",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)
_DETECTOR_TIMEOUT_SECONDS = 3.0
_TERMINAL_UNAVAILABLE_GRACE_SECONDS = 15.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_seconds(timestamp: Any) -> float:
    try:
        started = datetime.fromisoformat(str(timestamp or ""))
    except (TypeError, ValueError):
        return 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())


def _localized_action_label(action: Mapping[str, Any], language: str = "") -> str:
    locale = app_language(language)
    translations = action.get("i18n")
    if isinstance(translations, Mapping):
        fields = translations.get(locale)
        if isinstance(fields, Mapping) and str(fields.get("label") or "").strip():
            return str(fields["label"]).strip()
    return str(action.get("label") or action.get("id") or "Workspace action").strip()


class WorkspaceExecutionError(RuntimeError):
    def __init__(self, message: str, code: str, status_code: int = 400) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class WorkspaceExecutionService:
    """Resolve project actions and bind them to durable terminal sessions."""

    def __init__(
        self,
        *,
        db_path: str,
        state_path: Path,
        terminal_client: Any,
        find_project: Callable[[str], dict[str, Any] | None],
        resolve_workspace: Callable[[dict[str, Any] | None], str],
        project_type_provider: Callable[
            [], Sequence[tuple[str, WorkspaceProjectTypeContribution]]
        ] | None = None,
    ) -> None:
        self.db_path = str(db_path or "")
        self.state_path = Path(state_path)
        self.terminal = terminal_client
        self.find_project = find_project
        self.resolve_workspace = resolve_workspace
        self.project_type_provider = project_type_provider or (lambda: ())
        self._records: dict[str, dict[str, Any]] = {}
        self._baselines: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._load()

    @staticmethod
    def _terminal_error(exc: TerminalRequestError) -> WorkspaceExecutionError:
        code = str(exc.code or "unavailable")
        status_code = 409 if code == "bad_request" else 503
        return WorkspaceExecutionError(str(exc), f"terminal_{code}", status_code)

    def _load(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in raw.get("executions") or []:
            if not isinstance(item, Mapping) or not item.get("id"):
                continue
            record = dict(item)
            if str(record.get("status") or "") in _ACTIVE:
                record["status"] = "interrupted"
                record["statusReason"] = "application_restarted"
            self._records[str(record["id"])] = record

    def _persist(self) -> None:
        ordered = sorted(
            self._records.values(),
            key=lambda item: str(item.get("startedAt") or ""),
            reverse=True,
        )
        kept = [item for item in ordered if str(item.get("status") or "") in _ACTIVE]
        kept_ids = {str(item.get("id") or "") for item in kept}
        for item in ordered:
            item_id = str(item.get("id") or "")
            if item_id not in kept_ids and len(kept) < 200:
                kept.append(item)
                kept_ids.add(item_id)
        self._records = {str(item["id"]): item for item in kept}
        self._baselines = {
            key: value for key, value in self._baselines.items() if key in self._records
        }
        payload = {
            "version": 1,
            "executions": [dict(item) for item in kept],
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def _project(self, project_id: str) -> tuple[dict[str, Any], Path]:
        project = self.find_project(str(project_id or ""))
        if not project:
            raise WorkspaceExecutionError("Project not found.", "project_not_found", 404)
        raw_workspace = str(self.resolve_workspace(project) or "").strip()
        if not raw_workspace:
            raise WorkspaceExecutionError(
                "Project workspace is unavailable.", "workspace_unavailable", 409
            )
        workspace = Path(raw_workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise WorkspaceExecutionError(
                "Project workspace is unavailable.", "workspace_unavailable", 409
            )
        return project, workspace

    async def _plugin_actions(
        self,
        workspace: Path,
        current_path: str,
    ) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
        actions: list[dict[str, Any]] = []
        matched: list[str] = []
        errors: list[dict[str, str]] = []
        contributions = sorted(
            tuple(self.project_type_provider()),
            key=lambda item: (item[1].priority, item[0], item[1].id),
        )
        for pack_id, contribution in contributions:
            canonical_id = f"{pack_id}/{contribution.id}"
            try:
                if inspect.iscoroutinefunction(contribution.detect):
                    raw = await asyncio.wait_for(
                        contribution.detect(workspace, current_path),
                        timeout=_DETECTOR_TIMEOUT_SECONDS,
                    )
                else:
                    raw = await asyncio.wait_for(
                        asyncio.to_thread(
                            contribution.detect,
                            workspace,
                            current_path,
                        ),
                        timeout=_DETECTOR_TIMEOUT_SECONDS,
                    )
                    if inspect.isawaitable(raw):
                        raw = await asyncio.wait_for(
                            raw,
                            timeout=_DETECTOR_TIMEOUT_SECONDS,
                        )
                normalized = normalize_execution_actions(raw)
            except PLUGIN_BOUNDARY_ERRORS as exc:
                logger.exception("Workspace project detector failed: %s", canonical_id)
                errors.append({"projectType": canonical_id, "error": str(exc)})
                continue
            marker_match = nearest_scope(
                workspace,
                current_path,
                *contribution.marker_files,
            ) is not None if contribution.marker_files else False
            if marker_match or normalized:
                matched.append(canonical_id)
            actions.extend({
                **item,
                "source": f"project-plugin:{canonical_id}",
                "projectType": canonical_id,
            } for item in normalized)
        return actions, matched, errors

    def _file_actions(self, workspace: Path) -> list[dict[str, Any]]:
        config = workspace / ".cyrene" / "workspace-actions.json"
        try:
            value = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        try:
            actions = normalize_execution_actions(value.get("actions") if isinstance(value, Mapping) else value)
        except ValueError:
            return []
        return [{**item, "source": "workspace-config"} for item in actions]

    async def discover(self, project_id: str, current_path: str = "") -> dict[str, Any]:
        project, workspace = self._project(project_id)
        effective_path = str(current_path or project.get("executionScope") or ".")
        plugin_actions, project_types, detector_errors = await self._plugin_actions(
            workspace, effective_path
        )
        detected = [*plugin_actions, *self._file_actions(workspace)]
        by_id = {item["id"]: item for item in detected}
        for item in normalize_execution_actions(project.get("executionActions")):
            by_id[item["id"]] = item
        actions = list(by_id.values())
        from cyrene.plugins.builtin.cyrene_extensions.extension_service import (
            agent_process_environment,
        )

        search_path = agent_process_environment().get("PATH", "")
        for item in actions:
            program = str(item["program"])
            if "/" in program or "\\" in program:
                program_path = Path(program).expanduser()
                candidate = (
                    program_path.resolve()
                    if program_path.is_absolute()
                    else (workspace / str(item.get("cwd") or ".") / program_path).resolve()
                )
                item["available"] = bool(
                    candidate.is_file()
                    and (program_path.is_absolute() or candidate.is_relative_to(workspace))
                    and candidate.stat().st_mode & 0o111
                )
            else:
                item["available"] = bool(shutil.which(program, path=search_path))
            item["scope"] = item.get("cwd") or "."
        actions.sort(key=lambda item: (
            {"run": 0, "build": 1, "test": 2, "preview": 3}.get(item["kind"], 9),
            item["label"].casefold(),
        ))
        return {
            "projectId": project_id,
            "workspacePath": str(workspace),
            "currentPath": effective_path,
            "executionScope": str(project.get("executionScope") or "."),
            "projectTypes": project_types,
            "detectorErrors": detector_errors,
            "actions": actions,
        }

    async def _resolve_action(
        self, project_id: str, action_id: str, current_path: str
    ) -> tuple[dict[str, Any], Path]:
        _project, workspace = self._project(project_id)
        discovered = await self.discover(project_id, current_path)
        action = next(
            (item for item in discovered["actions"] if item["id"] == action_id), None
        )
        if action is None or action.get("disabled"):
            raise WorkspaceExecutionError("Workspace action not found.", "action_not_found", 404)
        if not action.get("available"):
            raise WorkspaceExecutionError(
                f"Program is unavailable: {action['program']}", "program_unavailable", 409
            )
        return action, workspace

    @staticmethod
    def _expand_args(action: Mapping[str, Any], current_path: str) -> list[str]:
        safe_path = str(current_path or "").replace("\\", "/")
        return [str(item).replace("{file}", safe_path) for item in action.get("args") or []]

    @staticmethod
    def _terminal_owner_id(key: str) -> str:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:24]
        return f"workspace-action:{digest}"

    @staticmethod
    def _terminal_title(base_title: str, terminals: Sequence[Mapping[str, Any]]) -> str:
        base = str(base_title or "Workspace action").strip()[:60] or "Workspace action"
        occupied = {
            str(item.get("title") or "").strip().casefold()
            for item in terminals
            if str(item.get("title") or "").strip()
        }
        if base.casefold() not in occupied:
            return base
        copy_number = 2
        while True:
            suffix = f" ({copy_number})"
            candidate = base[: 60 - len(suffix)].rstrip() + suffix
            if candidate.casefold() not in occupied:
                return candidate
            copy_number += 1

    async def _replace_action_terminal(
        self,
        *,
        project_id: str,
        action_id: str,
        action: Mapping[str, Any],
        workspace: Path,
        cwd: Path,
        current_path: str,
        chat_id: str,
        key: str,
        terminal_owner_id: str,
    ) -> tuple[dict[str, Any], str, Any]:
        try:
            listed = await self.terminal.list(project_id)
        except TerminalRequestError as exc:
            raise self._terminal_error(exc) from exc
        project_terminals = [
            dict(item)
            for item in listed.get("terminals") or []
            if isinstance(item, Mapping)
        ]
        previous_terminal_ids = {
            str(item.get("terminalId") or "")
            for item in self._records.values()
            if item.get("key") == key and item.get("terminalId")
        }
        previous_terminal_ids.update(
            str(item.get("id") or "")
            for item in project_terminals
            if str(item.get("ownerToolCallId") or "") == terminal_owner_id
            and item.get("id")
        )
        for terminal_id in previous_terminal_ids:
            try:
                await self.terminal.remove(terminal_id)
            except TerminalNotFoundError:
                pass
            except TerminalRequestError as exc:
                raise self._terminal_error(exc) from exc
        command = shlex.join(
            [str(action["program"]), *self._expand_args(action, current_path)]
        )
        baseline = await asyncio.to_thread(capture_workspace_snapshot, workspace)
        remaining_terminals = [
            item
            for item in project_terminals
            if str(item.get("id") or "") not in previous_terminal_ids
        ]
        try:
            created = await self.terminal.create_agent_terminal(
                project_id,
                owner_chat_id=str(chat_id or "workspace"),
                title=self._terminal_title(
                    _localized_action_label(action), remaining_terminals
                ),
                cwd=str(cwd),
                command=command,
                wake_on_exit=True,
                wake_note=f"Workspace action {action_id} completed.",
                owner_tool_call_id=terminal_owner_id,
            )
        except TerminalRequestError as exc:
            raise self._terminal_error(exc) from exc
        return dict(created.get("terminal") or {}), command, baseline

    @staticmethod
    def _execution_record(
        *,
        execution_id: str,
        key: str,
        project_id: str,
        action_id: str,
        action: Mapping[str, Any],
        current_path: str,
        chat_id: str,
        goal_id: str,
        terminal: Mapping[str, Any],
        terminal_owner_id: str,
        command: str,
    ) -> dict[str, Any]:
        return {
            "id": execution_id,
            "key": key,
            "projectId": project_id,
            "actionId": action_id,
            "action": dict(action),
            "currentPath": current_path,
            "chatId": chat_id,
            "goalId": goal_id,
            "owner": "goal" if goal_id else "user",
            "terminalId": str(terminal.get("id") or ""),
            "terminalOwnerId": terminal_owner_id,
            "status": "running",
            "startedAt": _now(),
            "updatedAt": _now(),
            "finishedAt": "",
            "exitCode": None,
            "diagnostics": [],
            "artifacts": [],
            "endpoints": [],
            "changeSet": None,
            "commandSummary": command,
        }

    async def start(
        self,
        project_id: str,
        action_id: str,
        *,
        current_path: str = "",
        chat_id: str = "",
        goal_id: str = "",
        force: bool = False,
    ) -> dict[str, Any]:
        action, workspace = await self._resolve_action(project_id, action_id, current_path)
        key = f"{project_id}:{action_id}:{action.get('cwd') or '.'}"
        terminal_owner_id = self._terminal_owner_id(key)
        async with self._lock:
            existing = next(
                (item for item in self._records.values() if item.get("key") == key and item.get("status") in _ACTIVE),
                None,
            )
            if existing and not force:
                return await self.refresh(str(existing["id"]))
            cwd = (workspace / str(action.get("cwd") or ".")).resolve()
            if not cwd.is_dir() or not cwd.is_relative_to(workspace):
                raise WorkspaceExecutionError("Action working directory is invalid.", "invalid_action_cwd")
            execution_id = "execution_" + uuid4().hex[:16]
            terminal, command, baseline = await self._replace_action_terminal(
                project_id=project_id,
                action_id=action_id,
                action=action,
                workspace=workspace,
                cwd=cwd,
                current_path=current_path,
                chat_id=chat_id,
                key=key,
                terminal_owner_id=terminal_owner_id,
            )
            record = self._execution_record(
                execution_id=execution_id,
                key=key,
                project_id=project_id,
                action_id=action_id,
                action=action,
                current_path=current_path,
                chat_id=chat_id,
                goal_id=goal_id,
                terminal=terminal,
                terminal_owner_id=terminal_owner_id,
                command=command,
            )
            self._records[execution_id] = record
            self._baselines[execution_id] = baseline
            self._persist()
        return await self.refresh(execution_id)

    @staticmethod
    def _diagnostics(text: str, project_root: Path) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for raw in text.splitlines()[-2000:]:
            line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw).strip()
            match = _DIAGNOSTIC.match(line)
            if not match:
                continue
            path = match.group("file").strip().replace("\\", "/")
            candidate = Path(path)
            if candidate.is_absolute():
                try:
                    path = candidate.resolve().relative_to(project_root).as_posix()
                except ValueError:
                    continue
            key = (path, match.group("line"), match.group("column"), match.group("message"))
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "severity": (match.group("severity") or "error").lower(),
                "message": match.group("message").strip(),
                "file": path,
                "line": int(match.group("line")),
                "column": int(match.group("column") or 1),
                "source": "workspace-action",
            })
        return result[:500]

    @staticmethod
    def _artifacts(action: Mapping[str, Any], workspace: Path) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for pattern in action.get("artifactPatterns") or []:
            for target in workspace.glob(str(pattern)):
                if not target.is_file() or not target.resolve().is_relative_to(workspace):
                    continue
                relative = target.resolve().relative_to(workspace).as_posix()
                if any(item["path"] == relative for item in found):
                    continue
                found.append({
                    "kind": "artifact",
                    "path": relative,
                    "name": target.name,
                    "size": target.stat().st_size,
                })
        return found[:100]

    async def refresh(self, execution_id: str) -> dict[str, Any]:
        record = self._records.get(str(execution_id or ""))
        if record is None:
            raise WorkspaceExecutionError("Execution not found.", "execution_not_found", 404)
        _project, workspace = self._project(str(record["projectId"]))
        try:
            snapshot = await self.terminal.screen(str(record.get("terminalId") or ""))
        except Exception:
            unavailable_since = str(record.get("terminalUnavailableSince") or "")
            if not unavailable_since:
                unavailable_since = _now()
                record["terminalUnavailableSince"] = unavailable_since
            record["statusReason"] = "terminal_unavailable"
            record["updatedAt"] = _now()
            if _elapsed_seconds(unavailable_since) >= _TERMINAL_UNAVAILABLE_GRACE_SECONDS:
                record["status"] = "interrupted"
                record["finishedAt"] = record["updatedAt"]
                record["exitCode"] = None
                await self._finalize_change_set(record, workspace)
            self._persist()
            return dict(record)
        record.pop("terminalUnavailableSince", None)
        if record.get("statusReason") == "terminal_unavailable":
            record.pop("statusReason", None)
        terminal = dict(snapshot.get("terminal") or snapshot)
        screen_text = str(snapshot.get("screenText") or "")
        terminal_status = str(terminal.get("status") or "")
        if terminal_status in {"starting", "running"}:
            record["status"] = "running"
            ready_pattern = str(record.get("action", {}).get("readyPattern") or "")
            if ready_pattern:
                try:
                    if re.search(ready_pattern, screen_text, re.MULTILINE):
                        record["status"] = "ready"
                except re.error:
                    pass
            port = record.get("action", {}).get("previewPort")
            if port:
                try:
                    _reader, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", int(port)), timeout=0.2
                    )
                    writer.close()
                    await writer.wait_closed()
                    record["status"] = "ready"
                except (OSError, asyncio.TimeoutError):
                    pass
        else:
            exit_code = terminal.get("exitCode")
            if exit_code is None:
                record["status"] = "interrupted"
                record["statusReason"] = "exit_code_unavailable"
            else:
                record["status"] = "completed" if int(exit_code) == 0 else "failed"
                record.pop("statusReason", None)
            record["exitCode"] = exit_code
            record["finishedAt"] = str(terminal.get("exitAt") or _now())
        record["updatedAt"] = _now()
        record["screenText"] = screen_text[-262144:]
        record["diagnostics"] = self._diagnostics(screen_text, workspace)
        record["artifacts"] = self._artifacts(record.get("action") or {}, workspace)
        port = record.get("action", {}).get("previewPort")
        record["endpoints"] = ([{
            "kind": "endpoint", "url": f"http://127.0.0.1:{int(port)}",
            "label": f"localhost:{int(port)}", "primary": True,
        }] if port else [])
        await self._finalize_change_set(record, workspace)
        self._persist()
        return dict(record)

    async def _finalize_change_set(
        self,
        record: dict[str, Any],
        workspace: Path,
    ) -> None:
        if (
            record.get("status") not in {"completed", "failed", "interrupted"}
            or record.get("changeSet") is not None
        ):
            return
        before = self._baselines.pop(str(record["id"]), None)
        if before is None:
            return
        after = await asyncio.to_thread(capture_workspace_snapshot, workspace)
        changes = await asyncio.to_thread(
            build_change_set,
            chat_id=str(record.get("chatId") or ""),
            run_id=str(record["id"]),
            before=before,
            after=after,
            status=str(record["status"]),
        )
        if record.get("chatId") and changes.get("fileCount"):
            await asyncio.to_thread(save_change_set, self.db_path, changes)
        record["changeSet"] = {
            key: value
            for key, value in changes.items()
            if key not in {"files", "workspacePath"}
        }
        record["changeSet"]["files"] = [
            {key: value for key, value in item.items() if key != "diff"}
            for item in changes.get("files") or []
        ]

    async def list(self, project_id: str) -> dict[str, Any]:
        records = [
            (
                await self.refresh(str(item["id"]))
                if str(item.get("status") or "") in _ACTIVE | {"stopping"}
                else dict(item)
            )
            for item in list(self._records.values())
            if str(item.get("projectId") or "") == str(project_id or "")
        ]
        records.sort(key=lambda item: str(item.get("startedAt") or ""), reverse=True)
        return {"projectId": project_id, "executions": records}

    @staticmethod
    async def _git_command(workspace: Path, *arguments: str) -> tuple[int, str]:
        try:
            process = await asyncio.create_subprocess_exec(
                "git", *arguments,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError:
            return 127, ""
        output, _ = await process.communicate()
        return int(process.returncode or 0), output.decode("utf-8", errors="replace")

    @classmethod
    async def _git_review(cls, workspace: Path) -> dict[str, Any]:
        inside_code, inside = await cls._git_command(
            workspace, "rev-parse", "--is-inside-work-tree"
        )
        if inside_code or inside.strip() != "true":
            return {"available": False, "hasChanges": False, "status": "", "diff": ""}
        review_paths = (".", f":(top,exclude){CYRENE_DIR_NAME}/**")
        _status_code, status = await cls._git_command(
            workspace, "status", "--short", "--untracked-files=all", "--", *review_paths
        )
        _diff_code, tracked_diff = await cls._git_command(
            workspace,
            "diff", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/",
            "--", *review_paths,
        )
        _staged_code, staged_diff = await cls._git_command(
            workspace,
            "diff", "--cached", "--no-ext-diff", "--src-prefix=a/",
            "--dst-prefix=b/", "--", *review_paths,
        )
        untracked_paths = [
            line[3:] for line in status.splitlines()
            if line.startswith("?? ") and line[3:].strip()
        ]
        untracked_diffs: list[str] = []
        for relative in untracked_paths[:100]:
            target = (workspace / relative).resolve()
            if not target.is_file() or not target.is_relative_to(workspace):
                continue
            try:
                if target.stat().st_size > 1_000_000:
                    continue
                text = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            untracked_diffs.append("".join(difflib.unified_diff(
                [], text.splitlines(keepends=True),
                fromfile="/dev/null", tofile=f"b/{relative}",
            )))
        sections = [value for value in (staged_diff, tracked_diff, *untracked_diffs) if value]
        diff = "\n".join(sections)
        if len(diff) > 4_000_000:
            diff = diff[:4_000_000] + "\n… diff truncated …\n"
        return {
            "available": True,
            "hasChanges": bool(status.strip()),
            "status": status,
            "diff": diff,
        }

    async def review(self, project_id: str, chat_id: str = "") -> dict[str, Any]:
        """Return both durable Cyrene snapshots and the current Git worktree diff."""
        _project, workspace = self._project(project_id)
        change_sets = (
            await asyncio.to_thread(list_chat_change_sets, self.db_path, chat_id)
            if chat_id else []
        )
        return {
            "projectId": project_id,
            "chatId": chat_id,
            "snapshot": {
                "changeSets": change_sets,
                "fileCount": sum(int(item.get("fileCount") or 0) for item in change_sets),
                "additions": sum(int(item.get("additions") or 0) for item in change_sets),
                "deletions": sum(int(item.get("deletions") or 0) for item in change_sets),
            },
            "git": await self._git_review(workspace),
        }

    async def stop(self, execution_id: str) -> dict[str, Any]:
        record = self._records.get(str(execution_id or ""))
        if record is None:
            raise WorkspaceExecutionError("Execution not found.", "execution_not_found", 404)
        if str(record.get("status") or "") in _ACTIVE:
            await self.terminal.interrupt(str(record.get("terminalId") or ""))
            record["status"] = "stopping"
            record["updatedAt"] = _now()
            self._persist()
        return await self.refresh(execution_id)

    async def restart(self, execution_id: str) -> dict[str, Any]:
        record = self._records.get(str(execution_id or ""))
        if record is None:
            raise WorkspaceExecutionError("Execution not found.", "execution_not_found", 404)
        if str(record.get("status") or "") in _ACTIVE:
            await self.terminal.interrupt(str(record.get("terminalId") or ""))
        return await self.start(
            str(record["projectId"]),
            str(record["actionId"]),
            current_path=str(record.get("currentPath") or ""),
            chat_id=str(record.get("chatId") or ""),
            goal_id=str(record.get("goalId") or ""),
            force=True,
        )

    async def claim(self, execution_id: str) -> dict[str, Any]:
        record = self._records.get(str(execution_id or ""))
        if record is None:
            raise WorkspaceExecutionError("Execution not found.", "execution_not_found", 404)
        record["owner"] = "user"
        record["goalId"] = ""
        record["updatedAt"] = _now()
        self._persist()
        return dict(record)

    async def stop_goal(self, goal_id: str) -> int:
        targets = [
            item for item in self._records.values()
            if item.get("owner") == "goal" and item.get("goalId") == goal_id
            and item.get("status") in _ACTIVE
        ]
        for item in targets:
            await self.stop(str(item["id"]))
        return len(targets)

    async def shutdown(self) -> None:
        for item in list(self._records.values()):
            if item.get("status") in _ACTIVE:
                try:
                    await self.terminal.interrupt(str(item.get("terminalId") or ""))
                except Exception:
                    pass


__all__ = [
    "WorkspaceExecutionError",
    "WorkspaceExecutionService",
]
