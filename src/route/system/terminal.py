"""Project-scoped proxies for the independent Cyrene Terminal Daemon."""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from cyrene.terminal.client import (
    TerminalNotFoundError,
    TerminalRequestError,
    get_terminal_daemon_client,
)


class TerminalCreateRequest(BaseModel):
    projectId: str = Field(min_length=1)
    title: str = ""
    cwd: str = ""
    cols: int = Field(default=100, ge=20, le=400)
    rows: int = Field(default=30, ge=5, le=200)


class TerminalRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class TerminalLayoutRequest(BaseModel):
    projectId: str = Field(min_length=1)
    order: list[str] = Field(default_factory=list)
    pinned: list[str] = Field(default_factory=list)


class TerminalActivateRequest(BaseModel):
    projectId: str = Field(min_length=1)
    terminalId: str | None = None


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TerminalNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (ValueError, TerminalRequestError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=503, detail=str(exc))


def register_terminal_routes(router: APIRouter) -> None:
    client = get_terminal_daemon_client()

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

    @router.get("/api/terminals/{terminal_id}/screen")
    async def terminal_screen(terminal_id: str):
        try:
            return await client.screen(terminal_id)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/api/terminals/{terminal_id}/input-history")
    async def terminal_input_history(terminal_id: str, limit: int = 200):
        try:
            result = await client.input_history(
                terminal_id, limit=max(1, min(int(limit or 200), 1000))
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"events": result.get("events", [])}

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


__all__ = ["register_terminal_routes"]
