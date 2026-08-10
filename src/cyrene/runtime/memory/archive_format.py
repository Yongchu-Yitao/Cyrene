"""Pure parsing helpers for Markdown conversation archives."""

from __future__ import annotations

import re


def parse_archive_meta(section: str, key: str) -> str:
    """Read one HTML-comment metadata value from an archive block."""
    match = re.search(
        rf"<!--\s*{re.escape(key)}:\s*(.*?)\s*-->",
        section,
    )
    return match.group(1).strip() if match else ""


def split_archive_entry_blocks(content: str) -> list[str]:
    """Split a daily conversation archive into timestamped exchange blocks."""
    blocks: list[str] = []
    matches = list(re.finditer(r"(?m)^##\s+\S+\s+UTC\s*$", content))
    for index, match in enumerate(matches):
        start = match.start()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(content)
        )
        block = content[start:end].strip()
        block = re.sub(r"\n+---\s*\Z", "", block).strip()
        if block:
            blocks.append(block)
    return blocks


def parse_archive_sections(
    content: str,
    date_str: str,
) -> list[dict[str, str]]:
    """Parse normalized exchanges from one daily archive."""
    sections: list[dict[str, str]] = []
    file_session_title = parse_archive_meta(content, "session_title")
    round_index = 0

    for section in split_archive_entry_blocks(content):
        if "**User**:" not in section:
            continue
        timestamp_match = re.search(r"##\s*(\S+\s+UTC)", section)
        dialogue_match = re.search(
            r"\*\*User\*\*:\s*(.*?)\n+\*\*[^*]+\*\*:\s*(.*)\Z",
            section,
            re.DOTALL,
        )
        if not timestamp_match or not dialogue_match:
            continue

        archive_session_id = parse_archive_meta(
            section,
            "archive_session_id",
        )
        is_legacy = not archive_session_id
        session_title = parse_archive_meta(
            section,
            "session_title",
        ) or (file_session_title if is_legacy else "")
        round_id = (
            parse_archive_meta(section, "round_id")
            or f"archive_round_{round_index}"
        )
        sections.append(
            {
                "date": date_str,
                "timestamp": timestamp_match.group(1).strip(),
                "archive_session_id": archive_session_id,
                "session_title": session_title,
                "round_id": round_id,
                "round_title": parse_archive_meta(
                    section,
                    "round_title",
                ),
                "user_body": dialogue_match.group(1).strip(),
                "assistant_body": dialogue_match.group(2).strip(),
                "raw_entry": section.strip(),
            }
        )
        round_index += 1

    return sections
