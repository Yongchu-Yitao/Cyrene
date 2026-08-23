"""Planning domain operations extracted from the Workbench compatibility runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from cyrene.workbench import generation_gateway
from cyrene.workbench import (
    memory,
    planning_contracts,
    project_runtime,
    task_initialization_runtime,
)

logger = logging.getLogger(__name__)


def _workbench_render_reflection_block(session: dict[str, Any]) -> str:
    reflection = session.get("reflection") if isinstance(session.get("reflection"), dict) else None
    packet = reflection.get("packet") if isinstance(reflection, dict) else None
    if not isinstance(packet, dict) or not packet:
        return ""
    try:
        from cyrene.agent.deep_reflection_prompts import render_deep_reflection_packet

        return render_deep_reflection_packet(packet)
    except Exception:
        logger.exception("Failed to render reflection packet")
        return ""


def _workbench_render_past_task_reports(project: dict[str, Any] | None) -> str:
    if not project:
        return ""
    try:
        return memory.render_task_reports_for_planning(
            project_runtime._workbench_project_memory_key(project), limit=3, max_chars=2500
        )
    except Exception:
        logger.exception("Failed to render past task reports for planning")
        return ""

async def _workbench_extract_constraints(text: str) -> list[str]:
    """Use a lightweight semantic pass to extract explicit task constraints.

    Constraints are requirements that restrict scope, implementation choices,
    compatibility, resources, timing, or behavior.  A model is used instead of
    keyword matching so negation in questions and descriptive prose is not
    mistaken for a task requirement.  Failure is fail-soft: the original user
    text remains available as the task goal/message and no guessed constraint is
    persisted.
    """
    source = str(text or '').strip()
    if not source:
        return []
    prompt = f'请判断下面的用户任务表述中是否包含明确的执行约束。约束是用户真正要求遵守的范围、禁止事项、必须保留项、技术/平台限制、兼容性要求、截止时间或资源限制。不要因为文本出现‘不’‘只’‘保留’等字样就机械提取；疑问、解释、背景事实、目标本身、建议和无法确定为用户要求的内容都不是约束。每条约束应保持原意、可独立理解、简洁，不得补充用户没有表达的要求。只返回 JSON：{{"constraints":["约束"]}}；没有约束时返回空数组，最多 8 条。\n\n用户表述：{source}'
    try:
        response = await asyncio.wait_for(generation_gateway.call_llm([{'role': 'user', 'content': prompt}], tools=None, max_tokens=700, secondary=True, thinking='disabled'), timeout=20)
    except Exception:
        logger.exception('Workbench constraint extraction failed')
        return []
    content = str(response.get('content') or '') if isinstance(response, dict) else ''
    parsed = task_initialization_runtime._workbench_parse_json_object(content)
    raw = parsed.get('constraints') if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        return []
    constraints: list[str] = []
    for value in raw:
        item = re.sub('\\s+', ' ', str(value or '').strip())[:300]
        if item and item not in constraints:
            constraints.append(item)
        if len(constraints) >= 8:
            break
    return constraints

def _workbench_new_plan_step(title: str, description: str, order: int, task_id: str='') -> dict[str, Any]:
    """A single execution-plan step — always starts pending (no pre-completion)."""
    return {'id': project_runtime._short_id('step'), 'taskId': task_id, 'title': str(title or '').strip(), 'description': str(description or '').strip(), 'status': 'pending', 'order': order, 'dependsOn': [], 'currentAction': '', 'relatedFiles': [], 'progressEvents': [], 'toolCalls': [], 'artifacts': [], 'error': None}

def _workbench_plan_from_input(user_input: str, session: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic FALLBACK plan, used only when LLM plan generation is
    unavailable. Every step starts ``pending`` — nothing is pre-marked done."""
    existing = session.get('plan') if isinstance(session.get('plan'), list) else []
    if existing:
        return existing
    base_steps = ['理解目标与约束', '收集相关信息和上下文', '分析现有内容', '制定执行方案', '推进执行', '验证结果并总结']
    task_id = session.get('id', '')
    return [_workbench_new_plan_step(title, '由兜底计划生成，请按需编辑。', index + 1, task_id) for index, title in enumerate(base_steps)]

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
        return (False, '计划格式无效。', 'invalid_plan')
    step_ids: list[str] = []
    titles: dict[str, str] = {}
    for index, step in enumerate(plan):
        if not isinstance(step, dict):
            return (False, f'第 {index + 1} 个步骤格式无效。', 'invalid_step')
        step_id = str(step.get('id') or '').strip()
        title = str(step.get('title') or '').strip()
        if not step_id:
            return (False, f'第 {index + 1} 个步骤缺少 id。', 'missing_step_id')
        if step_id in titles:
            return (False, '计划中存在重复的步骤 id。', 'duplicate_step_id')
        if not title:
            return (False, f'第 {index + 1} 个步骤标题不能为空。', 'empty_step_title')
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
                return (False, f'步骤「{titles[step_id]}」不能依赖自身。', 'self_dependency')
            if dependency_id not in known:
                return (False, f'步骤「{titles[step_id]}」引用了不存在的前置步骤。', 'missing_dependency')
            if require_dependency_order and positions[dependency_id] >= positions[step_id]:
                return (False, f'步骤「{titles[step_id]}」必须排在前置步骤「{titles[dependency_id]}」之后。', 'dependency_order')
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
        return (False, '步骤依赖形成了循环，请移除循环依赖。', 'dependency_cycle')
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
        dependency_suffix = '；前置步骤：' + '、'.join(dependency_titles) if dependency_titles else ''
        rows.append(f'{index}. id={step_id} [{status}] {title}{suffix}{dependency_suffix}')
    if not rows:
        return ''
    return '\n当前已有执行计划（除非用户明确要求删除/重排，请保留并在此基础上调整）：\n' + '\n'.join(rows)

def _workbench_session_summary_text(session: dict[str, Any]) -> str:
    """Extract the task's one-line summary (简介), tolerating the dict form the
    store sometimes holds (mirrors the frontend's sessionSummaryText)."""
    raw = session.get('summary')
    if isinstance(raw, dict):
        return str(raw.get('text') or raw.get('body') or raw.get('content') or raw.get('summary') or '').strip()
    return str(raw or '').strip()

def _workbench_follow_up_seed(session: dict[str, Any], *, requested_title: str='', requested_goal: str='') -> dict[str, Any]:
    """Build a deterministic follow-up task from the source task's live state."""
    source_title = str(session.get('title') or '任务').strip() or '任务'
    explicit_goal = str(requested_goal or '').strip()
    title = str(requested_title or '').strip()
    if not title:
        title = f'{source_title} · 后续'
    status_labels = {'idle': '未开始', 'answered': '已回答', 'acted': '已执行', 'planning': '规划中', 'waiting_for_approval': '等待确认', 'waiting_for_user': '等待用户', 'running': '执行中', 'review': '待验收', 'done': '已完成', 'completed': '已完成', 'failed': '失败', 'blocked': '阻塞', 'paused': '已暂停', 'cancelled': '已取消'}
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
    lines = [f'这是任务「{source_title}」的后续任务。']
    if explicit_goal:
        lines.append(f'本次后续要求：{explicit_goal}')
    if source_goal:
        lines.append(f'来源任务目标：{source_goal}')
    lines.append(f"来源任务当前状态：{status_labels.get(source_status, source_status or '未知')}")
    if source_summary:
        lines.append(f'来源任务摘要：{source_summary}')
    elif source_result:
        lines.append(f'来源任务当前结果：{source_result[:1200]}')
    if unresolved_steps:
        lines.append('尚未解决的步骤：' + '；'.join(unresolved_steps))
    if unresolved_acceptance:
        lines.append('尚未满足的验收项：' + '；'.join(unresolved_acceptance))
    if next_step:
        lines.append(f'反思建议的下一步：{next_step}')
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
    lines: list[str] = ['## 当前任务']
    if title:
        lines.append(f'- 标题：{title}')
    if goal:
        lines.append(f'- 目标：{goal}')
    if summary:
        lines.append(f'- 简介：{summary}')
    acceptance = session.get('acceptanceCriteria')
    if isinstance(acceptance, list):
        accept_texts = [str((a.get('text') if isinstance(a, dict) else a) or '').strip() for a in acceptance]
        accept_texts = [t for t in accept_texts if t][:8]
        if accept_texts:
            lines.append('- 验收标准：' + '；'.join(accept_texts))
    body = '\n'.join(lines)
    plan_block = _workbench_existing_plan_block(session)
    if plan_block:
        body += '\n' + plan_block.lstrip('\n')
    if session.get('titleLocked'):
        body += '\n（用户已手动设置任务标题，你不能修改标题；如标题/简介与实际工作不符，可用 set_task_goal 更新简介或目标。）'
    else:
        body += '\n（标题与简介都会显示在任务卡上；若与你实际要做的事不符，可用 set_task_goal 更新。）'
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

async def _workbench_generate_plan_steps(session: dict[str, Any], project: dict[str, Any], feedback: str='', auto_start: bool=False, requested_operation: str='auto') -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, str]:
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
        goal = '通读本项目的工作区文件与项目说明，判断当前最应该推进的工作并据此规划'
    fallback = existing_plan if feedback and existing_plan else _workbench_plan_from_input(goal, {'id': session.get('id', '')})
    existing_acceptance = session.get('acceptanceCriteria') if isinstance(session.get('acceptanceCriteria'), list) else []
    fallback_acceptance = [dict(item) for item in existing_acceptance if isinstance(item, dict)] if feedback and existing_plan and existing_acceptance else _workbench_fallback_acceptance(session, fallback)
    if not goal:
        return (fallback, fallback_acceptance, False, 'create')
    constraints = [str(c).strip() for c in session.get('constraints') or [] if str(c).strip()]
    workspace_path = str(project.get('workspacePath') or '').strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    planning_thread = task_initialization_runtime._workbench_planning_thread(session, workspace_root)
    previous_workspace_revision = str(planning_thread.get('workspaceRevision') or '')
    previous_workspace_snapshot = planning_thread.get('workspaceSnapshot') if isinstance(planning_thread.get('workspaceSnapshot'), dict) else {}
    tool_bundle_version, workspace_revision, workspace_snapshot, routing = await task_initialization_runtime._workbench_plan_tool_bundle(session, project, workspace_root, feedback=feedback, requested_operation=requested_operation, auto_start=auto_start)
    constraints_block = '\n约束：\n' + '\n'.join((f'- {c}' for c in constraints)) if constraints else ''
    feedback_block = '\n用户对计划的修改反馈（请据此调整）：' + feedback if feedback else ''
    workspace_delta_block = ''
    if previous_workspace_revision and previous_workspace_revision != workspace_revision:
        changed_files = sorted((path for path in set(previous_workspace_snapshot) | set(workspace_snapshot) if previous_workspace_snapshot.get(path) != workspace_snapshot.get(path)))
        workspace_delta_block = '\n工作区增量：' + task_initialization_runtime._workbench_stable_json({'type': 'workspace_delta', 'baseRevision': previous_workspace_revision, 'revision': workspace_revision, 'changedFiles': changed_files[:200], 'invalidatedObservations': ['directory/glob observations']})
    existing_plan_block = _workbench_existing_plan_block(session) if feedback and requested_operation != 'replace' else ''
    reflection_text = _workbench_render_reflection_block(session)
    reflection_block = '\n\n## 深度反思结论（必须据此调整计划）\n下面是对既往尝试的复盘。请避开其中的 excluded_paths（已被证明是死路的做法），优先采用 promising_directions（更有希望的方向），并参考 next_step：\n' + reflection_text if reflection_text else ''
    if tool_bundle_version == planning_contracts._WORKBENCH_PLANNER_EXPLORE_VERSION:
        explore_directive = '如确有必要，可用 list_directory、read_file、glob 探索工作区；已观察且未变化的内容不要重复读取，够用即止。'
    else:
        explore_directive = '本次不提供工作区探索工具，请基于规划历史、既往观察结果和下面的信息直接给出计划，不要尝试调用工具。'
    past_reports_block = _workbench_render_past_task_reports(project)
    reports_section = f'\n\n{past_reports_block}' if past_reports_block else ''
    if auto_start:
        if tool_bundle_version == planning_contracts._WORKBENCH_PLANNER_EXPLORE_VERSION:
            lead_in = '这是「直接开始」的任务——用户没有明确给出目标。请先用 list_directory、read_file、glob 通读这个项目（工作区文件 + 项目说明），判断当前最应该推进的一件工作，再据此给出 goal、title 和执行步骤；已观察且未变化的内容不要重复读取。'
        else:
            lead_in = '这是「直接开始」的任务——用户没有明确给出目标，且本次没有可用的工作区探索工具。请基于规划方向和已有信息，判断当前最应该推进的一件工作，再据此给出 goal、title 和执行步骤。'
        prompt = f'{lead_in}\n\n规划方向：{goal}{constraints_block}{workspace_delta_block}{reflection_block}{reports_section}\n\ngoal 要具体、贴合本项目实际、不要泛泛而谈，并尽量引用真实文件/目录/模块；验收标准要可独立核验，避免“目标清晰”这类过程性描述。按系统提示约定的 JSON 结构，只返回一个 JSON 对象，不要 Markdown 代码块标记。'
    else:
        prompt = f'请把下面这个任务拆解成清晰、有顺序、可逐步执行的步骤。\n{explore_directive}\n\n任务目标：{goal}{constraints_block}{existing_plan_block}{feedback_block}{workspace_delta_block}{reflection_block}{reports_section}\n\n任务涉及当前项目时，尽量引用真实文件、目录或模块；与当前项目无关时，围绕任务本身规划，不要引入无关的文件或代码操作。revisionMode 自行判断：仅补充、删改、调序或改变局部做法时用 revise；要求完全不同、全新、换一套、从头重做，或新目标与原计划明显不符时用 replace。按系统提示约定的 JSON 结构，只返回一个 JSON 对象，不要 Markdown 代码块标记。'
        if requested_operation == 'replace':
            prompt += '\n这是用户主动点击的「重新生成」：必须从最终任务目标重新独立拆解，至少一半步骤应采用不同的拆解方式或执行路径，不能只是改写措辞。'
    parsed = await task_initialization_runtime._workbench_run_explore_agent(workspace_root, prompt, max_tokens=12000, timeout=120, session_id=str(session.get('id') or ''), planning_thread=planning_thread, tool_bundle_version=tool_bundle_version, workspace_revision=workspace_revision)
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
    task_initialization_runtime._workbench_maybe_compact_planning_thread(planning_thread)
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
        items = ['任务目标已明确', '计划已生成', '执行进度可追踪', '最终总结已生成']
    return [{'id': project_runtime._short_id('accept'), 'text': item, 'status': 'pending'} for item in items[:8]]

def _workbench_fallback_acceptance(session: dict[str, Any], steps: list[dict[str, Any]] | None=None) -> list[dict[str, Any]]:
    """Build deterministic criteria when the acceptance agent is unavailable."""
    constraints = [str(item).strip() for item in session.get('constraints') or [] if str(item).strip()]
    goal = str(session.get('goal') or session.get('title') or '').strip()
    items = constraints[:4]
    if goal:
        items.append(f'任务目标已完成：{goal[:240]}')
    if steps:
        items.append('计划中的执行步骤均已完成或有明确处理结论')
    items.extend(['相关变更或产物可追踪', '最终结果已验证并形成总结'])
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

async def _workbench_generate_acceptance_criteria(session: dict[str, Any], project: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Ask an agent to derive verifiable criteria from the current task plan."""
    plan = session.get('plan') if isinstance(session.get('plan'), list) else []
    fallback = _workbench_fallback_acceptance(session, plan)
    goal = str(session.get('goal') or session.get('title') or '').strip()
    constraints = [str(item).strip() for item in session.get('constraints') or [] if str(item).strip()]
    plan_lines = '\n'.join((f"- {step.get('title') or ''}：{step.get('description') or ''}" for step in plan if isinstance(step, dict)))
    workspace_path = str(project.get('workspacePath') or '').strip()
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None
    prompt = f"""你是任务验收设计 Agent。请根据任务目标、约束、当前执行计划和工作区实际内容，生成清晰、具体、可核验的验收标准。你可以使用 list_directory、read_file、glob 工具探索项目，标准应尽量对应真实文件、功能、测试或产物，避免“目标清晰”这类过程性描述。\n\n任务目标：{goal or '暂无明确目标'}\n约束：{json.dumps(constraints, ensure_ascii=False)}\n当前计划：\n{plan_lines or '暂无计划'}\n\n只返回一个 JSON 对象，不要 Markdown。结构：\n{{\n  "acceptanceCriteria": ["可独立核验的验收标准"]\n}}\n\n要求：生成 3-8 条；每条只表达一个可验证结果；覆盖核心功能、约束和必要验证；全部使用简体中文。"""
    parsed = await task_initialization_runtime._workbench_run_explore_agent(workspace_root, prompt, max_tokens=6000, timeout=120, session_id=str(session.get('id') or ''))
    if not isinstance(parsed, dict):
        return (fallback, False)
    criteria = _workbench_coerce_acceptance_criteria(parsed, fallback)
    raw_criteria = parsed.get('acceptanceCriteria')
    generated = isinstance(raw_criteria, list) and any((str(item.get('text') if isinstance(item, dict) else item).strip() for item in raw_criteria))
    return (criteria, generated)

__all__ = ['_workbench_acceptance_from_session', '_workbench_coerce_acceptance_criteria', '_workbench_coerce_plan_steps', '_workbench_dependency_ids', '_workbench_existing_plan_block', '_workbench_extract_constraints', '_workbench_fallback_acceptance', '_workbench_follow_up_seed', '_workbench_generate_acceptance_criteria', '_workbench_generate_plan_steps', '_workbench_keep_ordered_dependencies', '_workbench_new_plan_step', '_workbench_normalize_plan', '_workbench_plan_definition_signature', '_workbench_plan_from_input', '_workbench_plan_has_started', '_workbench_plan_title_key', '_workbench_reconcile_revised_plan', '_workbench_render_task_brief_block', '_workbench_session_summary_text', '_workbench_step_dependencies_satisfied', '_workbench_validate_plan_graph']
