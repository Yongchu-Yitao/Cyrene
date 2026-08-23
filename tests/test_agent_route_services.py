from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cyrene.agent.session_services import (
    SessionApplicationService,
    SessionRepository,
    SessionServiceError,
)
from cyrene.workbench.browser_live_service import BrowserLiveController


class FakeSessionPresentation:
    def __init__(self, archives: list[dict[str, Any]] | None = None) -> None:
        self._archives = archives or []
        self.written: tuple[Path, str, list[dict[str, Any]]] | None = None

    def sessions(self) -> list[dict[str, Any]]:
        return [{"id": "run_live"}]

    def archives(self, skip_ids: set[str]) -> list[dict[str, Any]]:
        return [item for item in self._archives if item.get("archiveKey") not in skip_ids]

    def parse_archive(self, content: str) -> list[dict[str, Any]]:
        return json.loads(content)

    def write_archive(self, path: Path, date: str, sections: list[dict[str, Any]]) -> None:
        self.written = (path, date, sections)


@pytest.mark.asyncio
async def test_session_export_reads_typed_live_state_without_route_file_io(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "session_title": "A / B",
        "messages": [
            {"role": "user", "content": "Question", "created_at": "10:00"},
            {"role": "tool", "content": "hidden"},
            {"role": "assistant", "content": "Answer", "created_at": "10:01"},
        ],
    }), encoding="utf-8")
    presentation = FakeSessionPresentation()
    repository = SessionRepository(
        state_file=state_file,
        conversations_dir=tmp_path,
        presentation=presentation,
    )
    service = SessionApplicationService("db", repository=repository, presentation=presentation)

    exported = await service.export_session("run_live", "json")
    payload = json.loads(exported.content)

    assert exported.filename == "A _ B.json"
    assert payload["message_count"] == 2
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]


def test_session_repository_rejects_malformed_state_instead_of_empty_fallback(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("not-json", encoding="utf-8")
    repository = SessionRepository(
        state_file=state_file,
        conversations_dir=tmp_path,
        presentation=FakeSessionPresentation(),
    )

    with pytest.raises(SessionServiceError, match="failed to read current session"):
        repository.state()


def test_archive_context_marks_copied_messages_and_preserves_presentation(tmp_path: Path) -> None:
    source_message = {"id": "m1", "role": "user"}
    presentation = FakeSessionPresentation([{
        "id": "archive_2026-01-01_one",
        "archiveKey": "2026-01-01:one",
        "title": "One",
        "chat": {"messages": [source_message]},
    }])
    service = SessionApplicationService(
        "db",
        repository=SessionRepository(
            state_file=tmp_path / "missing.json",
            conversations_dir=tmp_path,
            presentation=presentation,
        ),
        presentation=presentation,
    )

    result = service.archive_context()

    assert result["messages"] == [{"id": "m1", "role": "user", "isArchivedContext": True}]
    assert "isArchivedContext" not in source_message


class FakeBrowserSession:
    def __init__(self) -> None:
        self.mouse: dict[str, Any] | None = None

    async def start_screencast(self, queue): pass
    async def stop_screencast(self, queue): pass
    def set_user_control(self, on: bool): pass
    async def dispatch_mouse(self, **kwargs: Any): self.mouse = kwargs
    async def dispatch_key(self, **kwargs: Any): pass
    async def insert_text(self, text: str): pass
    async def current_url(self): return "https://example.test"
    async def current_page_metadata(self): return {"url": "https://example.test", "title": "Example"}
    async def open_user_window(self, url: str = ""): pass
    async def close_user_window(self, url: str = ""): pass


@pytest.mark.asyncio
async def test_browser_live_controller_dispatches_and_records_public_page_metadata(monkeypatch) -> None:
    recorded: list[dict[str, Any]] = []

    async def record_browser_user_event(**kwargs: Any) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(
        "cyrene.workbench.browser_live_service.behavior_learning.record_browser_user_event",
        record_browser_user_event,
    )
    session = FakeBrowserSession()
    controller = BrowserLiveController(session)
    await controller.handle({"type": "context", "sessionId": "chat_1", "roundId": "round_1"})
    await controller.handle({
        "type": "mouse", "event": "mouseReleased", "x": 10, "y": 20,
        "button": "left", "clickCount": 1,
    })

    assert session.mouse == {
        "type": "mouseReleased", "x": 10.0, "y": 20.0, "button": "left",
        "click_count": 1, "delta_x": 0.0, "delta_y": 0.0, "modifiers": 0,
    }
    assert recorded[0]["session_id"] == "chat_1"
    assert recorded[0]["browser_title"] == "Example"
