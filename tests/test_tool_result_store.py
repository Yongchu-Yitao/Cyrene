from __future__ import annotations

import json
import os
import time

import pytest

from cyrene.plugins.builtin.cyrene_content import tool_result_store as result_store


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


def test_tool_batch_shares_one_context_fraction(isolated_result_store):
    small = "short evidence"
    large_a = "甲" * 4_000
    large_b = "乙" * 4_000
    token_limit = result_store.tool_result_token_limit(
        context_limit_tokens=100_000,
    )

    projected = result_store.project_tool_result_batch_for_model(
        [
            (small, "WebSearch", "search-1"),
            (large_a, "WebFetch", "fetch-1"),
            (large_b, "WebFetch", "fetch-2"),
        ],
        session_id="session-a",
        context_limit_tokens=100_000,
    )

    assert projected[0].content == small
    assert projected[0].truncated is False
    assert projected[1].truncated is True
    assert projected[2].truncated is True
    assert sum(
        result_store._token_count(item.content) for item in projected
    ) <= token_limit
    large_projection_tokens = [
        result_store._token_count(item.content) for item in projected[1:]
    ]
    assert abs(large_projection_tokens[0] - large_projection_tokens[1]) <= 1


def test_main_and_subagent_batches_have_independent_limits(isolated_result_store):
    raw = "证" * 30_000

    main_batch = result_store.project_tool_result_batch_for_model(
        [(raw, "WebFetch", "main-fetch")],
        session_id="session-a",
        context_limit_tokens=1_000_000,
    )
    subagent_batch = result_store.project_tool_result_batch_for_model(
        [(raw, "WebFetch", "subagent-fetch")],
        session_id="session-a",
        context_limit_tokens=1_000_000,
    )

    main_tokens = sum(result_store._token_count(item.content) for item in main_batch)
    subagent_tokens = sum(
        result_store._token_count(item.content) for item in subagent_batch
    )
    assert main_tokens <= 20_000
    assert subagent_tokens <= 20_000
    assert main_tokens + subagent_tokens > 20_000


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
