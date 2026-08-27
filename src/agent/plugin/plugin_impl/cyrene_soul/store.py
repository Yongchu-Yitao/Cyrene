"""SOUL.md storage owned by the editable Soul Plugin pack."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cyrene.config import ASSISTANT_NAME, WORKSPACE_DIR, cyrene_dir
from cyrene.localization import localized

logger = logging.getLogger(__name__)

_DEFAULT_SOUL_EN = """# {name}'s Soul

## SELF:IDENTITY
- I am {name}, a personal AI companion, not an assistant.
- My purpose is to be a friend and companion.

## SELF:BELIEFS

## RELATIONSHIP:USER
- Trust level: neutral
- Communication style: casual, direct

## MEMORY:HIGH_IMPACT

## PATTERN:USER

## TEMPORARY
<!-- Items with 24h expiry — timestamps are used for cleanup. -->
"""

_DEFAULT_SOUL_ZH = """# {name} 的人格

## SELF:IDENTITY
- 我是 {name}，一位私人 AI 伙伴，而不是工具式助手。
- 我的目标是成为用户的朋友与陪伴者。

## SELF:BELIEFS

## RELATIONSHIP:USER
- 信任程度：中立
- 沟通风格：随和、直接

## MEMORY:HIGH_IMPACT

## PATTERN:USER

## TEMPORARY
<!-- 此处内容保留 24 小时；时间戳用于自动清理。 -->
"""


def soul_path() -> Path:
    return cyrene_dir(WORKSPACE_DIR) / "SOUL.md"


def default_soul(
    name: str | None = None,
    *,
    language: Any = None,
) -> str:
    return localized(
        _DEFAULT_SOUL_EN,
        _DEFAULT_SOUL_ZH,
        language=language,
        name=name or ASSISTANT_NAME,
    )


def ensure_soul() -> None:
    path = soul_path()
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_soul(), encoding="utf-8")


def read_soul() -> str:
    ensure_soul()
    return soul_path().read_text(encoding="utf-8")


def write_soul(content: str) -> None:
    path = soul_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content or ""), encoding="utf-8")


def reset_soul() -> None:
    soul_path().unlink(missing_ok=True)
    ensure_soul()


def read_persona_context() -> str:
    """Return SOUL.md with expired temporary entries omitted."""

    content = read_soul()
    if not content:
        return ""
    now = datetime.now(timezone.utc)
    expiry = timedelta(hours=24)
    result: list[str] = []
    in_temporary = False
    for line in content.splitlines(keepends=True):
        trimmed = line.strip()
        if trimmed.startswith("## ") and not trimmed.startswith("###"):
            in_temporary = trimmed[3:].strip() == "TEMPORARY"
            result.append(line)
            continue
        if in_temporary and trimmed and not trimmed.startswith("<!--"):
            expired = False
            for token in trimmed.split():
                try:
                    item_date = datetime.strptime(token[:10], "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    continue
                expired = now - item_date > expiry
                break
            if expired:
                continue
        result.append(line)
    return "".join(result).strip()


def _section_name(line: str) -> str | None:
    trimmed = line.strip()
    if trimmed.startswith("## ") and not trimmed.startswith("###"):
        return trimmed[3:].strip()
    return None


def _section_range(lines: list[str], name: str) -> tuple[int, int] | None:
    start: int | None = None
    for index, line in enumerate(lines):
        section = _section_name(line)
        if section == name:
            start = index
        elif section is not None and start is not None:
            return start, index
    return (start, len(lines)) if start is not None else None


def _insert_point(lines: list[str], start: int, end: int) -> int:
    for index in range(end - 1, start - 1, -1):
        if index < len(lines) and lines[index].strip() not in {"", "---"}:
            return max(start + 1, index + 1)
    return start + 1


def apply_soul_update(update_commands: str) -> list[str]:
    """Apply steward APPEND/ERASE/MERGE commands to the owned SOUL.md."""

    if not str(update_commands or "").strip():
        return []
    ensure_soul()
    path = soul_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        logger.exception("Failed to read SOUL.md for update")
        return []
    changes: list[str] = []
    for raw_line in str(update_commands).splitlines():
        command_line = raw_line.strip()
        if not command_line or command_line.upper().startswith("SKIP"):
            continue
        command, separator, rest = command_line.partition(" ")
        if not separator or " :: " not in rest:
            logger.warning("Malformed SOUL update command: %s", command_line)
            continue
        section, _, content = rest.partition(" :: ")
        section = section.strip()
        content = content.strip()
        section_bounds = _section_range(lines, section)
        if section_bounds is None:
            logger.warning("SOUL section '%s' not found", section)
            continue
        start, end = section_bounds
        operation = command.upper()
        if operation == "APPEND":
            value = content if content.startswith(("- ", "  - ")) else "- " + content
            lines.insert(_insert_point(lines, start, end), value.rstrip("\n") + "\n")
            changes.append(f"APPEND {section}: {value.strip()}")
        elif operation == "ERASE":
            removed = False
            index = start + 1
            while index < end:
                if content in lines[index]:
                    lines.pop(index)
                    end -= 1
                    removed = True
                else:
                    index += 1
            if removed:
                changes.append(f"ERASE {section}: {content}")
        elif operation == "MERGE" and "|||" in content:
            old_text, _, new_text = content.partition("|||")
            old_text = old_text.strip()
            new_text = new_text.strip()
            if not new_text:
                continue
            for index in range(start + 1, end):
                if old_text not in lines[index]:
                    continue
                if lines[index].strip().startswith("- ") and not new_text.startswith("- "):
                    new_text = "- " + new_text
                lines[index] = new_text.rstrip("\n") + "\n"
                changes.append(f"MERGE {section}: {old_text} -> {new_text.strip()}")
                break
        else:
            logger.warning("Unknown or malformed SOUL update command: %s", command_line)
    if changes:
        try:
            path.write_text("".join(lines), encoding="utf-8")
        except OSError:
            logger.exception("Failed to write updated SOUL.md")
            return []
    return changes


class SoulApplication:
    """Application/session boundary exposed by the Soul Plugin pack."""

    def startup(self) -> None:
        ensure_soul()

    def path(self) -> Path:
        return soul_path()

    def default(
        self,
        name: str | None = None,
        *,
        language: Any = None,
    ) -> str:
        return default_soul(name, language=language)

    def ensure(self) -> None:
        ensure_soul()

    def read(self) -> str:
        return read_soul()

    def write(self, content: str) -> None:
        write_soul(content)

    def reset(self) -> None:
        reset_soul()

    def persona_context(self) -> str:
        return read_persona_context()

    def presentation_state(self) -> dict[str, object]:
        """Project Soul-owned data into core UI payloads without file access there."""

        path = soul_path()
        content = read_soul()
        stat = path.stat() if path.exists() else None
        items = [
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith("- ")
        ]
        return {
            "path": str(path),
            "content": content,
            "updated_at": (
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                if stat is not None
                else ""
            ),
            "recent_items": items[-3:],
            "section_count": content.count("\n## ")
            + (1 if content.strip().startswith("# ") else 0),
        }

    def apply_update(self, commands: str) -> list[str]:
        return apply_soul_update(commands)

    def storage_paths(self) -> dict[str, tuple[Path, ...]]:
        return {"memory": (soul_path(),)}

    def backup_sources(self) -> dict[str, tuple[tuple[Path, str], ...]]:
        return {"files": ((soul_path(), "workspace/SOUL.md"),)}


__all__ = [
    "SoulApplication",
    "apply_soul_update",
    "default_soul",
    "ensure_soul",
    "read_persona_context",
    "read_soul",
    "reset_soul",
    "soul_path",
    "write_soul",
]
