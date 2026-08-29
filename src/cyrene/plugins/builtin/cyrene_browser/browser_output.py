"""Shared formatting for browser observations returned to the agent."""

from __future__ import annotations

from typing import Any

from cyrene.core.plugin import PluginContext
from cyrene.plugins.native_runtime import plugin_localized


def _localized(
    context: PluginContext | None,
    en: str,
    zh: str,
    **values: Any,
) -> str:
    if context is not None:
        return plugin_localized(context, en, zh, **values)
    from cyrene.localization import localized

    return localized(en, zh, **values)


def browser_error_text(
    result: dict[str, Any],
    context: PluginContext | None,
    fallback_en: str,
    fallback_zh: str,
) -> str:
    """Render a safe browser error in the invocation language.

    Browser transports may supply diagnostic text in ``error``.  The Plugin
    boundary deliberately trusts only stable codes and otherwise uses the
    operation-specific fallback, so native exception details cannot reach the
    model and an invocation cannot inherit a different app-language message.
    """

    code = str(result.get("code") or "").strip()
    if code:
        from .runtime import _BROWSER_ERROR_MESSAGES

        messages = _BROWSER_ERROR_MESSAGES.get(code)
        if messages is not None:
            return _localized(context, *messages)
    return _localized(context, fallback_en, fallback_zh)


def page_signal_lines(
    result: dict[str, Any], context: PluginContext | None = None
) -> list[str]:
    signal = result.get("page_signal")
    if not isinstance(signal, dict):
        return []
    kind = str(signal.get("kind") or "").strip()
    if kind == "access_gate":
        try:
            cooldown_ms = int(
                signal.get("cooldown_ms") or signal.get("cooldownMs") or 10000
            )
        except (TypeError, ValueError):
            cooldown_ms = 10000
        return [
            "PAGE_SIGNAL: access_gate",
            _localized(
                context,
                "RECOVERY_ALLOWED: wait at least {seconds}s, then make at most one recovery attempt.",
                "RECOVERY_ALLOWED：至少等待 {seconds} 秒，然后最多进行一次恢复尝试。",
                seconds=cooldown_ms // 1000,
            ),
            _localized(
                context,
                "IF_STILL_BLOCKED: call browser_request_takeover; do not continue retrying.",
                "IF_STILL_BLOCKED：调用 browser_request_takeover；不要继续重试。",
            ),
            _localized(
                context,
                "Reason: {reason}",
                "原因：{reason}",
                reason=_localized(
                    context,
                    "Page content is temporarily unavailable and may require user takeover.",
                    "页面内容暂时不可用，可能需要用户接管。",
                ),
            ),
        ]
    if kind and kind != "normal":
        return [f"PAGE_SIGNAL: {kind}"]
    return []


def page_observation_lines(
    result: dict[str, Any], context: PluginContext | None = None
) -> list[str]:
    lines = page_signal_lines(result, context)
    if str((result.get("page_signal") or {}).get("kind") or "") == "access_gate":
        preview = str(result.get("text") or "").strip()
        if preview:
            lines.append(
                _localized(
                    context,
                    "Page text preview: {preview}",
                    "页面文本预览：{preview}",
                    preview=preview[:1200],
                )
            )
    return lines


def file_chooser_instruction(
    result: dict[str, Any], context: PluginContext | None = None
) -> str:
    """Return an agent-actionable message for a securely intercepted chooser."""
    if str(result.get("code") or "") != "FILE_CHOOSER_INTERCEPTED":
        return ""
    chooser_id = str(result.get("chooserId") or "").strip()
    target = result.get("uploadTarget") if isinstance(result.get("uploadTarget"), dict) else {}
    origin = str(target.get("origin") or target.get("frameUrl") or result.get("url") or "")
    accept = str(target.get("accept") or "") or _localized(
        context, "(not declared)", "（未声明）"
    )
    multiple = bool(target.get("multiple"))
    return _localized(
        context,
        "FILE_CHOOSER_INTERCEPTED: the native system picker was suppressed.\n"
        "chooser_id: {chooser_id}\n"
        "receiving_origin: {origin}\n"
        "accept: {accept}\n"
        "multiple: {multiple}\n"
        "Next action: call browser_upload_files with this chooser_id and the exact local file paths. "
        "That tool will pause for a human, single-use external-upload approval.",
        "FILE_CHOOSER_INTERCEPTED：已阻止原生系统文件选择器。\n"
        "chooser_id：{chooser_id}\n"
        "receiving_origin：{origin}\n"
        "accept：{accept}\n"
        "multiple：{multiple}\n"
        "下一步：使用此 chooser_id 和准确的本地文件路径调用 browser_upload_files。"
        "该工具会暂停并请求人工进行一次性的外部上传批准。",
        chooser_id=chooser_id,
        origin=origin,
        accept=accept,
        multiple=multiple,
    )


def page_link_lines(
    result: dict[str, Any], context: PluginContext | None = None
) -> list[str]:
    """Format readable anchors returned by browser navigation."""
    links = result.get("links")
    if not isinstance(links, list):
        return []
    rows: list[str] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        text = " ".join(str(link.get("text") or "").split()).strip()
        url = str(link.get("url") or link.get("href") or "").strip()
        ref = str(link.get("ref") or "").strip()
        if text and url:
            prefix = f"[{ref}] " if ref else ""
            rows.append(f"- {prefix}{text!r} -> {url}")
    if not rows:
        return []
    return [
        _localized(
            context,
            "Text links on this page:\n{rows}",
            "此页面上的文本链接：\n{rows}",
            rows="\n".join(rows),
        )
    ]


__all__ = [
    "browser_error_text",
    "page_signal_lines",
    "page_observation_lines",
    "page_link_lines",
    "file_chooser_instruction",
]
