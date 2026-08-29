"""Workbench search and presentation-model builders."""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cyrene.core.plugin import application_plugin_service
from cyrene.config import (
    ASSISTANT_NAME,
    BASE_DIR,
    DATA_DIR,
    DB_PATH,
    WORKSPACE_DIR,
)
from cyrene.localization import localized
from cyrene.runtime.onboarding import get_onboarding_status
from cyrene.runtime.version import get_version_label
from cyrene.workbench.projects import project_repository, project_runtime
from cyrene.workbench.sessions import session_metrics, session_view
from cyrene.workbench.sessions.session_presentation import (
    WorkbenchSessionError,
    WorkbenchSessionPresentation,
)

logger = logging.getLogger(__name__)


def _memory_service():
    return application_plugin_service("memory")


def load_entries() -> list[dict[str, Any]]:
    service = _memory_service()
    return service.short_term_entries() if service is not None else []


def _soul_presentation() -> dict[str, Any]:
    service = application_plugin_service("soul")
    projection = getattr(service, "presentation_state", None)
    if not callable(projection):
        return {
            "path": "",
            "content": "",
            "updated_at": "",
            "recent_items": [],
            "section_count": 0,
        }
    try:
        value = projection()
    except Exception:
        logger.warning("Soul UI projection is unavailable", exc_info=True)
        return {
            "path": "",
            "content": "",
            "updated_at": "",
            "recent_items": [],
            "section_count": 0,
        }
    return dict(value) if isinstance(value, dict) else {}


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

async def _search_workbench_items(
    query: str,
    types: set[str],
    per_type_limit: int,
    db_path: str | Path = DB_PATH,
) -> dict[str, list[dict[str, Any]]]:
    """Search across Workbench data sources and return grouped results."""
    groups: dict[str, list[dict[str, Any]]] = {t: [] for t in types}
    if not query:
        return groups
    store = await asyncio.to_thread(project_repository._read_workbench_store_lightweight)
    projects = store.get('projects', [])
    project_by_id: dict[str, dict[str, Any]] = {str(p.get('id') or ''): p for p in projects if p.get('id')}
    project_names: dict[str, str] = {
        pid: str(p.get('name') or p.get('id') or '').strip()
        for pid, p in project_by_id.items()
    }
    if 'project' in types:
        for project in projects:
            pid = str(project.get('id') or '')
            name = str(project.get('name') or '')
            desc = str(project.get('description') or '')
            summary = str((project.get('context') or {}).get('summary') or '')
            if _search_matches(query, name) or _search_matches(query, desc) or _search_matches(query, summary):
                groups['project'].append({'id': pid, 'type': 'project', 'title': name, 'titleKey': '' if name else 'search.default.workspace', 'snippet': _search_snippet(desc or summary, query), 'projectId': pid, 'projectName': project_names.get(pid, ''), 'projectNameDefault': not bool(project_names.get(pid, '')), 'updatedAt': project.get('updatedAt') or project.get('createdAt') or ''})
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
                    groups['task'].append({'id': sid, 'type': 'task', 'title': title, 'titleKey': '' if title else 'search.default.newTask', 'snippet': _search_snippet(goal or title, query), 'projectId': pid, 'projectName': project_names.get(pid, ''), 'projectNameDefault': not bool(project_names.get(pid, '')), 'sessionId': sid, 'status': session.get('status') or 'idle', 'updatedAt': session.get('updatedAt') or session.get('createdAt') or ''})
                    if len(groups['task']) >= per_type_limit:
                        break
            if len(groups['task']) >= per_type_limit:
                break
    if 'chat' in types:
        try:
            from cyrene.workbench.chat.chat_application import chat_preview
            from cyrene.workbench.chat.chat_repository import ChatRepository

            def _search_chats() -> list[dict[str, Any]]:
                found: list[dict[str, Any]] = []
                chats_payload = ChatRepository(str(db_path)).read()
                for chat in chats_payload.get('chats', []):
                    if str(chat.get('kind') or 'chat') != 'chat':
                        continue
                    chat_id = str(chat.get('id') or '')
                    pid = str(chat.get('projectId') or '')
                    title = str(chat.get('title') or '')
                    preview = str(chat.get('preview') or chat_preview(chat))
                    matched = _search_matches(query, title) or _search_matches(query, preview)
                    if not matched and isinstance(chat.get('messages'), list):
                        for message in chat['messages']:
                            if _search_matches(query, str(message.get('content') or message.get('body') or '')):
                                matched = True
                                break
                    if not matched:
                        continue
                    project_name = project_names.get(pid, '')
                    found.append({'id': chat_id, 'type': 'chat', 'title': title, 'titleKey': '' if title else 'search.default.newChat', 'snippet': _search_snippet(preview or title, query), 'projectId': pid, 'projectName': project_name, 'projectNameDefault': not bool(project_name), 'chatId': chat_id, 'updatedAt': chat.get('updatedAt') or chat.get('createdAt') or ''})
                    if len(found) >= per_type_limit:
                        break
                return found
            groups['chat'].extend(await asyncio.to_thread(_search_chats))
        except Exception:
            logger.exception('Workbench chat search failed')
    return groups

def _resolve_ui_tz(tz_name: str=''):
    name = str(tz_name or '').strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc

async def _build_ui_data(tz_name: str='', db_path: str | Path | None = None) -> dict:
    """Assemble the full DATA payload the SPA expects."""
    resolved_db_path = str(db_path or project_repository._db_path or DB_PATH)
    sessions = await asyncio.to_thread(_build_sessions, resolved_db_path)
    ui_tz = _resolve_ui_tz(tz_name)
    return {'user': _build_user(), 'assistantName': ASSISTANT_NAME, 'appVersion': get_version_label(), 'dashboard': await _build_dashboard(ui_tz, resolved_db_path, sessions=sessions), 'sessions': sessions, 'status': await _build_status(resolved_db_path, sessions=sessions), 'settings': _build_settings_meta(), 'onboarding': get_onboarding_status(), 'entities': await _build_entities_summary(resolved_db_path)}

async def _build_entities_summary(db_path: str | Path | None = None) -> list:
    """Return active entities for the SPA bootstrap payload."""
    try:
        entities = application_plugin_service("entities")
        if entities is None:
            return []
        return await entities.list(
            status='active',
            limit=100,
        )
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

async def _delete_chat_session(
    session_id: str,
    db_path: str | Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Delete one Workbench chat and its ContextTree.

    Kept as a narrow compatibility seam for callers that have not yet moved to
    :class:`WorkbenchSessionPresentation`; retired singleton/archive identifiers
    are intentionally unsupported.
    """
    resolved_db_path = str(db_path or project_repository._db_path or DB_PATH)
    try:
        await asyncio.to_thread(
            WorkbenchSessionPresentation(
                resolved_db_path,
                memory_service=_memory_service(),
            ).delete,
            str(session_id),
        )
    except WorkbenchSessionError as exc:
        return (
            {"error": exc.message, "code": exc.code},
            exc.status_code,
        )
    return (
        {
            'ok': True,
            'sessions': await asyncio.to_thread(_build_sessions, resolved_db_path),
        },
        200,
    )


def _build_sessions(db_path: str | Path | None = None) -> list[dict]:
    """Return SQLite Workbench chats enriched from their durable ContextTrees."""
    resolved_db_path = str(db_path or project_repository._db_path or DB_PATH)
    return WorkbenchSessionPresentation(resolved_db_path).list()


def build_sessions(db_path: str | Path | None = None) -> list[dict]:
    """Public Workbench session-list projection."""
    return _build_sessions(db_path)

def _build_summary(raw_msgs: list[dict]) -> dict:
    usage = session_metrics.usage_totals(raw_msgs)
    return {'tokens': session_metrics.format_tokens(usage), 'spend': _calc_messages_spend(raw_msgs), 'toolCalls': session_view.count_tool_calls(raw_msgs), 'requests': usage['requests'], 'total_tokens': usage['total_tokens']}

def _build_current_session(db_path: str | Path | None = None) -> dict | None:
    """Return the most recently updated Workbench chat, if one exists."""
    sessions = _build_sessions(db_path)
    return sessions[0] if sessions else None


async def _build_status(
    db_path: str | Path | None = None,
    *,
    sessions: list[dict[str, Any]] | None = None,
) -> dict:
    """Status data for the Status / Dashboard page."""
    soul = _soul_presentation()
    session_rows = sessions if sessions is not None else await asyncio.to_thread(_build_sessions, db_path)
    subagents = [agent for session in session_rows for agent in session.get('subagents', [])]
    return {'phase': 'evolve', 'state': localized('Evolving', '进化'), 'metrics': [], 'sparkData': [], 'workers': subagents, 'logs': [], 'services': [], 'model': project_runtime._get_model(), 'base_url': project_runtime._get_base_url(), 'short_term_entries': len(load_entries()), 'session_messages': sum(int(session.get('messageCount') or 0) for session in session_rows), 'scheduled_tasks': 0, 'soul_exists': bool(soul.get('path'))}


async def build_status(db_path: str | Path | None = None) -> dict:
    """Public status projection for adjacent application services."""
    return await _build_status(db_path)

async def _build_dashboard(
    ui_tz=None,
    db_path: str | Path | None = None,
    *,
    sessions: list[dict[str, Any]] | None = None,
) -> dict:
    """Aggregate homepage data from memory, soul, archive, and scheduler state."""
    from cyrene.runtime import database as cy_db
    resolved_db_path = str(db_path or project_repository._db_path or DB_PATH)
    ui_tz = ui_tz or (datetime.now().astimezone().tzinfo or timezone.utc)
    now_local = datetime.now(ui_tz)
    st_entries = load_entries()
    try:
        from cyrene.core.plugin import application_plugin_service

        schedule_application = application_plugin_service("schedule_application")
        tasks = (
            await schedule_application.list_all_tasks()
            if schedule_application is not None
            else []
        )
    except Exception:
        logger.warning('Failed to load tasks from the schedule Plugin; task list empty', exc_info=True)
        tasks = []
    today = now_local.strftime('%Y-%m-%d')
    soul = _soul_presentation()
    recent_memories = sorted(st_entries, key=lambda entry: (str(entry.get('last_mentioned', '')), int(entry.get('mention_count', 0))), reverse=True)[:6]
    today_entries = [entry for entry in st_entries if str(entry.get('last_mentioned', '')).strip() == today]
    learned_today = sorted(today_entries, key=lambda entry: (int(entry.get('mention_count', 0)), abs(int(entry.get('emotional_valence', 0)))), reverse=True)[:4]
    session_summaries = sessions if sessions is not None else await asyncio.to_thread(_build_sessions, db_path)
    session_message_count = sum(
        int(session.get('messageCount') or 0)
        for session in session_summaries
    )
    reminder_items = []
    for task in sorted(tasks, key=lambda item: str(item.get('next_run') or '')):
        next_run = str(task.get('next_run') or '').strip()
        status = str(task.get('status') or '').strip()
        if not next_run or status not in {'active', 'paused'}:
            continue
        reminder_items.append({'id': str(task.get('id') or ''), 'prompt': str(task.get('prompt') or '').strip(), 'next_run': next_run, 'schedule_type': str(task.get('schedule_type') or '').strip(), 'status': status})
    reminder_items = reminder_items[:6]
    archive_snippets: list[dict[str, Any]] = []
    memory_service = _memory_service()
    documents = (
        memory_service.list_archive_documents(limit=7)
        if memory_service is not None
        else []
    )
    for document in documents:
        date_str = str(document.get('date') or '')
        sections = document.get('sections') or []
        for section in reversed(sections):
            user_body = str(section.get('user_body', '')).strip()
            assistant_body = str(section.get('assistant_body', '')).strip()
            if user_body or assistant_body:
                archive_snippets.append({'date': date_str, 'title': str(section.get('round_title') or section.get('session_title') or '').strip(), 'user': user_body, 'assistant': assistant_body})
    archive_snippets = archive_snippets[:6]
    hist_days = 27
    day_from = (now_local - timedelta(days=hist_days)).strftime('%Y-%m-%d')
    day_to = today
    stats_rows = await cy_db.get_daily_stats_range(resolved_db_path, day_from, day_to)
    stats_by_day = {str(row.get('day') or ''): row for row in stats_rows if str(row.get('day') or '').strip()}
    model_stats_rows = await cy_db.get_model_stats_range(resolved_db_path, day_from, day_to)
    topic_rows = await cy_db.get_topic_counts_range(resolved_db_path, day_from, day_to, limit=18)
    tool_rows = await cy_db.get_tool_counts_range(resolved_db_path, day_from, day_to, limit=5)
    task_time = await cy_db.get_task_time_totals(resolved_db_path)
    archive_day_count = await cy_db.count_stat_days(resolved_db_path)
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
    return {'today': {'learned': learned_today, 'learned_count': len(today_entries), 'memory_count': len(st_entries), 'archive_days': archive_day_count}, 'soul': {'path': str(soul.get('path') or ''), 'updated_at': str(soul.get('updated_at') or ''), 'recent_items': list(soul.get('recent_items') or ()), 'section_count': int(soul.get('section_count') or 0)}, 'topic_cloud': topic_rows, 'emotion': emotion_series, 'usage': {'requests': historical_requests, 'tokens': session_metrics.format_tokens({'prompt_tokens': historical_prompt, 'completion_tokens': historical_completion, 'total_tokens': historical_total}), 'spend': spend_str, 'spend_cny': round(total_spend_cny, 6), 'spend_usd': round(total_spend_usd, 6), 'prompt_tokens': historical_prompt, 'completion_tokens': historical_completion, 'total_tokens': historical_total, 'cache_hit_tokens': historical_cache_hit, 'cache_miss_tokens': historical_cache_miss, 'total_messages': session_message_count, 'active_days': sum((1 for row in stats_by_day.values() if int(row.get('llm_requests') or 0) > 0)), 'current_streak': _calc_current_streak(stats_by_day, today), 'longest_streak': _calc_longest_streak(stats_by_day), 'peak_hour': _calc_peak_hour(stats_by_day), 'task_time': task_time, 'top_tools': tool_rows, 'timeline': [{'date': day, 'prompt': values['prompt'], 'completion': values['completion'], 'requests': values['requests']} for day, values in token_timeline.items()]}, 'reminders': reminder_items, 'recent_memories': recent_memories, 'recent_archive': archive_snippets, 'activity_heatmap': activity_heatmap, 'model_stats': model_stats_rows}

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
            rows.append({
                't': ts,
                'lvl': 'info',
                'msg': localized('Session started', '会话已开始'),
            })
    return list(reversed(rows[-20:]))

def _placeholder_logs() -> list[dict]:
    now = datetime.now(timezone.utc).strftime('%H:%M:%S')
    return [{
        't': now,
        'lvl': 'info',
        'msg': localized(
            'No debug logs yet — verbose mode is enabled; logs appear after agent runs.',
            '暂无调试日志——详细模式已启用，智能体运行后会显示日志。',
        ),
    }]

def _build_settings_meta() -> dict:
    labels = {
        'general': localized('General', '通用'),
        'search': localized('Search', '搜索'),
        'channels': localized('Channels', '渠道'),
        'models': localized('Models', '模型'),
        'agents': localized('Agents', '智能体'),
        'appearance': localized('Appearance', '外观'),
        'capabilities': localized('Capabilities', '能力'),
        'data': localized('Data', '数据'),
        'about': localized('About', '关于'),
    }
    return {
        'sections': [
            {'id': section_id, 'label': label}
            for section_id, label in labels.items()
        ]
    }

def _build_config() -> dict:
    from cyrene.runtime.config_store import get_settings_revision
    from cyrene.runtime.settings_service import read_public

    live_model, live_base_url = project_runtime._live_llm_config()
    soul = _soul_presentation()
    plugin_values = dict(read_public("runtime").get("values") or {})
    return {
        "revision": get_settings_revision(),
        "model": live_model,
        "base_url": live_base_url,
        "assistant_name": ASSISTANT_NAME,
        "base_dir": str(BASE_DIR),
        "data_dir": str(DATA_DIR),
        "workspace_dir": str(WORKSPACE_DIR),
        "soul_path": str(soul.get("path") or ""),
        "soul_content": str(soul.get("content") or ""),
        **plugin_values,
    }


def _load_messages(db_path: str | Path | None = None) -> list[dict]:
    """Load the latest Workbench transcript from the SQLite chat repository."""
    result = []
    for message in _load_state_messages(db_path):
        role = str(message.get('role') or '')
        if role not in ('user', 'assistant'):
            continue
        content = message.get('content', message.get('body', ''))
        if not isinstance(content, str) or not content.strip():
            continue
        result.append({'role': role, 'content': content})
    return result


def _load_state_messages(db_path: str | Path | None = None) -> list[dict]:
    """Deprecated name for the latest repository-backed chat transcript."""
    from cyrene.workbench.persistence import store as workbench_store

    resolved_db_path = str(db_path or project_repository._db_path or DB_PATH)
    chats = workbench_store.read_chat_summaries(
        resolved_db_path,
        lambda: {'chats': []},
    )
    candidates = [
        chat
        for chat in chats
        if isinstance(chat, dict)
        and str(chat.get('kind') or 'chat') == 'chat'
        and str(chat.get('id') or '').strip()
    ]
    candidates.sort(key=lambda chat: str(chat.get('updatedAt') or ''), reverse=True)
    if not candidates:
        return []
    chat = workbench_store.read_chat(
        resolved_db_path,
        str(candidates[0].get('id') or ''),
        lambda: {'chats': []},
    )
    return [
        dict(message)
        for message in (chat or {}).get('messages') or []
        if isinstance(message, dict)
    ]

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

__all__ = [
    "build_sessions",
    "build_status",
    "load_entries",
]
