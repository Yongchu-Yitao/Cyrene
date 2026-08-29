"""Application workflows shared by Workbench task-session adapters."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cyrene.localization import app_language, localized, localized_plural
from cyrene.workbench.tasks.task_execution_service import TaskExecutionResponse
from cyrene.workbench.tasks.task_services import TaskRouteDependencies


class TaskWorkspaceApplicationService:
    def __init__(self, dependencies: TaskRouteDependencies) -> None:
        self.dependencies = dependencies
        self.logger = logging.getLogger(__name__)

    async def file_diff(self, session_id: str, path: str) -> dict[str, Any] | TaskExecutionResponse:
        deps = self.dependencies
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": localized(
                "Session not found.", "未找到会话。"
            )}, 404)
        workspace_root = deps.workspace_root(project)
        recorded = deps.recorded_diff(session, path, workspace_root)
        if recorded and recorded.get("has_changes"):
            return recorded
        try:
            result = await deps.git_diff(workspace_root, path)
        except ValueError:
            self.logger.info(
                "Invalid Workbench diff request for session %s",
                session_id,
                exc_info=True,
            )
            return TaskExecutionResponse({"error": localized(
                "The requested diff path is invalid.", "请求的差异路径无效。"
            ), "code": "workbench_diff_invalid"}, 400)
        except TimeoutError:
            self.logger.warning(
                "Workbench diff timed out for session %s",
                session_id,
                exc_info=True,
            )
            return TaskExecutionResponse({"error": localized(
                "Generating the diff timed out.", "生成差异时超时。"
            ), "code": "workbench_diff_timeout"}, 504)
        except RuntimeError:
            self.logger.exception("Failed to compute Workbench diff for session %s", session_id)
            return TaskExecutionResponse({"error": localized(
                "Could not generate the diff.", "无法生成差异。"
            ), "code": "workbench_diff_failed"}, 500)
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
    def __init__(self, dependencies: TaskRouteDependencies, agent_runtime: Any) -> None:
        self.dependencies = dependencies
        self.agent_runtime = agent_runtime

    async def _dispatch_reflection_hints(
        self,
        project: dict[str, Any],
        session: dict[str, Any],
        packet: dict[str, Any],
    ) -> None:
        candidates = self.dependencies.reflection_candidates(project, session)
        matches = await self.agent_runtime.reflection_hints(
            packet, candidates, session, project
        )
        self.dependencies.apply_reflection_hints(
            project, session, packet, matches
        )

    async def _prepare_generation(self, session_id: str, body: dict[str, Any]):
        deps = self.dependencies
        goal = str(body.get("goal") or "").strip()
        feedback = str(body.get("feedback") or "").strip()
        operation = str(body.get("operation") or "auto").strip().lower()
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": localized(
                "Session not found.", "未找到会话。"
            )}, 404)
        base_revision = int(session.get("planRevision") or 0)
        requested_revision = body.get("basePlanRevision")
        if requested_revision is not None:
            try:
                requested_revision = int(requested_revision)
            except (TypeError, ValueError):
                return TaskExecutionResponse({"error": localized(
                    "Invalid base plan revision.", "基础计划版本无效。"
                )}, 400)
            if requested_revision != base_revision:
                return TaskExecutionResponse({"error": localized(
                    "The plan changed. Retry using the latest plan.",
                    "计划已发生变化，请基于最新计划重试。",
                ), "code": "stale_plan_revision"}, 409)
        if goal:
            session["goal"] = goal
            merged = list(session.get("constraints") or [])
            for item in await self.agent_runtime.extract_constraints(
                goal, session, project
            ):
                if item not in merged:
                    merged.append(item)
            session["constraints"] = merged
        should_reflect = feedback and operation != "replace" and str(session.get("status") or "") in ("failed", "review")
        if should_reflect and await self.agent_runtime.should_reflect(
            str(session.get("goal") or ""),
            session.get("acceptanceCriteria") or [],
            feedback,
            session,
            project,
        ):
            packet = await self.agent_runtime.reflect_task(
                session,
                project,
                focus=feedback,
                goal_gap=localized(
                    "The user is dissatisfied with the current plan or result: {feedback}",
                    "用户对当前计划/结果不满意：{feedback}",
                    feedback=feedback,
                ),
            )
            if packet:
                deps.store_reflection(session, packet, trigger="feedback", project=project)
                await self._dispatch_reflection_hints(project, session, packet)
        return PlanGenerationState(project, session, base_revision, feedback, bool(body.get("autoStart")), operation)

    def _persist_generated(self, session_id: str, state: PlanGenerationState, generated):
        deps = self.dependencies
        steps, acceptance, from_llm, operation = generated
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": localized(
                "Session not found.", "未找到会话。"
            )}, 404)
        if int(session.get("planRevision") or 0) != state.base_revision:
            return TaskExecutionResponse({"error": localized(
                "The plan changed while it was being generated. Retry using the latest plan.",
                "计划已在生成期间发生变化，请基于最新计划重试。",
            ), "code": "stale_plan_revision"}, 409)
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
        action = localized(
            "Replaced" if operation == "replace" else "Revised",
            "整体替换" if operation == "replace" else "修订",
        )
        step_count = len(steps)
        body = localized_plural(
            "{action} the execution plan with {count} step." if state.feedback else "Generated an execution plan with {count} step.",
            "{action} the execution plan with {count} steps." if state.feedback else "Generated an execution plan with {count} steps.",
            "{action}执行计划，共 {count} 步。"
            if state.feedback else "生成执行计划，共 {count} 步。",
            action=action,
            count=step_count,
        ) + ("" if from_llm else localized(
            " (Generation failed; kept the original plan.)",
            "（生成失败，保留原计划）",
        ))
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
                return localized(
                    "I generated a new execution plan; the previous plan is no longer active.",
                    "我已生成一份全新的执行计划，原计划不再作为当前步骤。",
                )
            if operation == "revise":
                return localized(
                    "I revised the execution plan and preserved the state of matching steps.",
                    "我已结合你的要求修订执行计划，并保留了可对应步骤的执行状态。",
                )
            return localized(
                "I created an execution plan from the workspace contents. You can edit its steps, order, and dependencies before running it.",
                "我已结合工作区里的实际内容拆解出执行计划。你可以编辑步骤、顺序和依赖后再执行。",
            )
        return localized(
            "Plan revision did not produce a valid result. The current plan is unchanged; try again later."
            if feedback else "Plan generation is temporarily unavailable. A basic editable plan is available; run it step by step or regenerate it later.",
            "计划调整未能生成有效结果，当前计划保持不变。你可以稍后重试。"
            if feedback else "计划生成服务暂时不可用，我先给出一份基础计划，你可以编辑后逐步执行，或稍后让我重新拆解。",
        )

    async def generate_plan(self, session_id: str, body: dict[str, Any]):
        state = await self._prepare_generation(session_id, body)
        if isinstance(state, TaskExecutionResponse):
            return state
        generated = await self.agent_runtime.generate_plan(
            state.session,
            state.project,
            feedback=state.feedback,
            auto_start=state.auto_start,
            requested_operation=state.operation,
        )
        return self._persist_generated(session_id, state, generated)

    async def reflect_and_fork(self, session_id: str):
        deps = self.dependencies
        payload = deps.read_store()
        project, session = deps.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": localized(
                "Session not found.", "未找到会话。"
            )}, 404)
        packet = await self.agent_runtime.reflect_task(
            session,
            project,
            goal_gap=localized(
                "Task acceptance failed; retry with a different approach in a new task.",
                "任务验收未通过，需在新任务中换思路重试。",
            ),
        )
        project_id = str(project.get("id") or "")
        source_title = str(session.get("title") or localized("Task", "任务"))
        new_session = deps.new_session(
            project_id,
            localized(
                "{title} · Reflect and retry",
                "{title} · 反思重试",
                title=source_title,
            )[:80],
            str(session.get("goal") or "").strip(),
        )
        new_session["constraints"] = list(session.get("constraints") or [])
        new_session["parentSessionId"] = session_id
        if isinstance(packet, dict) and packet:
            deps.store_reflection(new_session, packet, trigger="forked", source_session_id=session_id, project=project)
            await self._dispatch_reflection_hints(project, session, packet)
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
            return TaskExecutionResponse({"error": localized(
                "Session not found.", "未找到会话。"
            )}, 404)
        hints = session.get("pendingHints") if isinstance(session.get("pendingHints"), list) else []
        hint = next((item for item in hints if isinstance(item, dict) and str(item.get("id")) == hint_id), None)
        if not hint:
            return TaskExecutionResponse({"error": localized(
                "Hint not found.", "未找到提示。"
            )}, 404)
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
    def __init__(self, dependencies: TaskRouteDependencies, agent_runtime: Any) -> None:
        self.dependencies = dependencies
        self.agent_runtime = agent_runtime

    def _session(self, session_id: str):
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({"error": localized(
                "Session not found.", "未找到会话。"
            )}, 404)
        if str(session.get("kind") or "") != "init":
            return TaskExecutionResponse({"error": localized(
                "This is not an initialization session.", "这不是初始化会话。"
            )}, 400)
        return payload, project, session

    async def submit(self, session_id: str, body: dict[str, Any]):
        found = self._session(session_id)
        if isinstance(found, TaskExecutionResponse):
            return found
        payload, project, session = found
        deps = self.dependencies
        init_state = session.get("init") if isinstance(session.get("init"), dict) else {}
        if bool(init_state.get("completed")):
            return TaskExecutionResponse({"error": localized(
                "Initialization is already complete.", "初始化已经完成。"
            )}, 409)
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
        task_plan, from_llm, error = await self.agent_runtime.generate_init_plan(
            project, form, session=session
        )
        form["completed"] = False
        form.pop("planError", None)
        self._apply_generated_plan(form, task_plan, from_llm, error, now)
        session["init"] = form
        session["status"] = "waiting_for_user"
        if from_llm:
            session["agentReply"] = localized(
                "I created a high-level task plan from your initialization answers. Edit it or tell me how to revise it; after confirmation, each task becomes its own session.",
                "我已根据你的初始化回答拆解出大任务计划。你可以直接编辑，或继续告诉我如何调整；确认后我会把每个大任务创建为独立 session。",
            )
        else:
            session["agentReply"] = self._failure_reply(
                localized("Plan generation", "计划生成"), error
            )
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
        summary = str((error or {}).get("summary") or localized(
            "Unknown error", "未知错误"
        ))
        attempts = int((error or {}).get("attemptCount") or 5)
        return localized(
            "{label} failed after {attempts} attempts{unchanged}: {summary}",
            "{label}连续重试 {attempts} 次后仍然失败{unchanged}：{summary}",
            label=label,
            attempts=attempts,
            unchanged=(
                localized("; the current plan is unchanged", "，当前计划未改变")
                if unchanged else ""
            ),
            summary=summary,
        )

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
            return TaskExecutionResponse({"error": localized(
                "Initialization is already complete.", "初始化已经完成。"
            )}, 409)
        incoming = body.get("taskPlan") if isinstance(body.get("taskPlan"), list) else None
        current = deps.coerce_init_plan(incoming, []) if incoming else None
        if not current:
            existing = form.get("taskPlan")
            current = existing if isinstance(existing, list) and existing else None
        feedback = str(body.get("feedback") or body.get("message") or "").strip()
        plan, from_llm, error = await self.agent_runtime.generate_init_plan(
            project,
            form,
            session=session,
            feedback=feedback,
            current_plan=current,
        )
        form.pop("planError", None)
        if from_llm and plan:
            form.update(taskPlan=plan, planSource="llm")
            session["agentReply"] = localized(
                "I updated the task plan from your feedback. Continue editing it or confirm to create the sessions.",
                "我已按你的反馈更新任务计划。你可以继续修改，或确认创建 sessions。",
            )
        else:
            if current:
                form["taskPlan"] = current
            form.update(planSource="error", planError={**(error or {}), "occurredAt": deps.utc_now()})
            session["agentReply"] = self._failure_reply(
                localized("Plan revision", "计划调整"), error, unchanged=True
            )
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
            return TaskExecutionResponse({"error": localized(
                "The task plan is empty.", "任务计划为空。"
            )}, 400)
        now = deps.utc_now()
        created = deps.create_sessions_from_init_plan(project, plan, now)
        if not created:
            return TaskExecutionResponse({"error": localized(
                "No sessions were created.", "未能创建会话。"
            )}, 400)
        form.update(taskPlan=plan, planReady=True, completed=True, createdSessionIds=[item["id"] for item in created])
        language = app_language()
        created_count = len(created)
        session.update(init=form, status="completed", agentReply=localized_plural(
            "Initialization is complete. I created {count} task session from the confirmed plan.",
            "Initialization is complete. I created {count} task sessions from the confirmed plan.",
            "初始化已完成。我已根据确认后的计划创建 {count} 个任务 session。",
            language=language, count=created_count,
        ), updatedAt=now)
        project["updatedAt"] = now
        payload["activeProjectId"] = project.get("id")
        payload["activeSessionId"] = created[0]["id"]
        deps.write_store(payload)
        deps.notify(
            title=localized(
                "Initialization tasks created", "初始化任务已生成", language=language
            ),
            body=localized_plural(
                "{workspace} created {count} task session.",
                "{workspace} created {count} task sessions.",
                "{workspace} 已创建 {count} 个任务 session。",
                language=language,
                workspace=project.get('name') or 'Workspace',
                count=created_count,
            ),
            tab="system", project_ref=project.get("id"), source="init_confirmed",
            source_label=localized("System", "系统", language=language),
            link_label=str(project.get("name") or ""),
            meta={"createdSessionIds": [item["id"] for item in created]},
            language=language,
        )
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
            return self._conflict(localized(
                "Continuous execution controls this task. Pause or cancel it first.",
                "该任务正由持续执行状态机接管，请先暂停或取消它。",
            ))
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
            # Goal Loop owns the suspended step. Mount the answer into the same
            # durable Agent run in its background worker so the ordinary step
            # verifier and whole-goal state machine still run afterward.
            from cyrene.workbench.goals.goal_loop import begin_async_answer

            question_id = str(body.get("question_id") or "").strip()
            answer_text = str(
                body.get("answer") or body.get("selected_option") or ""
            ).strip()
            if await begin_async_answer(
                self.db_path,
                session_id,
                question_id,
                answer_text,
            ):
                payload = deps.read_store()
                project, resumed_session = deps.find_session(payload, session_id)
                return {
                    "ok": True,
                    "awaitingUser": False,
                    "continuePlanExecution": True,
                    "project": project,
                    "session": resumed_session,
                    **payload,
                }
            return await handler()
        if blocked is not None:
            return blocked
        run_id = deps.short_id("run")
        request_id = str(body.get("clientRequestId") or "").strip()
        coordinator = self.task_runs.coordinator_for(self.db_path)
        lease = coordinator.try_acquire("task", session_id, run_id, request_id=request_id, run_type=run_type)
        if lease is None:
            return self._conflict(localized(
                "This task already has a running request. Wait for it to finish or stop it first.",
                "该任务已有正在执行的请求，请等待完成或先停止它。",
            ))
        token = None
        try:
            if not self.task_runs.begin_task_run(session_id, run_id, request_id=request_id, run_type=run_type, body=body):
                return TaskExecutionResponse({"error": localized(
                    "Session not found.", "未找到会话。"
                )}, 404)
            token = self.task_runs.bind_task_run_id(run_id)
            result = await handler()
            self.task_runs.finish_task_run_if_open(session_id, run_id, result=result)
            return self._augment(result, session_id, run_id)
        except asyncio.CancelledError:
            # A server shutdown is a hand-off, not a user cancellation.  Keep the
            # durable run open so startup can bind the same run id and resume the
            # Agent ContextTree.  Explicit user cancellation remains terminal.
            if str(lease.termination_reason or "") != "server_shutdown":
                self.task_runs.finish_task_run_if_open(
                    session_id,
                    run_id,
                    status="cancelled",
                    error=localized(
                        "Task execution was interrupted.", "任务运行已被中断。"
                    ),
                    termination_reason=str(
                        lease.termination_reason or "user_interrupted"
                    ),
                )
            raise
        except Exception:
            self.logger.exception(
                "Task run handler failed for session %s (run %s)",
                session_id,
                run_id,
            )
            self.task_runs.finish_task_run_if_open(
                session_id,
                run_id,
                status="failed",
                error=localized(
                    "Task execution failed.", "任务执行失败。"
                ),
                termination_reason="handler_error",
            )
            raise
        finally:
            if token is not None:
                self.task_runs.reset_task_run_id(token)
            coordinator.release(lease)
