"""Typed capture models for Skills-owned behavior-learning telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
import asyncio
import logging
import re
import shutil

import aiosqlite
import json
from typing import Any, Mapping

from cyrene.localization import localized

from .artifacts import structured_paths

logger = logging.getLogger(__name__)

_IMAGE_ARTIFACT_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MAX_IMAGE_ARTIFACT_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_ARTIFACTS_PER_ACTION = 8


def _truncate(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


@dataclass(frozen=True, slots=True)
class ActionCapture:
    action_id: str
    turn_id: str
    session_id: str
    round_id: str
    created_at: str
    action_type: str
    action_subtype: str
    tool_name: str
    input_summary: str
    output_summary: str
    success: int
    error_summary: str
    requires_llm: int
    metadata_json: str

    @classmethod
    def create(
        cls,
        *,
        action_id: str,
        turn_id: str,
        session_id: str,
        round_id: str,
        created_at: str,
        domain: str,
        action_type: str,
        action_subtype: str,
        tool_name: str,
        args: Mapping[str, Any],
        result: Any,
        success: bool,
        error: str,
        requires_llm: int,
        caller: str,
        duration_ms: float,
        artifacts: list[dict[str, str]] | None = None,
    ) -> ActionCapture:
        metadata = {
            "caller": str(caller or "unknown"),
            "round_id": round_id,
            "duration_ms": round(float(duration_ms or 0), 2),
            "raw_args": dict(args),
            "action_domain": domain,
            "artifacts": list(artifacts or []),
        }
        return cls(
            action_id=action_id,
            turn_id=turn_id,
            session_id=session_id,
            round_id=round_id,
            created_at=created_at,
            action_type=action_type,
            action_subtype=action_subtype,
            tool_name=tool_name,
            input_summary=_truncate(
                json.dumps(dict(args), ensure_ascii=False),
                500,
            ),
            output_summary=_truncate(result, 500),
            success=1 if success else 0,
            error_summary=_truncate(error, 400),
            requires_llm=requires_llm,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )

    def as_insert_values(self, action_index: int) -> tuple[Any, ...]:
        return (
            self.action_id,
            self.turn_id,
            self.session_id,
            self.round_id,
            self.created_at,
            action_index,
            self.action_type,
            self.action_subtype,
            self.tool_name,
            self.input_summary,
            self.output_summary,
            self.success,
            self.error_summary,
            self.requires_llm,
            self.metadata_json,
        )

_TOOL_ACTION_MAP: dict[str, tuple[str, str, str, int]] = {
    "Read": ("local_resource_operation", "read_resource", "read_file", 0),
    "Write": ("local_resource_operation", "edit_resource", "write_file", 0),
    "Edit": ("local_resource_operation", "edit_resource", "edit_file", 0),
    "Glob": ("local_resource_operation", "search_resource", "list_files", 0),
    "Grep": ("local_resource_operation", "search_resource", "search_file_content", 0),
    "Bash": ("system_operation", "run_command", "shell_command", 0),
    "WebSearch": ("external_information_query", "query_realtime_info", "search_web", 0),
    "WebFetch": ("external_information_query", "retrieve_external_knowledge", "fetch_web_page", 0),
    "AnalyzeAttachment": ("content_transformation", "parse_content", "analyze_attachment", 1),
    "spawn_subagent": ("internal_reasoning", "manage_state", "spawn_subagent", 1),
    "send_agent_message": ("communication", "send_communication", "send_agent_message", 0),
    "broadcast_agent_message": ("communication", "send_communication", "broadcast_agent_message", 0),
    "query_round": ("state_management", "observe_context", "query_round", 0),
    "recall_memory": ("state_management", "observe_context", "recall_memory", 1),
    "ask_user": ("user_interaction", "ask_clarification", "ask_user", 1),
    "schedule.create": ("schedule_management", "manage_schedule", "schedule.create", 0),
    "schedule.list": ("schedule_management", "manage_schedule", "schedule.list", 0),
    "schedule.edit": ("schedule_management", "manage_schedule", "schedule.edit", 0),
    "schedule.pause": ("schedule_management", "manage_schedule", "schedule.pause", 0),
    "schedule.resume": ("schedule_management", "manage_schedule", "schedule.resume", 0),
    "schedule.cancel": ("schedule_management", "manage_schedule", "schedule.cancel", 0),
    "StartShell": ("system_operation", "run_command", "start_shell", 0),
    "SendShell": ("system_operation", "run_command", "send_shell", 0),
    "DeleteShell": ("system_operation", "manage_state", "delete_shell", 0),
    "start_shell": ("system_operation", "run_command", "start_shell", 0),
    "send_shell": ("system_operation", "run_command", "send_shell", 0),
    "close_shell": ("system_operation", "manage_state", "close_shell", 0),
    "read_file": ("local_resource_operation", "read_resource", "read_file", 0),
    "write_file": ("local_resource_operation", "edit_resource", "write_file", 0),
    "edit_file": ("local_resource_operation", "edit_resource", "edit_file", 0),
    "list_files": ("local_resource_operation", "search_resource", "list_files", 0),
    "search_files": ("local_resource_operation", "search_resource", "search_file_content", 0),
    "run_shell": ("system_operation", "run_command", "shell_command", 0),
    "run_command": ("system_operation", "run_command", "shell_command", 0),
    "search_web": ("external_information_query", "query_realtime_info", "search_web", 0),
    "fetch_web_page": ("external_information_query", "retrieve_external_knowledge", "fetch_web_page", 0),
}

# Internal messaging tools — not useful for reusable-workflow matching


def map_tool_to_action(tool_name: str) -> tuple[str, str, str, int]:
    return _TOOL_ACTION_MAP.get(tool_name, ("state_management", "call_tool", _slug(tool_name), 0))


def _slug(value: str) -> str:
    import re
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return text or "unknown"


SENSITIVE_BROWSER_TERMS = frozenset({
    "password", "passwd", "passcode", "pwd", "otp", "one-time",
    "verification code", "验证码", "密码", "token", "secret", "api_key",
    "apikey", "api key", "access_key", "cookie", "authorization",
    "credit card", "card number", "银行卡", "cvv", "cvc",
})


def chain_item_from_action(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata_json") or {}
    return {
        **({"artifacts": metadata["artifacts"]} if "artifacts" in metadata else {}),
        "id": str(row.get("action_id") or ""), "source": "agent",
        "index": int(row.get("action_index") or 0),
        "tool": str(row.get("tool_name") or ""),
        "type": str(row.get("action_type") or ""),
        "subtype": str(row.get("action_subtype") or ""),
        "domain": str(metadata.get("action_domain") or ""),
        "args": metadata.get("raw_args") or {},
        "input_summary": str(row.get("input_summary") or ""),
        "output_summary": str(row.get("output_summary") or ""),
        "success": bool(row.get("success")),
        "duration_ms": float(metadata.get("duration_ms") or 0),
        "created_at": str(row.get("created_at") or ""),
    }


def browser_target_label(target: Mapping[str, Any]) -> str:
    if not isinstance(target, Mapping):
        return ""
    text = _truncate(target.get("text") or target.get("innerText"), 80)
    aria = _truncate(target.get("ariaLabel") or target.get("aria_label"), 80)
    name = _truncate(target.get("name"), 80)
    placeholder = _truncate(target.get("placeholder"), 80)
    element_id = _truncate(target.get("id"), 80)
    role = _truncate(target.get("role"), 40)
    tag = _truncate(target.get("tag") or target.get("tagName"), 40).lower()
    label = text or aria or placeholder or name or element_id
    prefix = role or tag
    return f"{prefix} {label!r}" if prefix and label else label or prefix


def sanitize_browser_capture(
    kind: str,
    payload: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    event_kind = str(kind or "").strip().lower()
    clean_payload = json.loads(json.dumps(dict(payload or {}), ensure_ascii=False))
    clean_target = json.loads(json.dumps(dict(target or {}), ensure_ascii=False))
    haystack = " ".join(str(clean_target.get(key) or "") for key in (
        "type", "id", "name", "role", "text", "ariaLabel", "aria_label", "placeholder"
    )).lower()
    sensitive_target = any(term in haystack for term in SENSITIVE_BROWSER_TERMS)
    sensitive_keys = {
        "password", "passwd", "passcode", "otp", "token", "secret", "cookie",
        "authorization", "card_number", "cardnumber", "cvv", "cvc",
    }
    for key in list(clean_payload):
        lowered = str(key).lower()
        if lowered in sensitive_keys or sensitive_target and lowered in {"value", "text", "query"}:
            clean_payload[key] = "[redacted]"
    if event_kind == "text" and not browser_target_label(clean_target) and clean_payload.get("text") not in (None, ""):
        clean_payload["text"] = "[redacted-unattributed-text]"
    if event_kind == "key" and not browser_target_label(clean_target):
        if len(str(clean_payload.get("key") or "")) == 1:
            clean_payload["key"] = "[text-key]"
        if str(clean_payload.get("text") or ""):
            clean_payload["text"] = "[redacted-unattributed-text]"
    return clean_payload, clean_target


def _browser_value_preview(kind: str, payload: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    target_type = str(target.get("type") or "").lower()
    target_name = str(target.get("name") or target.get("id") or "").lower()
    if "password" in target_type or "password" in target_name:
        return "[redacted]"
    for key in ("value", "text", "query", "url", "href"):
        if payload.get(key) not in (None, ""):
            value = str(payload.get(key) or "")
            return value[:117] + "..." if len(value) > 120 else value
    if kind in {"scroll", "wheel"}:
        return _truncate(
            f"x={payload.get('scrollX', payload.get('x', ''))}, "
            f"y={payload.get('scrollY', payload.get('y', ''))}", 80
        )
    return ""


def browser_event_learning_fields(
    kind: str,
    payload: Mapping[str, Any],
    target: Mapping[str, Any],
    url: str,
    title: str,
) -> dict[str, str]:
    event_kind = str(kind or "event").strip() or "event"
    target_label = browser_target_label(target)
    value_preview = _browser_value_preview(event_kind, payload, target)
    page = title or url
    if event_kind in {"input", "text"}:
        destination = target_label or "focused field"
        action = f"entered {value_preview!r} into {destination}" if value_preview else f"entered text into {destination}"
        purpose = f"provide browser input for {destination}"
    elif event_kind == "click":
        destination = target_label or "page element"
        action = f"clicked {destination}"
        purpose = f"activate {destination}"
    elif event_kind == "submit":
        destination = target_label or "browser form"
        action = f"submitted {destination}"
        purpose = f"submit {destination}"
    elif event_kind in {"navigate", "navigation"}:
        action = f"navigated to {value_preview or url or 'browser page'}"
        purpose = "open or change browser page"
    elif event_kind in {"scroll", "wheel"}:
        action = f"scrolled {page or 'browser page'}"
        purpose = "inspect more page content"
    elif event_kind in {"back", "forward", "reload", "select_tab", "close_tab"}:
        action = f"{event_kind.replace('_', ' ')} on {page or 'browser tab'}"
        purpose = "manage browser navigation state"
    else:
        destination = target_label or page or "browser page"
        action = f"performed browser user event {event_kind} on {destination}"
        purpose = "continue browser task"
    return {
        "purpose": _truncate(purpose, 240), "action_summary": _truncate(action, 300),
        "object_summary": _truncate(target_label or page or url, 240),
        "value_preview": _truncate(value_preview, 160),
    }


def chain_item_from_browser_event(row: Mapping[str, Any]) -> dict[str, Any]:
    def load(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            loaded = json.loads(str(value or "{}"))
            return loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            return {}
    payload, target = load(row.get("payload_json")), load(row.get("target_json"))
    event_kind = str(row.get("event_kind") or "event")
    url, title = str(row.get("browser_url") or ""), str(row.get("browser_title") or "")
    return {
        "id": str(row.get("event_id") or ""), "source": "user_browser",
        "index": int(row.get("event_index") or 0), "tool": "browser.user." + event_kind,
        "type": "browser_user_operation", "subtype": event_kind, "domain": "browser_operation",
        "args": {"payload": payload, "target": target, "url": url, "title": title},
        "target": target, "url": url, "title": title,
        **browser_event_learning_fields(event_kind, payload, target, url, title),
        "success": True, "created_at": str(row.get("created_at") or ""),
    }

@dataclass(frozen=True, slots=True)
class CapturePorts:
    data_dir: Any
    default_data_dir: Any
    init_done: Any
    connect: Any
    current_round_id: Any
    current_session_id: Any
    current_turn_id: Any
    ensure_tables: Any
    history_summary: Any
    json_dumps: Any
    json_loads: Any
    new_id: Any
    now_iso: Any
    project_scope_for_session: Any
    truncate_text: Any
    turn_feedback_from_message: Any


class CaptureService:
    def __init__(self, ports: CapturePorts):
        self.ports = ports

    async def _persist_image_artifacts(self, turn_id: str, value: Any) -> list[dict[str, str]]:
        """Preserve explicit image references and return a durable manifest."""
        matches = structured_paths(value)
        if not turn_id:
            return [{"path": path} for path in matches]
        artifact_root = (self.ports.data_dir or self.ports.default_data_dir) / 'behavior-media' / str(turn_id)
        replacements: dict[str, str] = {}
        for index, raw_path in enumerate(matches[:_MAX_IMAGE_ARTIFACTS_PER_ACTION]):
            source = Path(raw_path).expanduser()
            try:
                if source.suffix.lower() not in _IMAGE_ARTIFACT_EXTS or not source.is_file():
                    continue
                if source.stat().st_size > _MAX_IMAGE_ARTIFACT_BYTES:
                    continue
                artifact_root.mkdir(parents=True, exist_ok=True)
                target = artifact_root / f'{index:02d}-{uuid4().hex}{source.suffix.lower()}'
                await asyncio.to_thread(shutil.copy2, source, target)
                replacements[raw_path] = str(target.resolve())
            except OSError:
                logger.debug('Unable to preserve image artifact %s', raw_path, exc_info=True)
        return [{"path": replacements.get(path, path)} for path in matches]

    async def _project_scope_for_turn(self, turn_id: str) -> dict[str, str]:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT session_id, project_id, project_key, session_kind FROM behavior_turns WHERE turn_id = ?', (str(turn_id or ''),))
            row = await cursor.fetchone()
        if row is None:
            return {'project_id': 'global', 'project_key': 'global', 'session_kind': 'global'}
        project_id = str(row['project_id'] or '').strip()
        project_key = str(row['project_key'] or '').strip()
        session_kind = str(row['session_kind'] or '').strip()
        if project_id and project_key:
            return {'project_id': project_id, 'project_key': project_key, 'session_kind': session_kind or 'global'}
        return self.ports.project_scope_for_session(str(row['session_id'] or ''))

    async def _latest_turn_for_session_round(self, session_id: str, round_id: str='') -> str:
        sid = str(session_id or '').strip()
        rid = str(round_id or '').strip()
        if not sid:
            return ''
        async with self.ports.connect() as conn:
            if rid:
                cursor = await conn.execute('\n                SELECT turn_id FROM behavior_turns\n                WHERE session_id = ? AND round_id = ?\n                ORDER BY created_at DESC\n                LIMIT 1\n                ', (sid, rid))
            else:
                cursor = await conn.execute('\n                SELECT turn_id FROM behavior_turns\n                WHERE session_id = ?\n                ORDER BY created_at DESC\n                LIMIT 1\n                ', (sid,))
            row = await cursor.fetchone()
        return str(row['turn_id'] or '') if row is not None else ''

    async def open_turn(self, session_id: str, round_id: str) -> dict[str, str] | None:
        """Recover the durable, not-yet-finalized turn for one Plugin run."""
        sid = str(session_id or '').strip()
        rid = str(round_id or '').strip()
        if not sid or not rid:
            return None
        async with self.ports.connect() as conn:
            cursor = await conn.execute(
                '''
                SELECT turn_id, session_id, round_id
                FROM behavior_turns
                WHERE session_id = ? AND round_id = ? AND processed_status = -1
                ORDER BY created_at DESC
                LIMIT 1
                ''',
                (sid, rid),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            'turn_id': str(row['turn_id'] or ''),
            'session_id': str(row['session_id'] or ''),
            'round_id': str(row['round_id'] or ''),
        }

    async def _upsert_behavior_session(self, conn: aiosqlite.Connection, *, session_id: str, scope: dict[str, str], now: str, session_title: str, user_message: str) -> None:
        cursor = await conn.execute('SELECT session_id FROM behavior_sessions WHERE session_id = ?', (session_id,))
        if await cursor.fetchone() is None:
            await conn.execute('\n            INSERT INTO behavior_sessions\n            (session_id, project_id, project_key, session_kind, created_at,\n             updated_at, session_summary, metadata_json)\n            VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n            ', (session_id, scope['project_id'], scope['project_key'], scope['session_kind'], now, now, self.ports.truncate_text(session_title or user_message, 240), self.ports.json_dumps({'source': 'live_session', **scope})))
            return
        await conn.execute("\n        UPDATE behavior_sessions\n        SET project_id = ?, project_key = ?, session_kind = ?, updated_at = ?,\n            session_summary = COALESCE(NULLIF(?, ''), session_summary)\n        WHERE session_id = ?\n        ", (scope['project_id'], scope['project_key'], scope['session_kind'], now, self.ports.truncate_text(session_title, 240), session_id))

    async def _mark_previous_turn_corrected(self, conn: aiosqlite.Connection, session_id: str, now: str) -> None:
        cursor = await conn.execute('\n        SELECT turn_id, metadata_json FROM behavior_turns\n        WHERE session_id = ? ORDER BY created_at DESC LIMIT 1\n        ', (session_id,))
        latest_turn = await cursor.fetchone()
        if latest_turn is None:
            return
        metadata = self.ports.json_loads(latest_turn['metadata_json'], {})
        metadata['correction_feedback'] = True
        await conn.execute('\n        UPDATE behavior_turns\n        SET user_feedback = ?, metadata_json = ?, updated_at = ?\n        WHERE turn_id = ?\n        ', ('correction', self.ports.json_dumps(metadata), now, latest_turn['turn_id']))

    async def begin_turn(
        self,
        *,
        session_id: str,
        round_id: str,
        user_message: str,
        history: list[dict[str, Any]],
        session_title: str = '',
        system_initiated: bool = False,
        defer_processing: bool = False,
    ) -> dict[str, Any]:
        if not self.ports.init_done:
            await self.ports.ensure_tables()
        now = self.ports.now_iso()
        normalized_session_id = str(session_id or '').strip() or self.ports.new_id('session')
        normalized_round_id = str(round_id or '').strip() or self.ports.new_id('round')
        if defer_processing:
            existing = await self.open_turn(
                normalized_session_id,
                normalized_round_id,
            )
            if existing is not None:
                existing_turn_id = existing['turn_id']
                return {
                    'turn_id': existing_turn_id,
                    'session_id': existing['session_id'],
                    'round_id': existing['round_id'],
                    'scope': await self._project_scope_for_turn(existing_turn_id),
                    'session_token': self.ports.current_session_id.set(existing['session_id']),
                    'turn_token': self.ports.current_turn_id.set(existing_turn_id),
                    'round_token': self.ports.current_round_id.set(existing['round_id']),
                }
        scope = self.ports.project_scope_for_session(normalized_session_id)
        turn_id = self.ports.new_id('turn')
        feedback = self.ports.turn_feedback_from_message(user_message)
        context_summary = self.ports.history_summary(history)
        metadata = {'round_id': normalized_round_id, 'session_title': str(session_title or '').strip(), 'correction_feedback': False, 'round_title': '', 'system_initiated': bool(system_initiated)}
        async with self.ports.connect() as conn:
            await self._upsert_behavior_session(conn, session_id=normalized_session_id, scope=scope, now=now, session_title=session_title, user_message=user_message)
            if defer_processing:
                # One Agent session runs one turn at a time.  An older open
                # marker therefore represents an interrupted process that did
                # not get a Stop Hook before this new run began.
                await conn.execute(
                    '''
                    UPDATE behavior_turns
                    SET updated_at = ?, outcome_status = 'cancelled',
                        processed_status = 1
                    WHERE session_id = ? AND processed_status = -1
                    ''',
                    (now, normalized_session_id),
                )
            if feedback:
                await self._mark_previous_turn_corrected(conn, normalized_session_id, now)
            await conn.execute("\n            INSERT INTO behavior_turns\n            (turn_id, session_id, project_id, project_key, session_kind, round_id, created_at, updated_at, user_message, context_summary,\n             agent_response, outcome_status, user_feedback, processed_status, metadata_json)\n            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'success', '', ?, ?)\n            ", (turn_id, normalized_session_id, scope['project_id'], scope['project_key'], scope['session_kind'], normalized_round_id, now, now, str(user_message or ''), context_summary, -1 if defer_processing else 0, self.ports.json_dumps({**metadata, **scope})))
            await conn.commit()
        session_token = self.ports.current_session_id.set(normalized_session_id)
        turn_token = self.ports.current_turn_id.set(turn_id)
        round_token = self.ports.current_round_id.set(normalized_round_id)
        return {'turn_id': turn_id, 'session_id': normalized_session_id, 'round_id': normalized_round_id, 'scope': scope, 'session_token': session_token, 'turn_token': turn_token, 'round_token': round_token}

    def clear_turn_context(self, context: dict[str, Any]) -> None:
        try:
            self.ports.current_session_id.reset(context['session_token'])
            self.ports.current_turn_id.reset(context['turn_token'])
            self.ports.current_round_id.reset(context['round_token'])
        except Exception:
            logger.debug('Failed to reset behavior context', exc_info=True)

    def current_turn_id(self) -> str:
        return self.ports.current_turn_id.get()

    async def _rebuild_tool_chain_for_turn(self, turn_id: str) -> dict[str, Any] | None:
        tid = str(turn_id or '').strip()
        if not tid:
            return None
        async with self.ports.connect() as conn:
            cursor = await conn.execute('\n            SELECT turn_id, session_id, project_id, project_key, session_kind, round_id, created_at\n            FROM behavior_turns\n            WHERE turn_id = ?\n            ', (tid,))
            turn_row = await cursor.fetchone()
            if turn_row is None:
                return None
            cursor = await conn.execute('\n            SELECT *\n            FROM behavior_actions\n            WHERE turn_id = ?\n            ORDER BY action_index ASC\n            ', (tid,))
            action_rows = [dict(row) for row in await cursor.fetchall()]
            cursor = await conn.execute('\n            SELECT *\n            FROM behavior_browser_user_events\n            WHERE turn_id = ?\n            ORDER BY event_index ASC, created_at ASC\n            ', (tid,))
            browser_rows = [dict(row) for row in await cursor.fetchall()]
            now = self.ports.now_iso()
            actions = []
            for row in action_rows:
                row['metadata_json'] = self.ports.json_loads(row.get('metadata_json'), {})
                actions.append(chain_item_from_action(row))
            browser_events = [chain_item_from_browser_event(row) for row in browser_rows]
            chain = sorted([*actions, *browser_events], key=lambda item: (str(item.get('created_at') or ''), str(item.get('source') or ''), int(item.get('index') or 0)))
            sources = sorted({str(item.get('source') or '') for item in chain if item.get('source')})
            summary = {'total_steps': len(chain), 'agent_steps': len(actions), 'browser_user_steps': len(browser_events), 'sources': sources, 'success_steps': sum((1 for item in chain if item.get('success'))), 'failed_steps': sum((1 for item in chain if not item.get('success'))), 'tool_names': [str(item.get('tool') or '') for item in chain]}
            source = 'mixed' if len(sources) > 1 else sources[0] if sources else 'agent'
            await conn.execute('\n            INSERT INTO behavior_turn_tool_chains\n            (chain_id, project_id, project_key, session_id, session_kind, turn_id, round_id, source,\n             chain_json, summary_json, created_at, updated_at)\n            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n            ON CONFLICT(turn_id) DO UPDATE SET\n                project_id = excluded.project_id,\n                project_key = excluded.project_key,\n                session_id = excluded.session_id,\n                session_kind = excluded.session_kind,\n                round_id = excluded.round_id,\n                source = excluded.source,\n                chain_json = excluded.chain_json,\n                summary_json = excluded.summary_json,\n                updated_at = excluded.updated_at\n            ', ('chain:' + tid, str(turn_row['project_id'] or ''), str(turn_row['project_key'] or ''), str(turn_row['session_id'] or ''), str(turn_row['session_kind'] or ''), tid, str(turn_row['round_id'] or ''), source, self.ports.json_dumps(chain), self.ports.json_dumps(summary), str(turn_row['created_at'] or now), now))
            await conn.commit()
        return {'chain': chain, 'summary': summary}

    def _map_tool_to_action(self, tool_name: str) -> tuple[str, str, str, int]:
        return map_tool_to_action(tool_name)

    async def record_action(
        self,
        tool_name: str,
        args: dict[str, Any],
        caller: str,
        round_id: str,
        duration_ms: float,
        *,
        result: Any = '',
        success: bool = True,
        error: str = '',
        session_id: str = '',
        turn_id: str = '',
    ) -> None:
        """Persist one completed Plugin action.

        Agent lifecycle Hooks run on a dedicated worker and therefore cannot
        rely on context variables set by a previous Hook task.  Explicit
        ``session_id``/``turn_id`` values are the canonical Plugin boundary;
        the context-variable fallback remains useful for direct, same-task
        capture such as browser takeover events.
        """
        session_id = str(session_id or self.ports.current_session_id.get()).strip()
        turn_id = str(turn_id or self.ports.current_turn_id.get()).strip()
        if not session_id or not turn_id:
            return
        now = self.ports.now_iso()
        action_id = self.ports.new_id('action')
        domain, action_type, action_subtype, requires_llm = self._map_tool_to_action(tool_name)
        artifacts = await self._persist_image_artifacts(turn_id, result)
        result_paths = set(structured_paths(result))
        artifacts.extend({"path": path} for path in structured_paths(args) if path not in result_paths)
        capture = ActionCapture.create(action_id=action_id, turn_id=turn_id, session_id=session_id, round_id=str(round_id or self.ports.current_round_id.get()), created_at=now, domain=domain, action_type=action_type, action_subtype=action_subtype, tool_name=tool_name, args=args or {}, result=result, artifacts=artifacts, success=success, error=error, requires_llm=requires_llm, caller=caller, duration_ms=duration_ms)
        try:
            async with self.ports.connect() as conn:
                cursor = await conn.execute('SELECT COALESCE(MAX(action_index), -1) AS max_idx FROM behavior_actions WHERE turn_id = ?', (turn_id,))
                row = await cursor.fetchone()
                max_idx = row['max_idx'] if row is not None else None
                next_index = int(max_idx) + 1 if max_idx is not None else 0
                await conn.execute("\n                INSERT INTO behavior_actions\n                (action_id, turn_id, session_id, round_id, created_at, action_index, action_type, action_subtype,\n                 tool_name, input_summary, output_summary, success, error_summary, requires_llm, risk_level, metadata_json)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', ?)\n                ", capture.as_insert_values(next_index))
                await conn.commit()
        except Exception:
            logger.debug('record_action telemetry write failed (ignored)', exc_info=True)

    async def record_browser_user_event(self, *, session_id: str='', round_id: str='', event_kind: str, payload: dict[str, Any] | None=None, browser_url: str='', browser_title: str='', target: dict[str, Any] | None=None) -> None:
        clean_payload, clean_target = sanitize_browser_capture(event_kind, payload, target)
        sid = str(session_id or self.ports.current_session_id.get() or '').strip()
        rid = str(round_id or self.ports.current_round_id.get() or '').strip()
        if not sid:
            sid = self.ports.new_id('browser_session')
        turn_id = await self._latest_turn_for_session_round(sid, rid)
        if not turn_id:
            ctx = await self.begin_turn(
                session_id=sid,
                round_id=rid or self.ports.new_id('browser_round'),
                user_message=localized(
                    'The user took control of the built-in browser and performed an action.',
                    '用户接管内置浏览器并执行操作。',
                ),
                history=[],
                session_title=localized(
                    'Browser user operation',
                    '浏览器用户操作',
                ),
            )
            turn_id = str(ctx['turn_id'])
            self.clear_turn_context(ctx)
        scope = await self._project_scope_for_turn(turn_id)
        now = self.ports.now_iso()
        try:
            async with self.ports.connect() as conn:
                cursor = await conn.execute('SELECT COALESCE(MAX(event_index), -1) AS max_idx FROM behavior_browser_user_events WHERE turn_id = ?', (turn_id,))
                row = await cursor.fetchone()
                max_idx = row['max_idx'] if row is not None else None
                next_index = int(max_idx) + 1 if max_idx is not None else 0
                await conn.execute('\n                INSERT INTO behavior_browser_user_events\n                (event_id, project_id, project_key, session_id, session_kind, turn_id, round_id, created_at,\n                 event_index, event_kind, browser_url, browser_title, target_json, payload_json)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ', (self.ports.new_id('browser_event'), scope['project_id'], scope['project_key'], sid, scope['session_kind'], turn_id, rid, now, next_index, str(event_kind or 'event'), self.ports.truncate_text(browser_url, 500), self.ports.truncate_text(browser_title, 240), self.ports.json_dumps(clean_target), self.ports.json_dumps(clean_payload)))
                await conn.execute('UPDATE behavior_turns SET updated_at = ?, processed_status = 0 WHERE turn_id = ?', (now, turn_id))
                await conn.commit()
        except Exception:
            logger.debug('browser user event learning write failed (ignored)', exc_info=True)
        if str(event_kind or '').strip().lower() == 'control_stop':
            await self._rebuild_tool_chain_for_turn(turn_id)

    async def list_recent_browser_user_events(self, *, session_id: str='', round_id: str='', limit: int=30) -> list[dict[str, Any]]:
        sid = str(session_id or self.ports.current_session_id.get() or '').strip()
        rid = str(round_id or '').strip()
        capped_limit = max(1, min(int(limit or 30), 100))
        if not sid:
            return []
        async with self.ports.connect() as conn:
            if rid:
                cursor = await conn.execute('\n                SELECT *\n                FROM behavior_browser_user_events\n                WHERE session_id = ? AND round_id = ?\n                ORDER BY created_at DESC, event_index DESC\n                LIMIT ?\n                ', (sid, rid, capped_limit))
            else:
                cursor = await conn.execute('\n                SELECT *\n                FROM behavior_browser_user_events\n                WHERE session_id = ?\n                ORDER BY created_at DESC, event_index DESC\n                LIMIT ?\n                ', (sid, capped_limit))
            rows = await cursor.fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            kind = str(item.get('event_kind') or '')
            url = str(item.get('browser_url') or '')
            title = str(item.get('browser_title') or '')
            target = self.ports.json_loads(item.get('target_json'), {})
            payload = self.ports.json_loads(item.get('payload_json'), {})
            learning_fields = browser_event_learning_fields(kind, payload, target, url, title)
            events.append({'id': str(item.get('event_id') or ''), 'session_id': str(item.get('session_id') or ''), 'round_id': str(item.get('round_id') or ''), 'turn_id': str(item.get('turn_id') or ''), 'created_at': str(item.get('created_at') or ''), 'index': int(item.get('event_index') or 0), 'kind': kind, 'tool': 'browser.user.' + (kind or 'event'), 'url': url, 'title': title, 'target': target, 'payload': payload, **learning_fields})
        events.reverse()
        return events

    async def _classify_turn_outcome(self, turn_id: str) -> str:
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT success FROM behavior_actions WHERE turn_id = ?', (turn_id,))
            rows = await cursor.fetchall()
            if not rows:
                return 'success'
            success_count = sum((1 for row in rows if int(row['success'] or 0) == 1))
            failure_count = len(rows) - success_count
            if failure_count == 0:
                return 'success'
            if success_count == 0:
                return 'failure'
            return 'partial_success'

    async def complete_turn(self, *, turn_id: str, assistant_response: str, session_title: str='', round_title: str='') -> None:
        # Plugin PostToolUse Hooks persist each action before returning, so
        # SessionEnd is already the ordering barrier.  There is deliberately
        # no detached legacy executor queue to flush here.
        now = self.ports.now_iso()
        outcome = await self._classify_turn_outcome(turn_id)
        async with self.ports.connect() as conn:
            cursor = await conn.execute('SELECT metadata_json, session_id FROM behavior_turns WHERE turn_id = ?', (turn_id,))
            row = await cursor.fetchone()
            if row is None:
                return
            metadata = self.ports.json_loads(row['metadata_json'], {})
            metadata['assistant_preview'] = self.ports.truncate_text(assistant_response, 240)
            if session_title:
                metadata['session_title'] = session_title
            if round_title:
                metadata['round_title'] = round_title
            await conn.execute('\n            UPDATE behavior_turns\n            SET updated_at = ?, outcome_status = ?, agent_response = ?,\n                processed_status = 0, metadata_json = ?\n            WHERE turn_id = ?\n            ', (now, outcome, str(assistant_response or ''), self.ports.json_dumps(metadata), turn_id))
            if session_title:
                await conn.execute('\n                UPDATE behavior_sessions\n                SET updated_at = ?, session_summary = ?\n                WHERE session_id = ?\n                ', (now, self.ports.truncate_text(session_title, 240), row['session_id']))
            await conn.commit()
        await self._rebuild_tool_chain_for_turn(turn_id)

    async def abort_turn(self, *, turn_id: str, reason: str = '') -> None:
        """Close an interrupted Plugin turn without making it learnable."""
        tid = str(turn_id or '').strip()
        if not tid:
            return
        now = self.ports.now_iso()
        async with self.ports.connect() as conn:
            cursor = await conn.execute(
                'SELECT metadata_json FROM behavior_turns WHERE turn_id = ?',
                (tid,),
            )
            row = await cursor.fetchone()
            if row is None:
                return
            metadata = self.ports.json_loads(row['metadata_json'], {})
            metadata['interrupted'] = True
            metadata['interruption_reason'] = self.ports.truncate_text(reason, 240)
            await conn.execute(
                '''
                UPDATE behavior_turns
                SET updated_at = ?, outcome_status = 'cancelled',
                    processed_status = 1, metadata_json = ?
                WHERE turn_id = ?
                ''',
                (now, self.ports.json_dumps(metadata), tid),
            )
            await conn.commit()
        await self._rebuild_tool_chain_for_turn(tid)
