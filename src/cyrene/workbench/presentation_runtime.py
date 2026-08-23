"""Workbench search and presentation-model builders."""

from __future__ import annotations

import asyncio
import getpass
import importlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cyrene.agent import clear_session_id, get_live_rounds, interrupt_active_run
from cyrene.config import (
    ASSISTANT_NAME,
    BASE_DIR,
    DATA_DIR,
    DB_PATH,
    SEARXNG_HOST,
    SEARXNG_PORT,
    SOUL_PATH,
    STATE_FILE,
    WORKSPACE_DIR,
)
from cyrene.observability import debug
from cyrene.runtime.memory.conversations import (
    CONVERSATIONS_DIR,
    parse_archive_meta,
    split_archive_entry_blocks,
    upsert_archive_session_title,
)
from cyrene.runtime.memory.short_term import load_entries
from cyrene.runtime.memory.soul import get_soul_path, read_soul
from cyrene.runtime.onboarding import get_onboarding_status
from cyrene.runtime.settings_store import get_all as get_web_settings
from cyrene.runtime.version import get_version_label
from cyrene.workbench import (
    memory as workbench_memory,
    project_repository,
    project_runtime,
    session_metrics,
    session_view,
)
from cyrene.workbench.session_view import count_tool_calls as _count_tool_calls

logger = logging.getLogger(__name__)
_SERVER_STARTED_AT = time.time()

def _normalize_search_text(text: Any) -> str:
    return re.sub('\\s+', ' ', str(text or '').lower()).strip()

def _search_matches(query: str, text: str) -> bool:
    """Case-insensitive, whitespace-normalized substring match.

    Also supports a tiny space-removed fallback so "helloworld" matches
    "hello world".
    """
    if not query or not text:
        return False
    haystack = _normalize_search_text(text)
    needle = _normalize_search_text(query)
    if not needle:
        return False
    if needle in haystack:
        return True
    if needle.replace(' ', '') and needle.replace(' ', '') in haystack.replace(' ', ''):
        return True
    return False

def _search_snippet(text: str, query: str, length: int=140) -> str:
    """Return a short snippet centered on the first match."""
    raw = str(text or '').strip()
    if not raw:
        return ''
    q = str(query or '').strip()
    if not q:
        return raw[:length] + ('…' if len(raw) > length else '')
    idx = raw.lower().find(q.lower())
    if idx < 0:
        try:
            pattern = re.compile(re.sub('\\s+', '\\\\s+', re.escape(q)), re.IGNORECASE)
            match = pattern.search(raw)
            if match:
                idx = match.start()
        except re.error:
            pass
    if idx < 0:
        return raw[:length] + ('…' if len(raw) > length else '')
    start = max(0, idx - length // 2)
    end = min(len(raw), start + length)
    snippet = raw[start:end]
    prefix = '…' if start > 0 else ''
    suffix = '…' if end < len(raw) else ''
    return prefix + snippet + suffix

async def _search_workbench_items(query: str, types: set[str], per_type_limit: int) -> dict[str, list[dict[str, Any]]]:
    """Search across Workbench data sources and return grouped results."""
    groups: dict[str, list[dict[str, Any]]] = {t: [] for t in types}
    if not query:
        return groups
    store = await asyncio.to_thread(project_repository._read_workbench_store_lightweight)
    projects = store.get('projects', [])
    project_by_id: dict[str, dict[str, Any]] = {str(p.get('id') or ''): p for p in projects if p.get('id')}
    project_names: dict[str, str] = {pid: str(p.get('name') or p.get('id') or '').strip() or 'Workspace' for pid, p in project_by_id.items()}
    project_kb_keys: dict[str, str] = {pid: project_runtime._workbench_project_memory_key(p) for pid, p in project_by_id.items()}
    data_key_to_project: dict[str, str] = {}
    for pid, p in project_by_id.items():
        data_key_to_project.setdefault(project_runtime._workbench_project_data_key(p), pid)
        data_key_to_project.setdefault(project_runtime._workbench_project_memory_key(p), pid)
    if 'project' in types:
        for project in projects:
            pid = str(project.get('id') or '')
            name = str(project.get('name') or '')
            desc = str(project.get('description') or '')
            summary = str((project.get('context') or {}).get('summary') or '')
            if _search_matches(query, name) or _search_matches(query, desc) or _search_matches(query, summary):
                groups['project'].append({'id': pid, 'type': 'project', 'title': name or 'Workspace', 'snippet': _search_snippet(desc or summary, query), 'projectId': pid, 'projectName': project_names.get(pid, ''), 'updatedAt': project.get('updatedAt') or project.get('createdAt') or ''})
                if len(groups['project']) >= per_type_limit:
                    break
    if 'task' in types:
        for project in projects:
            pid = str(project.get('id') or '')
            for session in project.get('sessions', []):
                sid = str(session.get('id') or '')
                title = str(session.get('title') or '')
                goal = str(session.get('goal') or '')
                if _search_matches(query, title) or _search_matches(query, goal):
                    groups['task'].append({'id': sid, 'type': 'task', 'title': title or 'New task', 'snippet': _search_snippet(goal or title, query), 'projectId': pid, 'projectName': project_names.get(pid, ''), 'sessionId': sid, 'status': session.get('status') or 'idle', 'updatedAt': session.get('updatedAt') or session.get('createdAt') or ''})
                    if len(groups['task']) >= per_type_limit:
                        break
            if len(groups['task']) >= per_type_limit:
                break
    if 'chat' in types:
        try:
            read_chats_store = importlib.import_module('cyrene.workbench.chat')._read_chats_store

            def _search_chats() -> list[dict[str, Any]]:
                found: list[dict[str, Any]] = []
                chats_payload = read_chats_store()
                for chat in chats_payload.get('chats', []):
                    if str(chat.get('kind') or 'chat') != 'chat':
                        continue
                    chat_id = str(chat.get('id') or '')
                    pid = str(chat.get('projectId') or '')
                    title = str(chat.get('title') or '')
                    preview = str(chat.get('preview') or '')
                    matched = _search_matches(query, title) or _search_matches(query, preview)
                    if not matched and isinstance(chat.get('messages'), list):
                        for message in chat['messages']:
                            if _search_matches(query, str(message.get('content') or message.get('body') or '')):
                                matched = True
                                break
                    if not matched:
                        continue
                    found.append({'id': chat_id, 'type': 'chat', 'title': title or 'New chat', 'snippet': _search_snippet(preview or title, query), 'projectId': pid, 'projectName': project_names.get(pid, 'Workspace'), 'chatId': chat_id, 'updatedAt': chat.get('updatedAt') or chat.get('createdAt') or ''})
                    if len(found) >= per_type_limit:
                        break
                return found
            groups['chat'].extend(await asyncio.to_thread(_search_chats))
        except Exception:
            logger.exception('Workbench chat search failed')
    if 'knowledge' in types:
        try:
            from cyrene.config import get_knowledge_db_path
            from cyrene.knowledge import retrieve
            from cyrene.runtime.database import init_knowledge_db
            seen_docs: set[str] = set()
            for pid, dk in project_kb_keys.items():
                db_path_kb = str(get_knowledge_db_path(dk))
                try:
                    await init_knowledge_db(db_path_kb)
                    kb_results = await retrieve.search_knowledge(db_path_kb, query, k=per_type_limit * 3)
                    for item in kb_results:
                        doc_id = str(item.get('document_id') or '')
                        if not doc_id:
                            continue
                        key = f'{dk}:{doc_id}'
                        if key in seen_docs:
                            continue
                        seen_docs.add(key)
                        groups['knowledge'].append({'id': doc_id, 'type': 'knowledge', 'title': str(item.get('document_name') or doc_id), 'snippet': _search_snippet(str(item.get('content') or ''), query), 'projectId': pid, 'projectName': project_names.get(pid, 'Workspace'), 'docId': doc_id, 'chunkId': item.get('chunk_id'), 'score': item.get('score')})
                        if len(groups['knowledge']) >= per_type_limit:
                            break
                except Exception:
                    logger.exception('Knowledge search failed for workspace %s', dk)
                if len(groups['knowledge']) >= per_type_limit:
                    break
        except Exception:
            logger.exception('Workbench knowledge search failed')
    if 'memory' in types:
        try:
            from cyrene.config import STORE_DIR
            from cyrene.workbench.store import list_document_keys, read_document
            memory_service = workbench_memory
            entry_id = memory_service._entry_id
            is_user_visible_entry = memory_service._is_user_visible_entry

            def _search_memories() -> list[dict[str, Any]]:
                found: list[dict[str, Any]] = []
                memory_keys = {key[len('memory:'):] for key in list_document_keys(project_repository._db_path or str(DB_PATH), prefix='memory:')}
                memory_keys.update((path.stem[len('wb_memory_'):] for path in STORE_DIR.glob('wb_memory_*.json')))
                for dk in sorted(memory_keys):
                    if len(found) >= per_type_limit:
                        break
                    pid = data_key_to_project.get(dk, '')
                    data = read_document(project_repository._db_path or str(DB_PATH), f'memory:{dk}', list, legacy_path=STORE_DIR / f'wb_memory_{dk}.json')
                    entries = data if isinstance(data, list) else []
                    for entry in entries:
                        if not isinstance(entry, dict) or not is_user_visible_entry(entry):
                            continue
                        content = str(entry.get('content') or '')
                        tags = [str(t) for t in entry.get('tags') or []]
                        tag_text = ' '.join(tags)
                        if not (_search_matches(query, content) or _search_matches(query, tag_text)):
                            continue
                        mem_id = entry_id(entry)
                        found.append({'id': mem_id, 'type': 'memory', 'title': content[:80] or 'Memory', 'snippet': _search_snippet(content, query), 'projectId': pid, 'projectName': project_names.get(pid, 'Workspace'), 'memId': mem_id, 'category': entry.get('category') or entry.get('type') or 'fact', 'tags': tags, 'updatedAt': entry.get('last_mentioned') or entry.get('first_seen') or ''})
                        if len(found) >= per_type_limit:
                            break
                return found
            groups['memory'].extend(await asyncio.to_thread(_search_memories))
        except Exception:
            logger.exception('Workbench memory search failed')
    if 'schedule' in types:
        try:
            from cyrene.runtime import database as cy_db
            from cyrene.tool_impl.entity.store import list_entities
            try:
                all_tasks = await cy_db.get_all_tasks(project_repository._db_path)
                for task in all_tasks:
                    prompt = str(task.get('prompt') or '')
                    if _search_matches(query, prompt):
                        dk = str(task.get('project_id') or 'default')
                        pid = data_key_to_project.get(dk, '')
                        groups['schedule'].append({'id': str(task.get('id') or ''), 'type': 'schedule', 'title': prompt or 'Scheduled task', 'snippet': _search_snippet(prompt, query), 'projectId': pid, 'projectName': project_names.get(pid, 'Workspace'), 'taskId': str(task.get('id') or ''), 'scheduleType': task.get('schedule_type') or 'once', 'scheduleValue': task.get('schedule_value') or '', 'nextRun': task.get('next_run') or '', 'category': 'task_recurring' if task.get('schedule_type') != 'once' else 'task_once'})
                        if len(groups['schedule']) >= per_type_limit:
                            break
            except Exception:
                logger.exception('Scheduled task search failed')
            if len(groups['schedule']) < per_type_limit:
                try:
                    entities = await list_entities(project_repository._db_path, has_due_date=True, limit=500)
                    for entity in entities:
                        title = str(entity.get('title') or '')
                        content = str(entity.get('content') or '')
                        if _search_matches(query, title) or _search_matches(query, content):
                            dk = str(entity.get('project_id') or 'default')
                            pid = data_key_to_project.get(dk, '')
                            groups['schedule'].append({'id': str(entity.get('id') or ''), 'type': 'schedule', 'title': title or 'Event', 'snippet': _search_snippet(content or title, query), 'projectId': pid, 'projectName': project_names.get(pid, 'Workspace'), 'entityId': str(entity.get('id') or ''), 'dueDate': entity.get('due_date') or '', 'category': 'entity_due'})
                            if len(groups['schedule']) >= per_type_limit:
                                break
                except Exception:
                    logger.exception('Entity deadline search failed')
        except Exception:
            logger.exception('Workbench schedule search failed')
    return groups

def _resolve_ui_tz(tz_name: str=''):
    name = str(tz_name or '').strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc

async def _build_ui_data(tz_name: str='') -> dict:
    """Assemble the full DATA payload the SPA expects."""
    sessions = _build_sessions()
    if not sessions:
        sessions = [_empty_session()]
    ui_tz = _resolve_ui_tz(tz_name)
    return {'user': _build_user(), 'assistantName': ASSISTANT_NAME, 'appVersion': get_version_label(), 'dashboard': await _build_dashboard(ui_tz), 'sessions': sessions, 'status': await _build_status(), 'settings': _build_settings_meta(), 'onboarding': get_onboarding_status(), 'entities': await _build_entities_summary()}

async def _build_entities_summary() -> list:
    """Return active entities for the SPA bootstrap payload."""
    try:
        from cyrene.tool_impl.entity.store import list_entities
        return await list_entities(project_repository._db_path, status='active', limit=100)
    except Exception:
        logger.exception('Failed to build entities summary')
        return []

def _build_user() -> dict:
    """User identity from the stored profile, falling back to the local account name."""
    from cyrene.runtime.settings_store import get as get_setting
    name = str(get_setting('profile_name', '') or '').strip() or _resolve_local_username()
    handle = re.sub('[^a-z0-9._-]+', '', name.lower().replace(' ', '')) or 'user'
    parts = [part for part in re.split('[\\s._-]+', name) if part]
    initials = ''.join((part[0].upper() for part in parts[:2])) or name[:2].upper() or 'U'
    return {'name': name, 'handle': handle, 'initials': initials, 'avatar': str(get_setting('profile_avatar', '') or ''), 'avatar_emoji': str(get_setting('profile_avatar_emoji', '') or ''), 'avatar_color': str(get_setting('profile_avatar_color', '') or ''), 'bio': str(get_setting('profile_bio', '') or '')}

def _resolve_local_username() -> str:
    """Best-effort local account name for the current machine."""
    candidates = [os.environ.get('USER'), os.environ.get('USERNAME'), os.environ.get('LOGNAME')]
    try:
        candidates.append(getpass.getuser())
    except Exception:
        pass
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return 'user'

async def _delete_chat_session(session_id: str) -> tuple[dict[str, Any], int]:
    """Delete/reset a legacy chat session and return its API payload/status."""
    if session_id == 'run_live':
        interrupt_active_run()
        await clear_session_id(deleting=True)
        return ({'ok': True, 'sessions': _build_sessions()}, 200)
    if session_id.startswith('archive_'):
        suffix = session_id[len('archive_'):]
        date_str, _, archive_session_id = suffix.partition('_')
        filepath = CONVERSATIONS_DIR / f'{date_str}.md'
        if not filepath.exists():
            return ({'error': 'session not found'}, 404)
        try:
            content = filepath.read_text(encoding='utf-8')
            sections = _parse_archive_sections(content)
            kept_sections = [section for section in sections if str(section.get('archive_session_id', '')).strip() != archive_session_id]
            if len(kept_sections) == len(sections):
                return ({'error': 'session not found'}, 404)
            _write_archive_sections(filepath, date_str, kept_sections)
        except Exception as exc:
            return ({'error': str(exc)}, 500)
        return ({'ok': True, 'sessions': _build_sessions()}, 200)
    return ({'error': 'unknown session id'}, 400)

def _build_sessions() -> list[dict]:
    """Build session list — current state.json + parsed conversation archives."""
    sessions: list[dict] = []
    current = _build_current_session()
    if current:
        sessions.append(current)
    skip_archive_ids: set[str] = set()
    current_archive_session_id = str(current.get('archiveSessionId', '')).strip() if current else ''
    current_archive_date = str(current.get('archiveDate', '')).strip() if current else ''
    if current_archive_session_id and current_archive_date:
        skip_archive_ids.add(f'{current_archive_date}:{current_archive_session_id}')
    archive_sessions = _build_archive_sessions(skip_archive_ids=skip_archive_ids)
    sessions.extend(archive_sessions)
    return sessions

def build_sessions() -> list[dict]:
    """Return the public session-list presentation."""
    return _build_sessions()

def _build_summary(raw_msgs: list[dict]) -> dict:
    usage = session_metrics.usage_totals(raw_msgs)
    return {'tokens': session_metrics.format_tokens(usage), 'spend': _calc_messages_spend(raw_msgs), 'toolCalls': session_view.count_tool_calls(raw_msgs), 'requests': usage['requests'], 'total_tokens': usage['total_tokens']}

def _build_current_session() -> dict | None:
    """Build a session object from state.json + live subagents.

    Always returns a run_live entry — when state.json is missing or empty,
    returns an empty placeholder so the Chat page shows a clean "start a new
    conversation" view instead of falling back to an old archive.
    """
    state: dict[str, Any] = {}
    raw_msgs: list[dict] = []
    if STATE_FILE.exists():
        try:
            loaded = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            state = loaded if isinstance(loaded, dict) else {}
            raw_msgs = state.get('messages', []) or []
        except Exception:
            logger.warning('Failed to parse state file %s; showing empty session', STATE_FILE, exc_info=True)
            raw_msgs = []
            state = {}
    pending_question = session_view.build_pending_question(state.get('pending_question', {}))
    messages = _convert_messages(raw_msgs) if raw_msgs else []
    current_round_id = _latest_round_id_from_messages(raw_msgs)
    current_round_title = next((str(msg.get('round_title', '')).strip() for msg in reversed(raw_msgs) if str(msg.get('round_id', '')).strip() == current_round_id and msg.get('round_title')), '')
    from cyrene.subagent import registry_snapshot
    subagent_registry = _infer_subagent_entries(raw_msgs, registry_snapshot())
    subagents = []
    for agent_id, info in subagent_registry.items():
        status = info.get('status', 'running')
        ui_status = {'running': 'running', 'waiting': 'queued', 'resumed': 'running', 'done': 'done', 'timeout': 'err', 'incomplete': 'err'}.get(status, status)
        created_at = info.get('created_at')
        subagents.append({'id': agent_id, 'name': agent_id, 'status': ui_status, 'task': info.get('task', ''), 'roundId': str(info.get('round_id', '')).strip(), 'tokens': len(info.get('messages', [])), 'elapsed': session_metrics.elapsed_since(created_at), 'progress': session_metrics.status_progress(status), 'result': info.get('result', ''), 'messageCount': len(info.get('messages', [])), 'createdAt': session_metrics.short_time(created_at), 'updatedAt': session_metrics.short_time(info.get('updated_at'))})
    subagents.sort(key=lambda item: (item.get('createdAt') == '—', item.get('createdAt'), item['name']))
    live_rounds = get_live_rounds()
    session_start = _session_started_at(raw_msgs)
    started_at = datetime.fromtimestamp(session_start, tz=timezone.utc).strftime('%H:%M')
    duration = session_metrics.format_duration(time.time() - session_start)
    last_msg = messages[-1] if messages else None
    is_empty = not messages
    if live_rounds and any((str(item.get('status', '')) == 'running' for item in live_rounds)):
        live_status = 'running'
    elif pending_question:
        live_status = 'queued'
    elif live_rounds and any((int(item.get('pendingGuidance', 0) or 0) > 0 for item in live_rounds)):
        live_status = 'queued'
    elif is_empty:
        live_status = 'idle'
    else:
        recent = debug.get_recent_events(200)
        now_ts = datetime.now(timezone.utc)
        if session_view.has_recent_main_agent_activity(recent, now_ts):
            live_status = 'running'
        else:
            live_status = 'done'
    live_summary = _build_summary(raw_msgs)
    main_agent_total_tokens = live_summary.get('total_tokens')
    subagent_usage = session_metrics.merge_usage_totals(*[session_metrics.usage_totals(info.get('messages', [])) for info in subagent_registry.values()])
    combined_live_usage = session_metrics.merge_usage_totals(session_metrics.usage_totals(raw_msgs), subagent_usage)
    if combined_live_usage.get('requests') is not None:
        live_summary['requests'] = combined_live_usage.get('requests')
        live_summary['tokens'] = session_metrics.format_tokens(combined_live_usage)
        live_summary['spend'] = _calc_messages_spend([*raw_msgs, *[message for info in subagent_registry.values() for message in info.get('messages', [])]])
        live_summary['toolCalls'] = live_summary['toolCalls'] + sum((_count_tool_calls(info.get('messages', [])) for info in subagent_registry.values()))
        live_summary['total_tokens'] = combined_live_usage.get('total_tokens')
    return {'id': 'run_live', 'title': str(state.get('session_title', '')).strip() or ('new session' if is_empty else 'current session'), 'status': live_status, 'started': started_at, 'archiveDate': datetime.now().astimezone().strftime('%Y-%m-%d'), 'archiveSessionId': str(state.get('archive_session_id', '')).strip(), 'dur': duration, 'preview': last_msg['body'][:80] + '…' if last_msg and last_msg.get('body') else '—', 'model': project_runtime._get_model(), 'ctx_limit': project_runtime._get_current_model_ctx_limit(), 'currentRoundId': current_round_id, 'currentRoundTitle': current_round_title, 'pendingQuestion': pending_question, 'summary': live_summary, 'main_agent_total_tokens': main_agent_total_tokens, 'main_agent_context_tokens': session_metrics.last_request_context_tokens(raw_msgs), 'chat': {'contextChips': _build_context_chips(), 'messages': messages}, 'liveRounds': live_rounds, 'subagents': subagents, 'flow': _build_live_flow(raw_msgs, messages, subagents, subagent_registry)}

def _build_archive_sessions(skip_dates: set[str] | None=None, skip_archive_ids: set[str] | None=None) -> list[dict]:
    """Build session entries from conversation archives (one per archived session)."""
    if not CONVERSATIONS_DIR.exists():
        return []
    sessions = []
    files = sorted(CONVERSATIONS_DIR.glob('*.md'), reverse=True)
    for filepath in files[:10]:
        date_str = filepath.stem
        if skip_dates and date_str in skip_dates:
            continue
        try:
            content = filepath.read_text(encoding='utf-8')
        except Exception:
            continue
        sections = _parse_archive_sections(content)
        if not sections:
            continue
        file_session_title = _parse_archive_session_title(content)
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for index, section in enumerate(sections):
            archive_session_id = str(section.get('archive_session_id', '')).strip() or f'legacy_{date_str}'
            if archive_session_id not in groups:
                groups[archive_session_id] = []
                order.append(archive_session_id)
            groups[archive_session_id].append({**section, '_order': index})
        for archive_session_id in reversed(order):
            archive_key = f'{date_str}:{archive_session_id}'
            if skip_archive_ids and archive_key in skip_archive_ids:
                continue
            group_sections = groups[archive_session_id]
            messages = _messages_from_archive_sections(group_sections)
            if not messages:
                continue
            last_user = next((m for m in messages if m['role'] == 'user'), None)
            group_session_title = next((str(section.get('session_title', '')).strip() for section in group_sections if section.get('session_title')), '')
            is_legacy = archive_session_id.startswith('legacy_')
            title = group_session_title or (file_session_title if is_legacy else '') or (last_user['body'][:60] + ('…' if len(last_user['body']) > 60 else '') if last_user else date_str)
            preview = messages[-1].get('body', '')[:80] if messages else ''
            current_round_id = next((str(m.get('round_id', '')).strip() for m in reversed(messages) if m.get('round_id')), '')
            current_round_title = next((str(m.get('round_title', '')).strip() for m in reversed(messages) if str(m.get('round_id', '')).strip() == current_round_id and m.get('round_title')), '')
            sessions.append({'id': f'archive_{date_str}_{archive_session_id}', 'title': title, 'status': 'done', 'started': date_str, 'dur': '—', 'preview': preview, 'model': project_runtime._get_model(), 'currentRoundId': current_round_id, 'currentRoundTitle': current_round_title, 'summary': {'tokens': f'{len(messages)} msgs', 'spend': '—', 'toolCalls': 0}, 'chat': {'contextChips': [{'icon': '📅', 'label': date_str}], 'messages': messages}, 'liveRounds': [], 'shells': [], 'subagents': [], 'flow': _build_simple_flow(messages)})
    return sessions

def build_archive_sessions(*, skip_archive_ids: set[str] | None = None) -> list[dict]:
    """Return archive session presentations excluding explicit identities."""
    return _build_archive_sessions(skip_archive_ids=skip_archive_ids)

def _parse_archive_session_title(content: str) -> str:
    return parse_archive_meta(content, 'session_title')

def _parse_archive_sections(content: str) -> list[dict[str, Any]]:
    """Parse a conversations/YYYY-MM-DD.md file into archive sections with metadata."""
    sections_out: list[dict[str, Any]] = []
    round_index = 0
    for section in split_archive_entry_blocks(content):
        if '**User**:' not in section:
            continue
        ts_match = re.search('##\\s*(\\S+\\s+UTC)', section)
        dialogue_match = re.search('\\*\\*User\\*\\*:\\s*(.*?)\\n+\\*\\*[^*]+\\*\\*:\\s*(.*)\\Z', section, re.DOTALL)
        if not ts_match or not dialogue_match:
            continue
        ts = ts_match.group(1).strip()
        user_body = dialogue_match.group(1).strip()
        assistant_body = dialogue_match.group(2).strip()
        round_id = parse_archive_meta(section, 'round_id') or f'archive_round_{round_index}'
        round_title = parse_archive_meta(section, 'round_title')
        archive_session_id = parse_archive_meta(section, 'archive_session_id')
        session_title = parse_archive_meta(section, 'session_title')
        body_start = section.find('## ')
        raw_entry = section[body_start:].strip() if body_start >= 0 else section.strip()
        sections_out.append({'timestamp': ts, 'user_body': user_body, 'assistant_body': assistant_body, 'round_id': round_id, 'round_title': round_title, 'archive_session_id': archive_session_id, 'session_title': session_title, 'raw_entry': raw_entry})
        round_index += 1
    return sections_out

def parse_archive_sections(content: str) -> list[dict[str, Any]]:
    """Parse persisted conversation archive sections."""
    return _parse_archive_sections(content)

def _messages_from_archive_sections(sections: list[dict[str, Any]]) -> list[dict]:
    messages: list[dict] = []
    for index, section in enumerate(sections):
        messages.append({'id': f'm{index}u', 'role': 'user', 'time': section['timestamp'], 'body': section['user_body'], 'round_id': section['round_id'], 'round_title': section['round_title']})
        messages.append({'id': f'm{index}a', 'role': 'agent', 'time': section['timestamp'], 'body': section['assistant_body'], 'round_id': section['round_id'], 'round_title': section['round_title']})
    return messages

def _parse_archive_file(content: str) -> list[dict]:
    """Parse a conversations/YYYY-MM-DD.md file into UI-formatted messages."""
    return _messages_from_archive_sections(_parse_archive_sections(content))

def _write_archive_sections(filepath: Path, date_str: str, sections: list[dict[str, Any]]) -> None:
    if not sections:
        if filepath.exists():
            filepath.unlink()
        return
    first_session_title = next((str(section.get('session_title', '')).strip() for section in sections if section.get('session_title')), '')
    content = upsert_archive_session_title(f'# Conversations - {date_str}\n\n', date_str, first_session_title)
    content += '\n---\n\n'.join((section['raw_entry'] for section in sections if section.get('raw_entry'))) + '\n\n---\n'
    filepath.write_text(content, encoding='utf-8')

def write_archive_sections(filepath: Path, date_str: str, sections: list[dict[str, Any]]) -> None:
    """Persist parsed archive sections using the canonical archive format."""
    _write_archive_sections(filepath, date_str, sections)

def _is_hidden_internal_message(message: dict[str, Any]) -> bool:
    if bool(message.get('hidden_from_ui')):
        return True
    role = str(message.get('role', '')).strip()
    content = str(message.get('content', '') or '').strip()
    if role != 'user' or not content:
        return False
    return content.startswith('## Research Materials\n\nBelow are the research findings gathered on this question.') or content.startswith('[Decision-phase correction] You attempted unavailable tool(s):')

def _convert_messages(raw_msgs: list[dict]) -> list[dict]:
    """Convert state.json raw messages → UI message format."""
    out = []
    compacted_marker_emitted = False
    tool_outputs = session_metrics.tool_output_map(raw_msgs)
    for i, m in enumerate(raw_msgs):
        if _is_hidden_internal_message(m):
            continue
        if isinstance(m, dict) and m.get('compacted_block'):
            if not compacted_marker_emitted:
                cid = str(m.get('message_id', '')).strip() or 'compacted' + str(i)
                out.append({'id': cid, 'messageId': cid, 'role': 'system', 'kind': 'compacted', 'compacted': True})
                compacted_marker_emitted = True
            continue
        role = m.get('role', '')
        if role not in ('user', 'assistant'):
            continue
        content = (m.get('content') or '').strip()
        has_live_detail = bool(m.get('reasoning_content') or m.get('tool_calls'))
        has_attachments = isinstance(m.get('attachments'), list) and bool(m.get('attachments'))
        if role == 'user' and (not content) and (not m.get('attachments')):
            continue
        if role == 'assistant' and (not content) and (not has_live_detail) and (not has_attachments):
            continue
        ui_role = 'user' if role == 'user' else 'agent'
        message_id = str(m.get('message_id', '')).strip() or f'm{i}'
        ui_msg = {'id': message_id, 'messageId': message_id, 'role': ui_role, 'time': '—'}
        if content:
            ui_msg['body'] = content
        if isinstance(m.get('attachments'), list):
            ui_msg['attachments'] = [{'id': str(item.get('id') or '').strip(), 'name': str(item.get('name') or 'file'), 'content_type': str(item.get('content_type') or 'application/octet-stream'), 'size': int(item.get('size') or 0), 'kind': str(item.get('kind') or 'file'), 'url': str(item.get('url') or '').strip(), **({'width': int(item.get('width'))} if str(item.get('width', '')).strip().isdigit() else {}), **({'height': int(item.get('height'))} if str(item.get('height', '')).strip().isdigit() else {})} for item in m.get('attachments') if isinstance(item, dict)]
        if bool(m.get('intermediate_reply')):
            ui_msg['intermediateReply'] = True
        if bool(m.get('question_prompt')):
            ui_msg['questionPrompt'] = True
        question_id = str(m.get('question_id', '')).strip()
        if question_id:
            ui_msg['questionId'] = question_id
        round_id = str(m.get('round_id', '')).strip()
        if round_id:
            ui_msg['roundId'] = round_id
        client_request_id = str(m.get('client_request_id', '')).strip()
        if client_request_id:
            ui_msg['clientRequestId'] = client_request_id
        queued_guidance_id = str(m.get('queued_guidance_id', '')).strip()
        if queued_guidance_id:
            ui_msg['queuedGuidanceId'] = queued_guidance_id
        guidance_ack_for_guidance_id = str(m.get('guidance_ack_for_guidance_id', '')).strip()
        if guidance_ack_for_guidance_id:
            ui_msg['guidanceAckForGuidanceId'] = guidance_ack_for_guidance_id
        in_reply_to_guidance_id = str(m.get('in_reply_to_guidance_id', '')).strip()
        if in_reply_to_guidance_id:
            ui_msg['inReplyToGuidanceId'] = in_reply_to_guidance_id
        if m.get('reasoning_content'):
            ui_msg['thinking'] = m['reasoning_content']
        if m.get('tool_calls'):
            tools = []
            for tc in m['tool_calls']:
                fn = tc.get('function', {})
                raw_args = fn.get('arguments', '')
                parsed_args = session_metrics.safe_json_loads(raw_args) if isinstance(raw_args, str) else raw_args
                args = raw_args
                if isinstance(args, str) and len(args) > 80:
                    args = args[:80] + '…'
                tool_call_id = str(tc.get('id') or '')
                tools.append({'name': fn.get('name', '?'), 'arg': str(args)[:120], 'status': 'done', 'out': tool_outputs.get(tool_call_id, ''), 'toolCallId': tool_call_id, 'rawArgs': parsed_args if parsed_args is not None else raw_args})
            ui_msg['tools'] = tools
        out.append(ui_msg)
    return session_view.collapse_duplicate_user_messages(session_view.merge_adjacent_trace_only_messages(session_view.dedupe_repeated_messages(out)))

def _session_started_at(raw_msgs: list[dict]) -> float:
    return session_view.session_started_at(raw_msgs, _SERVER_STARTED_AT)

def _build_simple_flow(messages: list[dict]) -> dict:
    """Archive flow grouped by conversation round, without live tool traces."""
    rounds: list[list[dict]] = []
    current: list[dict] = []
    current_round_id = ''
    for msg in messages:
        round_id = str(msg.get('round_id', '')).strip() or current_round_id or 'archive_round_0'
        if current and round_id != current_round_id:
            rounds.append(current)
            current = []
        current.append(msg)
        current_round_id = round_id
    if current:
        rounds.append(current)
    nodes: list[dict] = []
    edges: list[dict] = []
    y_offset = 0
    multiple_rounds = len(rounds) > 1
    for round_index, round_msgs in enumerate(rounds or [messages]):
        prefix = f'r{round_index}_' if multiple_rounds else ''
        last_user = next((m for m in round_msgs if m['role'] == 'user'), None)
        last_agent = next((m for m in reversed(round_msgs) if m['role'] == 'agent'), None)
        round_title = next((str(m.get('round_title', '')).strip() for m in round_msgs if m.get('round_title')), '') or 'user request'
        user_id = f'{prefix}n_user'
        main_id = f'{prefix}n_main'
        out_id = f'{prefix}n_out'
        nodes.extend([{'id': user_id, 'kind': 'input', 'x': 40, 'y': y_offset + 80, 'title': round_title, 'status': 'done', 'detail': {'role': 'User', 'text': last_user['body'] if last_user else '', 'tokens': 0, 'time': last_user['time'] if last_user else '—'}}, {'id': main_id, 'kind': 'main', 'x': 320, 'y': y_offset + 70, 'title': f'main agent · {ASSISTANT_NAME}', 'subtitle': 'archive', 'status': 'done', 'model': project_runtime._get_model(), 'detail': {'systemPrompt': f'You are {ASSISTANT_NAME}, an AI companion. Use SOUL.md to maintain persona.', 'reasoning': 'Loaded session from archive — no live reasoning trace.', 'tokensIn': 0, 'tokensOut': 0, 'model': project_runtime._get_model(), 'temp': 0.2}}, {'id': out_id, 'kind': 'output', 'x': 660, 'y': y_offset + 90, 'title': 'response', 'status': 'done', 'detail': {'kind': 'Output', 'content': last_agent['body'][:600] if last_agent else '—'}}])
        edges.extend([{'from': user_id, 'to': main_id}, {'from': main_id, 'to': out_id}])
        y_offset += 180
    return {'nodes': nodes, 'edges': edges}

def _build_live_flow(raw_msgs: list[dict], messages: list[dict], subagents: list[dict], registry: dict[str, dict]) -> dict:
    """Build a richer flow for the current session, stacked by conversation round."""
    rounds = session_view.split_raw_rounds(raw_msgs)
    recent_events = debug.get_recent_events(250)
    if not rounds and raw_msgs:
        rounds = [raw_msgs]
    if not rounds:
        synthetic_round = _synthetic_live_round(registry, recent_events)
        if synthetic_round:
            rounds = [synthetic_round]
    if not rounds:
        return {'nodes': [], 'edges': []}
    rounds, active_round_index = session_view.prune_flow_rounds(rounds)
    if not rounds:
        return {'nodes': [], 'edges': []}
    nodes: list[dict] = []
    edges: list[dict] = []
    next_y = 0
    multiple_rounds = len(rounds) > 1
    for round_index, round_raw in enumerate(rounds):
        is_current_round = round_index == active_round_index
        round_messages = _convert_messages(round_raw)
        round_id = _latest_round_id_from_messages(round_raw)
        round_registry = _round_registry_for_flow(round_raw, registry if is_current_round else {})
        related_agents = _related_round_agent_names(set(round_registry), round_id=round_id)
        if is_current_round and subagents:
            candidate_subagents = [sa for sa in subagents if _subagent_matches_round(sa, round_id) and (not round_registry or sa['name'] in related_agents)]
            for sa in candidate_subagents:
                entry = round_registry.setdefault(sa['name'], {'task': sa.get('task', ''), 'status': 'done', 'result': sa.get('result', ''), 'messages': [], 'created_at': None, 'updated_at': None, 'round_id': round_id})
                entry['task'] = entry.get('task') or sa.get('task', '')
                entry['status'] = _registry_status_from_ui(sa.get('status', entry.get('status', 'done')))
                entry['result'] = entry.get('result') or sa.get('result', '')
        if is_current_round and (not round_registry) and registry:
            round_registry = {agent_id: dict(info) for agent_id, info in registry.items() if not round_id or info.get('round_id') in ('', round_id)}
        round_subagents = _subagent_cards_from_registry(round_registry)
        round_recent_events = _events_for_round(recent_events, round_id) if is_current_round else []
        prefix = f'r{round_index}_' if multiple_rounds else ''
        round_nodes, round_edges, round_bottom = _build_live_flow_round(prefix=prefix, raw_msgs=round_raw, messages=round_messages, subagents=round_subagents, registry=round_registry, recent_events=round_recent_events, y_offset=next_y, round_id=round_id)
        nodes.extend(round_nodes)
        edges.extend(round_edges)
        next_y = round_bottom + 180
    return {'nodes': nodes, 'edges': edges}

def _synthetic_live_round(registry: dict[str, dict], recent_events: list[dict]) -> list[dict]:
    if not registry:
        return []
    round_id = next((str(info.get('round_id', '')).strip() for info in registry.values() if info.get('round_id')), '')
    latest_phase = next((e for e in reversed(recent_events) if e.get('type') == 'phase_transition'), None)
    latest_llm = next((e for e in reversed(recent_events) if e.get('type') == 'llm_call' and e.get('caller') == 'main_agent'), None)
    prompt = latest_phase.get('detail') if latest_phase and latest_phase.get('detail') else latest_llm.get('response') if latest_llm and latest_llm.get('response') else 'Live round in progress'
    entry: dict[str, Any] = {'role': 'user', 'content': prompt}
    if round_id:
        entry['round_id'] = round_id
    return [entry]

def _round_registry_for_flow(raw_msgs: list[dict], live_registry: dict[str, dict]) -> dict[str, dict]:
    round_id = _latest_round_id_from_messages(raw_msgs)
    entries: dict[str, dict] = _snapshot_entries_from_messages(raw_msgs, round_id=round_id)
    for msg in raw_msgs:
        for tc in msg.get('tool_calls') or []:
            fn = tc.get('function', {})
            if fn.get('name') != 'spawn_subagent':
                continue
            args = session_metrics.safe_json_loads(fn.get('arguments') or '{}')
            if not isinstance(args, dict):
                continue
            agent_id = str(args.get('agent_id') or '').strip()
            if not agent_id:
                continue
            live = dict(live_registry.get(agent_id, {}))
            if round_id and live.get('round_id') and (live.get('round_id') != round_id):
                live = {}
            task = str(args.get('task') or live.get('task') or '')
            _merge_subagent_record(entries, agent_id, {'task': task, 'status': live.get('status', entries.get(agent_id, {}).get('status', 'done')), 'result': live.get('result', entries.get(agent_id, {}).get('result', '')), 'messages': list(live.get('messages', [])) or list(entries.get(agent_id, {}).get('messages', [])), 'created_at': live.get('created_at', entries.get(agent_id, {}).get('created_at')), 'updated_at': live.get('updated_at', entries.get(agent_id, {}).get('updated_at')), 'round_id': round_id or live.get('round_id', entries.get(agent_id, {}).get('round_id', ''))})
    for agent_id, live in live_registry.items():
        live_round_id = str(live.get('round_id', '')).strip()
        if round_id and live_round_id and (live_round_id != round_id):
            continue
        _merge_subagent_record(entries, agent_id, {'task': live.get('task', ''), 'status': live.get('status', 'done'), 'result': live.get('result', ''), 'messages': list(live.get('messages', [])), 'created_at': live.get('created_at'), 'updated_at': live.get('updated_at'), 'round_id': round_id or live_round_id})
    return entries

def _related_round_agent_names(seed_ids: set[str], round_id: str='') -> set[str]:
    if not seed_ids:
        return set()
    related = set(seed_ids)
    inbox_root = DATA_DIR / 'inbox'
    if not inbox_root.exists():
        return related
    changed = True
    while changed:
        changed = False
        for msg_file in inbox_root.glob('*/*.json'):
            try:
                payload = json.loads(msg_file.read_text(encoding='utf-8'))
            except Exception:
                continue
            if round_id and str(payload.get('round_id', '')) != round_id:
                continue
            from_agent = str(payload.get('from', ''))
            to_agent = str(payload.get('to', ''))
            if from_agent in related or to_agent in related:
                size_before = len(related)
                if from_agent:
                    related.add(from_agent)
                if to_agent:
                    related.add(to_agent)
                changed = changed or len(related) != size_before
    return related

def _round_id_from_messages(raw_msgs: list[dict]) -> str:
    for msg in raw_msgs:
        round_id = str(msg.get('round_id', '')).strip()
        if round_id:
            return round_id
    return ''

def _latest_round_id_from_messages(raw_msgs: list[dict]) -> str:
    for msg in reversed(raw_msgs):
        round_id = str(msg.get('round_id', '')).strip()
        if round_id:
            return round_id
    return ''

def _events_for_round(recent_events: list[dict], round_id: str) -> list[dict]:
    if not round_id:
        return list(recent_events)
    return [event for event in recent_events if str(event.get('round_id', '')).strip() == round_id]

def _subagent_matches_round(subagent: dict[str, Any], round_id: str) -> bool:
    if not round_id:
        return True
    subagent_round_id = str(subagent.get('roundId') or subagent.get('round_id') or '').strip()
    return not subagent_round_id or subagent_round_id == round_id

def _registry_status_from_ui(status: str) -> str:
    return {'running': 'running', 'queued': 'waiting', 'done': 'done', 'err': 'timeout'}.get(status, status)

def _is_summary_agent_id(agent_id: str) -> bool:
    return str(agent_id or '').startswith('agent_summary_')

def _iter_flow_snapshots(raw_msgs: list[dict], round_id: str='') -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for msg in raw_msgs:
        snapshot = msg.get('subagent_flow_snapshot')
        if not isinstance(snapshot, dict):
            continue
        snapshot_round_id = str(snapshot.get('round_id', '')).strip() or str(msg.get('round_id', '')).strip()
        if round_id and snapshot_round_id and (snapshot_round_id != round_id):
            continue
        snapshots.append(snapshot)
    return snapshots

def _merge_subagent_record(entries: dict[str, dict[str, Any]], agent_id: str, meta: dict[str, Any]) -> None:
    incoming = dict(meta)
    incoming_round_id = str(incoming.get('round_id', '')).strip()
    existing = entries.get(agent_id)
    if existing is None:
        entries[agent_id] = incoming
        return
    existing_round_id = str(existing.get('round_id', '')).strip()
    if incoming_round_id and existing_round_id and (incoming_round_id != existing_round_id):
        entries[agent_id] = incoming
        return
    merged = dict(existing)
    for key, value in incoming.items():
        if key == 'messages':
            if value:
                merged['messages'] = value
            else:
                merged.setdefault('messages', [])
            continue
        if value not in (None, '', []):
            merged[key] = value
        else:
            merged.setdefault(key, value)
    entries[agent_id] = merged

def _snapshot_entries_from_messages(raw_msgs: list[dict], round_id: str='') -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for snapshot in _iter_flow_snapshots(raw_msgs, round_id=round_id):
        agents = snapshot.get('agents') or {}
        if not isinstance(agents, dict):
            continue
        snapshot_round_id = str(snapshot.get('round_id', '')).strip()
        for agent_id, info in agents.items():
            if not isinstance(info, dict):
                continue
            meta = dict(info)
            meta.setdefault('round_id', snapshot_round_id)
            meta.setdefault('messages', [])
            _merge_subagent_record(entries, str(agent_id), meta)
    return entries

def _snapshot_comm_messages_from_messages(raw_msgs: list[dict], round_id: str='') -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for snapshot in _iter_flow_snapshots(raw_msgs, round_id=round_id):
        comm_messages = snapshot.get('comm_messages') or []
        if not isinstance(comm_messages, list):
            continue
        for item in comm_messages:
            if not isinstance(item, dict):
                continue
            from_agent = str(item.get('from', '')).strip()
            to_agent = str(item.get('to', '')).strip()
            body = str(item.get('content', ''))
            message_id = str(item.get('message_id') or '').strip()
            dedupe_key = (message_id, from_agent, to_agent, body)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            items.append(dict(item))
    items.sort(key=lambda item: str(item.get('timestamp') or ''))
    return items

def _subagent_cards_from_registry(round_registry: dict[str, dict]) -> list[dict]:
    cards: list[dict] = []
    for agent_id, info in round_registry.items():
        status = info.get('status', 'done')
        ui_status = {'running': 'running', 'waiting': 'queued', 'resumed': 'running', 'done': 'done', 'timeout': 'err', 'incomplete': 'err'}.get(status, status)
        created_at = info.get('created_at')
        cards.append({'id': agent_id, 'name': agent_id, 'status': ui_status, 'task': info.get('task', ''), 'tokens': len(info.get('messages', [])), 'elapsed': session_metrics.elapsed_since(created_at), 'progress': session_metrics.status_progress(status), 'result': info.get('result', ''), 'messageCount': len(info.get('messages', [])), 'createdAt': session_metrics.short_time(created_at), 'updatedAt': session_metrics.short_time(info.get('updated_at'))})
    return cards

def _build_live_flow_round(prefix: str, raw_msgs: list[dict], messages: list[dict], subagents: list[dict], registry: dict[str, dict], recent_events: list[dict], y_offset: int, round_id: str) -> tuple[list[dict], list[dict], int]:
    main_x = 320
    main_y = y_offset + 70
    main_tool_x = 600
    subagent_x = 900
    subagent_tool_x = 1220
    output_x = 1540
    subagent_base_y = y_offset + 40
    subagent_gap_y = 220
    last_user = next((m for m in messages if m['role'] == 'user'), None)
    latest_main_llm = next((e for e in reversed(recent_events) if e.get('type') == 'llm_call' and e.get('caller') == 'main_agent'), None)
    latest_phase = next((e for e in reversed(recent_events) if e.get('type') == 'phase_transition'), None)
    latest_agent = next((m for m in reversed(messages) if m['role'] == 'agent'), None)
    latest_assistant_raw = next((m for m in reversed(raw_msgs) if m.get('role') == 'assistant'), None)
    round_title = next((str(m.get('round_title', '')).strip() for m in raw_msgs if m.get('round_title')), '') or 'user request'
    system_initiated = any((bool(m.get('system_initiated')) for m in raw_msgs if isinstance(m, dict)))
    if system_initiated and round_title == 'user request':
        round_title = 'proactive check-in'
    main_usage = session_metrics.usage_totals(raw_msgs)
    main_tool_base_y = main_y + 150
    main_id = f'{prefix}n_main'
    user_id = f'{prefix}n_user'
    output_id = f'{prefix}n_out'
    main_completed = bool(latest_agent)
    _llm_resp = latest_main_llm.get('response') if latest_main_llm else None
    _llm_text = str(_llm_resp.get('reasoning_content') or _llm_resp.get('content') or '') if isinstance(_llm_resp, dict) else ''
    _main_reasoning = str(latest_assistant_raw.get('reasoning_content') or '') if latest_assistant_raw and latest_assistant_raw.get('reasoning_content') else _llm_text if _llm_text else str(latest_phase.get('detail') or '') if latest_phase and latest_phase.get('detail') else 'Session step completed.'
    tool_nodes, tool_edges = _build_tool_nodes_for_owner(owner_node_id=main_id, owner_title=f'main agent · {ASSISTANT_NAME}', owner_x=main_x, owner_y=main_y, raw_messages=raw_msgs, recent_events=recent_events, caller_prefix='main_agent', x=main_tool_x, base_y=main_tool_base_y, owner_completed=main_completed)
    main_status = 'running' if any((sa['status'] == 'running' for sa in subagents)) or any((node['status'] == 'running' for node in tool_nodes)) else 'done' if main_completed else 'queued'
    nodes = [{'id': main_id, 'kind': 'main', 'x': main_x, 'y': main_y, 'title': f'main agent · {ASSISTANT_NAME}', 'subtitle': latest_phase['to'] if latest_phase and latest_phase.get('to') else 'orchestrator', 'status': main_status, 'model': project_runtime._get_model(), 'detail': {'systemPrompt': f'You are {ASSISTANT_NAME}. Two-phase loop: one fixed wire bundle, Phase 1 policy gating, then progressive module discovery in Phase 2. Chat filter applies SOUL.md voice.', 'reasoning': _main_reasoning, 'tokensIn': main_usage.get('prompt_tokens') or '—', 'tokensOut': main_usage.get('completion_tokens') or '—', 'model': project_runtime._get_model(), 'temp': 0.2}}]
    edges: list[dict[str, Any]] = []
    if last_user and (not system_initiated):
        user_text = str(last_user.get('body') or '').strip() or ('[Uploaded attachment]' if last_user.get('attachments') else '—')
        nodes.insert(0, {'id': user_id, 'kind': 'input', 'x': 40, 'y': y_offset + 80, 'title': round_title, 'status': 'done', 'detail': {'role': 'User', 'text': user_text, 'tokens': 0, 'time': last_user['time'] if last_user else '—'}})
        edges.append({'from': user_id, 'to': main_id, 'kind': 'active' if main_status == 'running' else None})
    nodes.extend(tool_nodes)
    edges.extend(tool_edges)
    agent_node_ids: dict[str, str] = {}
    subagent_bottoms: list[int] = []
    subagent_y = subagent_base_y
    for i, sa in enumerate(subagents):
        nid = f'{prefix}n_sa_{i}'
        agent_node_ids[sa['name']] = nid
        is_summary_agent = _is_summary_agent_id(sa['name'])
        info = registry.get(sa['name'], {})
        agent_messages = info.get('messages', [])
        latest_subassistant = next((m for m in reversed(agent_messages) if m.get('role') == 'assistant'), None)
        sub_usage = session_metrics.usage_totals(agent_messages)
        sub_tool_count = _count_tool_nodes_for_owner(raw_messages=agent_messages, recent_events=recent_events, caller_prefix=f"subagent_{sa['name']}")
        nodes.append({'id': nid, 'kind': 'subagent', 'x': subagent_x, 'y': subagent_y, 'title': f"{('summary subagent' if is_summary_agent else 'subagent')} · {sa['name']}", 'subtitle': 'synthesizer' if is_summary_agent else sa['task'][:30], 'status': sa['status'], 'detail': {'name': sa['name'], 'task': sa['task'], 'parent': 'main agent', 'role': 'summary' if is_summary_agent else 'worker', 'spawnedAt': sa.get('createdAt', '—'), 'tokensIn': sub_usage.get('prompt_tokens') or '—', 'tokensOut': sub_usage.get('completion_tokens') or '—', 'model': project_runtime._get_model(), 'reasoning': latest_subassistant.get('reasoning_content') if latest_subassistant else '', 'result': sa.get('result', '')}})
        edges.append({'from': main_id, 'to': nid, 'kind': 'dashed' if is_summary_agent else 'active' if sa['status'] == 'running' else None})
        sub_nodes, sub_edges = _build_tool_nodes_for_owner(owner_node_id=nid, owner_title=f"subagent · {sa['name']}", owner_x=subagent_x, owner_y=subagent_y, raw_messages=agent_messages, recent_events=recent_events, caller_prefix=f"subagent_{sa['name']}", x=subagent_tool_x, base_y=subagent_y, owner_completed=sa['status'] in {'done', 'err'})
        nodes.extend(sub_nodes)
        edges.extend(sub_edges)
        lane_height = _agent_lane_height(sub_tool_count)
        subagent_bottoms.append(subagent_y + lane_height)
        subagent_y += lane_height + subagent_gap_y
    summary_agent_name = next((name for name in agent_node_ids if _is_summary_agent_id(name)), '')
    if summary_agent_name:
        summary_node_id = agent_node_ids[summary_agent_name]
        for agent_name, node_id in agent_node_ids.items():
            if agent_name == summary_agent_name:
                continue
            edges.append({'from': node_id, 'to': summary_node_id, 'kind': 'dashed'})
    edges.extend(_build_comm_edges(agent_node_ids, agent_entries=registry, round_id=round_id, persisted_messages=_snapshot_comm_messages_from_messages(raw_msgs, round_id=round_id)))
    output_content = str(latest_agent.get('body') or '') if latest_agent else ''
    output_status = 'done' if output_content else 'running' if subagents else 'queued'
    if output_content or subagents:
        flow_bottom = max(subagent_bottoms) if subagent_bottoms else main_tool_base_y + _agent_lane_height(max(1, len(tool_nodes)))
        output_y = y_offset + 90 if not subagents else max(y_offset + 90, int((main_y + flow_bottom) / 2) - 43)
        nodes.append({'id': output_id, 'kind': 'output', 'x': output_x, 'y': output_y, 'title': 'response', 'status': output_status, 'detail': {'kind': 'Output', 'content': output_content or 'Waiting for subagent synthesis…'}})
        edges.append({'from': main_id, 'to': output_id, 'kind': 'active' if output_status == 'running' else None})
        if summary_agent_name:
            edges.append({'from': agent_node_ids[summary_agent_name], 'to': output_id, 'kind': 'dashed'})
    bottom = max((node['y'] + 86 for node in nodes)) if nodes else y_offset
    return (nodes, edges, bottom)

def _empty_session() -> dict:
    """Placeholder when no real session exists yet."""
    return {'id': 'run_empty', 'title': 'no active session', 'status': 'queued', 'started': '—', 'dur': '—', 'preview': 'Send a message to start a session.', 'model': project_runtime._get_model(), 'summary': {'tokens': '0', 'spend': '$0.00', 'toolCalls': 0}, 'chat': {'contextChips': _build_context_chips(), 'messages': []}, 'liveRounds': [], 'shells': [], 'subagents': [], 'flow': {'nodes': [{'id': 'n_main', 'kind': 'main', 'x': 200, 'y': 80, 'title': f'main agent · {ASSISTANT_NAME}', 'subtitle': 'idle', 'status': 'queued', 'model': project_runtime._get_model(), 'detail': {'systemPrompt': f'You are {ASSISTANT_NAME}.', 'reasoning': 'Waiting for user input.', 'tokensIn': 0, 'tokensOut': 0, 'model': project_runtime._get_model(), 'temp': 0.2}}], 'edges': []}}

async def _build_status() -> dict:
    """Status data for the Status / Dashboard page."""
    return {'phase': 'evolve', 'state': '进化', 'metrics': [], 'sparkData': [], 'workers': [], 'logs': [], 'services': [], 'model': project_runtime._get_model(), 'base_url': project_runtime._get_base_url(), 'short_term_entries': 0, 'session_messages': 0, 'scheduled_tasks': 0, 'soul_exists': SOUL_PATH.exists()}


async def build_status() -> dict:
    """Public status projection for adjacent application services."""
    return await _build_status()

async def _build_memory() -> dict:
    """Assemble full memory state for the Memory page."""
    import re
    from datetime import datetime, timezone
    soul_content = read_soul()
    soul_exists = bool(soul_content)
    sections: list[dict] = []
    current_section: dict | None = None
    temporary_count = 0
    temporary_expired = 0
    now = datetime.now(timezone.utc)
    for line in soul_content.splitlines() if soul_content else []:
        trimmed = line.strip()
        if trimmed.startswith('## ') and (not trimmed.startswith('### ')):
            if current_section:
                sections.append(current_section)
            name = trimmed[3:].strip()
            current_section = {'name': name, 'entries': [], 'entry_count': 0}
        elif current_section is not None:
            if trimmed and (not trimmed.startswith('<!--')):
                current_section['entries'].append(trimmed)
                current_section['entry_count'] += 1
                if current_section['name'] == 'TEMPORARY':
                    temporary_count += 1
                    date_match = re.search('(\\d{4}-\\d{2}-\\d{2})', trimmed)
                    if date_match:
                        try:
                            item_date = datetime.strptime(date_match.group(1), '%Y-%m-%d').replace(tzinfo=timezone.utc)
                            if (now - item_date).days >= 1:
                                temporary_expired += 1
                        except ValueError:
                            pass
    if current_section:
        sections.append(current_section)
    st_entries = load_entries()
    short_term = {'entries': sorted(st_entries, key=lambda e: e.get('last_mentioned', ''), reverse=True), 'total': len(st_entries)}
    session_msgs: list = []
    if STATE_FILE.exists():
        try:
            session_msgs = json.loads(STATE_FILE.read_text(encoding='utf-8')).get('messages', [])
        except Exception:
            session_msgs = []
    from cyrene.runtime.config_store import get_current_ctx_limit
    from cyrene.model_runtime.client import message_token_estimate
    _ctx_limit = get_current_ctx_limit()
    context_window = {'messages': len(session_msgs), 'max': 40, 'tokens': sum((message_token_estimate(m) for m in session_msgs)) if session_msgs else 0, 'ctx_limit': _ctx_limit, 'trigger_tokens': int(_ctx_limit * 0.6) if _ctx_limit else 0, 'compacted_blocks': sum((1 for m in session_msgs if isinstance(m, dict) and m.get('compacted_block')))}
    archive_days = 0
    today_exchanges = 0
    if CONVERSATIONS_DIR.exists():
        archive_files = sorted(CONVERSATIONS_DIR.glob('*.md'))
        archive_days = len(archive_files)
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        today_file = CONVERSATIONS_DIR / f'{today_str}.md'
        if today_file.exists():
            try:
                raw = today_file.read_text(encoding='utf-8')
                today_exchanges = raw.count('## ') - 1
            except Exception:
                pass
    return {'soul': {'exists': soul_exists, 'path': str(get_soul_path()), 'sections': sections, 'temporary_count': temporary_count, 'temporary_expired': temporary_expired}, 'short_term': short_term, 'context_window': context_window, 'archive': {'days': archive_days, 'today_exchanges': max(0, today_exchanges)}}

async def _build_dashboard(ui_tz=None) -> dict:
    """Aggregate homepage data from memory, soul, archive, and scheduler state."""
    from cyrene.runtime import database as cy_db
    from cyrene.subagent import registry_snapshot
    subagent_registry = registry_snapshot()
    ui_tz = ui_tz or (datetime.now().astimezone().tzinfo or timezone.utc)
    now_local = datetime.now(ui_tz)
    st_entries = load_entries()
    try:
        tasks = await cy_db.get_all_tasks(project_repository._db_path)
    except Exception:
        logger.warning('Failed to load tasks from DB %s; task list empty', project_repository._db_path, exc_info=True)
        tasks = []
    today = now_local.strftime('%Y-%m-%d')
    soul_content = read_soul()
    soul_path = get_soul_path()
    soul_stat = soul_path.stat() if soul_path.exists() else None
    soul_lines = [line.strip() for line in soul_content.splitlines() if line.strip().startswith('- ')]
    recent_soul_items = soul_lines[-3:]
    recent_memories = sorted(st_entries, key=lambda entry: (str(entry.get('last_mentioned', '')), int(entry.get('mention_count', 0))), reverse=True)[:6]
    today_entries = [entry for entry in st_entries if str(entry.get('last_mentioned', '')).strip() == today]
    learned_today = sorted(today_entries, key=lambda entry: (int(entry.get('mention_count', 0)), abs(int(entry.get('emotional_valence', 0)))), reverse=True)[:4]
    session_msgs: list[dict[str, Any]] = []
    if STATE_FILE.exists():
        try:
            session_state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            session_msgs = session_state.get('messages', []) if isinstance(session_state, dict) else []
        except Exception:
            session_msgs = []
    session_usage = session_metrics.usage_totals(session_msgs)
    subagent_usage = session_metrics.merge_usage_totals(*[session_metrics.usage_totals(info.get('messages', [])) for info in subagent_registry.values()])
    reminder_items = []
    for task in sorted(tasks, key=lambda item: str(item.get('next_run') or '')):
        next_run = str(task.get('next_run') or '').strip()
        status = str(task.get('status') or '').strip()
        if not next_run or status not in {'active', 'paused'}:
            continue
        reminder_items.append({'id': str(task.get('id') or ''), 'prompt': str(task.get('prompt') or '').strip(), 'next_run': next_run, 'schedule_type': str(task.get('schedule_type') or '').strip(), 'status': status})
    reminder_items = reminder_items[:6]
    archive_snippets: list[dict[str, Any]] = []
    for filepath in sorted(CONVERSATIONS_DIR.glob('*.md'), reverse=True)[:7]:
        date_str = filepath.stem
        try:
            sections = _parse_archive_sections(filepath.read_text(encoding='utf-8'))
        except Exception:
            continue
        for section in reversed(sections):
            user_body = str(section.get('user_body', '')).strip()
            assistant_body = str(section.get('assistant_body', '')).strip()
            if user_body or assistant_body:
                archive_snippets.append({'date': date_str, 'title': str(section.get('round_title') or section.get('session_title') or '').strip(), 'user': user_body, 'assistant': assistant_body})
    archive_snippets = archive_snippets[:6]
    hist_days = 27
    day_from = (now_local - timedelta(days=hist_days)).strftime('%Y-%m-%d')
    day_to = today
    stats_rows = await cy_db.get_daily_stats_range(project_repository._db_path, day_from, day_to)
    stats_by_day = {str(row.get('day') or ''): row for row in stats_rows if str(row.get('day') or '').strip()}
    model_stats_rows = await cy_db.get_model_stats_range(project_repository._db_path, day_from, day_to)
    topic_rows = await cy_db.get_topic_counts_range(project_repository._db_path, day_from, day_to, limit=18)
    tool_rows = await cy_db.get_tool_counts_range(project_repository._db_path, day_from, day_to, limit=5)
    task_time = await cy_db.get_task_time_totals(project_repository._db_path)
    archive_day_count = await cy_db.count_stat_days(project_repository._db_path)
    historical_prompt = sum((r.get('prompt_tokens') or 0 for r in stats_by_day.values()))
    historical_completion = sum((r.get('completion_tokens') or 0 for r in stats_by_day.values()))
    historical_total = sum((r.get('total_tokens') or 0 for r in stats_by_day.values()))
    historical_cache_hit = sum((r.get('cache_hit_tokens') or 0 for r in stats_by_day.values()))
    historical_cache_miss = sum((r.get('cache_miss_tokens') or 0 for r in stats_by_day.values()))
    historical_requests = sum((r.get('llm_requests') or 0 for r in stats_by_day.values()))
    from cyrene.model_runtime.pricing import CNY_PER_USD, effective_price, estimate_cost
    total_spend_cny = 0.0
    total_spend_usd = 0.0
    for row in model_stats_rows:
        mdl = str(row.get('model') or '').strip().lower()
        pt = int(row.get('prompt_tokens') or 0)
        ct = int(row.get('completion_tokens') or 0)
        pricing = effective_price(mdl)
        cost = estimate_cost(pricing, pt, ct)
        if str(pricing.get('currency') or 'CNY').upper() == 'USD':
            total_spend_usd += cost
            total_spend_cny += cost * CNY_PER_USD
        else:
            total_spend_cny += cost
            total_spend_usd += cost / CNY_PER_USD
    spend_str = '<¥0.01' if 0 < total_spend_cny < 0.01 else f'¥{total_spend_cny:.2f}'
    emotion_by_day: dict[str, list[float]] = {}
    for entry in st_entries:
        day = str(entry.get('last_mentioned', '')).strip()
        if day:
            valence = int(entry.get('emotional_valence', 0) or 0)
            emotion_by_day.setdefault(day, []).append(valence)
    emotion_series = []
    for offset in range(hist_days, -1, -1):
        day = (now_local - timedelta(days=offset)).strftime('%Y-%m-%d')
        vals = emotion_by_day.get(day, [])
        avg = round(sum(vals) / len(vals), 2) if vals else 0.0
        emotion_series.append({'date': day, 'value': avg, 'count': len(vals)})
    token_timeline: dict[str, dict[str, int]] = {}
    for offset in range(hist_days, -1, -1):
        day = (now_local - timedelta(days=offset)).strftime('%Y-%m-%d')
        row = stats_by_day.get(day) or {}
        token_timeline[day] = {'prompt': int(row.get('prompt_tokens') or 0), 'completion': int(row.get('completion_tokens') or 0), 'requests': int(row.get('llm_requests') or 0)}
    heatmap_days = [(now_local - timedelta(days=offset)).strftime('%Y-%m-%d') for offset in range(hist_days, -1, -1)]
    heatmap_row_defs = [('00:00', 0, 4), ('04:00', 4, 8), ('08:00', 8, 12), ('12:00', 12, 16), ('16:00', 16, 20), ('20:00', 20, 24)]
    heatmap_column_map = {'00:00': 'activity_00_04', '04:00': 'activity_04_08', '08:00': 'activity_08_12', '12:00': 'activity_12_16', '16:00': 'activity_16_20', '20:00': 'activity_20_24'}
    heatmap_buckets: dict[str, list[int]] = {}
    for label, _, _ in heatmap_row_defs:
        column = heatmap_column_map[label]
        heatmap_buckets[label] = [int((stats_by_day.get(day) or {}).get(column) or 0) for day in heatmap_days]
    activity_heatmap = {'days': heatmap_days, 'rows': [{'label': label, 'values': heatmap_buckets[label]} for label, _, _ in heatmap_row_defs]}
    return {'today': {'learned': learned_today, 'learned_count': len(today_entries), 'memory_count': len(st_entries), 'archive_days': archive_day_count}, 'soul': {'path': str(soul_path), 'updated_at': datetime.fromtimestamp(soul_stat.st_mtime, tz=timezone.utc).isoformat() if soul_stat else '', 'recent_items': recent_soul_items, 'section_count': soul_content.count('\n## ') + (1 if soul_content.strip().startswith('# ') else 0)}, 'topic_cloud': topic_rows, 'emotion': emotion_series, 'usage': {'requests': historical_requests, 'tokens': session_metrics.format_tokens({'prompt_tokens': historical_prompt, 'completion_tokens': historical_completion, 'total_tokens': historical_total}), 'spend': spend_str, 'spend_cny': round(total_spend_cny, 6), 'spend_usd': round(total_spend_usd, 6), 'prompt_tokens': historical_prompt, 'completion_tokens': historical_completion, 'total_tokens': historical_total, 'cache_hit_tokens': historical_cache_hit, 'cache_miss_tokens': historical_cache_miss, 'total_messages': (session_usage.get('requests') or 0) + (subagent_usage.get('requests') or 0), 'active_days': sum((1 for row in stats_by_day.values() if int(row.get('llm_requests') or 0) > 0)), 'current_streak': _calc_current_streak(stats_by_day, today), 'longest_streak': _calc_longest_streak(stats_by_day), 'peak_hour': _calc_peak_hour(stats_by_day), 'task_time': task_time, 'top_tools': tool_rows, 'timeline': [{'date': day, 'prompt': values['prompt'], 'completion': values['completion'], 'requests': values['requests']} for day, values in token_timeline.items()]}, 'reminders': reminder_items, 'recent_memories': recent_memories, 'recent_archive': archive_snippets, 'activity_heatmap': activity_heatmap, 'model_stats': model_stats_rows}

def _extract_topic_terms(text: str, limit: int=12) -> list[str]:
    """Extract simple high-signal topic terms from mixed Chinese/English text."""
    source = (text or '').lower()
    english_stop = {'the', 'and', 'for', 'that', 'this', 'with', 'from', 'have', 'about', 'what', 'when', 'your', 'just', 'into', 'then', 'they', 'them', 'their', 'would', 'could', 'should', 'there', 'here', 'been', 'were', 'will', 'some', 'more', 'than', 'after', 'before', 'need', 'want', 'like', 'today', 'yesterday', 'tomorrow', 'really', 'also', 'maybe', 'because', 'http', 'https', 'assistant', 'cyrene', 'user'}
    chinese_stop = {'今天', '最近', '这个', '那个', '一下', '已经', '我们', '你们', '然后', '需要', '可以', '还是', '就是', '一个', '没有', '什么', '怎么', '如果', '现在', '自己', '因为', '所以', '以及', '但是', '进行', '相关', '问题', '工作', '页面', '功能', '内容'}
    tokens = re.findall('[\\u4e00-\\u9fff]{2,}|[a-z][a-z0-9_-]{2,}', source)
    results: list[str] = []
    for token in tokens:
        if token in english_stop or token in chinese_stop:
            continue
        if token.isascii() and len(token) < 4:
            continue
        results.append(token)
        if len(results) >= limit:
            break
    return results

def _read_recent_logs() -> list[dict]:
    """Read the most recent debug log file and convert to status log rows."""
    from cyrene.config import DATA_DIR
    if not DATA_DIR.exists():
        return _placeholder_logs()
    log_files = sorted(DATA_DIR.glob('debug_*.jsonl'), reverse=True)
    if not log_files:
        return _placeholder_logs()
    latest = log_files[0]
    rows: list[dict] = []
    try:
        with open(latest, 'r', encoding='utf-8') as fh:
            lines = fh.readlines()
    except Exception:
        return _placeholder_logs()
    for line in lines[-40:]:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        kind = entry.get('type', 'info')
        ts = entry.get('timestamp', '')[11:19]
        if kind == 'llm_call':
            caller = entry.get('caller', '?')
            phase = entry.get('phase', '?')
            duration = entry.get('duration_ms', 0)
            rows.append({'t': ts, 'lvl': 'info', 'msg': f'{caller} · {phase} · {duration}ms'})
        elif kind == 'tool_call':
            caller = entry.get('caller', '?')
            tool = entry.get('tool', '?')
            rows.append({'t': ts, 'lvl': 'ok', 'msg': f'{caller} → {tool}'})
        elif kind == 'session_start':
            rows.append({'t': ts, 'lvl': 'info', 'msg': 'session started'})
    return list(reversed(rows[-20:]))

def _placeholder_logs() -> list[dict]:
    now = datetime.now(timezone.utc).strftime('%H:%M:%S')
    return [{'t': now, 'lvl': 'info', 'msg': 'no debug logs yet — verbose mode is enabled, logs appear after agent runs'}]

def _build_settings_meta() -> dict:
    return {'sections': [{'id': 'general', 'label': 'General'}, {'id': 'search', 'label': 'Search'}, {'id': 'channels', 'label': 'Channels'}, {'id': 'models', 'label': 'Models'}, {'id': 'agents', 'label': 'Agents'}, {'id': 'appearance', 'label': 'Appearance'}, {'id': 'capabilities', 'label': 'Capabilities'}, {'id': 'data', 'label': 'Data'}, {'id': 'about', 'label': 'About'}]}

def _build_config() -> dict:
    settings = get_web_settings()
    from cyrene.runtime.config_store import get_settings_revision
    live_model, live_base_url = project_runtime._live_llm_config()
    return {'revision': get_settings_revision(), 'model': live_model, 'base_url': live_base_url, 'assistant_name': ASSISTANT_NAME, 'base_dir': str(BASE_DIR), 'data_dir': str(DATA_DIR), 'soul_path': str(SOUL_PATH), 'workspace_dir': str(WORKSPACE_DIR), 'soul_content': _read_soul(), 'search_mode': 'builtin', 'search_external_url': '', 'spawn_policy': settings.get('spawn_policy', 'conservative'), 'heartbeat_interval': settings.get('heartbeat_interval', 1800), 'background_skill_learning': settings.get('background_skill_learning', True), 'agent_proactive': settings.get('agent_proactive', True), 'app_language': settings.get('app_language', ''), 'timezone': settings.get('timezone', 'Asia/Shanghai'), 'performance_mode': settings.get('performance_mode', False), 'external_agent_proxy_enabled': settings.get('external_agent_proxy_enabled', False), 'external_agent_proxy_port': settings.get('external_agent_proxy_port', 7897), 'subagent_execution_max_tool_calls': settings.get('subagent_execution_max_tool_calls', 200), 'subagent_execution_max_wall_seconds': settings.get('subagent_execution_max_wall_seconds', 1800), 'subagent_execution_no_progress_turns': settings.get('subagent_execution_no_progress_turns', 3), 'subagent_execution_checkpoint_calls': settings.get('subagent_execution_checkpoint_calls', 20), 'subagent_execution_max_cost_usd': settings.get('subagent_execution_max_cost_usd', 5.0), 'subagent_execution_max_context_tokens': settings.get('subagent_execution_max_context_tokens', 0), 'subagent_discussion_max_rounds': settings.get('subagent_discussion_max_rounds', 5), 'subagent_discussion_max_messages_per_agent': settings.get('subagent_discussion_max_messages_per_agent', 4), 'subagent_discussion_max_total_messages': settings.get('subagent_discussion_max_total_messages', 20), 'subagent_discussion_max_message_chars': settings.get('subagent_discussion_max_message_chars', 2000), 'subagent_discussion_max_wall_seconds': settings.get('subagent_discussion_max_wall_seconds', 600), 'subagent_discussion_max_tool_calls': settings.get('subagent_discussion_max_tool_calls', 50), 'subagent_discussion_no_new_info_rounds': settings.get('subagent_discussion_no_new_info_rounds', 2), 'notify_telegram': settings.get('notify_telegram', True), 'notify_wechat': settings.get('notify_wechat', True), 'redact_secrets': settings.get('redact_secrets', True), 'beta_updates': settings.get('beta_updates', False), 'auto_update': settings.get('auto_update', True), 'budget_enabled': settings.get('budget_enabled', False), 'codex_budget_enabled': settings.get('codex_budget_enabled', True), 'budget_monthly': settings.get('budget_monthly', 50), 'budget_currency': settings.get('budget_currency', 'CNY'), 'budget_action': settings.get('budget_action', 'warn'), 'budget_mode': settings.get('budget_mode', 'normal'), 'budget_start_day': settings.get('budget_start_day', 1), 'search_port': str(SEARXNG_PORT), 'search_host': SEARXNG_HOST}

def _build_context_chips() -> list[dict]:
    """Build context chips reflecting current SOUL.md and workspace state."""
    from cyrene.runtime.settings_store import is_workspace_active, is_soul_active
    chips = []
    if is_soul_active():
        chips.append({'icon': '🧠', 'label': 'SOUL.md', 'key': 'soul'})
    if is_workspace_active():
        chips.append({'icon': '📁', 'label': 'workspace', 'key': 'workspace'})
    return chips

def _build_search_config() -> dict:
    return {'search_mode': 'builtin', 'search_external_url': '', 'auto_start_enabled': os.getenv('SEARXNG_AUTO_START', '1') not in ('0', 'false', 'no')}

def _load_messages() -> list[dict]:
    msgs = _load_state_messages()
    if msgs:
        result = []
        for m in msgs:
            role = m.get('role', '')
            if role not in ('user', 'assistant'):
                continue
            content = m.get('content', '')
            if not content or not content.strip():
                continue
            result.append({'role': role, 'content': content})
        if result:
            return result
    archive_msgs = _parse_conversation_archive()
    if archive_msgs:
        return archive_msgs
    return []

def _load_state_messages() -> list[dict]:
    if not STATE_FILE.exists():
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        return data.get('messages', []) or []
    except Exception:
        return []

def _infer_subagent_entries(raw_msgs: list[dict], registry: dict[str, dict]) -> dict[str, dict]:
    entries: dict[str, dict] = _snapshot_entries_from_messages(raw_msgs)
    for agent_id, info in registry.items():
        _merge_subagent_record(entries, agent_id, dict(info))
    for entry in entries.values():
        entry.setdefault('messages', [])
    spawned: dict[str, dict[str, str]] = {}
    for msg in raw_msgs:
        for tc in msg.get('tool_calls') or []:
            fn = tc.get('function', {})
            if fn.get('name') != 'spawn_subagent':
                continue
            args = session_metrics.safe_json_loads(fn.get('arguments') or '{}')
            if not isinstance(args, dict):
                continue
            agent_id = str(args.get('agent_id') or '').strip()
            if not agent_id:
                continue
            spawned[agent_id] = {'task': str(args.get('task') or ''), 'round_id': str(msg.get('round_id', '')).strip()}
    for agent_id, meta in spawned.items():
        entry = entries.setdefault(agent_id, {})
        meta_round_id = str(meta.get('round_id', '')).strip()
        existing_round_id = str(entry.get('round_id', '')).strip()
        if meta_round_id and existing_round_id and (meta_round_id != existing_round_id):
            entry['task'] = meta['task'] or entry.get('task', '')
            entry['round_id'] = meta_round_id
            entry['status'] = 'running'
            entry['result'] = ''
            entry['messages'] = []
            entry['created_at'] = None
            entry['updated_at'] = None
            continue
        entry.setdefault('task', meta['task'])
        entry.setdefault('round_id', meta_round_id)
        entry.setdefault('status', 'done')
        entry.setdefault('result', '')
        entry.setdefault('messages', [])
        entry.setdefault('created_at', None)
        entry.setdefault('updated_at', None)
    inbox_meta = _scan_inbox_agents()
    for agent_id, meta in inbox_meta.items():
        entry = entries.setdefault(agent_id, {})
        entry.setdefault('task', spawned.get(agent_id, {}).get('task', 'Discuss with other subagents'))
        entry.setdefault('status', 'done')
        entry.setdefault('result', '')
        if not entry.get('messages'):
            entry['messages'] = [{}] * int(meta.get('message_count') or 0)
        if meta.get('created_at') and (not entry.get('created_at')):
            entry['created_at'] = meta['created_at']
        if meta.get('updated_at') and (not entry.get('updated_at')):
            entry['updated_at'] = meta['updated_at']
        if meta.get('round_id') and (not entry.get('round_id')):
            entry['round_id'] = meta['round_id']
    return entries

def _parse_conversation_archive() -> list[dict]:
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    filepath = CONVERSATIONS_DIR / f'{today}.md'
    if not filepath.exists():
        return []
    content = filepath.read_text(encoding='utf-8')
    messages = []
    current_user = None
    current_lines: list[str] = []
    in_assistant = False
    for line in content.split('\n'):
        if line.startswith('**User**: '):
            if current_user and current_lines:
                messages.append({'role': 'user', 'content': current_user})
                messages.append({'role': 'assistant', 'content': '\n'.join(current_lines).strip()})
            current_user = line[len('**User**: '):].strip()
            current_lines = []
            in_assistant = False
        elif line.startswith('**') and '**: ' in line and (not line.startswith('**User**')):
            in_assistant = True
            idx = line.index('**: ')
            current_lines = [line[idx + len('**: '):]]
        elif in_assistant:
            if line.strip() == '---':
                if current_user and current_lines:
                    messages.append({'role': 'user', 'content': current_user})
                    messages.append({'role': 'assistant', 'content': '\n'.join(current_lines).strip()})
                current_user = None
                current_lines = []
                in_assistant = False
            else:
                current_lines.append(line)
    if current_user and current_lines:
        messages.append({'role': 'user', 'content': current_user})
        messages.append({'role': 'assistant', 'content': '\n'.join(current_lines).strip()})
    return messages

def _read_soul() -> str:
    try:
        if SOUL_PATH.exists():
            return SOUL_PATH.read_text(encoding='utf-8')
    except Exception:
        pass
    return ''

def _model_pricing(model: str='') -> dict[str, float] | None:
    """Return token pricing for an actual response model, or the active model.

    Missing or invalid configured prices use the built-in catalog when known;
    unknown models resolve to zero.
    """
    from cyrene.model_runtime.pricing import effective_price
    return effective_price(str(model or project_runtime._get_model()))

def _calc_spend(usage: dict[str, int | None] | None, model: str='') -> str:
    if not isinstance(usage, dict):
        return '—'
    pricing = _model_pricing(model)
    if pricing is None:
        return '—'
    prompt_tokens = usage.get('prompt_tokens')
    completion_tokens = usage.get('completion_tokens')
    cache_hit_tokens = usage.get('prompt_cache_hit_tokens')
    cache_miss_tokens = usage.get('prompt_cache_miss_tokens')
    from cyrene.model_runtime.pricing import estimate_cost
    cost = estimate_cost(pricing, int(prompt_tokens or 0), int(completion_tokens or 0), cache_hit_tokens=int(cache_hit_tokens or 0), cache_miss_tokens=int(cache_miss_tokens or 0))
    currency = pricing.get('currency', 'USD')
    if currency == 'CNY':
        sym = '¥'
        threshold = 0.07
    else:
        sym = '$'
        threshold = 0.01
    if cost == 0:
        return f'{sym}0.00'
    if cost < threshold:
        return f'<{sym}{threshold:.2g}'
    return f'{sym}{cost:.2f}'

def _calc_messages_spend(messages: list[dict[str, Any]]) -> str:
    """Sum usage with each response's actual model price.

    Fallback can change models between calls in one session.  Aggregating all
    tokens first and applying the configured primary price misprices those
    mixed-model sessions, so calculate each recorded response independently.
    """
    from cyrene.model_runtime.pricing import CNY_PER_USD, estimate_cost
    totals = {'CNY': 0.0, 'USD': 0.0}
    found = False
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        usage = message.get('usage')
        if not isinstance(usage, dict):
            continue
        model = str(usage.get('model') or message.get('model') or project_runtime._get_model()).strip()
        pricing = _model_pricing(model)
        if pricing is None:
            continue
        found = True
        cost = estimate_cost(pricing, int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0), cache_hit_tokens=int(usage.get('prompt_cache_hit_tokens') or 0), cache_miss_tokens=int(usage.get('prompt_cache_miss_tokens') or 0))
        currency = str(pricing.get('currency') or 'CNY').upper()
        totals[currency if currency in totals else 'CNY'] += cost
    if not found:
        return '—'
    if totals['CNY'] and totals['USD']:
        cost = totals['CNY'] + totals['USD'] * CNY_PER_USD
        currency = 'CNY'
    elif totals['USD']:
        cost = totals['USD']
        currency = 'USD'
    else:
        cost = totals['CNY']
        currency = 'CNY'
    symbol = '¥' if currency == 'CNY' else '$'
    threshold = 0.07 if currency == 'CNY' else 0.01
    if cost == 0:
        return f'{symbol}0.00'
    return f'<{symbol}{threshold:.2g}' if cost < threshold else f'{symbol}{cost:.2f}'

def _calc_current_streak(stats_by_day: dict[str, dict], today: str) -> int:
    streak = 0
    for offset in range(366):
        day = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=offset)).strftime('%Y-%m-%d')
        row = stats_by_day.get(day)
        if row and int(row.get('llm_requests') or 0) > 0:
            streak += 1
        else:
            break
    return streak

def _calc_longest_streak(stats_by_day: dict[str, dict]) -> int:
    longest = 0
    current = 0
    for offset in range(365):
        day = (datetime.now() - timedelta(days=offset)).strftime('%Y-%m-%d')
        row = stats_by_day.get(day)
        if row and int(row.get('llm_requests') or 0) > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
_ACTIVITY_COLUMNS = [('activity_00_04', '00:00-04:00'), ('activity_04_08', '04:00-08:00'), ('activity_08_12', '08:00-12:00'), ('activity_12_16', '12:00-16:00'), ('activity_16_20', '16:00-20:00'), ('activity_20_24', '20:00-24:00')]

def _calc_peak_hour(stats_by_day: dict[str, dict]) -> str:
    totals: dict[str, int] = {}
    for col, _label in _ACTIVITY_COLUMNS:
        totals[col] = sum((int(row.get(col) or 0) for row in stats_by_day.values()))
    best_col = max(totals, key=totals.get) if any(totals.values()) else ''
    for col, label in _ACTIVITY_COLUMNS:
        if col == best_col:
            return label
    return '—'

def _build_shells_from_messages(raw_msgs: list[dict]) -> list[dict]:
    """Extract bash/shell tool calls from raw messages and build shell entries."""
    shells: list[dict] = []
    tool_results: dict[str, str] = {}
    for msg in raw_msgs:
        if msg.get('role') == 'tool' and msg.get('tool_call_id'):
            tool_results[str(msg['tool_call_id'])] = str(msg.get('content') or '')
    shell_index = 0
    for msg in raw_msgs:
        if msg.get('role') != 'assistant':
            continue
        for tc in msg.get('tool_calls') or []:
            fn = tc.get('function', {})
            name = fn.get('name', '')
            if name.lower() not in ('bash', 'shell', 'cmd', 'terminal'):
                continue
            args_str = fn.get('arguments', '{}')
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            cmd = args.get('command') or args.get('cmd') or json.dumps(args)
            cwd = args.get('cwd') or args.get('workdir') or 'workspace/'
            result = tool_results.get(str(tc.get('id')), '')
            lines: list[dict] = [{'kind': 'shell-prompt', 'text': f'$ {cmd}'}]
            if result:
                for line in result.strip().split('\n')[:30]:
                    lines.append({'kind': 'shell-out', 'text': line})
            else:
                lines.append({'kind': 'shell-out', 'text': '(running…)'})
            shells.append({'id': f'shell_{shell_index}', 'cwd': cwd, 'pid': '—', 'lines': lines})
            shell_index += 1
    return shells

def _build_tool_nodes_for_owner(owner_node_id: str, owner_title: str, owner_x: int, owner_y: int, raw_messages: list[dict], recent_events: list[dict], caller_prefix: str, x: int, base_y: int, owner_completed: bool=False) -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    tool_outputs = session_metrics.tool_output_map(raw_messages)
    tool_output_ids = session_metrics.tool_output_ids(raw_messages)
    tool_index = 0
    for msg_index, msg in enumerate(raw_messages):
        tool_calls = msg.get('tool_calls') or []
        for call_index, tc in enumerate(tool_calls):
            fn = tc.get('function', {})
            raw_args = fn.get('arguments') or '{}'
            parsed_args = session_metrics.safe_json_loads(raw_args) if isinstance(raw_args, str) else raw_args
            tool_call_id = str(tc.get('id') or '')
            output = tool_outputs.get(tool_call_id, '')
            has_output = tool_call_id in tool_output_ids
            has_followup = any((later.get('role') in {'assistant', 'tool', 'user'} for later in raw_messages[msg_index + 1:]))
            status = 'done' if has_output or has_followup or owner_completed else 'running'
            if has_output:
                output_detail = output or 'Completed with no captured output.'
            elif status == 'done':
                output_detail = 'Completed after follow-up activity; no tool output was captured.'
            else:
                output_detail = 'Running…'
            nid = f'{owner_node_id}_tool_{msg_index}_{call_index}'
            nodes.append({'id': nid, 'kind': 'tool', 'x': x, 'y': base_y + tool_index * 112, 'title': fn.get('name', 'tool'), 'subtitle': session_metrics.summarize_text(str(raw_args), 36) if raw_args else '', 'status': status, 'detail': {'name': fn.get('name', 'tool'), 'owner': owner_title, 'input': parsed_args if parsed_args is not None else raw_args, 'output': output_detail, 'duration': '—'}})
            edges.append({'from': owner_node_id, 'to': nid, 'kind': 'active' if status == 'running' else None})
            tool_index += 1
    overlay_events = [event for event in recent_events if event.get('type') == 'tool_call' and str(event.get('caller', '')).startswith(caller_prefix)][-6:]
    for event_index, event in enumerate(overlay_events):
        event_signature = session_metrics.tool_args_signature(event.get('args', {}))
        if any((node['detail'].get('name') == event.get('tool') and session_metrics.tool_args_signature(node['detail'].get('input', {})) == event_signature for node in nodes)):
            continue
        nid = f'{owner_node_id}_live_tool_{event_index}'
        nodes.append({'id': nid, 'kind': 'tool', 'x': x, 'y': base_y + tool_index * 112, 'title': event.get('tool', 'tool'), 'subtitle': session_metrics.summarize_text(json.dumps(event.get('args', {}), ensure_ascii=False), 36), 'status': 'done', 'detail': {'name': event.get('tool', 'tool'), 'owner': owner_title, 'input': event.get('args', {}), 'output': event.get('result_preview', 'Completed.'), 'duration': 'recent', 'eventKey': f"{event.get('tool')}::{event_signature}"}})
        edges.append({'from': owner_node_id, 'to': nid})
        tool_index += 1
    return (nodes, edges)

def _count_tool_nodes_for_owner(raw_messages: list[dict], recent_events: list[dict], caller_prefix: str) -> int:
    count = sum((len(msg.get('tool_calls') or []) for msg in raw_messages))
    message_keys = {(tc.get('function', {}).get('name', 'tool'), json.dumps(session_metrics.safe_json_loads(tc.get('function', {}).get('arguments') or '{}') if isinstance(tc.get('function', {}).get('arguments'), str) else tc.get('function', {}).get('arguments') or {}, ensure_ascii=False, sort_keys=True)) for msg in raw_messages for tc in msg.get('tool_calls') or []}
    overlay_events = [event for event in recent_events if event.get('type') == 'tool_call' and str(event.get('caller', '')).startswith(caller_prefix)][-6:]
    overlay_count = 0
    for event in overlay_events:
        event_key = (event.get('tool', 'tool'), json.dumps(event.get('args', {}), ensure_ascii=False, sort_keys=True))
        if event_key in message_keys:
            continue
        overlay_count += 1
    return count + overlay_count

def _agent_lane_height(tool_count: int) -> int:
    base_height = 86
    if tool_count <= 0:
        return base_height
    return max(base_height, base_height + (tool_count - 1) * 112)

def _build_comm_edges(agent_node_ids: dict[str, str], agent_entries: dict[str, dict[str, Any]] | None=None, round_id: str='', persisted_messages: list[dict[str, Any]] | None=None) -> list[dict]:
    edges: list[dict] = []
    if not agent_node_ids:
        return edges
    pair_messages: dict[tuple[str, str], list[dict]] = {}
    content_index: dict[tuple[str, str, str], int] = {}

    def _add_message_to_pair(from_agent: str, to_agent: str, body: str, *, label: str='chat', timestamp: str='', source: str='', summary: str='', priority: str='normal', raw_timestamp: str='') -> None:
        if from_agent not in agent_node_ids or to_agent not in agent_node_ids:
            return
        if not body.strip():
            return
        pair_key = (from_agent, to_agent)
        content_key = (from_agent, to_agent, body[:80])
        if content_key in content_index:
            idx = content_index[content_key]
            existing_msg = edges[idx].setdefault('message', {})
            if (not existing_msg.get('time') or existing_msg.get('time') == '—') and timestamp:
                existing_msg['time'] = session_metrics.short_time(timestamp)
            if summary and (not existing_msg.get('summary')):
                existing_msg['summary'] = summary
            if priority == 'high':
                existing_msg['priority'] = 'high'
            edges[idx]['weight'] = edges[idx].get('weight', 1) + 1
            pair_messages.setdefault(pair_key, []).append({'from': from_agent, 'to': to_agent, 'body': body, 'label': label, 'time': session_metrics.short_time(timestamp) if timestamp else '—', 'summary': summary, 'priority': priority, 'source': source})
            return
        edge_summary = summary if summary else session_metrics.summarize_text(body, 90)
        edge_label = label
        if priority == 'high':
            edge_label = label + ' !'
        edge_entry = {'from': agent_node_ids[from_agent], 'to': agent_node_ids[to_agent], 'kind': 'comm', 'label': edge_label, 'weight': 1, 'message': {'time': session_metrics.short_time(timestamp) if timestamp else '—', 'raw_timestamp': raw_timestamp or timestamp or '', 'summary': edge_summary, 'body': body, 'source': source or 'tool_call', 'msg_type': label, 'priority': priority}}
        edges.append(edge_entry)
        content_index[content_key] = len(edges) - 1
        pair_messages.setdefault(pair_key, []).append({'from': from_agent, 'to': to_agent, 'body': body, 'label': label, 'time': session_metrics.short_time(timestamp) if timestamp else '—', 'raw_timestamp': raw_timestamp or timestamp or '', 'summary': edge_summary, 'priority': priority, 'source': source})
    for agent_name, info in (agent_entries or {}).items():
        if agent_name not in agent_node_ids:
            continue
        messages = info.get('messages', []) or []
        tool_outputs = {str(msg.get('tool_call_id') or ''): str(msg.get('content') or '') for msg in messages if isinstance(msg, dict) and msg.get('role') == 'tool' and msg.get('tool_call_id')}
        for msg in messages:
            if not isinstance(msg, dict) or msg.get('role') != 'assistant':
                continue
            for tc in msg.get('tool_calls') or []:
                fn = tc.get('function', {}) if isinstance(tc, dict) else {}
                tool_name = str(fn.get('name') or '').strip()
                if tool_name not in ('send_agent_message', 'broadcast_agent_message'):
                    continue
                args = session_metrics.safe_json_loads(fn.get('arguments') or '{}')
                if not isinstance(args, dict):
                    continue
                output = tool_outputs.get(str(tc.get('id') or ''), '')
                output_lower = output.lower()
                if output and 'message sent to' not in output_lower and ('broadcast sent to' not in output_lower):
                    continue
                body = str(args.get('content') or '')
                if tool_name == 'broadcast_agent_message':
                    peer_ids = [aid for aid in agent_node_ids if aid != agent_name]
                    for peer_id in peer_ids:
                        _add_message_to_pair(agent_name, peer_id, body, label='progress', source='tool_call')
                else:
                    to_agent = str(args.get('to') or '').strip()
                    _add_message_to_pair(agent_name, to_agent, body, source='tool_call')
    for payload in persisted_messages or []:
        if not isinstance(payload, dict):
            continue
        if round_id and str(payload.get('round_id', '')).strip() != round_id:
            continue
        _add_message_to_pair(str(payload.get('from', '')).strip(), str(payload.get('to', '')).strip(), str(payload.get('content', '')), label=str(payload.get('type', 'chat') or 'chat'), timestamp=str(payload.get('timestamp', '') or ''), source='snapshot_log', summary=str(payload.get('summary', '') or ''), priority=str(payload.get('priority', 'normal') or 'normal'))
    for agent_name in agent_node_ids:
        inbox_dir = DATA_DIR / 'inbox' / agent_name
        if not inbox_dir.exists():
            continue
        for msg_file in sorted(inbox_dir.glob('msg_*.json')):
            try:
                payload = json.loads(msg_file.read_text(encoding='utf-8'))
            except Exception:
                continue
            from_agent = str(payload.get('from', ''))
            to_agent = str(payload.get('to', ''))
            if round_id and str(payload.get('round_id', '')) != round_id:
                continue
            _add_message_to_pair(from_agent, to_agent, str(payload.get('content', '')), label=str(payload.get('type', 'chat') or 'chat'), timestamp=str(payload.get('timestamp', '') or ''), source='inbox_log', summary=str(payload.get('summary', '') or ''), priority=str(payload.get('priority', 'normal') or 'normal'))
    for i, edge in enumerate(edges):
        pair = None
        for (f, t), msgs in pair_messages.items():
            if edge['from'] == agent_node_ids.get(f) and edge['to'] == agent_node_ids.get(t):
                pair = (f, t)
                edge['messages'] = msgs
                break
        if pair:
            edge['weight'] = len(pair_messages.get(pair, []))
    return edges

def _scan_inbox_agents() -> dict[str, dict[str, Any]]:
    agents: dict[str, dict[str, Any]] = {}
    inbox_root = DATA_DIR / 'inbox'
    if not inbox_root.exists():
        return agents
    for inbox_dir in sorted((path for path in inbox_root.iterdir() if path.is_dir())):
        agent_id = inbox_dir.name
        timestamps: list[str] = []
        round_ids: list[str] = []
        msg_count = 0
        for msg_file in sorted(inbox_dir.glob('msg_*.json')):
            try:
                payload = json.loads(msg_file.read_text(encoding='utf-8'))
            except Exception:
                continue
            msg_count += 1
            timestamp = payload.get('timestamp')
            if isinstance(timestamp, str) and timestamp:
                timestamps.append(timestamp)
            round_id = str(payload.get('round_id', '')).strip()
            if round_id:
                round_ids.append(round_id)
        if msg_count == 0:
            continue
        timestamps.sort()
        agents[agent_id] = {'message_count': msg_count, 'created_at': timestamps[0] if timestamps else None, 'updated_at': timestamps[-1] if timestamps else None, 'round_id': round_ids[-1] if round_ids else ''}
    return agents

__all__ = ['_ACTIVITY_COLUMNS', '_agent_lane_height', '_build_archive_sessions', '_build_comm_edges', '_build_config', '_build_context_chips', '_build_current_session', '_build_dashboard', '_build_entities_summary', '_build_live_flow', '_build_live_flow_round', '_build_memory', '_build_search_config', '_build_sessions', '_build_settings_meta', '_build_shells_from_messages', '_build_simple_flow', '_build_status', '_build_summary', '_build_tool_nodes_for_owner', '_build_ui_data', '_build_user', '_calc_current_streak', '_calc_longest_streak', '_calc_messages_spend', '_calc_peak_hour', '_calc_spend', '_convert_messages', '_count_tool_nodes_for_owner', '_delete_chat_session', '_empty_session', '_events_for_round', '_extract_topic_terms', '_infer_subagent_entries', '_is_hidden_internal_message', '_is_summary_agent_id', '_iter_flow_snapshots', '_latest_round_id_from_messages', '_load_messages', '_load_state_messages', '_merge_subagent_record', '_messages_from_archive_sections', '_model_pricing', '_normalize_search_text', '_parse_archive_file', '_parse_archive_sections', '_parse_archive_session_title', '_parse_conversation_archive', '_placeholder_logs', '_read_recent_logs', '_read_soul', '_registry_status_from_ui', '_related_round_agent_names', '_resolve_local_username', '_resolve_ui_tz', '_round_id_from_messages', '_round_registry_for_flow', '_scan_inbox_agents', '_search_matches', '_search_snippet', '_search_workbench_items', '_session_started_at', '_snapshot_comm_messages_from_messages', '_snapshot_entries_from_messages', '_subagent_cards_from_registry', '_subagent_matches_round', '_synthetic_live_round', '_write_archive_sections']
