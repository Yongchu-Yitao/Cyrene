"""Chat repository, summary projection, and merge."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.workbench.persistence.document_merge import (
    MISSING as _MISSING,
    TrackedDict,
    plain as _plain,
    three_way_merge as _three_way_merge,
)
from cyrene.workbench.persistence.schema import connect as _connect

_CHAT_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)


@dataclass(frozen=True, slots=True)
class ChatPorts:
    document_write_lock: Any
    load_row: Any
    write_row: Any


class ChatRepository:
    def __init__(self, ports: ChatPorts):
        self.ports = ports

    def _chat_id(self, chat: Any) -> str:
        if not isinstance(chat, dict):
            return ''
        return str(chat.get('id') or '').strip()

    def _split_chat(self, chat: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        payload = {str(key): _plain(value) for key, value in chat.items() if key != 'messages'}
        messages = [_plain(message) for message in chat.get('messages') or [] if isinstance(message, dict)]
        payload['id'] = self._chat_id(chat)
        return (payload, messages)

    def _chat_message_summary(self, chat: dict[str, Any]) -> dict[str, Any]:
        messages = [item for item in chat.get('messages') or [] if isinstance(item, dict)]
        usage = {key: 0 for key in _CHAT_USAGE_KEYS}
        preview = ''
        first_message = ''
        first_fallback = ''
        completed_turn_count = 0
        for message in messages:
            content = str(message.get('content') or '').strip()
            if content and (not first_fallback):
                first_fallback = content.replace('\n', ' ')[:80]
            if content and (not first_message) and (str(message.get('role') or '') == 'user'):
                first_message = content.replace('\n', ' ')[:80]
            if str(message.get('role') or '') == 'assistant' and 'processingDurationMs' in message and (not bool(message.get('systemInitiated'))):
                completed_turn_count += 1
            raw_usage = message.get('usage')
            if isinstance(raw_usage, dict):
                for key in _CHAT_USAGE_KEYS:
                    try:
                        usage[key] += int(raw_usage.get(key) or 0)
                    except (TypeError, ValueError):
                        pass
        for message in reversed(messages):
            content = str(message.get('content') or '').strip()
            if content:
                preview = content.replace('\n', ' ')[:80]
                break
        if not usage['total_tokens']:
            usage['total_tokens'] = usage['prompt_tokens'] + usage['completion_tokens']
        stored_turn_count = chat.get('completedTurnCount')
        if isinstance(stored_turn_count, int) and (not isinstance(stored_turn_count, bool)):
            completed_turn_count = max(0, stored_turn_count)
        return {'messageCount': len(messages), 'preview': preview, 'firstMessage': first_message or first_fallback, 'completedTurnCount': completed_turn_count, 'usage': usage}

    def _write_chat_row(self, conn: sqlite3.Connection, chat: dict[str, Any], ordinal: int, *, write_messages: bool=True, previous_messages: list[dict[str, Any]] | None=None) -> None:
        chat_id = self._chat_id(chat)
        if not chat_id:
            raise ValueError('Workbench chat is missing id')
        payload = {str(key): _plain(value) for key, value in chat.items() if key != 'messages'}
        payload['id'] = chat_id
        messages = [_plain(message) for message in chat.get('messages') or [] if isinstance(message, dict)] if write_messages else []
        now = datetime.now(timezone.utc).isoformat()
        conn.execute('\n        INSERT INTO workbench_chats(\n            chat_id, ordinal, payload_json, summary_json, updated_at\n        ) VALUES (?, ?, ?, ?, ?)\n        ON CONFLICT(chat_id) DO UPDATE SET\n            ordinal = excluded.ordinal,\n            payload_json = excluded.payload_json,\n            summary_json = excluded.summary_json,\n            updated_at = excluded.updated_at\n        ', (chat_id, int(ordinal), json.dumps(payload, ensure_ascii=False), json.dumps(self._chat_message_summary(chat), ensure_ascii=False), now))
        if not write_messages:
            return
        prefix = 0
        if previous_messages is not None:
            limit = min(len(previous_messages), len(messages))
            while prefix < limit and previous_messages[prefix] == messages[prefix]:
                prefix += 1
        conn.execute('DELETE FROM workbench_chat_messages WHERE chat_id = ? AND ordinal >= ?', (chat_id, prefix))
        tail = messages[prefix:]
        if tail:
            conn.executemany('\n            INSERT INTO workbench_chat_messages(\n                chat_id, ordinal, message_id, payload_json\n            ) VALUES (?, ?, ?, ?)\n            ', [(chat_id, index, str(message.get('id') or message.get('message_id') or ''), json.dumps(message, ensure_ascii=False)) for index, message in enumerate(tail, start=prefix)])

    def _load_chat_rows(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        chat_rows = conn.execute('SELECT chat_id, payload_json FROM workbench_chats ORDER BY ordinal, chat_id').fetchall()
        messages: dict[str, list[dict[str, Any]]] = {}
        for chat_id, payload_json in conn.execute('\n        SELECT chat_id, payload_json\n        FROM workbench_chat_messages\n        ORDER BY chat_id, ordinal\n        ').fetchall():
            try:
                message = json.loads(str(payload_json))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f'invalid Workbench chat message payload for {chat_id}') from exc
            if isinstance(message, dict):
                messages.setdefault(str(chat_id), []).append(message)
        result: dict[str, dict[str, Any]] = {}
        for chat_id, payload_json in chat_rows:
            try:
                chat = json.loads(str(payload_json))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f'invalid Workbench chat payload for {chat_id}') from exc
            if not isinstance(chat, dict):
                continue
            normalized_id = str(chat_id)
            chat['id'] = normalized_id
            chat['messages'] = messages.get(normalized_id, [])
            result[normalized_id] = chat
        return result

    def _load_chat_row(self, conn: sqlite3.Connection, chat_id: str) -> dict[str, Any] | None:
        row = conn.execute('SELECT payload_json FROM workbench_chats WHERE chat_id = ?', (chat_id,)).fetchone()
        if row is None:
            return None
        chat = json.loads(str(row[0]))
        if not isinstance(chat, dict):
            return None
        chat['id'] = chat_id
        chat['messages'] = [message for payload_json, in conn.execute('\n            SELECT payload_json FROM workbench_chat_messages\n            WHERE chat_id = ? ORDER BY ordinal\n            ', (chat_id,)).fetchall() if isinstance((message := json.loads(str(payload_json))), dict)]
        return chat

    def _chat_versions(self, conn: sqlite3.Connection) -> dict[str, str]:
        return {str(chat_id): str(updated_at) for chat_id, updated_at in conn.execute('SELECT chat_id, updated_at FROM workbench_chats').fetchall()}

    def _chat_shell(self, ids: list[str], metadata: dict[str, Any] | None=None) -> dict[str, Any]:
        shell = {str(key): _plain(value) for key, value in (metadata or {}).items() if key not in {'chats', 'chatIds', 'normalizedVersion'}}
        shell.update({'normalizedVersion': 1, 'chatIds': list(ids)})
        return shell

    def _load_chat_bundle_locked(
        self,
        conn: sqlite3.Connection,
        *,
        write_shell: bool,
    ) -> tuple[dict[str, Any], bool]:
        stored = self.ports.load_row(conn, 'chats')
        rows = self._load_chat_rows(conn)
        ids = [str(chat_id) for chat_id in (stored or {}).get('chatIds') or [] if str(chat_id) in rows]
        ids.extend((chat_id for chat_id in rows if chat_id not in ids))
        expected_shell = self._chat_shell(ids, stored if isinstance(stored, dict) else None)
        shell_update_required = stored != expected_shell
        if shell_update_required and write_shell:
            self.ports.write_row(conn, 'chats', expected_shell)
        value = {str(key): _plain(item) for key, item in expected_shell.items() if key not in {'chatIds', 'normalizedVersion'}}
        value['chats'] = [rows[chat_id] for chat_id in ids]
        return (value, shell_update_required)

    def _tracked_bundle(self, value: dict[str, Any], key: str, *, versions: dict[str, str] | None=None) -> TrackedDict:
        """Track a hydrated normalized bundle with one defensive baseline copy."""
        out = TrackedDict(value)
        out._workbench_base = _plain(value)
        out._workbench_key = key
        if versions is not None:
            out._workbench_versions = dict(versions)
        return out

    def read_chat_bundle(self, db_path: str | Path, default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Hydrate the public ``{"chats": [...]}`` shape from row storage."""
        conn = _connect(db_path)
        try:
            conn.execute('BEGIN')
            value, shell_update_required = self._load_chat_bundle_locked(
                conn,
                write_shell=False,
            )
            conn.commit()
            if shell_update_required:
                conn.execute('BEGIN IMMEDIATE')
                value, _ = self._load_chat_bundle_locked(
                    conn,
                    write_shell=True,
                )
                conn.commit()
            versions = self._chat_versions(conn)
            return self._tracked_bundle(value, 'chats', versions=versions)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def read_chat(self, db_path: str | Path, chat_id: str, default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        """Read one normalized chat without decoding every conversation."""
        target = str(chat_id or '').strip()
        if not target:
            return None
        conn = _connect(db_path)
        try:
            row = conn.execute('SELECT payload_json FROM workbench_chats WHERE chat_id = ?', (target,)).fetchone()
            if row is None:
                return None
            chat = json.loads(str(row[0]))
            if not isinstance(chat, dict):
                return None
            chat['id'] = target
            messages: list[dict[str, Any]] = []
            for payload_json, in conn.execute('\n            SELECT payload_json FROM workbench_chat_messages\n            WHERE chat_id = ? ORDER BY ordinal\n            ', (target,)).fetchall():
                message = json.loads(str(payload_json))
                if isinstance(message, dict):
                    messages.append(message)
            chat['messages'] = messages
            return chat
        finally:
            conn.close()

    def read_chat_summaries(self, db_path: str | Path, default_factory: Callable[[], dict[str, Any]]) -> list[dict[str, Any]]:
        """Read chat-list projections without decoding transcript rows."""
        conn = _connect(db_path)
        try:
            rows = conn.execute('\n            SELECT chat_id, payload_json, summary_json\n            FROM workbench_chats ORDER BY ordinal, chat_id\n            ').fetchall()
            result: list[dict[str, Any]] = []
            missing: list[tuple[str, dict[str, Any]]] = []
            for chat_id, payload_json, summary_json in rows:
                chat = json.loads(str(payload_json))
                if not isinstance(chat, dict):
                    continue
                chat['id'] = str(chat_id)
                try:
                    summary = json.loads(str(summary_json or '{}'))
                except (TypeError, ValueError, json.JSONDecodeError):
                    summary = {}
                if not isinstance(summary, dict) or 'messageCount' not in summary:
                    full_chat = self._load_chat_row(conn, str(chat_id))
                    summary = self._chat_message_summary(full_chat or chat)
                    missing.append((str(chat_id), summary))
                chat['_messageProjection'] = summary
                result.append(chat)
            if missing:
                conn.execute('BEGIN IMMEDIATE')
                conn.executemany('UPDATE workbench_chats SET summary_json = ? WHERE chat_id = ?', [(json.dumps(summary, ensure_ascii=False), chat_id) for chat_id, summary in missing])
                conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mutate_chat(self, db_path: str | Path, chat_id: str, mutation: Callable[[dict[str, Any]], Any], default_factory: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        """Mutate one chat atomically without hydrating sibling transcripts."""
        target = str(chat_id or '').strip()
        if not target:
            return None
        with self.ports.document_write_lock:
            conn = _connect(db_path)
            try:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT ordinal FROM workbench_chats WHERE chat_id = ?', (target,)).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                current = self._load_chat_row(conn, target)
                if current is None:
                    conn.rollback()
                    return None
                before_messages = [_plain(item) for item in current.get('messages') or [] if isinstance(item, dict)]
                changed = mutation(current)
                if changed is False:
                    conn.rollback()
                    return current
                if self._chat_id(current) != target:
                    raise ValueError('Workbench chat mutation cannot change id')
                self._write_chat_row(conn, current, int(row[0]), write_messages=before_messages != current.get('messages'), previous_messages=before_messages)
                stored = self.ports.load_row(conn, 'chats') or {}
                ids = [
                    str(item[0])
                    for item in conn.execute(
                        'SELECT chat_id FROM workbench_chats ORDER BY ordinal, chat_id'
                    ).fetchall()
                ]
                self.ports.write_row(conn, 'chats', self._chat_shell(ids, stored))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return current

    def write_chat(self, db_path: str | Path, chat: dict[str, Any], default_factory: Callable[[], dict[str, Any]], *, base_chat: dict[str, Any] | None=None) -> dict[str, Any] | None:
        """Three-way merge and persist one chat without loading its siblings."""
        target = self._chat_id(chat)
        if not target:
            return None
        local = _plain(chat)
        base = _plain(base_chat) if isinstance(base_chat, dict) else None
    
        def merge_into(current: dict[str, Any]) -> None:
            merged = local if base is None else _three_way_merge(base, local, current, ('chats', target))
            if not isinstance(merged, dict):
                raise ValueError('Workbench chat merge produced an invalid value')
            current.clear()
            current.update(merged)
        return self.mutate_chat(db_path, target, merge_into, default_factory)

    def _merge_chat_lists(self, base: list[Any], local: list[Any], remote: list[Any]) -> list[dict[str, Any]]:
        base_by = {self._chat_id(chat): chat for chat in base if self._chat_id(chat)}
        local_by = {self._chat_id(chat): chat for chat in local if self._chat_id(chat)}
        remote_by = {self._chat_id(chat): chat for chat in remote if self._chat_id(chat)}
        order: list[str] = []
        for source in (local, remote):
            for chat in source:
                chat_id = self._chat_id(chat)
                if chat_id and chat_id not in order:
                    order.append(chat_id)
        merged: list[dict[str, Any]] = []
        for chat_id in order:
            base_chat = base_by.get(chat_id, _MISSING)
            local_chat = local_by.get(chat_id, _MISSING)
            remote_chat = remote_by.get(chat_id, _MISSING)
            value = _three_way_merge(base_chat, local_chat, remote_chat, ('chats', chat_id))
            if isinstance(value, dict):
                merged.append(value)
        return merged

    def write_chat_bundle(self, db_path: str | Path, value: dict[str, Any], default_factory: Callable[[], dict[str, Any]], *, base_value: dict[str, Any] | None=None) -> dict[str, Any]:
        """Merge chats by id and persist only rows whose content or order changed."""
        local = value if isinstance(value, dict) else default_factory()
        inherited_base = getattr(value, '_workbench_base', None)
        inherited_versions = getattr(value, '_workbench_versions', None)
        base = base_value if base_value is not None else inherited_base
        with self.ports.document_write_lock:
            conn = _connect(db_path)
            try:
                conn.execute('BEGIN IMMEDIATE')
                if isinstance(inherited_versions, dict):
                    stored = self.ports.load_row(conn, 'chats')
                    current_versions = self._chat_versions(conn)
                    base_by = {self._chat_id(chat): chat for chat in (base or {}).get('chats') or [] if self._chat_id(chat)}
                    row_ids = [str(row[0]) for row in conn.execute('SELECT chat_id FROM workbench_chats ORDER BY ordinal, chat_id').fetchall()]
                    ordered_ids = [str(chat_id) for chat_id in (stored or {}).get('chatIds') or [] if str(chat_id) in current_versions]
                    ordered_ids.extend((chat_id for chat_id in row_ids if chat_id not in ordered_ids))
                    remote_chats: list[dict[str, Any]] = []
                    for chat_id in ordered_ids:
                        if inherited_versions.get(chat_id) == current_versions.get(chat_id) and chat_id in base_by:
                            remote_chats.append(base_by[chat_id])
                        else:
                            remote_chat = self._load_chat_row(conn, chat_id)
                            if remote_chat is not None:
                                remote_chats.append(remote_chat)
                    remote = {str(key): _plain(item) for key, item in (stored or {}).items() if key not in {'chatIds', 'normalizedVersion'}}
                    remote['chats'] = remote_chats
                else:
                    remote, _ = self._load_chat_bundle_locked(
                        conn,
                        write_shell=True,
                    )
                if not isinstance(base, dict):
                    merged = {'chats': [_plain(chat) for chat in local.get('chats') or []]}
                else:
                    merged_meta = _three_way_merge({key: item for key, item in base.items() if key != 'chats'}, {key: item for key, item in local.items() if key != 'chats'}, {key: item for key, item in remote.items() if key != 'chats'})
                    merged = dict(merged_meta) if isinstance(merged_meta, dict) else {}
                    merged['chats'] = self._merge_chat_lists(list(base.get('chats') or []), list(local.get('chats') or []), list(remote.get('chats') or []))
                remote_by = {self._chat_id(chat): chat for chat in remote.get('chats') or [] if self._chat_id(chat)}
                remote_ordinals = {self._chat_id(chat): index for index, chat in enumerate(remote.get('chats') or []) if self._chat_id(chat)}
                merged_ids: list[str] = []
                for index, chat in enumerate(merged.get('chats') or []):
                    if not isinstance(chat, dict) or not self._chat_id(chat):
                        continue
                    chat_id = self._chat_id(chat)
                    merged_ids.append(chat_id)
                    remote_chat = remote_by.get(chat_id)
                    remote_messages = remote_chat.get('messages') if isinstance(remote_chat, dict) else _MISSING
                    if remote_chat != chat or index != remote_ordinals.get(chat_id, -1):
                        self._write_chat_row(conn, chat, index, write_messages=remote_messages != chat.get('messages'), previous_messages=remote_messages if isinstance(remote_messages, list) else None)
                removed_ids = set(remote_by) - set(merged_ids)
                if removed_ids:
                    conn.executemany('DELETE FROM workbench_chat_messages WHERE chat_id = ?', [(chat_id,) for chat_id in sorted(removed_ids)])
                    conn.executemany('DELETE FROM workbench_chats WHERE chat_id = ?', [(chat_id,) for chat_id in sorted(removed_ids)])
                self.ports.write_row(conn, 'chats', self._chat_shell(merged_ids, merged))
                conn.commit()
                committed_versions = self._chat_versions(conn)
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return self._tracked_bundle(merged, 'chats', versions=committed_versions)
