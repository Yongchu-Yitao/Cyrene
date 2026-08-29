"""Background memory stewardship owned by the editable memory Plugin."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from cyrene.localization import app_language
from cyrene.runtime.io import atomic_write_json, read_json_safe

logger = logging.getLogger(__name__)

_SOUL_SECTIONS = (
    "SELF:IDENTITY",
    "SELF:BELIEFS",
    "RELATIONSHIP:USER",
    "MEMORY:HIGH_IMPACT",
    "PATTERN:USER",
    "TEMPORARY",
)
_SOUL_SECTION_PATTERN = "(?:" + "|".join(re.escape(item) for item in _SOUL_SECTIONS) + ")"
_SOUL_COMMAND_PATTERN = re.compile(
    rf"^(APPEND|ERASE|MERGE):?\s+\(?({_SOUL_SECTION_PATTERN})\)?\s*"
    rf"(?:::|:|—|\||-)\s*(.+)$",
    re.IGNORECASE,
)
_DAILY_ARCHIVE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}\.md")


def normalize_soul_commands(result: str) -> str:
    """Extract normalized SOUL commands from a mixed steward response."""

    commands: list[str] = []
    for raw_line in str(result or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper() == "SKIP":
            commands.append("SKIP")
            continue
        match = _SOUL_COMMAND_PATTERN.match(line)
        if match:
            command, section, content = match.groups()
            commands.append(f"{command.upper()} {section.upper()} :: {content.strip()}")
    return "\n".join(commands)


def has_daily_conversation(conversations_directory: Path, *, now: float | None = None) -> bool:
    """Return whether today's daily archive contains an actual exchange."""

    from datetime import datetime, timezone

    today = datetime.fromtimestamp(
        float(now if now is not None else time.time()),
        tz=timezone.utc,
    ).strftime("%Y-%m-%d")
    path = conversations_directory / f"{today}.md"
    try:
        return path.is_file() and "##" in path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        logger.exception("Failed to inspect the daily conversation archive")
        return False


def recent_workbench_conversations(
    application: Any,
    since_timestamp: float | None,
    *,
    now: float | None = None,
    max_files: int = 12,
    max_chars: int = 80_000,
    max_chars_per_file: int = 12_000,
) -> str:
    """Read bounded, recently modified per-session conversation archives."""

    from cyrene.workbench.sessions.context import read_projects

    current = float(now if now is not None else time.time())
    cutoff = float(since_timestamp) if since_timestamp is not None else current - 24 * 60 * 60
    directories: dict[Path, str] = {application.conversations_directory: "default"}
    try:
        for project in read_projects():
            if not isinstance(project, dict):
                continue
            workspace_path = str(project.get("workspacePath") or "").strip()
            if workspace_path:
                directories[application.session_conversations_directory(workspace_path)] = str(project.get("id") or "default")
    except Exception:
        logger.debug("Could not enumerate project conversation archives", exc_info=True)

    candidates: list[tuple[float, Path, str]] = []
    for directory, project_id in directories.items():
        try:
            for path in directory.glob("*.md"):
                if _DAILY_ARCHIVE_NAME.fullmatch(path.name):
                    continue
                modified_at = path.stat().st_mtime
                if modified_at > cutoff:
                    candidates.append((modified_at, path, project_id))
        except OSError:
            logger.debug("Could not scan conversations in %s", directory, exc_info=True)

    candidates.sort(key=lambda item: item[0], reverse=True)
    parts: list[str] = []
    used = 0
    for _modified_at, path, project_id in candidates[:max_files]:
        try:
            excerpt = path.read_text(encoding="utf-8")[-max_chars_per_file:]
        except (OSError, UnicodeError):
            logger.debug("Could not read conversation archive %s", path, exc_info=True)
            continue
        block = f"=== Workbench conversation: {path.name} project_id={project_id} ===\n{excerpt}"
        if parts and used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(reversed(parts))


def _entity_field(line: str, name: str) -> str:
    match = re.search(rf'{re.escape(name)}="([^"]*)"', line)
    return match.group(1).strip() if match else ""


async def _existing_entity_hint(entity_service: Any | None) -> str:
    if entity_service is None:
        return ""
    try:
        existing = await entity_service.list(limit=200)
    except Exception:
        logger.warning("Failed to query entities for steward deduplication", exc_info=True)
        return ""
    return "\n".join(
        f"- [project={item.get('project_id') or 'default'}] [{item.get('type') or 'unknown'}] {item.get('title') or ''}" for item in existing[:50] if isinstance(item, dict)
    )


def _steward_prompt(
    conversation_text: str,
    soul_content: str,
    entity_hint: str,
    *,
    language: str = "",
) -> str:
    target_language = "English" if app_language(language) == "en" else "Simplified Chinese"
    return f"""You are Cyrene's memory steward and entity extractor.

Update SOUL.md from the recent conversations. Every modification MUST use one
of these exact line formats (including the literal `` :: `` separator):
- APPEND SECTION_NAME :: content to append
- ERASE SECTION_NAME :: exact substring to remove
- MERGE SECTION_NAME :: old_text|||new_text
- SKIP

SECTION_NAME must be an existing heading such as MEMORY:HIGH_IMPACT,
PATTERN:USER, RELATIONSHIP:USER, or SELF:BELIEFS. Record only durable facts
about what the user's projects are doing, important habits, explicit
requirements, and personal information. Do not store implementation details,
file names, code changes, or step-by-step procedures.
Write all natural-language content you add in {target_language}; preserve code,
identifiers, paths, commands, model names, and proper nouns exactly.

Also extract real entities mentioned by the user using this exact format:
ENTITY project_id="project_abc" type="task" title="Buy groceries" confidence="0.85" content="User plans to buy groceries"

Supported entity types: task, project, decision, knowledge, relationship,
event, resource, idea, problem, habit. Use the project_id from each Workbench
conversation header; use "default" for global conversations. Do not emit
hypothetical, casual, or duplicate entities. Confidence below 0.2 must be
ignored; 0.8 or above means a clear actionable mention.

Existing entities (do not duplicate):
{entity_hint or "(none yet)"}

SOUL.md:
{soul_content}

Recent conversations:
{conversation_text}

Output only SOUL commands followed by ENTITY lines, one item per line."""


async def _run_model(
    conversation_text: str,
    soul_content: str,
    entity_service: Any | None,
    *,
    model_gateway: Any,
    session_id: str = "memory-steward",
) -> str:
    prompt = _steward_prompt(
        conversation_text,
        soul_content,
        await _existing_entity_hint(entity_service),
        language=app_language(),
    )
    if model_gateway is None or not callable(getattr(model_gateway, "complete", None)):
        raise RuntimeError("Memory model gateway is unavailable")
    response = await model_gateway.complete(
        [{"role": "user", "content": prompt}],
        tools=None,
        max_tokens=4000,
        caller="memory_steward",
        route="secondary",
        session_id=session_id,
    )
    return str(response.get("content") or "").strip()


async def _apply_entities(result: str, entity_service: Any | None) -> None:
    if entity_service is None:
        return
    for raw_line in result.splitlines():
        line = raw_line.strip()
        if not line.upper().startswith("ENTITY "):
            continue
        entity_type = _entity_field(line, "type")
        title = _entity_field(line, "title")
        confidence_text = _entity_field(line, "confidence")
        if not entity_type or not title or not confidence_text:
            continue
        try:
            confidence = float(confidence_text)
        except ValueError:
            logger.debug("Ignoring malformed steward entity confidence: %s", line)
            continue
        if confidence < 0.2:
            continue
        project_id = _entity_field(line, "project_id") or "default"
        try:
            if await entity_service.has_similar(
                entity_type,
                title,
                project_id=project_id,
            ):
                continue
            await entity_service.add_candidate(
                type=entity_type,
                title=title,
                content=_entity_field(line, "content"),
                confidence=confidence,
                project_id=project_id,
                raw_text=line,
            )
        except Exception:
            logger.exception("Failed to persist a steward entity candidate")
    try:
        promoted = await entity_service.process_candidates()
        if promoted:
            logger.info("Steward promoted %d entity candidate(s)", len(promoted))
    except Exception:
        logger.exception("Failed to promote steward entity candidates")


async def run_steward_if_needed(
    application: Any,
    *,
    interval: int,
    now: float | None = None,
    model_runner: Any = None,
    entity_service: Any = None,
    soul_application: Any = None,
) -> bool:
    """Run one due stewardship pass and persist its Plugin-owned cursor."""

    current = float(now if now is not None else time.time())
    state_path = application.data_directory / "memory_steward.json"
    raw_state = read_json_safe(state_path)
    state = raw_state if isinstance(raw_state, dict) else {}
    last_run = float(state.get("last_run") or 0)
    if last_run and current - last_run < max(1, int(interval)):
        return False

    global_text = await application.recent_conversations(days=1) if has_daily_conversation(application.conversations_directory, now=current) else ""
    workbench_text = recent_workbench_conversations(
        application,
        last_run or None,
        now=current,
    )
    conversation_text = "\n\n".join(part for part in (global_text, workbench_text) if part)
    if not conversation_text:
        return False

    if entity_service is None:
        from cyrene.core.plugin import application_plugin_service

        entity_service = application_plugin_service("entities")
    soul_content = (
        str(soul_application.read() or "")
        if soul_application is not None
        else ""
    )
    if model_runner is None:
        result = await _run_model(
            conversation_text,
            soul_content,
            entity_service,
            model_gateway=getattr(application, "model_gateway", None),
        )
    else:
        result = await model_runner(
            conversation_text,
            soul_content,
            entity_service,
        )
    result = str(result or "").strip()
    commands = normalize_soul_commands(result)
    if (
        soul_application is not None
        and commands
        and commands.upper() != "SKIP"
    ):
        changes = soul_application.apply_update(commands)
        logger.info("Steward applied %d SOUL change(s)", len(changes))
    await _apply_entities(result, entity_service)
    atomic_write_json(state_path, {"last_run": current})
    return True


__all__ = [
    "has_daily_conversation",
    "normalize_soul_commands",
    "recent_workbench_conversations",
    "run_steward_if_needed",
]
