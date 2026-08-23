"""Application services for the historical global-chat HTTP contract."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

import httpx

from cyrene import agent
from cyrene.agent import state as agent_state
from cyrene.agent.commands import DEEP_REFLECT_COMMAND_ID, parse_deep_reflect_command
from cyrene.agent.context import AWAITING_USER_SENTINEL, bind_run_context
from cyrene import config as cyrene_config
from cyrene.config import DB_PATH
from cyrene.model_runtime.errors import format_httpx_error
from cyrene.observability import debug
from cyrene.runtime import attachments as attachment_service
from cyrene.runtime.attachments import (
    EXPORTS_DIR,
    UPLOADS_DIR,
    attachment_kind_from_meta,
    build_public_attachment_payload,
    run_vision_chat,
    safe_attachment_filename,
)
from cyrene.runtime.memory.conversations import archive_exchange
from cyrene.workbench import generation_gateway, presentation_runtime
from cyrene.workbench.chat_service import (
    get_chat_run_manager,
    settle_chat_running_status,
)
from cyrene.workbench.subagent_messaging_service import (
    AgentMentionCommand,
    SubagentMessagingService,
)


logger = logging.getLogger(__name__)


class UploadSource(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class GlobalChatTurnCommand:
    message: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    guide_round_id: str = ""
    client_request_id: str = ""
    wants_stream: bool = False
    lang: str = ""
    command: str = ""
    permission_mode: str = "default"
    mentions: list[str] = field(default_factory=list)
    retry: bool = False
    retry_request_id: str = ""

    @classmethod
    def from_payload(cls, body: dict[str, Any]) -> GlobalChatTurnCommand:
        attachments = body.get("attachments")
        mentions = body.get("mentions")
        retry = bool(body.get("retry"))
        return cls(
            message=str(body.get("message") or "").strip(),
            attachments=list(attachments) if isinstance(attachments, list) else [],
            guide_round_id=(
                "" if retry else str(body.get("guide_round_id") or "").strip()
            ),
            client_request_id=str(body.get("client_request_id") or "").strip(),
            wants_stream=bool(body.get("stream")),
            lang=str(body.get("lang") or "").strip(),
            command=str(body.get("command") or "").strip(),
            permission_mode=str(body.get("mode") or "default").strip().lower(),
            mentions=[str(item) for item in mentions] if isinstance(mentions, list) else [],
            retry=retry,
            retry_request_id=str(body.get("retry_request_id") or "").strip(),
        )


@dataclass(frozen=True)
class GlobalChatAnswerCommand:
    question_id: str
    answer: str
    client_request_id: str = ""
    wants_stream: bool = False

    @classmethod
    def from_payload(cls, body: dict[str, Any]) -> GlobalChatAnswerCommand:
        selected = str(body.get("selected_option") or "").strip()
        return cls(
            question_id=str(body.get("question_id") or "").strip(),
            answer=str(body.get("answer") or "").strip() or selected,
            client_request_id=str(body.get("client_request_id") or "").strip(),
            wants_stream=bool(body.get("stream")),
        )


@dataclass(frozen=True)
class GlobalChatResult:
    payload: dict[str, Any] | None = None
    events: AsyncIterator[dict[str, Any]] | None = None
    file_path: Path | None = None


class GlobalChatApplicationError(Exception):
    def __init__(self, status_code: int, payload: dict[str, Any]):
        super().__init__(str(payload.get("error") or "global chat request failed"))
        self.status_code = status_code
        self.payload = payload


class GlobalChatApplicationService:
    """Own global-chat turn, upload, persistence, stream, and control flows."""

    def __init__(
        self,
        db_path: str,
        *,
        bot: Any,
        subagents: SubagentMessagingService,
        reset_agent_lottery: Callable[[], None],
        chat_id: int = -1,
        uploads_dir: Path = UPLOADS_DIR,
        exports_dir: Path = EXPORTS_DIR,
    ):
        self.db_path = str(db_path)
        self.bot = bot
        self.chat_id = chat_id
        self.subagents = subagents
        self.reset_agent_lottery = reset_agent_lottery
        self.uploads_dir = Path(uploads_dir)
        self.exports_dir = Path(exports_dir)

    async def upload(self, files: list[UploadSource]) -> dict[str, Any]:
        if not files:
            raise GlobalChatApplicationError(400, {"error": "no files uploaded"})
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        uploaded = [await self._store_upload(source) for source in files]
        return {"files": uploaded}

    def resolve_upload(self, upload_id: str) -> Path:
        target = (self.uploads_dir / self.safe_upload_name(upload_id)).resolve()
        return _resolve_managed_file(
            target, self.uploads_dir, "upload", "invalid upload path"
        )

    def resolve_export(self, export_id: str) -> Path:
        if _invalid_path_component(export_id):
            raise GlobalChatApplicationError(400, {"error": "invalid export path"})
        target = (self.exports_dir / export_id).resolve()
        return _resolve_managed_file(
            target, self.exports_dir, "export", "invalid export path"
        )

    async def submit(self, request: GlobalChatTurnCommand) -> GlobalChatResult:
        agent_state._conversation_source.set("webui")
        command = self._prepare_turn(request)
        await self._apply_turn_context(command)
        normalized = _normalize_attachments(command.attachments)
        if not command.message and not normalized and command.command != DEEP_REFLECT_COMMAND_ID:
            raise GlobalChatApplicationError(400, {"error": "empty message"})
        public = [build_public_attachment_payload(item) for item in normalized]
        _bind_attachment_paths(normalized)
        self.reset_agent_lottery()
        if command.mentions and command.message:
            return await self._submit_mentions(command, public)
        prompt = (command.message or "[Attachment upload]") + self.attachment_prompt_block(normalized)
        if command.guide_round_id:
            return await self._submit_guidance(command, prompt)
        return await self._run_turn(command, normalized, public, prompt)

    async def answer(self, request: GlobalChatAnswerCommand) -> GlobalChatResult:
        agent_state._conversation_source.set("webui")
        if not request.question_id:
            raise GlobalChatApplicationError(400, {"error": "missing question_id"})
        if not request.answer:
            raise GlobalChatApplicationError(400, {"error": "empty answer"})
        budget_error = await self.check_budget_gate(request.question_id)
        if budget_error:
            raise GlobalChatApplicationError(403, budget_error)
        async def factory():
            return await agent.answer_pending_question(
                request.question_id,
                request.answer,
                self.bot,
                self.chat_id,
                self.db_path,
                client_request_id=request.client_request_id,
            )
        if request.wants_stream:
            return GlobalChatResult(events=self.stream_agent_events(factory, request.answer))
        response = await factory()
        return GlobalChatResult(payload=await self._final_payload(request.answer, response))

    def history(self) -> dict[str, Any]:
        return {"messages": presentation_runtime._load_messages()}

    def state(self) -> dict[str, Any]:
        path = cyrene_config.STATE_FILE
        if not path.exists():
            return {"messages": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"messages": []}
        messages = payload.get("messages", [])
        return {"messages": messages if isinstance(messages, list) else []}

    async def interrupt(self, session_id: str = "") -> dict[str, Any]:
        manager = get_chat_run_manager()
        workbench_run = manager.get(session_id) if session_id else None
        interrupted = agent.interrupt_active_run(session_id=session_id)
        if not session_id:
            return {"ok": True, "interrupted": interrupted}
        interrupted = manager.interrupt(session_id) or interrupted
        if workbench_run is not None and not workbench_run.done.is_set():
            try:
                await asyncio.wait_for(
                    asyncio.shield(workbench_run.done.wait()), timeout=8.0
                )
            except asyncio.TimeoutError as exc:
                raise GlobalChatApplicationError(
                    504,
                    {
                        "ok": False,
                        "interrupted": False,
                        "error": "chat interruption is still settling",
                        "code": "chat_interrupt_timeout",
                    },
                ) from exc
        await asyncio.to_thread(settle_chat_running_status, session_id)
        return {"ok": True, "interrupted": interrupted}

    async def clear(self) -> dict[str, Any]:
        await agent.clear_session_id()
        return {"ok": True}

    def list_subagents(self, session_id: str = "") -> dict[str, Any]:
        return {"subagents": self.subagents.list_subagents(session_id)}

    @staticmethod
    def live_rounds() -> dict[str, Any]:
        return {"rounds": agent.get_live_rounds()}

    async def check_budget_gate(self, session_id: str) -> dict[str, Any] | None:
        from cyrene.agent.budget import check_budget_and_block
        from cyrene.runtime.settings_store import get_all as get_all_settings

        settings = get_all_settings()
        result = await check_budget_and_block(
            self.db_path or str(DB_PATH),
            monthly=float(settings.get("budget_monthly") or 0),
            enabled=bool(settings.get("budget_enabled", False)),
        )
        if not result:
            return None
        if result.get("warning"):
            await debug.publish_event(
                {"type": "budget_warning", "code": result["code"], "message": result["message"]},
                session_id=session_id,
            )
            logger.warning("Budget warning for %s: %s", session_id, result["code"])
            return None
        logger.warning("Budget block for %s: %s", session_id, result["code"])
        return {"error": result["message"], "code": result["code"]}

    async def stream_agent_events(
        self,
        run_coro_factory: Callable[[], Awaitable[str]],
        user_message: str,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        task = _start_stream_task(run_coro_factory, queue)
        saw_reply_events = False
        run_failed = False
        await debug.publish_event({"type": "session_update", "status": "running"})
        try:
            async for event in _drain_events(task, queue):
                saw_reply_events |= str(event.get("type") or "").startswith("reply_")
                yield event
            try:
                response = await task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                run_failed = True
                async for event in _stream_failure_events(exc):
                    yield event
                return
            if response == AWAITING_USER_SENTINEL:
                yield {
                    "type": "awaiting_user",
                    "awaiting_user": True,
                    "pending_question": agent.get_pending_question(),
                }
                return
            if not saw_reply_events:
                async for event in _synthetic_reply_events(response):
                    yield event
            await self._archive(user_message, response)
            await debug.publish_event({"type": "session_update", "status": "done"})
        finally:
            if not task.done():
                task.cancel()
            if not run_failed:
                await debug.publish_event({"type": "session_update", "status": "done"})

    @staticmethod
    def safe_upload_name(filename: str) -> str:
        return safe_attachment_filename(filename, fallback_stem="upload")

    @staticmethod
    def attachment_prompt_block(items: list[dict[str, Any]]) -> str:
        if not items:
            return ""
        lines = [
            "",
            "[Uploaded attachments]",
            "The user uploaded the following files into the local workspace-accessible runtime data directory.",
            "Before answering anything about these files, you MUST inspect the relevant attachment with AnalyzeAttachment.",
            "Do not answer from the filename, extension, or metadata alone.",
            "After AnalyzeAttachment returns extracted content, use that extracted content to answer the user.",
            "If AnalyzeAttachment reports that an uploaded file is missing or unavailable, stop attachment analysis and ask the user to upload it again.",
            "Do NOT use Glob, Grep, Bash, find, or directory scans to search for a replacement file elsewhere on the device.",
        ]
        lines.extend(
            f'- {item["name"]} ({item["content_type"]}): {item["path"]}'
            for item in items
        )
        return "\n".join(lines)

    @staticmethod
    async def chat_with_uploaded_images(
        message: str, attachments: list[dict[str, Any]]
    ) -> str:
        prompt = str(message or "").strip() or (
            "Describe the uploaded image in detail and extract any visible text."
        )
        content = _image_content(prompt, attachments)
        try:
            response = await generation_gateway.call_llm(
                [{"role": "user", "content": content}], tools=None, max_tokens=None
            )
        except httpx.HTTPError as exc:
            if _is_unsupported_image_error(exc):
                result = await run_vision_chat(content, content_prompt=prompt)
                return str(result.get("vision_text") or "").strip() or (
                    "The vision fallback model returned no usable image analysis."
                )
            raise
        return _response_text(response)

    async def _store_upload(self, source: UploadSource) -> dict[str, Any]:
        safe_name = self.safe_upload_name(source.filename or "")
        target = self.uploads_dir / f"{uuid.uuid4().hex}_{safe_name}"
        file_size = 0
        try:
            with target.open("wb") as destination:
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    file_size += len(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return _uploaded_file_payload(source, target, safe_name, file_size)

    def _prepare_turn(self, request: GlobalChatTurnCommand) -> GlobalChatTurnCommand:
        command = request.command
        message = request.message
        if parse_deep_reflect_command(message).get("matched"):
            command = DEEP_REFLECT_COMMAND_ID
        if command == DEEP_REFLECT_COMMAND_ID and not message:
            message = "/deep-reflect"
        mode = request.permission_mode
        if mode not in agent_state.PERMISSION_MODES:
            mode = "default"
        return replace(
            request,
            message=message,
            command=command,
            permission_mode=mode,
        )

    async def _apply_turn_context(self, command: GlobalChatTurnCommand) -> None:
        if command.lang in {"en", "zh"}:
            from cyrene.runtime.settings_store import get, set_

            if str(get("app_language", "") or "") != command.lang:
                set_("app_language", command.lang)
        if command.retry and command.retry_request_id:
            await agent._remove_messages_by_request_id(command.retry_request_id)

    async def _submit_mentions(
        self,
        command: GlobalChatTurnCommand,
        public_attachments: list[dict[str, Any]],
    ) -> GlobalChatResult:
        payload = await self.subagents.send_mentions(
            AgentMentionCommand(
                command.message,
                command.mentions,
                public_attachments,
                command.client_request_id,
            )
        )
        events = _single_event({"type": "reply_done", **payload}) if command.wants_stream else None
        return GlobalChatResult(payload=None if events else payload, events=events)

    async def _submit_guidance(
        self, command: GlobalChatTurnCommand, prompt: str
    ) -> GlobalChatResult:
        try:
            item = await agent.queue_round_guidance(
                command.guide_round_id,
                prompt,
                self.bot,
                self.chat_id,
                self.db_path,
                client_request_id=command.client_request_id,
            )
        except ValueError as exc:
            raise GlobalChatApplicationError(400, {"error": str(exc)}) from exc
        payload = {
            "response": f"Sent to the main-agent inbox for {command.guide_round_id}. It will run after the current main-agent output finishes.",
            "queued": True,
            "guide_round_id": command.guide_round_id,
            "guide_request_id": item.get("id", ""),
        }
        events = _single_event({"type": "queued", **payload}) if command.wants_stream else None
        return GlobalChatResult(payload=None if events else payload, events=events)

    async def _run_turn(
        self,
        command: GlobalChatTurnCommand,
        attachments: list[dict[str, Any]],
        public_attachments: list[dict[str, Any]],
        prompt: str,
    ) -> GlobalChatResult:
        all_images = bool(attachments) and all(item.get("kind") == "image" for item in attachments)
        if all_images and command.command != DEEP_REFLECT_COMMAND_ID:
            async def factory():
                return await self._run_direct_image_chat(
                    command, attachments, public_attachments
                )
        else:
            async def factory():
                return await agent.run_agent(
                    prompt,
                    self.bot,
                    self.chat_id,
                    self.db_path,
                    client_request_id=command.client_request_id,
                    lang=command.lang,
                    command=command.command,
                    public_user_message=command.message,
                    public_attachments=public_attachments,
                    permission_mode=command.permission_mode,
                )
        if command.wants_stream:
            return GlobalChatResult(events=self.stream_agent_events(factory, command.message))
        response = await factory()
        if all_images and command.command != DEEP_REFLECT_COMMAND_ID:
            return GlobalChatResult(payload={"response": response})
        return GlobalChatResult(payload=await self._final_payload(command.message, response))

    async def _run_direct_image_chat(
        self,
        command: GlobalChatTurnCommand,
        attachments: list[dict[str, Any]],
        public_attachments: list[dict[str, Any]],
    ) -> str:
        response = await self.chat_with_uploaded_images(command.message, attachments)
        await _persist_image_exchange(
            command.message,
            response,
            public_attachments,
            command.client_request_id,
        )
        await self._archive(command.message, response)
        return response

    async def _final_payload(self, user_message: str, response: str) -> dict[str, Any]:
        if response == AWAITING_USER_SENTINEL:
            return {"awaiting_user": True, "pending_question": agent.get_pending_question()}
        await self._archive(user_message, response)
        return {"response": response}

    async def _archive(self, user_message: str, response: str) -> None:
        labels = agent.get_session_labels()
        await archive_exchange(
            user_message,
            response,
            self.chat_id,
            session_title=labels.get("session_title", ""),
            round_title=labels.get("round_title", ""),
            round_id=labels.get("round_id", ""),
            archive_session_id=labels.get("archive_session_id", ""),
        )


def _start_stream_task(
    factory: Callable[[], Awaitable[str]], queue: asyncio.Queue[dict[str, Any]]
) -> asyncio.Task[str]:
    async def publish(event: dict[str, Any]) -> None:
        await queue.put(dict(event))

    binding = bind_run_context(reply_stream_writer=publish, runtime_event_writer=publish)
    try:
        return asyncio.create_task(factory())
    finally:
        binding.reset()


async def _drain_events(
    task: asyncio.Task[str], queue: asyncio.Queue[dict[str, Any]]
) -> AsyncIterator[dict[str, Any]]:
    while not task.done() or not queue.empty():
        try:
            yield await asyncio.wait_for(queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            continue


async def _stream_failure_events(exc: Exception) -> AsyncIterator[dict[str, Any]]:
    if isinstance(exc, agent.SessionRunConflictError):
        yield {
            "type": "error",
            "error": "task_run_in_progress",
            "message": "该会话已有正在执行的请求，请等待完成或先明确停止它。",
        }
        await debug.publish_event({"type": "session_update", "status": "running"})
        return
    logger.exception("Streaming chat run failed: %s", format_httpx_error(exc))
    yield {
        "type": "error",
        "error": "model_call_failed",
        "message": str(exc).strip() or exc.__class__.__name__,
    }
    await debug.publish_event({"type": "session_update", "status": "error"})


async def _synthetic_reply_events(response: str) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "reply_start"}
    for chunk in _reply_stream_chunks(response):
        yield {"type": "reply_delta", "delta": chunk}
    yield {"type": "reply_done", "response": response}


async def _single_event(event: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    yield event


def _normalize_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or "file"),
            "path": str(item.get("path") or ""),
            "content_type": str(item.get("content_type") or "application/octet-stream"),
            "size": int(item.get("size") or 0),
            "kind": str(item.get("kind") or "file"),
            **({"width": int(item["width"])} if str(item.get("width", "")).strip().isdigit() else {}),
            **({"height": int(item["height"])} if str(item.get("height", "")).strip().isdigit() else {}),
        }
        for item in items
        if str(item.get("path") or "").strip()
    ]


def _bind_attachment_paths(items: list[dict[str, Any]]) -> None:
    paths: dict[str, str] = {}
    for item in items:
        full_path = str(item.get("path") or "").strip()
        if not full_path:
            continue
        name = Path(full_path).name
        paths[name] = full_path
        parts = name.split("_", 1)
        if len(parts) == 2:
            paths[parts[1]] = full_path
    if paths:
        agent_state._attachment_paths_by_name.set(paths)


def _uploaded_file_payload(
    source: UploadSource, target: Path, safe_name: str, file_size: int
) -> dict[str, Any]:
    content_type = str(
        source.content_type
        or mimetypes.guess_type(str(target))[0]
        or "application/octet-stream"
    )
    kind = attachment_kind_from_meta(content_type, target.name)
    width, height = (
        attachment_service._image_dimensions(target)
        if kind == "image"
        else (None, None)
    )
    return {
        "id": target.name,
        "name": source.filename or safe_name,
        "path": str(target.resolve()),
        "content_type": content_type,
        "size": file_size,
        "kind": kind,
        "url": f"/api/chat/upload/{target.name}",
        **({"width": width} if isinstance(width, int) else {}),
        **({"height": height} if isinstance(height, int) else {}),
    }


def _resolve_managed_file(
    target: Path, root: Path, kind: str, invalid_error: str
) -> Path:
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise GlobalChatApplicationError(400, {"error": invalid_error})
    if not target.exists() or not target.is_file():
        raise GlobalChatApplicationError(404, {"error": f"{kind} not found"})
    return target


def _invalid_path_component(value: str) -> bool:
    return (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    )


def _image_content(
    prompt: str, attachments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for item in attachments:
        path = Path(str(item.get("path") or "")).resolve()
        mime = str(
            item.get("content_type")
            or mimetypes.guess_type(str(path))[0]
            or "image/png"
        )
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
        )
    return content


def _is_unsupported_image_error(exc: httpx.HTTPError) -> bool:
    detail = format_httpx_error(exc).lower()
    return any(
        token in detail
        for token in ("image", "vision", "multimodal", "unsupported", "invalid content")
    )


def _response_text(response: dict[str, Any]) -> str:
    raw_content = response.get("content")
    if isinstance(raw_content, str) and raw_content.strip():
        return raw_content.strip()
    parts = [
        str(item.get("text") or "")
        for item in raw_content or []
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return "".join(parts).strip() or "The model returned no usable image analysis."


async def _persist_image_exchange(
    message: str,
    response: str,
    public_attachments: list[dict[str, Any]],
    client_request_id: str,
) -> None:
    round_id = f"round_{int(time.time() * 1000)}"
    user_entry: dict[str, Any] = {
        "role": "user",
        "content": str(message or ""),
        "attachments": [dict(item) for item in public_attachments],
        "round_id": round_id,
    }
    if client_request_id:
        user_entry["client_request_id"] = client_request_id
    await agent._append_session_message(user_entry)
    await agent.append_system_message(
        response,
        message_meta={
            "system_initiated": False,
            "round_id": round_id,
            **({"client_request_id": client_request_id} if client_request_id else {}),
        },
        publish_event={
            "type": "chat_message",
            "round_id": round_id,
            "client_request_id": client_request_id,
        },
    )


def _reply_stream_chunks(text: str, target_chars: int = 36) -> list[str]:
    chunks: list[str] = []
    for block in re.split(r"(\n\n+)", str(text or "")):
        if not block:
            continue
        if block.startswith("\n"):
            chunks.append(block)
            continue
        while block:
            if len(block) <= target_chars:
                chunks.append(block)
                break
            split_at = target_chars
            for index in range(target_chars - 1, max(0, target_chars - 14) - 1, -1):
                if block[index] in "，。！？；：,.!?;: ":
                    split_at = index + 1
                    break
            chunks.append(block[:split_at])
            block = block[split_at:]
    return [chunk for chunk in chunks if chunk]


__all__ = [
    "GlobalChatAnswerCommand",
    "GlobalChatApplicationError",
    "GlobalChatApplicationService",
    "GlobalChatResult",
    "GlobalChatTurnCommand",
    "UploadSource",
]
