"""Content-free diagnostic evidence, independent of user settings."""
from __future__ import annotations

import re
from typing import Any

_SECRET = re.compile(r"(?:bearer\s+\S+|sk-[\w.-]+|(?:api[_-]?key|password|token|secret|authorization)\s*[:=]\s*[^\s,;]+)", re.I)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if re.search(r"secret|password|token|authorization|cookie|api.?key|prompt|content|messages", str(k), re.I)
                else redact(v) for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value[:100]]
    if isinstance(value, str):
        return _SECRET.sub("[REDACTED]", value[:4000])
    return value if isinstance(value, (int, float, bool)) or value is None else type(value).__name__


def error_code(exc: BaseException) -> str:
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        exporter = getattr(current, "as_error_details", None)
        try:
            details = exporter() if callable(exporter) else {}
            code = details.get("code") or getattr(current, "code", "")
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", code):
                return code
        except Exception:
            pass
        current = current.__cause__ or current.__context__
    return "internal_error"


def direction(code: str) -> dict[str, str]:
    if code in {"model_response_invalid", "model_response_incomplete", "model_output_truncated"}:
        hints = {
            "model_response_invalid": ("模型回复或工具参数不符合协议；检查流式终止原因及工具参数校验结果。先确认工具是否已执行，再调整提示或工具 schema 后重试。", "Inspect protocol termination and tool argument validation. Check prior tool execution before changing the prompt or schema and retrying."),
            "model_response_incomplete": ("上游未完整结束回复；检查终止事件与服务状态。保留已产生的内容，确认工具执行情况后再继续。", "The upstream response did not complete. Inspect terminal events and service state; retain partial output and check tool execution before continuing."),
            "model_output_truncated": ("回复达到输出限制，工具参数可能未生成完整；减少单次输出或调整输出额度，确认工具状态后重试。", "Output reached its limit and tool arguments may be incomplete. Reduce output or adjust the allowance, and check tool state before retrying."),
        }
        zh, en = hints[code]
        return {"zh": zh, "en": en}
    if code.startswith("http_") or code in {"frontend_error", "unhandled_rejection", "network_error", "ui_error", "incident_missing"}:
        hints = {
            "http_401": ("应用登录已失效，请重新登录。", "Application authentication expired; sign in again."),
            "http_403": ("当前操作被拒绝，请检查应用访问权限。", "Check application access permissions."),
            "http_404": ("目标可能已删除或前后端版本不同，请刷新并检查目标是否存在。", "Refresh and check whether the target exists and versions match."),
            "http_409": ("状态发生冲突，请重新加载最新状态后再操作。", "Reload current state before retrying the conflicting action."),
            "http_422": ("请求参数未通过校验，请检查输入与客户端版本。", "Check request inputs and client version."),
            "http_429": ("请求过于频繁，请稍后重试。", "Too many requests; retry later."),
            "network_error": ("未收到服务响应，请检查 Cyrene 后端是否运行以及网络连接；后端无法启动时使用离线 Doctor。", "Check the Cyrene backend and network; use offline Doctor if the backend cannot start."),
        }
        zh, en = hints.get(code, ("记录出错前的操作，检查近期变更和本地日志；这些证据尚不足以确定根因。", "Review the preceding action, recent changes and local logs; these findings do not establish a root cause."))
        return {"zh": zh, "en": en}
    if any(part in code for part in ("auth", "credential")):
        return {"zh": "检查所选模型或 Agent 的 API Key / 登录状态，然后验证连接。", "en": "Check the selected model or Agent credentials, then test the connection."}
    if any(part in code for part in ("quota", "rate_limit")):
        return {"zh": "检查额度或限流状态，等待恢复或选择可用模型。", "en": "Check quota or rate limits; wait or select an available model."}
    if any(part in code for part in ("context", "too_large")):
        return {"zh": "检查上下文限制和附件大小，缩小输入后重试。", "en": "Check context limits and attachments; reduce input before retrying."}
    if any(part in code for part in ("connection", "timeout", "tls", "unavailable")):
        return {"zh": "检查模型绑定、服务地址、网络和代理；不要关闭证书校验。", "en": "Check model binding, endpoint, network and proxy; keep certificate verification enabled."}
    return {"zh": "检查关联的失败阶段与插件；证据不足时保留数据并查看本地日志。", "en": "Inspect the failed stage and Plugin; preserve data and inspect local logs if evidence is incomplete."}
