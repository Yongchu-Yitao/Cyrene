"""Planning domain operations extracted from the Workbench compatibility runtime."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent.plugin import active_plugin_service
from cyrene.localization import app_language, localized
from cyrene.workbench import project_runtime

logger = logging.getLogger(__name__)


def _l(en: str, zh: str, **values: Any) -> str:
    return localized(en, zh, **values)


def _workbench_render_reflection_block(session: dict[str, Any]) -> str:
    reflection = session.get("reflection") if isinstance(session.get("reflection"), dict) else None
    packet = reflection.get("packet") if isinstance(reflection, dict) else None
    if not isinstance(packet, dict) or not packet:
        return ""
    labels = (
        (_l("Goal", "目标"), packet.get("objective") or packet.get("goal")),
        (_l("Attempt summary", "尝试总结"), packet.get("attempt_summary")),
        (_l("Next step", "下一步"), packet.get("next_step")),
    )
    separator = "：" if app_language() == "zh" else ": "
    lines = [f"{label}{separator}{str(value).strip()}" for label, value in labels if str(value or "").strip()]
    for label, key in (
        (_l("Root cause", "根因"), "root_causes"),
        (_l("Avoid", "应避免"), "excluded_paths"),
        (_l("Promising direction", "可行方向"), "promising_directions"),
    ):
        values = packet.get(key)
        if isinstance(values, list):
            lines.extend(f"{label}{separator}{str(value).strip()}" for value in values if str(value).strip())
    return "\n".join(lines)


def _workbench_render_past_task_reports(project: dict[str, Any] | None) -> str:
    if not project:
        return ""
    try:
        memory_service = active_plugin_service("memory")
        if memory_service is None:
            return ""
        return memory_service.render_past_task_reports(
            project,
            limit=3,
            max_chars=2500,
        )
    except Exception:
        logger.exception("Failed to render past task reports for planning")
        return ""


def _workbench_store_reflection(
    session: dict[str, Any],
    packet: dict[str, Any],
    *,
    trigger: str = "manual",
    source_session_id: str = "",
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a Plugin-generated reflection packet to Task state."""

    created_at = project_runtime._utc_now_iso()
    entry = {
        "packet": dict(packet),
        "createdAt": created_at,
        "trigger": str(trigger or "manual"),
        "sourceSessionId": str(source_session_id or session.get("id") or ""),
    }
    session["reflection"] = entry
    next_step = str(packet.get("next_step") or "").strip()
    session["events"] = list(session.get("events") or []) + [{
        "id": project_runtime._short_id("event"),
        "type": "DeepReflection",
        "createdAt": created_at,
        "body": _l(
            "Deep reflection completed. {detail}",
            "已完成深度反思。{detail}",
            detail=(
                _l(
                    "Suggested next step: {step}",
                    "建议下一步：{step}",
                    step=next_step,
                )
                if next_step
                else _l(
                    "Generated a recommendation to reset direction.",
                    "已生成方向重整建议。",
                )
            ),
        ),
    }]
    memory_service = active_plugin_service("memory")
    if memory_service is not None and project is not None:
        try:
            memory_service.store_reflection_insights(project, packet)
        except Exception:
            logger.exception("Failed to store reflection insights")
    return entry


_WORKBENCH_OPEN_STATUSES = {
    "idle", "pending", "planning", "paused", "review", "failed"
}


def _workbench_reflection_candidates(
    project: dict[str, Any] | None,
    source_session: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(project, dict):
        return []
    source_id = str(source_session.get("id") or "")
    return [
        session for session in project.get("sessions") or []
        if isinstance(session, dict)
        and str(session.get("id") or "") != source_id
        and str(session.get("status") or "idle") in _WORKBENCH_OPEN_STATUSES
    ]


def _workbench_apply_reflection_hints(
    project: dict[str, Any] | None,
    source_session: dict[str, Any],
    packet: dict[str, Any],
    matches: dict[str, str],
) -> None:
    """Apply model-selected cross-Task hints without performing model work."""

    if not isinstance(project, dict) or not packet or not matches:
        return
    source_id = str(source_session.get("id") or "")
    source_title = str(source_session.get("title") or _l("Task", "任务"))
    now = project_runtime._utc_now_iso()
    for session in _workbench_reflection_candidates(project, source_session):
        hint_text = str(matches.get(str(session.get("id") or "")) or "").strip()
        if not hint_text:
            continue
        hints = session.setdefault("pendingHints", [])
        if not isinstance(hints, list):
            hints = []
            session["pendingHints"] = hints
        if any(
            isinstance(item, dict)
            and str(item.get("fromSessionId") or "") == source_id
            and str(item.get("status") or "") == "pending"
            for item in hints
        ):
            continue
        hints.append({
            "id": project_runtime._short_id("hint"),
            "fromSessionId": source_id,
            "fromTitle": source_title,
            "hint": hint_text[:200],
            "packet": dict(packet),
            "status": "pending",
            "createdAt": now,
        })
        session["events"] = list(session.get("events") or []) + [{
            "id": project_runtime._short_id("event"),
            "type": "ReflectionHint",
            "createdAt": now,
            "body": _l(
                'Related task "{title}" produced this reflection: {hint}',
                '相关任务《{title}》反思发现：{hint}',
                title=source_title,
                hint=hint_text[:200],
            ),
        }]
        session["updatedAt"] = now


def _workbench_merge_hint_mutations(
    original_project: dict[str, Any],
    latest_project: dict[str, Any],
) -> None:
    """Merge append-only reflection hints after concurrent plan generation."""

    latest = {
        str(session.get("id") or ""): session
        for session in latest_project.get("sessions") or []
        if isinstance(session, dict) and str(session.get("id") or "")
    }
    for source in original_project.get("sessions") or []:
        if not isinstance(source, dict):
            continue
        target = latest.get(str(source.get("id") or ""))
        if target is None:
            continue
        for field in ("pendingHints", "events"):
            values = source.get(field)
            if not isinstance(values, list):
                continue
            destination = target.setdefault(field, [])
            if not isinstance(destination, list):
                destination = []
                target[field] = destination
            known = {
                str(item.get("id") or "") for item in destination
                if isinstance(item, dict)
            }
            destination.extend(
                item for item in values
                if isinstance(item, dict) and str(item.get("id") or "") not in known
            )
        target["updatedAt"] = source.get("updatedAt") or target.get("updatedAt")

async def _workbench_extract_constraints(
    text: str,
    *,
    agent_runtime: Any = None,
    session: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
) -> list[str]:
    """New-kernel constraint seam retained for pure-domain callers."""

    if agent_runtime is None or session is None or project is None:
        return []
    return await agent_runtime.extract_constraints(text, session, project)

def _workbench_new_plan_step(title: str, description: str, order: int, task_id: str='') -> dict[str, Any]:
    """A single execution-plan step — always starts pending (no pre-completion)."""
    return {'id': project_runtime._short_id('step'), 'taskId': task_id, 'title': str(title or '').strip(), 'description': str(description or '').strip(), 'status': 'pending', 'order': order, 'dependsOn': [], 'currentAction': '', 'relatedFiles': [], 'progressEvents': [], 'toolCalls': [], 'artifacts': [], 'error': None}

def _workbench_plan_from_input(user_input: str, session: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic FALLBACK plan, used only when LLM plan generation is
    unavailable. Every step starts ``pending`` — nothing is pre-marked done."""
    existing = session.get('plan') if isinstance(session.get('plan'), list) else []
    if existing:
        return existing
    base_steps = [
        _l('Understand the goal and constraints', '理解目标与约束'),
        _l('Gather relevant information and context', '收集相关信息和上下文'),
        _l('Analyze the existing material', '分析现有内容'),
        _l('Create an execution approach', '制定执行方案'),
        _l('Carry out the work', '推进执行'),
        _l('Verify the result and summarize', '验证结果并总结'),
    ]
    task_id = session.get('id', '')
    description = _l(
        'Generated by the fallback planner; edit as needed.',
        '由兜底计划生成，请按需编辑。',
    )
    return [_workbench_new_plan_step(title, description, index + 1, task_id) for index, title in enumerate(base_steps)]

def _workbench_dependency_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        step_id = str(item or '').strip()
        if step_id and step_id not in result:
            result.append(step_id)
    return result

def _workbench_plan_has_started(plan: Any) -> bool:
    if not isinstance(plan, list):
        return False
    for step in plan:
        if not isinstance(step, dict):
            continue
        if str(step.get('status') or 'pending') != 'pending':
            return True
        if step.get('startedAt') or step.get('completedAt') or step.get('durationSec') is not None:
            return True
        if step.get('progressEvents') or step.get('toolCalls'):
            return True
    return False

def _workbench_validate_plan_graph(plan: Any, *, require_dependency_order: bool=True) -> tuple[bool, str, str]:
    if not isinstance(plan, list):
        return (False, _l('Invalid plan format.', '计划格式无效。'), 'invalid_plan')
    step_ids: list[str] = []
    titles: dict[str, str] = {}
    for index, step in enumerate(plan):
        if not isinstance(step, dict):
            return (False, _l(
                'Step {index} has an invalid format.',
                '第 {index} 个步骤格式无效。',
                index=index + 1,
            ), 'invalid_step')
        step_id = str(step.get('id') or '').strip()
        title = str(step.get('title') or '').strip()
        if not step_id:
            return (False, _l(
                'Step {index} is missing an id.',
                '第 {index} 个步骤缺少 id。',
                index=index + 1,
            ), 'missing_step_id')
        if step_id in titles:
            return (False, _l(
                'The plan contains duplicate step ids.',
                '计划中存在重复的步骤 id。',
            ), 'duplicate_step_id')
        if not title:
            return (False, _l(
                'Step {index} must have a title.',
                '第 {index} 个步骤标题不能为空。',
                index=index + 1,
            ), 'empty_step_title')
        step_ids.append(step_id)
        titles[step_id] = title
    known = set(step_ids)
    positions = {step_id: index for index, step_id in enumerate(step_ids)}
    indegree = {step_id: 0 for step_id in step_ids}
    followers: dict[str, list[str]] = {step_id: [] for step_id in step_ids}
    for step in plan:
        step_id = str(step.get('id') or '').strip()
        for dependency_id in _workbench_dependency_ids(step.get('dependsOn')):
            if dependency_id == step_id:
                return (False, _l(
                    'Step "{step}" cannot depend on itself.',
                    '步骤「{step}」不能依赖自身。',
                    step=titles[step_id],
                ), 'self_dependency')
            if dependency_id not in known:
                return (False, _l(
                    'Step "{step}" references a prerequisite that does not exist.',
                    '步骤「{step}」引用了不存在的前置步骤。',
                    step=titles[step_id],
                ), 'missing_dependency')
            if require_dependency_order and positions[dependency_id] >= positions[step_id]:
                return (False, _l(
                    'Step "{step}" must come after prerequisite "{dependency}".',
                    '步骤「{step}」必须排在前置步骤「{dependency}」之后。',
                    step=titles[step_id],
                    dependency=titles[dependency_id],
                ), 'dependency_order')
            indegree[step_id] += 1
            followers[dependency_id].append(step_id)
    queue = [step_id for step_id in step_ids if indegree[step_id] == 0]
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for follower in followers[current]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                queue.append(follower)
    if visited != len(step_ids):
        return (False, _l(
            'Step dependencies form a cycle. Remove the circular dependency.',
            '步骤依赖形成了循环，请移除循环依赖。',
        ), 'dependency_cycle')
    return (True, '', '')

def _workbench_normalize_plan(plan: Any, *, task_id: str='') -> list[dict[str, Any]]:
    if not isinstance(plan, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, raw_step in enumerate(plan):
        if not isinstance(raw_step, dict):
            continue
        step = dict(raw_step)
        step['id'] = str(step.get('id') or '').strip() or project_runtime._short_id('step')
        step['taskId'] = str(step.get('taskId') or task_id or '').strip()
        step['title'] = str(step.get('title') or '').strip()[:160]
        step['description'] = str(step.get('description') or '').strip()[:4000]
        step['order'] = index + 1
        step['dependsOn'] = _workbench_dependency_ids(step.get('dependsOn'))
        step.pop('_dependsOnProvided', None)
        normalized.append(step)
    return normalized[:12]

def _workbench_keep_ordered_dependencies(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only dependency edges that still point to an earlier retained step."""
    seen: set[str] = set()
    for step in plan:
        step_id = str(step.get('id') or '').strip()
        step['dependsOn'] = [dependency_id for dependency_id in _workbench_dependency_ids(step.get('dependsOn')) if dependency_id in seen]
        if step_id:
            seen.add(step_id)
    return plan

def _workbench_step_dependencies_satisfied(plan: Any, step_id: str) -> tuple[bool, list[str]]:
    if not isinstance(plan, list):
        return (False, [])
    by_id = {str(step.get('id') or ''): step for step in plan if isinstance(step, dict) and str(step.get('id') or '')}
    step = by_id.get(str(step_id or ''))
    if not step:
        return (False, [])
    unmet: list[str] = []
    for dependency_id in _workbench_dependency_ids(step.get('dependsOn')):
        dependency = by_id.get(dependency_id)
        if not dependency or str(dependency.get('status') or '') not in ('completed', 'done'):
            unmet.append(dependency_id)
    return (not unmet, unmet)

def _workbench_plan_definition_signature(plan: Any) -> str:
    rows: list[dict[str, Any]] = []
    for step in plan if isinstance(plan, list) else []:
        if not isinstance(step, dict):
            continue
        context_files = step.get('contextFiles') if isinstance(step.get('contextFiles'), list) else []
        rows.append({'id': str(step.get('id') or ''), 'title': str(step.get('title') or ''), 'description': str(step.get('description') or ''), 'dependsOn': _workbench_dependency_ids(step.get('dependsOn')), 'promptOverride': str(step.get('promptOverride') or ''), 'contextFiles': context_files})
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def _workbench_coerce_plan_steps(raw: Any, session: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize an LLM plan reply (``{"steps": [...]}`` or a bare list) into
    execution-plan steps. All steps start ``pending``."""
    items: list[Any] = []
    if isinstance(raw, dict) and isinstance(raw.get('steps'), list):
        items = raw['steps']
    elif isinstance(raw, list):
        items = raw
    task_id = session.get('id', '')
    steps: list[dict[str, Any]] = []
    dependency_indexes: list[list[int]] = []
    for item in items:
        if isinstance(item, dict):
            title = str(item.get('title') or item.get('name') or '').strip()
            description = str(item.get('description') or item.get('detail') or '').strip()
            source_step_id = str(item.get('sourceStepId') or item.get('source_step_id') or '').strip()
            raw_dependencies = item.get('dependsOnStepIndexes')
            if not isinstance(raw_dependencies, list):
                raw_dependencies = item.get('depends_on_step_indexes')
            dependencies_provided = isinstance(raw_dependencies, list)
            dependency_indices: list[int] = []
            if dependencies_provided:
                for value in raw_dependencies:
                    try:
                        dependency_index = int(value)
                    except (TypeError, ValueError):
                        continue
                    if dependency_index > 0 and dependency_index not in dependency_indices:
                        dependency_indices.append(dependency_index)
        else:
            title = str(item or '').strip()
            description = ''
            source_step_id = ''
            dependencies_provided = False
            dependency_indices = []
        if not title:
            continue
        step = _workbench_new_plan_step(title, description, len(steps) + 1, task_id)
        if source_step_id:
            step['sourceStepId'] = source_step_id
        step['_dependsOnProvided'] = dependencies_provided
        steps.append(step)
        dependency_indexes.append(dependency_indices)
        if len(steps) >= 12:
            break
    for index, step in enumerate(steps):
        step['dependsOn'] = [steps[dependency_index - 1]['id'] for dependency_index in dependency_indexes[index] if 0 < dependency_index <= len(steps) and dependency_index - 1 < index]
    return steps

def _workbench_plan_title_key(value: Any) -> str:
    return re.sub('\\s+', '', str(value or '').strip().lower())

def _workbench_existing_plan_block(session: dict[str, Any]) -> str:
    plan = session.get('plan') if isinstance(session.get('plan'), list) else []
    titles_by_id = {str(step.get('id') or ''): str(step.get('title') or '').strip() for step in plan if isinstance(step, dict) and str(step.get('id') or '')}
    rows: list[str] = []
    for index, step in enumerate(plan[:12], 1):
        if not isinstance(step, dict):
            continue
        title = str(step.get('title') or '').strip()
        if not title:
            continue
        status = str(step.get('status') or 'pending').strip()
        description = str(step.get('description') or '').strip()
        suffix = f' — {description}' if description else ''
        step_id = str(step.get('id') or '').strip()
        dependency_titles = [titles_by_id.get(dependency_id, dependency_id) for dependency_id in _workbench_dependency_ids(step.get('dependsOn'))]
        dependency_suffix = (
            _l(
                '; prerequisites: {dependencies}',
                '；前置步骤：{dependencies}',
                dependencies=(
                    '、' if app_language() == 'zh' else ', '
                ).join(dependency_titles),
            )
            if dependency_titles else ''
        )
        rows.append(f'{index}. id={step_id} [{status}] {title}{suffix}{dependency_suffix}')
    if not rows:
        return ''
    return _l(
        '\nCurrent execution plan (preserve it and build on it unless the user explicitly asks to remove or reorder it):\n{rows}',
        '\n当前已有执行计划（除非用户明确要求删除/重排，请保留并在此基础上调整）：\n{rows}',
        rows='\n'.join(rows),
    )

def _workbench_session_summary_text(session: dict[str, Any]) -> str:
    """Extract the task's one-line summary (简介), tolerating the dict form the
    store sometimes holds (mirrors the frontend's sessionSummaryText)."""
    raw = session.get('summary')
    if isinstance(raw, dict):
        return str(raw.get('text') or raw.get('body') or raw.get('content') or raw.get('summary') or '').strip()
    return str(raw or '').strip()

def _workbench_follow_up_seed(session: dict[str, Any], *, requested_title: str='', requested_goal: str='') -> dict[str, Any]:
    """Build a deterministic follow-up task from the source task's live state."""
    source_title = str(session.get('title') or _l('Task', '任务')).strip() or _l('Task', '任务')
    explicit_goal = str(requested_goal or '').strip()
    title = str(requested_title or '').strip()
    if not title:
        title = _l('{title} · Follow-up', '{title} · 后续', title=source_title)
    status_labels = {
        'idle': _l('not started', '未开始'),
        'answered': _l('answered', '已回答'),
        'acted': _l('executed', '已执行'),
        'planning': _l('planning', '规划中'),
        'waiting_for_approval': _l('waiting for confirmation', '等待确认'),
        'waiting_for_user': _l('waiting for the user', '等待用户'),
        'running': _l('running', '执行中'),
        'review': _l('ready for review', '待验收'),
        'done': _l('completed', '已完成'),
        'completed': _l('completed', '已完成'),
        'failed': _l('failed', '失败'),
        'blocked': _l('blocked', '阻塞'),
        'paused': _l('paused', '已暂停'),
        'cancelled': _l('cancelled', '已取消'),
    }
    source_status = str(session.get('status') or 'idle').strip()
    source_goal = str(session.get('goal') or '').strip()
    source_summary = _workbench_session_summary_text(session)
    source_result = str(session.get('agentReply') or '').strip()
    unresolved_steps: list[str] = []
    for step in session.get('plan') or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get('status') or 'pending').strip()
        if status in ('completed', 'done', 'skipped'):
            continue
        step_title = str(step.get('title') or '').strip()
        if step_title:
            unresolved_steps.append(step_title)
        if len(unresolved_steps) >= 6:
            break
    unresolved_acceptance: list[str] = []
    for item in session.get('acceptanceCriteria') or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get('status') or 'pending').strip()
        if status in ('passed', 'done', 'completed'):
            continue
        text = str(item.get('text') or '').strip()
        if text:
            unresolved_acceptance.append(text)
        if len(unresolved_acceptance) >= 6:
            break
    reflection = session.get('reflection')
    packet = reflection.get('packet') if isinstance(reflection, dict) else None
    next_step = str(packet.get('next_step') or '').strip() if isinstance(packet, dict) else ''
    lines = [_l(
        'This is a follow-up to task "{title}".',
        '这是任务「{title}」的后续任务。',
        title=source_title,
    )]
    if explicit_goal:
        lines.append(_l(
            'Follow-up request: {goal}', '本次后续要求：{goal}', goal=explicit_goal
        ))
    if source_goal:
        lines.append(_l(
            'Source task goal: {goal}', '来源任务目标：{goal}', goal=source_goal
        ))
    lines.append(_l(
        'Current source-task status: {status}',
        '来源任务当前状态：{status}',
        status=status_labels.get(source_status, source_status or _l('unknown', '未知')),
    ))
    if source_summary:
        lines.append(_l(
            'Source task summary: {summary}',
            '来源任务摘要：{summary}',
            summary=source_summary,
        ))
    elif source_result:
        lines.append(_l(
            'Current source-task result: {result}',
            '来源任务当前结果：{result}',
            result=source_result[:1200],
        ))
    if unresolved_steps:
        lines.append(_l(
            'Unresolved steps: {steps}',
            '尚未解决的步骤：{steps}',
            steps=('；' if app_language() == 'zh' else '; ').join(unresolved_steps),
        ))
    if unresolved_acceptance:
        lines.append(_l(
            'Unmet acceptance criteria: {criteria}',
            '尚未满足的验收项：{criteria}',
            criteria=('；' if app_language() == 'zh' else '; ').join(unresolved_acceptance),
        ))
    if next_step:
        lines.append(_l(
            'Next step suggested by reflection: {step}',
            '反思建议的下一步：{step}',
            step=next_step,
        ))
    return {'title': title[:80], 'goal': '\n'.join(lines), 'constraints': [str(value).strip() for value in session.get('constraints') or [] if str(value).strip()], 'priority': str(session.get('priority') or 'medium').strip() if str(session.get('priority') or '').strip() in ('high', 'medium', 'low') else 'medium', 'unresolvedAcceptance': unresolved_acceptance, 'context': {'sourceTitle': source_title, 'sourceStatus': source_status, 'sourceSummary': source_summary, 'unresolvedSteps': unresolved_steps, 'unresolvedAcceptance': unresolved_acceptance, 'reflectionNextStep': next_step}}

def _workbench_render_task_brief_block(session: dict[str, Any]) -> str:
    """Render the task's identity (title / goal / summary / acceptance) + current
    plan as a prompt block for the agent run.

    These live ONLY in the Workbench store, not in the agent's conversation
    history — without this the agent literally cannot see the plan or goal the UI
    shows, and ends up asking "我没看到执行计划". Injected via ``ephemeral_system``
    (prompt tail), so it stays cache-safe.
    """
    title = str(session.get('title') or '').strip()
    goal = str(session.get('goal') or '').strip()
    summary = _workbench_session_summary_text(session)
    lines: list[str] = [_l('## Current task', '## 当前任务')]
    if title:
        lines.append(_l('- Title: {title}', '- 标题：{title}', title=title))
    if goal:
        lines.append(_l('- Goal: {goal}', '- 目标：{goal}', goal=goal))
    if summary:
        lines.append(_l('- Summary: {summary}', '- 简介：{summary}', summary=summary))
    acceptance = session.get('acceptanceCriteria')
    if isinstance(acceptance, list):
        accept_texts = [str((a.get('text') if isinstance(a, dict) else a) or '').strip() for a in acceptance]
        accept_texts = [t for t in accept_texts if t][:8]
        if accept_texts:
            lines.append(_l(
                '- Acceptance criteria: {criteria}',
                '- 验收标准：{criteria}',
                criteria=('；' if app_language() == 'zh' else '; ').join(accept_texts),
            ))
    body = '\n'.join(lines)
    plan_block = _workbench_existing_plan_block(session)
    if plan_block:
        body += '\n' + plan_block.lstrip('\n')
    if session.get('titleLocked'):
        body += _l(
            '\n(The user manually set the task title, so you cannot change it. If the title or summary no longer matches the work, use set_task_goal to update the summary or goal.)',
            '\n（用户已手动设置任务标题，你不能修改标题；如标题/简介与实际工作不符，可用 set_task_goal 更新简介或目标。）',
        )
    else:
        body += _l(
            '\n(The title and summary appear on the task card. If they do not match the work, update them with set_task_goal.)',
            '\n（标题与简介都会显示在任务卡上；若与你实际要做的事不符，可用 set_task_goal 更新。）',
        )
    return body

def _workbench_reconcile_revised_plan(existing: list[dict[str, Any]], generated: list[dict[str, Any]], feedback: str, operation: str='auto') -> list[dict[str, Any]]:
    mode = str(operation or 'auto').strip().lower()
    if mode not in ('revise', 'replace'):
        mode = 'revise'
    if not existing or not feedback or mode == 'replace':
        return _workbench_normalize_plan(generated)
    if not generated:
        return _workbench_normalize_plan(existing)
    existing_steps = [dict(step) for step in existing if isinstance(step, dict)]
    by_id = {str(step.get('id') or ''): step for step in existing_steps if str(step.get('id') or '')}
    by_title = {_workbench_plan_title_key(step.get('title')): step for step in existing_steps if _workbench_plan_title_key(step.get('title'))}
    merged_generated: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    generated_to_final_id: dict[str, str] = {}
    for index, step in enumerate(generated):
        if not isinstance(step, dict):
            continue
        generated_id = str(step.get('id') or '').strip()
        source_id = str(step.get('sourceStepId') or '').strip()
        original = by_id.get(source_id)
        if original is None:
            original = by_title.get(_workbench_plan_title_key(step.get('title')))
        if original is not None:
            next_step = dict(original)
            next_step['title'] = str(step.get('title') or original.get('title') or '').strip()
            next_step['description'] = str(step.get('description') or '').strip()
            next_step['order'] = index + 1
            if step.get('_dependsOnProvided'):
                next_step['dependsOn'] = _workbench_dependency_ids(step.get('dependsOn'))
            next_step.pop('sourceStepId', None)
            next_step.pop('_dependsOnProvided', None)
            matched_ids.add(str(original.get('id') or ''))
        else:
            next_step = dict(step)
            next_step['order'] = index + 1
            next_step.pop('sourceStepId', None)
            next_step.pop('_dependsOnProvided', None)
        if generated_id:
            generated_to_final_id[generated_id] = str(next_step.get('id') or '')
        merged_generated.append(next_step)
    for step in merged_generated:
        step['dependsOn'] = [generated_to_final_id.get(dependency_id, dependency_id) for dependency_id in _workbench_dependency_ids(step.get('dependsOn')) if generated_to_final_id.get(dependency_id, dependency_id)]
    if not matched_ids:
        merged = existing_steps
        seen = {_workbench_plan_title_key(step.get('title')) for step in merged}
        for step in merged_generated:
            key = _workbench_plan_title_key(step.get('title'))
            if key and key not in seen:
                merged.append(step)
                seen.add(key)
        for index, step in enumerate(merged):
            step['order'] = index + 1
        return _workbench_keep_ordered_dependencies(_workbench_normalize_plan(merged[:12]))
    for original in existing_steps:
        original_id = str(original.get('id') or '')
        if original_id and original_id not in matched_ids:
            merged_generated.append(original)
    for index, step in enumerate(merged_generated):
        step['order'] = index + 1
    final_plan = _workbench_normalize_plan(merged_generated[:12])
    return _workbench_keep_ordered_dependencies(final_plan)

async def _workbench_generate_plan_steps(
    session: dict[str, Any],
    project: dict[str, Any],
    feedback: str = '',
    auto_start: bool = False,
    requested_operation: str = 'auto',
    *,
    agent_runtime: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, str]:
    """Generate a REAL execution plan for a task session from its goal +
    constraints, exploring the project workspace. Returns
    ``(steps, acceptance_criteria, from_llm, operation)``; ``from_llm`` is False
    when generation failed and deterministic fallbacks were used.

    ``auto_start`` (「直接开始」): no goal is given up front — the agent explores
    the project and the LLM proposes a concise goal + title (back-filled onto the
    session) alongside the steps, so the task gets a real, project-relevant
    identity instead of filler."""
    goal = str(session.get('goal') or session.get('title') or '').strip()
    existing_plan = session.get('plan') if isinstance(session.get('plan'), list) else []
    feedback = str(feedback or '').strip()
    requested_operation = str(requested_operation or 'auto').strip().lower()
    if requested_operation not in ('auto', 'create', 'revise', 'replace'):
        requested_operation = 'auto'
    if auto_start and project_runtime._workbench_is_blank_goal(goal):
        goal = _l(
            'Read the project workspace and description, determine the most important work to advance now, and plan it.',
            '通读本项目的工作区文件与项目说明，判断当前最应该推进的工作并据此规划',
        )
    fallback = existing_plan if feedback and existing_plan else _workbench_plan_from_input(goal, {'id': session.get('id', '')})
    existing_acceptance = session.get('acceptanceCriteria') if isinstance(session.get('acceptanceCriteria'), list) else []
    fallback_acceptance = [dict(item) for item in existing_acceptance if isinstance(item, dict)] if feedback and existing_plan and existing_acceptance else _workbench_fallback_acceptance(session, fallback)
    if not goal or agent_runtime is None:
        return (fallback, fallback_acceptance, False, 'create')
    constraints = [str(c).strip() for c in session.get('constraints') or [] if str(c).strip()]
    planning_thread = session.setdefault('planningThread', {})
    if not isinstance(planning_thread, dict):
        planning_thread = {}
        session['planningThread'] = planning_thread
    previous_workspace_revision = str(planning_thread.get('workspaceRevision') or '')
    previous_workspace_snapshot = planning_thread.get('workspaceSnapshot') if isinstance(planning_thread.get('workspaceSnapshot'), dict) else {}
    workspace_revision = ''
    workspace_snapshot: dict[str, str] = {}
    routing = {'revisionMode': 'revise'}
    constraints_block = _l(
        '\nConstraints:\n{items}',
        '\n约束：\n{items}',
        items='\n'.join((f'- {c}' for c in constraints)),
    ) if constraints else ''
    feedback_block = _l(
        '\nUser feedback on the plan (adjust accordingly): {feedback}',
        '\n用户对计划的修改反馈（请据此调整）：{feedback}',
        feedback=feedback,
    ) if feedback else ''
    workspace_delta_block = ''
    if previous_workspace_revision and previous_workspace_revision != workspace_revision:
        changed_files = sorted((path for path in set(previous_workspace_snapshot) | set(workspace_snapshot) if previous_workspace_snapshot.get(path) != workspace_snapshot.get(path)))
        workspace_delta_block = _l(
            '\nWorkspace delta: {delta}',
            '\n工作区增量：{delta}',
            delta=json.dumps({'type': 'workspace_delta', 'baseRevision': previous_workspace_revision, 'revision': workspace_revision, 'changedFiles': changed_files[:200], 'invalidatedObservations': ['directory/glob observations']}, ensure_ascii=False, sort_keys=True),
        )
    existing_plan_block = _workbench_existing_plan_block(session) if feedback and requested_operation != 'replace' else ''
    reflection_text = _workbench_render_reflection_block(session)
    reflection_block = _l(
        '\n\n## Deep-reflection findings (the plan must incorporate them)\nThe following reviews earlier attempts. Avoid excluded_paths, prioritize promising_directions, and use next_step as guidance:\n{reflection}',
        '\n\n## 深度反思结论（必须据此调整计划）\n下面是对既往尝试的复盘。请避开其中的 excluded_paths（已被证明是死路的做法），优先采用 promising_directions（更有希望的方向），并参考 next_step：\n{reflection}',
        reflection=reflection_text,
    ) if reflection_text else ''
    explore_directive = _l(
        'If genuinely necessary, explore the workspace through the Plugin toolbox, stopping once there is enough evidence.',
        '如确有必要，可通过 Plugin toolbox 探索工作区；够用即止。',
    )
    past_reports_block = _workbench_render_past_task_reports(project)
    reports_section = f'\n\n{past_reports_block}' if past_reports_block else ''
    if auto_start:
        lead_in = _l(
            'This task uses Start directly: the user did not provide an explicit goal. First inspect the project through the Plugin toolbox, identify the single most important work item to advance, then provide the goal, title, and execution steps.',
            '这是「直接开始」的任务——用户没有明确给出目标。请先通过 Plugin toolbox 通读项目，判断当前最应该推进的一件工作，再据此给出 goal、title 和执行步骤。',
        )
        prompt = _l(
            '{lead_in}\n\nPlanning direction: {goal}{constraints}{workspace_delta}{reflection}{reports}\n\nThe goal must be concrete and grounded in this project, not generic, and should reference real files, directories, or modules where possible. Acceptance criteria must be independently verifiable and avoid process-only statements such as “the goal is clear.” Follow the JSON structure from the system prompt and return exactly one JSON object without a Markdown code fence. Write all user-visible goal, title, steps, descriptions, and acceptance criteria in English.',
            '{lead_in}\n\n规划方向：{goal}{constraints}{workspace_delta}{reflection}{reports}\n\ngoal 要具体、贴合本项目实际、不要泛泛而谈，并尽量引用真实文件/目录/模块；验收标准要可独立核验，避免“目标清晰”这类过程性描述。按系统提示约定的 JSON 结构，只返回一个 JSON 对象，不要 Markdown 代码块标记。所有用户可见的 goal、title、步骤、说明和验收标准均使用简体中文。',
            lead_in=lead_in,
            goal=goal,
            constraints=constraints_block,
            workspace_delta=workspace_delta_block,
            reflection=reflection_block,
            reports=reports_section,
        )
    else:
        prompt = _l(
            'Break the task below into clear, ordered, executable steps.\n{explore}\n\nTask goal: {goal}{constraints}{existing_plan}{feedback}{workspace_delta}{reflection}{reports}\n\nWhen the task involves the current project, reference real files, directories, or modules where possible. When it is unrelated, plan around the task itself without introducing irrelevant file or code work. Choose revisionMode: use revise for additions, deletions, reordering, or local approach changes; use replace when the request is entirely different, new, asks for another approach or a restart, or the new goal clearly conflicts with the old plan. Follow the JSON structure from the system prompt and return exactly one JSON object without a Markdown code fence. Write all user-visible goal, title, steps, descriptions, and acceptance criteria in English.',
            '请把下面这个任务拆解成清晰、有顺序、可逐步执行的步骤。\n{explore}\n\n任务目标：{goal}{constraints}{existing_plan}{feedback}{workspace_delta}{reflection}{reports}\n\n任务涉及当前项目时，尽量引用真实文件、目录或模块；与当前项目无关时，围绕任务本身规划，不要引入无关的文件或代码操作。revisionMode 自行判断：仅补充、删改、调序或改变局部做法时用 revise；要求完全不同、全新、换一套、从头重做，或新目标与原计划明显不符时用 replace。按系统提示约定的 JSON 结构，只返回一个 JSON 对象，不要 Markdown 代码块标记。所有用户可见的 goal、title、步骤、说明和验收标准均使用简体中文。',
            explore=explore_directive,
            goal=goal,
            constraints=constraints_block,
            existing_plan=existing_plan_block,
            feedback=feedback_block,
            workspace_delta=workspace_delta_block,
            reflection=reflection_block,
            reports=reports_section,
        )
        if requested_operation == 'replace':
            prompt += _l(
                '\nThe user explicitly chose Regenerate. Decompose the final goal independently from scratch; at least half the steps must use a different decomposition or execution path, not merely reword the old plan.',
                '\n这是用户主动点击的「重新生成」：必须从最终任务目标重新独立拆解，至少一半步骤应采用不同的拆解方式或执行路径，不能只是改写措辞。',
            )
    try:
        parsed = await agent_runtime._independent_json_agent(
            project=project,
            session=session,
            purpose='task_planning',
            prompt=prompt,
        )
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        fallback_operation = 'replace' if requested_operation == 'replace' else 'revise' if feedback else 'create'
        return (fallback, fallback_acceptance, False, fallback_operation)
    steps = _workbench_coerce_plan_steps(parsed, session)
    if not steps:
        fallback_operation = 'replace' if requested_operation == 'replace' else 'revise' if feedback else 'create'
        return (fallback, fallback_acceptance, False, fallback_operation)
    operation = 'create'
    if feedback:
        agent_operation = str(parsed.get('revisionMode') or '').strip().lower()
        if requested_operation in ('revise', 'replace'):
            operation = requested_operation
        elif agent_operation in ('revise', 'replace'):
            operation = agent_operation
        else:
            operation = str(routing.get('revisionMode') or 'revise')
        steps = _workbench_reconcile_revised_plan(existing_plan, steps, feedback, operation)
        revised_goal = str(parsed.get('goal') or '').strip()
        if revised_goal:
            session['goal'] = revised_goal
    else:
        steps = _workbench_normalize_plan(steps, task_id=str(session.get('id') or ''))
    valid_plan, _, _ = _workbench_validate_plan_graph(steps)
    if not valid_plan:
        for step in steps:
            step['dependsOn'] = []
    if auto_start:
        derived_goal = str(parsed.get('goal') or '').strip()
        derived_title = str(parsed.get('title') or '').strip()
        if derived_goal and project_runtime._workbench_is_blank_goal(session.get('goal')):
            session['goal'] = derived_goal
        if project_runtime._workbench_is_default_title(session.get('title')):
            session['title'] = (derived_title or project_runtime._workbench_derive_title(session.get('goal') or ''))[:80]
    planning_thread['goal'] = str(session.get('goal') or goal)
    planning_thread['constraints'] = constraints
    planning_thread['workspaceSnapshot'] = workspace_snapshot
    planning_thread['currentPlan'] = [{'id': str(step.get('id') or ''), 'title': str(step.get('title') or ''), 'description': str(step.get('description') or ''), 'dependsOn': _workbench_dependency_ids(step.get('dependsOn'))} for step in steps if isinstance(step, dict)]
    if feedback:
        decisions = planning_thread.setdefault('userDecisions', [])
        if isinstance(decisions, list):
            decisions.append(feedback[:2000])
            if len(decisions) > 30:
                del decisions[:-30]
    planning_thread['workspaceRevision'] = workspace_revision
    if len(planning_thread.get('userDecisions') or []) > 30:
        planning_thread['userDecisions'] = list(planning_thread['userDecisions'])[-30:]
    acceptance_session = dict(session)
    acceptance_session['plan'] = steps
    acceptance_fallback = _workbench_fallback_acceptance(acceptance_session, steps)
    raw_acceptance = parsed.get('acceptanceCriteria')
    has_generated_acceptance = isinstance(raw_acceptance, list) and any((str(item.get('text') if isinstance(item, dict) else item).strip() for item in raw_acceptance))
    if has_generated_acceptance:
        acceptance = _workbench_coerce_acceptance_criteria(parsed, acceptance_fallback)
    else:
        acceptance = acceptance_fallback
    return (steps, acceptance, True, operation)

def _workbench_acceptance_from_session(session: dict[str, Any]) -> list[dict[str, Any]]:
    existing = session.get('acceptanceCriteria')
    if isinstance(existing, list) and existing:
        return existing
    constraints = session.get('constraints') if isinstance(session.get('constraints'), list) else []
    items = [str(item) for item in constraints if str(item).strip()]
    if not items:
        items = [
            _l('The task goal is clear', '任务目标已明确'),
            _l('The plan has been generated', '计划已生成'),
            _l('Execution progress is traceable', '执行进度可追踪'),
            _l('A final summary has been produced', '最终总结已生成'),
        ]
    return [{'id': project_runtime._short_id('accept'), 'text': item, 'status': 'pending'} for item in items[:8]]

def _workbench_fallback_acceptance(session: dict[str, Any], steps: list[dict[str, Any]] | None=None) -> list[dict[str, Any]]:
    """Build deterministic criteria when the acceptance agent is unavailable."""
    constraints = [str(item).strip() for item in session.get('constraints') or [] if str(item).strip()]
    goal = str(session.get('goal') or session.get('title') or '').strip()
    items = constraints[:4]
    if goal:
        items.append(_l(
            'The task goal is complete: {goal}',
            '任务目标已完成：{goal}',
            goal=goal[:240],
        ))
    if steps:
        items.append(_l(
            'Every planned execution step is complete or has an explicit resolution',
            '计划中的执行步骤均已完成或有明确处理结论',
        ))
    items.extend([
        _l('Relevant changes or artifacts are traceable', '相关变更或产物可追踪'),
        _l('The final result has been verified and summarized', '最终结果已验证并形成总结'),
    ])
    unique: list[str] = []
    for item in items:
        if item and item not in unique:
            unique.append(item)
        if len(unique) >= 8:
            break
    return [{'id': project_runtime._short_id('accept'), 'text': item, 'status': 'pending'} for item in unique]

def _workbench_coerce_acceptance_criteria(raw: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize agent-produced acceptance criteria into session records."""
    source = raw.get('acceptanceCriteria') if isinstance(raw, dict) else raw
    if not isinstance(source, list):
        return fallback
    criteria: list[dict[str, Any]] = []
    for item in source:
        text = str(item.get('text') if isinstance(item, dict) else item).strip()
        if not text:
            continue
        criteria.append({'id': project_runtime._short_id('accept'), 'text': text[:300], 'status': 'pending'})
        if len(criteria) >= 8:
            break
    return criteria or fallback

async def _workbench_generate_acceptance_criteria(
    session: dict[str, Any],
    project: dict[str, Any],
    *,
    agent_runtime: Any = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Ask an agent to derive verifiable criteria from the current task plan."""
    plan = session.get('plan') if isinstance(session.get('plan'), list) else []
    fallback = _workbench_fallback_acceptance(session, plan)
    goal = str(session.get('goal') or session.get('title') or '').strip()
    constraints = [str(item).strip() for item in session.get('constraints') or [] if str(item).strip()]
    plan_separator = '：' if app_language() == 'zh' else ': '
    plan_lines = '\n'.join((
        f"- {step.get('title') or ''}{plan_separator}{step.get('description') or ''}"
        for step in plan if isinstance(step, dict)
    ))
    prompt = _l(
        'You are a task-acceptance design agent. Based on the task goal, constraints, current execution plan, and actual workspace contents, generate clear, specific, verifiable acceptance criteria. You may use list_directory, read_file, and glob to explore the project. Criteria should map to real files, behavior, tests, or artifacts where possible and avoid process-only statements such as “the goal is clear.”\n\nTask goal: {goal}\nConstraints: {constraints}\nCurrent plan:\n{plan}\n\nReturn exactly one JSON object without Markdown, using this structure:\n{{\n  "acceptanceCriteria": ["independently verifiable criterion"]\n}}\n\nRequirements: generate 3-8 criteria; each criterion expresses one verifiable result; cover core functionality, constraints, and required validation; write all criteria in English.',
        '你是任务验收设计 Agent。请根据任务目标、约束、当前执行计划和工作区实际内容，生成清晰、具体、可核验的验收标准。你可以使用 list_directory、read_file、glob 工具探索项目，标准应尽量对应真实文件、功能、测试或产物，避免“目标清晰”这类过程性描述。\n\n任务目标：{goal}\n约束：{constraints}\n当前计划：\n{plan}\n\n只返回一个 JSON 对象，不要 Markdown。结构：\n{{\n  "acceptanceCriteria": ["可独立核验的验收标准"]\n}}\n\n要求：生成 3-8 条；每条只表达一个可验证结果；覆盖核心功能、约束和必要验证；全部使用简体中文。',
        goal=goal or _l('No explicit goal', '暂无明确目标'),
        constraints=json.dumps(constraints, ensure_ascii=False),
        plan=plan_lines or _l('No plan', '暂无计划'),
    )
    if agent_runtime is None:
        return (fallback, False)
    try:
        parsed = await agent_runtime._independent_json_agent(
            project=project,
            session=session,
            purpose='acceptance_design',
            prompt=prompt,
        )
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        return (fallback, False)
    criteria = _workbench_coerce_acceptance_criteria(parsed, fallback)
    raw_criteria = parsed.get('acceptanceCriteria')
    generated = isinstance(raw_criteria, list) and any((str(item.get('text') if isinstance(item, dict) else item).strip() for item in raw_criteria))
    return (criteria, generated)

__all__ = ['_workbench_acceptance_from_session', '_workbench_apply_reflection_hints', '_workbench_coerce_acceptance_criteria', '_workbench_coerce_plan_steps', '_workbench_dependency_ids', '_workbench_existing_plan_block', '_workbench_extract_constraints', '_workbench_fallback_acceptance', '_workbench_follow_up_seed', '_workbench_generate_acceptance_criteria', '_workbench_generate_plan_steps', '_workbench_keep_ordered_dependencies', '_workbench_merge_hint_mutations', '_workbench_new_plan_step', '_workbench_normalize_plan', '_workbench_plan_definition_signature', '_workbench_plan_from_input', '_workbench_plan_has_started', '_workbench_plan_title_key', '_workbench_reconcile_revised_plan', '_workbench_reflection_candidates', '_workbench_render_task_brief_block', '_workbench_session_summary_text', '_workbench_step_dependencies_satisfied', '_workbench_store_reflection', '_workbench_validate_plan_graph']
