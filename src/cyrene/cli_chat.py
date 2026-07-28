"""Interactive HTTP client for the Cyrene daemon.

The CLI is deliberately a thin presentation layer. Agent execution, sessions,
permissions, tools, plans, persistence, and interruption remain owned by the
daemon and its Workbench application services.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import random
import shlex
import signal
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.table import Table
from rich.text import Text


DEFAULT_DAEMON_URL = "http://localhost:4242"
DEFAULT_TIMEOUT_SECONDS = 300.0
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

_COMMANDS = (
    "/help",
    "/new",
    "/resume",
    "/mode",
    "/status",
    "/attach",
    "/attachments",
    "/detach",
    "/deep-reflect",
    "/deep-research",
    "/context",
    "/config",
    "/mcp",
    "/exit",
)

_CONTEXT_LAYER_LABELS = {
    "system_prefix": "系统前缀",
    "ephemeral": "临时注入",
    "messages": "对话消息",
}

_CONTEXT_BLOCK_LABELS = {
    "main.system.base": "基础指令",
    "main.system.effective": "系统提示",
    "main.system.static_extra": "任务框架",
    "main.system.language": "语言偏好",
    "mode.plan.discovery": "计划模式探索",
    "memory.context": "记忆注入",
    "skills.installed": "已启用技能",
    "skills.learned": "已学习技能",
    "runtime.workspace_scope": "工作区约束",
    "runtime.permission": "权限模式",
    "runtime.project_context": "项目记忆",
    "runtime.session_scope": "会话标签",
    "runtime.spawn_policy": "子代理策略",
    "runtime.goal": "目标提示",
    "ephemeral.run": "临时注入",
    "short_term.restored": "短期记忆",
    "spawn_policy.conservative": "保守策略",
    "spawn_policy.default": "默认策略",
    "spawn_policy.off": "关闭子代理",
    "spawn_policy.deep-research": "深度研究策略",
    "spawn_policy.help-me-decide": "决策支持策略",
    "spawn_policy.learning-plan": "学习计划策略",
    "spawn_policy.deep-compare": "深度对比策略",
}

_CONTEXT_MESSAGE_LABELS = {
    "compacted": "压缩历史",
    "system": "系统",
    "user": "用户",
    "assistant": "助手",
    "tool": "工具",
}

_CONTEXT_MESSAGE_COLORS = {
    "compacted": "#d7a760",
    "system": "#798593",
    "user": "#3b82f6",
    "assistant": "#9c90c7",
    "tool": "#1f9d57",
}

_CONTEXT_SYSTEM_COLORS = (
    "#c7cdd3",
    "#acc9ed",
    "#cec7e3",
    "#9bcdb5",
    "#d8bd8c",
    "#9d7845",
    "#b2bac3",
    "#9ca7b1",
)


class ChatClientError(RuntimeError):
    """A recoverable daemon, transport, or protocol error."""


class NdjsonDecoder:
    """Incrementally decode newline-delimited JSON from arbitrary byte chunks."""

    def __init__(self, *, max_line_bytes: int = 4 * 1024 * 1024) -> None:
        self._buffer = bytearray()
        self.max_line_bytes = int(max_line_bytes)

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        if chunk:
            self._buffer.extend(chunk)
        if len(self._buffer) > self.max_line_bytes and b"\n" not in self._buffer:
            raise ChatClientError("NDJSON event exceeded the 4 MiB safety limit.")
        events: list[dict[str, Any]] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break
            raw = bytes(self._buffer[:newline]).strip()
            del self._buffer[: newline + 1]
            if raw:
                events.append(self._decode(raw))
        return events

    def finish(self) -> list[dict[str, Any]]:
        raw = bytes(self._buffer).strip()
        self._buffer.clear()
        return [self._decode(raw)] if raw else []

    @staticmethod
    def _decode(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChatClientError(f"Invalid NDJSON event: {exc}") from exc
        if not isinstance(value, dict):
            raise ChatClientError("Invalid NDJSON event: expected an object.")
        return value


@dataclass
class StreamResult:
    response: str = ""
    pending_question: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    run_id: str = ""
    cursor: int = 0


class ChatTransport:
    """Async transport for Workbench conversations and legacy ``run_live``."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_DAEMON_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        legacy: bool = False,
        chat_id: str = "",
        project_id: str = "",
        title: str = "",
        auth_token: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout = float(timeout)
        self.legacy = bool(legacy)
        self.chat_id = str(chat_id or "").strip()
        self.project_id = str(project_id or "").strip()
        self.title = str(title or "").strip()
        self.auth_token = str(auth_token or os.environ.get("CYRENE_AUTH_TOKEN") or "").strip()
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "ChatTransport":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                trust_env=False,
                headers=(
                    {"X-Cyrene-Token": self.auth_token}
                    if self.auth_token
                    else None
                ),
            )
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

    @property
    def session_label(self) -> str:
        return "run_live" if self.legacy else (self.chat_id or "new conversation")

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ChatTransport must be used as an async context manager.")
        return self._client

    async def health(self) -> dict[str, Any]:
        return await self._json("GET", "/api/status")

    async def status(self) -> dict[str, Any]:
        return await self.health()

    async def list_chats(self) -> list[dict[str, Any]]:
        params = {"project": self.project_id} if self.project_id else None
        payload = await self._json("GET", "/api/workbench/chats", params=params)
        chats = payload.get("chats") if isinstance(payload, dict) else []
        return [dict(item) for item in chats if isinstance(item, dict)]

    async def list_projects(self) -> list[dict[str, Any]]:
        payload = await self._json("GET", "/api/projects", params={"detail": "summary"})
        projects = payload.get("projects") if isinstance(payload, dict) else []
        return [dict(item) for item in projects if isinstance(item, dict)]

    async def list_chat_targets(self) -> list[dict[str, Any]]:
        payload = await self._json("GET", "/api/workbench/quick-chat/targets")
        targets = payload.get("targets") if isinstance(payload, dict) else []
        return [dict(item) for item in targets if isinstance(item, dict)]

    async def list_mcp(self) -> list[dict[str, Any]]:
        payload = await self._json("GET", "/api/settings/mcp")
        servers = payload.get("servers") if isinstance(payload, dict) else []
        return [dict(item) for item in servers if isinstance(item, dict)]

    async def use_chat(self, chat_id: str) -> dict[str, Any]:
        target = str(chat_id or "").strip()
        if not target:
            raise ChatClientError("Chat ID is required.")
        payload = await self._json("GET", f"/api/workbench/chats/{target}")
        chat = payload.get("chat") if isinstance(payload, dict) else None
        if not isinstance(chat, dict):
            raise ChatClientError(f"Conversation {target!r} was not found.")
        if str(chat.get("id") or "").startswith("legacy:"):
            raise ChatClientError("Legacy archived conversations are read-only.")
        self.legacy = False
        self.chat_id = target
        self.project_id = str(chat.get("projectId") or self.project_id)
        return chat

    async def new_chat(self, *, project_id: str = "", title: str = "") -> dict[str, Any]:
        self.legacy = False
        target_project = str(project_id or self.project_id).strip()
        if not target_project:
            targets = await self._json("GET", "/api/workbench/quick-chat/targets")
            default_project = targets.get("defaultProject") if isinstance(targets, dict) else None
            if isinstance(default_project, dict):
                target_project = str(default_project.get("id") or "").strip()
        if not target_project:
            raise ChatClientError("No Workbench project is available for a new conversation.")
        payload = await self._json(
            "POST",
            "/api/workbench/chats",
            json={
                "project": target_project,
                "title": str(title or self.title),
            },
        )
        chat = payload.get("chat") if isinstance(payload, dict) else None
        if not isinstance(chat, dict) or not str(chat.get("id") or ""):
            raise ChatClientError("Daemon did not return the new conversation.")
        self.chat_id = str(chat["id"])
        self.project_id = str(chat.get("projectId") or target_project)
        return chat

    async def ensure_chat(self) -> None:
        if not self.legacy and not self.chat_id:
            await self.new_chat()

    async def send(
        self,
        message: str,
        *,
        mode: str,
        lang: str,
        attachments: list[dict[str, Any]],
        command: str = "",
        on_event: EventHandler,
    ) -> StreamResult:
        await self.ensure_chat()
        client_request_id = f"cli_{uuid.uuid4().hex}"
        if self.legacy:
            path = "/api/chat"
            payload = {
                "message": message,
                "session_id": "run_live",
                "stream": True,
                "mode": mode,
                "lang": lang,
                "client_request_id": client_request_id,
                "attachments": attachments,
                "command": command,
            }
        else:
            path = f"/api/workbench/chats/{self.chat_id}/messages"
            payload = {
                "message": message,
                "stream": True,
                "mode": mode,
                "lang": lang,
                "attachments": attachments,
                "command": command,
            }
        return await self._stream("POST", path, payload, on_event)

    async def context(self) -> dict[str, Any]:
        if self.legacy:
            return await self._json("GET", "/api/context/state")
        await self.ensure_chat()
        return await self._json(
            "GET",
            f"/api/workbench/chats/{self.chat_id}/context",
        )

    async def context_blocks(self) -> dict[str, Any]:
        if self.legacy:
            return {"layers": [], "totalTokensEst": 0, "messageTokens": 0}
        await self.ensure_chat()
        return await self._json(
            "GET",
            f"/api/workbench/chats/{self.chat_id}/context-blocks",
        )

    async def get_setting(self, path: str) -> dict[str, Any]:
        return await self._json("GET", path)

    async def update_setting(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        method: str = "PUT",
    ) -> dict[str, Any]:
        return await self._json(method, path, json=payload)

    async def answer(
        self,
        question: dict[str, Any],
        answer: str,
        *,
        mode: str,
        on_event: EventHandler,
    ) -> StreamResult:
        question_id = str(question.get("id") or question.get("question_id") or "").strip()
        if not question_id:
            raise ChatClientError("Pending question has no ID.")
        if self.legacy:
            return await self._stream(
                "POST",
                "/api/chat/answer-question",
                {
                    "question_id": question_id,
                    "answer": answer,
                    "stream": True,
                    "client_request_id": f"cli_{uuid.uuid4().hex}",
                },
                on_event,
            )

        return await self._stream(
            "POST",
            f"/api/workbench/chats/{self.chat_id}/answer",
            {
                "question_id": question_id,
                "answer": answer,
                "mode": mode,
                "stream": True,
            },
            on_event,
        )

    async def resume(self, *, on_event: EventHandler, cursor: int = 0) -> StreamResult:
        if self.legacy:
            raise ChatClientError("Legacy run_live does not provide a reconnectable run stream.")
        if not self.chat_id:
            raise ChatClientError("Use --chat CHAT_ID or /use CHAT_ID before resuming.")
        return await self._stream(
            "GET",
            f"/api/workbench/chats/{self.chat_id}/run-stream",
            None,
            on_event,
            params={"cursor": max(0, int(cursor or 0))},
        )

    async def interrupt(self) -> bool:
        params = {"session_id": self.session_label}
        payload = await self._json("POST", "/api/chat/interrupt", params=params)
        return bool(payload.get("interrupted")) if isinstance(payload, dict) else False

    async def clear(self) -> None:
        if self.legacy:
            await self._json("POST", "/api/chat/clear")
        else:
            self.chat_id = ""

    async def upload(self, paths: Iterable[Path]) -> list[dict[str, Any]]:
        client = self._require_client()
        opened: list[Any] = []
        files: list[tuple[str, tuple[str, Any, str]]] = []
        try:
            for path in paths:
                resolved = Path(path).expanduser().resolve()
                if not resolved.is_file():
                    raise ChatClientError(f"Attachment not found: {path}")
                handle = resolved.open("rb")
                opened.append(handle)
                content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
                files.append(("files", (resolved.name, handle, content_type)))
            response = await client.post("/api/chat/upload", files=files)
            await self._raise_for_status(response)
            payload = response.json()
        finally:
            for handle in opened:
                handle.close()
        uploaded = payload.get("files") if isinstance(payload, dict) else []
        return [dict(item) for item in uploaded if isinstance(item, dict)]

    async def _stream(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        on_event: EventHandler,
        *,
        params: dict[str, Any] | None = None,
    ) -> StreamResult:
        client = self._require_client()
        decoder = NdjsonDecoder()
        result = StreamResult()
        try:
            request_kwargs = {"json": payload} if payload is not None else {}
            if params:
                request_kwargs["params"] = params
            async with client.stream(method, path, **request_kwargs) as response:
                if response.is_error:
                    body = await response.aread()
                    raise self._http_error(response, body)
                async for chunk in response.aiter_bytes():
                    for event in decoder.feed(chunk):
                        await on_event(event)
                        self._update_result(result, event)
                for event in decoder.finish():
                    await on_event(event)
                    self._update_result(result, event)
        except httpx.ConnectError as exc:
            raise ChatClientError(
                f"Cannot connect to Cyrene daemon at {self.base_url}. "
                "Start it with: cyrene start"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ChatClientError(f"Cyrene request timed out after {self.timeout:g}s.") from exc
        except httpx.HTTPError as exc:
            raise ChatClientError(str(exc)) from exc
        return result

    @staticmethod
    def _update_result(result: StreamResult, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event.get("runId"):
            result.run_id = str(event["runId"])
        if event.get("_seq"):
            result.cursor = max(result.cursor, int(event["_seq"]))
        if event_type == "reply_done":
            result.response = str(event.get("response") or "")
        elif event_type == "awaiting_user":
            pending = event.get("pending_question") or event.get("pendingQuestion")
            result.pending_question = dict(pending) if isinstance(pending, dict) else None
        elif event_type == "error":
            result.error = dict(event)

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        client = self._require_client()
        try:
            response = await client.request(method, path, **kwargs)
            await self._raise_for_status(response)
            payload = response.json()
        except httpx.ConnectError as exc:
            raise ChatClientError(
                f"Cannot connect to Cyrene daemon at {self.base_url}. "
                "Start it with: cyrene start"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ChatClientError(f"Cyrene request timed out after {self.timeout:g}s.") from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ChatClientError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise ChatClientError(f"Invalid response from {path}: expected an object.")
        return payload

    async def _raise_for_status(self, response: httpx.Response) -> None:
        if not response.is_error:
            return
        body = await response.aread()
        raise self._http_error(response, body)

    @staticmethod
    def _http_error(response: httpx.Response, body: bytes) -> ChatClientError:
        detail = ""
        try:
            payload = json.loads(body.decode("utf-8"))
            if isinstance(payload, dict):
                detail = str(payload.get("error") or payload.get("detail") or "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = body.decode("utf-8", errors="replace").strip()
        message = detail or response.reason_phrase or "request failed"
        if response.status_code == 401 and message == "bad token":
            message = (
                "daemon requires authentication; set CYRENE_AUTH_TOKEN to the "
                "daemon token, or use a daemon started by `cyrene start`"
            )
        return ChatClientError(f"Cyrene API error {response.status_code}: {message}")


class JsonRenderer:
    def __init__(self, *, stream: Any = None) -> None:
        self.stream = stream or sys.stdout

    async def handle(self, event: dict[str, Any]) -> None:
        print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), file=self.stream, flush=True)

    def header(self, _session: str, _mode: str) -> None:
        return

    def info(self, message: str) -> None:
        print(json.dumps({"type": "cli_info", "message": message}, ensure_ascii=False), file=self.stream)

    def error(self, message: str) -> None:
        print(json.dumps({"type": "cli_error", "message": message}, ensure_ascii=False), file=self.stream)

    def finish(self) -> None:
        return

    async def begin_turn(self, _activity: str = "正在思考") -> None:
        return

    async def end_turn(self, *, _success: bool) -> None:
        return

    def resume_activity(self, _activity: str = "正在继续") -> None:
        return


class RichRenderer:
    """Append-only renderer that preserves normal terminal scrollback."""

    _ACTIVITY_SYMBOLS = ("✶", "✸", "✹", "✺", "✷", "◌")

    def __init__(
        self,
        *,
        color: bool = True,
        verbose: bool = False,
        show_reasoning: bool = False,
    ) -> None:
        self.console = Console(
            no_color=not color,
            color_system="auto" if color else None,
            highlight=False,
        )
        self.verbose = verbose
        self.reply_open = False
        self.saw_reply_delta = False
        self._last_done_response = ""
        self._last_progress: dict[str, int] = {}
        self.show_reasoning = bool(show_reasoning)
        self._turn_started_at = 0.0
        self._reasoning_started_at = 0.0
        self._reasoning_chunks: list[str] = []
        self._reasoning_rounds: list[tuple[float, str]] = []
        self._thought_summary_printed = False
        self._activity = "正在思考"
        self._status: Live | None = None
        self._activity_symbol = ""
        self._timer_task: asyncio.Task[None] | None = None

    def header(self, session: str, mode: str) -> None:
        self.console.print()
        self.console.print("[bold bright_cyan]CYRENE[/]  [bold]Agent[/]")
        self.console.print(
            f"[dim]{escape(session)}  ·  {escape(mode)}[/]  [green]● 已连接[/]"
        )
        self.console.rule(style="bright_black")
        self.console.print(
            "[dim]输入任务；/help 查看命令，Alt+Enter 换行，Ctrl+O 展开思考，Ctrl+C 退出。[/]"
        )
        self.console.print()

    def input_rule(self) -> None:
        self.console.rule(style="bright_black")

    async def handle(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "reasoning_start":
            if not self._reasoning_started_at:
                self._reasoning_started_at = time.monotonic()
            self._reasoning_chunks = []
            self.resume_activity("正在思考")
            return
        if event_type == "reasoning_delta":
            delta = str(event.get("delta") or "")
            if delta:
                self._reasoning_chunks.append(delta)
            return
        if event_type == "reasoning_done":
            response = str(event.get("response") or "".join(self._reasoning_chunks)).strip()
            started = self._reasoning_started_at or self._turn_started_at
            duration = max(0.0, time.monotonic() - started)
            self._stop_status()
            if response:
                self._reasoning_rounds.append((duration, response))
            self._print_thought(duration, response)
            self._thought_summary_printed = True
            self._reasoning_started_at = 0.0
            return
        if event_type == "reply_start":
            self._stop_status()
            if not self._thought_summary_printed:
                duration = max(0.0, time.monotonic() - self._turn_started_at)
                self._print_thought(duration, "")
                self._thought_summary_printed = True
            if not self.reply_open:
                self.console.print("\n[bold bright_cyan]Cyrene ›[/] ", end="")
                self.reply_open = True
                self._last_done_response = ""
            return
        if event_type == "reply_delta":
            if not self.reply_open:
                self.console.print("\n[bold bright_cyan]Cyrene ›[/] ", end="")
                self.reply_open = True
            text = str(event.get("delta") or "")
            if text:
                self.console.print(text, end="", markup=False, soft_wrap=True)
                self.saw_reply_delta = True
            return
        if event_type == "reply_done":
            final = str(event.get("response") or "")
            if not self.reply_open and final and final == self._last_done_response:
                return
            if not self.reply_open:
                self.console.print("\n[bold bright_cyan]Cyrene ›[/] ", end="")
                if final:
                    self.console.print(final, end="", markup=False, soft_wrap=True)
            elif final and not self.saw_reply_delta:
                self.console.print(final, end="", markup=False, soft_wrap=True)
            self.console.print()
            self.reply_open = False
            self.saw_reply_delta = False
            self._last_done_response = final
            return
        if event_type == "tool_call_started":
            self._break_reply()
            self._reasoning_started_at = 0.0
            tool = escape(str(event.get("tool") or "tool"))
            self.console.print(f"  [bright_cyan]◌[/] [bold]{tool}[/]  [dim]运行中[/]")
            self.resume_activity(f"正在执行 {tool}")
            return
        if event_type == "tool_call_progress":
            self._break_reply()
            call_id = str(event.get("tool_call_id") or "")
            current = int(event.get("current") or 0)
            total = int(event.get("total") or 0)
            percent = int((current / total) * 100) if total else 100
            bucket = percent // 10
            if self._last_progress.get(call_id) == bucket and percent not in {0, 100}:
                return
            self._last_progress[call_id] = bucket
            label = escape(str(event.get("label") or ""))
            self.console.print(f"    [dim]↳ {percent:>3}% {label}[/]")
            return
        if event_type == "tool_call_finished":
            self._break_reply()
            tool = escape(str(event.get("tool") or "tool"))
            failed = bool(event.get("failed")) or str(event.get("status") or "") == "failed"
            symbol, color = ("×", "red") if failed else ("✓", "green")
            self.console.print(f"  [{color}]{symbol}[/] [bold]{tool}[/]")
            self._reasoning_started_at = time.monotonic()
            self._thought_summary_printed = False
            self.resume_activity("正在思考")
            return
        if event_type == "phase_transition":
            self._break_reply()
            source = str(event.get("from") or "")
            target = str(event.get("to") or "")
            detail = str(event.get("detail") or "")
            transition = f"{source} → {target}".strip(" →")
            suffix = f"  {escape(detail)}" if detail else ""
            self.console.print(f"[bright_cyan]→[/] [dim]{escape(transition)}{suffix}[/]")
            return
        if event_type in {"plan", "plan_progress"}:
            self._break_reply()
            self._render_plan(event)
            return
        if event_type == "intermediate_message":
            self._break_reply()
            message = event.get("message")
            content = str(message.get("content") or "") if isinstance(message, dict) else ""
            if content:
                self.console.print(f"[dim]Cyrene · {escape(content)}[/]")
            return
        if event_type == "awaiting_user":
            self._break_reply()
            self._stop_status()
            self.console.print("[yellow]◆ 需要你的确认[/]")
            return
        if event_type in {"interrupted", "run_interrupted"}:
            self._break_reply()
            self.console.print("[yellow]■ 运行已中断[/]")
            return
        if event_type == "error":
            self._break_reply()
            self._stop_status()
            message = str(event.get("message") or event.get("error") or "Unknown error")
            self.console.print(f"[bold red]× 请求失败[/]  {escape(message)}")
            return
        if event_type == "run_finalizing":
            self.resume_activity("正在完成")
            return
        if event_type in {"ack", "saved", "heartbeat"}:
            return
        if self.verbose and not event_type.startswith("reasoning_"):
            self._break_reply()
            self.console.print(f"[dim]{escape(json.dumps(event, ensure_ascii=False, default=str))}[/]")

    def _render_plan(self, event: dict[str, Any]) -> None:
        if str(event.get("type") or "") == "plan_progress":
            step = event.get("step") or event.get("step_number") or "?"
            status = escape(str(event.get("status") or "updated"))
            self.console.print(f"[bright_cyan]◆[/] 计划步骤 {step}  [dim]{status}[/]")
            return
        plan = event.get("plan")
        if not isinstance(plan, dict):
            return
        title = escape(str(plan.get("title") or "执行计划"))
        self.console.print(f"[bold bright_cyan]◆ {title}[/]")
        for index, step in enumerate(plan.get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            status = str(step.get("status") or "pending")
            symbol = {"completed": "✓", "in_progress": "◌", "failed": "×"}.get(status, "○")
            self.console.print(f"  {symbol} {index}. {escape(str(step.get('title') or '步骤'))}")

    def _break_reply(self) -> None:
        if self.reply_open:
            self.console.print()
            self.reply_open = False
            self.saw_reply_delta = False

    def info(self, message: str) -> None:
        self._break_reply()
        self.console.print(f"[dim]{escape(message)}[/]")

    def error(self, message: str) -> None:
        self._break_reply()
        self.console.print(f"[bold red]×[/] {escape(message)}")

    def finish(self) -> None:
        self._break_reply()

    async def begin_turn(self, activity: str = "正在思考") -> None:
        self._stop_status()
        if self._timer_task is not None:
            self._timer_task.cancel()
        self._turn_started_at = time.monotonic()
        self._reasoning_started_at = self._turn_started_at
        self._reasoning_chunks = []
        self._reasoning_rounds = []
        self._thought_summary_printed = False
        self._activity = activity
        self._start_status()
        self._timer_task = asyncio.create_task(self._refresh_timer())

    async def end_turn(self, *, _success: bool) -> None:
        task = self._timer_task
        self._timer_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._stop_status()
        if not self._turn_started_at:
            return
        duration = max(0.0, time.monotonic() - self._turn_started_at)
        if _success:
            self.console.print(
                f"[dim]✻ 完成，用时 {self._format_elapsed(duration)}[/]"
            )
        else:
            self.console.print(
                f"[dim]✻ 已停止，用时 {self._format_elapsed(duration)}[/]"
            )
        self._turn_started_at = 0.0

    def resume_activity(self, activity: str = "正在继续") -> None:
        if not self._turn_started_at:
            return
        self._activity = activity
        self._start_status()
        self._update_status()

    def toggle_reasoning(self) -> None:
        self.show_reasoning = not self.show_reasoning
        if not self.show_reasoning:
            self.info("思考详情已折叠。")
            return
        if not self._reasoning_rounds:
            self.info("当前还没有可显示的思考详情。")
            return
        self.console.print("[bold bright_cyan]思考详情[/]")
        for index, (duration, content) in enumerate(self._reasoning_rounds, start=1):
            if len(self._reasoning_rounds) > 1:
                self.console.print(
                    f"[dim]第 {index} 段 · {self._format_elapsed(duration)}[/]"
                )
            self.console.print(content, markup=False, style="dim", soft_wrap=True)

    def _print_thought(self, duration: float, content: str) -> None:
        suffix = "" if self.show_reasoning else "（Ctrl+O 展开）"
        self.console.print(
            f"[dim]✻ 思考了 {self._format_elapsed(duration)}{suffix}[/]"
        )
        if self.show_reasoning and content:
            self.console.print(content, markup=False, style="dim", soft_wrap=True)

    def _start_status(self) -> None:
        if self._status is not None:
            return
        self._activity_symbol = ""
        self._status = Live(
            "",
            console=self.console,
            refresh_per_second=20,
            transient=True,
        )
        self._status.start()

    def _stop_status(self) -> None:
        if self._status is None:
            return
        self._status.stop()
        self._status = None

    def _update_status(self) -> None:
        if self._status is None or not self._turn_started_at:
            return
        elapsed = max(0.0, time.monotonic() - self._turn_started_at)
        self._activity_symbol = self._next_activity_symbol()
        self._status.update(
            Text.from_markup(
                f"[bright_cyan]{self._activity_symbol}[/] "
                f"[bold]{escape(self._activity)}[/]  "
                f"[dim]{self._format_elapsed(elapsed)}[/]"
            ),
            refresh=True,
        )

    def _next_activity_symbol(self) -> str:
        choices = [
            symbol for symbol in self._ACTIVITY_SYMBOLS
            if symbol != self._activity_symbol
        ]
        return random.choice(choices)

    async def _refresh_timer(self) -> None:
        while True:
            self._update_status()
            await asyncio.sleep(0.1)

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        if total < 60:
            return f"{total}s"
        minutes, remainder = divmod(total, 60)
        if minutes < 60:
            return f"{minutes}m {remainder:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes:02d}m {remainder:02d}s"


@dataclass
class ChatOptions:
    mode: str = "default"
    lang: str = "zh"
    json_output: bool = False
    color: bool = True
    verbose: bool = False
    show_reasoning: bool = False
    history_file: str = ""
    queued_attachments: list[dict[str, Any]] = field(default_factory=list)


class InteractiveChat:
    def __init__(
        self,
        transport: ChatTransport,
        renderer: RichRenderer | JsonRenderer,
        options: ChatOptions,
    ) -> None:
        self.transport = transport
        self.renderer = renderer
        self.options = options
        self._prompt: PromptSession[str] | None = None
        self._ctrl_c_deadline = 0.0
        self._exit_requested = False

    def _build_prompt_session(self) -> PromptSession[str]:
        history = (
            FileHistory(str(Path(self.options.history_file).expanduser()))
            if self.options.history_file
            else InMemoryHistory()
        )
        bindings = KeyBindings()

        @bindings.add("escape", "enter")
        def _insert_newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-c")
        def _confirm_exit(event: Any) -> None:
            if self._arm_ctrl_c_exit():
                self._exit_requested = True
                event.app.exit(result="")
            else:
                self.renderer.info("再次按 Ctrl+C 退出。")
                event.app.invalidate()

        @bindings.add("c-o")
        def _toggle_reasoning(event: Any) -> None:
            if isinstance(self.renderer, RichRenderer):
                self.renderer.toggle_reasoning()
                self.options.show_reasoning = self.renderer.show_reasoning
            event.app.invalidate()

        return PromptSession(
            history=history,
            completer=WordCompleter(list(_COMMANDS), sentence=True),
            complete_while_typing=False,
            key_bindings=bindings,
            style=self._terminal_style(),
        )

    def _terminal_style(self) -> Style:
        if not self.options.color:
            return Style.from_dict({})
        return Style.from_dict({
            "prompt": "bold ansibrightcyan",
            # prompt_toolkit gives bottom toolbars a reversed background by
            # default. Reset it so the lower input border stays a thin rule.
            "bottom-toolbar": "noreverse fg:ansibrightblack bg:default",
            "completion-menu.completion": "ansigray",
            "completion-menu.completion.current": "bold ansibrightcyan reverse",
            "selection-title": "bold ansibrightcyan",
            "selection-current": "bold ansibrightcyan reverse",
            "selection-help": "ansibrightblack",
        })

    def _prompt_session(self) -> PromptSession[str]:
        if self._prompt is None:
            self._prompt = self._build_prompt_session()
        return self._prompt

    async def run(self) -> int:
        await self.transport.health()
        self.renderer.header(self.transport.session_label, self.options.mode)
        while True:
            try:
                if isinstance(self.renderer, RichRenderer):
                    self.renderer.input_rule()
                with patch_stdout(raw=True):
                    text = (await self._prompt_session().prompt_async(
                        [("class:prompt", "› ")],
                        bottom_toolbar=self._input_bottom_rule,
                    )).strip()
            except EOFError:
                self.renderer.info("会话已关闭；Daemon 继续运行。")
                return 0
            except KeyboardInterrupt:
                if self._arm_ctrl_c_exit():
                    self.renderer.info("会话已关闭；Daemon 继续运行。")
                    return 0
                self.renderer.info("再次按 Ctrl+C 退出。")
                continue
            if self._exit_requested:
                self.renderer.info("会话已关闭；Daemon 继续运行。")
                return 0
            if not text:
                continue
            if text.startswith("/"):
                try:
                    should_exit = await self._command(text)
                except ChatClientError as exc:
                    self.renderer.error(str(exc))
                    continue
                if should_exit:
                    return 0
                continue
            await self._run_turn(text, allow_prompt=True)
            if self._exit_requested:
                self.renderer.info("会话已关闭；当前 Agent Run 继续在后台运行。")
                return 0

    @staticmethod
    def _input_bottom_rule() -> str:
        width = max(10, shutil.get_terminal_size(fallback=(80, 24)).columns - 1)
        return "─" * width

    def _arm_ctrl_c_exit(self) -> bool:
        now = time.monotonic()
        if now <= self._ctrl_c_deadline:
            self._ctrl_c_deadline = 0.0
            return True
        self._ctrl_c_deadline = now + 2.0
        return False

    async def run_once(self, text: str) -> int:
        await self.transport.health()
        return 0 if await self._run_turn(text, allow_prompt=False) else 1

    async def _run_turn(
        self,
        text: str,
        *,
        allow_prompt: bool,
        command: str = "",
    ) -> bool:
        restore_interrupt = self._install_interrupt_handler()
        turn_success = False
        await self.renderer.begin_turn()
        try:
            result = await self.transport.send(
                text,
                mode=self.options.mode,
                lang=self.options.lang,
                attachments=list(self.options.queued_attachments),
                command=command,
                on_event=self.renderer.handle,
            )
            self.options.queued_attachments.clear()
            while result.pending_question:
                if not allow_prompt:
                    if not self.options.json_output:
                        self.renderer.error(
                            "本次运行需要用户确认；请使用交互式 `cyrene chat` 继续。"
                        )
                    return False
                answer = await self._ask_question(result.pending_question)
                self.renderer.resume_activity("正在继续")
                result = await self.transport.answer(
                    result.pending_question,
                    answer,
                    mode=self.options.mode,
                    on_event=self.renderer.handle,
                )
            if result.error:
                return False
            turn_success = True
            return True
        except asyncio.CancelledError:
            if self._exit_requested:
                return False
            raise
        except ChatClientError as exc:
            self.renderer.finish()
            self.renderer.error(str(exc))
            return False
        finally:
            restore_interrupt()
            self.renderer.finish()
            await self.renderer.end_turn(_success=turn_success)

    async def resume(self, cursor: int = 0) -> bool:
        restore_interrupt = self._install_interrupt_handler()
        turn_success = False
        await self.renderer.begin_turn("正在恢复运行")
        try:
            result = await self.transport.resume(
                on_event=self.renderer.handle,
                cursor=max(0, int(cursor or 0)),
            )
            turn_success = result.error is None
            return turn_success
        except ChatClientError as exc:
            self.renderer.error(str(exc))
            return False
        except asyncio.CancelledError:
            if self._exit_requested:
                return False
            raise
        finally:
            restore_interrupt()
            self.renderer.finish()
            await self.renderer.end_turn(_success=turn_success)

    def _install_interrupt_handler(self) -> Callable[[], None]:
        """Require two Ctrl+C presses before detaching from an active stream."""
        try:
            loop = asyncio.get_running_loop()
            previous = signal.getsignal(signal.SIGINT)
            active_task = asyncio.current_task()

            def request_exit() -> None:
                if self._arm_ctrl_c_exit():
                    self._exit_requested = True
                    if active_task is not None:
                        active_task.cancel()
                else:
                    self.renderer.info("再次按 Ctrl+C 退出。")

            loop.add_signal_handler(signal.SIGINT, request_exit)
        except (NotImplementedError, RuntimeError, ValueError):
            return lambda: None

        def restore() -> None:
            loop.remove_signal_handler(signal.SIGINT)
            signal.signal(signal.SIGINT, previous)

        return restore

    async def _ask_question(self, question: dict[str, Any]) -> str:
        text = str(question.get("text") or "请确认后继续。")
        options = question.get("options") if isinstance(question.get("options"), list) else []
        if isinstance(self.renderer, RichRenderer):
            self.renderer.console.print(f"\n[bold yellow]{escape(text)}[/]")
            for index, option in enumerate(options, start=1):
                label = self._option_label(option)
                self.renderer.console.print(f"  [bright_cyan][{index}][/] {escape(label)}")
        while True:
            with patch_stdout(raw=True):
                answer = (await self._prompt_session().prompt_async("确认 › ")).strip()
            if not answer:
                continue
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return self._option_label(options[int(answer) - 1])
            return answer

    @staticmethod
    def _option_label(option: Any) -> str:
        if isinstance(option, dict):
            for key in ("label", "text", "value", "title", "name"):
                value = str(option.get(key) or "").strip()
                if value:
                    return value
        return str(option or "").strip()

    async def _command(self, raw: str) -> bool:
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            self.renderer.error(str(exc))
            return False
        command = parts[0].lower()
        args = parts[1:]
        if command in {"/exit", "/quit"}:
            self.renderer.info("会话已关闭；Daemon 继续运行。")
            return True
        if command in {"/help", "/h"}:
            self._help()
        elif command == "/mode":
            if not args or args[0] not in {"default", "plan", "auto"}:
                self.renderer.error("用法：/mode default|plan|auto")
            else:
                self.options.mode = args[0]
                self.renderer.info(f"权限模式已切换为 {args[0]}。")
        elif command == "/new":
            await self._new_chat()
        elif command == "/status":
            await self._show_status()
        elif command == "/resume":
            await self._resume_chat(args[0] if args else "")
        elif command == "/mcp":
            await self._show_mcp()
        elif command == "/context":
            await self._show_context()
        elif command == "/config":
            await self._config_menu()
        elif command == "/attach":
            if not args:
                self.renderer.error("用法：/attach PATH [PATH ...]")
            else:
                uploaded = await self.transport.upload([Path(item) for item in args])
                self.options.queued_attachments.extend(uploaded)
                for item in uploaded:
                    self.renderer.info(f"已附加：{item.get('name')} ({item.get('size', 0)} bytes)")
        elif command == "/attachments":
            if not self.options.queued_attachments:
                self.renderer.info("当前没有待发送附件。")
            for index, item in enumerate(self.options.queued_attachments, start=1):
                self.renderer.info(f"{index}. {item.get('name')} ({item.get('size', 0)} bytes)")
        elif command == "/detach":
            if not args or args[0] == "all":
                self.options.queued_attachments.clear()
                self.renderer.info("已移除全部待发送附件。")
            elif args[0].isdigit() and 1 <= int(args[0]) <= len(self.options.queued_attachments):
                item = self.options.queued_attachments.pop(int(args[0]) - 1)
                self.renderer.info(f"已移除：{item.get('name')}")
            else:
                self.renderer.error("用法：/detach INDEX|all")
        elif command == "/deep-reflect":
            await self._run_turn(
                "/deep-reflect",
                allow_prompt=True,
                command="deep-reflect",
            )
        elif command == "/deep-research":
            topic = " ".join(args).strip()
            if not topic:
                topic = await self._prompt_text("研究主题 › ")
            if topic:
                await self._run_turn(
                    topic,
                    allow_prompt=True,
                    command="deep-research",
                )
        else:
            self.renderer.error(f"未知命令：{command}。输入 /help 查看帮助。")
        return False

    def _help(self) -> None:
        if not isinstance(self.renderer, RichRenderer):
            self.renderer.info(" ".join(_COMMANDS))
            return
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="bright_cyan", no_wrap=True)
        table.add_column(style="dim")
        rows = (
            ("/new", "选择 Project 并创建新对话"),
            ("/resume [SESSION_ID]", "选择并继续已有 Session"),
            ("/mode default|plan|auto", "切换权限模式"),
            ("/attach PATH...", "上传并附加文件"),
            ("/attachments", "查看待发送附件"),
            ("/detach INDEX|all", "移除待发送附件"),
            ("/deep-reflect", "对当前对话运行深度反思"),
            ("/deep-research [TOPIC]", "运行深度研究"),
            ("/context", "查看当前 Session 的上下文占用"),
            ("/config", "查看和修改全部设置"),
            ("/status", "查看 Daemon 与模型状态"),
            ("/mcp", "列出 MCP Server"),
            ("/exit", "退出 CLI，保留 Daemon"),
        )
        for command, detail in rows:
            table.add_row(command, detail)
        self.renderer.console.print(table)

    async def _prompt_text(self, message: str) -> str:
        with patch_stdout(raw=True):
            return (await self._prompt_session().prompt_async(message)).strip()

    async def _choose(
        self,
        title: str,
        items: list[dict[str, Any]],
        *,
        label: Callable[[dict[str, Any]], str],
        auto_single: bool = False,
    ) -> dict[str, Any] | None:
        if not items:
            self.renderer.info("没有可选择的项目。")
            return None
        if len(items) == 1 and auto_single:
            return items[0]
        if isinstance(self.renderer, RichRenderer):
            return await self._choose_with_arrows(title, items, label=label)
        while True:
            answer = await self._prompt_text("选择 › ")
            if answer in {"", "0"}:
                return None
            if answer.isdigit() and 1 <= int(answer) <= len(items):
                return items[int(answer) - 1]
            self.renderer.error("请输入列表中的编号。")

    async def _choose_with_arrows(
        self,
        title: str,
        items: list[dict[str, Any]],
        *,
        label: Callable[[dict[str, Any]], str],
    ) -> dict[str, Any] | None:
        selected = 0
        page_size = min(12, len(items))
        bindings = KeyBindings()

        def formatted_items() -> list[tuple[str, str]]:
            start = max(0, min(selected - page_size // 2, len(items) - page_size))
            end = min(len(items), start + page_size)
            fragments: list[tuple[str, str]] = [
                ("class:selection-title", f"\n{title}\n"),
            ]
            for index in range(start, end):
                marker = "›" if index == selected else " "
                style = "class:selection-current" if index == selected else ""
                fragments.append((
                    style,
                    f" {marker} {label(items[index])}\n",
                ))
            fragments.append((
                "class:selection-help",
                " ↑/↓ 选择  Enter 确认  Esc 取消",
            ))
            return fragments

        control = FormattedTextControl(
            text=formatted_items,
            focusable=True,
            show_cursor=False,
        )
        app: Application[dict[str, Any] | None]

        @bindings.add("up")
        @bindings.add("c-p")
        def _up(event: Any) -> None:
            nonlocal selected
            selected = (selected - 1) % len(items)
            event.app.invalidate()

        @bindings.add("down")
        @bindings.add("c-n")
        def _down(event: Any) -> None:
            nonlocal selected
            selected = (selected + 1) % len(items)
            event.app.invalidate()

        @bindings.add("enter")
        def _accept(event: Any) -> None:
            event.app.exit(result=items[selected])

        @bindings.add("escape")
        @bindings.add("c-c")
        def _cancel(event: Any) -> None:
            event.app.exit(result=None)

        app = Application(
            layout=Layout(
                Window(
                    content=control,
                    height=page_size + 3,
                    dont_extend_height=True,
                    always_hide_cursor=True,
                )
            ),
            key_bindings=bindings,
            style=self._terminal_style(),
            full_screen=False,
            erase_when_done=False,
        )
        with patch_stdout(raw=True):
            return await app.run_async()

    async def _new_chat(self) -> None:
        projects = await self.transport.list_projects()
        selected = await self._choose(
            "选择新对话所属 Project",
            projects,
            label=lambda item: (
                f"{item.get('name') or 'Untitled'}  "
                f"[{item.get('workspacePath') or item.get('id') or ''}]"
            ),
            auto_single=True,
        )
        if selected is None:
            return
        chat = await self.transport.new_chat(
            project_id=str(selected.get("id") or ""),
            title="",
        )
        self.renderer.info(
            f"已在 {selected.get('name') or 'Project'} 创建对话："
            f"{chat.get('title')} ({chat.get('id')})"
        )

    async def _resume_chat(self, chat_id: str = "") -> None:
        targets = await self.transport.list_chat_targets()
        selected = next(
            (item for item in targets if str(item.get("chatId") or "") == chat_id),
            None,
        )
        if chat_id and selected is None:
            self.renderer.error(f"未找到 Session：{chat_id}")
            return
        if selected is None:
            selected = await self._choose(
                "选择要继续的 Session",
                targets,
                label=lambda item: (
                    f"{item.get('title') or 'Untitled'}  ·  "
                    f"{item.get('projectName') or 'Unknown Project'}  ·  "
                    f"{'运行中' if item.get('running') else item.get('preview') or '空对话'}"
                ),
            )
        if selected is None:
            return
        chat = await self.transport.use_chat(str(selected.get("chatId") or ""))
        self.renderer.info(
            f"已进入：{chat.get('title')}  ·  "
            f"{selected.get('projectName') or 'Unknown Project'}  "
            f"({chat.get('id')})"
        )

    @staticmethod
    def _redact_settings(value: Any, key: str = "") -> Any:
        sensitive = any(
            marker in key.lower()
            for marker in ("api_key", "token", "password", "secret", "cookie")
        )
        if sensitive and value not in ("", None, False):
            return "••••••••"
        if isinstance(value, dict):
            return {
                str(item_key): InteractiveChat._redact_settings(item_value, str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [InteractiveChat._redact_settings(item, key) for item in value]
        return value

    def _print_json(self, value: Any) -> None:
        safe = self._redact_settings(value)
        if isinstance(self.renderer, RichRenderer):
            self.renderer.console.print_json(data=safe, ensure_ascii=False)
        else:
            self.renderer.info(json.dumps(safe, ensure_ascii=False))

    async def _config_menu(self) -> None:
        sections = [
            {"id": "general", "name": "General / Agent / Budget"},
            {"id": "models", "name": "Models"},
            {"id": "tools", "name": "Tools & Capability Packages"},
            {"id": "keys", "name": "API Keys"},
            {"id": "soul", "name": "SOUL / Personality"},
            {"id": "integrations", "name": "Integrations"},
            {"id": "mcp", "name": "MCP Servers"},
            {"id": "skills", "name": "Skills"},
            {"id": "remote", "name": "Remote Control"},
            {"id": "search", "name": "Search"},
            {"id": "profile", "name": "Profile"},
            {"id": "budget", "name": "Budget Usage"},
            {"id": "data", "name": "Data & Backups"},
            {"id": "cli", "name": "CLI Preferences"},
            {"id": "about", "name": "About / Runtime"},
        ]
        while True:
            selected = await self._choose(
                "Settings",
                sections,
                label=lambda item: str(item["name"]),
            )
            if selected is None:
                return
            section = str(selected["id"])
            if section == "general":
                await self._config_general()
            elif section == "tools":
                await self._config_tools()
            elif section == "keys":
                await self._config_keys()
            elif section == "soul":
                await self._config_soul()
            elif section == "mcp":
                await self._config_json(
                    "/api/settings/mcp",
                    "/api/settings/mcp",
                    extract=lambda data: data.get("configs") or [],
                    wrap=lambda data: {"servers": data},
                )
            elif section == "profile":
                await self._config_json(
                    "/api/ui-data",
                    "/api/profile",
                    extract=lambda data: data.get("user") or {},
                    merge=False,
                )
            elif section == "budget":
                self._print_json(
                    await self.transport.get_setting("/api/settings/budget/stats")
                )
            elif section == "skills":
                await self._config_skills()
            elif section == "remote":
                await self._config_json(
                    "/api/remote/settings",
                    "/api/remote/settings",
                )
            elif section == "data":
                await self._config_data()
            elif section == "cli":
                await self._config_cli()
            elif section == "about":
                self._print_json(
                    await self.transport.get_setting("/api/settings/config")
                )
            elif section == "search":
                await self._config_json(
                    "/api/settings/search",
                    "/api/settings/search",
                )
            elif section == "integrations":
                await self._config_json(
                    "/api/settings/integrations",
                    "/api/settings/integrations",
                    merge=False,
                )
            else:
                await self._config_json(
                    f"/api/settings/{section}",
                    f"/api/settings/{section}",
                )

    async def _config_general(self) -> None:
        current = await self.transport.get_setting("/api/settings/config")
        self._print_json(current)
        key = await self._prompt_text("设置项（留空返回）› ")
        if not key:
            return
        if key not in current:
            self.renderer.error(f"未知设置项：{key}")
            return
        raw = await self._prompt_text(f"{key} [{current[key]}] › ")
        if not raw:
            return
        try:
            value = self._coerce_value(raw, current[key])
            result = await self.transport.update_setting(
                "/api/settings/config",
                {key: value},
            )
            self.renderer.info(f"已更新：{', '.join(result.get('changed') or [key])}")
        except (ValueError, ChatClientError) as exc:
            self.renderer.error(str(exc))

    @staticmethod
    def _coerce_value(raw: str, current: Any) -> Any:
        text = str(raw).strip()
        if isinstance(current, bool):
            lowered = text.lower()
            if lowered in {"true", "1", "yes", "on", "是"}:
                return True
            if lowered in {"false", "0", "no", "off", "否"}:
                return False
            raise ValueError("请输入 true 或 false。")
        if isinstance(current, int) and not isinstance(current, bool):
            return int(text)
        if isinstance(current, float):
            return float(text)
        return text

    async def _config_tools(self) -> None:
        payload = await self.transport.get_setting("/api/settings/tools")
        items = [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("id") or ""),
                "enabled": bool(item.get("enabled")),
                "kind": "package",
            }
            for item in payload.get("packages") or []
            if isinstance(item, dict)
        ] + [
            {
                "id": str(item.get("name") or ""),
                "name": str(item.get("name") or ""),
                "enabled": bool(item.get("enabled")),
                "kind": "tool",
            }
            for item in payload.get("tools") or []
            if isinstance(item, dict) and not bool(item.get("locked"))
        ]
        selected = await self._choose(
            "选择要切换的 Capability Package 或 Tool",
            items,
            label=lambda item: (
                f"{'●' if item['enabled'] else '○'} {item['name']} "
                f"({item['kind']})"
            ),
        )
        if selected is None:
            return
        key = "packages" if selected["kind"] == "package" else "tools"
        await self.transport.update_setting(
            "/api/settings/tools",
            {key: {selected["id"]: not selected["enabled"]}},
        )
        self.renderer.info(
            f"{selected['name']} 已{'启用' if not selected['enabled'] else '停用'}。"
        )

    async def _config_keys(self) -> None:
        current = await self.transport.get_setting("/api/settings/keys")
        self._print_json(current)
        name = await self._prompt_text("Key 名称（留空返回）› ")
        if not name:
            return
        value = await self._prompt_text(f"{name} 新值 › ")
        if not value:
            return
        result = await self.transport.update_setting(
            "/api/settings/keys",
            {name: value},
        )
        self.renderer.info(f"已更新：{', '.join(result.get('updated') or [name])}")

    async def _config_soul(self) -> None:
        current = await self.transport.get_setting("/api/settings/soul")
        self._print_json(current)
        content = await self._prompt_text("新的 SOUL 内容（留空返回，Alt+Enter 换行）› ")
        if not content:
            return
        await self.transport.update_setting(
            "/api/settings/soul",
            {"content": content},
        )
        self.renderer.info("SOUL 已更新。")

    async def _config_skills(self) -> None:
        payload = await self.transport.get_setting("/api/skills/installed")
        skills = [
            dict(item)
            for item in payload.get("skills") or []
            if isinstance(item, dict)
        ]
        selected = await self._choose(
            "选择要启用/停用的 Skill",
            skills,
            label=lambda item: (
                f"{'●' if item.get('enabled', True) else '○'} "
                f"{item.get('name') or item.get('id') or 'Skill'}"
            ),
        )
        if selected is None:
            install_path = await self._prompt_text(
                "安装 Skill 路径（留空返回）› "
            )
            if install_path:
                await self.transport.update_setting(
                    "/api/skills/install",
                    {"path": install_path},
                    method="POST",
                )
                self.renderer.info("Skill 已安装。")
            return
        skill_id = str(selected.get("id") or "")
        await self.transport.update_setting(
            f"/api/skills/{skill_id}/toggle",
            {},
            method="POST",
        )
        self.renderer.info(f"{selected.get('name') or skill_id} 状态已切换。")

    async def _config_data(self) -> None:
        payload = await self.transport.get_setting("/api/backup/list")
        self._print_json(payload)
        action = (await self._prompt_text(
            "操作：export / reset / 返回（留空）› "
        )).lower()
        if action == "export":
            result = await self.transport.update_setting(
                "/api/backup/export",
                {},
                method="POST",
            )
            self._print_json(result)
        elif action == "reset":
            confirm = await self._prompt_text("输入 RESET CYRENE DATA 确认 › ")
            if confirm == "RESET CYRENE DATA":
                await self.transport.update_setting(
                    "/api/settings/reset-data",
                    {},
                    method="POST",
                )
                self.renderer.info("应用数据已重置。")
            else:
                self.renderer.info("已取消重置。")

    async def _config_cli(self) -> None:
        current = {
            "mode": self.options.mode,
            "language": self.options.lang,
            "color": self.options.color,
            "verbose": self.options.verbose,
            "thinking": "expanded" if self.options.show_reasoning else "compact",
            "history_file": self.options.history_file,
        }
        self._print_json(current)
        key = await self._prompt_text("设置项（留空返回）› ")
        if not key:
            return
        if key not in current:
            self.renderer.error(f"未知设置项：{key}")
            return
        raw = await self._prompt_text(f"{key} [{current[key]}] › ")
        if not raw:
            return
        if key == "mode" and raw not in {"default", "plan", "auto"}:
            self.renderer.error("mode 必须是 default、plan 或 auto。")
            return
        if key == "language" and raw not in {"zh", "en"}:
            self.renderer.error("language 必须是 zh 或 en。")
            return
        if key == "thinking" and raw not in {"compact", "expanded"}:
            self.renderer.error("thinking 必须是 compact 或 expanded。")
            return
        if key == "mode":
            self.options.mode = raw
        elif key == "language":
            self.options.lang = raw
        elif key == "color":
            self.options.color = bool(self._coerce_value(raw, True))
        elif key == "verbose":
            self.options.verbose = bool(self._coerce_value(raw, False))
        elif key == "thinking":
            self.options.show_reasoning = raw == "expanded"
            if isinstance(self.renderer, RichRenderer):
                self.renderer.show_reasoning = self.options.show_reasoning
        elif key == "history_file":
            self.options.history_file = raw
            self._prompt = None
        self.renderer.info(f"CLI {key} 已更新。")

    async def _config_json(
        self,
        get_path: str,
        put_path: str,
        *,
        extract: Callable[[dict[str, Any]], Any] | None = None,
        wrap: Callable[[Any], dict[str, Any]] | None = None,
        merge: bool = True,
    ) -> None:
        response = await self.transport.get_setting(get_path)
        current = extract(response) if extract else response
        self._print_json(current)
        raw = await self._prompt_text("JSON Patch（留空返回）› ")
        if not raw:
            return
        try:
            patch = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.renderer.error(f"JSON 无效：{exc}")
            return
        if merge and isinstance(current, dict) and isinstance(patch, dict):
            payload: Any = self._merge_settings(current, patch)
        else:
            payload = patch
        body = wrap(payload) if wrap else payload
        if not isinstance(body, dict):
            self.renderer.error("该设置需要 JSON Object。")
            return
        await self.transport.update_setting(put_path, body)
        self.renderer.info("设置已更新。")

    @classmethod
    def _merge_settings(
        cls,
        current: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        for key, value in patch.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = cls._merge_settings(existing, value)
            else:
                merged[key] = value
        return merged

    async def _show_context(self) -> None:
        if not self.transport.legacy and not self.transport.chat_id:
            self.renderer.info("当前尚无 Session；输入消息后会在默认 Project 新建对话。")
            return
        payload = await self.transport.context()
        if "ctxUsed" not in payload:
            self._print_json(payload)
            return
        if isinstance(self.renderer, RichRenderer) and not self.transport.legacy:
            blocks = await self.transport.context_blocks()
            if isinstance(blocks.get("layers"), list) and blocks["layers"]:
                self._render_context_blocks(payload, blocks)
                return
        used = int(payload.get("ctxUsed") or 0)
        limit = int(payload.get("ctxLimit") or 0)
        ratio = float(payload.get("ratio") or 0)
        if isinstance(self.renderer, RichRenderer):
            table = Table("Model", "Used", "Limit", "Usage", "Messages", box=None)
            table.add_row(
                str(payload.get("model") or "?"),
                f"{used:,}",
                f"{limit:,}" if limit else "unknown",
                f"{ratio * 100:.1f}%" if limit else "unknown",
                str(payload.get("messageCount") or 0),
            )
            self.renderer.console.print(table)
            for segment in payload.get("segments") or []:
                if isinstance(segment, dict) and int(segment.get("tokens") or 0):
                    self.renderer.info(
                        f"  {segment.get('key')}: "
                        f"{int(segment.get('tokens') or 0):,} tokens"
                    )
        else:
            self._print_json(payload)

    def _render_context_blocks(
        self,
        overview: dict[str, Any],
        composition: dict[str, Any],
    ) -> None:
        if not isinstance(self.renderer, RichRenderer):
            return
        layers = [
            layer for layer in composition.get("layers") or []
            if isinstance(layer, dict) and int(layer.get("totalTokens") or 0) > 0
        ]
        message_tokens = int(
            composition.get("messageTokens")
            or overview.get("ctxUsed")
            or 0
        )
        bar_total = sum(int(layer.get("totalTokens") or 0) for layer in layers)

        self.renderer.console.print("[bold]对话上下文[/]")
        self.renderer.console.print(
            f"\n[bold]{self._compact_tokens(message_tokens)}[/] [dim]tokens[/]"
        )
        segments = self._context_segments(layers)
        if segments and bar_total > 0:
            bar_width = max(
                20,
                min(64, shutil.get_terminal_size(fallback=(80, 24)).columns - 4),
            )
            self.renderer.console.print(
                self._context_bar(segments, bar_total, width=bar_width)
            )

        order = {"system_prefix": 0, "ephemeral": 1, "messages": 2}
        for layer in sorted(layers, key=lambda item: order.get(str(item.get("id")), 99)):
            layer_id = str(layer.get("id") or "")
            self.renderer.console.print(
                f"\n[bold]{escape(_CONTEXT_LAYER_LABELS.get(layer_id, str(layer.get('label') or layer_id)))}[/]"
            )
            rows = Table.grid(padding=(0, 1))
            rows.add_column(width=3, no_wrap=True)
            rows.add_column(min_width=18)
            rows.add_column(justify="right", style="dim")
            blocks = [
                block for block in layer.get("blocks") or []
                if isinstance(block, dict) and int(block.get("tokens_est") or 0) > 0
            ]
            if layer_id not in {"system_prefix", "messages"} or not blocks:
                blocks = [{
                    "id": layer_id,
                    "type": layer_id,
                    "tokens_est": int(layer.get("totalTokens") or 0),
                }]
            for block in blocks:
                label, color = self._context_block_style(layer_id, block)
                rows.add_row(
                    Text("  ■", style=color),
                    label,
                    self._compact_tokens(int(block.get("tokens_est") or 0)),
                )
            self.renderer.console.print(rows)

        limit = int(overview.get("ctxLimit") or 0)
        used = int(overview.get("ctxUsed") or message_tokens)
        ratio = float(overview.get("ratio") or 0)
        model = str(overview.get("model") or "?")
        message_count = int(overview.get("messageCount") or 0)
        window = (
            f"{used:,} / {limit:,} · {ratio * 100:.1f}%"
            if limit > 0
            else f"{used:,} tokens"
        )
        self.renderer.console.print(
            f"\n[dim]{escape(model)} · {window} · {message_count} messages[/]"
        )

    @classmethod
    def _context_segments(
        cls,
        layers: list[dict[str, Any]],
    ) -> list[tuple[int, str]]:
        segments: list[tuple[int, str]] = []
        for layer in layers:
            layer_id = str(layer.get("id") or "")
            blocks = [
                block for block in layer.get("blocks") or []
                if isinstance(block, dict) and int(block.get("tokens_est") or 0) > 0
            ]
            if layer_id in {"system_prefix", "messages"} and blocks:
                for block in blocks:
                    _label, color = cls._context_block_style(layer_id, block)
                    segments.append((int(block.get("tokens_est") or 0), color))
            else:
                color = "#db7373" if layer_id == "ephemeral" else "#798593"
                segments.append((int(layer.get("totalTokens") or 0), color))
        return segments

    @staticmethod
    def _context_bar(
        segments: list[tuple[int, str]],
        total: int,
        *,
        width: int = 48,
    ) -> Text:
        width = max(int(width), len(segments))
        bar = Text()
        remaining = width
        for index, (tokens, color) in enumerate(segments):
            if index == len(segments) - 1:
                cells = remaining
            else:
                cells = max(1, round((tokens / total) * width))
                cells = min(cells, max(1, remaining - (len(segments) - index - 1)))
            bar.append("━" * max(0, cells), style=color)
            remaining -= cells
        return bar

    @staticmethod
    def _context_block_style(
        layer_id: str,
        block: dict[str, Any],
    ) -> tuple[str, str]:
        block_id = str(block.get("id") or "")
        block_type = str(block.get("type") or "")
        if layer_id == "messages":
            return (
                _CONTEXT_MESSAGE_LABELS.get(block_type, block_type or block_id),
                _CONTEXT_MESSAGE_COLORS.get(block_type, "#798593"),
            )
        if layer_id == "ephemeral":
            return ("临时注入", "#db7373")

        label = _CONTEXT_BLOCK_LABELS.get(block_id)
        if label is None and block_id.startswith("command."):
            label = f"{block_id.removeprefix('command.')} 指令"
        if label is None and block_id.startswith("spawn_policy."):
            label = f"{block_id.removeprefix('spawn_policy.')} 策略"
        label = label or str(block.get("label") or block_id or block_type)
        shade = {
            "memory": 1,
            "skills": 2,
            "runtime": 3,
            "command_prompt": 4,
            "spawn_policy": 5,
            "short_term": 7,
        }.get(block_type, 0)
        if block_id.startswith("main.system.language") or block_id.startswith("memory."):
            shade = 1
        elif block_id.startswith("skills."):
            shade = 2
        elif block_id.startswith("runtime.workspace"):
            shade = 3
        elif block_id.startswith("runtime.spawn"):
            shade = 5
        elif block_id.startswith("runtime.permission"):
            shade = 6
        elif block_id.startswith(("spawn_policy.", "short_term.")):
            shade = 7
        return label, _CONTEXT_SYSTEM_COLORS[shade]

    @staticmethod
    def _compact_tokens(value: int) -> str:
        number = max(0, int(value or 0))
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M"
        if number >= 1_000:
            return f"{number / 1_000:.1f}k"
        return str(number)

    async def _show_chats(self) -> None:
        chats = await self.transport.list_chat_targets()
        if not isinstance(self.renderer, RichRenderer):
            self.renderer.info(json.dumps(chats, ensure_ascii=False))
            return
        table = Table("ID", "Title", "Project", "Status", box=None)
        for chat in chats[:30]:
            table.add_row(
                str(chat.get("chatId") or ""),
                str(chat.get("title") or ""),
                str(chat.get("projectName") or ""),
                "running" if chat.get("running") else "idle",
            )
        self.renderer.console.print(table)

    async def _show_status(self) -> None:
        status = await self.transport.status()
        self.renderer.info(
            f"Model: {status.get('model', '?')} · "
            f"Endpoint: {status.get('base_url', '?')} · "
            f"Session: {self.transport.session_label}"
        )

    async def _show_mcp(self) -> None:
        servers = await self.transport.list_mcp()
        if not servers:
            self.renderer.info("没有配置 MCP Server。")
            return
        for server in servers:
            self.renderer.info(
                f"{server.get('name', '?')} · {server.get('transport', '?')} · "
                f"{server.get('status', 'disconnected')} · {server.get('tool_count', 0)} tools"
            )


async def run_chat(args: Any) -> int:
    """Entry point called by :mod:`cyrene.cli`."""
    json_output = bool(getattr(args, "json", False))
    text = str(getattr(args, "text", "") or "").strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if json_output and not text and not bool(getattr(args, "list_chats", False)):
        if not bool(getattr(args, "resume", False)):
            print("cyrene chat --json requires TEXT, stdin input, or --resume.", file=sys.stderr)
            return 2

    options = ChatOptions(
        mode=str(getattr(args, "mode", "default") or "default"),
        lang=str(getattr(args, "lang", "zh") or "zh"),
        json_output=json_output,
        color=not bool(getattr(args, "no_color", False)),
        verbose=bool(getattr(args, "verbose", False)),
        history_file=str(getattr(args, "history_file", "") or ""),
    )
    renderer: RichRenderer | JsonRenderer
    renderer = JsonRenderer() if json_output else RichRenderer(
        color=options.color,
        verbose=options.verbose,
        show_reasoning=options.show_reasoning,
    )
    transport = ChatTransport(
        base_url=str(getattr(args, "url", DEFAULT_DAEMON_URL) or DEFAULT_DAEMON_URL),
        timeout=float(getattr(args, "timeout", DEFAULT_TIMEOUT_SECONDS)),
        legacy=bool(getattr(args, "legacy", False)),
        chat_id=str(getattr(args, "chat_id", "") or ""),
        project_id=str(getattr(args, "project", "") or ""),
        title=str(getattr(args, "title", "") or ""),
        auth_token=str(getattr(args, "auth_token", "") or ""),
    )
    try:
        async with transport:
            app = InteractiveChat(transport, renderer, options)
            if bool(getattr(args, "list_chats", False)):
                await transport.health()
                chats = await transport.list_chats()
                if json_output:
                    print(json.dumps(chats, ensure_ascii=False))
                else:
                    await app._show_chats()
                return 0
            if bool(getattr(args, "resume", False)):
                await transport.health()
                return 0 if await app.resume(int(getattr(args, "cursor", 0) or 0)) else 1
            if text:
                return await app.run_once(text)
            return await app.run()
    except ChatClientError as exc:
        renderer.error(str(exc))
        return 1


__all__ = [
    "ChatClientError",
    "ChatOptions",
    "ChatTransport",
    "InteractiveChat",
    "JsonRenderer",
    "NdjsonDecoder",
    "RichRenderer",
    "StreamResult",
    "run_chat",
]
