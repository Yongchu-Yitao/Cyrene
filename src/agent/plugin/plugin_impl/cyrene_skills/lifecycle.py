"""Pure learned-skill lifecycle policy owned by the Skills pack."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

import aiosqlite

from typing import Any, Iterable, Mapping

from cyrene.localization import localized

from .replay import HIGH_RISK_TOOLS, enabled_step_tool_names


def infer_risk_level(
    steps: Iterable[Mapping[str, Any]],
    high_risk_tools: frozenset[str],
) -> str:
    steps = list(steps)
    if any(
        bool(step.get("enabled", True))
        and str(step.get("implementation_kind") or "") == "script"
        for step in steps
    ):
        return "high"
    return (
        "high"
        if any(tool in high_risk_tools for tool in enabled_step_tool_names(steps))
        else "none"
    )

CORRECTION_TERMS = (
    "不对", "不行", "错", "重来", "改一下", "重新", "fix", "wrong", "retry", "instead",
)

SKILL_TYPE_ORDER = {
    "draft": 0,
    "workflow": 1,
    "parameterized": 2,
    "deterministic": 3,
}

CITY_ALIASES = {
    "beijing": "beijing",
    "北京": "beijing",
    "toronto": "toronto",
    "多伦多": "toronto",
}

WEATHER_ENTITY_HINTS = tuple(CITY_ALIASES.keys())

def _clone_json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))

def _path_parts(target_path: str) -> list[str | int]:
    parts: list[str | int] = []
    for raw in str(target_path or "").split("."):
        raw = raw.strip()
        if not raw:
            continue
        parts.append(int(raw) if raw.isdigit() else raw)
    return parts

def _walk_to_parent(root: Any, parts: list[str | int], *, create: bool = False) -> tuple[Any, str | int | None]:
    if not parts:
        return root, None
    current = root
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(part, int):
            if not isinstance(current, list):
                raise KeyError(f"Path segment {part} requires list container")
            while create and part >= len(current):
                current.append({} if not isinstance(next_part, int) else [])
            current = current[part]
            continue
        if not isinstance(current, dict):
            raise KeyError(f"Path segment {part} requires dict container")
        if part not in current or current[part] is None:
            if not create:
                raise KeyError(part)
            current[part] = [] if isinstance(next_part, int) else {}
        current = current[part]
    return current, parts[-1]

def _set_path_value(root: Any, target_path: str, value: Any, *, create: bool = True) -> None:
    parent, leaf = _walk_to_parent(root, _path_parts(target_path), create=create)
    if leaf is None:
        raise KeyError("empty target path")
    if isinstance(leaf, int):
        if not isinstance(parent, list):
            raise KeyError(f"Leaf {leaf} requires list container")
        while create and leaf >= len(parent):
            parent.append(None)
        parent[leaf] = value
        return
    if not isinstance(parent, dict):
        raise KeyError(f"Leaf {leaf} requires dict container")
    parent[leaf] = value

def _remove_path_value(root: Any, target_path: str) -> None:
    parent, leaf = _walk_to_parent(root, _path_parts(target_path), create=False)
    if leaf is None:
        raise KeyError("empty target path")
    if isinstance(leaf, int):
        if not isinstance(parent, list):
            raise KeyError(f"Leaf {leaf} requires list container")
        parent.pop(leaf)
        return
    if not isinstance(parent, dict):
        raise KeyError(f"Leaf {leaf} requires dict container")
    parent.pop(leaf, None)

def _build_patch_change_list(skill: dict[str, Any], patch_type: str, reason: str, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    extra = extra or {}
    if patch_type == "update_input_schema":
        current_policy = str((skill.get("fallback_policy") or {}).get("on_missing_args") or "fallback_to_agent")
        if current_policy != "ask_user":
            return [
                {
                    "operation": "replace",
                    "target_path": "fallback_policy.on_missing_args",
                    "old_value": current_policy,
                    "new_value": "ask_user",
                }
            ]
    if patch_type == "replace_step":
        failing_tool = ""
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", reason or "")
        if match:
            failing_tool = match.group(1)
        for index, step in enumerate(skill.get("steps") or []):
            reference = step.get("implementation_reference") or {}
            if failing_tool and str(reference.get("tool_name") or "") != failing_tool:
                continue
            current_policy = str(step.get("failure_policy") or "fail")
            if current_policy != "fallback_to_agent":
                return [
                    {
                        "operation": "replace",
                        "target_path": f"steps.{index}.failure_policy",
                        "old_value": current_policy,
                        "new_value": "fallback_to_agent",
                    }
                ]
            break
    return extra.get("change_list") or []

def _apply_change_list(definition: dict[str, Any], change_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for change in change_list:
        operation = str(change.get("operation") or "replace")
        target_path = str(change.get("target_path") or "").strip()
        if not target_path:
            continue
        if operation in {"add", "replace", "enable", "disable"}:
            new_value = change.get("new_value")
            if operation == "enable":
                new_value = True
            elif operation == "disable":
                new_value = False
            _set_path_value(definition, target_path, _clone_json_value(new_value), create=True)
        elif operation == "remove":
            _remove_path_value(definition, target_path)
        else:
            continue
        applied.append(change)
    return applied

clone_json_value = _clone_json_value
build_patch_change_list = _build_patch_change_list
apply_change_list = _apply_change_list

@dataclass(frozen=True, slots=True)
class LifecyclePorts:
    connect: Any
    default_skill_stats: Any
    get_stats_lock: Any
    is_reusable_skill_definition: Any
    json_dumps: Any
    json_loads: Any
    new_id: Any
    normalize_slot: Any
    now_iso: Any
    call_llm_json: Any
    current_session_id: Any
    current_turn_id: Any
    project_scope_for_session: Any


class LifecycleService:
    def __init__(self, ports: LifecyclePorts):
        self.ports = ports

    async def _unique_skill_name(self, conn: aiosqlite.Connection, preferred_name: str, *, skill_id: str='') -> str:
        base = str(preferred_name or '').strip() or '学习技能'
        candidate = base
        counter = 2
        while True:
            if skill_id:
                cursor = await conn.execute('SELECT skill_id FROM learned_skills WHERE name = ? AND skill_id != ?', (candidate, skill_id))
                row = await cursor.fetchone()
            else:
                cursor = await conn.execute('SELECT skill_id FROM learned_skills WHERE name = ?', (candidate,))
                row = await cursor.fetchone()
            if row is None:
                return candidate
            candidate = f'{base} {counter}'
            counter += 1

    def _infer_skill_risk_level(self, steps: list[dict[str, Any]]) -> str:
        """Return 'high' if any enabled step references a high-risk tool, else 'none'."""
        return infer_risk_level(steps, HIGH_RISK_TOOLS)

    def _skill_stats_with_usage_counters(self, stats: dict[str, Any] | None) -> dict[str, Any]:
        raw = stats or {}
        merged = {**self.ports.default_skill_stats(), **raw}
        actual_runs = int(merged.get('actual_runs') or 0)
        if 'actual_runs' not in raw:
            actual_runs = int(merged.get('active_success') or 0) + int(merged.get('active_failure') or 0)
        merged['actual_runs'] = actual_runs
        return merged

    def _skill_row_to_definition(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        steps = self.ports.json_loads(data['steps_json'], [])
        risk_level = self._infer_skill_risk_level(steps)
        guards = self.ports.json_loads(data['guards_json'], {})
        if isinstance(guards, dict):
            guards = {**guards, 'risk_level': risk_level}
        return {'skill_id': data['skill_id'], 'project_id': data.get('project_id', ''), 'project_key': data.get('project_key', ''), 'name': data['name'], 'description': data['description'], 'version': int(data['current_version']), 'status': data['status'], 'skill_type': data['skill_type'], 'risk_level': risk_level, 'requires_llm': bool(data['requires_llm']), 'trigger': self.ports.json_loads(data['trigger_json'], {}), 'input_schema': self.ports.json_loads(data['input_schema_json'], []), 'parameter_extractor': self.ports.json_loads(data['parameter_extractor_json'], {}), 'steps': steps, 'script': self.ports.json_loads(data.get('script_json'), {}), 'guards': guards, 'fallback_policy': self.ports.json_loads(data['fallback_policy_json'], {}), 'tests': self.ports.json_loads(data['tests_json'], []), 'editable_fields': self.ports.json_loads(data['editable_fields_json'], []), 'created_from': self.ports.json_loads(data['created_from_json'], {}), 'run_statistics': self.ports.json_loads(data['run_statistics_json'], {}), 'created_at': data['created_at'], 'updated_at': data['updated_at']}

    async def _save_skill_version(self, *, conn: aiosqlite.Connection, skill_id: str, version: int, parent_version: int | None, definition: dict[str, Any], change_type: str, change_summary: str, patch_list: list[dict[str, Any]] | None=None, test_result: dict[str, Any] | None=None, rollback_target: int | None=None) -> None:
        await conn.execute('\n        INSERT OR REPLACE INTO learned_skill_versions\n        (skill_id, version, parent_version, skill_definition, change_type, change_summary,\n         patch_list, created_at, test_result, rollback_target)\n        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n        ', (skill_id, version, parent_version, self.ports.json_dumps(definition), change_type, change_summary, self.ports.json_dumps(patch_list or []), self.ports.now_iso(), self.ports.json_dumps(test_result or {}), rollback_target))

    async def manual_activate_skill(self, skill_id: str) -> bool:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT * FROM learned_skills WHERE skill_id = ?', (skill_id,))
            row = await cursor.fetchone()
        if row is None:
            return False
        current = self._skill_row_to_definition(row)
        next_version = int(row['current_version']) + 1
        current['status'] = 'active'
        current['version'] = next_version
        current['updated_at'] = self.ports.now_iso()
        async with self.ports.connect() as conn:
            await conn.execute("UPDATE learned_skills SET status = 'active', current_version = ?, updated_at = ? WHERE skill_id = ?", (next_version, current['updated_at'], skill_id))
            await self._save_skill_version(
                conn=conn,
                skill_id=skill_id,
                version=next_version,
                parent_version=int(row['current_version']),
                definition=current,
                change_type='activate',
                change_summary=localized(
                    'Manually activated from the evolution UI.',
                    '已从演化界面手动启用。',
                ),
            )
            await conn.commit()
        return True

    async def manual_deprecate_skill(self, skill_id: str) -> bool:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT * FROM learned_skills WHERE skill_id = ?', (skill_id,))
            row = await cursor.fetchone()
            if row is None:
                return False
            current = self._skill_row_to_definition(row)
            next_version = int(row['current_version']) + 1
            current['status'] = 'deprecated'
            current['version'] = next_version
            current['updated_at'] = self.ports.now_iso()
            await conn.execute("UPDATE learned_skills SET status = 'deprecated', current_version = ?, updated_at = ? WHERE skill_id = ?", (next_version, current['updated_at'], skill_id))
            await self._save_skill_version(
                conn=conn,
                skill_id=skill_id,
                version=next_version,
                parent_version=int(row['current_version']),
                definition=current,
                change_type='deprecate',
                change_summary=localized(
                    'Manually deprecated from the evolution UI.',
                    '已从演化界面手动停用。',
                ),
            )
            await conn.commit()
        return True

    async def delete_learned_skill(self, skill_id: str) -> bool:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT skill_id FROM learned_skills WHERE skill_id = ?', (skill_id,))
            row = await cursor.fetchone()
            if row is None:
                return False
            await conn.execute('DELETE FROM learned_skill_patches WHERE skill_id = ?', (skill_id,))
            await conn.execute('DELETE FROM learned_skill_runs WHERE skill_id = ?', (skill_id,))
            await conn.execute('DELETE FROM learned_skill_versions WHERE skill_id = ?', (skill_id,))
            await conn.execute("\n            UPDATE behavior_skill_candidates\n            SET status = 'dismissed', linked_skill_id = '', user_decision = 'skill_deleted', updated_at = ?\n            WHERE linked_skill_id = ?\n            ", (self.ports.now_iso(), skill_id))
            await conn.execute('DELETE FROM learned_skills WHERE skill_id = ?', (skill_id,))
            await conn.commit()
        return True

    async def _update_skill_run_stats(self, skill_id: str, *, execution_status: str, consistency_score: float=0.0) -> None:
        async with self.ports.get_stats_lock():
            async with self.ports.connect() as conn:
                cursor = await conn.execute('SELECT * FROM learned_skills WHERE skill_id = ?', (skill_id,))
                row = await cursor.fetchone()
                if row is None:
                    return
                stats = self._skill_stats_with_usage_counters(self.ports.json_loads(row['run_statistics_json'], self.ports.default_skill_stats()))
                stats['total_runs'] = int(stats.get('total_runs') or 0) + 1
                stats['last_run_at'] = self.ports.now_iso()
                total_runs = stats['total_runs']
                old_consistency = float(stats.get('consistency_avg') or 0.0)
                stats['consistency_avg'] = round((old_consistency * (total_runs - 1) + consistency_score) / total_runs, 4)
                if execution_status == 'success':
                    stats['active_success'] = int(stats.get('active_success') or 0) + 1
                    stats['actual_runs'] = int(stats.get('actual_runs') or 0) + 1
                elif execution_status == 'failure':
                    stats['active_failure'] = int(stats.get('active_failure') or 0) + 1
                    stats['actual_runs'] = int(stats.get('actual_runs') or 0) + 1
                elif execution_status == 'fallback':
                    stats['active_failure'] = int(stats.get('active_failure') or 0) + 1
                await conn.execute('UPDATE learned_skills SET run_statistics_json = ?, updated_at = ? WHERE skill_id = ?', (self.ports.json_dumps(stats), self.ports.now_iso(), skill_id))
                await conn.commit()

    async def _create_patch_proposal(self, skill_id: str, base_version: int, patch_type: str, reason: str, patch_content: dict[str, Any]) -> None:
        async with self.ports.connect() as conn:
            await conn.execute("\n            INSERT INTO learned_skill_patches\n            (patch_id, skill_id, base_version, patch_type, reason, patch_content, risk_assessment, status, created_at)\n            VALUES (?, ?, ?, ?, ?, ?, '', 'proposed', ?)\n            ", (self.ports.new_id('patch'), skill_id, base_version, patch_type, reason, self.ports.json_dumps(patch_content), self.ports.now_iso()))
            await conn.commit()

    async def _maybe_propose_patch(self, skill_id: str, version: int, failure_reason: str) -> None:
        reason = str(failure_reason or '')
        if not reason:
            return
        skill = await self.get_learned_skill(skill_id)
        if skill is None:
            return
        lowered = reason.lower()
        if 'missing' in lowered or 'parameter' in lowered or '参数' in reason:
            patch_type = 'update_input_schema'
        else:
            patch_type = 'replace_step'
        await self._create_patch_proposal(skill_id, version, patch_type, reason, {'failure_reason': reason, 'change_list': _build_patch_change_list(skill, patch_type, reason)})

    async def list_learned_skills(self, project_id: str='') -> list[dict[str, Any]]:
        async with self.ports.connect() as conn:
            pid = str(project_id or '').strip()
            if pid:
                cursor = await conn.execute('SELECT * FROM learned_skills WHERE project_id = ? ORDER BY updated_at DESC', (pid,))
            else:
                cursor = await conn.execute('SELECT * FROM learned_skills ORDER BY updated_at DESC')
            rows = await cursor.fetchall()
        definitions = [definition for definition in (self._skill_row_to_definition(row) for row in rows) if self.ports.is_reusable_skill_definition(definition)]
        skills: list[dict[str, Any]] = []
        for definition in definitions:
            trigger = definition['trigger']
            stats = self._skill_stats_with_usage_counters(definition['run_statistics'])
            actual_usage_count = int(stats.get('actual_runs') or 0)
            skills.append({'id': definition['skill_id'], 'project_id': definition.get('project_id', ''), 'project_key': definition.get('project_key', ''), 'name': definition['name'], 'description': definition['description'], 'status': definition['status'], 'skill_type': definition['skill_type'], 'risk_level': definition['risk_level'], 'version': definition['version'], 'requires_llm': definition['requires_llm'], 'trigger': trigger, 'input_schema': definition['input_schema'], 'steps': definition['steps'], 'script': definition.get('script') or {}, 'run_statistics': stats, 'actual_usage_count': actual_usage_count, 'updated_at': definition['updated_at'], 'created_at': definition['created_at'], 'positive_examples': trigger.get('positive_examples') or []})
        return skills

    async def build_learned_skill_block(self, session_id: str='', max_skills: int=20, *, scope: dict[str, str] | None=None) -> str:
        """Build a compact system-prompt block listing active learned skill names.
    
        Returns empty string when there are no active skills for the session's
        project.  Within a session the result is stable, so callers can safely
        cache it in the system prompt without degrading prefix-cache hit rates.
        """
        current_sid = str(session_id or self.ports.current_session_id.get() or '').strip()
        resolved_scope = scope or self.ports.project_scope_for_session(current_sid or None)
        async with self.ports.connect() as conn:
            cursor = await conn.execute("\n            SELECT *\n            FROM learned_skills\n            WHERE status = 'active' AND project_id = ?\n            ORDER BY updated_at DESC\n            LIMIT ?\n            ", (resolved_scope['project_id'], max(int(max_skills or 20), 1)))
            rows = await cursor.fetchall()
        if not rows:
            return ''
        lines: list[str] = ['## Learned Skills']
        for row in rows:
            definition = self._skill_row_to_definition(row)
            if not self.ports.is_reusable_skill_definition(definition):
                continue
            name = str(definition['name'] or '').strip()
            desc = str(definition['description'] or '').strip()
            if name:
                entry = f'- {name}'
                if desc:
                    entry += f': {desc[:120]}'
                lines.append(entry)
        return '\n'.join(lines) if len(lines) > 1 else ''

    async def get_learned_skill(self, skill_id: str) -> dict[str, Any] | None:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT * FROM learned_skills WHERE skill_id = ?', (skill_id,))
            row = await cursor.fetchone()
        if row is None:
            return None
        definition = self._skill_row_to_definition(row)
        return definition if self.ports.is_reusable_skill_definition(definition) else None

    async def get_learned_skill_by_name(self, name: str, session_id: str='') -> dict[str, Any] | None:
        """Look up an active learned skill by name for the current session's project."""
        current_sid = str(session_id or self.ports.current_session_id.get() or '').strip()
        scope = self.ports.project_scope_for_session(current_sid or None)
        async with self.ports.connect() as conn:
            cursor = await conn.execute("SELECT * FROM learned_skills WHERE status = 'active' AND project_id = ? AND name = ?", (scope['project_id'], str(name or '').strip()))
            row = await cursor.fetchone()
        if row is None:
            return None
        definition = self._skill_row_to_definition(row)
        return definition if self.ports.is_reusable_skill_definition(definition) else None

    async def record_manual_skill_run(self, skill_id: str, version: int, *, execution_status: str='success', consistency_score: float=0.0) -> None:
        """Record a skill run initiated through the explicit learned-skill tool."""
        from cyrene.runtime.settings_store import get_write_permission_mode as _get_perm_mode
        run_id = self.ports.new_id('skill_run')
        turn_id = self.ports.current_turn_id.get()
        async with self.ports.connect() as conn:
            await conn.execute("\n            INSERT INTO learned_skill_runs\n            (run_id, skill_id, version, turn_id, match_score, parameter_status, execution_status, failure_reason,\n             fallback_used, user_feedback, dry_run, consistency_score, permission_snapshot, created_at)\n            VALUES (?, ?, ?, ?, 1.0, 'manual', ?, '', 0, '', 0, ?, ?, ?)\n            ", (run_id, skill_id, version, turn_id or '', execution_status, round(consistency_score, 4), _get_perm_mode(), self.ports.now_iso()))
            await conn.commit()
        await self._update_skill_run_stats(skill_id, execution_status=execution_status, consistency_score=consistency_score)

    async def list_learned_skill_versions(self, skill_id: str) -> list[dict[str, Any]]:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('\n            SELECT skill_id, version, parent_version, change_type, change_summary, patch_list, created_at,\n                   test_result, rollback_target\n            FROM learned_skill_versions\n            WHERE skill_id = ?\n            ORDER BY version DESC\n            ', (skill_id,))
            rows = await cursor.fetchall()
        return [{'skill_id': str(row['skill_id']), 'version': int(row['version']), 'parent_version': int(row['parent_version']) if row['parent_version'] is not None else None, 'change_type': str(row['change_type'] or ''), 'change_summary': str(row['change_summary'] or ''), 'patch_list': self.ports.json_loads(row['patch_list'], []), 'created_at': str(row['created_at'] or ''), 'test_result': self.ports.json_loads(row['test_result'], {}), 'rollback_target': int(row['rollback_target']) if row['rollback_target'] is not None else None} for row in rows]

    async def list_learned_skill_patches(self, skill_id: str, status: str='all') -> list[dict[str, Any]]:
        async with self.ports.connect() as conn:
            if status == 'all':
                cursor = await conn.execute('\n                SELECT *\n                FROM learned_skill_patches\n                WHERE skill_id = ?\n                ORDER BY created_at DESC\n                ', (skill_id,))
                rows = await cursor.fetchall()
            else:
                cursor = await conn.execute('\n                SELECT *\n                FROM learned_skill_patches\n                WHERE skill_id = ? AND status = ?\n                ORDER BY created_at DESC\n                ', (skill_id, status))
                rows = await cursor.fetchall()
        return [{'patch_id': str(row['patch_id']), 'skill_id': str(row['skill_id']), 'base_version': int(row['base_version']), 'patch_type': str(row['patch_type'] or ''), 'reason': str(row['reason'] or ''), 'patch_content': self.ports.json_loads(row['patch_content'], {}), 'risk_assessment': str(row['risk_assessment'] or ''), 'status': str(row['status'] or ''), 'created_at': str(row['created_at'] or '')} for row in rows]

    async def list_learned_skill_runs(self, skill_id: str, limit: int=50) -> list[dict[str, Any]]:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('\n            SELECT *\n            FROM learned_skill_runs\n            WHERE skill_id = ?\n            ORDER BY created_at DESC\n            LIMIT ?\n            ', (skill_id, max(1, int(limit))))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _sanitize_skill_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        sanitized = _clone_json_value(definition)
        if isinstance(sanitized.get('input_schema'), list):
            sanitized['input_schema'] = [self.ports.normalize_slot(item) for item in sanitized['input_schema'] if isinstance(item, dict)]
        for key in ('parameter_extractor', 'guards', 'fallback_policy', 'created_from', 'run_statistics'):
            if not isinstance(sanitized.get(key), dict):
                sanitized[key] = {}
        for key in ('steps', 'tests', 'editable_fields'):
            if not isinstance(sanitized.get(key), list):
                sanitized[key] = []
        if self._infer_skill_risk_level(sanitized.get('steps') or []) == 'high':
            sanitized['risk_level'] = 'high'
            if isinstance(sanitized.get('guards'), dict):
                sanitized['guards']['risk_level'] = 'high'
        return sanitized

    async def _persist_skill_version(self, conn: aiosqlite.Connection, *, skill_id: str, current_row: sqlite3.Row, definition: dict[str, Any], change_type: str, change_summary: str, patch_list: list[dict[str, Any]] | None=None, test_result: dict[str, Any] | None=None, rollback_target: int | None=None) -> dict[str, Any]:
        now = self.ports.now_iso()
        next_version = int(current_row['current_version']) + 1
        persisted = {'skill_id': skill_id, **definition, 'version': next_version, 'created_at': definition.get('created_at') or str(current_row['created_at'] or now), 'updated_at': now, 'run_statistics': definition.get('run_statistics') or self.ports.json_loads(current_row['run_statistics_json'], self.ports.default_skill_stats())}
        script = _clone_json_value(persisted.get('script') or {})
        if str(script.get('format') or '') != 'cyrene.parameterized-tool-script':
            script = {'format': 'cyrene.parameterized-tool-script', 'execution': {'stop_on_failure': True, 'record_run': True, 'suppress_relearning': True}, 'source_turn_ids': (persisted.get('created_from') or {}).get('turn_list') or []}
        script.update({'version': next_version, 'name': str(persisted.get('name') or ''), 'description': str(persisted.get('description') or ''), 'parameters': persisted.get('input_schema') or [], 'steps': persisted.get('steps') or [], 'risk': {'level': str(persisted.get('risk_level') or 'none'), 'requires_runtime_approval': str(persisted.get('risk_level') or 'none') == 'high'}})
        persisted['script'] = script
        await conn.execute('\n        UPDATE learned_skills\n        SET name = ?, description = ?, current_version = ?, status = ?, skill_type = ?, risk_level = ?,\n            requires_llm = ?, trigger_json = ?, input_schema_json = ?, parameter_extractor_json = ?,\n            steps_json = ?, script_json = ?, guards_json = ?, fallback_policy_json = ?, tests_json = ?, editable_fields_json = ?,\n            created_from_json = ?, run_statistics_json = ?, updated_at = ?\n        WHERE skill_id = ?\n        ', (str(persisted.get('name') or ''), str(persisted.get('description') or ''), next_version, str(persisted.get('status') or 'draft'), str(persisted.get('skill_type') or 'draft'), str(persisted.get('risk_level') or 'none'), 1 if bool(persisted.get('requires_llm')) else 0, self.ports.json_dumps(persisted.get('trigger') or {}), self.ports.json_dumps(persisted.get('input_schema') or []), self.ports.json_dumps(persisted.get('parameter_extractor') or {}), self.ports.json_dumps(persisted.get('steps') or []), self.ports.json_dumps(script), self.ports.json_dumps(persisted.get('guards') or {}), self.ports.json_dumps(persisted.get('fallback_policy') or {}), self.ports.json_dumps(persisted.get('tests') or []), self.ports.json_dumps(persisted.get('editable_fields') or []), self.ports.json_dumps(persisted.get('created_from') or {}), self.ports.json_dumps(persisted.get('run_statistics') or self.ports.default_skill_stats()), now, skill_id))
        await self._save_skill_version(conn=conn, skill_id=skill_id, version=next_version, parent_version=int(current_row['current_version']), definition=persisted, change_type=change_type, change_summary=change_summary, patch_list=patch_list, test_result=test_result, rollback_target=rollback_target)
        return persisted

    def _extract_with_rules(self, user_message: str, schema_item: dict[str, Any]) -> tuple[Any, float]:
        text = str(user_message or '')
        aliases = [str(item).lower() for item in schema_item.get('aliases') or [] if str(item).strip()]
        schema_type = str(schema_item.get('type') or 'text')
        examples = [str(item) for item in schema_item.get('examples') or []]
        if examples:
            for example in examples:
                if example and example in text:
                    return (example, 0.95)
        if schema_type in {'path', 'file', 'filepath'} or any((alias in {'path', 'file', 'file_path'} for alias in aliases)):
            match = re.search('(~?/?[A-Za-z0-9_.-][A-Za-z0-9_./-]*\\.[A-Za-z0-9]{1,8}|~?/?[A-Za-z0-9_.-][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]+)', text)
            if match:
                return (match.group(1), 0.85)
        if schema_type in {'number', 'int', 'float'}:
            match = re.search('-?\\d+(?:\\.\\d+)?', text)
            if match:
                raw = match.group(0)
                return (float(raw) if '.' in raw else int(raw), 0.8)
        if schema_type == 'date':
            match = re.search('\\b\\d{4}-\\d{2}-\\d{2}\\b', text)
            if match:
                return (match.group(0), 0.9)
        if schema_type == 'url':
            match = re.search('https?://\\S+', text)
            if match:
                return (match.group(0), 0.9)
        quoted = re.findall('"([^"]+)"|\\\'([^\\\']+)\\\'', text)
        if quoted:
            first = next((item[0] or item[1] for item in quoted if item[0] or item[1]), '')
            if first:
                return (first, 0.65)
        return (None, 0.0)

    async def _extract_with_llm(self, *, user_message: str, context_summary: str, input_schema: list[dict[str, Any]], partial_params: dict[str, Any]) -> dict[str, Any]:
        prompt = f'Extract parameters for a learned automation skill.\n\nReturn JSON only:\n{{"params": {{"name": "value"}}}}\n\nUser message:\n{user_message}\n\nContext summary:\n{context_summary}\n\nInput schema:\n{json.dumps(input_schema, ensure_ascii=False, indent=2)}\n\nAlready extracted params:\n{json.dumps(partial_params, ensure_ascii=False, indent=2)}\n'
        result = await self.ports.call_llm_json(prompt, caller='skill_param_extractor')
        params = result.get('params')
        return params if isinstance(params, dict) else {}

    async def extract_skill_parameters(self, *, user_message: str, context_summary: str, input_schema: list[dict[str, Any]], llm_fallback: bool=True, overrides: dict[str, Any] | None=None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        confidence_scores: list[float] = []
        overrides = overrides or {}
        for item in input_schema:
            name = str(item.get('parameter_name') or item.get('name') or '').strip()
            if not name:
                continue
            if name in overrides:
                params[name] = overrides[name]
                confidence_scores.append(1.0)
                continue
            value, score = self._extract_with_rules(user_message, item)
            if value is not None:
                params[name] = value
                confidence_scores.append(score)
                continue
            default_value = item.get('default_value')
            if default_value not in (None, '') and (not item.get('required', False)):
                params[name] = default_value
                confidence_scores.append(0.55)
        missing_required = [str(item.get('parameter_name') or item.get('name') or '') for item in input_schema if bool(item.get('required', False)) and str(item.get('parameter_name') or item.get('name') or '') and (str(item.get('parameter_name') or item.get('name') or '') not in params)]
        if missing_required and llm_fallback:
            llm_params = await self._extract_with_llm(user_message=user_message, context_summary=context_summary, input_schema=input_schema, partial_params=params)
            for key, value in llm_params.items():
                if key not in params and value not in (None, ''):
                    params[key] = value
                    confidence_scores.append(0.7)
            missing_required = [item for item in missing_required if item not in params]
        confidence = round(sum(confidence_scores) / len(confidence_scores), 4) if confidence_scores else 0.0
        return {'params': params, 'missing_required': missing_required, 'complete': not missing_required, 'confidence': confidence}

    async def update_learned_skill(self, skill_id: str, updates: dict[str, Any], *, reason: str='') -> dict[str, Any] | None:
        if not isinstance(updates, dict):
            return None
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT * FROM learned_skills WHERE skill_id = ?', (skill_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            current = self._skill_row_to_definition(row)
            definition = _clone_json_value(current)
            allowed_fields = {'name', 'description', 'status', 'skill_type', 'risk_level', 'requires_llm', 'trigger', 'input_schema', 'parameter_extractor', 'steps', 'guards', 'fallback_policy', 'editable_fields', 'created_from'}
            changed_fields = {key for key in updates.keys() if key in allowed_fields}
            for field in changed_fields:
                definition[field] = _clone_json_value(updates[field])
            structural_fields = {'trigger', 'input_schema', 'parameter_extractor', 'steps', 'guards', 'fallback_policy', 'skill_type'}
            if structural_fields & changed_fields and 'status' not in changed_fields:
                definition['status'] = 'active'
            definition['created_at'] = current['created_at']
            definition['run_statistics'] = current['run_statistics']
            sanitized = await self._sanitize_skill_definition(definition)
            valid_statuses = {'draft', 'active', 'refined', 'deprecated'}
            if str(sanitized.get('status') or '') not in valid_statuses:
                sanitized['status'] = current['status']
            if str(sanitized.get('skill_type') or '') not in SKILL_TYPE_ORDER:
                sanitized['skill_type'] = current['skill_type']
            sanitized['requires_llm'] = bool(sanitized.get('requires_llm'))
            persisted = await self._persist_skill_version(
                conn,
                skill_id=skill_id,
                current_row=row,
                definition=sanitized,
                change_type='manual_edit',
                change_summary=reason or localized(
                    'Manual skill edit.',
                    '手动编辑技能。',
                ),
            )
            await conn.commit()
        return persisted

    async def apply_skill_patch(self, skill_id: str, patch_id: str) -> dict[str, Any]:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT * FROM learned_skills WHERE skill_id = ?', (skill_id,))
            skill_row = await cursor.fetchone()
            cursor = await conn.execute('SELECT * FROM learned_skill_patches WHERE skill_id = ? AND patch_id = ?', (skill_id, patch_id))
            patch_row = await cursor.fetchone()
            if skill_row is None or patch_row is None:
                return {
                    'ok': False,
                    'code': 'skill_patch_not_found',
                    'error': localized(
                        'Skill or patch not found.',
                        '未找到技能或补丁。',
                    ),
                }
            if str(patch_row['status'] or '') != 'proposed':
                return {
                    'ok': False,
                    'code': 'skill_patch_not_proposed',
                    'error': localized(
                        'The patch is not in the proposed state.',
                        '补丁不处于待应用状态。',
                    ),
                }
            current = self._skill_row_to_definition(skill_row)
            patch_content = self.ports.json_loads(patch_row['patch_content'], {})
            change_list = patch_content.get('change_list') or []
            if not change_list:
                change_list = _build_patch_change_list(current, str(patch_row['patch_type'] or ''), str(patch_row['reason'] or ''), patch_content)
            if not change_list:
                return {
                    'ok': False,
                    'code': 'skill_patch_requires_manual_edit',
                    'error': localized(
                        'This patch is advisory only and requires manual editing.',
                        '此补丁仅供参考，需要手动编辑。',
                    ),
                }
            definition = _clone_json_value(current)
            applied_changes = _apply_change_list(definition, change_list)
            definition['status'] = 'active'
            definition['created_at'] = current['created_at']
            definition['run_statistics'] = current['run_statistics']
            sanitized = await self._sanitize_skill_definition(definition)
            persisted = await self._persist_skill_version(
                conn,
                skill_id=skill_id,
                current_row=skill_row,
                definition=sanitized,
                change_type='apply_patch',
                change_summary=str(
                    patch_row['reason']
                    or localized('Applied skill patch.', '已应用技能补丁。')
                ),
                patch_list=applied_changes,
            )
            await conn.execute("UPDATE learned_skill_patches SET status = 'applied' WHERE patch_id = ?", (patch_id,))
            await conn.commit()
        return {'ok': True, 'skill': persisted, 'patch_id': patch_id, 'applied_changes': applied_changes}

    async def reject_skill_patch(self, skill_id: str, patch_id: str) -> bool:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT patch_id FROM learned_skill_patches WHERE skill_id = ? AND patch_id = ?', (skill_id, patch_id))
            row = await cursor.fetchone()
            if row is None:
                return False
            await conn.execute("UPDATE learned_skill_patches SET status = 'rejected' WHERE patch_id = ?", (patch_id,))
            await conn.commit()
        return True

    async def rollback_learned_skill(self, skill_id: str, rollback_version: int) -> dict[str, Any]:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT * FROM learned_skills WHERE skill_id = ?', (skill_id,))
            current_row = await cursor.fetchone()
            cursor = await conn.execute('\n            SELECT skill_definition\n            FROM learned_skill_versions\n            WHERE skill_id = ? AND version = ?\n            ', (skill_id, int(rollback_version)))
            version_row = await cursor.fetchone()
            if current_row is None or version_row is None:
                return {
                    'ok': False,
                    'code': 'skill_version_not_found',
                    'error': localized(
                        'Skill or target version not found.',
                        '未找到技能或目标版本。',
                    ),
                }
            definition = self.ports.json_loads(version_row['skill_definition'], {})
            if not isinstance(definition, dict):
                return {
                    'ok': False,
                    'code': 'skill_version_invalid',
                    'error': localized(
                        'The stored skill version is invalid.',
                        '存储的技能版本无效。',
                    ),
                }
            definition['status'] = str(definition.get('status') or 'active')
            definition['created_at'] = str(current_row['created_at'] or definition.get('created_at') or self.ports.now_iso())
            definition['run_statistics'] = self.ports.json_loads(current_row['run_statistics_json'], self.ports.default_skill_stats())
            sanitized = await self._sanitize_skill_definition(definition)
            persisted = await self._persist_skill_version(
                conn,
                skill_id=skill_id,
                current_row=current_row,
                definition=sanitized,
                change_type='rollback',
                change_summary=localized(
                    'Rolled back skill to version {version}.',
                    '已将技能回滚到版本 {version}。',
                    version=rollback_version,
                ),
                rollback_target=int(rollback_version),
            )
            await conn.commit()
        return {'ok': True, 'skill': persisted, 'rollback_target': int(rollback_version)}
