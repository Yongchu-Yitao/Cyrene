"""Project bundle and task-session repository."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.workbench.persistence.document_merge import (
    baseline,
    entity_id as _entity_id,
    plain as _plain,
    three_way_merge as _three_way_merge,
    tracked as _tracked,
)
from cyrene.workbench.persistence.schema import connect as _connect

_TASK_SESSION_SUMMARY_FIELDS = (
    "id",
    "projectId",
    "kind",
    "title",
    "goal",
    "status",
    "priority",
    "createdAt",
    "updatedAt",
    "summary",
    "titleLocked",
)


@dataclass(frozen=True, slots=True)
class ProjectPorts:
    document_write_lock: Any
    load_row: Any
    write_row: Any


class ProjectRepository:
    def __init__(self, ports: ProjectPorts):
        self.ports = ports

    def _load_task_session_rows(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        rows = conn.execute('SELECT session_id, payload_json FROM workbench_task_sessions').fetchall()
        result: dict[str, dict[str, Any]] = {}
        for session_id, payload_json in rows:
            try:
                payload = json.loads(str(payload_json))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f'invalid Workbench task-session payload for {session_id}') from exc
            if isinstance(payload, dict):
                result[str(session_id)] = payload
        return result

    def _write_task_session_row(self, conn: sqlite3.Connection, session: dict[str, Any]) -> None:
        session_id = _entity_id(session)
        if not session_id:
            raise ValueError('Workbench task session is missing id')
        now = datetime.now(timezone.utc).isoformat()
        conn.execute('\n        INSERT INTO workbench_task_sessions(\n            session_id, project_id, payload_json, updated_at\n        ) VALUES (?, ?, ?, ?)\n        ON CONFLICT(session_id) DO UPDATE SET\n            project_id = excluded.project_id,\n            payload_json = excluded.payload_json,\n            updated_at = excluded.updated_at\n        ', (session_id, str(session.get('projectId') or ''), json.dumps(_plain(session), ensure_ascii=False), now))

    def summarize_task_session(self, session: dict[str, Any]) -> dict[str, Any]:
        """Project-index projection for one independently stored task session."""
        summary = {field: _plain(session.get(field)) for field in _TASK_SESSION_SUMMARY_FIELDS if field in session}
        summary['id'] = str(summary.get('id') or session.get('id') or '')
        summary['projectId'] = str(summary.get('projectId') or session.get('projectId') or '')
        summary['isSummary'] = True
        plan = session.get('plan') if isinstance(session.get('plan'), list) else []
        summary['planStepCount'] = len(plan)
        resolved_statuses = {'completed', 'done', 'skipped'}
        summary['planCompletedCount'] = sum((1 for step in plan if isinstance(step, dict) and str(step.get('status') or 'pending') in resolved_statuses))
        current_step: dict[str, Any] | None = next((step for step in plan if isinstance(step, dict) and str(step.get('status') or 'pending') == 'running'), None)
        if current_step is None:
            current_step = next((step for step in plan if isinstance(step, dict) and str(step.get('status') or 'pending') not in resolved_statuses), None)
        if current_step is not None:
            summary['planCurrentIndex'] = plan.index(current_step) + 1
            summary['planCurrentTitle'] = str(current_step.get('title') or '')
            summary['planCurrentAction'] = str(current_step.get('currentAction') or '')
        summary['eventCount'] = len(session.get('events') or []) if isinstance(session.get('events'), list) else 0
        summary['runCount'] = len(session.get('runs') or []) if isinstance(session.get('runs'), list) else 0
        summary['artifactCount'] = len(session.get('artifacts') or []) if isinstance(session.get('artifacts'), list) else 0
        return summary

    def _split_project_bundle(self, payload: dict[str, Any], summarize_session: Callable[[dict[str, Any]], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """Return the lightweight project index and independently stored sessions."""
        shell = _plain(payload)
        sessions: dict[str, dict[str, Any]] = {}
        shell_projects: list[dict[str, Any]] = []
        for raw_project in payload.get('projects') or []:
            if not isinstance(raw_project, dict):
                continue
            project = _plain(raw_project)
            summaries: list[dict[str, Any]] = []
            for raw_session in raw_project.get('sessions') or []:
                if not isinstance(raw_session, dict):
                    continue
                session = _plain(raw_session)
                session_id = _entity_id(session)
                if not session_id:
                    continue
                sessions[session_id] = session
                summaries.append(_plain(summarize_session(session)))
            project['sessions'] = summaries
            shell_projects.append(project)
        shell['projects'] = shell_projects
        return (shell, sessions)

    def _hydrate_project_bundle(self, shell: dict[str, Any], session_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
        payload = _plain(shell)
        projects: list[dict[str, Any]] = []
        for raw_project in shell.get('projects') or []:
            if not isinstance(raw_project, dict):
                continue
            project = _plain(raw_project)
            sessions: list[dict[str, Any]] = []
            for reference in raw_project.get('sessions') or []:
                if not isinstance(reference, dict):
                    continue
                session_id = _entity_id(reference)
                if not session_id:
                    continue
                sessions.append(_plain(session_rows.get(session_id, reference)))
            project['sessions'] = sessions
            projects.append(project)
        payload['projects'] = projects
        return payload

    def _load_project_bundle_locked(self, conn: sqlite3.Connection, default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Load the current SQLite project index and its task-session rows."""
        stored = self.ports.load_row(conn, 'projects')
        raw = stored
        if not isinstance(raw, dict) or not isinstance(raw.get('projects'), list):
            raw = default_factory()
        session_rows = self._load_task_session_rows(conn)
        full = self._hydrate_project_bundle(raw, session_rows)
        shell, sessions = self._split_project_bundle(full, summarize_session)
        for session_id, session in sessions.items():
            if session_id not in session_rows:
                self._write_task_session_row(conn, session)
        if stored != shell:
            self.ports.write_row(conn, 'projects', shell)
        return (shell, full)

    def read_project_bundle(self, db_path: str | Path, default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]], *, lightweight: bool=False) -> dict[str, Any]:
        """Read normalized Workbench projects, hydrating task sessions on demand.
    
        The ``projects`` document is a lightweight index. Complete task-session
        payloads live in ``workbench_task_sessions`` so a single run update no
        longer rewrites every task history in every project.
        """
        conn = _connect(db_path)
        try:
            stored = self.ports.load_row(conn, 'projects')
            raw = stored if isinstance(stored, dict) and isinstance(stored.get('projects'), list) else default_factory()
            session_rows = self._load_task_session_rows(conn)
            full = self._hydrate_project_bundle(raw, session_rows)
            shell, _sessions = self._split_project_bundle(full, summarize_session)
            if lightweight:
                value = _plain(shell)
                active_project_id = str(value.get('activeProjectId') or '')
                active_session_id = str(value.get('activeSessionId') or '')
                if active_project_id and active_session_id:
                    full_project = next((project for project in full.get('projects') or [] if isinstance(project, dict) and str(project.get('id') or '') == active_project_id), None)
                    full_session = next((session for session in (full_project or {}).get('sessions') or [] if isinstance(session, dict) and str(session.get('id') or '') == active_session_id), None)
                    if full_session is not None:
                        for project in value.get('projects') or []:
                            if not isinstance(project, dict) or str(project.get('id') or '') != active_project_id:
                                continue
                            project['sessions'] = [_plain(full_session) if isinstance(session, dict) and str(session.get('id') or '') == active_session_id else session for session in project.get('sessions') or []]
                            break
            else:
                value = full
            return _tracked(value, 'projects')
        finally:
            conn.close()

    def write_project_bundle(self, db_path: str | Path, value: dict[str, Any], default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]], *, base_value: dict[str, Any] | None=None) -> dict[str, Any]:
        """Merge and atomically persist a project index plus changed task rows."""
        local = _plain(value)
        base = _plain(base_value) if base_value is not None else baseline(value)
        with self.ports.document_write_lock:
            conn = _connect(db_path)
            try:
                conn.execute('BEGIN IMMEDIATE')
                _remote_shell, remote = self._load_project_bundle_locked(conn, default_factory, summarize_session)
                merged = local if base is None else _three_way_merge(base, local, remote)
                if not isinstance(merged, dict):
                    raise TypeError('Workbench projects bundle is not an object')
                shell, sessions = self._split_project_bundle(merged, summarize_session)
                remote_sessions = {session_id: session for session_id, session in self._load_task_session_rows(conn).items()}
                for session_id, session in sessions.items():
                    if remote_sessions.get(session_id) != session:
                        self._write_task_session_row(conn, session)
                removed_ids = set(remote_sessions) - set(sessions)
                if removed_ids:
                    conn.executemany('DELETE FROM workbench_task_sessions WHERE session_id = ?', [(session_id,) for session_id in sorted(removed_ids)])
                self.ports.write_row(conn, 'projects', shell)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return _tracked(merged, 'projects')

    def patch_project_bundle_fields(self, db_path: str | Path, fields: dict[str, Any], default_factory: Callable[[], dict[str, Any]], summarize_session: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Patch project-index scalars without hydrating task rows for the write."""
        updates = _plain(fields)
        with self.ports.document_write_lock:
            conn = _connect(db_path)
            try:
                conn.execute('BEGIN IMMEDIATE')
                shell, _full = self._load_project_bundle_locked(conn, default_factory, summarize_session)
                shell.update(updates)
                self.ports.write_row(conn, 'projects', shell)
                full = self._hydrate_project_bundle(shell, self._load_task_session_rows(conn))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return {name: _plain(full.get(name)) for name in updates}
