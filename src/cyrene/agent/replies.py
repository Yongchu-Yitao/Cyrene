"""Final reply synthesis and user-visible fallback helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from cyrene.agent.message import _is_placeholder_reply
from cyrene.agent.model_service import (
    call_agent_model as _call_llm,
    set_final_reply_usage,
    stream_agent_model as _call_llm_stream,
    streaming_reply_requested as _streaming_reply_requested,
)
from cyrene.model_runtime.messages import assistant_text

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


async def _final_user_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    return await _recover_final_reply(messages, max_tokens=max_tokens)


async def _final_plain_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    return await _recover_final_reply(messages, max_tokens=max_tokens)


def _tool_result_fallback_text(messages: list[dict]) -> str:
    chinese = any(
        isinstance(message, dict)
        and str(message.get("role") or "") == "user"
        and _looks_chinese(str(message.get("content") or ""))
        for message in messages
    )
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
                safe_text = _safe_fallback_detail(text_preview, limit=4000)
                if safe_text:
                    return (
                        f"工具返回的内容是：\n\n{safe_text}"
                        if chinese
                        else f"The tool returned:\n\n{safe_text}"
                    )
            stdout = str(payload.get("stdout") or "").strip()
            if stdout:
                safe_text = _safe_fallback_detail(stdout, limit=4000)
                if safe_text:
                    return (
                        f"工具返回的内容是：\n\n{safe_text}"
                        if chinese
                        else f"The tool returned:\n\n{safe_text}"
                    )
            preview = str(payload.get("preview") or "").strip()
            if preview and "no built-in parser" not in preview.lower():
                safe_text = _safe_fallback_detail(preview, limit=4000)
                if safe_text:
                    return (
                        f"工具返回的内容是：\n\n{safe_text}"
                        if chinese
                        else f"The tool returned:\n\n{safe_text}"
                    )
        elif raw and not raw.lower().startswith("tool failed:"):
            safe_text = _safe_fallback_detail(raw, limit=4000)
            if safe_text:
                return (
                    f"工具返回的内容是：\n\n{safe_text}"
                    if chinese
                    else f"The tool returned:\n\n{safe_text}"
                )
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


def _safe_fallback_detail(value: Any, *, limit: int = 1000) -> str:
    detail = re.sub(r"\s+", " ", str(value or "")).strip()
    if not detail:
        return ""
    detail = _strip_visible_dsml_tool_blocks(detail)
    if _contains_visible_dsml_tool_markup(detail):
        return ""
    return detail[:limit]


def _tool_failure_fallback_text(messages: list[dict]) -> str:
    """Surface the latest explicit tool failure without exposing diagnostics."""
    chinese = any(
        isinstance(message, dict)
        and str(message.get("role") or "") == "user"
        and _looks_chinese(str(message.get("content") or ""))
        for message in messages
    )
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
        failed = raw.lower().startswith("tool failed:")
        detail = ""
        if isinstance(payload, dict):
            status = str(payload.get("status") or "").strip().lower()
            failed = failed or status in {
                "error", "failed", "failure", "denied", "cancelled", "canceled",
            } or payload.get("success") is False
            if failed:
                for key in ("error", "message", "detail"):
                    detail = _safe_fallback_detail(payload.get(key))
                    if detail:
                        break
        elif failed:
            detail = _safe_fallback_detail(raw.partition(":")[2])
        if not failed:
            continue
        if chinese:
            return f"工具执行失败：{detail}" if detail else "工具执行失败，未能完成请求。"
        return (
            f"A tool failed: {detail}"
            if detail
            else "A tool failed, so the request could not be completed."
        )
    return ""


def _deterministic_final_fallback(messages: list[dict]) -> str:
    """Return a safe terminal reply without another model request."""
    for fallback_factory in (
        _tool_failure_fallback_text,
        _delivery_fallback_text,
        _tool_result_fallback_text,
    ):
        fallback = fallback_factory(messages)
        if fallback:
            return fallback
    chinese = any(
        isinstance(message, dict)
        and str(message.get("role") or "") == "user"
        and _looks_chinese(str(message.get("content") or ""))
        for message in messages
    )
    has_tool_result = any(
        isinstance(message, dict) and str(message.get("role") or "") == "tool"
        for message in messages
    )
    if chinese:
        return (
            "工具执行已结束，但未能生成安全的最终答复。"
            if has_tool_result
            else "抱歉，我未能生成安全的答复。"
        )
    return (
        "The tools finished, but I could not generate a safe final response."
        if has_tool_result
        else "Sorry, I could not generate a safe response."
    )


async def _recover_final_reply(
    messages: list[dict],
    max_tokens: int | None = None,
    *,
    completion_packet: dict[str, Any] | None = None,
    call_llm: Any = None,
    call_llm_stream: Any = None,
    streaming_reply_requested: Any = None,
) -> str:
    """Run one synthesis; only textual tool markup gets one correction call.

    Empty-body Execution ``quit`` calls append a coordinator-built completion
    packet.  The existing Execution transcript stays byte-stable, while the
    packet explicitly states that no prior public answer exists and supplies
    the structured completion facts needed for a self-contained reply.
    """
    if completion_packet is not None:
        return await _validated_final_no_tool_reply(
            [
                *messages,
                {
                    "role": "user",
                    "content": json.dumps(
                        completion_packet,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            max_tokens=max_tokens,
            call_llm=call_llm,
            call_llm_stream=call_llm_stream,
            streaming_reply_requested=streaming_reply_requested,
        )
    has_tool_results = any(
        isinstance(message, dict)
        and (
            str(message.get("role") or "") == "tool"
            or (
                str(message.get("role") or "") == "assistant"
                and bool(message.get("tool_calls"))
            )
        )
        for message in messages
    )
    instruction = (
        "Now answer the user's request directly using the gathered tool results.\n"
        "Do not call tools.\n"
        "Do not reply with only 'Done'.\n"
        "If a tool was unavailable or failed, state that explicitly and do not "
        "promise a future retry.\n"
        "If tools extracted file or attachment contents, quote or summarize them."
        if has_tool_results
        else
        "Answer the latest user message directly.\n"
        "Do not call tools.\n"
        "Do not reply with only 'Done'."
    )
    return await _validated_final_no_tool_reply(
        [*messages, {"role": "user", "content": instruction}],
        max_tokens=max_tokens,
        call_llm=call_llm,
        call_llm_stream=call_llm_stream,
        streaming_reply_requested=streaming_reply_requested,
    )


async def _final_reply_from_history(
    messages: list[dict],
    max_tokens: int | None = None,
) -> str:
    return await _recover_final_reply(messages, max_tokens=max_tokens)


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
    response = await _call_llm_stream(
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
    """Generate safe text with one bounded provider-markup correction."""
    call_llm = _call_llm if call_llm is None else call_llm
    call_llm_stream = (
        _call_llm_stream if call_llm_stream is None else call_llm_stream
    )
    streaming_reply_requested = (
        _streaming_reply_requested
        if streaming_reply_requested is None
        else streaming_reply_requested
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
    if (
        text
        and not _is_placeholder_reply(text)
        and not _contains_visible_dsml_tool_markup(text)
    ):
        return text
    if _contains_visible_dsml_tool_markup(text):
        correction_messages = [
            *messages,
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": (
                    "Your previous message was textual tool-call markup, but tools "
                    "are unavailable in this final-answer step. Write the final "
                    "answer in plain text only from the gathered context. Do not "
                    "output XML, DSML, JSON tool calls, or tool-call markup."
                ),
            },
        ]
        corrected_response = await call_llm(
            correction_messages,
            tools=None,
            max_tokens=max_tokens,
        )
        _record_final_reply_usage(response, corrected_response)
        corrected = assistant_text(corrected_response).strip()
        if (
            corrected
            and not _is_placeholder_reply(corrected)
            and not _contains_visible_dsml_tool_markup(corrected)
        ):
            return corrected
    return _deterministic_final_fallback(messages)
