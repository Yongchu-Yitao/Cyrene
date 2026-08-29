"""Task execution application service for run, chat, dispatch, and answer flows."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cyrene.workbench.core_adapter.task_runtime import (
    TaskAgentResult,
    TaskAgentRuntime,
    TaskAgentRuntimeError,
    persist_session_model_preference,
)
from cyrene.localization import app_language, localized, localized_plural
from cyrene.runtime.attachments import build_public_attachment_payload


@dataclass(slots=True)
class TaskExecutionResponse:
    payload: dict[str, Any]
    status_code: int


@dataclass(frozen=True, slots=True)
class TaskExecutionDependencies:
    """Task-domain ports; model/tool execution is owned by ``TaskAgentRuntime``."""

    read_store: Any
    short_id: Any
    utc_now: Any
    acceptance_from_session: Any
    apply_step_file_changes: Any
    collect_run_file_changes: Any
    derive_title: Any
    file_changes_from_tool_event: Any
    find_session: Any
    git_status_snapshot: Any
    is_blank_goal: Any
    is_default_title: Any
    plan_from_input: Any
    promote_file_artifacts: Any
    step_dependencies_satisfied: Any
    workspace_file_snapshot: Any
    workspace_root: Any
    workspace_text_snapshot: Any
    write_store: Any
    append_notification: Any

    @classmethod
    def from_task_routes(cls, dependencies: Any) -> "TaskExecutionDependencies":
        """Build from explicit Task route ports and pure Task domain modules."""

        from cyrene.workbench.artifacts import artifact_runtime
        from cyrene.workbench.planning import planning_runtime
        from cyrene.workbench.projects import project_runtime

        return cls(
            read_store=dependencies.read_store,
            short_id=dependencies.short_id,
            utc_now=dependencies.utc_now,
            acceptance_from_session=planning_runtime._workbench_acceptance_from_session,
            apply_step_file_changes=artifact_runtime._workbench_apply_step_file_changes,
            collect_run_file_changes=artifact_runtime._workbench_collect_run_file_changes,
            derive_title=project_runtime._workbench_derive_title,
            file_changes_from_tool_event=(
                artifact_runtime._workbench_file_changes_from_tool_event
            ),
            find_session=dependencies.find_session,
            git_status_snapshot=artifact_runtime._workbench_git_status_snapshot,
            is_blank_goal=project_runtime._workbench_is_blank_goal,
            is_default_title=project_runtime._workbench_is_default_title,
            plan_from_input=planning_runtime._workbench_plan_from_input,
            promote_file_artifacts=artifact_runtime._workbench_promote_file_artifacts,
            step_dependencies_satisfied=planning_runtime._workbench_step_dependencies_satisfied,
            workspace_file_snapshot=artifact_runtime._workbench_workspace_file_snapshot,
            workspace_root=dependencies.workspace_root,
            workspace_text_snapshot=artifact_runtime._workbench_workspace_text_snapshot,
            write_store=dependencies.write_store,
            append_notification=dependencies.notify,
        )


class TaskExecutionApplicationService:
    def __init__(
        self,
        *,
        dependencies: TaskExecutionDependencies,
        agent_runtime: TaskAgentRuntime,
        task_runs: Any,
        db_path: str,
    ) -> None:
        self.dependencies = dependencies
        self.agent_runtime = agent_runtime
        self.task_runs = task_runs
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def agent_run_error_response(exc: TaskAgentRuntimeError) -> TaskExecutionResponse:
        return TaskExecutionResponse({"error": exc.message, "code": exc.code}, exc.status_code)

    @staticmethod
    def finalize_host_actions_after_reply(session_id: str, client_request_id: str = "") -> None:
        from cyrene.runtime.host_actions import finalize_origin
        asyncio.create_task(finalize_origin(session_id, "", origin_run_id=client_request_id))

    @staticmethod
    def apply_task_model_preference(session_id: str, body: dict[str, Any], session: dict[str, Any]):
        requested_model = str(body.get("model") or "").strip()
        selected_key = requested_model or str(session.get("modelSelectionId") or "").strip()
        if not selected_key:
            return None
        from cyrene.core.plugin import application_plugin_service
        service = application_plugin_service("model_configuration")
        candidates = service.selectable_model_candidates() if service is not None else []
        selected_candidate = next((candidate for candidate in candidates if selected_key in {str(candidate.get("id") or "").strip(), str(candidate.get("model") or "").strip(), str(candidate.get("name") or "").strip()}), None)
        if selected_candidate is None:
            return TaskExecutionResponse({"error": localized(
                "Configured model not found.", "未找到已配置的模型。"
            )}, 400) if requested_model else None
        requested_effort = str(body.get("reasoningEffort") or "").strip().lower()
        selected_effort = requested_effort or str(session.get("reasoningEffort") or selected_candidate.get("reasoning_effort") or "").strip().lower()
        selected_model_id = str(selected_candidate.get("id") or selected_key).strip()
        selected_model_name = str(selected_candidate.get("model") or selected_candidate.get("name") or selected_key).strip()
        persist_session_model_preference(session_id, selected_candidate, selected_effort)
        session["modelSelectionId"] = selected_model_id
        session["model"] = selected_model_name
        session["reasoningEffort"] = selected_effort
        return None

    @staticmethod
    def normalize_attachments(attachments: Any) -> list[dict[str, Any]]:
        values = attachments if isinstance(attachments, list) else []
        normalized: list[dict[str, Any]] = []
        for raw in values:
            if not isinstance(raw, dict) or not str(raw.get("path") or "").strip():
                continue
            item = {
                "id": str(raw.get("id") or "").strip(),
                "name": str(raw.get("name") or "file"),
                "path": str(raw.get("path") or ""),
                "content_type": str(
                    raw.get("content_type") or "application/octet-stream"
                ),
                "size": int(raw.get("size") or 0),
                "kind": str(raw.get("kind") or "file"),
            }
            for field in ("width", "height"):
                try:
                    if raw.get(field) is not None:
                        item[field] = int(raw[field])
                except (TypeError, ValueError):
                    pass
            normalized.append(item)
        return normalized

    @staticmethod
    def _agent_instruction(
        *,
        kind: str,
        step: dict[str, Any] | None = None,
        pending_question: dict[str, Any] | None = None,
    ) -> str:
        if step is not None:
            return (
                "Execute exactly this approved plan step, using tools as needed, then "
                "report concrete changes and verification:\n"
                + json.dumps(step, ensure_ascii=False, default=str)
            )
        if kind == "answer":
            return "Answer the user's question directly. Do not create a plan unless asked."
        if kind == "finalize":
            return (
                "Review the existing task work, verify what is practical, summarize the "
                "deliverables and remaining risks, and prepare it for user acceptance."
            )
        if kind == "repair":
            return (
                "Use the current acceptance results to repair the task. Make concrete "
                "changes and report how they address the failed criteria."
            )
        if kind == "pending_answer":
            return (
                "The user is answering the pending clarification or approval below. "
                "Continue the same task from the existing ContextTree and either finish "
                "or ask one new question.\n"
                + json.dumps(pending_question or {}, ensure_ascii=False, default=str)
            )
        return "Execute the user's instruction now and report concrete results."

    async def _register_attachments(
        self, session_id: str, attachments: list[dict[str, Any]]
    ) -> None:
        if not attachments:
            return
        try:
            from cyrene.core.plugin import application_plugin_service

            service = application_plugin_service("knowledge")
            if service is not None:
                await service.register_attachments(session_id, attachments)
        except Exception:
            self.logger.exception(
                "Failed to register Task attachments [session=%s]", session_id
            )

    def _merge_agent_task_edits(
        self,
        payload: dict[str, Any],
        session_id: str,
        session: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Keep task metadata written by Plugins during an Agent turn."""

        latest_payload = self.dependencies.read_store()
        latest_project, latest_session = self.dependencies.find_session(
            latest_payload, session_id
        )
        if not latest_project or not latest_session:
            project, _ = self.dependencies.find_session(payload, session_id)
            return payload, project or {}, session
        for field in ("goal", "title", "summary", "titleLocked", "constraints"):
            if field in latest_session:
                session[field] = latest_session[field]
        project, _ = self.dependencies.find_session(payload, session_id)
        return payload, project or latest_project, session

    def _project_tool_events(
        self,
        events: Any,
        workspace_root: Any,
    ) -> list[dict[str, Any]]:
        """Attach Task artifact projections to ContextTree Plugin events."""

        projected: list[dict[str, Any]] = []
        for raw in events or ():
            if not isinstance(raw, dict):
                continue
            event = dict(raw)
            result = event.get("result")
            result_text = (
                result
                if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False, default=str)
            )
            event["fileChanges"] = self.dependencies.file_changes_from_tool_event(
                {
                    "tool": event.get("tool"),
                    "args": event.get("args") or event.get("arguments") or {},
                    "result": result_text,
                },
                workspace_root,
            )
            projected.append(event)
        return projected

    @staticmethod
    def _apply_agent_pending(
        session: dict[str, Any], result: TaskAgentResult
    ) -> tuple[str, bool]:
        if result.pending_question is not None:
            session["pendingQuestion"] = dict(result.pending_question)
            session["status"] = "waiting_for_user"
            return result.text or localized(
                "Your confirmation is required to continue.",
                "需要你确认后才能继续。",
            ), True
        session.pop("pendingQuestion", None)
        return result.text, False

    def _prepare_step_run(
        self, session: dict[str, Any], body: dict[str, Any]
    ) -> tuple[TaskExecutionResponse | None, str, dict[str, Any], bool, dict[str, Any] | None]:
        step_id = str(body.get('stepId') or '').strip()
        action = str(body.get('action') or '').strip()
        run_meta = body.get('meta') if isinstance(body.get('meta'), dict) else {}
        is_step_run = bool(step_id) or action == 'spawn_subagent'
        if not is_step_run:
            return None, step_id, run_meta, False, None
        plan = session.get('plan') if isinstance(session.get('plan'), list) else []
        step = next((item for item in plan if isinstance(item, dict)
                     and str(item.get('id') or '') == step_id), None)
        if not step:
            return TaskExecutionResponse(
                {'error': localized("Step not found.", "步骤不存在。"), 'code': 'step_not_found'}, 404
            ), step_id, run_meta, True, None
        try:
            requested_revision = int(body.get('planDefinitionRevision'))
        except (TypeError, ValueError):
            return TaskExecutionResponse({'error': localized(
                "Invalid plan definition revision.", "计划定义版本无效。"
            )}, 400), step_id, run_meta, True, step
        current_revision = int(session.get('planDefinitionRevision') or 0)
        if requested_revision != current_revision:
            return TaskExecutionResponse(
                {'error': localized(
                    "The plan changed. Confirm the latest plan before running it.",
                    "计划已发生变化，请重新确认后执行。",
                ), 'code': 'stale_plan_revision'}, 409
            ), step_id, run_meta, True, step
        try:
            approved_revision = int(session.get('approvedPlanDefinitionRevision'))
        except (TypeError, ValueError):
            approved_revision = -1
        if approved_revision != current_revision:
            return TaskExecutionResponse(
                {'error': localized(
                    "The current plan has not been approved for execution.",
                    "当前计划尚未获得执行确认。",
                ), 'code': 'plan_not_approved'}, 409
            ), step_id, run_meta, True, step
        ready, unmet_ids = self.dependencies.step_dependencies_satisfied(plan, step_id)
        if not ready:
            titles = {str(item.get('id') or ''): str(item.get('title') or '')
                      for item in plan if isinstance(item, dict)}
            return TaskExecutionResponse({
                'error': localized(
                    "Prerequisite steps are incomplete: {steps}",
                    "前置步骤尚未完成：{steps}",
                    steps=(
                        ", ".join(titles.get(dependency_id, dependency_id) for dependency_id in unmet_ids)
                        if app_language() == "en"
                        else "、".join(titles.get(dependency_id, dependency_id) for dependency_id in unmet_ids)
                    ),
                ),
                'code': 'unmet_dependencies',
            }, 409), step_id, run_meta, True, step
        return None, step_id, run_meta, True, step

    def _update_create_run_status(
        self, session: dict[str, Any], step: dict[str, Any] | None,
        *, is_step_run: bool, step_id: str, run_meta: dict[str, Any],
        agent_error: Any, awaiting_user: bool, file_changes: list[dict[str, Any]],
        tool_events: list[dict[str, Any]], started_at: str, finished_at: str,
    ) -> None:
        if agent_error is not None and not is_step_run:
            session['status'] = 'failed'
        elif not is_step_run and not awaiting_user:
            session['status'] = (
                'planning' if session.get('status') in ('idle', 'pending')
                else session.get('status', 'planning')
            )
        if is_step_run and step_id:
            self.dependencies.apply_step_file_changes(session, step_id, file_changes)
        if is_step_run and agent_error is not None and step:
            step.update(status='failed', updatedAt=finished_at, currentAction=agent_error.message)
            session['planRevision'] = int(session.get('planRevision') or 0) + 1
            session['status'] = 'failed'
            return
        if not (is_step_run and not awaiting_user and step):
            return
        step['status'] = 'completed'
        step['completedAt'] = finished_at
        step['updatedAt'] = finished_at
        step['currentAction'] = (
            localized_plural(
                "Completed after {count} tool call.",
                "Completed after {count} tool calls.",
                "已完成，本步调用工具 {count} 次。",
                count=len(tool_events),
            )
            if tool_events
            else localized("Step completed.", "已完成该步骤。")
        )
        step['toolCalls'] = [
            {'tool': event['tool'], 'argsPreview': event['argsPreview']} for event in tool_events
        ]
        try:
            duration = round((datetime.fromisoformat(finished_at)
                              - datetime.fromisoformat(str(step.get('startedAt') or started_at))).total_seconds())
            if duration >= 1:
                step['durationSec'] = duration
        except (TypeError, ValueError):
            pass
        session['planRevision'] = int(session.get('planRevision') or 0) + 1
        unresolved = [
            item for item in session.get('plan') or [] if isinstance(item, dict)
            and str(item.get('status') or 'pending') not in {'completed', 'done', 'skipped'}
        ]
        session['status'] = (
            'review' if not unresolved else 'running'
            if bool(run_meta.get('continueAll')) else 'paused'
        )

    @staticmethod
    def _request_fields(body: dict[str, Any]) -> tuple[list[Any], str, str, str, str]:
        attachments = body.get('attachments') if isinstance(body.get('attachments'), list) else []
        return (
            attachments,
            str(body.get('mode') or 'auto'),
            str(body.get('command') or ''),
            str(body.get('uiInstanceId') or '').strip(),
            str(body.get('clientRequestId') or '').strip(),
        )

    async def create_run(self, session_id: str, body: dict[str, Any]):
        user_input = str(body.get('input') or body.get('message') or '').strip()
        attachments, mode, command, ui_instance_id, client_request_id = self._request_fields(body)
        if not user_input and (not attachments):
            return TaskExecutionResponse({'error': localized(
                "Input is required.", "请输入内容。"
            )}, status_code=400)
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({'error': localized(
                "Session not found.", "未找到会话。"
            )}, status_code=404)
        model_error = self.apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error
        validation_error, step_id, run_meta, is_step_run, step = self._prepare_step_run(
            session, body
        )
        if validation_error:
            return validation_error
        run_started_at = self.dependencies.utc_now()
        if not is_step_run:
            constraints = await self.agent_runtime.extract_constraints(
                user_input, session, project
            )
            merged_constraints = list(session.get('constraints') or [])
            for item in constraints:
                if item not in merged_constraints:
                    merged_constraints.append(item)
            if not session.get('goal') or session.get('status') == 'idle':
                session['goal'] = user_input
            session['constraints'] = merged_constraints
            session['plan'] = self.dependencies.plan_from_input(user_input, session)
            session['acceptanceCriteria'] = self.dependencies.acceptance_from_session(session)
        else:
            constraints = []
        workspace_root = self.dependencies.workspace_root(project)
        git_status_before = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_before = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_before = self.dependencies.workspace_text_snapshot(workspace_root)
        normalized_attachments = self.normalize_attachments(attachments)
        public_attachments = [
            build_public_attachment_payload(item) for item in normalized_attachments
        ]
        run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        agent_error = None
        try:
            agent_result = await self.agent_runtime.run_turn(
                project=project,
                session=session,
                text=user_input,
                run_id=run_id,
                permission_mode=mode,
                command=command,
                client_request_id=client_request_id,
                ui_instance_id=ui_instance_id,
                attachments=normalized_attachments,
                purpose='step_execution' if is_step_run else 'task_execution',
                instruction=self._agent_instruction(
                    kind='direct', step=step if is_step_run else None
                ),
                metadata={'step_id': step_id} if is_step_run else None,
            )
            agent_reply, awaiting_user = self._apply_agent_pending(
                session, agent_result
            )
            tool_call_events = self._project_tool_events(
                agent_result.tool_events, workspace_root
            )
        except TaskAgentRuntimeError as exc:
            agent_error = exc
            agent_reply = exc.message
            awaiting_user = False
            tool_call_events = []
        git_status_after = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_after = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_after = self.dependencies.workspace_text_snapshot(workspace_root)
        if is_step_run and awaiting_user:
            session['pendingPlanStep'] = {'stepId': step_id, 'continueAll': bool(run_meta.get('continueAll'))}
        elif is_step_run:
            session.pop('pendingPlanStep', None)
        payload, project, session = self._merge_agent_task_edits(
            payload, session_id, session
        )
        session['agentReply'] = agent_reply
        if normalized_attachments:
            await self._register_attachments(session_id, normalized_attachments)
        activity_events = tool_call_events
        file_changes = self.dependencies.collect_run_file_changes(tool_call_events, git_status_before, git_status_after, workspace_files_before, workspace_files_after, workspace_root, f'{user_input}\n{agent_reply}', workspace_text_before=workspace_text_before, workspace_text_after=workspace_text_after)
        finished_at = self.dependencies.utc_now()
        self._update_create_run_status(
            session, step, is_step_run=is_step_run, step_id=step_id, run_meta=run_meta, agent_error=agent_error, awaiting_user=awaiting_user, file_changes=file_changes, tool_events=tool_call_events, started_at=run_started_at, finished_at=finished_at,
        )
        language = app_language()
        events = [{'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent', 'runId': run_id, 'createdAt': run_started_at, 'body': user_input or localized('[Attachment]', '[附件]', language=language), 'attachments': public_attachments}, *activity_events, {'id': self.dependencies.short_id('event'), 'type': 'AgentErrorEvent' if agent_error else 'AgentResponseEvent', 'runId': run_id, 'createdAt': finished_at, 'body': agent_reply}, {'id': self.dependencies.short_id('event'), 'type': 'PlanUpdatedEvent', 'runId': run_id, 'createdAt': finished_at, 'stepCount': len(session.get('plan') or [])}]
        if is_step_run and (not awaiting_user) and step:
            step_title = step.get('title') or step_id
            events.append({'id': self.dependencies.short_id('event'), 'type': 'ExecutionFailed' if agent_error else 'ExecutionFinished', 'runId': run_id, 'stepId': step_id, 'createdAt': finished_at, 'body': localized(
                'Step "{title}" failed: {error}',
                '步骤「{title}」执行失败：{error}',
                language=language, title=step_title, error=agent_error.message,
            ) if agent_error else localized(
                'Step "{title}" completed.', '步骤「{title}」执行完成。',
                language=language, title=step_title,
            )})
        run = {'id': run_id, 'taskId': session_id, 'userInput': user_input, 'agentResponse': agent_reply, 'status': 'failed' if agent_error else 'awaiting_user' if awaiting_user else 'completed', 'startedAt': run_started_at, 'endedAt': finished_at, 'contextPackId': self.dependencies.short_id('ctx'), 'events': events, 'fileChanges': file_changes, 'toolCalls': [{'tool': e['tool'], 'argsPreview': e['argsPreview']} for e in tool_call_events], 'artifacts': [], 'attachments': public_attachments, 'mode': mode, 'error': agent_error.message if agent_error else None, 'usage': dict(agent_result.usage) if agent_error is None else {}, 'model': agent_result.model if agent_error is None else '', 'modelIdentity': dict(agent_result.model_identity) if agent_error is None else {}, 'generationDurationMs': agent_result.generation_duration_ms if agent_error is None else None, 'outputTokensPerSecond': agent_result.output_tokens_per_second if agent_error is None else None}
        self.task_runs.upsert_task_run(session, run)
        session.setdefault('events', []).extend(events)
        self.dependencies.promote_file_artifacts(session, file_changes, finished_at)
        session['updatedAt'] = finished_at
        project['updatedAt'] = finished_at
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        self.finalize_host_actions_after_reply(session_id, client_request_id)
        task_title = session.get('title') or localized(
            'Untitled task', '未命名任务', language=language
        )
        self.dependencies.append_notification(
            title=localized(
                'Task execution failed' if agent_error else 'Task reply completed',
                '任务执行失败' if agent_error else '任务回复完成',
                language=language,
            ),
            body=localized(
                'The Agent failed while running task "{title}": {error}',
                'Agent 执行任务「{title}」失败：{error}',
                language=language, title=task_title, error=agent_error.message,
            ) if agent_error else localized(
                'The Agent updated task "{title}".',
                'Agent 已更新任务「{title}」。',
                language=language, title=task_title,
            ),
            tab='comment', project_ref=project.get('id'), source='task_reply',
            source_label=localized('Task', '任务', language=language),
            link_label=str(session.get('title') or ''),
            meta={'sessionId': session_id, 'runId': run_id}, language=language,
        )
        if agent_error is not None:
            return self.agent_run_error_response(agent_error)
        return {'ok': True, 'project': project, 'session': session, 'run': run, **payload}

    async def chat(self, session_id: str, body: dict[str, Any]):
        """Simple chat mode — returns agent reply without generating plans/steps."""
        message = str(body.get('message') or '').strip()
        attachments = body.get('attachments') if isinstance(body.get('attachments'), list) else []
        mode = str(body.get('mode') or 'auto')
        command = str(body.get('command') or '')
        ui_instance_id = str(body.get('uiInstanceId') or '').strip()
        client_request_id = str(body.get('clientRequestId') or '').strip()
        if not message and (not attachments):
            return TaskExecutionResponse({'error': localized(
                "Message is required.", "请输入消息。"
            )}, status_code=400)
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({'error': localized(
                "Session not found.", "未找到会话。"
            )}, status_code=404)
        model_error = self.apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error
        chat_run_start_ts = self.dependencies.utc_now()
        workspace_root = self.dependencies.workspace_root(project)
        git_status_before = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_before = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_before = self.dependencies.workspace_text_snapshot(workspace_root)
        normalized_attachments = self.normalize_attachments(attachments)
        chat_run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        agent_command = command or 'workbench-task-reply'
        try:
            agent_result = await self.agent_runtime.run_turn(
                project=project,
                session=session,
                text=message,
                run_id=chat_run_id,
                permission_mode=mode,
                command=agent_command,
                client_request_id=client_request_id,
                ui_instance_id=ui_instance_id,
                attachments=normalized_attachments,
                purpose='task_chat',
                instruction=self._agent_instruction(kind='answer'),
            )
        except TaskAgentRuntimeError as exc:
            return self.agent_run_error_response(exc)
        git_status_after = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_after = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_after = self.dependencies.workspace_text_snapshot(workspace_root)
        agent_reply, awaiting_user = self._apply_agent_pending(session, agent_result)
        payload, project, session = self._merge_agent_task_edits(
            payload, session_id, session
        )
        session['agentReply'] = agent_reply
        session['status'] = 'waiting_for_user' if awaiting_user else 'answered'
        now = self.dependencies.utc_now()
        session['updatedAt'] = now
        project['updatedAt'] = now
        chat_tool_events = self._project_tool_events(
            agent_result.tool_events, workspace_root
        )
        await self._register_attachments(session_id, normalized_attachments)
        file_changes = self.dependencies.collect_run_file_changes(chat_tool_events, git_status_before, git_status_after, workspace_files_before, workspace_files_after, workspace_root, f'{message}\n{agent_reply}', workspace_text_before=workspace_text_before, workspace_text_after=workspace_text_after)
        language = app_language()
        chat_events = [{'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent', 'runId': chat_run_id, 'createdAt': chat_run_start_ts, 'body': message or localized('[Attachment]', '[附件]', language=language)}, *chat_tool_events, {'id': self.dependencies.short_id('event'), 'type': 'AgentResponseEvent', 'runId': chat_run_id, 'createdAt': now, 'body': agent_reply}]
        run = {'id': chat_run_id, 'taskId': session_id, 'userInput': message, 'agentResponse': agent_reply, 'status': 'awaiting_user' if awaiting_user else 'completed', 'startedAt': chat_run_start_ts, 'endedAt': now, 'contextPackId': self.dependencies.short_id('ctx'), 'events': chat_events, 'fileChanges': file_changes, 'toolCalls': [{'tool': event.get('tool'), 'argsPreview': event.get('argsPreview', '')} for event in chat_tool_events if isinstance(event, dict) and event.get('type') == 'ToolCallEvent'], 'artifacts': [], 'attachments': [build_public_attachment_payload(item) for item in normalized_attachments], 'mode': mode, 'error': None, 'usage': dict(agent_result.usage), 'model': agent_result.model, 'modelIdentity': dict(agent_result.model_identity), 'generationDurationMs': agent_result.generation_duration_ms, 'outputTokensPerSecond': agent_result.output_tokens_per_second}
        self.task_runs.upsert_task_run(session, run)
        session.setdefault('events', []).extend(chat_events)
        self.dependencies.promote_file_artifacts(session, file_changes, now)
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        self.finalize_host_actions_after_reply(session_id, client_request_id)
        chat_title = session.get('title') or localized(
            'Conversation', '对话', language=language
        )
        self.dependencies.append_notification(
            title=localized('Agent reply completed', 'Agent 回复完成', language=language),
            body=localized(
                'The Agent replied to you in "{title}".',
                'Agent 在「{title}」中回复了你。',
                language=language, title=chat_title,
            ),
            tab='mention', project_ref=project.get('id'), source='chat_reply',
            source_label=localized('Conversation', '对话', language=language),
            link_label=str(session.get('title') or ''),
            meta={'sessionId': session_id}, language=language,
        )
        return {'ok': True, 'project': project, 'session': session, 'run': run, **payload}

    async def _maybe_generate_title(
        self,
        session_id: str,
        session: dict[str, Any],
        project: dict[str, Any],
        user_input: str,
    ) -> None:
        should_generate = bool(
            user_input and not session.get('titleLocked')
            and not session.get('titleNamingStatus')
            and self.dependencies.is_default_title(session.get('title'))
        )
        if not should_generate:
            return
        session['titleNamingStatus'] = 'pending'
        session['titleNamingStartedAt'] = self.dependencies.utc_now()
        try:
            generated = await self.agent_runtime.generate_title(
                user_input, session, project
            )
        except Exception as exc:
            self.logger.exception(
                'Workbench task session naming failed [session=%s error_type=%s]',
                session_id,
                type(exc).__name__,
            )
            generated = ''
        if generated and not session.get('titleLocked'):
            session['title'] = generated
            session['titleNamingStatus'] = 'generated'
            session['titleGeneratedAt'] = self.dependencies.utc_now()
        else:
            session['titleNamingStatus'] = 'failed'

    async def _generate_dispatch_plan(
        self, session_id: str, session: dict[str, Any], project: dict[str, Any],
        user_input: str, requested_revision: Any, now: str,
    ):
        existing_plan = session.get('plan') if isinstance(session.get('plan'), list) else []
        revising = bool(existing_plan)
        base_revision = int(session.get('planRevision') or 0)
        if requested_revision is not None:
            try:
                requested_revision = int(requested_revision)
            except (TypeError, ValueError):
                return TaskExecutionResponse({'error': localized(
                    "Invalid base plan revision.", "基础计划版本无效。"
                )}, 400)
            if requested_revision != base_revision:
                return TaskExecutionResponse(
                    {'error': localized(
                        "The plan changed. Retry using the latest plan.",
                        "计划已发生变化，请基于最新计划重试。",
                    ), 'code': 'stale_plan_revision'}, 409
                )
        steps, acceptance, from_llm, operation = await self.agent_runtime.generate_plan(
            session, project, feedback=user_input if revising else ''
        )
        payload = self.dependencies.read_store()
        latest_project, latest = self.dependencies.find_session(payload, session_id)
        if not latest or not latest_project:
            return TaskExecutionResponse({'error': localized(
                "Session not found.", "未找到会话。"
            )}, 404)
        if int(latest.get('planRevision') or 0) != base_revision:
            return TaskExecutionResponse(
                {'error': localized(
                    "The plan changed while it was being generated. Retry using the latest plan.",
                    "计划已在生成期间发生变化，请基于最新计划重试。",
                ), 'code': 'stale_plan_revision'}, 409
            )
        if not (revising and not from_llm):
            latest['plan'] = steps
            latest['planRevision'] = base_revision + 1
            latest['planDefinitionRevision'] = int(latest.get('planDefinitionRevision') or 0) + 1
            latest['approvedPlanDefinitionRevision'] = None
            latest['acceptanceCriteria'] = acceptance
        for field in ('goal', 'title', 'constraints', 'reflection', 'modelSelectionId',
                      'model', 'reasoningEffort', 'titleNamingStatus',
                      'titleNamingStartedAt', 'titleGeneratedAt'):
            if field in session:
                latest[field] = session[field]
        latest['status'] = 'planning'
        if revising:
            latest['agentReply'] = (
                localized(
                    "This request needs a replacement plan, so I generated new steps.",
                    "我判断这次要求需要整体替换计划，已生成全新步骤。",
                )
                if from_llm and operation == 'replace' else
                localized(
                    "I revised the execution plan and preserved the state of matching steps.",
                    "我已按你的说明修订执行计划，并保留了可对应步骤的执行状态。",
                )
                if from_llm else localized(
                    "Plan revision is temporarily unavailable. The original plan was preserved; try again later.",
                    "计划调整服务暂时不可用，已保留原计划。你可以稍后再让我调整。",
                )
            )
        else:
            latest['agentReply'] = (
                localized(
                    "I created an execution plan from the workspace contents. You can edit its steps, order, and dependencies before running it.",
                    "我已结合工作区里的实际内容拆解出执行计划。你可以编辑步骤、顺序和依赖后再执行。",
                )
                if from_llm else
                localized(
                    "Plan generation is temporarily unavailable. I created a basic plan that you can edit and run, or regenerate later.",
                    "计划生成服务暂时不可用，我先给出一份基础计划，你可以编辑后逐步执行，或稍后让我重新拆解。",
                )
            )
        plan_action = localized(
            "Replaced" if operation == 'replace' else "Revised",
            "整体替换" if operation == 'replace' else "修订",
        )
        step_count = len(steps)
        plan_body = localized_plural(
            "{action} the execution plan with {count} step." if revising else "Generated an execution plan with {count} step.",
            "{action} the execution plan with {count} steps." if revising else "Generated an execution plan with {count} steps.",
            "{action}执行计划，共 {count} 步。"
            if revising else "生成执行计划，共 {count} 步。",
            action=plan_action,
            count=step_count,
        )
        if not from_llm:
            plan_body += localized(
                " (Generation failed; kept the original plan.)"
                if revising else " (Fallback plan.)",
                "（生成失败，保留原计划）" if revising else "（兜底计划）",
            )
        latest['events'] = list(latest.get('events') or []) + [{
            'id': self.dependencies.short_id('event'),
            'type': 'PlanRevised' if revising else 'PlanGenerated',
            'createdAt': now,
            'body': plan_body,
        }]
        latest['updatedAt'] = now
        latest_project['updatedAt'] = now
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        return {'ok': True, 'replyKind': 'plan', 'planOperation': operation,
                'planSource': 'llm' if from_llm else 'fallback',
                'project': latest_project, 'session': latest, **payload}

    async def dispatch(self, session_id: str, body: dict[str, Any]):
        """Classify composer input and dispatch it to plan, answer, or execution."""
        user_input = str(body.get('input') or body.get('message') or '').strip()
        attachments = body.get('attachments') if isinstance(body.get('attachments'), list) else []
        mode = str(body.get('mode') or 'auto')
        command = str(body.get('command') or '')
        ui_instance_id = str(body.get('uiInstanceId') or '').strip()
        client_request_id = str(body.get('clientRequestId') or '').strip()
        requested_base_revision = body.get('basePlanRevision')
        if not user_input and (not attachments):
            return TaskExecutionResponse({'error': localized(
                "Input is required.", "请输入内容。"
            )}, status_code=400)
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({'error': localized(
                "Session not found.", "未找到会话。"
            )}, status_code=404)
        model_error = self.apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error
        await self._maybe_generate_title(session_id, session, project, user_input)
        if command or (not user_input and attachments):
            kind = 'direct'
        else:
            kind = await self.agent_runtime.classify_intent(
                user_input, session, project
            )
        now = self.dependencies.utc_now()
        if kind not in ('answer', 'finalize') and self.dependencies.is_blank_goal(session.get('goal')) and user_input:
            session['goal'] = user_input
            if self.dependencies.is_default_title(session.get('title')):
                session['title'] = self.dependencies.derive_title(user_input)
        if kind in ('plan', 'direct') and user_input:
            merged = list(session.get('constraints') or [])
            for item in await self.agent_runtime.extract_constraints(
                user_input, session, project
            ):
                if item not in merged:
                    merged.append(item)
            session['constraints'] = merged
        if kind == 'plan':
            return await self._generate_dispatch_plan(
                session_id, session, project, user_input, requested_base_revision, now
            )
        finalizing = kind == 'finalize'
        repairing_acceptance = command == 'workbench-task-repair'
        run_start_ts = self.dependencies.utc_now()
        workspace_root = self.dependencies.workspace_root(project)
        git_status_before = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_before = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_before = self.dependencies.workspace_text_snapshot(workspace_root)
        normalized_attachments = self.normalize_attachments(attachments)
        public_attachments = [
            build_public_attachment_payload(item) for item in normalized_attachments
        ]
        run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        agent_command = command or ('workbench-task-reply' if kind == 'answer' else '')
        try:
            agent_result = await self.agent_runtime.run_turn(
                project=project,
                session=session,
                text=user_input,
                run_id=run_id,
                permission_mode=mode,
                command=agent_command,
                client_request_id=client_request_id,
                ui_instance_id=ui_instance_id,
                attachments=normalized_attachments,
                purpose='task_' + ('repair' if repairing_acceptance else kind),
                instruction=self._agent_instruction(
                    kind='repair' if repairing_acceptance else kind
                ),
            )
        except TaskAgentRuntimeError as exc:
            return self.agent_run_error_response(exc)
        git_status_after = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_after = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_after = self.dependencies.workspace_text_snapshot(workspace_root)
        agent_reply, awaiting_user = self._apply_agent_pending(session, agent_result)
        payload, project, session = self._merge_agent_task_edits(
            payload, session_id, session
        )
        session['agentReply'] = agent_reply
        session['status'] = 'waiting_for_user' if awaiting_user else 'review' if finalizing or repairing_acceptance else 'acted' if kind == 'direct' else 'answered'
        await self._register_attachments(session_id, normalized_attachments)
        tool_call_events = self._project_tool_events(
            agent_result.tool_events, workspace_root
        )
        activity_events = tool_call_events
        file_changes = self.dependencies.collect_run_file_changes(tool_call_events, git_status_before, git_status_after, workspace_files_before, workspace_files_after, workspace_root, f'{user_input}\n{agent_reply}', workspace_text_before=workspace_text_before, workspace_text_after=workspace_text_after)
        finished_at = self.dependencies.utc_now()
        language = app_language()
        events = [{'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent', 'runId': run_id, 'createdAt': run_start_ts, 'body': user_input or localized('[Attachment]', '[附件]', language=language), 'attachments': public_attachments}, *activity_events, {'id': self.dependencies.short_id('event'), 'type': 'AgentResponseEvent', 'runId': run_id, 'createdAt': finished_at, 'body': agent_reply}]
        run = {'id': run_id, 'taskId': session_id, 'userInput': user_input, 'agentResponse': agent_reply, 'status': 'awaiting_user' if awaiting_user else 'completed', 'startedAt': run_start_ts, 'endedAt': finished_at, 'contextPackId': self.dependencies.short_id('ctx'), 'events': events, 'fileChanges': file_changes, 'toolCalls': [{'tool': e['tool'], 'argsPreview': e['argsPreview']} for e in tool_call_events], 'artifacts': [], 'attachments': public_attachments, 'mode': mode, 'error': None, 'usage': dict(agent_result.usage), 'model': agent_result.model, 'modelIdentity': dict(agent_result.model_identity), 'generationDurationMs': agent_result.generation_duration_ms, 'outputTokensPerSecond': agent_result.output_tokens_per_second}
        self.task_runs.upsert_task_run(session, run)
        session.setdefault('events', []).extend(events)
        self.dependencies.promote_file_artifacts(session, file_changes, finished_at)
        session['updatedAt'] = finished_at
        project['updatedAt'] = finished_at
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        self.finalize_host_actions_after_reply(session_id, client_request_id)
        task_title = session.get('title') or localized('Task', '任务', language=language)
        action = localized(
            'organized and delivered the task results for your review.' if finalizing
            else 'continued revising the task based on the review results.' if repairing_acceptance
            else 'carried out your instruction.' if kind == 'direct'
            else 'replied to you.',
            '整理并交付了任务成果，待你验收。' if finalizing
            else '参考验收结果继续修改了当前任务。' if repairing_acceptance
            else '执行了你的指令。' if kind == 'direct'
            else '回复了你。',
            language=language,
        )
        self.dependencies.append_notification(
            title=localized('Agent reply completed', 'Agent 回复完成', language=language),
            body=localized(
                'In "{title}", the Agent {action}',
                'Agent 在「{title}」中{action}',
                language=language, title=task_title, action=action,
            ),
            tab='comment', project_ref=project.get('id'), source='task_reply',
            source_label=localized('Task', '任务', language=language),
            link_label=str(session.get('title') or ''),
            meta={'sessionId': session_id, 'runId': run_id}, language=language,
        )
        return {'ok': True, 'replyKind': 'repair' if repairing_acceptance else kind, 'project': project, 'session': session, 'run': run, **payload}

    def _cancelled_answer_response(
        self, session_id: str, answer_text: str, run_started_at: str
    ) -> dict[str, Any] | None:
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session:
            return None
        finished_at = self.dependencies.utc_now()
        session.pop('pendingQuestion', None)
        session.pop('pendingPlanStep', None)
        session['status'] = 'paused'
        language = app_language()
        session['agentReply'] = localized(
            "Your answer was submitted, but you interrupted the continuation. You can resume later.",
            "回答已提交，但继续执行已被你中断。可稍后继续。",
            language=language,
        )
        for step in session.get('plan') or []:
            if not isinstance(step, dict) or step.get('status') != 'running':
                continue
            step.update(status='pending', startedAt=None,
                        currentAction=localized(
                            "Stopped; ready to run again.", "已停止，可重新执行。",
                            language=language,
                        ), updatedAt=finished_at)
        run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        events = [
            {'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent',
             'runId': run_id, 'createdAt': run_started_at, 'body': localized(
                 '[Confirmed] {answer}', '[确认] {answer}',
                 language=language, answer=answer_text,
             )},
            {'id': self.dependencies.short_id('event'), 'type': 'Paused',
             'runId': run_id, 'createdAt': finished_at, 'body': localized(
                 "The user interrupted execution after answering.",
                 "用户中断了回答后的继续执行。", language=language,
             )},
        ]
        session.setdefault('events', []).extend(events)
        run = {'id': run_id, 'taskId': session_id, 'userInput': answer_text,
               'agentResponse': '', 'status': 'cancelled',
               'terminationReason': 'user_interrupted', 'startedAt': run_started_at,
               'endedAt': finished_at, 'contextPackId': self.dependencies.short_id('ctx'),
               'events': events, 'toolCalls': [], 'fileChanges': [], 'artifacts': [],
               'attachments': [], 'mode': 'auto', 'error': None}
        self.task_runs.upsert_task_run(session, run)
        session['updatedAt'] = finished_at
        self.dependencies.write_store(payload)
        return {'ok': True, 'interrupted': True, 'awaitingUser': False,
                'continuePlanExecution': False, 'project': project,
                'session': session, 'run': run, **payload}

    def _apply_answered_step(
        self, session: dict[str, Any], pending: dict[str, Any] | None,
        *, awaiting_user: bool, permission_denied: bool,
        tool_events: list[dict[str, Any]], file_changes: list[dict[str, Any]],
        finished_at: str,
    ) -> bool:
        if not pending or awaiting_user:
            return False
        step_id = str(pending.get('stepId') or '').strip()
        goal_loop_step = bool(pending.get('goalLoop'))
        target = next((step for step in session.get('plan') or []
                       if isinstance(step, dict) and str(step.get('id') or '') == step_id), None)
        continue_execution = False
        if target:
            session['planRevision'] = int(session.get('planRevision') or 0) + 1
            target['updatedAt'] = finished_at
            if permission_denied:
                target.update(status='pending', startedAt=None,
                              currentAction=localized(
                                  "Permission was denied. Adjust the command before running again.",
                                  "权限请求被拒绝，可调整命令后重新执行。",
                              ))
                session['status'] = 'paused'
            else:
                target['status'] = 'completed'
                target['completedAt'] = finished_at
                target['currentAction'] = (
                    localized_plural(
                        "User confirmed; step completed with {count} tool call."
                        if goal_loop_step else "Completed with {count} tool call.",
                        "User confirmed; step completed with {count} tool calls."
                        if goal_loop_step else "Completed with {count} tool calls.",
                        "用户已确认；本步完成，调用工具 {count} 次。"
                        if goal_loop_step else "已完成，调用工具 {count} 次。",
                        count=len(tool_events),
                    )
                    if tool_events else localized(
                        "User confirmed; step completed."
                        if goal_loop_step else "Step completed.",
                        "用户已确认，本步骤完成。"
                        if goal_loop_step else "已完成该步骤。",
                    )
                )
                target['toolCalls'] = [
                    {'tool': event['tool'], 'argsPreview': event['argsPreview']} for event in tool_events
                ]
                self.dependencies.apply_step_file_changes(session, step_id, file_changes)
                if goal_loop_step:
                    session['status'] = 'running'
                else:
                    started_at = target.get('startedAt')
                    if started_at and target.get('durationSec') is None:
                        try:
                            seconds = round((datetime.fromisoformat(finished_at)
                                             - datetime.fromisoformat(str(started_at))).total_seconds())
                            if seconds >= 1:
                                target['durationSec'] = seconds
                        except (TypeError, ValueError):
                            pass
                    remaining = [step for step in session.get('plan') or []
                                 if isinstance(step, dict) and str(step.get('status') or 'pending')
                                 not in ('completed', 'done', 'skipped')]
                    session['status'] = ('review' if not remaining else 'running'
                                         if bool(pending.get('continueAll')) else 'paused')
                    continue_execution = bool(remaining and pending.get('continueAll'))
        session.pop('pendingPlanStep', None)
        return continue_execution

    async def answer(self, session_id: str, body: dict[str, Any]):
        """Continue the same durable Task ContextTree with the user's answer."""
        question_id = str(body.get('question_id') or '').strip()
        answer_text = str(body.get('answer') or body.get('selected_option') or '').strip()
        ui_instance_id = str(body.get('uiInstanceId') or '').strip()
        if not question_id or not answer_text:
            return TaskExecutionResponse({'error': localized(
                "Question ID and answer are required.", "问题 ID 和回答不能为空。"
            )}, status_code=400)
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({'error': localized(
                "Session not found.", "未找到会话。"
            )}, status_code=404)
        pending = session.get('pendingQuestion') if isinstance(session.get('pendingQuestion'), dict) else None
        if not pending or str(pending.get('id') or '') != question_id:
            return TaskExecutionResponse({'error': localized(
                "No matching pending question.", "没有匹配的待回答问题。"
            )}, status_code=409)
        pending_plan_step = dict(session.get('pendingPlanStep')) if isinstance(session.get('pendingPlanStep'), dict) else None
        permission_kinds = {'scope_elevation', 'write_permission_request', 'read_elevation', 'subshell_elevation', 'delete_confirmation', 'task_permission_request', 'git_commit', 'destructive_confirmation', 'external_delivery_request', 'external_upload_confirmation'}
        pending_options = pending.get('options') if isinstance(pending.get('options'), list) else []
        normalized_answer = answer_text.strip().casefold()
        explicit_denial = normalized_answer == str(pending_options[-1]).strip().casefold() if pending_options else normalized_answer in {'拒绝', '不允许', '否', 'reject', 'deny', 'no'}
        permission_denied = str(pending.get('kind') or '') in permission_kinds and explicit_denial
        now = self.dependencies.utc_now()
        run_start_ts = now
        workspace_root = self.dependencies.workspace_root(project)
        git_status_before = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_before = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_before = self.dependencies.workspace_text_snapshot(workspace_root)
        run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        agent_run_id = str(pending.get('roundId') or '').strip()
        if not agent_run_id:
            return TaskExecutionResponse(
                {'error': localized(
                    "The pending Agent run ID is missing.",
                    "待继续的 Agent 运行 ID 缺失。",
                ), 'code': 'task_answer_run_missing'},
                status_code=409,
            )
        try:
            agent_result = await self.agent_runtime.answer_turn(
                project=project,
                session=session,
                question_id=question_id,
                answer=answer_text,
                run_id=agent_run_id,
                permission_mode='auto',
                command='workbench-task-answer',
                ui_instance_id=ui_instance_id,
                purpose='task_answer',
                instruction=self._agent_instruction(
                    kind='pending_answer', pending_question=pending
                ),
                metadata={
                    'question_id': question_id,
                    'answers_run_id': agent_run_id,
                },
            )
        except asyncio.CancelledError:
            lease = self.task_runs.coordinator_for(self.db_path).get(
                "task", session_id
            )
            if lease is not None and str(lease.termination_reason or "") == "server_shutdown":
                # Preserve the pending question and running audit.  Startup will
                # bind this exact run id and continue the same Agent ContextTree.
                raise
            cancelled = self._cancelled_answer_response(session_id, answer_text, run_start_ts)
            if cancelled is not None:
                return cancelled
            raise
        except TaskAgentRuntimeError as exc:
            return self.agent_run_error_response(exc)
        git_status_after = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_after = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_after = self.dependencies.workspace_text_snapshot(workspace_root)
        agent_reply, awaiting_user = self._apply_agent_pending(session, agent_result)
        payload, project, session = self._merge_agent_task_edits(
            payload, session_id, session
        )
        session['agentReply'] = agent_reply
        if not awaiting_user:
            session.pop('pendingQuestion', None)
            session['status'] = 'acted'
        tool_call_events = self._project_tool_events(
            agent_result.tool_events, workspace_root
        )
        activity_events = tool_call_events
        file_changes = self.dependencies.collect_run_file_changes(tool_call_events, git_status_before, git_status_after, workspace_files_before, workspace_files_after, workspace_root, f'{answer_text}\n{agent_reply}', workspace_text_before=workspace_text_before, workspace_text_after=workspace_text_after)
        finished_at = self.dependencies.utc_now()
        language = app_language()
        events = [{'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent', 'runId': run_id, 'createdAt': now, 'body': localized(
            '[Confirmed] {answer}', '[确认] {answer}',
            language=language, answer=answer_text,
        )}, *activity_events, {'id': self.dependencies.short_id('event'), 'type': 'AgentResponseEvent', 'runId': run_id, 'createdAt': finished_at, 'body': agent_reply}]
        run = {'id': run_id, 'taskId': session_id, 'userInput': answer_text, 'agentResponse': agent_reply, 'status': 'awaiting_user' if awaiting_user else 'completed', 'startedAt': run_start_ts, 'endedAt': finished_at, 'contextPackId': self.dependencies.short_id('ctx'), 'events': events, 'fileChanges': file_changes, 'toolCalls': [{'tool': e['tool'], 'argsPreview': e['argsPreview']} for e in tool_call_events], 'artifacts': [], 'attachments': [], 'mode': 'auto', 'error': None, 'usage': dict(agent_result.usage), 'model': agent_result.model, 'modelIdentity': dict(agent_result.model_identity), 'generationDurationMs': agent_result.generation_duration_ms, 'outputTokensPerSecond': agent_result.output_tokens_per_second}
        self.task_runs.upsert_task_run(session, run)
        session.setdefault('events', []).extend(events)
        self.dependencies.promote_file_artifacts(session, file_changes, finished_at)
        continue_plan_execution = self._apply_answered_step(
            session, pending_plan_step, awaiting_user=awaiting_user,
            permission_denied=permission_denied, tool_events=tool_call_events,
            file_changes=file_changes, finished_at=finished_at,
        )
        session['updatedAt'] = finished_at
        project['updatedAt'] = finished_at
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        self.finalize_host_actions_after_reply(session_id)
        if pending_plan_step and bool(pending_plan_step.get('goalLoop')) and (not awaiting_user):
            from cyrene.workbench.goals.goal_loop import resume_after_answer
            await resume_after_answer(self.db_path, session_id, permission_denied=permission_denied)
        return {'ok': True, 'awaitingUser': awaiting_user, 'continuePlanExecution': continue_plan_execution, 'project': project, 'session': session, 'run': run, **payload}


__all__ = ["TaskExecutionApplicationService", "TaskExecutionResponse"]
