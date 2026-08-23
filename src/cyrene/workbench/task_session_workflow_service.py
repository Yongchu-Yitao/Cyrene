"""Application workflows shared by Workbench task-session adapters."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cyrene.workbench.task_execution_service import TaskExecutionResponse
from cyrene.workbench.task_services import TaskRouteDependencies


class TaskWorkspaceApplicationService:
    def __init__(self, dependencies: TaskRouteDependencies) -> None:
        self.dependencies = dependencies
        self.logger = logging.getLogger(__name__)

    async def file_diff(self, session_id: str, path: str) -> dict[str, Any] | TaskExecutionResponse:
        deps = self.dependencies
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": "session not found"}, 404)
        workspace_root = deps.workspace_root(project)
        recorded = deps.recorded_diff(session, path, workspace_root)
        if recorded and recorded.get("has_changes"):
            return recorded
        try:
            result = await deps.git_diff(workspace_root, path)
        except ValueError as exc:
            return TaskExecutionResponse({"error": str(exc)}, 400)
        except TimeoutError as exc:
            return TaskExecutionResponse({"error": str(exc)}, 504)
        except RuntimeError:
            self.logger.exception("Failed to compute Workbench diff for session %s", session_id)
            return TaskExecutionResponse({"error": "Diff failed", "code": "workbench_diff_failed"}, 500)
        if recorded and not (result.get("has_changes") and result.get("source") == "git"):
            return recorded
        return result


@dataclass(slots=True)
class PlanGenerationState:
    project: dict[str, Any]
    session: dict[str, Any]
    base_revision: int
    feedback: str
    auto_start: bool
    operation: str


class TaskPlanningWorkflowService:
    def __init__(self, dependencies: TaskRouteDependencies) -> None:
        self.dependencies = dependencies

    async def _prepare_generation(self, session_id: str, body: dict[str, Any]):
        deps = self.dependencies
        goal = str(body.get("goal") or "").strip()
        feedback = str(body.get("feedback") or "").strip()
        operation = str(body.get("operation") or "auto").strip().lower()
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": "session not found"}, 404)
        base_revision = int(session.get("planRevision") or 0)
        requested_revision = body.get("basePlanRevision")
        if requested_revision is not None:
            try:
                requested_revision = int(requested_revision)
            except (TypeError, ValueError):
                return TaskExecutionResponse({"error": "invalid basePlanRevision"}, 400)
            if requested_revision != base_revision:
                return TaskExecutionResponse({"error": "计划已发生变化，请基于最新计划重试。", "code": "stale_plan_revision"}, 409)
        if goal:
            session["goal"] = goal
            merged = list(session.get("constraints") or [])
            for item in await deps.extract_constraints(goal):
                if item not in merged:
                    merged.append(item)
            session["constraints"] = merged
        should_reflect = feedback and operation != "replace" and str(session.get("status") or "") in ("failed", "review")
        if should_reflect and await deps.should_reflect(str(session.get("goal") or ""), session.get("acceptanceCriteria") or [], feedback):
            packet = await deps.run_reflection(session_id, focus=feedback, goal_gap="用户对当前计划/结果不满意：" + feedback)
            if packet:
                deps.store_reflection(session, packet, trigger="feedback", project=project)
                await deps.dispatch_reflection_hints(project, session, packet)
        return PlanGenerationState(project, session, base_revision, feedback, bool(body.get("autoStart")), operation)

    def _persist_generated(self, session_id: str, state: PlanGenerationState, generated):
        deps = self.dependencies
        steps, acceptance, from_llm, operation = generated
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": "session not found"}, 404)
        if int(session.get("planRevision") or 0) != state.base_revision:
            return TaskExecutionResponse({"error": "计划已在生成期间发生变化，请基于最新计划重试。", "code": "stale_plan_revision"}, 409)
        if not (state.feedback and not from_llm):
            session.update(plan=steps, planRevision=state.base_revision + 1, acceptanceCriteria=acceptance)
            session["planDefinitionRevision"] = int(session.get("planDefinitionRevision") or 0) + 1
            session["approvedPlanDefinitionRevision"] = None
        for field in ("goal", "title", "constraints", "reflection", "planningThread"):
            if field in state.session:
                session[field] = state.session[field]
        deps.merge_hint_mutations(state.project, project)
        session["status"] = "planning"
        session["agentReply"] = self._plan_reply(state.feedback, from_llm, operation)
        now = deps.utc_now()
        body = (f"{'整体替换' if operation == 'replace' else '修订'}执行计划，共 {len(steps)} 步。" if state.feedback else f"生成执行计划，共 {len(steps)} 步。") + ("" if from_llm else "（生成失败，保留原计划）")
        session["events"] = list(session.get("events") or []) + [{"id": deps.short_id("event"), "type": "PlanRevised" if state.feedback else "PlanGenerated", "createdAt": now, "body": body}]
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        deps.write_store(payload)
        return {"ok": True, "project": project, "session": session, "planOperation": operation, "planSource": "llm" if from_llm else "fallback", **payload}

    @staticmethod
    def _plan_reply(feedback: str, from_llm: bool, operation: str) -> str:
        if from_llm:
            if operation == "replace":
                return "我已生成一份全新的执行计划，原计划不再作为当前步骤。"
            if operation == "revise":
                return "我已结合你的要求修订执行计划，并保留了可对应步骤的执行状态。"
            return "我已结合工作区里的实际内容拆解出执行计划。你可以编辑步骤、顺序和依赖后再执行。"
        return "计划调整未能生成有效结果，当前计划保持不变。你可以稍后重试。" if feedback else "计划生成服务暂时不可用，我先给出一份基础计划，你可以编辑后逐步执行，或稍后让我重新拆解。"

    async def generate_plan(self, session_id: str, body: dict[str, Any]):
        state = await self._prepare_generation(session_id, body)
        if isinstance(state, TaskExecutionResponse):
            return state
        generated = await self.dependencies.generate_plan(state.session, state.project, feedback=state.feedback, auto_start=state.auto_start, requested_operation=state.operation)
        return self._persist_generated(session_id, state, generated)

    async def reflect_and_fork(self, session_id: str):
        deps = self.dependencies
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": "session not found"}, 404)
        packet = await deps.run_reflection(session_id, goal_gap="任务验收未通过，需在新任务中换思路重试。")
        project_id = str(project.get("id") or "")
        new_session = deps.new_session(project_id, (str(session.get("title") or "任务") + " · 反思重试")[:80], str(session.get("goal") or "").strip())
        new_session["constraints"] = list(session.get("constraints") or [])
        new_session["parentSessionId"] = session_id
        if isinstance(packet, dict) and packet:
            deps.store_reflection(new_session, packet, trigger="forked", source_session_id=session_id, project=project)
            await deps.dispatch_reflection_hints(project, session, packet)
        project.setdefault("sessions", []).insert(0, new_session)
        now = deps.utc_now()
        project["updatedAt"] = now
        payload["activeProjectId"] = project_id
        payload["activeSessionId"] = new_session["id"]
        deps.write_store(payload)
        return {"ok": True, "session": new_session, "sourceSessionId": session_id, **payload}

    def update_hint(self, session_id: str, hint_id: str, *, accepted: bool):
        deps = self.dependencies
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": "session not found"}, 404)
        hints = session.get("pendingHints") if isinstance(session.get("pendingHints"), list) else []
        hint = next((item for item in hints if isinstance(item, dict) and str(item.get("id")) == hint_id), None)
        if not hint:
            return TaskExecutionResponse({"error": "hint not found"}, 404)
        packet = hint.get("packet") if isinstance(hint.get("packet"), dict) else None
        if accepted and packet:
            deps.store_reflection(session, packet, trigger="hint", source_session_id=str(hint.get("fromSessionId") or ""), project=project)
        hint["status"] = "accepted" if accepted else "dismissed"
        now = deps.utc_now()
        session["updatedAt"] = now
        if accepted:
            project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        deps.write_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}


class TaskInitializationApplicationService:
    def __init__(self, dependencies: TaskRouteDependencies) -> None:
        self.dependencies = dependencies

    def _session(self, session_id: str):
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": "session not found"}, 404)
        if str(session.get("kind") or "") != "init":
            return TaskExecutionResponse({"error": "not an init session"}, 400)
        return payload, project, session

    async def submit(self, session_id: str, body: dict[str, Any]):
        found = self._session(session_id)
        if isinstance(found, TaskExecutionResponse):
            return found
        payload, project, session = found
        deps = self.dependencies
        init_state = session.get("init") if isinstance(session.get("init"), dict) else {}
        if bool(init_state.get("completed")):
            return TaskExecutionResponse({"error": "init already completed"}, 409)
        form = session.get("init") if isinstance(session.get("init"), dict) else deps.default_init_form(project)
        if isinstance(body.get("answers"), dict):
            merged = form.get("answers") if isinstance(form.get("answers"), dict) else {}
            merged.update(body["answers"])
            form["answers"] = merged
        brief = deps.init_brief(project, form)
        answers = form.get("answers") if isinstance(form.get("answers"), dict) else {}
        goal = str(answers.get("goal") or "").strip()
        now = deps.utc_now()
        context = project.get("context") if isinstance(project.get("context"), dict) else {}
        if brief:
            context["summary"] = brief
        project["context"] = context
        if not str(project.get("description") or "").strip() and goal:
            project["description"] = goal[:200]
        task_plan, from_llm, error = await deps.generate_init_plan(project, form)
        form["completed"] = False
        form.pop("planError", None)
        self._apply_generated_plan(form, task_plan, from_llm, error, now)
        session["init"] = form
        session["status"] = "waiting_for_user"
        if from_llm:
            session["agentReply"] = "我已根据你的初始化回答拆解出大任务计划。你可以直接编辑，或继续告诉我如何调整；确认后我会把每个大任务创建为独立 session。"
        else:
            session["agentReply"] = self._failure_reply("计划生成", error)
        session["summary"] = brief or session.get("summary")
        return self._save(payload, project, session, session_id, now)

    @staticmethod
    def _apply_generated_plan(form: dict[str, Any], plan: list[Any], from_llm: bool, error: Any, now: str) -> None:
        if from_llm and plan:
            form.update(taskPlan=plan, planReady=True, planSource="llm")
        else:
            form.update(taskPlan=[], planReady=False, planSource="error", planError={**(error or {}), "occurredAt": now})

    @staticmethod
    def _failure_reply(label: str, error: Any, *, unchanged: bool = False) -> str:
        summary = str((error or {}).get("summary") or "未知错误")
        attempts = int((error or {}).get("attemptCount") or 5)
        return f"{label}连续重试 {attempts} 次后仍然失败{'，当前计划未改变' if unchanged else ''}：{summary}"

    def _save(self, payload, project, session, session_id: str, now: str):
        session["updatedAt"] = now
        project["updatedAt"] = now
        payload["activeSessionId"] = session_id
        self.dependencies.write_store(payload)
        return {"ok": True, "project": project, "session": session, **payload}

    async def revise(self, session_id: str, body: dict[str, Any]):
        found = self._session(session_id)
        if isinstance(found, TaskExecutionResponse):
            return found
        payload, project, session = found
        deps = self.dependencies
        form = session.get("init") if isinstance(session.get("init"), dict) else deps.default_init_form(project)
        if bool(form.get("completed")):
            return TaskExecutionResponse({"error": "init already completed"}, 409)
        incoming = body.get("taskPlan") if isinstance(body.get("taskPlan"), list) else None
        current = deps.coerce_init_plan(incoming, []) if incoming else None
        if not current:
            existing = form.get("taskPlan")
            current = existing if isinstance(existing, list) and existing else None
        feedback = str(body.get("feedback") or body.get("message") or "").strip()
        plan, from_llm, error = await deps.generate_init_plan(project, form, feedback=feedback, current_plan=current)
        form.pop("planError", None)
        if from_llm and plan:
            form.update(taskPlan=plan, planSource="llm")
            session["agentReply"] = "我已按你的反馈更新任务计划。你可以继续修改，或确认创建 sessions。"
        else:
            if current:
                form["taskPlan"] = current
            form.update(planSource="error", planError={**(error or {}), "occurredAt": deps.utc_now()})
            session["agentReply"] = self._failure_reply("计划调整", error, unchanged=True)
        form["planReady"] = bool(isinstance(form.get("taskPlan"), list) and form.get("taskPlan"))
        session["init"] = form
        session["status"] = "waiting_for_user"
        return self._save(payload, project, session, session_id, deps.utc_now())

    def confirm(self, session_id: str, body: dict[str, Any]):
        found = self._session(session_id)
        if isinstance(found, TaskExecutionResponse):
            return found
        payload, project, session = found
        deps = self.dependencies
        form = session.get("init") if isinstance(session.get("init"), dict) else deps.default_init_form(project)
        if bool(form.get("completed")):
            ids = form.get("createdSessionIds") if isinstance(form.get("createdSessionIds"), list) else []
            existing = [item for item in project.get("sessions", []) if str(item.get("id") or "") in {str(value) for value in ids}]
            return {"ok": True, "project": project, "session": existing[0] if existing else session, "initSession": session, "createdSessions": existing, **payload}
        incoming = body.get("taskPlan") if isinstance(body.get("taskPlan"), list) else form.get("taskPlan")
        plan = deps.coerce_init_plan(incoming, deps.fallback_init_plan(project, form))
        if not plan:
            return TaskExecutionResponse({"error": "task plan is empty"}, 400)
        now = deps.utc_now()
        created = deps.create_sessions_from_init_plan(project, plan, now)
        if not created:
            return TaskExecutionResponse({"error": "no sessions created"}, 400)
        form.update(taskPlan=plan, planReady=True, completed=True, createdSessionIds=[item["id"] for item in created])
        session.update(init=form, status="completed", agentReply=f"初始化已完成。我已根据确认后的计划创建 {len(created)} 个任务 session。", updatedAt=now)
        project["updatedAt"] = now
        payload["activeProjectId"] = project.get("id")
        payload["activeSessionId"] = created[0]["id"]
        deps.write_store(payload)
        deps.notify(title="初始化任务已生成", body=f"{project.get('name') or 'Workspace'} 已创建 {len(created)} 个任务 session。", tab="system", project_ref=project.get("id"), source="init_confirmed", source_label="系统", link_label=str(project.get("name") or ""), meta={"createdSessionIds": [item["id"] for item in created]})
        return {"ok": True, "project": project, "session": created[0], "initSession": session, "createdSessions": created, **payload}


class TaskRunCoordinationService:
    def __init__(self, dependencies: TaskRouteDependencies, task_runs: Any, db_path: str) -> None:
        self.dependencies = dependencies
        self.task_runs = task_runs
        self.db_path = db_path

    @staticmethod
    def _conflict(message: str) -> TaskExecutionResponse:
        return TaskExecutionResponse({"error": message, "code": "task_run_in_progress"}, 409)

    def _blocked(self, session_id: str, session: dict[str, Any] | None, *, bypass: bool):
        pending = session.get("pendingPlanStep") if isinstance(session, dict) and isinstance(session.get("pendingPlanStep"), dict) else {}
        if bypass and bool(pending.get("goalLoop")):
            return False
        goal_loop = session.get("goalLoop") if isinstance(session, dict) and isinstance(session.get("goalLoop"), dict) else {}
        if str(goal_loop.get("status") or "") in {"running", "waiting_for_user"}:
            return self._conflict("该任务正由持续执行状态机接管，请先暂停或取消它。")
        if self.dependencies.is_session_running(session_id):
            return self._conflict("该任务已有正在执行的请求，请等待完成或先停止它。")
        return None

    def _augment(self, result: Any, session_id: str, run_id: str) -> Any:
        if not isinstance(result, dict):
            return result
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        result.update(payload)
        result["project"] = project
        result["session"] = session
        run = next((item for item in (session or {}).get("runs") or [] if isinstance(item, dict) and str(item.get("id") or "") == run_id), None)
        if run is not None:
            result["run"] = run
        return result

    async def execute(self, run_type: str, session_id: str, body: dict[str, Any], handler: Callable[[], Awaitable[Any]], *, bypass_goal_loop_answer: bool = False):
        deps = self.dependencies
        _project, session = deps.find_session(deps.read_store(), session_id)
        blocked = self._blocked(session_id, session, bypass=bypass_goal_loop_answer)
        if blocked is False:
            return await handler()
        if blocked is not None:
            return blocked
        run_id = deps.short_id("run")
        request_id = str(body.get("clientRequestId") or "").strip()
        coordinator = self.task_runs.coordinator_for(self.db_path)
        lease = coordinator.try_acquire("task", session_id, run_id, request_id=request_id, run_type=run_type)
        if lease is None:
            return self._conflict("该任务已有正在执行的请求，请等待完成或先停止它。")
        token = None
        try:
            if not self.task_runs.begin_task_run(session_id, run_id, request_id=request_id, run_type=run_type, body=body):
                return TaskExecutionResponse({"error": "session not found"}, 404)
            token = self.task_runs.bind_task_run_id(run_id)
            result = await handler()
            self.task_runs.finish_task_run_if_open(session_id, run_id, result=result)
            return self._augment(result, session_id, run_id)
        except asyncio.CancelledError:
            self.task_runs.finish_task_run_if_open(session_id, run_id, status="cancelled", error="任务运行已被中断。", termination_reason="user_interrupted")
            raise
        except Exception as exc:
            self.task_runs.finish_task_run_if_open(session_id, run_id, status="failed", error=str(exc), termination_reason="handler_error")
            raise
        finally:
            if token is not None:
                self.task_runs.reset_task_run_id(token)
            coordinator.release(lease)
