from __future__ import annotations

import copy
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


def _large_powerpoint_arguments(slide_count: int = 2) -> str:
    return json.dumps({
        "operation": "invoke",
        "capability_id": "ppt.create_slides",
        "arguments": {
            "mode": "live_office",
            "expectedRevision": 7,
            "slideSpecs": [
                {
                    "layout": "blank",
                    "elements": [
                        {
                            "ref": f"shape-{slide}-{element}",
                            "type": "shape",
                            "box": [element * 10, element * 8, 180, 48],
                            "style": {"fillColor": "#C84B31"},
                        }
                        for element in range(18)
                    ],
                }
                for slide in range(slide_count)
            ],
        },
    }, ensure_ascii=False)


def _powerpoint_result(revision: int = 19) -> str:
    return json.dumps({
        "status": "applied",
        "capability_id": "ppt.create_slides",
        "result": {
            "status": "applied",
            "operation": "ppt.create_slides",
            "mode": "live_office",
            "revision": revision,
            "created": [
                {
                    "index": 0,
                    "slideId": "slide-1",
                    "stages": [
                        {"name": f"element-{index}", "status": "applied", "created": [{"id": str(index)}]}
                        for index in range(50)
                    ],
                }
            ],
            "changed": [],
            "deleted": [],
            "warnings": [],
        },
    }, ensure_ascii=False)


def test_powerpoint_mutation_result_is_always_externalized(isolated_result_store):
    raw = _powerpoint_result()
    projected = result_store.project_tool_result_for_model(
        raw,
        tool_name="PowerPointToolSearch",
        tool_call_id="ppt-1",
        session_id="session-a",
        context_limit_tokens=1_000_000,
    )

    assert projected.truncated is True
    envelope = json.loads(projected.content)
    assert envelope["revision"] == 19
    assert envelope["created_slide_ids"] == ["slide-1"]
    assert envelope["created"] == [{"index": 0, "slideId": "slide-1"}]
    assert envelope["content_ref"] == projected.content_ref
    full = json.loads(result_store.read_tool_result(
        projected.content_ref,
        limit=len(raw),
        session_id="session-a",
    ))
    assert full["content"] == raw


def test_epoch_compaction_keeps_live_powerpoint_tail_and_receipts_older_episode(
    isolated_result_store,
):
    arguments = _large_powerpoint_arguments()
    assistant = {
        "role": "assistant",
        "content": "creating slides",
        "reasoning_content": "provider-native reasoning",
        "tool_calls": [{
            "id": "ppt-1",
            "type": "function",
            "function": {"name": "PowerPointToolSearch", "arguments": arguments},
        }],
    }
    original_assistant = copy.deepcopy(assistant)
    ppt_result = result_store.project_tool_result_for_model(
        _powerpoint_result(),
        tool_name="PowerPointToolSearch",
        tool_call_id="ppt-1",
        session_id="session-a",
        context_limit_tokens=1_000_000,
    )
    first_projection = result_store.compact_powerpoint_tool_episodes_for_epoch([
        assistant,
        {"role": "tool", "tool_call_id": "ppt-1", "content": ppt_result.content},
    ])
    assert first_projection[0]["tool_calls"][0]["function"]["arguments"] == arguments
    assert assistant == original_assistant

    later_projection = result_store.compact_powerpoint_tool_episodes_for_epoch([
        assistant,
        {"role": "tool", "tool_call_id": "ppt-1", "content": ppt_result.content},
        {
            "role": "assistant",
            "content": "verify",
            "tool_calls": [{
                "id": "render-1",
                "type": "function",
                "function": {"name": "PowerPointRenderSlide", "arguments": '{"slideId":"slide-1"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "render-1", "content": '{"status":"success"}'},
    ])

    assert len(later_projection) == 3
    receipt = json.loads(later_projection[0]["content"])
    assert receipt["type"] == "powerpoint_tool_episode_receipt"
    assert receipt["calls"][0]["payload_ref"].startswith("tool-result://")
    assert receipt["calls"][0]["result"]["revision"] == 19
    assert arguments not in later_projection[0]["content"]
    assert later_projection[0]["compacted_block"] is True
    assert later_projection[0]["powerpoint_episode_receipt"] is True
    assert later_projection[1]["tool_calls"][0]["function"]["name"] == "PowerPointRenderSlide"
    assert assistant == original_assistant


def test_older_small_powerpoint_read_episode_becomes_receipt(isolated_result_store):
    inspect = {
        "role": "assistant",
        "content": "inspect",
        "tool_calls": [{
            "id": "inspect-1",
            "type": "function",
            "function": {
                "name": "PowerPointInspect",
                "arguments": '{"operation":"list_slides"}',
            },
        }],
    }
    render = {
        "role": "assistant",
        "content": "verify",
        "tool_calls": [{
            "id": "render-1",
            "type": "function",
            "function": {
                "name": "PowerPointRenderSlide",
                "arguments": '{"slideIndex":0}',
            },
        }],
    }
    projected = result_store.compact_powerpoint_tool_episodes_for_epoch([
        inspect,
        {
            "role": "tool",
            "tool_call_id": "inspect-1",
            "content": json.dumps({
                "status": "success",
                "revision": 7,
                "slides": [{"index": 0, "slideId": "slide-1", "title": "Cover"}],
            }),
        },
        render,
        {"role": "tool", "tool_call_id": "render-1", "content": '{"status":"success"}'},
    ])

    assert len(projected) == 3
    receipt = json.loads(projected[0]["content"])
    assert receipt["type"] == "powerpoint_tool_episode_receipt"
    assert receipt["calls"][0]["result"]["revision"] == 7
    assert receipt["calls"][0]["result"]["slides"] == [
        {"index": 0, "slideId": "slide-1"}
    ]
    assert receipt["calls"][0]["result_ref"].startswith("tool-result://")


def test_normal_llm_projection_does_not_rewrite_powerpoint_history(
    isolated_result_store,
):
    from cyrene.agent.deep_reflection import project_history_for_llm

    messages = [
        {
            "role": "assistant",
            "content": "creating slides",
            "reasoning_content": "provider-native reasoning",
            "tool_calls": [{
                "id": "ppt-append-only",
                "type": "function",
                "function": {
                    "name": "PowerPointToolSearch",
                    "arguments": _large_powerpoint_arguments(),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "ppt-append-only",
            "content": _powerpoint_result(),
        },
        {"role": "assistant", "content": "verified"},
    ]
    before = copy.deepcopy(messages)

    projected = project_history_for_llm(messages)

    assert projected == before
    assert messages == before
    assert not any(
        message.get("powerpoint_episode_receipt")
        for message in projected
    )


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
