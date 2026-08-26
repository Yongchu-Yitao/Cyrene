"""FastAPI surface for the Context Tree Agent demo."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .session import AgentTreeSession


class UserMessage(BaseModel):
    text: str


def create_demo_app(session: AgentTreeSession, html: str | Path) -> FastAPI:
    html_path = Path(html).resolve()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            session.close()

    app = FastAPI(title="Cyrene Context Tree Demo", lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(html_path)

    @app.get("/api/state")
    async def state():
        return session.snapshot()

    @app.post("/api/messages", status_code=202)
    async def send_message(message: UserMessage):
        try:
            node = session.submit(message.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"node_id": node.id}

    return app


__all__ = ["create_demo_app"]
