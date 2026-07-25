"""Regression tests for workbench chat transcript segmentation.

Covers the fixes for the "send me the file" exchange:

* a tool-delivered reply (``send_file``) is rendered *after* the tool call
  that produced it, not before (ordering);
* when one turn delivers several files at once, the tool card sits above *all*
  of them, not just the last (batched ``send_file`` ordering);
* a failed tool call is marked in the trace instead of showing a success
  check (``send_wechat_file`` on the WebUI channel);
* a mid-run turn that carries prose *and* tool calls keeps its prose as its
  own reply block instead of dropping it (the "let me check…" preamble);
* the final text-only reply is left to the caller (never duplicated).
"""

import pytest

from cyrene.agent.message import _apply_assistant_meta, _assistant_entry_from_response
from cyrene.workbench.chat_runs import ChatRun
from cyrene.workbench.chat import (
    _extract_exchange_segments,
    _extract_exchange_timeline,
    _last_exchange_model,
    _merge_chat_messages_chronologically,
    _pending_question_message,
    _publish_live_exchange_segments_once,
    _remove_retry_replaced_messages,
    _tool_result_is_error,
)


def test_durable_timeline_keeps_cards_and_messages_in_event_order():
    messages = [
        {"role": "user", "message_id": "u1", "content": "start"},
        {
            "role": "assistant",
            "message_id": "a1",
            "created_at": "2026-01-01T00:00:01+00:00",
            "reasoning_content": "first thought",
            "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": '{}'}}],
        },
        _tool_result("c1", '{"ok":true}'),
        {
            "role": "assistant",
            "message_id": "m1",
            "created_at": "2026-01-01T00:00:02+00:00",
            "intermediate_reply": True,
            "content": "中间进度",
        },
        {
            "role": "assistant",
            "message_id": "a2",
            "created_at": "2026-01-01T00:00:03+00:00",
            "tool_calls": [{"id": "c2", "function": {"name": "list_skills", "arguments": '{}'}}],
        },
        _tool_result("c2", '{"ok":true}'),
        {
            "role": "assistant",
            "message_id": "a3",
            "created_at": "2026-01-01T00:00:04+00:00",
            "tool_calls": [{"id": "c3", "function": {"name": "read_file", "arguments": '{}'}}],
        },
        _tool_result("c3", '{"ok":true}'),
        {
            "role": "assistant",
            "message_id": "a4",
            "created_at": "2026-01-01T00:00:05+00:00",
            "reasoning_content": "final thought",
            "content": "完成",
        },
    ]

    timeline, _usage, _files = _extract_exchange_timeline(messages, set())

    assert [entry["id"] for entry in timeline] == [
        "activity_a1",
        "m1",
        "activity_a2",
    ]
    assert [tool["tool"] for tool in timeline[2]["trace"]] == [
        "list_skills",
        "read_file",
    ]
    assert timeline[1]["trace"] == []
    assert timeline[2]["reasoning"] == "final thought"
    assert timeline[0]["createdAt"] < timeline[1]["createdAt"] < timeline[2]["createdAt"]


def test_durable_timeline_splits_tools_around_visible_tool_preamble():
    messages = [
        {"role": "user", "message_id": "u1", "content": "把照片发给我"},
        {
            "role": "assistant",
            "message_id": "a1",
            "created_at": "2026-01-01T00:00:01+00:00",
            "reasoning_content": "先确认文件存在",
            "tool_calls": [{"id": "c1", "function": {"name": "Bash", "arguments": '{"command":"ls photo.jpg"}'}}],
        },
        _tool_result("c1", '{"exit_code":0}'),
        # send_file writes this delivery reply before its own assistant tool-call
        # message is committed; the renderer reorders it after that tool call.
        {
            "role": "assistant",
            "message_id": "file1",
            "created_at": "2026-01-01T00:00:03+00:00",
            "intermediate_reply": True,
            "content": "你的照片",
            "attachments": [{"id": "f1", "name": "photo.jpg", "url": "/f/photo.jpg"}],
        },
        {
            "role": "assistant",
            "message_id": "a2",
            "created_at": "2026-01-01T00:00:02+00:00",
            "reasoning_content": "文件存在，现在发送",
            "content": "找到了，我发给你。",
            "tool_calls": [{"id": "c2", "function": {"name": "send_file", "arguments": '{"path":"photo.jpg"}'}}],
        },
        _tool_result("c2", '{"status":"sent"}'),
        {
            "role": "assistant",
            "message_id": "a3",
            "created_at": "2026-01-01T00:00:04+00:00",
            "content": "发送完成。",
        },
    ]

    timeline, _usage, _files = _extract_exchange_timeline(messages, set())

    assert [entry["id"] for entry in timeline] == [
        "activity_a1",
        "a2",
        "activity_a2",
        "file1",
    ]
    assert [tool["tool"] for tool in timeline[0]["trace"]] == ["Bash"]
    assert timeline[0]["reasoning"] == "先确认文件存在"
    assert timeline[1]["content"] == "找到了，我发给你。"
    assert timeline[1]["trace"] == []
    assert [tool["tool"] for tool in timeline[2]["trace"]] == ["send_file"]
    assert timeline[2]["reasoning"] == "文件存在，现在发送"
    assert timeline[3]["content"] == "你的照片"
    assert timeline[3]["trace"] == []


def test_durable_timeline_omits_tool_free_pure_reasoning_card():
    messages = [
        {
            "role": "assistant",
            "message_id": "a1",
            "created_at": "2026-01-01T00:00:01+00:00",
            "reasoning_content": "first",
        },
        {
            "role": "assistant",
            "message_id": "a2",
            "created_at": "2026-01-01T00:00:02+00:00",
            "reasoning_content": "second",
        },
        {
            "role": "assistant",
            "message_id": "a3",
            "created_at": "2026-01-01T00:00:03+00:00",
            "content": "done",
        },
    ]

    timeline, _usage, _files = _extract_exchange_timeline(messages, set())

    assert timeline == []


def test_exchange_model_comes_from_actual_fallback_response():
    messages = [
        {"role": "assistant", "message_id": "old", "usage": {"model": "primary"}},
        {
            "role": "assistant",
            "message_id": "new",
            "content": "fallback answer",
            "usage": {"model": "backup", "prompt_tokens": 10, "completion_tokens": 2},
        },
    ]

    assert _last_exchange_model(messages, {"old"}) == "backup"
    segments, _trace, _usage, _files = _extract_exchange_segments(messages, {"old"})
    assert segments == []  # final answer is persisted by the caller


def test_late_discovered_assistant_message_is_inserted_before_guidance():
    chat = {
        "messages": [
            {"id": "u1", "role": "user", "content": "start", "createdAt": "2026-01-01T00:00:00+00:00"},
            {"id": "g1", "role": "user", "content": "steer", "createdAt": "2026-01-01T00:00:02+00:00", "guidance": True},
        ]
    }
    _merge_chat_messages_chronologically(chat, [
        {"id": "a1", "role": "assistant", "content": "working", "createdAt": "2026-01-01T00:00:01+00:00"},
        {"id": "a2", "role": "assistant", "content": "done", "createdAt": "2026-01-01T00:00:03+00:00"},
    ])

    assert [message["id"] for message in chat["messages"]] == ["u1", "a1", "g1", "a2"]


def test_pending_question_is_a_durable_transcript_message_with_trace():
    message = _pending_question_message(
        {"id": "q1", "text": "Which option?", "kind": "clarification"},
        trace=[{"tool": "request_user_input"}],
        model="test-model",
    )

    assert message["id"] == "msg_question_q1"
    assert message["questionPrompt"] is True
    assert message["questionId"] == "q1"
    assert message["content"] == "Which option?"
    assert message["trace"] == [{"tool": "request_user_input"}]


def test_all_assistant_entry_builders_stamp_event_time():
    direct = _assistant_entry_from_response({"content": "working"}, "round_1")
    applied = _apply_assistant_meta({"role": "assistant", "content": "done"})

    assert direct["created_at"]
    assert applied["created_at"]


def test_retry_cut_preserves_guidance_added_during_regeneration():
    chat = {
        "messages": [
            {"id": "u1", "role": "user", "content": "try this"},
            {"id": "old_a", "role": "assistant", "content": "old answer"},
            {"id": "g_new", "role": "user", "content": "new guidance", "guidance": True},
        ]
    }

    _remove_retry_replaced_messages(chat, "u1", {"old_a"})

    assert [message["id"] for message in chat["messages"]] == ["u1", "g_new"]


def test_live_intermediate_checkpoint_does_not_keep_activity_trace(monkeypatch):
    from cyrene.workbench import chat as chat_mod

    store = {
        "chats": [{
            "id": "chat_live",
            "messages": [{"id": "u1", "role": "user", "content": "go", "createdAt": "2026-01-01T00:00:00+00:00"}],
        }]
    }
    monkeypatch.setattr(chat_mod, "_read_chats_store", lambda: store)
    monkeypatch.setattr(chat_mod, "_write_chats_store", lambda payload: store.update(payload))

    chat_mod._persist_live_public_message("chat_live", {
        "id": "a1",
        "role": "assistant",
        "content": "checking",
        "createdAt": "2026-01-01T00:00:01+00:00",
        "trace": [{"tool": "Bash"}],
    })
    assert "trace" not in store["chats"][0]["messages"][1]

    chat_mod._merge_chat_messages_chronologically(store["chats"][0], [{
        "id": "a1",
        "role": "assistant",
        "content": "checking",
        "createdAt": "2026-01-01T00:00:01+00:00",
        "intermediate": True,
        "trace": [],
    }])

    messages = store["chats"][0]["messages"]
    assert [message["id"] for message in messages] == ["u1", "a1"]
    assert messages[1]["trace"] == []


def _asst_tool(mid, name, call_id, args="{}", content=""):
    return {
        "role": "assistant",
        "message_id": mid,
        "content": content,
        "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": args}}],
    }


def _tool_result(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _asst_tools(mid, calls, content=""):
    """An assistant turn that batches several tool calls into one message.

    *calls* is a list of (name, call_id, args) tuples."""
    return {
        "role": "assistant",
        "message_id": mid,
        "content": content,
        "tool_calls": [
            {"id": cid, "function": {"name": name, "arguments": args}}
            for (name, cid, args) in calls
        ],
    }


def _delivered(mid, name):
    """A tool-delivered (``send_file``) intermediate reply carrying one file."""
    return {
        "role": "assistant",
        "message_id": mid,
        "intermediate_reply": True,
        "content": f"图 {name}",
        "attachments": [{"id": name, "name": name, "url": f"/f/{name}", "content_type": "image/png"}],
    }


# The recorded exchange: ls -> cp -> send_wechat_file (fails on WebUI) ->
# send_file delivers the file. The delivery reply lands in storage *before* its
# own send_file tool call (live write wins the merge position).
RECORDED = [
    {"role": "user", "message_id": "u8", "content": "把我桌面的 rfi.pdf 发给我"},
    _asst_tool("a9", "Bash", "c_ls", '{"command":"ls -la"}'),
    _tool_result("c_ls", '{"exit_code":0}'),
    _asst_tool("a11", "Bash", "c_cp", '{"command":"cp ..."}'),
    _tool_result("c_cp", '{"exit_code":0}'),
    _asst_tool("a13", "send_wechat_file", "c_wx", '{"path":"deliverables/rfi.pdf"}'),
    _tool_result("c_wx", "Error: current channel does not support WeChat file sending. Use send_file for WebUI attachments."),
    {
        "role": "assistant",
        "message_id": "a15",
        "intermediate_reply": True,
        "content": "你桌面上的 rfi.pdf",
        "attachments": [{"id": "rfi", "name": "rfi.pdf", "url": "/f/rfi.pdf", "content_type": "application/pdf"}],
    },
    _asst_tool("a16", "send_file", "c_sf", '{"path":"deliverables/rfi.pdf"}'),
    _tool_result("c_sf", '{"status":"sent"}'),
    {"role": "assistant", "message_id": "a18", "content": "Done."},
]


def test_delivered_file_renders_after_its_send_file_call():
    segments, trailing, _usage, _files = _extract_exchange_segments(RECORDED, set())

    # One reply block (the file delivery); "Done." is left to the caller.
    assert len(segments) == 1
    seg = segments[0]
    assert seg["content"] == "你桌面上的 rfi.pdf"
    assert [a["name"] for a in seg["attachments"]] == ["rfi.pdf"]

    # send_file is in the trace card shown ABOVE the file, i.e. the file no
    # longer floats above its own "sent file" card.
    tools = [t["tool"] for t in seg["trace"]]
    assert tools == ["Bash", "Bash", "send_wechat_file", "send_file"]

    # The final "Done." reply carries no leftover tool card.
    assert trailing == []


def test_batched_send_file_card_sits_above_all_delivered_files():
    # One turn calls send_file three times; all three delivery replies stack up
    # in storage *before* that single tool-call message (each live write lands
    # ahead of the batched tool call). The tool card must render above ALL three
    # files, not just the last one.
    messages = [
        {"role": "user", "message_id": "u1", "content": "把三张图发过来"},
        _delivered("r1", "01.png"),
        _delivered("r2", "02.png"),
        _delivered("r3", "03.png"),
        _asst_tools("a1", [
            ("send_file", "c1", '{"path":"deliverables/01.png"}'),
            ("send_file", "c2", '{"path":"deliverables/02.png"}'),
            ("send_file", "c3", '{"path":"deliverables/03.png"}'),
        ]),
        _tool_result("c1", '{"status":"sent"}'),
        _tool_result("c2", '{"status":"sent"}'),
        _tool_result("c3", '{"status":"sent"}'),
        {"role": "assistant", "message_id": "a2", "content": "三张图都发好了。"},
    ]
    segments, trailing, _usage, _files = _extract_exchange_segments(messages, set())

    # Three reply blocks, in delivery order.
    assert [a["name"] for s in segments for a in s["attachments"]] == ["01.png", "02.png", "03.png"]

    # The whole 3-call card sits with the FIRST file (so it renders above all
    # files); the later files carry no leftover card.
    assert [t["tool"] for t in segments[0]["trace"]] == ["send_file", "send_file", "send_file"]
    assert all(seg.get("trace") in (None, []) for seg in segments[1:])
    assert trailing == []


def test_send_file_replies_from_separate_turns_keep_their_own_cards():
    # Two independent send_file turns: each delivery reply belongs to its own
    # tool call. They must NOT be merged into one card — the batch reorder only
    # groups replies that share a single tool-call message, and across turns each
    # reply is split from the next by its own tool call + result.
    messages = [
        {"role": "user", "message_id": "u1", "content": "发两个文件"},
        _delivered("r1", "a.png"),
        _asst_tool("a1", "send_file", "c1", '{"path":"deliverables/a.png"}'),
        _tool_result("c1", '{"status":"sent"}'),
        _delivered("r2", "b.png"),
        _asst_tool("a2", "send_file", "c2", '{"path":"deliverables/b.png"}'),
        _tool_result("c2", '{"status":"sent"}'),
        {"role": "assistant", "message_id": "a3", "content": "两个都发了。"},
    ]
    segments, trailing, _usage, _files = _extract_exchange_segments(messages, set())

    assert [a["name"] for s in segments for a in s["attachments"]] == ["a.png", "b.png"]
    # Each file keeps its own single-call card.
    assert [t["tool"] for t in segments[0]["trace"]] == ["send_file"]
    assert [t["tool"] for t in segments[1]["trace"]] == ["send_file"]
    assert trailing == []


def test_failed_wechat_call_is_marked_not_a_success():
    segments, _trailing, _usage, _files = _extract_exchange_segments(RECORDED, set())
    by_tool = {t["tool"]: t for t in segments[0]["trace"]}
    assert by_tool["send_wechat_file"].get("failed") is True
    # The successful calls are not mismarked.
    assert "failed" not in by_tool["send_file"]
    assert all("failed" not in t for t in segments[0]["trace"] if t["tool"] == "Bash")


def test_preamble_prose_is_kept_as_its_own_reply_block():
    messages = [
        {"role": "user", "message_id": "u1", "content": "列一下桌面"},
        _asst_tool("a1", "Bash", "c1", '{"command":"ls"}', content="我先看看桌面。"),
        _tool_result("c1", '{"exit_code":0}'),
        {"role": "assistant", "message_id": "a2", "content": "桌面上有 3 个文件。"},
    ]
    segments, trailing, _usage, _files = _extract_exchange_segments(messages, set())

    # The preamble prose survives as a standalone reply with no trace above it…
    assert len(segments) == 1
    assert segments[0]["content"] == "我先看看桌面。"
    assert segments[0].get("trace") in (None, [])
    assert segments[0]["opensActivity"] is True
    # …and the tool it requested becomes the card shown with the final reply.
    assert [t["tool"] for t in trailing] == ["Bash"]


def test_plain_text_guidance_ack_before_final_reply_is_kept():
    messages = [
        {"role": "user", "message_id": "u1", "content": "start"},
        {
            "role": "assistant",
            "message_id": "a1",
            "content": "收到，我会按新的要求调整。",
            "created_at": "2026-01-01T00:00:02+00:00",
            "guidance_ack_for_guidance_id": "g1",
        },
        {
            "role": "assistant",
            "message_id": "a2",
            "content": "调整后的最终结果。",
            "created_at": "2026-01-01T00:00:03+00:00",
        },
    ]

    segments, trailing, _usage, _files = _extract_exchange_segments(messages, set())

    assert [segment["content"] for segment in segments] == ["收到，我会按新的要求调整。"]
    assert segments[0]["createdAt"] == "2026-01-01T00:00:02+00:00"
    assert trailing == []


@pytest.mark.asyncio
async def test_live_preamble_without_message_id_is_published_once(monkeypatch):
    """The live segment scanner can see a tool preamble before message_id lands.

    Its fallback id must be stable across scans; otherwise the Workbench shows
    the same assistant block repeatedly while the agent is still running.
    """
    from cyrene.workbench import chat as chat_mod

    live_messages = [
        {"role": "user", "message_id": "u1", "content": "search"},
        {
            "role": "assistant",
            "content": "我先打开页面看看。",
            "tool_calls": [
                {"id": "c1", "function": {"name": "browser_navigate", "arguments": '{"url":"https://example.com"}'}}
            ],
        },
    ]
    monkeypatch.setattr(chat_mod, "_session_state_messages", lambda _chat_id: live_messages)
    run = ChatRun("chat_live", {"type": "ack", "chatId": "chat_live"})
    published_ids: set[str] = set()

    await _publish_live_exchange_segments_once(run, "chat_live", {"u1"}, published_ids)
    await _publish_live_exchange_segments_once(run, "chat_live", {"u1"}, published_ids)

    events = [event for event in run.events if event.get("type") == "intermediate_message"]
    assert len(events) == 1
    assert events[0]["message"]["content"] == "我先打开页面看看。"
    assert events[0]["message"]["id"].startswith("msg_live_")


@pytest.mark.asyncio
async def test_live_preamble_durable_id_does_not_republish_same_text(monkeypatch):
    """A live preamble can gain message_id after the first scanner tick."""
    from cyrene.workbench import chat as chat_mod

    live_messages = [
        {"role": "user", "message_id": "u1", "content": "open"},
        {
            "role": "assistant",
            "content": "我先打开页面看看。",
            "tool_calls": [
                {"id": "c1", "function": {"name": "browser_navigate", "arguments": '{"url":"https://example.com"}'}}
            ],
        },
    ]

    monkeypatch.setattr(chat_mod, "_session_state_messages", lambda _chat_id: live_messages)
    run = ChatRun("chat_live", {"type": "ack", "chatId": "chat_live"})
    published_ids: set[str] = set()

    await _publish_live_exchange_segments_once(run, "chat_live", {"u1"}, published_ids)
    live_messages[1]["message_id"] = "a1"
    # Simulate the publisher rebuilding its seen set from the run event log.
    published_ids = set()
    await _publish_live_exchange_segments_once(run, "chat_live", {"u1"}, published_ids)

    events = [event for event in run.events if event.get("type") == "intermediate_message"]
    assert len(events) == 1
    assert events[0]["message"]["content"] == "我先打开页面看看。"
    assert events[0]["message"]["liveDedupeKey"].startswith("msg_sem_")
    assert "a1" in published_ids


def test_live_extraction_surfaces_open_tool_preamble():
    messages = [
        {"role": "user", "message_id": "u1", "content": "打开 B 站看看"},
        _asst_tool("a1", "browser_navigate", "c1", '{"url":"https://www.bilibili.com"}'),
        _tool_result("c1", '{"ok":true}'),
        _asst_tool(
            "a2",
            "browser_screenshot",
            "c2",
            '{"url":"https://www.bilibili.com"}',
            content="B 站检测到浏览器版本过低，先截个图给你看。",
        ),
        _tool_result("c2", '{"ok":true}'),
    ]

    segments, trailing, _usage, _files = _extract_exchange_segments(messages, set())
    assert segments == []
    assert [t["tool"] for t in trailing] == ["browser_navigate", "browser_screenshot"]

    live_segments, live_trailing, _usage, _files = _extract_exchange_segments(
        messages,
        set(),
        include_open_tool_preamble=True,
    )
    assert [segment["content"] for segment in live_segments] == [
        "B 站检测到浏览器版本过低，先截个图给你看。"
    ]
    assert [t["tool"] for t in live_segments[0]["trace"]] == ["browser_navigate"]
    assert [t["tool"] for t in live_trailing] == ["browser_screenshot"]


def test_live_extraction_does_not_surface_terminal_control_reply():
    messages = [
        {"role": "user", "message_id": "u1", "content": "算一下 2+2"},
        {
            "role": "assistant",
            "message_id": "a1",
            "content": "2 + 2 = 4。",
            "tool_calls": [{"id": "q", "function": {"name": "quit", "arguments": "{}"}}],
        },
    ]

    segments, trailing, _usage, _files = _extract_exchange_segments(
        messages,
        set(),
        include_open_tool_preamble=True,
    )
    assert segments == []
    assert trailing == []


def test_final_text_only_reply_is_not_duplicated_into_a_segment():
    # A plain answer with no tools and no mid-run replies yields zero segments;
    # the caller persists the final reply itself.
    messages = [
        {"role": "user", "message_id": "u1", "content": "你好"},
        {"role": "assistant", "message_id": "a1", "content": "你好，有什么可以帮你？"},
    ]
    segments, trailing, _usage, _files = _extract_exchange_segments(messages, set())
    assert segments == []
    assert trailing == []


def test_final_turn_with_content_and_quit_is_not_duplicated():
    # The closing turn may carry prose alongside a control-only `quit` batch.
    # That content is the caller's reply_text — it must not also surface as a
    # mid-run reply block (would render the final answer twice).
    messages = [
        {"role": "user", "message_id": "u1", "content": "算一下 2+2"},
        {
            "role": "assistant",
            "message_id": "a1",
            "content": "2 + 2 = 4。",
            "tool_calls": [{"id": "q", "function": {"name": "quit", "arguments": "{}"}}],
        },
    ]
    segments, trailing, _usage, _files = _extract_exchange_segments(messages, set())
    assert segments == []
    assert trailing == []


def test_tool_result_is_error_detection():
    assert _tool_result_is_error("Error: nope")
    assert _tool_result_is_error("Tool failed: boom")
    assert _tool_result_is_error("Failed to connect")
    assert _tool_result_is_error('{"status":"error","type":"provider_error"}')
    assert _tool_result_is_error('{"status":"uncertain","summary":"not verified"}')
    assert not _tool_result_is_error('{"status":"success"}')
    assert not _tool_result_is_error('{"exit_code": 0}')
    assert not _tool_result_is_error('{"exit_code": 1, "stderr": "x"}')  # non-zero bash exit is not flagged
    assert not _tool_result_is_error("")
