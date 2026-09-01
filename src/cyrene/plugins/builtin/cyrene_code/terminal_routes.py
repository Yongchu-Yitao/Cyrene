"""Project-scoped proxies for the independent Cyrene Terminal Daemon."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cyrene.localization import localized
from .terminal.client import (
    TerminalNotFoundError,
    TerminalRequestError,
    get_terminal_daemon_client,
)

logger = logging.getLogger(__name__)


class TerminalCreateRequest(BaseModel):
    projectId: str = Field(min_length=1)
    title: str = ""
    cwd: str = ""
    cols: int = Field(default=100, ge=20, le=400)
    rows: int = Field(default=30, ge=5, le=200)
    sshTarget: str = Field(default="", max_length=255)
    remoteCwd: str = Field(default="", max_length=4096)
    tmuxSession: str = Field(default="", max_length=128)


class TerminalRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class TerminalLayoutRequest(BaseModel):
    projectId: str = Field(min_length=1)
    order: list[str] = Field(default_factory=list)
    pinned: list[str] = Field(default_factory=list)


class TerminalActivateRequest(BaseModel):
    projectId: str = Field(min_length=1)
    terminalId: str | None = None


class TerminalAgentEventRequest(BaseModel):
    agentId: str = Field(min_length=1, max_length=60)
    event: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict)


def _http_error(exc: Exception) -> HTTPException:
    logger.warning("Terminal request failed", exc_info=(type(exc), exc, exc.__traceback__))
    if isinstance(exc, TerminalNotFoundError):
        return HTTPException(
            status_code=404,
            detail=localized("Terminal not found.", "未找到终端。"),
        )
    if isinstance(exc, (ValueError, TerminalRequestError)):
        return HTTPException(
            status_code=400,
            detail=localized("Invalid terminal request.", "终端请求无效。"),
        )
    return HTTPException(
        status_code=503,
        detail=localized(
            "Terminal service is unavailable.",
            "终端服务不可用。",
        ),
    )


async def _terminal_history_export(client, terminal_id: str) -> StreamingResponse:
    try:
        first = await client.scrollback(
            terminal_id, cursor=0, max_bytes=512 * 1024,
        )
    except Exception as exc:
        raise _http_error(exc) from exc

    async def chunks():
        page = first
        target = int(first.get("nextSeq") or 0)
        while True:
            data = base64.b64decode(str(page.get("data") or ""))
            if data:
                yield data
            end = int(page.get("endSeq") or 0)
            if end >= target:
                break
            page = await client.scrollback(
                terminal_id,
                cursor=end,
                max_bytes=min(512 * 1024, target - end),
            )

    headers = {
        "Content-Disposition": f'attachment; filename="{terminal_id}.ansi"',
        "X-Terminal-Oldest-Seq": str(first.get("oldestSeq") or 0),
        "X-Terminal-Next-Seq": str(first.get("nextSeq") or 0),
    }
    return StreamingResponse(
        chunks(), media_type="application/octet-stream", headers=headers,
    )


def _register_terminal_history_routes(router: APIRouter, client) -> None:
    @router.get("/api/terminals/history/search")
    async def search_terminal_history(
        projectId: str, q: str, terminalId: str = "", limit: int = 100,
    ):
        try:
            result = await client.search_history(
                projectId,
                q,
                terminal_id=terminalId,
                limit=max(1, min(int(limit or 100), 500)),
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"matches": result.get("matches", [])}

    @router.get("/api/terminals/{terminal_id}/input-history")
    async def terminal_input_history(terminal_id: str, limit: int = 200):
        try:
            result = await client.input_history(
                terminal_id, limit=max(1, min(int(limit or 200), 1000))
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"events": result.get("events", [])}

    @router.get("/api/terminals/{terminal_id}/commands")
    async def terminal_commands(terminal_id: str):
        try:
            result = await client.commands(terminal_id)
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"commands": result.get("commands", [])}

    @router.get("/api/terminals/{terminal_id}/commands/{command_id}/output")
    async def terminal_command_output(terminal_id: str, command_id: str):
        try:
            return await client.command_output(terminal_id, command_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/api/terminals/{terminal_id}/history/export")
    async def export_terminal_history(terminal_id: str):
        return await _terminal_history_export(client, terminal_id)


def register_terminal_routes(router: APIRouter) -> None:
    client = get_terminal_daemon_client()
    _register_terminal_history_routes(router, client)

    @router.get("/api/terminals")
    async def list_terminals(projectId: str = "", ownerChatId: str | None = None):
        try:
            result = await client.list(projectId, owner_chat_id=ownerChatId)
        except Exception as exc:
            raise _http_error(exc) from exc
        return {
            "terminals": result.get("terminals", []),
            "activeTerminalId": result.get("activeTerminalId"),
        }

    @router.post("/api/terminals", status_code=201)
    async def create_terminal(payload: TerminalCreateRequest):
        try:
            result = await client.create(
                payload.projectId, title=payload.title, cwd=payload.cwd,
                cols=payload.cols, rows=payload.rows,
                ssh_target=payload.sshTarget,
                remote_cwd=payload.remoteCwd,
                tmux_session=payload.tmuxSession,
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"terminal": result["terminal"]}

    @router.patch("/api/terminals/{terminal_id}")
    async def rename_terminal(terminal_id: str, payload: TerminalRenameRequest):
        try:
            result = await client.rename(terminal_id, payload.title)
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"terminal": result["terminal"]}

    @router.delete("/api/terminals/{terminal_id}")
    async def delete_terminal(terminal_id: str):
        try:
            return await client.remove(terminal_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/api/terminals/{terminal_id}/restart")
    async def restart_terminal(terminal_id: str):
        try:
            result = await client.restart(terminal_id)
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"terminal": result["terminal"]}

    @router.post("/api/terminals/{terminal_id}/read")
    async def mark_terminal_read(terminal_id: str):
        try:
            result = await client.mark_read(terminal_id)
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"terminal": result["terminal"]}

    @router.post("/api/terminals/{terminal_id}/agent-events")
    async def report_terminal_agent_event(
        terminal_id: str, payload: TerminalAgentEventRequest,
    ):
        try:
            result = await client.agent_event(
                terminal_id,
                agent_id=payload.agentId,
                event=payload.event,
                payload=payload.payload,
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"terminal": result["terminal"]}

    @router.get("/api/terminals/{terminal_id}/screen")
    async def terminal_screen(terminal_id: str):
        try:
            return await client.screen(terminal_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.put("/api/terminals/layout")
    async def update_terminal_layout(payload: TerminalLayoutRequest):
        try:
            result = await client.update_layout(
                payload.projectId, payload.order, payload.pinned
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"terminals": result.get("terminals", [])}

    @router.put("/api/terminals/active")
    async def activate_terminal(payload: TerminalActivateRequest):
        try:
            return await client.activate(payload.projectId, payload.terminalId)
        except Exception as exc:
            raise _http_error(exc) from exc

    _register_terminal_socket_route(router, client)


__all__ = ["register_terminal_routes"]


def _register_terminal_socket_route(router: APIRouter, client: Any) -> None:
    @router.websocket("/ws/terminals/{terminal_id}")
    async def terminal_socket(websocket: WebSocket, terminal_id: str):
        await websocket.accept()
        cursor = max(0, int(websocket.query_params.get("cursor", "0") or 0))
        try:
            connection, first = await client.connect_terminal(terminal_id, cursor)
        except TerminalNotFoundError:
            await websocket.close(code=4404, reason="terminal not found")
            return
        except Exception:
            await websocket.close(code=1013, reason="terminal daemon unavailable")
            return

        await websocket.send_json(first)

        async def send_events() -> None:
            while True:
                await websocket.send_json(await connection.read())

        async def receive_commands() -> None:
            while True:
                await connection.send(await websocket.receive_json())

        sender = asyncio.create_task(send_events())
        receiver = asyncio.create_task(receive_commands())
        daemon_disconnected = False
        try:
            done, pending = await asyncio.wait(
                {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*done, *pending, return_exceptions=True)
            daemon_disconnected = any(
                isinstance(result, ConnectionError) for result in results
            )
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            receiver.cancel()
            with contextlib.suppress(Exception):
                await connection.close()
            if daemon_disconnected:
                with contextlib.suppress(Exception):
                    await websocket.close(
                        code=1013, reason="terminal daemon disconnected"
                    )
