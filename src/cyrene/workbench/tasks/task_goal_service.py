"""Task-goal mutation application service."""

from __future__ import annotations

from typing import Any

from cyrene.localization import localized
from cyrene.workbench.artifacts import presentation_runtime
from cyrene.workbench.planning import planning_runtime
from cyrene.workbench.projects import project_repository, project_runtime

async def set_task_goal_for_session(session_id: str, goal: str, title: str='', summary: str='') -> dict[str, Any]:
    """Set/correct a Workbench task session's goal, short title, and/or one-line
    summary (简介).

    Backs the ``set_task_goal`` agent tool: the agent may call it once it actually
    understands what the task is (e.g. after exploring the project, or when the
    user's opener was a question rather than a goal). At least one of goal/title/
    summary must be provided. The title is LOCKED once the user has manually edited
    it (``titleLocked``) — the agent can no longer change the title, though goal and
    summary still update. Returns a small status dict.
    """
    sid = str(session_id or '').strip()
    new_goal = str(goal or '').strip()
    new_title = str(title or '').strip()
    new_summary = str(summary or '').strip()
    if not sid:
        return {'ok': False, 'error': localized('No active task session.', '没有活动的任务会话。'), 'code': 'no_session'}
    if not new_goal and (not new_title) and (not new_summary):
        return {'ok': False, 'error': localized('Nothing to update. Provide a goal, title, or summary.', '没有可更新的内容，请提供目标、标题或摘要。'), 'code': 'nothing_to_update'}
    if new_goal and len(new_goal) < 3:
        return {'ok': False, 'error': localized('The goal is too short.', '目标过短。'), 'code': 'goal_too_short'}
    payload = project_repository._read_workbench_store()
    project, session = project_repository._workbench_find_session(payload, sid)
    if not session or not project:
        return {'ok': False, 'error': localized('Session not found.', '未找到会话。'), 'code': 'session_not_found'}
    if str(session.get('kind') or '') == 'init':
        return {'ok': False, 'error': localized('A goal cannot be set on a project setup session.', '不能为项目初始化会话设置目标。'), 'code': 'init_session'}
    extracted_constraints = await planning_runtime._workbench_extract_constraints(new_goal) if new_goal else []
    payload = project_repository._read_workbench_store()
    project, session = project_repository._workbench_find_session(payload, sid)
    if not session or not project:
        return {'ok': False, 'error': localized('Session not found.', '未找到会话。'), 'code': 'session_not_found'}
    now = project_runtime._utc_now_iso()
    if new_goal:
        session['goal'] = new_goal
        merged = list(session.get('constraints') or [])
        for item in extracted_constraints:
            if item not in merged:
                merged.append(item)
        session['constraints'] = merged
    title_locked = bool(session.get('titleLocked'))
    title_blocked = False
    if new_title:
        if title_locked:
            title_blocked = True
        else:
            session['title'] = new_title[:80]
    elif new_goal and (not title_locked) and project_runtime._workbench_is_default_title(session.get('title')):
        derived = project_runtime._workbench_derive_title(new_goal)
        if derived:
            session['title'] = derived[:80]
    if new_summary:
        session['summary'] = new_summary
    session['updatedAt'] = now
    project['updatedAt'] = now
    project_repository._write_workbench_store(payload)
    return {'ok': True, 'goal': session.get('goal') or '', 'title': session.get('title') or '', 'summary': presentation_runtime._workbench_session_summary_text(session), 'titleLocked': title_locked, 'titleBlocked': title_blocked}

__all__ = ["set_task_goal_for_session"]
