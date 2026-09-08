"""Incident-scoped diagnosis and repair, started explicitly from a failure card."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

from .checks import finding, readonly_db
from .evidence import redact


def failed_run_scope(database: Path, scope: dict) -> dict:
    """Older clients persist terminal errors as status=done, outcome=error."""
    if not scope.get('chat_id') or scope.get('run_id') or scope.get('incident_id') or not database.is_file():
        return scope
    try:
        with readonly_db(database) as db:
            row = db.execute('SELECT payload_json FROM workbench_chats WHERE chat_id = ?', (scope['chat_id'],)).fetchone()
        chat = json.loads(row[0]) if row else {}
        if scope.get('project_id') and chat.get('projectId') != scope['project_id']:
            raise ValueError('Conversation does not belong to this project')
        last = chat.get('lastRun') or {}
        if last.get('id') and (last.get('status') in {'failed', 'error'} or last.get('outcome') == 'error' or last.get('terminationReason') == 'agent_error'):
            return {**scope, 'run_id': last['id']}
    except (OSError, sqlite3.Error, TypeError):
        pass
    return scope


def run_evidence(database: Path, scope: dict) -> list[dict]:
    """Read only bounded result metadata; never return tool output or chat text."""
    chat, run = scope.get('chat_id'), scope.get('run_id')
    if not chat or not run:
        return []
    key = hashlib.sha256(chat.encode()).hexdigest()
    path = database.parent / 'agent-state/context/trees' / key[:2] / (key + '.sqlite3')
    if not path.is_file():
        return []
    results = []
    try:
        with readonly_db(path) as db:
            rows = db.execute("SELECT node_id, value_json FROM context_nodes WHERE json_valid(value_json) AND json_extract(value_json, '$.run_id') = ? ORDER BY created_at DESC LIMIT 30", (run,)).fetchall()
        for position, (node_id, raw) in enumerate(rows):
            if len(raw) > 1_000_000:
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                continue
            if value.get('role') == 'tool_results':
                pending = value.get('pending_question')
                if position == 0 and isinstance(pending, dict) and pending.get('status') == 'awaiting_user':
                    results.append(finding('run_pending_question', 'failed',
                        '失败运行末尾仍有待回答的问题或权限确认；不能自动代答',
                        'The failed run retains a pending question or permission confirmation; it cannot be answered automatically',
                        {'run_id': run, 'node_id': node_id, 'question_id': pending.get('id'), 'kind': pending.get('kind')}))
                for tool in (value.get('results') or [])[:30]:
                    if not isinstance(tool, dict):
                        continue
                    failure = tool.get('failure') or {}
                    if not isinstance(failure, dict):
                        failure = {}
                    results.append(finding('run_tool_failed' if tool.get('success') is False else 'run_tool_completed',
                        'failed' if tool.get('success') is False else 'info',
                        '本次运行工具结果（成功项不会重放）', 'Tool result in this run (successful calls are not replayed)',
                        {'node_id': node_id, 'run_id': run, 'tool': str(tool.get('name') or ''),
                         'call_id': str(tool.get('call_id') or ''), 'success': tool.get('success'),
                         'code': failure.get('error_code'), 'retry_scope': failure.get('retry_scope'), 'terminal_batch': position == 0}))
            elif value.get('role') == 'assistant' and (value.get('failure_kind') or value.get('error') is True or value.get('session_end_status') == 'failed'):
                results.append(finding(str(value.get('failure_kind') or 'agent_transition_failed'), 'failed', '本次 Agent 终止原因', 'Agent termination in this run',
                    {'run_id': run, 'node_id': node_id, 'failure_kind': value.get('failure_kind'), 'stage': value.get('failure_stage') or 'agent_transition', 'exception_type': value.get('exception_type'), 'frames': value.get('failure_frames', []), 'session_end_status': value.get('session_end_status'), 'caused_by': value.get('caused_by')}))
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return [finding('run_evidence_unavailable', 'unknown', '无法读取本次运行证据', 'Run evidence could not be read')]
    successful = [item for item in results if item['code'] == 'run_tool_completed']
    results = [item for item in results if item['code'] != 'run_tool_completed']
    if successful:
        results.append(finding('run_tools_completed', 'info', '已完成的工具调用汇总（不会重放）', 'Completed tool calls (not replayed)',
            {'run_id': run, 'count': len(successful), 'tools': sorted({item['evidence']['tool'] for item in successful})[:30]}))
    return results


def implicated_targets(report, host, plugins):
    """Only deterministic failure provenance can select an automatic write target."""
    targets = set()
    root = plugins.resolve()
    registry = getattr(host, 'registry', None)
    registered = registry.list_plugins() if registry else ()
    for item in report['findings']:
        evidence = item.get('evidence', {})
        # Incident frame annotations are produced by the host recorder, not by
        # the user description or by the diagnosis model.
        for frame in evidence.get('frames', []):
            target = frame.get('plugin')
            if isinstance(target, str) and target and not target.startswith('.') and '/' not in target and '\\' not in target and (root / target).exists() and not (root / target).is_symlink():
                targets.add(target)
        if item['code'] != 'run_tool_failed' or not evidence.get('terminal_batch') or evidence.get('retry_scope') == 'never' or str(evidence.get('code') or '').startswith(('permission_', 'plugin_review_')):
            continue
        for entry in registered:
            if entry.plugin.name != evidence.get('tool'):
                continue
            code = getattr(entry.plugin.handler, '__code__', None)
            for location in (entry.source, getattr(code, 'co_filename', '')):
                if not location:
                    continue
                source = Path(location).resolve()
                if source != root and source.is_relative_to(root):
                    targets.add(source.relative_to(root).parts[0])
    return sorted(targets)


async def start_failure(doctor, scope, *, language='zh', description=''):
    if not isinstance(description, str) or len(description) > 4000:
        raise ValueError('Problem description must be at most 4000 characters')
    if not scope.get('chat_id') and not scope.get('incident_id'):
        raise ValueError('Failure diagnosis requires a conversation or incident')
    if doctor.failure_tasks or doctor.tasks or doctor.lock.locked():
        # Reopening the same failure attaches to the running workflow.
        for identifier in doctor.failure_tasks:
            existing = doctor.get(identifier)
            if all(existing['scope'].get(k) == v for k, v in scope.items() if v):
                return existing
        raise ValueError('Wait for the current Doctor operation to finish')
    scope = await asyncio.to_thread(failed_run_scope, doctor.database, scope)
    report = await doctor.diagnose(scope, language=language, persist=False)
    incidents = [f['evidence'] for f in report['findings'] if str(f.get('evidence', {}).get('id', '')).startswith('incident_')]
    if incidents:
        selected = incidents[0]
        scope = {**report['scope'], 'incident_id': selected['id']}
        if selected.get('run_id'):
            scope['run_id'] = selected['run_id']
        if selected.get('chat_id'):
            scope['chat_id'] = selected['chat_id']
    else:
        scope = report['scope']
    report = await doctor.diagnose(scope, language=language)
    report['findings'].extend(await asyncio.to_thread(run_evidence, doctor.database, scope))
    from .runtime_evidence import collect_runtime_evidence
    report['findings'].extend(await asyncio.to_thread(collect_runtime_evidence, doctor.database, doctor.data, scope))
    for index, item in enumerate(report['findings']):
        item['id'] = 'e' + str(index + 1)
    report['findings'] = redact(report['findings'])
    report['user_description'] = '诊断并尝试修复这次失败运行，定位最后失败阶段；不要重放已成功工具或修改无关插件。' if language == 'zh' else 'Diagnose and attempt repair of this failed run. Locate its failed stage; do not replay successful tools or change unrelated plugins.'
    if description.strip():
        report['user_description'] = redact(description.strip())
    report['failure'] = {'status': 'running', 'phase': 'analyzing'}
    doctor.repository.save(report)

    def update(status='running', **fields):
        latest = doctor.get(report['id'])
        latest['failure'] = {**latest['failure'], **fields, 'status': status}
        doctor.repository.save(latest)

    async def work():
        repair_id = None
        try:
            current = doctor.get(report['id'])
            codes = {f['code'] for f in current['findings'] if f['status'] == 'failed'}
            # A generic terminal failure also needs a current connectivity check;
            # do this before analysis so the conclusion includes its result.
            should_probe = 'run_pending_question' not in codes and (any(c.startswith(('model_', 'llm_')) for c in codes) or not implicated_targets(current, doctor.host, doctor.plugins))
            if should_probe:
                update(phase='probing_model')
                probed = await doctor.probe_model(report['id'])
                current = doctor.get(report['id'])
                result = probed['model_probe']
                current['model_probe'] = result
                current['findings'].append(finding('diagnostic_model_probe', result['status'],
                    'Doctor 已主动测试当前模型连接（不重放原任务）', 'Doctor tested the current model connection without replaying the task',
                    {'run_id': scope.get('run_id'), **result}))
                for index, item in enumerate(current['findings']):
                    item['id'] = 'e' + str(index + 1)
                doctor.repository.save(current)
            update(phase='analyzing')
            await doctor.start_analysis(report['id'], description=report['user_description'])
            task = doctor.tasks.get(report['id'])
            if task:
                await asyncio.shield(task)
            latest = doctor.get(report['id'])
            # Explicit model terminal evidence takes precedence over incidental
            # plugin issues or tools that completed earlier in the same run.
            failure_codes = [f['code'] for f in latest['findings'] if f['status'] == 'failed' and (
                (scope.get('run_id') and f.get('evidence', {}).get('run_id') == scope['run_id']) or
                (scope.get('incident_id') and f.get('evidence', {}).get('id') == scope['incident_id']))]
            if 'run_pending_question' in failure_codes:
                update('needs_attention', phase='diagnosed', reason='pending_question_requires_answer')
                return
            action = latest['analysis'].get('repair_action')
            if action:
                prepared = await doctor.plan_repair(report['id'], action['finding_id'], action['action_index'])
                update('needs_review', phase='candidate_ready', fixed_plan_id=prepared['id'], reason='recovery_action_ready')
                return
            targets = implicated_targets(latest, doctor.host, doctor.plugins)
            selected = latest['analysis'].get('repair_target')
            inspected = {f.get('evidence', {}).get('target') for f in latest['findings'] if f['code'] == 'repair_source_inspected'}
            if selected and selected in inspected and selected in latest.get('plugin_targets', []):
                targets = [selected]
            model_failure = any(code.startswith(('model_', 'llm_')) for code in failure_codes)
            if model_failure and not selected:
                probed = doctor.get(report['id'])
                update('needs_attention', phase='model_checked', reason='model_probe_passed' if probed['model_probe']['status'] == 'passed' else 'model_probe_failed')
                return
            if len(targets) != 1:
                update('needs_attention', phase='diagnosed', reason='ambiguous_failure_target' if targets else ('host_transition_failed' if 'agent_transition_failed' in failure_codes else 'failure_target_unavailable'))
                return
            target = targets[0]
            update(phase='generating', target=target)
            plan = await doctor.repairs.start(report['id'], target, report['user_description'])
            repair_id = plan['id']
            update(repair_id=repair_id)
            task = doctor.tasks.get(repair_id)
            if task:
                await asyncio.shield(task)
            plan = doctor.get(repair_id)
            if plan['status'] != 'planned':
                update('needs_attention', phase='repair_failed', reason='candidate_unavailable')
                return
            baseline, verification = plan['baseline'], plan['verification']
            verified = (baseline['syntax']['status'] == 'failed' and verification['syntax']['status'] == 'passed') or (
                baseline.get('probe', {}).get('status') == 'failed' and verification.get('probe', {}).get('status') == 'passed')
            if not verified:
                update('needs_review', phase='candidate_ready', reason='runtime_not_verified')
                return
            update(phase='applying')
            applied = await doctor.apply_repair(repair_id, plan['plan_hash'])
            update('completed' if applied.get('outcome', {}).get('status') == 'verified' else 'needs_attention', phase='finished',
                   reason='restart_required' if applied.get('outcome', {}).get('status') == 'restart_required' else '')
        except asyncio.CancelledError:
            await doctor.cancel_analysis(report['id'])
            if repair_id:
                await doctor.repairs.cancel(repair_id)
            update('cancelled', phase='cancelled')
        except Exception as exc:
            update('needs_attention', reason=redact(str(exc)), phase='stopped')
        finally:
            doctor.failure_tasks.pop(report['id'], None)
    doctor.failure_tasks[report['id']] = asyncio.create_task(work())
    return report
