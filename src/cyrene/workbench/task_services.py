"""Task and artifact application services for Workbench HTTP adapters."""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from cyrene.localization import app_language, localized, localized_plural
from cyrene.workbench.task_dto import (
    ProjectShellDTO,
    TaskSessionDTO,
    TaskSessionViewDTO,
    WorkspacePathStatusDTO,
)


def _l(en: str, zh: str, **values: Any) -> str:
    return localized(en, zh, **values)


class TaskSessionNotFoundError(LookupError):
    pass


@dataclass(slots=True)
class TaskMutationError(Exception):
    message: str
    status_code: int
    code: str = ""
    category: str = ""

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class TaskRouteDependencies:
    """Explicit Task repository and pure-domain dependencies."""

    read_store: Any
    find_session: Any
    project_shell: Any
    workspace_root: Any
    write_store: Any
    utc_now: Any
    artifact_download_target: Any
    store_lock: Any
    short_id: Any
    recorded_diff: Any
    git_diff: Any
    prune_artifacts: Any
    plan_signature: Any
    normalize_plan: Any
    validate_plan: Any
    mark_completed: Any
    store_reflection: Any
    reflection_candidates: Any
    apply_reflection_hints: Any
    merge_hint_mutations: Any
    new_session: Any
    default_init_form: Any
    init_brief: Any
    coerce_init_plan: Any
    fallback_init_plan: Any
    create_sessions_from_init_plan: Any
    update_task_plan: Any
    notify: Any

    @classmethod
    def from_modules(cls, db_path: str) -> "TaskRouteDependencies":
        from cyrene.workbench import (
            artifact_runtime,
            notifications,
            planning_runtime,
            project_repository,
            project_runtime,
            task_initialization_runtime,
        )

        project_repository._configure_workbench_store(str(db_path or ""))
        return cls(
            read_store=project_repository._read_workbench_store,
            find_session=project_repository._workbench_find_session,
            project_shell=project_repository._workbench_project_shell,
            workspace_root=artifact_runtime._workbench_workspace_root,
            write_store=project_repository._write_workbench_store,
            utc_now=project_runtime._utc_now_iso,
            artifact_download_target=artifact_runtime._workbench_artifact_download_target,
            store_lock=project_repository._WORKBENCH_STORE_LOCK,
            short_id=project_runtime._short_id,
            recorded_diff=artifact_runtime._workbench_recorded_diff_for_path,
            git_diff=artifact_runtime._workbench_git_diff_for_path,
            prune_artifacts=artifact_runtime._workbench_prune_non_file_artifacts,
            plan_signature=planning_runtime._workbench_plan_definition_signature,
            normalize_plan=planning_runtime._workbench_normalize_plan,
            validate_plan=planning_runtime._workbench_validate_plan_graph,
            mark_completed=project_runtime._workbench_mark_completed_if_acceptance_passed,
            store_reflection=planning_runtime._workbench_store_reflection,
            reflection_candidates=planning_runtime._workbench_reflection_candidates,
            apply_reflection_hints=planning_runtime._workbench_apply_reflection_hints,
            merge_hint_mutations=planning_runtime._workbench_merge_hint_mutations,
            new_session=project_runtime._workbench_new_session,
            default_init_form=project_runtime._workbench_default_init_form,
            init_brief=task_initialization_runtime._workbench_init_brief,
            coerce_init_plan=task_initialization_runtime._workbench_coerce_init_task_plan,
            fallback_init_plan=task_initialization_runtime._workbench_fallback_init_task_plan,
            create_sessions_from_init_plan=task_initialization_runtime._workbench_create_sessions_from_init_plan,
            update_task_plan=project_repository.update_task_plan_for_session,
            notify=notifications.append_notification,
        )


@dataclass(slots=True)
class PlanningMutationError(Exception):
    message: str
    status_code: int
    code: str = ""

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    path: Path
    filename: str
    media_type: str


class TaskApplicationService:
    """Read-side task operations shared by HTTP and future local adapters."""

    def __init__(
        self,
        *,
        read_store: Callable[[], dict[str, Any]],
        find_session: Callable[
            [dict[str, Any], str],
            tuple[dict[str, Any] | None, dict[str, Any] | None],
        ],
        project_shell: Callable[[dict[str, Any] | None], dict[str, Any] | None],
        workspace_root: Callable[[dict[str, Any] | None], Path | None],
        write_store: Callable[[dict[str, Any]], None] | None = None,
        utc_now: Callable[[], str] | None = None,
        prune_artifacts: Callable[[dict[str, Any]], bool] | None = None,
        plan_signature: Callable[[Any], str] | None = None,
        normalize_plan: Callable[..., list[dict[str, Any]]] | None = None,
        validate_plan: Callable[[list[dict[str, Any]]], tuple[bool, str, str]] | None = None,
        mark_completed: Callable[..., bool] | None = None,
        notify: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._read_store = read_store
        self._find_session = find_session
        self._project_shell = project_shell
        self._workspace_root = workspace_root
        self._write_store = write_store
        self._utc_now = utc_now
        self._prune_artifacts = prune_artifacts
        self._plan_signature = plan_signature
        self._normalize_plan = normalize_plan
        self._validate_plan = validate_plan
        self._mark_completed = mark_completed
        self._notify = notify

    def read(self, session_id: str) -> TaskSessionViewDTO:
        project, session = self._session(session_id)
        return {
            "projectId": str(project.get("id") or "") if project else "",
            "project": cast(ProjectShellDTO | None, self._project_shell(project)),
            "session": cast(TaskSessionDTO, session),
        }

    def events(self, session_id: str) -> list[dict[str, Any]]:
        _project, session = self._session(session_id)
        events = session.get("events")
        return events if isinstance(events, list) else []

    def workspace_path_status(
        self,
        session_id: str,
        requested_path: str,
    ) -> WorkspacePathStatusDTO:
        project, _session = self._session(session_id, require_project=True)
        root = self._workspace_root(project)
        if not root:
            raise ValueError(_l(
                "No workspace is configured.", "尚未配置工作区。"
            ))
        raw = str(requested_path or "").strip()
        if not raw:
            return {"exists": False, "path": "", "isDir": False}
        try:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = candidate.resolve()
            relative = resolved.relative_to(root).as_posix()
        except (ValueError, OSError):
            return {
                "exists": False,
                "path": raw,
                "error": _l(
                    "The path is outside the workspace.",
                    "路径不在工作区内。",
                ),
            }
        exists = resolved.exists()
        return {
            "exists": exists,
            "path": relative,
            "isDir": resolved.is_dir() if exists else False,
        }

    def update(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Apply a user-authored task mutation and persist it atomically."""
        if not all((self._write_store, self._utc_now, self._prune_artifacts,
                    self._plan_signature, self._normalize_plan,
                    self._validate_plan, self._mark_completed)):
            raise RuntimeError("task mutation dependencies are not configured")
        payload = self._read_store()
        project, session = self._find_session(payload, session_id)
        if not session or not project:
            raise TaskSessionNotFoundError(_l(
                "Session not found.", "未找到会话。"
            ))
        previous_status = str(session.get("status") or "")
        requested_status = str(body.get("status") or "")
        if requested_status == "paused" and previous_status not in {
            "running", "waiting_for_user",
        }:
            raise TaskMutationError(
                _l(
                    "Only an active task can be paused.",
                    "只有正在执行的任务可以暂停。",
                ),
                409,
                "invalid_status_transition",
            )
        if "title" in body and str(body.get("title") or "").strip():
            session["titleLocked"] = True
        for field in (
            "title", "goal", "status", "priority", "agentReply", "summary",
            "kind", "approvedPlanDefinitionRevision",
        ):
            if field in body:
                session[field] = body[field]
        for field in ("constraints", "events", "runs", "artifacts", "acceptanceCriteria"):
            if isinstance(body.get(field), list):
                session[field] = body[field]
        if isinstance(body.get("acceptanceCriteria"), list) and previous_status == "failed":
            session.update({
                "status": "review",
                "verifyReason": "",
                "recommendReflection": False,
                "agentReply": _l(
                    "Acceptance criteria changed. Run acceptance again.",
                    "验收条件已修改，请重新验收。",
                ),
            })
        self._prune_artifacts(session)
        if isinstance(body.get("plan"), list):
            previous_definition = self._plan_signature(session.get("plan"))
            next_plan = self._normalize_plan(body["plan"], task_id=session_id)
            valid, message, code = self._validate_plan(next_plan)
            if not valid:
                raise TaskMutationError(message, 400, code)
            session["plan"] = next_plan
            session["planRevision"] = int(session.get("planRevision") or 0) + 1
            if self._plan_signature(next_plan) != previous_definition:
                session["planDefinitionRevision"] = int(
                    session.get("planDefinitionRevision") or 0
                ) + 1
                session["approvedPlanDefinitionRevision"] = None
        if isinstance(body.get("init"), dict):
            session["init"] = {**(session.get("init") or {}), **body["init"]}
        now = self._utc_now()
        self._mark_completed(session, now=now)
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        self._write_store(payload)
        next_status = str(session.get("status") or "")
        if self._notify and next_status != previous_status and next_status in {
            "done", "completed", "failed", "blocked", "paused", "review",
        }:
            language = app_language()
            titles = {
                "done": localized("Task completed", "任务完成", language=language),
                "completed": localized("Task completed", "任务完成", language=language),
                "failed": localized("Task failed", "任务失败", language=language),
                "blocked": localized("Task blocked", "任务阻塞", language=language),
                "paused": localized("Task paused", "任务已暂停", language=language),
                "review": localized("Task ready for review", "任务待验收", language=language),
            }
            labels = {
                "done": localized("completed", "已完成", language=language),
                "completed": localized("completed", "已完成", language=language),
                "failed": localized("failed", "失败", language=language),
                "blocked": localized("blocked", "阻塞", language=language),
                "paused": localized("paused", "已暂停", language=language),
                "review": localized("ready for review", "待验收", language=language),
            }
            task_title = session.get('title') or localized(
                "Untitled task", "未命名任务", language=language
            )
            self._notify(
                title=titles.get(next_status, localized(
                    "Task status updated", "任务状态更新", language=language
                )),
                body=localized(
                    'Task "{title}" is now {status}.',
                    '任务「{title}」当前状态：{status}。',
                    language=language,
                    title=task_title,
                    status=labels.get(next_status, next_status),
                ),
                tab="system" if next_status != "review" else "comment",
                project_ref=project.get("id"), source="task_status",
                source_label=localized("Task", "任务", language=language),
                link_label=str(session.get("title") or ""),
                meta={"sessionId": session_id, "status": next_status},
                language=language,
            )
        return {"ok": True, "project": project, "session": session, **payload}

    async def delete(
        self,
        session_id: str,
        *,
        db_path: str,
        task_runs: Any,
        goal_loops: Any,
        agent_runtime: Any,
        store_lock: AbstractContextManager[Any],
    ) -> dict[str, Any]:
        """Stop every writer before durably removing a task session."""
        if not self._write_store or not self._utc_now:
            raise RuntimeError("task mutation dependencies are not configured")
        payload = self._read_store()
        project, session = self._find_session(payload, session_id)
        if not session or not project:
            raise TaskSessionNotFoundError(_l(
                "Session not found.", "未找到会话。"
            ))
        task_runs.interrupt_task_run(db_path, session_id)
        goal_run = await goal_loops._get_run_by_session(db_path, session_id)
        if goal_run and str(goal_run.get("status") or "") not in {
            "review", "completed", "cancelled",
        }:
            cancelled = await goal_loops._set_inactive_status(
                db_path, goal_run, "cancelled", phase="cancelled",
                stop_reason="session_deleted",
            )
            if cancelled:
                await goal_loops._event(
                    db_path, str(goal_run["id"]), "cancelled",
                    payload={"reason": "session_deleted"},
                )
                await goal_loops._publish(cancelled)
            manager = goal_loops._MANAGERS.get(str(db_path))
            worker = manager.tasks.get(str(goal_run["id"])) if manager else None
            if worker is not None and not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        for _attempt in range(100):
            if not task_runs.is_task_run_active(db_path, session_id):
                break
            await asyncio.sleep(0.05)
        if task_runs.is_task_run_active(db_path, session_id):
            raise TaskMutationError(
                _l(
                    "The task is still stopping. Try deleting it again shortly.",
                    "任务仍在停止中，请稍后重试删除。",
                ),
                409,
                "session_still_running",
            )
        await agent_runtime.clear_session(session_id)
        with store_lock:
            payload = self._read_store()
            project, session = self._find_session(payload, session_id)
            if not session or not project:
                return {"ok": True, **payload}
            project["sessions"] = [
                item for item in project.get("sessions", [])
                if str(item.get("id") or "") != session_id
            ]
            project["updatedAt"] = self._utc_now()
            if str(payload.get("activeSessionId") or "") == session_id:
                remaining = project.get("sessions") or []
                payload["activeSessionId"] = remaining[0]["id"] if remaining else ""
            self._write_store(payload)
        return {"ok": True, **payload}

    def _session(
        self,
        session_id: str,
        *,
        require_project: bool = False,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        payload = self._read_store()
        project, session = self._find_session(payload, session_id)
        if not session or (require_project and not project):
            raise TaskSessionNotFoundError(_l(
                "Session not found.", "未找到会话。"
            ))
        return project, session


class ArtifactApplicationService:
    """Task artifact listing and safe download resolution."""

    def __init__(
        self,
        *,
        read_store: Callable[[], dict[str, Any]],
        find_session: Callable[
            [dict[str, Any], str],
            tuple[dict[str, Any] | None, dict[str, Any] | None],
        ],
        resolve_download: Callable[
            [dict[str, Any], dict[str, Any], str], tuple[dict[str, Any], Path]
        ],
    ) -> None:
        self._read_store = read_store
        self._find_session = find_session
        self._resolve_download = resolve_download

    async def list(self, session_id: str) -> list[dict[str, Any]]:
        payload = self._read_store()
        _project, session = self._find_session(payload, session_id)
        if not session:
            raise TaskSessionNotFoundError(_l(
                "Session not found.", "未找到会话。"
            ))
        artifacts = session.get("artifacts")
        return artifacts if isinstance(artifacts, list) else []

    def download(self, session_id: str, artifact_id: str) -> ArtifactDownload:
        payload = self._read_store()
        project, session = self._find_session(payload, session_id)
        if not session or not project:
            raise TaskSessionNotFoundError(_l(
                "Session not found.", "未找到会话。"
            ))
        artifact, target = self._resolve_download(project, session, artifact_id)
        filename = Path(str(artifact.get("name") or target.name)).name or target.name
        return ArtifactDownload(
            path=target,
            filename=filename,
            media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        )

class PlanningApplicationService:
    """Serialize user-authored plan mutations through the canonical domain operation."""

    _status_by_code = {
        "session_not_found": 404,
        "step_not_found": 404,
        "plan_started": 409,
        "step_started": 409,
        "step_has_dependents": 409,
    }

    def __init__(
        self,
        *,
        lock: AbstractContextManager[Any],
        read_store: Callable[[], dict[str, Any]],
        find_session: Callable[
            [dict[str, Any], str],
            tuple[dict[str, Any] | None, dict[str, Any] | None],
        ],
        is_task_run_active: Callable[[str, str], bool],
        db_path: str,
        agent_runtime: Any,
        mutate_plan: Callable[..., dict[str, Any]],
        utc_now: Callable[[], str],
        short_id: Callable[[str], str],
        write_store: Callable[[dict[str, Any]], None],
        store_reflection: Callable[..., Any],
        reflection_candidates: Callable[..., list[dict[str, Any]]],
        apply_reflection_hints: Callable[..., Any],
        mark_completed: Callable[..., bool],
    ) -> None:
        self._lock = lock
        self._read_store = read_store
        self._find_session = find_session
        self._is_task_run_active = is_task_run_active
        self._db_path = db_path
        self._agent_runtime = agent_runtime
        self._mutate_plan = mutate_plan
        self._utc_now = utc_now
        self._short_id = short_id
        self._write_store = write_store
        self._store_reflection = store_reflection
        self._reflection_candidates = reflection_candidates
        self._apply_reflection_hints = apply_reflection_hints
        self._mark_completed = mark_completed

    def _session_record(
        self, session_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        payload = self._read_store()
        project, session = self._find_session(payload, session_id)
        if not project or not session:
            raise TaskSessionNotFoundError(_l(
                "Session not found.", "未找到会话。"
            ))
        return payload, project, session

    async def generate_acceptance(self, session_id: str) -> dict[str, Any]:
        payload, project, session = self._session_record(session_id)
        criteria, from_llm = await self._agent_runtime.generate_acceptance_criteria(
            session, project
        )
        session["acceptanceCriteria"] = criteria
        now = self._utc_now()
        session["events"] = list(session.get("events") or []) + [{
            "id": self._short_id("event"), "type": "AcceptanceGenerated",
            "createdAt": now,
            "body": localized_plural(
                "Generated {count} acceptance criterion.{fallback}",
                "Generated {count} acceptance criteria.{fallback}",
                "生成验收标准，共 {count} 条。{fallback}",
                count=len(criteria),
                fallback=(
                    ""
                    if from_llm
                    else _l(" (fallback criteria)", "（兜底标准）")
                ),
            ),
        }]
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        self._write_store(payload)
        return {"ok": True, "project": project, "session": session,
                "acceptanceSource": "llm" if from_llm else "fallback", **payload}

    async def reflect(self, session_id: str, *, focus: str, goal_gap: str) -> dict[str, Any]:
        payload, project, session = self._session_record(session_id)
        packet = await self._agent_runtime.reflect_task(
            session, project, focus=focus, goal_gap=goal_gap
        )
        if not packet:
            raise TaskMutationError(_l(
                "There is no history to reflect on.",
                "没有可供反思的历史记录。",
            ), 400)
        self._store_reflection(session, packet, trigger="manual", project=project)
        candidates = self._reflection_candidates(project, session)
        matches = await self._agent_runtime.reflection_hints(
            packet, candidates, session, project
        )
        self._apply_reflection_hints(project, session, packet, matches)
        now = self._utc_now()
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        self._write_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}

    async def verify(self, session_id: str) -> dict[str, Any]:
        payload, project, session = self._session_record(session_id)
        try:
            verdict = await self._agent_runtime.verify_acceptance(session, project)
        except Exception as exc:
            message = str(exc or _l("Model call failed", "模型调用失败"))
            category = str(getattr(exc, "code", "model") or "model")
            raise TaskMutationError(
                _l(
                    "Acceptance is temporarily unavailable: {message}",
                    "验收暂时不可用：{message}",
                    message=message,
                ), 503,
                "verification_unavailable", category,
            ) from exc
        if not isinstance(verdict, dict):
            raise TaskMutationError(
                _l(
                    "Acceptance is temporarily unavailable because the model returned no valid result.",
                    "验收暂时不可用：模型没有返回有效结果。",
                ), 503,
                "verification_unavailable", "response_format",
            )
        results = verdict.get("results") if isinstance(verdict.get("results"), list) else []
        by_id = {str(item.get("id")): item for item in results if isinstance(item, dict)}
        criteria = [item for item in session.get("acceptanceCriteria") or [] if isinstance(item, dict)]
        any_failed = False
        for criterion in criteria:
            result = by_id.get(str(criterion.get("id")))
            if not isinstance(result, dict):
                criterion.update(
                    status="failed",
                    evidence=_l(
                        "The verifier returned no conclusion for this criterion.",
                        "验收器未返回这一项的结论。",
                    ),
                )
                any_failed = True
                continue
            passed = bool(result.get("passed"))
            criterion["status"] = "passed" if passed else "failed"
            criterion["evidence"] = str(result.get("evidence") or "")
            any_failed = any_failed or not passed
        session["acceptanceCriteria"] = criteria
        now = self._utc_now()
        if any_failed:
            reason = str(verdict.get("reason") or "")
            session.update(status="failed", verifyReason=reason,
                           recommendReflection=bool(verdict.get("recommend_reflection")),
                           agentReply=_l(
                               "Independent acceptance failed: {reason}",
                               "独立验收未通过：{reason}",
                               reason=reason or _l(
                                   "Some acceptance criteria were not met.",
                                   "部分验收标准未达成。",
                               ),
                           ))
            event_type = "VerificationFailed"
            event_body = _l(
                "Independent acceptance failed. {reason}",
                "独立验收未通过。{reason}",
                reason=reason,
            )
        else:
            session.update(recommendReflection=False, verifyReason="",
                           agentReply=_l(
                               "Independent acceptance passed: all acceptance criteria were met.",
                               "独立验收通过：所有验收标准均已达成。",
                           ))
            event_type, event_body = "VerificationPassed", _l(
                "Independent acceptance passed; all criteria were met.",
                "独立验收通过，所有标准达成。",
            )
        session["events"] = list(session.get("events") or []) + [{
            "id": self._short_id("event"), "type": event_type,
            "createdAt": now, "body": event_body,
        }]
        if not any_failed:
            self._mark_completed(
                session, now=now,
                event_body=_l(
                    "Independent acceptance passed. All acceptance criteria passed, and the task was marked complete automatically.",
                    "独立验收通过，所有验收标准均已通过，任务自动标记为已完成。",
                ),
            )
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        self._write_store(payload)
        return {"ok": True, "verdict": verdict, "project": project,
                "session": session, **payload}

    def mutate(
        self,
        session_id: str,
        body: dict[str, Any],
        *,
        base_plan_revision: int,
    ) -> dict[str, Any]:
        operation = str(body.get("operation") or "").strip().lower()
        with self._lock:
            payload = self._read_store()
            project, session = self._find_session(payload, session_id)
            if not project or not session:
                raise PlanningMutationError(_l(
                    "Session not found.", "未找到会话。"
                ), 404)
            current_revision = int(session.get("planDefinitionRevision") or 0)
            if base_plan_revision != current_revision:
                raise PlanningMutationError(
                    _l(
                        "The plan changed. Refresh and try again.",
                        "计划已发生变化，请刷新后重试。",
                    ),
                    409,
                    "stale_plan_revision",
                )
            if (
                self._is_task_run_active(self._db_path, session_id)
                or str(session.get("status") or "") in {"running", "waiting_for_user"}
            ):
                raise PlanningMutationError(
                    _l(
                        "The Agent is running, so the plan cannot be edited yet.",
                        "Agent 正在执行，暂时不能修改计划。",
                    ),
                    409,
                    "plan_running",
                )
            result = self._mutate_plan(
                session_id,
                operation,
                step_id=str(body.get("stepId") or ""),
                step=body.get("step") if isinstance(body.get("step"), dict) else None,
                fields=body.get("fields") if isinstance(body.get("fields"), dict) else None,
                ordered_step_ids=(
                    body.get("orderedStepIds")
                    if isinstance(body.get("orderedStepIds"), list)
                    else None
                ),
                depends_on=(
                    body.get("dependsOn")
                    if isinstance(body.get("dependsOn"), list)
                    else None
                ),
                event_source="user",
            )
        if not result.get("ok"):
            code = str(result.get("code") or "")
            raise PlanningMutationError(
                str(result.get("error") or _l(
                    "Unsupported plan operation.",
                    "不支持的计划操作。",
                )),
                self._status_by_code.get(code, 400),
                "" if code in {"invalid_context_files", "unsupported_operation"} else code,
            )
        result.pop("plan", None)
        result.pop("planRevision", None)
        result.pop("planDefinitionRevision", None)
        return result


__all__ = [
    "ArtifactApplicationService",
    "ArtifactDownload",
    "PlanningApplicationService",
    "PlanningMutationError",
    "TaskApplicationService",
    "TaskMutationError",
    "TaskRouteDependencies",
    "TaskSessionNotFoundError",
]
