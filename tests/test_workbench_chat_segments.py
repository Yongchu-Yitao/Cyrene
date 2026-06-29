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

from webui.routes_workbench_chat import (
    _extract_exchange_segments,
    _tool_result_is_error,
)


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

    # …and the tool it requested becomes the card shown with the final reply.
    assert [t["tool"] for t in trailing] == ["Bash"]


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
    assert not _tool_result_is_error('{"exit_code": 0}')
    assert not _tool_result_is_error('{"exit_code": 1, "stderr": "x"}')  # non-zero bash exit is not flagged
    assert not _tool_result_is_error("")
