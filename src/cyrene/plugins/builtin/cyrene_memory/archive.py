"""Conversation archiving owned by the editable memory Plugin."""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from cyrene.config import ASSISTANT_NAME, WORKSPACE_DIR, cyrene_dir
from cyrene.localization import app_language, localized
from .archive_format import (
    parse_archive_meta,
    parse_archive_sections,
)

logger = logging.getLogger(__name__)

CONVERSATIONS_DIR = cyrene_dir(WORKSPACE_DIR) / "conversations"
_STATS_DB_PATH = ""


def configure_archive(db_path: str) -> None:
    """Bind archive analytics to the database owned by this Plugin host."""
    global _STATS_DB_PATH
    _STATS_DB_PATH = str(db_path or "").strip()


def ensure_conversations_dir() -> None:
    """Create conversations directory if it doesn't exist."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Per-session conversation files (Workbench)
#
# Global channels funnel exchanges into one shared daily file. Workbench
# conversations instead get ONE Markdown
# file per conversation id, written under the project's own workspace, so each
# conversation is independently readable by id and the agent can ``Read``/``Grep``
# its own history straight from its workspace: ``conversations/<session_id>.md``.
# ---------------------------------------------------------------------------

def _safe_session_filename(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(session_id or "").strip()).strip("._")
    return cleaned or "session"


def session_conversations_dir(workspace_dir: str | Path | None = None) -> Path:
    """Return the ``conversations/`` dir for a workspace.

    A non-empty ``workspace_dir`` (a Workbench project's workspacePath) scopes
    archives to that project; empty/None falls back to the global
    ``WORKSPACE_DIR/.cyrene/conversations`` so non-project runs keep their location.
    """
    base = Path(workspace_dir).expanduser() if workspace_dir else WORKSPACE_DIR
    return cyrene_dir(base) / "conversations"


def session_conversation_file(
    session_id: str, workspace_dir: str | Path | None = None
) -> Path:
    """Path to a single conversation's archive file (may not exist yet)."""
    return session_conversations_dir(workspace_dir) / f"{_safe_session_filename(session_id)}.md"


def _upsert_session_file_header(content: str, session_id: str, session_title: str) -> str:
    """Build/refresh the per-session file header, preserving existing entries."""
    title_line = f"<!-- session_title: {session_title} -->\n" if session_title else ""
    header = (
        f"# Conversation {session_id}\n\n"
        f"<!-- session_id: {session_id} -->\n"
        f"{title_line}\n"
    )
    if content.startswith("# Conversation "):
        match = re.search(r"(?m)^##\s", content)
        body = content[match.start():] if match else ""
        return header + body
    return header + content


def archive_session_exchange(
    session_id: str,
    user_message: str,
    assistant_response: str,
    *,
    workspace_dir: str | Path | None = None,
    session_title: str = "",
    round_id: str = "",
    language: str = "",
) -> Path | None:
    """Append one user/assistant exchange to ``conversations/<session_id>.md``.

    Best-effort: returns the file path on success, ``None`` on any failure
    (callers must never let archiving break the live reply).
    """
    sid = str(session_id or "").strip()
    if not sid:
        return None
    normalized_round_id = re.sub(
        r"[^A-Za-z0-9._:-]+",
        "_",
        str(round_id or "").strip(),
    ).strip("._")
    round_marker = (
        f"<!-- round_id: {normalized_round_id} -->" if normalized_round_id else ""
    )
    directory = session_conversations_dir(workspace_dir)
    filepath = directory / f"{_safe_session_filename(sid)}.md"
    now = datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S %Z").strip() or now.strftime("%Y-%m-%d %H:%M:%S")
    language = app_language(language)
    user_text = str(user_message or "").strip() or localized(
        "(no text)", "（无文本）", language=language
    )
    assistant_text = str(assistant_response or "").strip()
    round_block = f"{round_marker}\n\n" if round_marker else ""
    entry = (
        f"## {timestamp}\n\n"
        f"{round_block}"
        f"**User**: {user_text}\n\n"
        f"**{ASSISTANT_NAME}**: {assistant_text}\n\n"
        f"---\n\n"
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        content = filepath.read_text(encoding="utf-8") if filepath.exists() else ""
        if round_marker and round_marker in content:
            return filepath
        content = _upsert_session_file_header(content, sid, session_title)
        filepath.write_text(content + entry, encoding="utf-8")

        # Keep the profile activity heatmap in sync with Workbench conversations.
        try:
            from cyrene.platform import database as cy_db

            if _STATS_DB_PATH:
                cy_db.bump_activity_sync(_STATS_DB_PATH, timestamp=now.isoformat())
        except Exception:
            logger.exception("Failed to bump activity for session archive")

        logger.debug("Archived conversation exchange to %s", filepath)
        return filepath
    except Exception:
        logger.exception("Failed to archive session exchange to %s", filepath)
        return None


def _get_today_file() -> Path:
    """Get the conversation file for today."""
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    return CONVERSATIONS_DIR / f"{today}.md"


def upsert_archive_session_title(
    content: str,
    date_str: str,
    session_title: str,
) -> str:
    """Insert or replace the daily archive's optional session-title marker."""
    header = f"# Conversations - {date_str}\n\n"
    if not content:
        content = header
    elif not content.startswith("# Conversations - "):
        content = header + content

    if not session_title:
        return content

    marker = f"<!-- session_title: {session_title} -->\n\n"
    pattern = re.compile(r"^(# Conversations - .*?\n\n)(?:<!-- session_title: .*? -->\n\n)?", re.DOTALL)
    if pattern.search(content):
        return pattern.sub(lambda match: match.group(1) + marker, content, count=1)
    return header + marker + content[len(header):]


async def archive_exchange(
    user_message: str,
    assistant_response: str,
    chat_id: int,
    session_title: str = "",
    round_title: str = "",
    round_id: str = "",
    archive_session_id: str = "",
) -> None:
    """Archive a single user-assistant exchange to today's conversation file.

    Format:
    ## HH:MM:SS UTC

    **User**: <message>

    **Assistant**: <response>

    ---
    """
    ensure_conversations_dir()

    filepath = _get_today_file()
    now = datetime.now().astimezone()
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%H:%M:%S UTC")
    stats_timestamp = now.isoformat()
    meta_lines = []
    if archive_session_id:
        meta_lines.append(f"<!-- archive_session_id: {archive_session_id} -->")
    if session_title:
        meta_lines.append(f"<!-- session_title: {session_title} -->")
    if round_id:
        meta_lines.append(f"<!-- round_id: {round_id} -->")
    if round_title:
        meta_lines.append(f"<!-- round_title: {round_title} -->")
    meta_block = ("\n".join(meta_lines) + "\n\n") if meta_lines else ""

    # Build the exchange entry
    entry = f"""## {timestamp}

{meta_block}**User**: {user_message}

**{ASSISTANT_NAME}**: {assistant_response}

---

"""

    # Append to file (create if doesn't exist)
    try:
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
        else:
            # Create file with header
            content = f"# Conversations - {date_str}\n\n"

        content = upsert_archive_session_title(content, date_str, session_title)
        content += entry
        filepath.write_text(content, encoding="utf-8")
        if _STATS_DB_PATH:
            from cyrene.platform import database as cy_db

            await cy_db.record_archive_exchange(
                _STATS_DB_PATH,
                timestamp=stats_timestamp,
                user_message=user_message,
                assistant_response=assistant_response,
            )
        logger.debug(f"Archived exchange to {filepath}")
    except Exception:
        logger.exception(f"Failed to archive exchange to {filepath}")


async def get_recent_conversations(days: int = 1) -> str:
    """Return conversation records from the last *days* days.

    Each day is prefixed with ``=== YYYY-MM-DD ===`` for easy parsing.

    Returns an empty string when no conversation files are found.
    """
    ensure_conversations_dir()
    now = datetime.now().astimezone()
    result_parts: list[str] = []

    for i in range(days):
        date = now - timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        filepath = CONVERSATIONS_DIR / f"{date_str}.md"
        try:
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8")
                result_parts.append(f"=== {date_str} ===\n{content}")
        except Exception:
            logger.exception("Failed to read conversation file %s", filepath)

    return "\n\n".join(result_parts).strip() if result_parts else ""


async def search_conversations(keyword: str, path: str | None = None) -> str:
    """Search conversation history for *keyword* using plain-text matching.

    This is a simple line-by-line substring search (case-insensitive) that
    does NOT use RAG or vector embeddings.  It is intentionally lightweight
    and works even when ``grep`` is unavailable on the host system.

    Args:
        keyword: The text to search for.
        path: Optional subdirectory under CONVERSATIONS_DIR to scope search.
              Defaults to the entire conversations directory.

    Returns:
        Matching lines prefixed with ``filename:line_number:``, or the string
        "No matches found."
    """
    ensure_conversations_dir()

    search_root = CONVERSATIONS_DIR
    if path:
        search_root = search_root / path

    matches: list[str] = []
    kw_lower = keyword.lower()

    try:
        # Collect all .md files sorted by name (i.e. chronologically)
        files = sorted(search_root.glob("**/*.md"))
    except Exception:
        logger.exception("Failed to list conversation files")
        return localized(
            "Error searching conversations.",
            "搜索对话时出错。",
            language=app_language(),
        )

    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for line_no, line in enumerate(content.splitlines(), start=1):
            if kw_lower in line.lower():
                rel = filepath.relative_to(CONVERSATIONS_DIR)
                matches.append(f"{rel}:{line_no}:{line}")
                if len(matches) >= 200:
                    break

        if len(matches) >= 200:
            break

    return "\n".join(matches) if matches else localized(
        "No matches found.",
        "未找到匹配项。",
        language=app_language(),
    )


def _split_session_entry_blocks(content: str) -> list[str]:
    blocks: list[str] = []
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", content))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        block = content[start:end].strip()
        block = re.sub(r"\n+---\s*\Z", "", block).strip()
        if block:
            blocks.append(block)
    return blocks


def _parse_session_file_sections(content: str, filepath: Path) -> list[dict[str, str]]:
    """Parse Workbench per-session conversation files.

    Workbench archives one Markdown file per conversation id:
    ``conversations/<session_id>.md``. This parser mirrors the global archive
    shape closely enough that RecallConversation can return a consistent
    payload while searching the project workspace directly.
    """
    sections_out: list[dict[str, str]] = []
    session_id = parse_archive_meta(content, "session_id")
    if not session_id:
        header_match = re.search(r"(?m)^#\s+Conversation\s+(.+?)\s*$", content)
        session_id = header_match.group(1).strip() if header_match else filepath.stem
    session_title = parse_archive_meta(content, "session_title")

    round_index = 0
    for section in _split_session_entry_blocks(content):
        if "**User**:" not in section:
            continue
        ts_match = re.search(r"^##\s*(.+?)\s*$", section, re.MULTILINE)
        dialogue_match = re.search(r"\*\*User\*\*:\s*(.*?)\n+\*\*[^*]+\*\*:\s*(.*)\Z", section, re.DOTALL)
        if not ts_match or not dialogue_match:
            continue

        timestamp = ts_match.group(1).strip()
        date_match = re.match(r"(\d{4}-\d{2}-\d{2})\b", timestamp)
        round_id = parse_archive_meta(section, "round_id") or (
            f"{session_id}:{round_index}"
            if session_id
            else f"{filepath.stem}:{round_index}"
        )
        sections_out.append({
            "date": date_match.group(1) if date_match else "",
            "timestamp": timestamp,
            "archive_session_id": session_id,
            "session_id": session_id,
            "session_title": session_title,
            "round_id": round_id,
            "round_title": "",
            "user_body": dialogue_match.group(1).strip(),
            "assistant_body": dialogue_match.group(2).strip(),
            "raw_entry": section.strip(),
            "source_file": str(filepath),
            "source": "workbench_workspace",
        })
        round_index += 1

    return sections_out


def load_session_conversation_entries(
    session_id: str,
    workspace_dir: str | Path | None = None,
) -> list[dict[str, str]]:
    """Load every archived round for one Workbench conversation in order.

    This is intentionally lossless and has no result/snippet limit.  Context
    rewriting uses it to retain the user's original completed-turn text even
    after the corresponding active ContextTree nodes are consolidated.
    """

    filepath = session_conversation_file(session_id, workspace_dir)
    if not filepath.is_file():
        return []
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed to read session conversation file %s", filepath)
        return []
    return _parse_session_file_sections(content, filepath)


async def search_conversations_structured(
    query: str,
    limit: int = 30,
    max_context_chars: int = 300,
) -> list[dict[str, str]]:
    """Full-text search across all conversation archives.

    Returns structured results sorted by date descending, with matching
    context snippets and conversation metadata.

    Args:
        query: Search text (case-insensitive).
        limit: Maximum number of results to return.
        max_context_chars: Max characters for the matching snippet.

    Returns:
        List of dicts with keys: date, timestamp, user_body, assistant_body,
        session_title, snippet, file_path.
    """
    ensure_conversations_dir()
    kw_lower = query.strip().lower()
    if not kw_lower:
        return []

    files = sorted(CONVERSATIONS_DIR.glob("*.md"), reverse=True)
    results: list[dict[str, str]] = []

    for filepath in files:
        if not filepath.exists():
            continue
        date_str = filepath.stem
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to read conversation file %s", filepath)
            continue

        sections = parse_archive_sections(content, date_str)
        for section in sections:
            user_body = str(section.get("user_body") or "").strip()
            assistant_body = str(section.get("assistant_body") or "").strip()
            session_title = str(section.get("session_title") or "").strip()
            haystack = f"{user_body} {assistant_body} {session_title}".lower()

            if kw_lower not in haystack:
                continue

            # build the best snippet around the match
            body = f"{user_body} {assistant_body}"
            snippet = _build_search_snippet(body, query, max_chars=max_context_chars)

            results.append({
                "date": str(section.get("date") or date_str),
                "timestamp": str(section.get("timestamp") or ""),
                "user_body": user_body,
                "assistant_body": assistant_body,
                "session_title": session_title,
                "snippet": snippet,
                "file_path": str(filepath),
            })

            if len(results) >= limit:
                return results

    return results


def _build_search_snippet(text: str, query: str, max_chars: int = 300) -> str:
    """Extract a relevant snippet around the first match of *query* in *text*."""
    kw_lower = query.strip().lower()
    body = text.strip()
    if not kw_lower or not body:
        return body[:max_chars]

    idx = body.lower().find(kw_lower)
    if idx < 0:
        return body[:max_chars]

    start = max(0, idx - max_chars // 2)
    end = min(len(body), idx + len(kw_lower) + max_chars // 2)

    snippet = body[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(body):
        snippet = snippet + "…"
    return snippet


def recall_workspace_conversations(
    workspace_dir: str | Path,
    query: str = "",
    session_id: str = "",
    date: str = "",
    limit: int = 5,
) -> list[dict[str, str]]:
    """Return Workbench conversation entries from one workspace.

    Searches every ``conversations/*.md`` file under *workspace_dir*, which is
    the storage layout used by Workbench chats/tasks. Results are newest first.
    """
    directory = session_conversations_dir(workspace_dir)
    normalized_query = query.strip().lower()
    normalized_session_id = session_id.strip()
    normalized_date = date.strip()

    try:
        files = sorted(directory.glob("*.md"), reverse=True)
    except Exception:
        logger.exception("Failed to list workspace conversation files in %s", directory)
        return []

    if normalized_session_id:
        exact_file = directory / f"{_safe_session_filename(normalized_session_id)}.md"
        if exact_file.exists():
            files = [exact_file]

    matches: list[dict[str, str]] = []
    for filepath in files:
        if not filepath.exists():
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to read workspace conversation file %s", filepath)
            continue

        for section in _parse_session_file_sections(content, filepath):
            section_session_id = str(
                section.get("session_id") or section.get("archive_session_id") or ""
            ).strip()
            safe_filter_id = _safe_session_filename(normalized_session_id)
            if (
                normalized_session_id
                and section_session_id != normalized_session_id
                and filepath.stem != safe_filter_id
            ):
                continue
            if normalized_date and section.get("date", "") != normalized_date:
                continue
            if normalized_query:
                haystack = "\n".join([
                    section.get("session_title", ""),
                    section.get("round_title", ""),
                    section.get("user_body", ""),
                    section.get("assistant_body", ""),
                    section.get("raw_entry", ""),
                ]).lower()
                if normalized_query not in haystack:
                    continue
            matches.append(section)

    matches.sort(
        key=lambda item: (
            str(item.get("timestamp") or ""),
            str(item.get("source_file") or ""),
            str(item.get("round_id") or ""),
        ),
        reverse=True,
    )
    return matches[:max(1, limit)]


def recall_conversations(
    query: str = "",
    session_id: str = "",
    date: str = "",
    limit: int = 5,
) -> list[dict[str, str]]:
    """Return archived conversation entries matching the given filters.

    Results are ordered from newest to oldest and are intended for agent recall,
    not for exact full-history replay.
    """
    ensure_conversations_dir()

    normalized_query = query.strip().lower()
    normalized_session_id = session_id.strip()
    if normalized_session_id.startswith("archive_"):
        _, _, normalized_session_id = normalized_session_id.partition("_")
        date_prefix, sep, archive_suffix = normalized_session_id.partition("_")
        if sep and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_prefix):
            if not date:
                date = date_prefix
            normalized_session_id = archive_suffix

    files: list[Path]
    if date:
        files = [CONVERSATIONS_DIR / f"{date}.md"]
    else:
        files = sorted(CONVERSATIONS_DIR.glob("*.md"), reverse=True)

    matches: list[dict[str, str]] = []
    for filepath in files:
        if not filepath.exists():
            continue
        date_str = filepath.stem
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to read conversation file %s", filepath)
            continue

        sections = parse_archive_sections(content, date_str)
        for section in reversed(sections):
            if normalized_session_id and section.get("archive_session_id", "").strip() != normalized_session_id:
                continue
            if normalized_query:
                haystack = "\n".join([
                    section.get("session_title", ""),
                    section.get("round_title", ""),
                    section.get("user_body", ""),
                    section.get("assistant_body", ""),
                    section.get("raw_entry", ""),
                ]).lower()
                if normalized_query not in haystack:
                    continue
            matches.append(section)
            if len(matches) >= max(1, limit):
                return matches

    return matches


def get_archived_round(
    archive_session_id: str,
    round_id: str,
) -> dict[str, str] | None:
    """Return one archived round by exact archive session + round id match."""
    ensure_conversations_dir()
    target_session_id = str(archive_session_id or "").strip()
    target_round_id = str(round_id or "").strip()
    if not target_session_id or not target_round_id:
        return None

    for filepath in sorted(CONVERSATIONS_DIR.glob("*.md"), reverse=True):
        if not filepath.exists():
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to read conversation file %s", filepath)
            continue

        for section in reversed(parse_archive_sections(content, filepath.stem)):
            if str(section.get("archive_session_id", "")).strip() != target_session_id:
                continue
            if str(section.get("round_id", "")).strip() != target_round_id:
                continue
            return section

    return None
