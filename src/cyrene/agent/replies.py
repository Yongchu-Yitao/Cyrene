"""Final reply synthesis and user-visible fallback helpers."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from cyrene.agent.message import _is_placeholder_reply
from cyrene.agent.model_service import (
    call_agent_model as _call_llm,
    set_final_reply_usage,
    stream_agent_model as _call_llm_stream,
    streaming_reply_requested as _streaming_reply_requested,
)
from cyrene.model_runtime.messages import assistant_text

_default_call_llm = _call_llm
_default_call_llm_stream = _call_llm_stream
_default_streaming_reply_requested = _streaming_reply_requested

_VISIBLE_DSML_TOOL_BLOCK_RE = re.compile(
    r"(?:"
    r"<(?:｜｜|\|\|)DSML(?:｜｜|\|\|)tool_calls>.*?</(?:｜｜|\|\|)DSML(?:｜｜|\|\|)tool_calls>"
    r"|<tool_call>.*?</tool_call>"
    r")",
    re.DOTALL | re.IGNORECASE,
)
_VISIBLE_DSML_TOOL_MARKUP_RE = re.compile(
    r"(?:"
    r"</?(?:｜｜|\|\|)DSML(?:｜｜|\|\|)"
    r"|</?tool_call\b"
    r"|<function="
    r"|<parameter="
    r")",
    re.IGNORECASE,
)


def _legacy_guidance_dependency(
    name: str,
    original_value: Any,
    current_value: Any,
) -> Any:
    """Honor old guidance monkeypatches without importing it or adding a cycle."""
    guidance = sys.modules.get("cyrene.agent.guidance")
    legacy_value = getattr(guidance, name, original_value)
    return (
        legacy_value
        if legacy_value is not original_value
        else current_value
    )


async def _final_user_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    last_user_text = next(
        (
            str(message.get("content") or "").strip()
            for message in reversed(messages)
            if isinstance(message, dict)
            and str(message.get("role") or "") == "user"
            and str(message.get("content") or "").strip()
        ),
        "",
    )
    prompt_messages = [
        *messages,
        {
            "role": "user",
            "content": (
                (
                    "Now answer the user's request directly using the gathered tool results.\n"
                    if last_user_text
                    else "The user uploaded one or more attachments without extra text. "
                    "Summarize the attachment contents directly using the gathered tool results.\n"
                )
                + "Do not call tools.\n"
                + "Do not reply with only 'Done'.\n"
                + "If the tools extracted file or attachment contents, quote or "
                "summarize those contents in your answer."
            ),
        },
    ]
    return await _validated_final_no_tool_reply(
        prompt_messages,
        max_tokens=max_tokens,
    )


async def _final_plain_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    prompt_messages = [
        *messages,
        {
            "role": "user",
            "content": (
                "Answer the latest user message directly.\n"
                "Do not call tools.\n"
                "Do not reply with only 'Done'."
            ),
        },
    ]
    return await _validated_final_no_tool_reply(
        prompt_messages,
        max_tokens=max_tokens,
    )


def _tool_result_fallback_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if (
            not isinstance(message, dict)
            or str(message.get("role") or "") != "tool"
        ):
            continue
        raw = str(message.get("content") or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            text_preview = str(payload.get("text_preview") or "").strip()
            if text_preview:
                return f"我从附件中提取到的内容是：\n\n{text_preview}"
            stdout = str(payload.get("stdout") or "").strip()
            if stdout:
                return f"我从附件中提取到的内容是：\n\n{stdout[:4000]}"
            preview = str(payload.get("preview") or "").strip()
            if preview and "no built-in parser" not in preview.lower():
                return f"我从附件中提取到的内容是：\n\n{preview}"
        elif raw and not raw.lower().startswith("tool failed:"):
            return f"我从附件中提取到的内容是：\n\n{raw[:4000]}"
    return ""


def _looks_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))


def _delivery_fallback_text(messages: list[dict]) -> str:
    """Build a minimal user-facing reply from successful delivery results."""
    names: list[str] = []
    delivery_call_ids: set[str] = set()
    saw_delivery = False
    chinese = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if (
            str(message.get("role") or "") == "user"
            and _looks_chinese(str(message.get("content") or ""))
        ):
            chinese = True
        if str(message.get("role") or "") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                tool_name = str(
                    tool_call.get("function", {}).get("name") or ""
                ).strip()
                if tool_name in {"send_file", "send_wechat_file"}:
                    call_id = str(tool_call.get("id") or "").strip()
                    if call_id:
                        delivery_call_ids.add(call_id)
    for message in messages:
        if (
            not isinstance(message, dict)
            or str(message.get("role") or "") != "tool"
            or str(message.get("tool_call_id") or "").strip()
            not in delivery_call_ids
        ):
            continue
        raw = str(message.get("content") or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
        if (
            isinstance(payload, dict)
            and str(payload.get("status") or "") == "sent"
        ):
            attachment = payload.get("attachment")
            name = ""
            if isinstance(attachment, dict):
                name = str(
                    attachment.get("name")
                    or attachment.get("filename")
                    or ""
                ).strip()
            if name and name not in names:
                names.append(name)
            saw_delivery = True
            continue
        match = re.search(r"File sent via WeChat:\s*(.+)$", raw)
        if match:
            name = match.group(1).strip()
            if name and name not in names:
                names.append(name)
            saw_delivery = True
    if not saw_delivery:
        return ""
    if chinese:
        return (
            "文件已发给你：" + "、".join(names) + "。"
            if names
            else "文件已发给你。"
        )
    if names:
        joined = ", ".join(names)
        return f"I sent the file{'s' if len(names) != 1 else ''}: {joined}."
    return "I sent the file."


async def _final_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    return (
        await _validated_final_no_tool_reply(
            messages,
            max_tokens=max_tokens,
        )
    ) or "Done."


async def _final_reply_with_tools(
    messages: list[dict],
    tools: list,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Stream a wrap-up reply while keeping the tool channel available."""
    prompt_messages = [
        *messages,
        {
            "role": "user",
            "content": (
                "Write the final user-facing reply now, in the user's language. "
                "Do not reply with only 'Done', 'OK', or another placeholder. "
                "If files were delivered with send_file or send_wechat_file, "
                "briefly confirm the delivery and mention the file name or what "
                "was sent. Call another tool only if it is genuinely needed to "
                "satisfy the user's request."
            ),
        },
    ]
    stream_call = _legacy_guidance_dependency(
        "_call_llm_stream",
        _default_call_llm_stream,
        _call_llm_stream,
    )
    response = await stream_call(
        prompt_messages,
        max_tokens=max_tokens,
        tools=tools,
    )
    _record_final_reply_usage(response)
    return response


def _strip_visible_dsml_tool_blocks(text: str) -> str:
    return _VISIBLE_DSML_TOOL_BLOCK_RE.sub("", str(text or "")).strip()


def _contains_visible_dsml_tool_markup(text: str) -> bool:
    """Return whether text contains complete or partial textual tool syntax."""
    return bool(_VISIBLE_DSML_TOOL_MARKUP_RE.search(str(text or "")))


def _record_final_reply_usage(*responses: Any) -> None:
    merged: dict[str, Any] = {}
    for response in responses:
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if (
                isinstance(value, (int, float))
                and isinstance(merged.get(key), (int, float))
            ):
                merged[key] = merged[key] + value
            else:
                merged.setdefault(key, value)
    set_final_reply_usage(merged or None)


async def _validated_final_no_tool_reply(
    messages: list[dict],
    max_tokens: int | None = None,
    *,
    call_llm: Any = None,
    call_llm_stream: Any = None,
    streaming_reply_requested: Any = None,
) -> str:
    """Generate final text without leaking textual tool-call markup."""
    call_llm = call_llm or _legacy_guidance_dependency(
        "_call_llm",
        _default_call_llm,
        _call_llm,
    )
    call_llm_stream = call_llm_stream or _legacy_guidance_dependency(
        "_call_llm_stream",
        _default_call_llm_stream,
        _call_llm_stream,
    )
    streaming_reply_requested = (
        streaming_reply_requested
        or _legacy_guidance_dependency(
            "_streaming_reply_requested",
            _default_streaming_reply_requested,
            _streaming_reply_requested,
        )
    )
    if streaming_reply_requested():
        response = await call_llm_stream(messages, max_tokens=max_tokens)
    else:
        response = await call_llm(
            messages,
            tools=None,
            max_tokens=max_tokens,
        )
    _record_final_reply_usage(response)
    text = assistant_text(response).strip()
    if not _contains_visible_dsml_tool_markup(text):
        return text

    retry_messages = [
        *messages,
        {"role": "assistant", "content": text},
        {
            "role": "user",
            "content": (
                "Your previous message was textual tool-call markup, but tools are "
                "not available in this final-answer step. Write the final answer "
                "to the user in plain text only, using the already gathered "
                "context. Do not output XML, DSML, JSON tool calls, or any "
                "tool-call markup."
            ),
        },
    ]
    retry_response = await call_llm(
        retry_messages,
        tools=None,
        max_tokens=max_tokens,
    )
    _record_final_reply_usage(response, retry_response)
    retry_text = assistant_text(retry_response).strip()
    if _contains_visible_dsml_tool_markup(retry_text):
        retry_text = _strip_visible_dsml_tool_blocks(retry_text)
        if _contains_visible_dsml_tool_markup(retry_text):
            return ""
    return retry_text
