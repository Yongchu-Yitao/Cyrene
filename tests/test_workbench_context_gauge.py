"""Context-window gauge + composition for the Workbench chat overview.

The overview reads the agent's RAW per-session state (not the public transcript)
so the gauge reflects exactly what the compactor measures against the context
window. These tests pin two invariants:

1. ``_context_segment_tokens`` sums to ``call_llm._message_token_estimate`` — the
   breakdown shares one honest denominator with the 60% compaction trigger.
2. ``_chat_context_payload`` reports a per-conversation ratio, ordered segments
   and compaction state derived from that raw state.
"""

from cyrene import config_store
from webui import routes_workbench_chat as rwc
from cyrene.call_llm import _message_token_estimate


def _fixed_ctx_limit(monkeypatch, value):
    """Pin the window size so the gauge is independent of the host's model config.

    ``_chat_context_payload`` resolves the limit lazily via
    ``config_store.ctx_limit_for_model``; patch it there.
    """
    monkeypatch.setattr(config_store, "effective_ctx_limit_for_model", lambda _model: value)


def _sample_messages():
    return [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "帮我查一下北京到上海的高铁"},
        {
            "role": "assistant",
            "content": "好的，我来查询。",
            "tool_calls": [
                {"function": {"name": "search", "arguments": '{"q": "北京 上海 高铁"}'}}
            ],
        },
        {"role": "tool", "content": "G1次 08:00 发车 " * 20, "tool_call_id": "call_1"},
        {"role": "assistant", "content": "查到了：G1次 08:00 出发。"},
        {
            "role": "system",
            "content": "[Compacted earlier context]\nUser: 之前的对话…",
            "compacted_block": True,
            "llm_compacted": True,
        },
    ]


def test_segment_tokens_sum_matches_compactor_estimate():
    messages = _sample_messages()
    seg = rwc._context_segment_tokens(messages)
    assert set(seg) == set(rwc._CONTEXT_SEGMENT_KEYS)
    # The whole point: per-category split must reconcile with the exact number
    # the compactor compares to the window, so the gauge can't drift from it.
    assert sum(seg.values()) == sum(_message_token_estimate(m) for m in messages)


def test_segment_tokens_attribution():
    seg = rwc._context_segment_tokens(_sample_messages())
    # Tool-call args + tool-result body land in `tool` and dominate (the bulk).
    assert seg["tool"] == max(seg.values())
    # Each named bucket got something; compacted history is tracked separately.
    for key in ("compacted", "system", "user", "assistant", "tool"):
        assert seg[key] > 0


def test_segment_tokens_ignore_non_dicts():
    seg = rwc._context_segment_tokens([None, "junk", {"role": "user", "content": "hi"}])
    assert seg["user"] > 0
    assert seg["tool"] == 0 and seg["compacted"] == 0


def test_context_payload_ratio_and_segments(monkeypatch):
    monkeypatch.setattr(rwc, "_session_state_messages", lambda _id: _sample_messages())
    _fixed_ctx_limit(monkeypatch, 1_000_000)
    payload = rwc._chat_context_payload("chat_x", "deepseek-v4-flash")

    assert payload["ctxLimit"] == 1_000_000
    assert payload["ctxUsed"] == sum(_message_token_estimate(m) for m in _sample_messages())
    assert payload["ratio"] == payload["ctxUsed"] / 1_000_000
    assert payload["compactTriggerRatio"] == 0.6
    assert payload["messageCount"] == 6
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["usage"]["total_tokens"] == 0

    keys = [seg["key"] for seg in payload["segments"]]
    assert keys == list(rwc._CONTEXT_SEGMENT_KEYS)
    assert sum(seg["tokens"] for seg in payload["segments"]) == payload["ctxUsed"]

    assert payload["compaction"] == {
        "active": True,
        "blocks": 1,
        "tokens": payload["segments"][0]["tokens"],
        "distilled": True,
    }


def test_context_payload_unknown_model_uses_smallest_known_window(monkeypatch):
    monkeypatch.setattr(rwc, "_session_state_messages", lambda _id: _sample_messages())
    monkeypatch.setattr(config_store, "effective_ctx_limit_for_model", lambda _model: 200_000)
    payload = rwc._chat_context_payload("chat_x", "some-unlisted-model")
    assert payload["ctxLimit"] == 200_000
    assert payload["ratio"] == payload["ctxUsed"] / 200_000
    assert payload["ctxUsed"] > 0


def test_context_payload_empty_state(monkeypatch):
    monkeypatch.setattr(rwc, "_session_state_messages", lambda _id: [])
    _fixed_ctx_limit(monkeypatch, 1_000_000)
    payload = rwc._chat_context_payload("fresh_chat", "deepseek-v4-flash")
    assert payload["ctxUsed"] == 0
    assert payload["ratio"] == 0.0
    assert all(seg["tokens"] == 0 for seg in payload["segments"])
    assert payload["compaction"]["active"] is False


def test_context_payload_uses_actual_model_and_live_usage(monkeypatch):
    messages = _sample_messages()
    messages[-2]["usage"] = {
        "model": "mimo-v2.5",
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }
    monkeypatch.setattr(rwc, "_session_state_messages", lambda _id: messages)
    seen_models = []
    monkeypatch.setattr(
        config_store,
        "effective_ctx_limit_for_model",
        lambda model: seen_models.append(model) or 1_000_000,
    )

    payload = rwc._chat_context_payload("chat_x", "google/gemma-4-12b-qat")

    assert seen_models == ["mimo-v2.5"]
    assert payload["model"] == "mimo-v2.5"
    assert payload["usage"]["prompt_tokens"] == 120
    assert payload["usage"]["completion_tokens"] == 30
    assert payload["usage"]["total_tokens"] == 150
