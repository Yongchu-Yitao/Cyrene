"""Task execution application service for run, chat, dispatch, and answer flows."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cyrene.runtime.attachments import build_public_attachment_payload


@dataclass(slots=True)
class TaskExecutionResponse:
    payload: dict[str, Any]
    status_code: int


@dataclass(frozen=True, slots=True)
class TaskExecutionDependencies:
    """Explicit ports used by task execution orchestration."""

    task_reply_directive: str
    agent_run_error: Any
    check_budget_gate: Any
    collect_run_activity_events: Any
    collect_run_tool_events: Any
    read_store: Any
    schedule_task_report: Any
    short_id: Any
    utc_now: Any
    acceptance_from_session: Any
    acceptance_repair_directive: Any
    agent_reply: Any
    answer_pending: Any
    apply_pending: Any
    apply_step_file_changes: Any
    archive_run_knowledge: Any
    capture_task_meta: Any
    classify_intent: Any
    collect_run_file_changes: Any
    compose_ephemeral_system: Any
    compose_memory_ephemeral: Any
    compose_static_system: Any
    compose_volatile_ephemeral_system: Any
    derive_title: Any
    extract_constraints: Any
    finalize_directive: Any
    find_session: Any
    generate_plan_steps: Any
    generate_step_outcome: Any
    git_status_snapshot: Any
    is_blank_goal: Any
    is_default_title: Any
    normalize_attachments: Any
    plan_from_input: Any
    project_memory_key: Any
    promote_file_artifacts: Any
    register_attachments_kb: Any
    resolve_workspace_async: Any
    step_dependencies_satisfied: Any
    sync_agent_task_meta: Any
    workspace_file_snapshot: Any
    workspace_root: Any
    workspace_text_snapshot: Any
    write_store: Any
    append_notification: Any
    schedule_capture: Any

    @classmethod
    def from_runtime(cls, runtime: Any) -> "TaskExecutionDependencies":
        return cls(
            task_reply_directive=runtime._WORKBENCH_TASK_REPLY_DIRECTIVE,
            agent_run_error=runtime._WorkbenchAgentRunError,
            check_budget_gate=runtime._check_budget_gate,
            collect_run_activity_events=runtime._collect_run_activity_events,
            collect_run_tool_events=runtime._collect_run_tool_events,
            read_store=runtime._read_workbench_store,
            schedule_task_report=runtime._schedule_task_report,
            short_id=runtime._short_id,
            utc_now=runtime._utc_now_iso,
            acceptance_from_session=runtime._workbench_acceptance_from_session,
            acceptance_repair_directive=runtime._workbench_acceptance_repair_directive,
            agent_reply=runtime._workbench_agent_reply,
            answer_pending=runtime._workbench_answer_pending,
            apply_pending=runtime._workbench_apply_pending,
            apply_step_file_changes=runtime._workbench_apply_step_file_changes,
            archive_run_knowledge=runtime._workbench_archive_run_knowledge,
            capture_task_meta=runtime._workbench_capture_task_meta,
            classify_intent=runtime._workbench_classify_intent,
            collect_run_file_changes=runtime._workbench_collect_run_file_changes,
            compose_ephemeral_system=runtime._workbench_compose_ephemeral_system,
            compose_memory_ephemeral=runtime._workbench_compose_memory_ephemeral,
            compose_static_system=runtime._workbench_compose_static_system,
            compose_volatile_ephemeral_system=runtime._workbench_compose_volatile_ephemeral_system,
            derive_title=runtime._workbench_derive_title,
            extract_constraints=runtime._workbench_extract_constraints,
            finalize_directive=runtime._workbench_finalize_directive,
            find_session=runtime._workbench_find_session,
            generate_plan_steps=runtime._workbench_generate_plan_steps,
            generate_step_outcome=runtime._workbench_generate_step_outcome,
            git_status_snapshot=runtime._workbench_git_status_snapshot,
            is_blank_goal=runtime._workbench_is_blank_goal,
            is_default_title=runtime._workbench_is_default_title,
            normalize_attachments=runtime._workbench_normalize_attachments,
            plan_from_input=runtime._workbench_plan_from_input,
            project_memory_key=runtime._workbench_project_memory_key,
            promote_file_artifacts=runtime._workbench_promote_file_artifacts,
            register_attachments_kb=runtime._workbench_register_attachments_kb,
            resolve_workspace_async=runtime._workbench_resolve_workspace_dir_async,
            step_dependencies_satisfied=runtime._workbench_step_dependencies_satisfied,
            sync_agent_task_meta=runtime._workbench_sync_agent_task_meta,
            workspace_file_snapshot=runtime._workbench_workspace_file_snapshot,
            workspace_root=runtime._workbench_workspace_root,
            workspace_text_snapshot=runtime._workbench_workspace_text_snapshot,
            write_store=runtime._write_workbench_store,
            append_notification=runtime.append_notification,
            schedule_capture=runtime.schedule_capture,
        )


class TaskExecutionApplicationService:
    def __init__(self, *, dependencies: TaskExecutionDependencies, task_runs: Any, db_path: str) -> None:
        self.dependencies = dependencies
        self.task_runs = task_runs
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def agent_run_error_response(exc: Any) -> TaskExecutionResponse:
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
        from cyrene.runtime.model_configuration import selectable_model_candidates
        selected_candidate = next((candidate for candidate in selectable_model_candidates() if selected_key in {str(candidate.get("id") or "").strip(), str(candidate.get("model") or "").strip(), str(candidate.get("name") or "").strip()}), None)
        if selected_candidate is None:
            return TaskExecutionResponse({"error": "configured model not found"}, 400) if requested_model else None
        from cyrene.model_runtime.client import set_session_model_preference
        requested_effort = str(body.get("reasoningEffort") or "").strip().lower()
        selected_effort = requested_effort or str(session.get("reasoningEffort") or selected_candidate.get("reasoning_effort") or "").strip().lower()
        selected_model_id = str(selected_candidate.get("id") or selected_key).strip()
        selected_model_name = str(selected_candidate.get("model") or selected_candidate.get("name") or selected_key).strip()
        set_session_model_preference(session_id, selected_candidate, selected_effort)
        session["modelSelectionId"] = selected_model_id
        session["model"] = selected_model_name
        session["reasoningEffort"] = selected_effort
        return None

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
                {'error': '步骤不存在。', 'code': 'step_not_found'}, 404
            ), step_id, run_meta, True, None
        try:
            requested_revision = int(body.get('planDefinitionRevision'))
        except (TypeError, ValueError):
            return TaskExecutionResponse({'error': 'invalid planDefinitionRevision'}, 400), step_id, run_meta, True, step
        current_revision = int(session.get('planDefinitionRevision') or 0)
        if requested_revision != current_revision:
            return TaskExecutionResponse(
                {'error': '计划已发生变化，请重新确认后执行。', 'code': 'stale_plan_revision'}, 409
            ), step_id, run_meta, True, step
        try:
            approved_revision = int(session.get('approvedPlanDefinitionRevision'))
        except (TypeError, ValueError):
            approved_revision = -1
        if approved_revision != current_revision:
            return TaskExecutionResponse(
                {'error': '当前计划尚未获得执行确认。', 'code': 'plan_not_approved'}, 409
            ), step_id, run_meta, True, step
        ready, unmet_ids = self.dependencies.step_dependencies_satisfied(plan, step_id)
        if not ready:
            titles = {str(item.get('id') or ''): str(item.get('title') or '')
                      for item in plan if isinstance(item, dict)}
            return TaskExecutionResponse({
                'error': '前置步骤尚未完成：' + '、'.join(
                    titles.get(dependency_id, dependency_id) for dependency_id in unmet_ids
                ), 'code': 'unmet_dependencies',
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
            f'已完成，本步调用工具 {len(tool_events)} 次。' if tool_events else '已完成该步骤。'
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
            return TaskExecutionResponse({'error': 'input is required'}, status_code=400)
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({'error': 'session not found'}, status_code=404)
        model_error = self.apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error
        task_meta_before = self.dependencies.capture_task_meta(session)
        validation_error, step_id, run_meta, is_step_run, step = self._prepare_step_run(
            session, body
        )
        if validation_error:
            return validation_error
        run_started_at = self.dependencies.utc_now()
        if not is_step_run:
            constraints = await self.dependencies.extract_constraints(user_input)
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
        run_start_ts = run_started_at
        workspace_root = self.dependencies.workspace_root(project)
        git_status_before = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_before = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_before = self.dependencies.workspace_text_snapshot(workspace_root)
        memory_pair = self.dependencies.compose_memory_ephemeral(project, session)
        ephemeral_system = self.dependencies.compose_ephemeral_system(project, session, step_id=step_id if is_step_run else '', workspace_root=workspace_root, memory_pair=memory_pair)
        volatile_ephemeral_system = self.dependencies.compose_volatile_ephemeral_system(project, session, memory_pair=memory_pair)
        agent_error = None
        try:
            agent_reply = await self.dependencies.agent_reply(user_input, session, constraints, attachments=attachments, permission_mode=mode, command=command, project_workspace=await self.dependencies.resolve_workspace_async(project), ephemeral_system=ephemeral_system, volatile_ephemeral_system=volatile_ephemeral_system, static_system_extra=self.dependencies.compose_static_system(project, session), conversation_source='' if ui_instance_id else 'webui', ui_instance_id=ui_instance_id, client_request_id=client_request_id)
        except self.dependencies.agent_run_error as exc:
            agent_error = exc
            agent_reply = exc.message
        git_status_after = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_after = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_after = self.dependencies.workspace_text_snapshot(workspace_root)
        if agent_error is None:
            agent_reply, awaiting_user = self.dependencies.apply_pending(session, session_id, agent_reply)
        else:
            awaiting_user = False
        if is_step_run and awaiting_user:
            session['pendingPlanStep'] = {'stepId': step_id, 'continueAll': bool(run_meta.get('continueAll'))}
        elif is_step_run:
            session.pop('pendingPlanStep', None)
        self.dependencies.sync_agent_task_meta(session, session_id, task_meta_before)
        session['agentReply'] = agent_reply
        if is_step_run and (not awaiting_user) and (agent_error is None) and step:
            try:
                await asyncio.wait_for(self.dependencies.generate_step_outcome(step, agent_reply, user_input), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
        if not command and (not awaiting_user) and (agent_error is None):
            self.dependencies.schedule_capture(self.dependencies.project_memory_key(project), user_input, agent_reply)
        normalized_attachments = self.dependencies.normalize_attachments(attachments)
        public_attachments = [build_public_attachment_payload(item) for item in normalized_attachments]
        if normalized_attachments:
            await self.dependencies.register_attachments_kb(session_id, normalized_attachments)
        run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        activity_events = self.dependencies.collect_run_activity_events(session_id, run_start_ts, run_id, workspace_root)
        tool_call_events = [event for event in activity_events if event.get('type') == 'ToolCallEvent']
        file_changes = self.dependencies.collect_run_file_changes(tool_call_events, git_status_before, git_status_after, workspace_files_before, workspace_files_after, workspace_root, f'{user_input}\n{agent_reply}', workspace_text_before=workspace_text_before, workspace_text_after=workspace_text_after)
        finished_at = self.dependencies.utc_now()
        self._update_create_run_status(
            session, step, is_step_run=is_step_run, step_id=step_id, run_meta=run_meta, agent_error=agent_error, awaiting_user=awaiting_user, file_changes=file_changes, tool_events=tool_call_events, started_at=run_started_at, finished_at=finished_at,
        )
        events = [{'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent', 'runId': run_id, 'createdAt': run_started_at, 'body': user_input or '[附件]', 'attachments': public_attachments}, *activity_events, {'id': self.dependencies.short_id('event'), 'type': 'AgentErrorEvent' if agent_error else 'AgentResponseEvent', 'runId': run_id, 'createdAt': finished_at, 'body': agent_reply}, {'id': self.dependencies.short_id('event'), 'type': 'PlanUpdatedEvent', 'runId': run_id, 'createdAt': finished_at, 'stepCount': len(session.get('plan') or [])}]
        if is_step_run and (not awaiting_user) and step:
            events.append({'id': self.dependencies.short_id('event'), 'type': 'ExecutionFailed' if agent_error else 'ExecutionFinished', 'runId': run_id, 'stepId': step_id, 'createdAt': finished_at, 'body': f"步骤「{step.get('title') or step_id}」执行失败：{agent_error.message}" if agent_error else f"步骤「{step.get('title') or step_id}」执行完成。"})
        run = {'id': run_id, 'taskId': session_id, 'userInput': user_input, 'agentResponse': agent_reply, 'status': 'failed' if agent_error else 'awaiting_user' if awaiting_user else 'completed', 'startedAt': run_started_at, 'endedAt': finished_at, 'contextPackId': self.dependencies.short_id('ctx'), 'events': events, 'fileChanges': file_changes, 'toolCalls': [{'tool': e['tool'], 'argsPreview': e['argsPreview']} for e in tool_call_events], 'artifacts': [], 'attachments': public_attachments, 'mode': mode, 'error': agent_error.message if agent_error else None}
        self.task_runs.upsert_task_run(session, run)
        session.setdefault('events', []).extend(events)
        self.dependencies.promote_file_artifacts(session, file_changes, finished_at)
        if not awaiting_user and agent_error is None:
            await self.dependencies.archive_run_knowledge(project, session, run, workspace_root, finished_at)
        session['updatedAt'] = finished_at
        project['updatedAt'] = finished_at
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        self.finalize_host_actions_after_reply(session_id, client_request_id)
        self.dependencies.append_notification(title='任务执行失败' if agent_error else '任务回复完成', body=f"Agent 执行任务「{session.get('title') or '未命名任务'}」失败：{agent_error.message}" if agent_error else f"Agent 已更新任务「{session.get('title') or '未命名任务'}」。", tab='comment', project_ref=project.get('id'), source='task_reply', source_label='任务', link_label=str(session.get('title') or ''), meta={'sessionId': session_id, 'runId': run_id})
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
            return TaskExecutionResponse({'error': 'message is required'}, status_code=400)
        _bgt = await self.dependencies.check_budget_gate(session_id)
        if _bgt:
            return TaskExecutionResponse(_bgt, status_code=403)
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({'error': 'session not found'}, status_code=404)
        model_error = self.apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error
        task_meta_before = self.dependencies.capture_task_meta(session)
        chat_run_start_ts = self.dependencies.utc_now()
        workspace_root = self.dependencies.workspace_root(project)
        git_status_before = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_before = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_before = self.dependencies.workspace_text_snapshot(workspace_root)
        memory_pair = self.dependencies.compose_memory_ephemeral(project, session)
        ephemeral_system = self.dependencies.compose_ephemeral_system(project, session, workspace_root=workspace_root, memory_pair=memory_pair)
        ephemeral_system = (ephemeral_system + '\n\n' + self.dependencies.task_reply_directive).strip()
        volatile_ephemeral_system = self.dependencies.compose_volatile_ephemeral_system(project, session, memory_pair=memory_pair)
        agent_command = command or 'workbench-task-reply'
        try:
            agent_reply = await self.dependencies.agent_reply(message, session, [], attachments=attachments, permission_mode=mode, command=agent_command, project_workspace=await self.dependencies.resolve_workspace_async(project), ephemeral_system=ephemeral_system, volatile_ephemeral_system=volatile_ephemeral_system, static_system_extra=self.dependencies.compose_static_system(project, session), conversation_source='' if ui_instance_id else 'webui', ui_instance_id=ui_instance_id, client_request_id=client_request_id)
        except self.dependencies.agent_run_error as exc:
            return self.agent_run_error_response(exc)
        git_status_after = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_after = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_after = self.dependencies.workspace_text_snapshot(workspace_root)
        agent_reply, awaiting_user = self.dependencies.apply_pending(session, session_id, agent_reply)
        self.dependencies.sync_agent_task_meta(session, session_id, task_meta_before)
        session['agentReply'] = agent_reply
        if not command and (not awaiting_user):
            self.dependencies.schedule_capture(self.dependencies.project_memory_key(project), message, agent_reply)
        session['status'] = 'waiting_for_user' if awaiting_user else 'answered'
        now = self.dependencies.utc_now()
        session['updatedAt'] = now
        project['updatedAt'] = now
        chat_run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        chat_tool_events = self.dependencies.collect_run_tool_events(session_id, chat_run_start_ts, chat_run_id, workspace_root)
        file_changes = self.dependencies.collect_run_file_changes(chat_tool_events, git_status_before, git_status_after, workspace_files_before, workspace_files_after, workspace_root, f'{message}\n{agent_reply}', workspace_text_before=workspace_text_before, workspace_text_after=workspace_text_after)
        chat_events = [{'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent', 'runId': chat_run_id, 'createdAt': chat_run_start_ts, 'body': message or '[附件]'}, *chat_tool_events, {'id': self.dependencies.short_id('event'), 'type': 'AgentResponseEvent', 'runId': chat_run_id, 'createdAt': now, 'body': agent_reply}]
        run = {'id': chat_run_id, 'taskId': session_id, 'userInput': message, 'agentResponse': agent_reply, 'status': 'awaiting_user' if awaiting_user else 'completed', 'startedAt': chat_run_start_ts, 'endedAt': now, 'contextPackId': self.dependencies.short_id('ctx'), 'events': chat_events, 'fileChanges': file_changes, 'toolCalls': [{'tool': event.get('tool'), 'argsPreview': event.get('argsPreview', '')} for event in chat_tool_events if isinstance(event, dict) and event.get('type') == 'ToolCallEvent'], 'artifacts': [], 'attachments': [build_public_attachment_payload(item) for item in self.dependencies.normalize_attachments(attachments)], 'mode': mode, 'error': None}
        self.task_runs.upsert_task_run(session, run)
        session.setdefault('events', []).extend(chat_events)
        self.dependencies.promote_file_artifacts(session, file_changes, now)
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        self.finalize_host_actions_after_reply(session_id, client_request_id)
        self.dependencies.append_notification(title='Agent 回复完成', body=f"Agent 在「{session.get('title') or '对话'}」中回复了你。", tab='mention', project_ref=project.get('id'), source='chat_reply', source_label='对话', link_label=str(session.get('title') or ''), meta={'sessionId': session_id})
        return {'ok': True, 'project': project, 'session': session, 'run': run, **payload}

    async def _maybe_generate_title(
        self, session_id: str, session: dict[str, Any], user_input: str
    ) -> None:
        should_generate = bool(
            user_input and not session.get('titleLocked')
            and not session.get('titleNamingStatus')
            and self.dependencies.is_default_title(session.get('title'))
        )
        if not should_generate:
            return
        from cyrene.model_runtime.client import resolve_session_model_candidate
        from cyrene.workbench.session_naming import generate_session_title

        session['titleNamingStatus'] = 'pending'
        session['titleNamingStartedAt'] = self.dependencies.utc_now()
        candidate = resolve_session_model_candidate(session_id)
        candidate_id = str((candidate or {}).get('id') or '')
        candidate_model = str((candidate or {}).get('model') or '')
        try:
            if candidate is None:
                raise RuntimeError('no configured model candidate for task session')
            generated = await generate_session_title(user_input, limit=80, candidate=candidate)
        except Exception as exc:
            self.logger.exception(
                'Workbench task session naming failed [session=%s candidate=%s model=%s error_type=%s]',
                session_id, candidate_id or 'unresolved', candidate_model or 'unresolved',
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
                return TaskExecutionResponse({'error': 'invalid basePlanRevision'}, 400)
            if requested_revision != base_revision:
                return TaskExecutionResponse(
                    {'error': '计划已发生变化，请基于最新计划重试。', 'code': 'stale_plan_revision'}, 409
                )
        steps, acceptance, from_llm, operation = await self.dependencies.generate_plan_steps(
            session, project, feedback=user_input if revising else ''
        )
        payload = self.dependencies.read_store()
        latest_project, latest = self.dependencies.find_session(payload, session_id)
        if not latest or not latest_project:
            return TaskExecutionResponse({'error': 'session not found'}, 404)
        if int(latest.get('planRevision') or 0) != base_revision:
            return TaskExecutionResponse(
                {'error': '计划已在生成期间发生变化，请基于最新计划重试。', 'code': 'stale_plan_revision'}, 409
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
                '我判断这次要求需要整体替换计划，已生成全新步骤。'
                if from_llm and operation == 'replace' else
                '我已按你的说明修订执行计划，并保留了可对应步骤的执行状态。'
                if from_llm else '计划调整服务暂时不可用，已保留原计划。你可以稍后再让我调整。'
            )
        else:
            latest['agentReply'] = (
                '我已结合工作区里的实际内容拆解出执行计划。你可以编辑步骤、顺序和依赖后再执行。'
                if from_llm else
                '计划生成服务暂时不可用，我先给出一份基础计划，你可以编辑后逐步执行，或稍后让我重新拆解。'
            )
        latest['events'] = list(latest.get('events') or []) + [{
            'id': self.dependencies.short_id('event'),
            'type': 'PlanRevised' if revising else 'PlanGenerated',
            'createdAt': now,
            'body': (f"{('整体替换' if operation == 'replace' else '修订')}执行计划，共 {len(steps)} 步。"
                     if revising else f'生成执行计划，共 {len(steps)} 步。')
                    + ('' if from_llm else '（生成失败，保留原计划）' if revising else '（兜底计划）'),
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
            return TaskExecutionResponse({'error': 'input is required'}, status_code=400)
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({'error': 'session not found'}, status_code=404)
        model_error = self.apply_task_model_preference(session_id, body, session)
        if model_error is not None:
            return model_error
        _bgt = await self.dependencies.check_budget_gate(session_id)
        if _bgt:
            return TaskExecutionResponse(_bgt, status_code=403)
        task_meta_before = self.dependencies.capture_task_meta(session)
        await self._maybe_generate_title(session_id, session, user_input)
        if command or (not user_input and attachments):
            kind = 'direct'
        else:
            kind = await self.dependencies.classify_intent(user_input, session)
        now = self.dependencies.utc_now()
        if kind not in ('answer', 'finalize') and self.dependencies.is_blank_goal(session.get('goal')) and user_input:
            session['goal'] = user_input
            if self.dependencies.is_default_title(session.get('title')):
                session['title'] = self.dependencies.derive_title(user_input)
        if kind in ('plan', 'direct') and user_input:
            merged = list(session.get('constraints') or [])
            for item in await self.dependencies.extract_constraints(user_input):
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
        memory_pair = self.dependencies.compose_memory_ephemeral(project, session)
        ephemeral_system = self.dependencies.compose_ephemeral_system(project, session, workspace_root=workspace_root, memory_pair=memory_pair)
        if finalizing:
            ephemeral_system = (ephemeral_system + '\n\n' + self.dependencies.finalize_directive(session)).strip()
        elif repairing_acceptance:
            ephemeral_system = (ephemeral_system + '\n\n' + self.dependencies.acceptance_repair_directive(session)).strip()
        elif kind == 'answer':
            ephemeral_system = (ephemeral_system + '\n\n' + self.dependencies.task_reply_directive).strip()
        volatile_ephemeral_system = self.dependencies.compose_volatile_ephemeral_system(project, session, memory_pair=memory_pair)
        agent_command = command or ('workbench-task-reply' if kind == 'answer' else '')
        try:
            agent_reply = await self.dependencies.agent_reply(user_input, session, [], attachments=attachments, permission_mode=mode, command=agent_command, project_workspace=await self.dependencies.resolve_workspace_async(project), ephemeral_system=ephemeral_system, volatile_ephemeral_system=volatile_ephemeral_system, static_system_extra=self.dependencies.compose_static_system(project, session), conversation_source='' if ui_instance_id else 'webui', ui_instance_id=ui_instance_id, client_request_id=client_request_id)
        except self.dependencies.agent_run_error as exc:
            return self.agent_run_error_response(exc)
        git_status_after = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_after = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_after = self.dependencies.workspace_text_snapshot(workspace_root)
        agent_reply, awaiting_user = self.dependencies.apply_pending(session, session_id, agent_reply)
        self.dependencies.sync_agent_task_meta(session, session_id, task_meta_before)
        session['agentReply'] = agent_reply
        if not command and (not awaiting_user):
            self.dependencies.schedule_capture(self.dependencies.project_memory_key(project), user_input, agent_reply)
        if finalizing and (not awaiting_user):
            self.dependencies.schedule_task_report(project, session)
        session['status'] = 'waiting_for_user' if awaiting_user else 'review' if finalizing or repairing_acceptance else 'acted' if kind == 'direct' else 'answered'
        normalized_attachments = self.dependencies.normalize_attachments(attachments)
        public_attachments = [build_public_attachment_payload(item) for item in normalized_attachments]
        if normalized_attachments:
            await self.dependencies.register_attachments_kb(session_id, normalized_attachments)
        run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        activity_events = self.dependencies.collect_run_activity_events(session_id, run_start_ts, run_id, workspace_root)
        tool_call_events = [event for event in activity_events if event.get('type') == 'ToolCallEvent']
        file_changes = self.dependencies.collect_run_file_changes(tool_call_events, git_status_before, git_status_after, workspace_files_before, workspace_files_after, workspace_root, f'{user_input}\n{agent_reply}', workspace_text_before=workspace_text_before, workspace_text_after=workspace_text_after)
        finished_at = self.dependencies.utc_now()
        events = [{'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent', 'runId': run_id, 'createdAt': run_start_ts, 'body': user_input or '[附件]', 'attachments': public_attachments}, *activity_events, {'id': self.dependencies.short_id('event'), 'type': 'AgentResponseEvent', 'runId': run_id, 'createdAt': finished_at, 'body': agent_reply}]
        run = {'id': run_id, 'taskId': session_id, 'userInput': user_input, 'agentResponse': agent_reply, 'status': 'awaiting_user' if awaiting_user else 'completed', 'startedAt': run_start_ts, 'endedAt': finished_at, 'contextPackId': self.dependencies.short_id('ctx'), 'events': events, 'fileChanges': file_changes, 'toolCalls': [{'tool': e['tool'], 'argsPreview': e['argsPreview']} for e in tool_call_events], 'artifacts': [], 'attachments': public_attachments, 'mode': mode, 'error': None}
        self.task_runs.upsert_task_run(session, run)
        session.setdefault('events', []).extend(events)
        self.dependencies.promote_file_artifacts(session, file_changes, finished_at)
        if not awaiting_user:
            await self.dependencies.archive_run_knowledge(project, session, run, workspace_root, finished_at)
        session['updatedAt'] = finished_at
        project['updatedAt'] = finished_at
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        self.finalize_host_actions_after_reply(session_id, client_request_id)
        self.dependencies.append_notification(title='Agent 回复完成', body=f"Agent 在「{session.get('title') or '任务'}」中" + ('整理并交付了任务成果，待你验收。' if finalizing else '参考验收结果继续修改了当前任务。' if repairing_acceptance else '执行了你的指令。' if kind == 'direct' else '回复了你。'), tab='comment', project_ref=project.get('id'), source='task_reply', source_label='任务', link_label=str(session.get('title') or ''), meta={'sessionId': session_id, 'runId': run_id})
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
        session['agentReply'] = '回答已提交，但继续执行已被你中断。可稍后继续。'
        for step in session.get('plan') or []:
            if not isinstance(step, dict) or step.get('status') != 'running':
                continue
            step.update(status='pending', startedAt=None,
                        currentAction='已停止，可重新执行。', updatedAt=finished_at)
        run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        events = [
            {'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent',
             'runId': run_id, 'createdAt': run_started_at, 'body': f'[确认] {answer_text}'},
            {'id': self.dependencies.short_id('event'), 'type': 'Paused',
             'runId': run_id, 'createdAt': finished_at, 'body': '用户中断了回答后的继续执行。'},
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
                              currentAction='权限请求被拒绝，可调整命令后重新执行。')
                session['status'] = 'paused'
            else:
                target['status'] = 'completed'
                target['completedAt'] = finished_at
                target['currentAction'] = (
                    f"{'用户已确认；本步完成' if goal_loop_step else '已完成'}，调用工具 {len(tool_events)} 次。"
                    if tool_events else '用户已确认，本步骤完成。' if goal_loop_step else '已完成该步骤。'
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
        """Answer a paused run's permission / clarification question and resume
            the SAME round inside the project scope. The continued reply (or a follow-up
            question) replaces the question card. Mirrors the legacy chat answer flow,
            but session-scoped to this Workbench task."""
        question_id = str(body.get('question_id') or '').strip()
        answer_text = str(body.get('answer') or body.get('selected_option') or '').strip()
        ui_instance_id = str(body.get('uiInstanceId') or '').strip()
        if not question_id or not answer_text:
            return TaskExecutionResponse({'error': 'question_id and answer are required'}, status_code=400)
        payload = self.dependencies.read_store()
        project, session = self.dependencies.find_session(payload, session_id)
        if not session or not project:
            return TaskExecutionResponse({'error': 'session not found'}, status_code=404)
        pending = session.get('pendingQuestion') if isinstance(session.get('pendingQuestion'), dict) else None
        if not pending or str(pending.get('id') or '') != question_id:
            return TaskExecutionResponse({'error': 'no matching pending question'}, status_code=409)
        pending_plan_step = dict(session.get('pendingPlanStep')) if isinstance(session.get('pendingPlanStep'), dict) else None
        permission_kinds = {'scope_elevation', 'write_permission_request', 'read_elevation', 'subshell_elevation', 'delete_confirmation', 'task_permission_request', 'git_commit', 'destructive_confirmation', 'external_delivery_request', 'external_upload_confirmation'}
        pending_options = pending.get('options') if isinstance(pending.get('options'), list) else []
        normalized_answer = answer_text.strip().casefold()
        explicit_denial = normalized_answer == str(pending_options[-1]).strip().casefold() if pending_options else normalized_answer in {'拒绝', '不允许', '否', 'reject', 'deny', 'no'}
        permission_denied = str(pending.get('kind') or '') in permission_kinds and explicit_denial
        if pending_plan_step and bool(pending_plan_step.get('goalLoop')) and (not permission_denied):
            from cyrene.workbench.goal_loop import begin_async_answer
            if await begin_async_answer(self.db_path, session_id, question_id, answer_text):
                payload = self.dependencies.read_store()
                project, session = self.dependencies.find_session(payload, session_id)
                return {'ok': True, 'awaitingUser': False, 'continuePlanExecution': False, 'project': project, 'session': session, 'run': None, **payload}
        now = self.dependencies.utc_now()
        run_start_ts = now
        workspace_root = self.dependencies.workspace_root(project)
        workspace_dir = await self.dependencies.resolve_workspace_async(project)
        git_status_before = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_before = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_before = self.dependencies.workspace_text_snapshot(workspace_root)
        from cyrene.runtime.host_bridge import resolve_conversation_source
        conversation_source = await resolve_conversation_source(ui_instance_id)
        try:
            agent_reply = await self.dependencies.answer_pending(session_id, question_id, answer_text, workspace_dir, ui_instance_id=ui_instance_id, conversation_source=conversation_source)
        except asyncio.CancelledError:
            cancelled = self._cancelled_answer_response(session_id, answer_text, run_start_ts)
            if cancelled is not None:
                return cancelled
            raise
        except Exception:
            self.logger.exception('Workbench answer-resume failed for session %s', session_id)
            return TaskExecutionResponse({'error': 'answer resume failed'}, status_code=502)
        git_status_after = self.dependencies.git_status_snapshot(workspace_root)
        workspace_files_after = self.dependencies.workspace_file_snapshot(workspace_root)
        workspace_text_after = self.dependencies.workspace_text_snapshot(workspace_root)
        agent_reply, awaiting_user = self.dependencies.apply_pending(session, session_id, agent_reply)
        session['agentReply'] = agent_reply
        if not awaiting_user:
            session.pop('pendingQuestion', None)
            session['status'] = 'acted'
            self.dependencies.schedule_capture(self.dependencies.project_memory_key(project), answer_text, agent_reply)
        run_id = self.task_runs.current_task_run_id() or self.dependencies.short_id('run')
        activity_events = self.dependencies.collect_run_activity_events(session_id, run_start_ts, run_id, workspace_root)
        tool_call_events = [e for e in activity_events if e.get('type') == 'ToolCallEvent']
        file_changes = self.dependencies.collect_run_file_changes(tool_call_events, git_status_before, git_status_after, workspace_files_before, workspace_files_after, workspace_root, f'{answer_text}\n{agent_reply}', workspace_text_before=workspace_text_before, workspace_text_after=workspace_text_after)
        finished_at = self.dependencies.utc_now()
        events = [{'id': self.dependencies.short_id('event'), 'type': 'UserMessageEvent', 'runId': run_id, 'createdAt': now, 'body': f'[确认] {answer_text}'}, *activity_events, {'id': self.dependencies.short_id('event'), 'type': 'AgentResponseEvent', 'runId': run_id, 'createdAt': finished_at, 'body': agent_reply}]
        run = {'id': run_id, 'taskId': session_id, 'userInput': answer_text, 'agentResponse': agent_reply, 'status': 'awaiting_user' if awaiting_user else 'completed', 'startedAt': run_start_ts, 'endedAt': finished_at, 'contextPackId': self.dependencies.short_id('ctx'), 'events': events, 'fileChanges': file_changes, 'toolCalls': [{'tool': e['tool'], 'argsPreview': e['argsPreview']} for e in tool_call_events], 'artifacts': [], 'attachments': [], 'mode': 'auto', 'error': None}
        self.task_runs.upsert_task_run(session, run)
        session.setdefault('events', []).extend(events)
        self.dependencies.promote_file_artifacts(session, file_changes, finished_at)
        continue_plan_execution = self._apply_answered_step(
            session, pending_plan_step, awaiting_user=awaiting_user,
            permission_denied=permission_denied, tool_events=tool_call_events,
            file_changes=file_changes, finished_at=finished_at,
        )
        if not awaiting_user:
            await self.dependencies.archive_run_knowledge(project, session, run, workspace_root, finished_at)
        session['updatedAt'] = finished_at
        project['updatedAt'] = finished_at
        payload['activeSessionId'] = session_id
        self.dependencies.write_store(payload)
        self.finalize_host_actions_after_reply(session_id)
        if pending_plan_step and bool(pending_plan_step.get('goalLoop')) and (not awaiting_user):
            from cyrene.workbench.goal_loop import resume_after_answer
            await resume_after_answer(self.db_path, session_id, permission_denied=permission_denied)
        return {'ok': True, 'awaitingUser': awaiting_user, 'continuePlanExecution': continue_plan_execution, 'project': project, 'session': session, 'run': run, **payload}


__all__ = ["TaskExecutionApplicationService", "TaskExecutionResponse"]
