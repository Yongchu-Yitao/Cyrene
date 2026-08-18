from __future__ import annotations

import json
import os
import time

import pytest

from cyrene.tooling import result_store


@pytest.fixture
def isolated_result_store(tmp_path, monkeypatch):
    root = tmp_path / "tool-results"
    monkeypatch.setattr(result_store, "_RESULT_ROOT", root)
    return root


def test_small_tool_result_is_returned_exactly(isolated_result_store):
    projected = result_store.project_tool_result_for_model(
        "small result",
        tool_name="Read",
        tool_call_id="call-1",
        session_id="session-a",
        context_limit_tokens=10_000,
    )

    assert projected.content == "small result"
    assert projected.truncated is False
    assert projected.content_ref is None
    assert not isolated_result_store.exists()


def test_large_result_is_bounded_and_recoverable(isolated_result_store):
    raw = "HEAD\n" + ("middle-value\n" * 500) + "TAIL"
    token_limit = result_store.tool_result_token_limit(context_limit_tokens=10_000)

    projected = result_store.project_tool_result_for_model(
        raw,
        tool_name="Bash",
        tool_call_id="call-2",
        session_id="session-a",
        context_limit_tokens=10_000,
    )

    assert projected.truncated is True
    assert projected.content_ref
    assert result_store._token_count(projected.content) <= token_limit
    envelope = json.loads(projected.content)
    assert envelope["preview_head"].startswith("HEAD")
    assert envelope["preview_tail"].endswith("TAIL")
    assert envelope["content_ref"] == projected.content_ref

    page = json.loads(result_store.read_tool_result(
        projected.content_ref,
        offset=0,
        limit=25,
        session_id="session-a",
    ))
    assert page["content"] == raw[:25]
    assert page["next_offset"] == 25
    assert page["has_more"] is True


def test_result_reference_is_session_scoped(isolated_result_store):
    content_ref = result_store.store_tool_result("private", session_id="session-a")

    with pytest.raises(result_store.ToolResultReferenceError, match="another session"):
        result_store.read_tool_result(content_ref, session_id="session-b")


def test_result_search_returns_matches_and_cursor(isolated_result_store):
    content_ref = result_store.store_tool_result(
        "alpha\nneedle one\nbeta\nneedle two\nomega",
        session_id="session-a",
    )

    response = json.loads(result_store.read_tool_result(
        content_ref,
        query="NEEDLE",
        limit=100,
        session_id="session-a",
    ))

    assert response["matches"] == 2
    assert "needle one" in response["content"]
    assert "needle two" in response["content"]
    assert response["has_more"] is False


def test_expired_result_cannot_be_read(isolated_result_store):
    content_ref = result_store.store_tool_result("old", session_id="session-a")
    path = result_store._resolve_reference(content_ref, session_id="session-a")
    expired = time.time() - result_store._RESULT_TTL_SECONDS - 1
    os.utime(path, (expired, expired))

    with pytest.raises(result_store.ToolResultReferenceError, match="expired"):
        result_store.read_tool_result(content_ref, session_id="session-a")


def test_read_tool_result_is_available_to_main_and_subagent():
    from cyrene.tooling.wire import DIRECT_TOOL_NAMES, SUBAGENT_DIRECT_TOOL_NAMES

    assert "read_tool_result" in DIRECT_TOOL_NAMES
    assert "read_tool_result" in SUBAGENT_DIRECT_TOOL_NAMES


@pytest.mark.asyncio
async def test_read_tool_result_executes_through_wire_gateway(isolated_result_store):
    from cyrene.agent.context import bind_run_context
    from cyrene.tooling.gateway import execute_wire_tool

    with bind_run_context(session_id="session-a"):
        content_ref = result_store.store_tool_result("abcdef", session_id="session-a")
        response = await execute_wire_tool(
            "read_tool_result",
            {"content_ref": content_ref, "offset": 2, "limit": 3},
            None,
            0,
            "",
            None,
            actor="main",
        )

    assert json.loads(response)["content"] == "cde"
