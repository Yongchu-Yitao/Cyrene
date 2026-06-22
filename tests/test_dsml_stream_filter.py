"""Streaming-safe DSML suppression + streamed tool-call assembly.

Regression coverage for the leak where DeepSeek's textual DSML tool-call
fallback was forwarded to the UI verbatim mid-stream (it is only parsed back
into real tool calls *after* the stream completes). The filter must hide the
markup from the forwarded stream while the caller keeps the raw text so
``_normalize_dsml_tool_calls`` can still recover the tool call.
"""
import json

from cyrene.call_llm import (
    _DsmlStreamFilter,
    _accumulate_tool_call_deltas,
    _finalize_tool_call_fragments,
    _normalize_dsml_tool_calls,
)

WEBFETCH_BLOCK = (
    '<｜｜DSML｜｜tool_calls>'
    '<｜｜DSML｜｜invoke name="WebFetch">'
    '<｜｜DSML｜｜parameter name="url" string="true">https://example.com/x.py</｜｜DSML｜｜parameter>'
    '<｜｜DSML｜｜parameter name="offset" string="false">600</｜｜DSML｜｜parameter>'
    '</｜｜DSML｜｜invoke>'
    '</｜｜DSML｜｜tool_calls>'
)


def _feed_in_chunks(text: str, size: int) -> str:
    f = _DsmlStreamFilter()
    out = []
    for i in range(0, len(text), size):
        out.append(f.feed(text[i:i + size]))
    out.append(f.flush())
    emitted = "".join(out)
    assert emitted == f.emitted()
    return emitted


def test_plain_text_passes_through_unchanged():
    text = "Hello, this mentions a < and a | but no markup."
    assert _feed_in_chunks(text, 1) == text
    assert _feed_in_chunks(text, 7) == text


def test_full_block_in_single_feed_is_suppressed():
    f = _DsmlStreamFilter()
    assert f.feed(WEBFETCH_BLOCK) == ""
    assert f.flush() == ""
    assert f.emitted() == ""


def test_prose_around_block_is_kept_block_dropped():
    raw = "你提醒得对，让我对比一下。" + WEBFETCH_BLOCK + "尾巴"
    for size in (1, 2, 3, 5, 13, 1000):
        assert _feed_in_chunks(raw, size) == "你提醒得对，让我对比一下。尾巴"


def test_ascii_pipe_variant_is_suppressed():
    raw = "before<||DSML||tool_calls><||DSML||invoke name=\"quit\"/></||DSML||tool_calls>after"
    for size in (1, 4, 999):
        assert _feed_in_chunks(raw, size) == "beforeafter"


def test_split_at_every_boundary_never_leaks_marker():
    raw = "intro " + WEBFETCH_BLOCK + " outro"
    emitted = _feed_in_chunks(raw, 1)
    assert "DSML" not in emitted
    assert emitted == "intro  outro"


def test_dangling_partial_opener_is_dropped_on_flush():
    f = _DsmlStreamFilter()
    # Stream ends mid-opener: the held tail is an incomplete DSML opener.
    assert f.feed("ok <｜｜DSML｜｜tool_ca") == "ok "
    assert f.flush() == ""
    assert f.emitted() == "ok "


def test_lone_angle_bracket_is_not_held_forever():
    f = _DsmlStreamFilter()
    f.feed("a <")
    out = f.feed("b c")  # '<b' diverges from the opener, must be released
    assert f.emitted() == "a <b c"
    assert "<b c" in out or out == "<b c"


def test_filter_hides_dsml_but_raw_still_normalizes_to_tool_call():
    """The core invariant: UI sees no markup, backend still gets the tool call."""
    raw = "Let me verify against the source.\n" + WEBFETCH_BLOCK
    visible = _feed_in_chunks(raw, 3)
    assert "DSML" not in visible
    assert visible.strip() == "Let me verify against the source."

    tools = [{"type": "function", "function": {"name": "WebFetch", "parameters": {}}}]
    normalized = _normalize_dsml_tool_calls({"role": "assistant", "content": raw}, tools)
    calls = normalized.get("tool_calls") or []
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "WebFetch"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["url"] == "https://example.com/x.py"
    # block stripped from the stored content too
    assert "DSML" not in normalized["content"]


def test_streamed_tool_call_fragments_are_assembled():
    frags: dict = {}
    _accumulate_tool_call_deltas(
        [{"index": 0, "id": "call_1", "type": "function",
          "function": {"name": "WebFetch", "arguments": ""}}], frags)
    _accumulate_tool_call_deltas([{"index": 0, "function": {"arguments": '{"url":'}}], frags)
    _accumulate_tool_call_deltas([{"index": 0, "function": {"arguments": ' "http://x"}'}}], frags)
    calls = _finalize_tool_call_fragments(frags)
    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert calls[0]["function"]["name"] == "WebFetch"
    assert json.loads(calls[0]["function"]["arguments"]) == {"url": "http://x"}


def test_assembled_tool_calls_get_generated_ids_and_skip_nameless():
    frags: dict = {}
    _accumulate_tool_call_deltas([{"index": 0, "function": {"name": "Read", "arguments": "{}"}}], frags)
    _accumulate_tool_call_deltas([{"index": 1, "function": {"arguments": "{}"}}], frags)  # no name -> skipped
    calls = _finalize_tool_call_fragments(frags)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "Read"
    assert calls[0]["id"].startswith("call_stream_")
