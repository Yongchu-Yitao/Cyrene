"""Workspace-scoped memory API for the new Workbench UI.

This module is intentionally INDEPENDENT from the legacy memory page
(``/api/memory`` in ``routes.py`` + ``compiled/memory.js``), which the old
``--agent`` UI uses. It exposes a parallel set of endpoints under
``/api/workbench/memory/*`` so the two UIs never share request code.

Per-workspace isolation: every request carries a ``workspace`` query param
(the Workbench project id). It resolves to its own SQLite document, so each
workspace/project owns a separate memory store. A missing/blank workspace
falls back to ``default``.
Cross-workspace memory is intentionally NOT implemented yet.

Each memory item is a structured entry adapted into the rich model the
Workbench memory page shows (category / tags / source / confidence /
citations). The storage format is forward/backward compatible: extra fields
are additive and unknown fields are preserved on round-trips.
"""

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cyrene.config import STORE_DIR
from cyrene.io_utils import atomic_write_json, read_json_safe
from cyrene.workbench_store import delete_document, read_document, write_document
from webui import api_models
from webui.api_errors import error_response

logger = logging.getLogger(__name__)
_STORE_DB_PATH = ""
_CONFIGURED_STORE_DIR: Path | None = None

# ── classification vocab ─────────────────────────────────────────────────
# The five memory categories surfaced in the sidebar, in display order.
_CATEGORY_LABELS: dict[str, str] = {
    "preference": "个人偏好",
    "project": "项目背景",
    "habit": "工作习惯",
    "fact": "事实信息",
    "conversation": "对话记忆",
    "task_report": "任务报告",
}
_CATEGORY_ORDER = ["preference", "project", "habit", "fact", "conversation", "task_report"]

# Map a legacy/free-form entry ``type`` onto a Workbench category so memories
# captured by the agent (which only tags ``fact`` / ``preference`` / …) still
# land in a sensible bucket.
_TYPE_TO_CATEGORY: dict[str, str] = {
    "preference": "preference",
    "pref": "preference",
    "fact": "fact",
    "project": "project",
    "background": "project",
    "habit": "habit",
    "routine": "habit",
    "conversation": "conversation",
    "chat": "conversation",
    "event": "conversation",
    "emotion": "conversation",
    "task_report": "task_report",
}

_SOURCE_LABELS: dict[str, str] = {
    "conversation": "对话",
    "knowledge": "知识库",
    "manual": "手动添加",
    "agent": "Agent 记录",
    "other": "其他",
}
_SOURCE_ORDER = ["conversation", "knowledge", "manual", "agent", "other"]

# Memory categories worth injecting into an agent run. "conversation" (idle
# chatter distilled from talk) is excluded — high noise, low task value.
_INJECT_CATEGORIES = {"preference", "project", "habit", "fact"}

_CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}


def _safe_workspace_id(workspace_id: str | None) -> str:
    """Sanitize a workspace id into a filesystem-safe key (defaults to 'default')."""
    raw = str(workspace_id or "").strip()
    if not raw:
        return "default"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return cleaned or "default"


def _resolve_workspace_id(workspace_id: str | None) -> str:
    """Map a Workbench project id to its storage key when possible."""
    wid = _safe_workspace_id(workspace_id)
    try:
        from webui import routes as R

        payload = R._read_workbench_store()
        project = R._workbench_find_project(payload, str(workspace_id or "").strip())
        if project:
            return R._workbench_project_data_key(project)
    except Exception:
        pass
    return wid


def _memory_path(workspace_id: str | None) -> Path:
    """Resolve a workspace to its per-workspace memory JSON file."""
    return STORE_DIR / f"wb_memory_{_resolve_workspace_id(workspace_id)}.json"


def _load(workspace_id: str | None) -> list[dict]:
    resolved = _resolve_workspace_id(workspace_id)
    if resolved == "default":
        from cyrene.short_term import load_entries

        return load_entries()
    if not _STORE_DB_PATH or _CONFIGURED_STORE_DIR != Path(STORE_DIR):
        data = read_json_safe(_memory_path(workspace_id))
        return data if isinstance(data, list) else []
    data = read_document(
        _STORE_DB_PATH,
        f"memory:{resolved}",
        list,
        legacy_path=_memory_path(workspace_id),
    )
    return data if isinstance(data, list) else []


def _save(
    workspace_id: str | None,
    entries: list[dict],
    *,
    base_value: list[dict] | None = None,
) -> None:
    resolved = _resolve_workspace_id(workspace_id)
    if resolved == "default":
        from cyrene.short_term import save_entries

        save_entries(entries)
        return
    if not _STORE_DB_PATH or _CONFIGURED_STORE_DIR != Path(STORE_DIR):
        atomic_write_json(_memory_path(workspace_id), entries)
        return
    merged = write_document(
        _STORE_DB_PATH,
        f"memory:{resolved}",
        entries,
        list,
        legacy_path=_memory_path(workspace_id),
        export_path=_memory_path(workspace_id),
        base_value=base_value,
    )
    entries.clear()
    entries.extend(merged)
    if hasattr(entries, "_workbench_base"):
        entries._workbench_base = getattr(merged, "_workbench_base", list(merged))


def configure_store(db_path: str) -> None:
    global _STORE_DB_PATH, _CONFIGURED_STORE_DIR
    _STORE_DB_PATH = str(db_path or "")
    _CONFIGURED_STORE_DIR = Path(STORE_DIR)


def delete_workspace_memory(workspace_id: str | None) -> None:
    resolved = _resolve_workspace_id(workspace_id)
    if resolved == "default":
        return
    path = _memory_path(workspace_id)
    if _STORE_DB_PATH and _CONFIGURED_STORE_DIR == Path(STORE_DIR):
        delete_document(_STORE_DB_PATH, f"memory:{resolved}", export_path=path)
    else:
        path.unlink(missing_ok=True)


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _entry_category(entry: dict) -> str:
    cat = str(entry.get("category") or "").strip().lower()
    if cat in _CATEGORY_LABELS:
        return cat
    t = str(entry.get("type") or "").strip().lower()
    return _TYPE_TO_CATEGORY.get(t, "conversation")


def _entry_source(entry: dict) -> str:
    src = str(entry.get("source") or "").strip().lower()
    return src if src in _SOURCE_LABELS else "conversation"


def _entry_confidence(entry: dict) -> str:
    conf = str(entry.get("confidence") or "").strip().lower()
    if conf in _CONFIDENCE_LABELS:
        return conf
    # Derive from how often the memory has been reinforced — mirrors the
    # short-term retention heuristic (>=3 mentions == high confidence).
    mc = int(entry.get("mention_count") or 1)
    if mc >= 3:
        return "high"
    if mc == 2:
        return "medium"
    return "low"


def _entry_id(entry: dict) -> str:
    eid = str(entry.get("id") or "").strip()
    if eid:
        return eid
    content = str(entry.get("content") or "")
    return "mem_" + hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]


_MAX_CITATIONS = 50
_MAX_HISTORY = 50

_CITATION_SOURCE_LABELS = {
    "conversation": "对话引用",
    "agent": "Agent 引用",
    "knowledge": "知识库引用",
    "manual": "手动添加",
    "other": "其他",
}

_HISTORY_ACTION_LABELS = {
    "created": "创建记忆",
    "edited": "编辑内容",
    "reinforced": "再次引用",
    "stale": "标记过时",
    "revived": "恢复使用",
}


def _append_citation(entry: dict, source: str, snippet: str = "") -> None:
    """Record one citation event on the entry (capped to _MAX_CITATIONS)."""
    cits = entry.get("citations")
    if not isinstance(cits, list):
        cits = []
        entry["citations"] = cits
    cits.append({
        "at": _today(),
        "source": source if source in _CITATION_SOURCE_LABELS else "other",
        "snippet": str(snippet or "").strip()[:200],
    })
    if len(cits) > _MAX_CITATIONS:
        del cits[: len(cits) - _MAX_CITATIONS]


def _append_history(entry: dict, action: str, detail: str = "") -> None:
    """Record one history event on the entry (capped to _MAX_HISTORY)."""
    hist = entry.get("history")
    if not isinstance(hist, list):
        hist = []
        entry["history"] = hist
    hist.append({
        "at": _today(),
        "action": action if action in _HISTORY_ACTION_LABELS else "edited",
        "detail": str(detail or "").strip()[:200],
    })
    if len(hist) > _MAX_HISTORY:
        del hist[: len(hist) - _MAX_HISTORY]


def _entry_citations(entry: dict) -> list:
    cits = entry.get("citations")
    if not isinstance(cits, list):
        return []
    return [
        {
            "at": str(c.get("at") or ""),
            "source": str(c.get("source") or "other"),
            "source_label": _CITATION_SOURCE_LABELS.get(
                str(c.get("source") or "other"), "其他"
            ),
            "snippet": str(c.get("snippet") or ""),
        }
        for c in cits
        if isinstance(c, dict)
    ]


def _entry_history(entry: dict) -> list:
    hist = entry.get("history")
    if not isinstance(hist, list):
        return []
    return [
        {
            "at": str(h.get("at") or ""),
            "action": str(h.get("action") or "edited"),
            "action_label": _HISTORY_ACTION_LABELS.get(
                str(h.get("action") or "edited"), "编辑"
            ),
            "detail": str(h.get("detail") or ""),
        }
        for h in hist
        if isinstance(h, dict)
    ]


def _serialize(entry: dict) -> dict:
    cat = _entry_category(entry)
    src = _entry_source(entry)
    conf = _entry_confidence(entry)
    tags = entry.get("tags")
    if not isinstance(tags, list):
        tags = []
    history = _entry_history(entry)
    # Backfill a minimal history from timestamps for legacy entries that
    # predate explicit event tracking so the History tab is never empty.
    if not history:
        created = str(entry.get("first_seen") or "")
        updated = str(entry.get("last_mentioned") or entry.get("first_seen") or "")
        if created:
            history.append({"at": created, "action": "created", "action_label": "创建记忆", "detail": ""})
        if updated and updated != created:
            history.append({"at": updated, "action": "reinforced", "action_label": "再次引用", "detail": ""})
    return {
        "id": _entry_id(entry),
        "content": str(entry.get("content") or ""),
        "category": cat,
        "category_label": _CATEGORY_LABELS[cat],
        "source": src,
        "source_label": _SOURCE_LABELS[src],
        "confidence": conf,
        "confidence_label": _CONFIDENCE_LABELS[conf],
        "tags": [str(t) for t in tags],
        "citation_count": int(entry.get("mention_count") or 1),
        "created_at": str(entry.get("first_seen") or ""),
        "updated_at": str(entry.get("last_mentioned") or entry.get("first_seen") or ""),
        "citations": _entry_citations(entry),
        "history": history,
        "emotional_valence": entry.get("emotional_valence", 0),
        "stale": bool(entry.get("stale")),
    }


def _recent_added(entries: list[dict], days: int = 7) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for e in entries:
        seen = str(e.get("first_seen") or "")
        try:
            dt = datetime.strptime(seen[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if 0 <= (now - dt).days < days:
            count += 1
    return count


def _build_payload(workspace_id: str | None) -> dict:
    """Assemble the full memory state (items + sidebar aggregates) for a workspace."""
    entries = _load(workspace_id)
    memories = [_serialize(e) for e in entries]
    memories.sort(key=lambda m: m["updated_at"], reverse=True)
    total = len(memories)

    cat_counts = {c: 0 for c in _CATEGORY_ORDER}
    src_counts = {s: 0 for s in _SOURCE_ORDER}
    for m in memories:
        cat_counts[m["category"]] = cat_counts.get(m["category"], 0) + 1
        src_counts[m["source"]] = src_counts.get(m["source"], 0) + 1

    categories = [{"id": "all", "label": "全部记忆", "count": total}]
    categories += [
        {"id": c, "label": _CATEGORY_LABELS[c], "count": cat_counts[c]}
        for c in _CATEGORY_ORDER
    ]
    sources = [
        {
            "id": s,
            "label": _SOURCE_LABELS[s],
            "count": src_counts[s],
            "pct": round(src_counts[s] / total * 100) if total else 0,
        }
        for s in _SOURCE_ORDER
    ]

    overview = {
        "total": total,
        "recent_added": _recent_added(entries),
        "total_citations": sum(m["citation_count"] for m in memories),
        "last_updated": max((m["updated_at"] for m in memories), default=""),
    }
    return {
        "memories": memories,
        "categories": categories,
        "sources": sources,
        "overview": overview,
    }


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = re.split(r"[,，;；\s]+", value)
    else:
        return []
    out: list[str] = []
    for t in items:
        s = str(t or "").strip()
        if s and s not in out:
            out.append(s)
    return out[:12]


def _preferred_memory_language() -> str:
    """Return the configured language used for user-visible memory content."""
    try:
        from cyrene.settings_store import get as _get_setting

        return "en" if str(_get_setting("app_language", "") or "").strip().lower() == "en" else "zh"
    except Exception:
        return "zh"


def _content_matches_language(content: str, language: str) -> bool:
    """Whether natural-language text already matches the requested language.

    Pure identifiers, paths, commands, and similarly language-neutral fragments
    are accepted unchanged. Chinese content needs at least one Han character;
    English content must not contain Han characters.
    """
    text = str(content or "").strip()
    has_han = bool(re.search(r"[\u3400-\u9fff]", text))
    if language == "en":
        return not has_han
    if has_han:
        han_count = len(re.findall(r"[\u3400-\u9fff]", text))
        # Lowercase English words are a useful prose signal while mixed-case
        # tokens such as PostgreSQL, TypeScript, and Next.js are usually
        # technical names that should not force translation. Require enough
        # Chinese text to outweigh multiple English prose words.
        prose_words = [
            word for word in re.findall(r"[A-Za-z][A-Za-z'-]*", text)
            if word.islower() or word.casefold() in {"a", "an", "the", "user"}
        ]
        return han_count >= max(2, len(prose_words) * 2)
    if not re.search(r"\s", text):
        return True
    # Do not translate language-neutral values such as ``src/app.py`` or
    # ``MAX_RETRIES=3``. Two or more words indicate English prose.
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    return len(words) < 2


async def _normalize_agent_memory_language(content: str) -> str:
    """Translate an agent-authored memory into the configured UI language.

    Returns an empty string when translation fails or still produces the wrong
    language. Callers must treat that as a rejected write so wrong-language
    content never reaches the Workbench memory store.
    """
    text = str(content or "").strip()
    language = _preferred_memory_language()
    if not text or _content_matches_language(text, language):
        return text

    from cyrene.agent.state import _call_llm, _caller_type
    from cyrene.llm import _assistant_text

    target = "English" if language == "en" else "Simplified Chinese"
    prompt = (
        f"Translate the following project-memory sentence into {target}. "
        "Keep the meaning precise and concise. Preserve code, file paths, shell commands, "
        "identifiers, numbers, model names, and proper nouns exactly. "
        'Return JSON only in this form: {"content":"translated sentence"}.\n\n'
        f"Memory:\n{text}"
    )
    token = _caller_type.set("workbench_memory")
    try:
        response = await asyncio.wait_for(
            _call_llm(
                [{"role": "user", "content": prompt}],
                tools=None,
                max_tokens=300,
                secondary=True,
                thinking="disabled",
            ),
            timeout=30,
        )
        parsed = _parse_json_object(_assistant_text(response))
        translated = str(parsed.get("content") or "").strip() if isinstance(parsed, dict) else ""
    except Exception:  # noqa: BLE001
        logger.debug("Workbench agent-memory translation failed", exc_info=True)
        return ""
    finally:
        _caller_type.reset(token)

    if len(translated) < 4 or not _content_matches_language(translated, language):
        logger.warning("Rejected wrong-language Workbench agent memory after translation")
        return ""
    return translated


# ── conversation capture (agent memory → per-workspace store) ────────────
# When the workbench agent finishes a turn, an LLM pass distills durable,
# user-specific memories from the exchange and sinks them into THIS workspace's
# store (source = "conversation"). Runs fire-and-forget so it never blocks the
# reply. This is the per-workspace equivalent of the legacy global short-term
# capture, and is the only path that feeds memories automatically.

# Hold references to in-flight capture tasks so they are not garbage-collected.
_pending_captures: set[asyncio.Task] = set()

_EXTRACT_PROMPT = """\
你是一个记忆抽取器。请从下面这一轮对话中，提取「值得长期记住的、关于用户的稳定信息」。

只提取：用户的偏好、习惯、角色/身份、稳定的事实、项目背景，或明确的长期决定。
不要提取：一次性的任务细节、寒暄客套、临时的操作请求、以及助手自己说的话。
如果没有值得长期记住的内容，就返回空列表。

每条记忆的字段：
- content: %(content_lang_hint)s。简洁、自包含、不含具体某次任务的临时细节。
- category: 从这五个里选一个——按优先级依次判断：
  * preference —— 用户的静态口味/长期偏好（例：偏好简洁回答、喜欢深色主题）
  * project   —— 用户长期正在做/维护的项目或工作主线（例：正在优化 CIFAR-10 分类器 v2）
  * habit     —— 用户可重复观察到的行为模式（例：习惯让 subagent 执行任务后看汇总）
  * fact      —— 用户的客观背景信息（例：是数据科学家、使用 Mac M2）
  * conversation —— 仅当以上四类均不适用时才选，用于一次性情绪/互动状态
- confidence: high / medium / low（这条信息的可靠程度）

%(output_lang_line)s

只输出 JSON，不要解释，格式如下：
{"memories": [{"content": "...", "category": "preference", "confidence": "high"}]}

[用户]
%(user)s

[助手]
%(agent)s
"""


def _parse_json_object(text: str) -> dict:
    """Best-effort parse of an LLM response into a JSON object."""
    s = str(text or "").strip()
    if not s:
        return {}
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        pass
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(s[start:end + 1])
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _similar_entry(entries: list[dict], content: str) -> dict | None:
    """Find an existing entry whose content is (near-)identical, for dedup."""
    target = content.strip().lower()
    if not target:
        return None
    for e in entries:
        existing = str(e.get("content") or "").strip().lower()
        if not existing:
            continue
        if existing == target:
            return e
        # one side substantially contains the other → treat as the same memory
        shorter, longer = sorted((existing, target), key=len)
        if shorter and shorter in longer and len(shorter) >= len(longer) * 0.7:
            return e
    return None


async def _extract_memories_llm(user_text: str, agent_text: str) -> list[dict]:
    """Ask the LLM to distill durable memories from one exchange."""
    from cyrene.agent.state import _call_llm, _caller_type
    from cyrene.llm import _assistant_text
    from cyrene.settings_store import get as _get_setting

    lang = str(_get_setting("app_language", "") or "").strip().lower()
    if lang == "en":
        content_lang_hint = 'one sentence describing the user in second person "you" (e.g. "You prefer concise answers"). Write in English'
        output_lang_line = "IMPORTANT: Write every 'content' value in English."
    else:
        content_lang_hint = '一句话，用第二人称"你"描述用户（例："你偏好简洁、结构化的回答"），必须用中文书写'
        output_lang_line = "重要：所有 content 字段必须用中文书写，不得使用英文。"

    prompt = _EXTRACT_PROMPT % {
        "content_lang_hint": content_lang_hint,
        "output_lang_line": output_lang_line,
        "user": user_text[:1500],
        "agent": agent_text[:1500] or "（无回复）",
    }
    token = _caller_type.set("workbench_memory")
    try:
        resp = await _call_llm([{"role": "user", "content": prompt}], tools=None, max_tokens=700)
        data = _parse_json_object(_assistant_text(resp))
    finally:
        _caller_type.reset(token)
    mems = data.get("memories") if isinstance(data, dict) else None
    return mems if isinstance(mems, list) else []


async def capture_from_exchange(workspace_id: str, user_text: str, agent_text: str) -> int:
    """Distill durable memories from one turn and merge them into the store.

    Returns the number of memories newly added (existing ones are reinforced via
    ``mention_count`` rather than duplicated). Safe to call in the background.
    """
    user_text = str(user_text or "").strip()
    agent_text = str(agent_text or "").strip()
    # Skip trivial inputs and slash-commands (those are actions, not memories).
    if len(user_text) < 4 or user_text.startswith("/"):
        return 0

    extracted = await _extract_memories_llm(user_text, agent_text)
    if not extracted:
        return 0

    entries = _load(workspace_id)
    today = _today()
    added = 0
    changed = False
    for mem in extracted:
        if not isinstance(mem, dict):
            continue
        content = str(mem.get("content") or "").strip()
        if len(content) < 4:
            continue
        category = str(mem.get("category") or "").strip().lower()
        if category not in _CATEGORY_LABELS:
            category = "conversation"
        confidence = str(mem.get("confidence") or "").strip().lower()

        dup = _similar_entry(entries, content)
        if dup is not None:
            dup["last_mentioned"] = today
            dup["mention_count"] = int(dup.get("mention_count") or 1) + 1
            _append_citation(dup, "conversation", user_text[:200])
            _append_history(dup, "reinforced")
            changed = True
            continue

        entry: dict[str, Any] = {
            "id": "mem_" + uuid.uuid4().hex[:12],
            "content": content,
            "type": category,
            "category": category,
            "source": "conversation",
            "tags": _normalize_tags(mem.get("tags")),
            "first_seen": today,
            "last_mentioned": today,
            "mention_count": 1,
            "emotional_valence": 0,
        }
        if confidence in _CONFIDENCE_LABELS:
            entry["confidence"] = confidence
        _append_citation(entry, "conversation", user_text[:200])
        _append_history(entry, "created")
        entries.append(entry)
        added += 1
        changed = True

    if changed:
        _save(workspace_id, entries)
    return added


def schedule_capture(workspace_id: str | None, user_text: str, agent_text: str) -> None:
    """Fire-and-forget :func:`capture_from_exchange` so it never blocks a reply."""
    wid = _resolve_workspace_id(workspace_id)

    async def _runner() -> None:
        try:
            count = await capture_from_exchange(wid, user_text, agent_text)
            if count:
                logger.info("Workbench memory: captured %d memory(ies) for %s", count, wid)
        except Exception:  # noqa: BLE001
            logger.debug("Workbench memory capture failed for %s", wid, exc_info=True)

    try:
        task = asyncio.create_task(_runner())
    except RuntimeError:
        # No running event loop (e.g. called from sync context) — skip silently.
        return
    _pending_captures.add(task)
    task.add_done_callback(_pending_captures.discard)


def add_agent_memory(
    workspace_id: str | None,
    content: str,
    *,
    category: str = "fact",
    tags: Any = None,
    confidence: str = "",
    source: str = "agent",
) -> dict | None:
    """Append one durable memory written by the task agent into the project store.

    Reuses the same store + dedup as conversation capture so agent-written items
    show up on the Workbench memory page AND feed back into future runs. Returns
    the serialized entry, or ``None`` when skipped (blank/too short, or a
    non-Workbench session that resolves to the global ``default`` store — which
    aliases short-term memory and must never be written here).
    """
    content = str(content or "").strip()
    if len(content) < 4:
        return None
    if _resolve_workspace_id(workspace_id) == "default":
        return None
    category = str(category or "").strip().lower()
    if category not in _CATEGORY_LABELS:
        category = "fact"
    entries = _load(workspace_id)
    today = _today()
    dup = _similar_entry(entries, content)
    if dup is not None:
        # Reinforce an existing memory rather than duplicating it. Re-recording a
        # fact also revives it if it had been retired (stale).
        was_stale = bool(dup.get("stale"))
        dup["last_mentioned"] = today
        dup["mention_count"] = int(dup.get("mention_count") or 1) + 1
        dup["stale"] = False
        _append_citation(dup, source if source in _CITATION_SOURCE_LABELS else "agent", content[:200])
        _append_history(dup, "reinforced")
        if was_stale:
            _append_history(dup, "revived")
        _save(workspace_id, entries)
        return _serialize(dup)
    entry: dict[str, Any] = {
        "id": "mem_" + uuid.uuid4().hex[:12],
        "content": content,
        "type": category,
        "category": category,
        "source": source if source in _SOURCE_LABELS else "agent",
        "tags": _normalize_tags(tags),
        "first_seen": today,
        "last_mentioned": today,
        "mention_count": 1,
        "emotional_valence": 0,
    }
    conf = str(confidence or "").strip().lower()
    if conf in _CONFIDENCE_LABELS:
        entry["confidence"] = conf
    _append_citation(entry, source if source in _CITATION_SOURCE_LABELS else "agent", content[:200])
    _append_history(entry, "created")
    entries.append(entry)
    _save(workspace_id, entries)
    return _serialize(entry)


def search_project_memories(
    workspace_id: str | None,
    *,
    query: str,
    category: str = "",
    source: str = "",
    limit: int = 10,
    include_stale: bool = False,
    max_chars: int = 6000,
    max_content_chars: int = 800,
) -> list[dict]:
    """Search one Workbench project's durable memories.

    This is the read-side counterpart to ``save_project_memory``. Results are
    bounded, project-scoped, and ranked by direct content match, then recency.
    """
    needle = str(query or "").strip().casefold()
    if not needle:
        return []
    category = str(category or "").strip().lower()
    source = str(source or "").strip().lower()
    limit = max(1, min(int(limit or 10), 20))

    matches: list[tuple[int, str, dict]] = []
    for entry in _load(workspace_id):
        if not isinstance(entry, dict):
            continue
        if entry.get("stale") and not include_stale:
            continue
        entry_category = _entry_category(entry)
        entry_source = _entry_source(entry)
        if category and entry_category != category:
            continue
        if source and entry_source != source:
            continue
        content = str(entry.get("content") or "")
        tags = [str(tag) for tag in entry.get("tags") or []]
        content_folded = content.casefold()
        tags_folded = " ".join(tags).casefold()
        if needle not in content_folded and needle not in tags_folded:
            continue
        score = 2 if needle in content_folded else 1
        updated = str(entry.get("last_mentioned") or entry.get("first_seen") or "")
        matches.append((score, updated, entry))

    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    results: list[dict] = []
    used_chars = 0
    for _score, _updated, entry in matches[:limit]:
        raw_content = str(entry.get("content") or "")
        content_truncated = len(raw_content) > max_content_chars
        content = raw_content[:max_content_chars] + ("…" if content_truncated else "")
        tags = [str(tag)[:80] for tag in (entry.get("tags") or [])[:8]]
        item = {
            "id": _entry_id(entry),
            "content": content,
            "category": _entry_category(entry),
            "source": _entry_source(entry),
            "confidence": _entry_confidence(entry),
            "tags": tags,
            "updated_at": str(entry.get("last_mentioned") or entry.get("first_seen") or ""),
            "stale": bool(entry.get("stale")),
        }
        if content_truncated:
            item["content_truncated"] = True
        item_chars = len(json.dumps(item, ensure_ascii=False))
        if results and used_chars + item_chars > max_chars:
            break
        results.append(item)
        used_chars += item_chars
    return results


async def _detect_conflicting_memories(new_content: str, candidates: list[dict]) -> list[str]:
    """LLM judge: which existing memories does the new fact contradict/supersede?

    Conservative — flags only genuine conflicts (same thing with a different or
    updated value; a conclusion that overturns an old one), not merely related
    or complementary facts. Returns entry ids to retire. Best-effort → []."""
    if not new_content or not candidates:
        return []
    from cyrene.agent.state import _call_llm, _caller_type
    lines = [f"- id={_entry_id(e)}: {str(e.get('content') or '').strip()}" for e in candidates]
    prompt = (
        "正在为一个项目记录一条【新记忆】。判断它是否与下面某些【已有记忆】直接冲突，"
        "或使其过时（例如：同一参数/设置给了不同的值；新结论推翻了旧结论）。"
        "只标记真正冲突或被取代的；仅仅相关、互补、不矛盾的【不要】标记。\n\n"
        f"新记忆：{new_content}\n\n"
        "已有记忆：\n" + "\n".join(lines) + "\n\n"
        '只返回 JSON：{"conflicts":["被取代记忆的 id", ...]}，没有冲突就返回 {"conflicts":[]}。'
    )
    token = _caller_type.set("workbench_memory")
    try:
        resp = await asyncio.wait_for(
            _call_llm([{"role": "user", "content": prompt}], tools=None, max_tokens=300, secondary=True, thinking="disabled"),
            timeout=30,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Workbench conflict-detector failed", exc_info=True)
        return []
    finally:
        _caller_type.reset(token)
    parsed = _parse_json_object(resp.get("content") or "")
    raw = parsed.get("conflicts") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        return []
    valid = {_entry_id(e) for e in candidates}
    out: list[str] = []
    for x in raw:
        sid = str(x or "").strip()
        if sid in valid and sid not in out:
            out.append(sid)
    return out


async def add_agent_memory_checked(
    workspace_id: str | None,
    content: str,
    *,
    category: str = "fact",
    tags: Any = None,
    confidence: str = "",
) -> tuple[dict | None, list[dict]]:
    """Agent-facing write with semantic conflict resolution.

    Like :func:`add_agent_memory`, but before appending a genuinely new fact it
    asks an LLM whether the fact contradicts/supersedes existing active memories
    and retires (marks stale) those — so the agent's latest understanding wins,
    while the superseded record is kept (reversible) and no longer injected.

    Returns ``(new_or_reinforced_entry, [retired_entries])``. It may make one
    language-normalization call when the agent supplied content in the wrong
    language, plus one semantic-conflict call for a genuinely new fact. The
    cheap conversation-capture / reflection-sink paths are unaffected."""
    content = str(content or "").strip()
    if len(content) < 4:
        return None, []
    if _resolve_workspace_id(workspace_id) == "default":
        return None, []
    content = await _normalize_agent_memory_language(content)
    if len(content) < 4:
        return None, []
    category = str(category or "").strip().lower()
    if category not in _CATEGORY_LABELS:
        category = "fact"
    entries = _load(workspace_id)
    today = _today()

    # Textually (near-)identical → reinforce; a fact never conflicts with itself.
    dup = _similar_entry(entries, content)
    if dup is not None:
        was_stale = bool(dup.get("stale"))
        dup["last_mentioned"] = today
        dup["mention_count"] = int(dup.get("mention_count") or 1) + 1
        dup["stale"] = False
        _append_citation(dup, "agent", content[:200])
        _append_history(dup, "reinforced")
        if was_stale:
            _append_history(dup, "revived")
        _save(workspace_id, entries)
        return _serialize(dup), []

    new_id = "mem_" + uuid.uuid4().hex[:12]

    # Semantic conflict check against active memories (recent first, capped).
    active = [
        e for e in entries
        if isinstance(e, dict) and not e.get("stale") and str(e.get("content") or "").strip()
    ]
    active.sort(key=lambda e: str(e.get("last_mentioned") or e.get("first_seen") or ""), reverse=True)
    try:
        conflict_ids = await _detect_conflicting_memories(content, active[:25])
    except Exception:  # noqa: BLE001 — conflict detection must never block the write
        logger.debug("Workbench conflict-detection failed, saving without it", exc_info=True)
        conflict_ids = []
    retired: list[dict] = []
    if conflict_ids:
        cset = set(conflict_ids)
        for e in entries:
            if isinstance(e, dict) and _entry_id(e) in cset and not e.get("stale"):
                e["stale"] = True
                e["supersededAt"] = today
                e["supersededBy"] = new_id
                _append_history(e, "stale", "被新记忆取代")
                retired.append(_serialize(e))

    entry: dict[str, Any] = {
        "id": new_id,
        "content": content,
        "type": category,
        "category": category,
        "source": "agent",
        "tags": _normalize_tags(tags),
        "first_seen": today,
        "last_mentioned": today,
        "mention_count": 1,
        "emotional_valence": 0,
    }
    conf = str(confidence or "").strip().lower()
    if conf in _CONFIDENCE_LABELS:
        entry["confidence"] = conf
    _append_citation(entry, "agent", content[:200])
    _append_history(entry, "created")
    entries.append(entry)
    _save(workspace_id, entries)
    return _serialize(entry), retired


def render_memory_for_injection(
    workspace_id: str | None,
    *,
    limit: int = 20,
    max_chars: int = 2000,
) -> str:
    """Render a project's durable memories as a compact prompt block for a run.

    Cache note: callers inject this via ``ephemeral_system`` (prompt tail), so it
    never invalidates the cached system+history prefix. ``conversation`` memories
    are skipped (noise); strongest (most reinforced, then most recent) first.
    Returns "" when there is nothing worth injecting.
    """
    if _resolve_workspace_id(workspace_id) == "default":
        return ""
    entries = _load(workspace_id)
    if not entries:
        return ""
    items: list[tuple[int, str, str, str]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("stale"):
            continue  # retired memory — never inject into a run
        cat = _entry_category(e)
        if cat not in _INJECT_CATEGORIES:
            continue
        content = str(e.get("content") or "").strip()
        if not content:
            continue
        mc = int(e.get("mention_count") or 1)
        ts = str(e.get("last_mentioned") or e.get("first_seen") or "")
        items.append((mc, ts, cat, content))
    if not items:
        return ""
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    lines: list[str] = []
    used = 0
    for _mc, _ts, cat, content in items[:limit]:
        line = f"- [{_CATEGORY_LABELS.get(cat, cat)}] {content}"
        if lines and used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    header = "## 项目记忆（本项目此前沉淀/记录的长期信息，执行时请参考复用、避免重复摸索；与当前任务无关则忽略）"
    return header + "\n" + "\n".join(lines)


def render_task_reports_for_planning(
    workspace_id: str | None,
    *,
    limit: int = 3,
    max_chars: int = 2500,
) -> str:
    """Render past task completion reports for injection into the plan-generation
    prompt. Only ``task_report``-category entries are included; stale entries are
    skipped. Returns "" when no reports exist.

    NOT used in general agent runs — task reports are too verbose for every step.
    """
    if _resolve_workspace_id(workspace_id) == "default":
        return ""
    entries = _load(workspace_id)
    if not entries:
        return ""
    reports: list[tuple[str, str]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("stale"):
            continue
        if _entry_category(e) != "task_report":
            continue
        content = str(e.get("content") or "").strip()
        if not content:
            continue
        ts = str(e.get("first_seen") or "")
        reports.append((ts, content))
    if not reports:
        return ""
    reports.sort(key=lambda x: x[0], reverse=True)
    blocks: list[str] = []
    used = 0
    for _ts, content in reports[:limit]:
        if used + len(content) > max_chars:
            break
        blocks.append(content)
        used += len(content)
    if not blocks:
        return ""
    header = "## 本项目历史任务报告（请参考成功经验，避免重复踩坑；与当前任务无关则忽略）"
    return header + "\n\n" + "\n---\n".join(blocks)


def register_workbench_memory_routes(router: APIRouter, db_path: str = "") -> None:
    """Register workspace-scoped memory routes for the Workbench UI."""
    if db_path:
        configure_store(db_path)

    @router.get("/api/workbench/memory")
    async def wb_list_memory(workspace: str = "default"):
        try:
            return _build_payload(workspace)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to list Workbench memory for %s", workspace)
            return error_response("List failed", 500, "memory_list_failed")

    @router.post("/api/workbench/memory")
    async def wb_create_memory(
        body_model: api_models.MemoryCreateBody, workspace: str = "default"
    ):
        body = api_models.body_dict(body_model)

        content = str(body.get("content") or "").strip()
        if not content:
            return JSONResponse({"error": "content is required"}, status_code=400)

        category = str(body.get("category") or "").strip().lower()
        if category not in _CATEGORY_LABELS:
            category = "fact"
        source = str(body.get("source") or "manual").strip().lower()
        if source not in _SOURCE_LABELS:
            source = "manual"
        confidence = str(body.get("confidence") or "").strip().lower()

        today = _today()
        entry: dict[str, Any] = {
            "id": "mem_" + uuid.uuid4().hex[:12],
            "content": content,
            # Keep ``type`` in sync with category for any legacy reader.
            "type": category,
            "category": category,
            "source": source,
            "tags": _normalize_tags(body.get("tags")),
            "first_seen": today,
            "last_mentioned": today,
            "mention_count": 1,
            "emotional_valence": 0,
        }
        if confidence in _CONFIDENCE_LABELS:
            entry["confidence"] = confidence

        try:
            entries = _load(workspace)
            _append_history(entry, "created")
            entries.append(entry)
            _save(workspace, entries)
            payload = _build_payload(workspace)
            payload["id"] = entry["id"]
            return payload
        except Exception:  # noqa: BLE001
            logger.exception("Failed to create Workbench memory for %s", workspace)
            return error_response("Create failed", 500, "memory_create_failed")

    @router.patch("/api/workbench/memory/{mem_id}")
    async def wb_update_memory(
        mem_id: str,
        body_model: api_models.MemoryUpdateBody,
        workspace: str = "default",
    ):
        body = api_models.body_dict(body_model)

        try:
            entries = _load(workspace)
            target = None
            for e in entries:
                if _entry_id(e) == mem_id:
                    target = e
                    break
            if target is None:
                return JSONResponse({"error": "memory not found"}, status_code=404)

            # Persist the resolved id so future edits stay stable even after the
            # content (and thus its content-hash fallback id) changes.
            target["id"] = mem_id

            if "content" in body:
                content = str(body.get("content") or "").strip()
                if not content:
                    return JSONResponse({"error": "content cannot be empty"}, status_code=400)
                target["content"] = content
            if "category" in body:
                cat = str(body.get("category") or "").strip().lower()
                if cat in _CATEGORY_LABELS:
                    target["category"] = cat
                    target["type"] = cat
            if "source" in body:
                src = str(body.get("source") or "").strip().lower()
                if src in _SOURCE_LABELS:
                    target["source"] = src
            if "confidence" in body:
                conf = str(body.get("confidence") or "").strip().lower()
                if conf in _CONFIDENCE_LABELS:
                    target["confidence"] = conf
                else:
                    target.pop("confidence", None)
            if "tags" in body:
                target["tags"] = _normalize_tags(body.get("tags"))
            if "stale" in body:
                # Retire (or revive) a memory: stale entries stay on the page but
                # are no longer injected into agent runs.
                new_stale = bool(body.get("stale"))
                old_stale = bool(target.get("stale"))
                target["stale"] = new_stale
                if new_stale and not old_stale:
                    _append_history(target, "stale")
                elif not new_stale and old_stale:
                    _append_history(target, "revived")
            # An edit counts as a fresh touch — drives the "更新时间".
            target["last_mentioned"] = _today()
            if any(k in body for k in ("content", "category", "source", "confidence", "tags")):
                _append_history(target, "edited")

            _save(workspace, entries)
            return _build_payload(workspace)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to update Workbench memory %s for %s", mem_id, workspace
            )
            return error_response("Update failed", 500, "memory_update_failed")

    @router.delete("/api/workbench/memory/{mem_id}")
    async def wb_delete_memory(mem_id: str, workspace: str = "default"):
        try:
            entries = _load(workspace)
            kept = [e for e in entries if _entry_id(e) != mem_id]
            if len(kept) == len(entries):
                return JSONResponse({"error": "memory not found"}, status_code=404)
            _save(
                workspace,
                kept,
                base_value=getattr(entries, "_workbench_base", entries),
            )
            return _build_payload(workspace)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to delete Workbench memory %s for %s", mem_id, workspace
            )
            return error_response("Delete failed", 500, "memory_delete_failed")
