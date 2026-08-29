"""Slash-command parsing for Plugin Agent entrypoints."""

from __future__ import annotations

from typing import Any

DEEP_REFLECT_COMMAND_ID = "deep-reflect"
BUILTIN_COMMAND_IDS = (
    "quick-answer",
    "deep-research",
    DEEP_REFLECT_COMMAND_ID,
    "help-me-decide",
    "learning-plan",
    "daily-review",
    "deep-compare",
    "terminal",
)

_COMMAND_PROMPTS = {
    "quick-answer": (
        "Quick-answer mode: answer directly and concisely. Use tools only when "
        "the answer requires current or workspace evidence."
    ),
    "deep-research": (
        "Deep-research mode: decompose the request into independent research "
        "tracks, delegate them in parallel with the subagent Plugin, verify "
        "claims against available sources, then synthesize an evidence-backed result."
    ),
    "deep-reflect": (
        "Deep-reflection mode: inspect the conversation's goal, evidence, failed "
        "approaches, assumptions, and remaining gaps. Correct the approach and "
        "continue with the next useful action; do not merely announce reflection."
    ),
    "help-me-decide": (
        "Decision mode: identify the real options and criteria, investigate each "
        "option independently when evidence is needed, compare trade-offs, and "
        "finish with a clear recommendation and its assumptions."
    ),
    "learning-plan": (
        "Learning-plan mode: assess the learner's starting point and target, split "
        "the subject into prerequisite-aware modules, and produce a practical plan "
        "with exercises, checkpoints, and resources."
    ),
    "daily-review": (
        "Daily-review mode: summarize completed work, unresolved items, risks, and "
        "the smallest concrete priorities for the next work period."
    ),
    "deep-compare": (
        "Deep-compare mode: define comparison dimensions, investigate them "
        "independently, produce a compact comparison matrix, and recommend the best "
        "fit for the user's stated constraints."
    ),
    "terminal": (
        "Terminal mode: operate through managed terminal Plugins, report commands "
        "and outcomes precisely, and respect workspace and permission boundaries."
    ),
}


def parse_slash_command(
    text: str,
    *,
    allowed_commands: tuple[str, ...] | list[str] | set[str] = BUILTIN_COMMAND_IDS,
) -> dict[str, Any]:
    source = str(text or "").strip()
    allowed = {str(item or "").strip() for item in allowed_commands}
    if not source.startswith("/"):
        return {
            "matched": False,
            "command": "",
            "arguments": "",
            "public_text": source,
        }
    parts = source[1:].split(None, 1)
    head = parts[0] if parts else ""
    command = {"深度反思": DEEP_REFLECT_COMMAND_ID}.get(head, head)
    if command not in allowed:
        return {
            "matched": False,
            "command": "",
            "arguments": "",
            "public_text": source,
        }
    return {
        "matched": True,
        "command": command,
        "arguments": parts[1].strip() if len(parts) > 1 else "",
        "public_text": source,
    }


def parse_slash_invocation(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    if not source.startswith("/"):
        return {
            "matched": False,
            "command": "",
            "arguments": "",
            "public_text": source,
        }
    parts = source[1:].split(None, 1)
    command = parts[0] if parts else ""
    if not command:
        return {
            "matched": False,
            "command": "",
            "arguments": "",
            "public_text": source,
        }
    return {
        "matched": True,
        "command": command,
        "arguments": parts[1].strip() if len(parts) > 1 else "",
        "public_text": source,
    }


def command_system_prompt(command: object) -> str:
    """Return the run-scoped instruction for one built-in command."""

    return _COMMAND_PROMPTS.get(str(command or "").strip(), "")


__all__ = [
    "BUILTIN_COMMAND_IDS",
    "DEEP_REFLECT_COMMAND_ID",
    "command_system_prompt",
    "parse_slash_command",
    "parse_slash_invocation",
]
