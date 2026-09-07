"""Explicit input selection for new sends and history-based retries.

These helpers preserve command resolution and mutation order. The input record
is shallowly immutable; attachment and descriptor identity is intentionally kept.
"""
from dataclasses import dataclass, field
from typing import Any
from cyrene.workbench.http.errors import localized_error_response


@dataclass(frozen=True, slots=True)
class SendInput:
    message: str
    public_message: str
    command: str
    normalized: list[Any]
    public_attachments: list[Any]
    dynamic_command: dict[str, Any] | None = None
    dynamic_command_prompt: str = ""


@dataclass(frozen=True, slots=True)
class PreparedUserTurn:
    input: SendInput
    user_entry: dict[str, Any]
    now: str
    should_generate_title: bool = False
    truncate_after_id: str = ""
    retry_replaced_message_ids: set[str] = field(default_factory=set)


async def resolve_send_command(source: SendInput, chat, is_external_agent, language):
    message, public_message, command = source.message, source.public_message, source.command
    from cyrene.workbench.application.commands import parse_slash_command

    dynamic_command = None
    dynamic_command_prompt = ""

    if is_external_agent:
        declared_commands = [
            str(item.get("id") or item.get("name") or item.get("command") or "")
            if isinstance(item, dict) else str(item or "")
            for item in (chat.get("agentCommands") or [])
        ]
        if not command and declared_commands:
            parsed = parse_slash_command(
                message,
                allowed_commands=declared_commands,
            )
            if parsed.get("matched"):
                command = str(parsed.get("command") or "")
                message = str(parsed.get("arguments") or "")
        if command and declared_commands and command not in declared_commands:
            return None, localized_error_response(
                "This Agent command is not available.",
                "此 Agent 命令不可用。",
                400,
                "agent_command_unavailable",
                language=language,
            )
    else:
        message, command, dynamic_command, dynamic_command_prompt, error = await _builtin_command(message, command, chat, language)
        if error is not None:
            return None, error
    if command and not public_message:
        public_message = "/" + command

    return SendInput(message, public_message, command, source.normalized,
                     source.public_attachments, dynamic_command, dynamic_command_prompt), None


async def retry_send_input(source: SendInput, user_entry, chat, is_external_agent, normalize_attachments):
    dynamic_command = source.dynamic_command
    dynamic_command_prompt = source.dynamic_command_prompt
    message = str(user_entry.get("content") or "").strip()
    public_message = message
    command = str(user_entry.get("command") or "").strip()
    from cyrene.workbench.application.commands import parse_slash_invocation

    parsed_retry_command = parse_slash_invocation(message)
    if parsed_retry_command.get("matched") and (
        not command
        or command == str(parsed_retry_command.get("command") or "")
    ):
        command = str(parsed_retry_command.get("command") or "")
        message = str(parsed_retry_command.get("arguments") or "")
    if not is_external_agent and command:
        from cyrene.workbench.chat.slash_commands import resolve_slash_command

        descriptor = await resolve_slash_command(
            command,
            str(chat.get("projectId") or ""),
        )
        if descriptor is None:
            command = ""
        elif descriptor.get("source") != "builtin":
            dynamic_command = descriptor
            dynamic_command_prompt = str(
                descriptor.get("system_prompt") or ""
            ).strip()
    normalized = normalize_attachments(user_entry.get("agentAttachments") or [])
    public_attachments = user_entry.get("attachments") if isinstance(user_entry.get("attachments"), list) else []
    return SendInput(message, public_message, command, normalized, public_attachments,
                     dynamic_command, dynamic_command_prompt)


def append_user_message(chat, messages, source: SendInput, origin, now, short_id):
    should_generate_title = False
    user_entry = {
        "id": short_id("msg"),
        "role": "user",
        "content": source.public_message,
        "createdAt": now,
    }
    if source.command:
        user_entry["command"] = source.command
    if origin.client_request_id:
        user_entry["clientRequestId"] = origin.client_request_id
    if origin.agent_originated:
        user_entry["agentOriginated"] = True
    if origin.origin_session_id:
        user_entry["originSessionId"] = origin.origin_session_id
    if source.public_attachments:
        user_entry["attachments"] = source.public_attachments
        user_entry["agentAttachments"] = source.normalized
    is_first_message = not any(item.get("role") == "user" for item in messages)
    messages.append(user_entry)
    if is_first_message:
        locked_agent = dict(chat.get("agent") or {})
        locked_agent["bindingLocked"] = True
        chat["agent"] = locked_agent
    if is_first_message and chat.get("title") in ("", "New chat", "新对话", None) and source.public_message:
        chat["title"] = source.public_message.replace("\n", " ")[:24]
    if is_first_message and bool(source.public_message) and not bool(chat.get("titleLocked")) and not chat.get("titleNamingStatus"):
        should_generate_title = True
        chat["titleNamingStatus"] = "pending"
        chat["titleNamingStartedAt"] = now

    return PreparedUserTurn(source, user_entry, now, should_generate_title)


async def _builtin_command(message, command, chat, language):
    from cyrene.workbench.application.commands import parse_slash_invocation
    dynamic_command = None
    dynamic_command_prompt = ""
    from cyrene.workbench.chat.slash_commands import resolve_slash_command

    parsed = parse_slash_invocation(message) if not command else None
    candidate = command or str((parsed or {}).get("command") or "")
    descriptor = await resolve_slash_command(
        candidate,
        str(chat.get("projectId") or ""),
    ) if candidate else None
    if descriptor is not None:
        command = str(descriptor.get("id") or "")
        dynamic_command = (
            descriptor if descriptor.get("source") != "builtin" else None
        )
        if parsed and parsed.get("matched"):
            message = str(parsed.get("arguments") or "")
        if dynamic_command:
            dynamic_command_prompt = str(
                dynamic_command.get("system_prompt") or ""
            ).strip()
    elif command:
        return message, command, dynamic_command, dynamic_command_prompt, localized_error_response(
            "Unknown Cyrene command.",
            "未知的 Cyrene 命令。",
            400,
            "unknown_command",
            language=language,
        )
    return message, command, dynamic_command, dynamic_command_prompt, None
