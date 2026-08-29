"""Workbench project repository and durable task-plan mutations."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from cyrene.config import DB_PATH, WORKSPACE_DIR
from cyrene.localization import localized
from cyrene.workbench.artifacts import artifact_runtime
from cyrene.workbench.planning import planning_runtime
from cyrene.workbench.projects import project_runtime
from cyrene.workbench.persistence.store import patch_project_bundle_fields, read_project_bundle, summarize_task_session, write_project_bundle

logger = logging.getLogger(__name__)
_WORKBENCH_STORE_LOCK = threading.RLock()
_db_path = str(DB_PATH)

def _configure_workbench_store(db_path: str) -> None:
    global _db_path
    _db_path = str(db_path or DB_PATH)

def _read_workbench_store() -> dict[str, Any]:
    with _WORKBENCH_STORE_LOCK:
        try:
            raw = read_project_bundle(_db_path, project_runtime._workbench_default_project, _workbench_session_summary)
            if not isinstance(raw, dict) or not isinstance(raw.get('projects'), list):
                raw = write_project_bundle(_db_path, project_runtime._workbench_default_project(), project_runtime._workbench_default_project, _workbench_session_summary)
            if not raw['projects']:
                raw = write_project_bundle(_db_path, project_runtime._workbench_default_project(), project_runtime._workbench_default_project, _workbench_session_summary, base_value=getattr(raw, '_workbench_base', None))
            _workbench_ensure_invariants(raw)
            return raw
        except Exception:
            logger.exception('Failed to read Workbench state from SQLite')
            raise

def _read_workbench_store_lightweight() -> dict[str, Any]:
    """Read project/task state without hydrating every session payload."""
    with _WORKBENCH_STORE_LOCK:
        raw = read_project_bundle(_db_path, project_runtime._workbench_default_project, _workbench_session_summary, lightweight=True)
        if isinstance(raw, dict) and isinstance(raw.get('projects'), list) and raw['projects']:
            return raw
    return _read_workbench_store()

def find_workbench_project_lightweight(project_id: str) -> dict[str, Any] | None:
    """Look up one project without hydrating unrelated task sessions."""
    target_id = str(project_id or '').strip()
    if not target_id:
        return None
    raw = _read_workbench_store_lightweight()
    project = _workbench_find_project(raw, target_id)
    if not isinstance(project, dict):
        return None
    result = dict(project)
    relocated_root = artifact_runtime._workbench_workspace_root(result)
    if relocated_root is not None:
        result['workspacePath'] = str(relocated_root)
    return result


def resolve_project_workspace_dir(project: dict[str, Any] | None) -> str:
    """Resolve and create the workspace owned by one Workbench project."""

    root = artifact_runtime._workbench_workspace_root(project)
    if root is None:
        return ""
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning(
            "Workbench workspace unavailable, using global: %s",
            str((project or {}).get("workspacePath") or ""),
        )
        return ""
    return str(root)


async def resolve_project_workspace_dir_async(
    project: dict[str, Any] | None,
) -> str:
    """Resolve a project workspace without blocking the HTTP event loop."""

    return await asyncio.to_thread(resolve_project_workspace_dir, project)

def _write_workbench_store(payload: dict[str, Any], *, base_value: dict[str, Any] | None=None) -> None:
    with _WORKBENCH_STORE_LOCK:
        merged = write_project_bundle(_db_path, payload, project_runtime._workbench_default_project, _workbench_session_summary, base_value=base_value)
        payload.clear()
        payload.update(merged)
        if hasattr(payload, '_workbench_base'):
            payload._workbench_base = getattr(merged, '_workbench_base', dict(merged))
    from cyrene.observability.debug import publish_event_sync
    publish_event_sync({'type': 'task_board_changed'})

def _persist_workbench_selection(project_id: str | None, session_id: str | None) -> dict[str, Any]:
    """Persist only the active selection, without task/workspace invariant scans."""
    fields: dict[str, Any] = {}
    if project_id is not None:
        fields['activeProjectId'] = str(project_id).strip()
    if session_id is not None:
        fields['activeSessionId'] = str(session_id).strip()
    if not fields:
        return {}
    with _WORKBENCH_STORE_LOCK:
        return patch_project_bundle_fields(_db_path, fields, project_runtime._workbench_default_project, _workbench_session_summary)

def _workbench_ensure_invariants(payload: dict[str, Any]) -> bool:
    changed = False
    projects = payload.setdefault('projects', [])
    now = project_runtime._utc_now_iso()
    for project in projects:
        project.setdefault('id', project_runtime._short_id('project'))
        project.setdefault('name', 'Workspace')
        project.setdefault('description', '')
        project.setdefault('icon', 'spark')
        project.setdefault('color', '')
        project.setdefault('template', 'blank')
        project.setdefault('workspacePath', str(WORKSPACE_DIR))
        project.setdefault('workspacePathSource', 'user')
        project.setdefault('status', 'active')
        project.setdefault('model', project_runtime._get_model())
        project.setdefault('accountTier', 'Pro')
        project.setdefault('context', {'summary': '', 'stack': [], 'decisions': [], 'knowledgeDocumentIds': []})
        project.setdefault('createdAt', now)
        project.setdefault('updatedAt', now)
        relocated_root = artifact_runtime._workbench_workspace_root(project)
        if relocated_root is not None and str(project.get('workspacePath') or '') != str(relocated_root):
            project['workspacePath'] = str(relocated_root)
            changed = True
        project.setdefault('dataKey', project_runtime._safe_workbench_data_key(project.get('id')))
        sessions = project.setdefault('sessions', [])
        if not sessions:
            sessions.append(project_runtime._workbench_new_session(project['id'], '新任务', '', now))
            changed = True
        for session in sessions:
            session.setdefault('projectId', project['id'])
            session.setdefault('kind', 'task')
            session.setdefault('status', 'idle')
            session.setdefault('priority', 'medium')
            session.setdefault('createdAt', now)
            session.setdefault('updatedAt', now)
            session.setdefault('agentReply', '')
            session.setdefault('plan', [])
            session.setdefault('planRevision', 0)
            session.setdefault('planDefinitionRevision', 0)
            session.setdefault('approvedPlanDefinitionRevision', None)
            session.setdefault('events', [])
            session.setdefault('runs', [])
            session.setdefault('artifacts', [])
            session.setdefault('acceptanceCriteria', [])
            session.setdefault('summary', None)
            session.setdefault('titleLocked', False)
            plan = session.get('plan') if isinstance(session.get('plan'), list) else []
            for index, step in enumerate(plan):
                if not isinstance(step, dict):
                    continue
                if not isinstance(step.get('dependsOn'), list):
                    step['dependsOn'] = []
                    changed = True
                if step.get('order') != index + 1:
                    step['order'] = index + 1
                    changed = True
    if projects and (not payload.get('activeProjectId')):
        payload['activeProjectId'] = projects[0].get('id')
        changed = True
    if projects and (not payload.get('activeSessionId')):
        first_sessions = projects[0].get('sessions') or []
        payload['activeSessionId'] = first_sessions[0].get('id') if first_sessions else ''
        changed = True
    return changed

def _workbench_find_project(payload: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    for project in payload.get('projects', []):
        if str(project.get('id') or '') == project_id:
            return project
    return None

def _workbench_session_summary(session: dict[str, Any]) -> dict[str, Any]:
    """Return the rail/list shape for a task session without history payloads."""
    return summarize_task_session(session)

def _workbench_lightweight_store(payload: dict[str, Any]) -> dict[str, Any]:
    """Return projects with session summaries, keeping only the active session full."""
    active_project_id = str(payload.get('activeProjectId') or '')
    active_session_id = str(payload.get('activeSessionId') or '')
    projects: list[dict[str, Any]] = []
    for project in payload.get('projects', []):
        if not isinstance(project, dict):
            continue
        next_project = dict(project)
        next_sessions: list[dict[str, Any]] = []
        for session in project.get('sessions') or []:
            if not isinstance(session, dict):
                continue
            if str(project.get('id') or '') == active_project_id and str(session.get('id') or '') == active_session_id:
                full = dict(session)
                full.pop('isSummary', None)
                next_sessions.append(full)
            elif session.get('isSummary'):
                next_sessions.append(dict(session))
            else:
                next_sessions.append(_workbench_session_summary(session))
        next_project['sessions'] = next_sessions
        projects.append(next_project)
    return {**{k: v for k, v in payload.items() if k != 'projects'}, 'projects': projects}

def _workbench_project_shell(project: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return project metadata with session summaries only."""
    if not isinstance(project, dict):
        return None
    shell = dict(project)
    shell['sessions'] = [_workbench_session_summary(session) for session in project.get('sessions') or [] if isinstance(session, dict)]
    return shell

def _workbench_find_session(payload: dict[str, Any], session_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for project in payload.get('projects', []):
        for session in project.get('sessions', []):
            if str(session.get('id') or '') == session_id:
                return (project, session)
    return (None, None)

def _task_plan_event_body(operation: str, event_source: str, reason: str) -> str:
    action = {
        'add': localized('Added an execution step.', '新增执行步骤。'),
        'update': localized('Updated an execution step.', '更新执行步骤。'),
        'set_dependencies': localized('Updated step dependencies.', '更新步骤依赖。'),
        'delete': localized('Deleted an execution step.', '删除执行步骤。'),
        'reorder': localized('Reordered the execution steps.', '调整执行步骤顺序。'),
    }.get(operation, localized('Updated the execution plan.', '更新执行计划。'))
    if event_source != 'user':
        action = localized(
            f'The Agent {action[0].lower() + action[1:]}',
            'Agent 根据当前输入' + action,
        )
    reason_text = str(reason or '').strip()
    if reason_text:
        action += localized(' Reason: ', ' 原因：') + reason_text[:500]
    return action

def update_task_plan_for_session(session_id: str, operation: str, *, step_id: str='', step: dict[str, Any] | None=None, fields: dict[str, Any] | None=None, ordered_step_ids: list[Any] | None=None, depends_on: list[Any] | None=None, reason: str='', event_source: str='agent') -> dict[str, Any]:
    """Mutate the current Workbench task plan for the main-agent tool.

    The user-facing HTTP mutation endpoint blocks while an agent is running.
    This helper is intentionally separate so the running main task agent can
    update its own pending plan steps when new input changes the plan.
    """
    sid = str(session_id or '').strip()
    op = str(operation or '').strip().lower()
    if not sid:
        return {'ok': False, 'error': localized('No active task session.', '没有活动的任务会话。'), 'code': 'no_session'}
    with _WORKBENCH_STORE_LOCK:
        payload = _read_workbench_store()
        project, session = _workbench_find_session(payload, sid)
        if not session or not project:
            return {'ok': False, 'error': localized('Session not found.', '未找到会话。'), 'code': 'session_not_found'}
        if str(session.get('kind') or 'task') != 'task':
            return {'ok': False, 'error': localized('Only Workbench task sessions support task plans.', '只有工作台任务会话支持任务计划。'), 'code': 'not_task_session'}
        current_revision = int(session.get('planDefinitionRevision') or 0)
        plan = planning_runtime._workbench_normalize_plan(session.get('plan'), task_id=sid)
        by_id = {str(item.get('id') or ''): item for item in plan if isinstance(item, dict)}
        field_values = fields if isinstance(fields, dict) else {}
        structure_operation = op in ('add', 'reorder', 'set_dependencies')
        if op == 'update' and any((field in field_values for field in ('title', 'description', 'dependsOn'))):
            structure_operation = True
        if structure_operation and planning_runtime._workbench_plan_has_started(plan):
            return {'ok': False, 'error': localized('The plan has started. Only the commands and context of pending steps can be edited.', '计划已经开始执行，只能编辑尚未运行步骤的命令和上下文。'), 'code': 'plan_started'}
        if op == 'add':
            step_input = step if isinstance(step, dict) else {}
            title = str(step_input.get('title') or '').strip()
            if not title:
                return {'ok': False, 'error': localized('Step title cannot be empty.', '步骤标题不能为空。'), 'code': 'empty_step_title'}
            if len(plan) >= 12:
                return {'ok': False, 'error': localized('An execution plan can contain at most 12 steps.', '执行计划最多包含 12 个步骤。'), 'code': 'plan_too_large'}
            new_step = planning_runtime._workbench_new_plan_step(title[:160], str(step_input.get('description') or '').strip()[:4000], len(plan) + 1, sid)
            new_step['dependsOn'] = planning_runtime._workbench_dependency_ids(step_input.get('dependsOn'))
            plan.append(new_step)
        elif op == 'update':
            target_id = str(step_id or '').strip()
            target = by_id.get(target_id)
            if not target:
                return {'ok': False, 'error': localized('Step not found.', '步骤不存在。'), 'code': 'step_not_found'}
            allowed_fields = {'title', 'description', 'dependsOn', 'promptOverride', 'contextFiles'}
            if any((field not in allowed_fields for field in field_values)):
                return {'ok': False, 'error': localized('The request contains a step field that cannot be changed.', '包含不允许修改的步骤字段。'), 'code': 'invalid_step_fields'}
            if str(target.get('status') or 'pending') != 'pending':
                return {'ok': False, 'error': localized('Only pending steps can be edited.', '只能编辑尚未运行的步骤。'), 'code': 'step_started'}
            if 'title' in field_values:
                title = str(field_values.get('title') or '').strip()
                if not title:
                    return {'ok': False, 'error': localized('Step title cannot be empty.', '步骤标题不能为空。'), 'code': 'empty_step_title'}
                target['title'] = title[:160]
            if 'description' in field_values:
                target['description'] = str(field_values.get('description') or '').strip()[:4000]
            if 'dependsOn' in field_values:
                target['dependsOn'] = planning_runtime._workbench_dependency_ids(field_values.get('dependsOn'))
            if 'promptOverride' in field_values:
                target['promptOverride'] = str(field_values.get('promptOverride') or '')[:12000]
            if 'contextFiles' in field_values:
                context_files = field_values.get('contextFiles')
                if not isinstance(context_files, list):
                    return {'ok': False, 'error': localized('contextFiles must be a list.', 'contextFiles 必须是列表。'), 'code': 'invalid_context_files'}
                target['contextFiles'] = context_files[:30]
        elif op == 'set_dependencies':
            target_id = str(step_id or '').strip()
            target = by_id.get(target_id)
            if not target:
                return {'ok': False, 'error': localized('Step not found.', '步骤不存在。'), 'code': 'step_not_found'}
            target['dependsOn'] = planning_runtime._workbench_dependency_ids(depends_on)
        elif op == 'delete':
            target_id = str(step_id or '').strip()
            target = by_id.get(target_id)
            if not target:
                return {'ok': False, 'error': localized('Step not found.', '步骤不存在。'), 'code': 'step_not_found'}
            if str(target.get('status') or 'pending') != 'pending':
                return {'ok': False, 'error': localized('Only pending steps can be deleted.', '只能删除尚未运行的步骤。'), 'code': 'step_started'}
            dependent_titles = [str(item.get('title') or '') for item in plan if target_id in planning_runtime._workbench_dependency_ids(item.get('dependsOn'))]
            if dependent_titles:
                return {'ok': False, 'error': localized('This step is still required by: ', '该步骤仍被以下步骤依赖：') + localized(', ', '、').join(dependent_titles), 'code': 'step_has_dependents'}
            plan = [item for item in plan if str(item.get('id') or '') != target_id]
        elif op == 'reorder':
            ordered_ids = planning_runtime._workbench_dependency_ids(ordered_step_ids)
            current_ids = [str(item.get('id') or '') for item in plan]
            if len(ordered_ids) != len(current_ids) or set(ordered_ids) != set(current_ids):
                return {'ok': False, 'error': localized('The step order does not match the current plan.', '步骤顺序与当前计划不一致。'), 'code': 'invalid_reorder'}
            plan = [by_id[item_id] for item_id in ordered_ids]
        else:
            return {'ok': False, 'error': localized('Unsupported plan operation.', '不支持此计划操作。'), 'code': 'unsupported_operation'}
        plan = planning_runtime._workbench_normalize_plan(plan, task_id=sid)
        valid, error_message, error_code = planning_runtime._workbench_validate_plan_graph(plan)
        if not valid:
            return {'ok': False, 'error': error_message, 'code': error_code}
        now = project_runtime._utc_now_iso()
        session['plan'] = plan
        session['planRevision'] = int(session.get('planRevision') or 0) + 1
        session['planDefinitionRevision'] = current_revision + 1
        session['approvedPlanDefinitionRevision'] = None
        if str(session.get('status') or '') == 'waiting_for_approval':
            session['status'] = 'planning'
            session['agentReply'] = localized(
                'The plan changed. Review it again before running it.',
                '计划已修改，请重新确认后执行。',
            )
        event_body = _task_plan_event_body(op, event_source, reason)
        session['events'] = list(session.get('events') or []) + [{'id': project_runtime._short_id('event'), 'type': 'PlanUpdatedEvent', 'createdAt': now, 'body': event_body}]
        session['updatedAt'] = now
        project['updatedAt'] = now
        payload['activeSessionId'] = sid
        _write_workbench_store(payload)
        return {'ok': True, 'project': project, 'session': session, 'plan': plan, 'planRevision': session.get('planRevision'), 'planDefinitionRevision': session.get('planDefinitionRevision'), **payload}

configure_workbench_store = _configure_workbench_store
read_workbench_store = _read_workbench_store
find_workbench_project = _workbench_find_project


__all__ = ['configure_workbench_store', 'find_workbench_project', 'find_workbench_project_lightweight', 'read_workbench_store', 'resolve_project_workspace_dir', 'resolve_project_workspace_dir_async', 'update_task_plan_for_session']
