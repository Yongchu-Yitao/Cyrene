"""Shared text-file write conventions."""

from __future__ import annotations


def normalize_text_for_write(
    content: str,
    *,
    existing_content: str | None,
    add_final_newline: bool = False,
) -> str:
    """Preserve an existing file's final newline convention.

    A content edit must not gain an unrelated Git change because its writer
    silently dropped the final newline. Existing files keep their current EOF
    convention; callers creating source files may opt into a conventional LF.
    """

    if not content or content.endswith(("\n", "\r")):
        return content
    if existing_content is not None:
        if existing_content.endswith("\r\n"):
            return content + "\r\n"
        if existing_content.endswith("\n"):
            return content + "\n"
        if existing_content.endswith("\r"):
            return content + "\r"
        return content
    return content + "\n" if add_final_newline else content


__all__ = ["normalize_text_for_write"]
