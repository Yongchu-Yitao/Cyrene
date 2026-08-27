"""Pure learned-skill replay policy and template resolution."""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess

from typing import Any, Iterable, Mapping


def enabled_step_tool_names(steps: Iterable[Mapping[str, Any]]) -> list[str]:
    tool_names: list[str] = []
    for step in steps:
        if not bool(step.get("enabled", True)):
            continue
        reference = step.get("implementation_reference") or {}
        if str(step.get("implementation_kind") or "") == "script":
            tool_names.extend(enabled_step_tool_names(reference.get("original_steps") or []))
            continue
        tool_names.append(str(reference.get("tool_name") or ""))
    return tool_names


def tool_call_steps(steps: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    replay_steps: list[dict[str, Any]] = []
    for step in steps:
        if not bool(step.get("enabled", True)):
            continue
        reference = step.get("implementation_reference") or {}
        if str(step.get("implementation_kind") or "") == "script":
            replay_steps.extend(tool_call_steps(reference.get("original_steps") or []))
            continue
        replay_steps.append(step if isinstance(step, dict) else dict(step))
    return replay_steps


def has_blocked_step(
    steps: Iterable[Mapping[str, Any]],
    blocked_tools: frozenset[str],
) -> bool:
    return any(
        tool in blocked_tools or tool.startswith("browser.user.")
        for tool in enabled_step_tool_names(steps)
    )


def has_skillworthy_steps(
    steps: Iterable[Mapping[str, Any]],
    *,
    trivial_tools: frozenset[str],
    internal_tools: frozenset[str],
    minimum_steps: int,
) -> bool:
    tool_names = [
        tool
        for tool in enabled_step_tool_names(steps)
        if tool and tool not in trivial_tools and tool not in internal_tools
    ]
    if len(tool_names) < minimum_steps:
        return False
    if len(set(tool_names)) >= minimum_steps:
        return True
    return all(tool.startswith("browser.user.") for tool in tool_names)


def resolve_value_template(value: Any, params: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        resolved = value
        for key, param in params.items():
            resolved = resolved.replace(f"{{{{{key}}}}}", str(param))
        return resolved
    if isinstance(value, list):
        return [resolve_value_template(item, params) for item in value]
    if isinstance(value, dict):
        return {key: resolve_value_template(item, params) for key, item in value.items()}
    return value

INTERNAL_TOOLS: frozenset[str] = frozenset({
    "spawn_subagent",
    "send_agent_message",
    "broadcast_agent_message",
})

# Tools that should not trigger skill creation when used alone — they're interactive
# or informational, not "production" tool calls that form a reusable workflow.
TRIVIAL_SKILL_TOOLS: frozenset[str] = frozenset({
    "ask_user",
    "send_message",
    "send_message_to_user",
    "query_round",
    "browser.user.control_start",
    "browser.user.control_stop",
    "browser.user.key",
    "browser.user.mousemove",
    "browser.user.mouseMove",
    "GetLearnedSkill",
    "RunLearnedSkill",
})

INTERNAL_LEARNING_MESSAGE_PREFIXES = (
    "[Internal permission decision received.",
    "This is a scheduler-initiated proactive check-in.",
    "你正在持续执行模式中完成一个有界工作片段。",
    "You are completing one bounded work packet",
)

MIN_SKILL_CHAIN_STEPS = 2

# These steps change the conversation state or pause for the user. They are
# useful in the normal agent loop, but learned-skill replay should never run
# them ahead of the router's ordinary clarification/permission flow.
AUTO_REPLAY_BLOCKED_TOOLS: frozenset[str] = frozenset({
    "ask_user",
    "browser.user.control_start",
    "browser.user.control_stop",
    "browser.user.click",
    "browser.user.scroll",
    "browser.user.key",
    "browser.user.text",
})

# Delivery-only calls are useful while the original agent run is executing,
# but they are not part of the reusable workflow itself.  Older learned skills
# may still contain them, so replay skips them instead of rejecting the whole
# otherwise-safe skill or sending a duplicate progress update.
REPLAY_IGNORED_TOOLS: frozenset[str] = frozenset({
    "send_message",
    "send_message_to_user",
})

# Tools that carry meaningful side-effects and must never be replayed silently.
# A learned skill whose steps include any of these requires fresh user approval;
# the skill router falls back to the normal agent loop instead of auto-executing.
HIGH_RISK_TOOLS: frozenset[str] = frozenset({
    # Arbitrary shell / command execution
    "Bash", "run_shell", "run_command", "StartShell", "SendShell", "start_shell", "send_shell",
    # File write operations (outside-workspace risk)
    "Write", "write_file", "Edit", "edit_file",
    # Persistent scheduled task creation or mutation
    "schedule.create", "schedule.edit",
    # Entering data or uploading a file can disclose information or submit a
    # state-changing form.  Navigation, observation, and ordinary clicks remain
    # replayable because the user explicitly invoked the learned workflow.
    "browser_type", "browser_type_ref", "browser_upload_files",
})

BROWSER_SKILL_EVENT_KINDS = frozenset({
    "click", "input", "text", "submit", "navigate", "navigation",
    "select", "select_tab", "close_tab", "back", "forward", "reload", "download",
})

_MAX_GENERATED_SCRIPT_CHARS = 48_000

def is_reusable_skill_definition(definition: dict[str, Any] | None) -> bool:
    """Return whether a stored skill is eligible for learning or execution.

    This is intentionally checked at every read boundary as well as during
    creation. Older databases can contain skills created before the
    multi-operation guard was introduced, and a generated-script wrapper may
    make such a skill look like a one-step skill in the UI.
    """
    if not isinstance(definition, dict):
        return False
    return has_skillworthy_steps(
        definition.get("steps") or [],
        trivial_tools=TRIVIAL_SKILL_TOOLS,
        internal_tools=INTERNAL_TOOLS,
        minimum_steps=MIN_SKILL_CHAIN_STEPS,
    )

def is_complex_continuous_workflow(steps: list[dict[str, Any]]) -> bool:
    call_count = 0
    has_repeated_call = False
    for step in steps:
        if not bool(step.get("enabled", True)) or str(step.get("implementation_kind") or "") != "tool_call":
            continue
        args_template = (step.get("implementation_reference") or {}).get("args_template") or {}
        items = args_template.get("_items")
        if isinstance(items, list) and items:
            call_count += len(items)
            has_repeated_call = has_repeated_call or len(items) > 1
        else:
            call_count += 1
    return call_count >= 3 or has_repeated_call

def workflow_can_be_scripted(steps: list[dict[str, Any]]) -> bool:
    tools = enabled_step_tool_names(steps)
    return bool(tools) and not any(tool.startswith("browser.user.") for tool in tools)

def normalize_generated_script_source(language: str, value: Any) -> str:
    source = str(value or "").replace("\r\n", "\n").strip()
    fenced = re.fullmatch(r"```(?:python|py|bash|sh|shell)?\s*\n([\s\S]*?)\n```", source, re.IGNORECASE)
    if fenced:
        source = fenced.group(1).strip()
    if not source or "\x00" in source or len(source) > _MAX_GENERATED_SCRIPT_CHARS:
        return ""
    if language == "python":
        try:
            ast.parse(source)
        except SyntaxError:
            return ""
        if not source.startswith("#!"):
            source = "#!/usr/bin/env python3\n" + source
    elif language == "shell":
        try:
            checked = subprocess.run(
                ["/bin/sh", "-n"],
                input=source,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return ""
        if checked.returncode != 0:
            return ""
        if not source.startswith("#!"):
            source = "#!/bin/sh\nset -eu\n" + source
    else:
        return ""
    return source.rstrip() + "\n"

def normalize_script_implementation(value: Any, *, allow_script: bool) -> dict[str, Any]:
    if not allow_script or not isinstance(value, dict):
        return {"kind": "tool_chain"}
    raw_kind = str(value.get("kind") or value.get("language") or "").strip().lower()
    language = "python" if raw_kind in {"python", "py", "python_script"} else "shell" if raw_kind in {"shell", "sh", "bash", "shell_script"} else ""
    source = normalize_generated_script_source(language, value.get("source"))
    if not language or not source:
        return {"kind": "tool_chain"}
    return {
        "kind": f"{language}_script",
        "language": language,
        "filename": "run.py" if language == "python" else "run.sh",
        "source": source,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }
