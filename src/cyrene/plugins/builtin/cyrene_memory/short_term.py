"""
Short-term memory management owned by the editable memory Plugin.
Stores compressed conversation summaries that persist across sessions.
Entry lifecycle: conversation -> compressed -> short_term -> (via Steward) -> long_term
"""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cyrene.localization import app_language, localized
from cyrene.runtime.io import atomic_write_json, read_json_safe

logger = logging.getLogger(__name__)

# 文件路径由 init_short_term 设置
_SHORT_TERM_FILE: Path | None = None
_STATS_DB_PATH = ""
_COMPRESSION_MIN_MESSAGES = 45
_COMPRESSION_WINDOW_MESSAGES = 20


def init_short_term(data_dir: Path, db_path: str = "") -> None:
    """Configure the Plugin-owned short-term store and statistics database."""
    global _SHORT_TERM_FILE, _STATS_DB_PATH
    _SHORT_TERM_FILE = data_dir / "short_term.json"
    _STATS_DB_PATH = str(db_path or "").strip()


def load_entries() -> list[dict]:
    """从 short_term.json 加载所有条目。文件不存在时返回空列表。"""
    if _SHORT_TERM_FILE is None:
        return []
    try:
        data = read_json_safe(_SHORT_TERM_FILE)
    except Exception:
        logger.exception("Failed to load short-term memory")
        return []
    if data is None:
        return []
    return data if isinstance(data, list) else []


def save_entries(entries: list[dict]) -> None:
    """保存条目到 short_term.json。"""
    if _SHORT_TERM_FILE is None:
        return
    try:
        atomic_write_json(_SHORT_TERM_FILE, entries)
    except Exception:
        logger.exception("Failed to save short-term memory")


def entry_id(entry: dict[str, Any]) -> str:
    """Return a stable id for one short-term memory entry."""
    existing = str(entry.get("id") or entry.get("memory_id") or "").strip()
    if existing:
        return existing
    basis = "\n".join(
        [
            str(entry.get("type") or ""),
            str(entry.get("first_seen") or ""),
            str(entry.get("content") or ""),
        ]
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return f"stm_{digest}"


def retire_entry(memory_id: str, reason: str = "") -> tuple[dict[str, Any] | None, bool]:
    """Mark a short-term memory stale by id without deleting the stored record."""
    target_id = str(memory_id or "").strip()
    if not target_id:
        return None, False

    entries = load_entries()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry_id(entry) != target_id:
            continue
        changed = not bool(entry.get("stale"))
        if "id" not in entry:
            entry["id"] = target_id
        entry["stale"] = True
        entry["retired_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        clean_reason = str(reason or "").strip()
        if clean_reason:
            entry["retire_reason"] = clean_reason
        save_entries(entries)
        return entry, changed

    return None, False


def touch_entry(content_keyword: str, metadata: dict | None = None) -> None:
    """
    更新已有条目的 last_mentioned 和 mention_count。
    如果 content_keyword 匹配已有条目，+1 count + 更新时间。
    如果不存在且 metadata 提供，新增条目。
    """
    entries = load_entries()
    now = datetime.now().astimezone().strftime("%Y-%m-%d")

    kw_lower = content_keyword.lower()
    found = False
    touched_valence = metadata.get("emotional_valence", 0) if metadata else 0
    for entry in entries:
        entry_content = entry.get("content", "").lower()
        # Exact match or one is a near-complete substring of the other
        if (
            kw_lower == entry_content
            or (len(kw_lower) >= len(entry_content) * 0.7 and kw_lower in entry_content)
            or (len(entry_content) >= len(kw_lower) * 0.7 and entry_content in kw_lower)
        ):
            entry["last_mentioned"] = now
            entry["mention_count"] = entry.get("mention_count", 1) + 1
            touched_valence = entry.get("emotional_valence", touched_valence)
            found = True
            break

    if not found and metadata:
        entries.append(
            {
                "content": metadata.get("content", content_keyword),
                "type": metadata.get("type", "fact"),
                "first_seen": now,
                "last_mentioned": now,
                "mention_count": 1,
                "emotional_valence": metadata.get("emotional_valence", 0),
            }
        )

    save_entries(entries)
    try:
        if not _STATS_DB_PATH:
            return
        from cyrene.runtime import database as cy_db

        cy_db.record_memory_touch_sync(
            _STATS_DB_PATH,
            day=now,
            emotional_valence=float(touched_valence or 0),
            is_new=not found and bool(metadata),
        )
    except Exception:
        logger.exception("Failed to persist memory stats")


def get_context(max_chars: int = 5000, header: str | None = None) -> str:
    """
    格式化短期记忆条目为一个字符串，用于注入 context。
    按 last_mentioned 倒序（最近的最靠前）。
    不超过 max_chars 字符。
    """
    entries = load_entries()
    if not entries:
        return ""

    # 按 last_mentioned 倒序
    active_entries = [entry for entry in entries if not entry.get("stale")]
    if not active_entries:
        return ""
    sorted_entries = sorted(
        active_entries,
        key=lambda e: e.get("last_mentioned", ""),
        reverse=True,
    )

    resolved_header = header or localized(
        "[Previous context:]",
        "[先前上下文：]",
        language=app_language(),
    )
    parts: list[str] = [resolved_header]
    chars_used = len(parts[0])

    for entry in sorted_entries:
        line = f"- {entry.get('content', '')}"
        if chars_used + len(line) + 1 > max_chars:
            break
        parts.append(line)
        chars_used += len(line) + 1

    return "\n".join(parts)


def clear_old_entries(days: int = 7) -> None:
    """
    清除超过 days 天未提及的一次性闲聊条目。
    保留高频（mention_count >= 3）、情感极值（|valence| >= 3）、事实类型条目。
    """
    entries = load_entries()
    now = datetime.now(timezone.utc)

    kept = []
    for e in entries:
        last_str = e.get("last_mentioned", "")
        mention_count = e.get("mention_count", 1)
        valence = e.get("emotional_valence", 0)

        # 保留高频/情感/事实
        if mention_count >= 3 or abs(valence) >= 3 or e.get("type") in ("fact", "preference"):
            kept.append(e)
            continue

        # 检查是否超期
        try:
            last_dt = datetime.strptime(last_str, "%Y-%m-%d")
            if (now - last_dt).days > days:
                continue  # 丢弃
        except (ValueError, TypeError):
            pass
        kept.append(e)

    save_entries(kept)


def compression_due(messages: list[dict[str, Any]]) -> bool:
    """Return whether a persisted transcript is large enough to summarize."""
    return len(messages) >= _COMPRESSION_MIN_MESSAGES


def _compression_transcript(
    messages: list[dict[str, Any]],
    assistant_name: str,
) -> tuple[list[str], list[str]]:
    user_lines: list[str] = []
    context_lines: list[str] = []
    for message in messages:
        content = str(message.get("content") or "")[:200]
        if message.get("role") == "user":
            user_lines.append(f"User: {content}")
            context_lines.append(f"User: {content}")
        else:
            context_lines.append(f"{assistant_name}: {content}")
    return user_lines, context_lines


async def _extract_compressed_memories(
    user_lines: list[str],
    context_lines: list[str],
    *,
    model_gateway: Any,
    session_id: str = "",
) -> str:
    language = app_language()
    if language == "zh":
        prompt = f"""从下方用户消息中提取关键信息，重点关注：
1. 用户事实（工作、偏好、习惯）
2. 情绪模式或反复话题
3. 待办事项或已做决定

每条结果分类为：fact | pattern | preference | emotion

完整对话上下文（仅供参考）：
{chr(10).join(context_lines)}

待分析的用户消息：
{chr(10).join(user_lines)}

输出格式（每行一条，不要解释）：
[fact] 用户在一家科技公司工作
[emotion] 用户因项目截止日期感到沮丧
[preference] 用户喜欢轻松简短的回复
"""
    else:
        prompt = f"""Extract key information from the USER's messages below. Focus on:
1. Facts about the user (job, preferences, habits)
2. Emotional patterns or recurring topics
3. Action items or decisions made

For each finding, classify as: fact | pattern | preference | emotion

Full conversation context (for reference only):
{chr(10).join(context_lines)}

User messages to analyse:
{chr(10).join(user_lines)}

Output format (one per line, no explanations):
[fact] user works at a tech company
[emotion] user was frustrated about a project deadline
[preference] user likes casual short replies
"""
    from cyrene.model_runtime.messages import assistant_text

    if model_gateway is None or not callable(getattr(model_gateway, "complete", None)):
        raise RuntimeError("Memory model gateway is unavailable")
    response = await model_gateway.complete(
        [
            {
                "role": "system",
                "content": localized(
                    "You extract structured memories from conversations. Be concise.",
                    "你负责从对话中提取结构化记忆。保持简洁。",
                    language=language,
                ),
            },
            {"role": "user", "content": prompt},
        ],
        tools=None,
        max_tokens=1200,
        caller="compactor",
        route="primary",
        session_id=session_id,
    )
    return assistant_text(response) or ""


def _store_compressed_memories(compressed: str) -> None:
    for raw_line in compressed.splitlines():
        line = raw_line.strip()
        if not line.startswith("["):
            continue
        try:
            closing = line.index("]")
        except ValueError:
            continue
        entry_type = line[1:closing].strip()
        content = line[closing + 1 :].strip()
        if len(content) <= 3:
            continue
        lowered = content.casefold()
        valence = -2 if any(term in lowered for term in ("frustrat", "stress", "angry")) else 2 if any(term in lowered for term in ("happy", "love", "excit")) else 0
        touch_entry(
            content,
            {
                "content": content,
                "type": entry_type,
                "emotional_valence": valence,
            },
        )


async def compress_messages(
    all_messages: list[dict[str, Any]],
    *,
    session_id: str = "",
    model_gateway: Any = None,
) -> None:
    """Extract cross-session short-term memory from a global conversation."""

    if session_id:
        from cyrene.workbench.sessions.context import resolve_workbench_project_id_for_session

        if resolve_workbench_project_id_for_session(session_id) is not None:
            return

    eligible = [message for message in all_messages if isinstance(message, dict) and message.get("role") in {"user", "assistant"} and not bool(message.get("hidden_from_ui"))]
    window = eligible[-_COMPRESSION_WINDOW_MESSAGES:]
    if not window:
        return

    from cyrene.config import ASSISTANT_NAME

    user_lines, context_lines = _compression_transcript(window, ASSISTANT_NAME)
    if not user_lines:
        return
    try:
        compressed = await _extract_compressed_memories(
            user_lines,
            context_lines,
            model_gateway=model_gateway,
            session_id=session_id,
        )
    except Exception:
        logger.warning("Memory compression failed", exc_info=True)
        return
    _store_compressed_memories(compressed)
