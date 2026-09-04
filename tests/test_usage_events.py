from __future__ import annotations

import pytest

from cyrene.observability import debug
from cyrene.workbench.application.usage_events import publish_usage_event


@pytest.mark.asyncio
async def test_tool_started_accounting_projection_is_marked_analytics_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[dict[str, object], str]] = []

    async def capture(event: dict[str, object], session_id: str = "") -> None:
        published.append((event, session_id))

    monkeypatch.setattr(debug, "publish_event", capture)

    await publish_usage_event(
        {
            "type": "tool.started",
            "runId": "run-1",
            "timestamp": "2026-09-05T00:00:00+00:00",
            "payload": {"toolCallId": "call-1", "name": "Bash"},
        },
        session_id="chat-1",
    )

    assert published == [
        (
            {
                "type": "tool_call",
                "analytics_only": True,
                "timestamp": "2026-09-05T00:00:00+00:00",
                "round_id": "run-1",
                "session_id": "chat-1",
                "caller": "main_agent",
                "tool": "Bash",
            },
            "chat-1",
        )
    ]
