"""Request/reply session registry for live Office add-ins.

The Office task pane owns the host object model.  Cyrene never tries to edit an
open presentation file behind PowerPoint's back; it sends typed requests over a
local WebSocket and waits for the add-in to finish its ``context.sync()``.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cyrene.localization import localized
from .protocol import READ_ONLY_METHODS, expected_handshake

logger = logging.getLogger(__name__)


class OfficeBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass
class OfficeSession:
    session_id: str
    websocket: Any
    host: str
    document: dict[str, Any]
    capabilities: dict[str, Any]
    agent_kit: dict[str, Any]
    compatible: bool
    revision: int = 0
    selection: dict[str, Any] = field(default_factory=dict)
    connected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_seen_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "host": self.host,
            "document": dict(self.document),
            "selection": dict(self.selection),
            "revision": self.revision,
            "capabilities": dict(self.capabilities),
            "agentKit": dict(self.agent_kit),
            "compatible": self.compatible,
            "connectedAt": self.connected_at,
            "lastSeenAt": self.last_seen_at,
        }


class OfficeBridgeService:
    """In-memory registry scoped to the running Cyrene desktop process."""

    def __init__(self) -> None:
        self._sessions: dict[str, OfficeSession] = {}
        self._registry_lock = asyncio.Lock()

    async def _publish_session_event(
        self,
        event: str,
        session: OfficeSession | None = None,
        *,
        session_id: str = "",
    ) -> None:
        from cyrene.observability import debug

        await debug.publish_event({
            "type": "office_session_update",
            "event": event,
            "sessionId": session.session_id if session is not None else session_id,
            "session": session.public() if session is not None else None,
            "sessions": self.list_sessions("powerpoint"),
        })

    def _schedule_session_event(self, event: str, session: OfficeSession) -> None:
        from cyrene.observability import debug

        debug.publish_event_sync({
            "type": "office_session_update",
            "event": event,
            "sessionId": session.session_id,
            "session": session.public(),
            "sessions": self.list_sessions("powerpoint"),
        })

    async def register(self, websocket: Any, hello: dict[str, Any]) -> OfficeSession:
        host = str(hello.get("host") or "").strip().lower()
        if host not in {"powerpoint", "word"}:
            raise OfficeBridgeError(
                "unsupported_host",
                localized(
                    "Only PowerPoint and Word hosts are accepted.",
                    "仅支持 PowerPoint 和 Word 宿主。",
                ),
            )
        document = hello.get("document") if isinstance(hello.get("document"), dict) else {}
        capabilities = hello.get("capabilities") if isinstance(hello.get("capabilities"), dict) else {}
        expected = expected_handshake()
        received = {
            "protocolVersion": hello.get("protocolVersion"),
            "kitVersion": hello.get("kitVersion"),
            "schemaHash": hello.get("schemaHash"),
            "buildHash": hello.get("buildHash"),
        }
        compatible = all(received.get(key) == value for key, value in expected.items())
        requested_id = str(hello.get("resumeSessionId") or "").strip()
        session_id = requested_id if requested_id and requested_id not in self._sessions else secrets.token_urlsafe(18)
        session = OfficeSession(
            session_id=session_id,
            websocket=websocket,
            host=host,
            document=dict(document),
            capabilities=dict(capabilities),
            agent_kit={"received": received, "expected": expected},
            compatible=compatible,
            revision=max(0, int(hello.get("revision") or 0)),
        )
        async with self._registry_lock:
            self._sessions[session_id] = session
        await self._publish_session_event("connected", session)
        return session

    async def unregister(self, session_id: str, websocket: Any) -> None:
        async with self._registry_lock:
            session = self._sessions.get(session_id)
            if session is None or session.websocket is not websocket:
                return
            self._sessions.pop(session_id, None)
        for future in list(session.pending.values()):
            if not future.done():
                future.set_exception(OfficeBridgeError(
                    "session_disconnected",
                    localized("The Office add-in disconnected.", "Office 加载项已断开连接。"),
                ))
        session.pending.clear()
        await self._publish_session_event("disconnected", session_id=session_id)

    def receive(self, session: OfficeSession, payload: dict[str, Any]) -> None:
        session.last_seen_at = datetime.now(UTC).isoformat()
        if payload.get("type") == "event":
            session.revision = max(session.revision, int(payload.get("revision") or 0))
            selection = payload.get("selection")
            if isinstance(selection, dict):
                session.selection = dict(selection)
            document = payload.get("document")
            if isinstance(document, dict):
                session.document = dict(document)
            self._schedule_session_event(str(payload.get("event") or "changed"), session)
            return
        if payload.get("type") != "response":
            return
        request_id = str(payload.get("id") or "")
        future = session.pending.pop(request_id, None)
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("revision"), int):
            revision = int(result["revision"])
            if revision > session.revision:
                session.revision = revision
                self._schedule_session_event("revision_changed", session)
        if future is not None and not future.done():
            future.set_result(payload)

    def list_sessions(self, host: str | None = None) -> list[dict[str, Any]]:
        selected = str(host or "").strip().lower()
        return [
            session.public()
            for session in self._sessions.values()
            if not selected or session.host == selected
        ]

    def get_session(self, session_id: str | None, *, host: str = "powerpoint") -> OfficeSession:
        requested = str(session_id or "").strip()
        if requested:
            session = self._sessions.get(requested)
            if session is None:
                raise OfficeBridgeError(
                    "session_not_found",
                    localized(
                        f"Office session {requested!r} is not connected.",
                        f"Office 会话 {requested!r} 未连接。",
                    ),
                )
            if host and session.host != host:
                raise OfficeBridgeError(
                    "wrong_host",
                    localized(
                        f"Session {requested!r} is connected to {session.host}, not {host}.",
                        f"会话 {requested!r} 连接到 {session.host}，而不是 {host}。",
                    ),
                )
            return session
        matches = [session for session in self._sessions.values() if not host or session.host == host]
        if not matches:
            raise OfficeBridgeError(
                "office_not_connected",
                localized(
                    "No live PowerPoint session is connected. Open the Cyrene add-in task pane in PowerPoint.",
                    "当前没有已连接的 PowerPoint 会话，请在 PowerPoint 中打开 Cyrene 加载项任务窗格。",
                ),
            )
        if len(matches) > 1:
            raise OfficeBridgeError(
                "session_required",
                localized(
                    "More than one PowerPoint presentation is connected; pass sessionId explicitly.",
                    "当前连接了多个 PowerPoint 演示文稿，请明确传入 sessionId。",
                ),
                details={"sessions": [item.public() for item in matches]},
            )
        return matches[0]

    async def call(
        self,
        session_id: str | None,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        host: str = "powerpoint",
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        session = self.get_session(session_id, host=host)
        if not session.compatible and method not in READ_ONLY_METHODS:
            raise OfficeBridgeError(
                "addin_outdated",
                localized(
                    "The connected PowerPoint add-in is outdated or incompatible. Reopen its task pane; if it remains incompatible, use Reinstall in Settings → Service integrations.",
                    "已连接的 PowerPoint 加载项版本过旧或不兼容。请重新打开任务窗格；若仍不兼容，请在“设置 → 服务集成”中重新安装。",
                ),
                details=session.agent_kit,
            )
        request_id = secrets.token_urlsafe(14)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        async with session.lock:
            session.pending[request_id] = future
            try:
                await session.websocket.send_json({
                    "type": "request",
                    "id": request_id,
                    "method": method,
                    "params": dict(params or {}),
                })
            except Exception as exc:
                session.pending.pop(request_id, None)
                logger.warning("Could not send Office request %s", method, exc_info=True)
                raise OfficeBridgeError(
                    "send_failed",
                    localized(
                        "Could not send the request to PowerPoint.",
                        "无法向 PowerPoint 发送请求。",
                    ),
                ) from exc
            try:
                payload = await asyncio.wait_for(future, timeout=timeout)
            except TimeoutError as exc:
                session.pending.pop(request_id, None)
                raise OfficeBridgeError(
                    "office_timeout",
                    localized(
                        f"PowerPoint did not finish {method} within {timeout:g} seconds.",
                        f"PowerPoint 未能在 {timeout:g} 秒内完成 {method}。",
                    ),
                ) from exc
            except asyncio.CancelledError:
                session.pending.pop(request_id, None)
                raise

        if payload.get("ok") is not True:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            logger.info(
                "PowerPoint request %s failed [%s]: %s",
                method,
                error.get("code"),
                error.get("message"),
            )
            raise OfficeBridgeError(
                str(error.get("code") or "office_error"),
                localized(
                    f"PowerPoint could not complete {method}.",
                    f"PowerPoint 无法完成 {method}。",
                ),
                details=error.get("details"),
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            result = {"value": result}
        revision = result.get("revision")
        if isinstance(revision, int):
            session.revision = max(session.revision, revision)
        if method == "ppt.get_context":
            result.setdefault("agentKit", {
                **session.agent_kit,
                "compatible": session.compatible,
            })
        session.last_seen_at = datetime.now(UTC).isoformat()
        return result

    async def close(self) -> None:
        sessions = list(self._sessions.values())
        for session in sessions:
            try:
                await session.websocket.close(code=1001, reason="Cyrene is shutting down")
            except Exception:
                pass
            await self.unregister(session.session_id, session.websocket)


_SERVICE = OfficeBridgeService()


def get_office_bridge() -> OfficeBridgeService:
    return _SERVICE


__all__ = ["OfficeBridgeError", "OfficeBridgeService", "OfficeSession", "get_office_bridge"]
