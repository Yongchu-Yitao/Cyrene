"""Verify the `quit(reply=...)` mechanism: the model delivers its final answer
through the quit tool argument so the common "done with an answer" turn skips the
tools=None reconstruction call (a prompt-cache prefix-root break)."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Patch missing deps before any cyrene import
sys.modules.setdefault("PIL", MagicMock())
sys.modules["PIL"].Image = MagicMock()
sys.modules.setdefault("pypdf", MagicMock())


def _quit_call(arguments: str) -> dict:
    return {"tool_calls": [{"function": {"name": "quit", "arguments": arguments}}]}


def test_quit_reply_extracted_from_arguments():
    from cyrene.agent.agent import _quit_reply_from_response

    assert _quit_reply_from_response(_quit_call(json.dumps({"reply": "最终答复"}))) == "最终答复"
    # leading/trailing whitespace is trimmed
    assert _quit_reply_from_response(_quit_call(json.dumps({"reply": "  hi  "}))) == "hi"


def test_quit_reply_absent_or_invalid_returns_empty():
    from cyrene.agent.agent import _quit_reply_from_response

    # quit without a reply argument -> falls back to reconstruction (empty here)
    assert _quit_reply_from_response(_quit_call("{}")) == ""
    # non-quit tool call
    assert _quit_reply_from_response(
        {"tool_calls": [{"function": {"name": "use_tools", "arguments": json.dumps({"task": "x"})}}]}
    ) == ""
    # malformed JSON arguments must not raise
    assert _quit_reply_from_response(_quit_call("{bad json")) == ""
    # no tool calls at all
    assert _quit_reply_from_response({"content": "hello"}) == ""
    # reply present but not a string
    assert _quit_reply_from_response(_quit_call(json.dumps({"reply": 123}))) == ""


def test_all_quit_tool_defs_expose_reply_param():
    """Every phase-specific or fixed-wire quit schema exposes optional reply."""
    from cyrene.agent.state import _LIGHT_TOOL_DEFS, _DEEP_RESEARCH_LIGHT_TOOL_DEFS
    from cyrene.tooling import get_main_wire_tool_defs

    def _quit_def(defs):
        return next(d for d in defs if d["function"]["name"] == "quit")

    for defs in (
        _LIGHT_TOOL_DEFS,
        _DEEP_RESEARCH_LIGHT_TOOL_DEFS,
        get_main_wire_tool_defs(),
    ):
        props = _quit_def(defs)["function"]["parameters"]["properties"]
        assert "reply" in props, "quit def is missing the reply param"
        assert props["reply"]["type"] == "string"
        # reply is intentionally optional: system-initiated rounds may quit silently.
        assert "required" not in _quit_def(defs)["function"]["parameters"]


def test_assistant_text_empty_for_quit_so_reply_is_used():
    """The wiring assumption: a bare quit call (no content) makes _assistant_text
    return "", which is what triggers _ensure_text_reply to fall back to the quit
    reply instead of the model's (absent) prose."""
    from cyrene.llm import _assistant_text

    assert _assistant_text(_quit_call(json.dumps({"reply": "x"}))) == ""


def test_delivery_fallback_replaces_bare_done_after_send_file():
    """If the streaming wrap-up still returns a placeholder, use the delivery
    tool result to avoid persisting a bare ``Done.`` after a file card."""
    from cyrene.agent.guidance import _delivery_fallback_text

    messages = [
        {"role": "user", "content": "发我"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "sf1", "function": {"name": "send_file", "arguments": "{}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "sf1",
            "content": json.dumps({
                "status": "sent",
                "attachment": {"name": "RF_Temperature_RFFP_2026.pdf"},
            }),
        },
    ]

    assert _delivery_fallback_text(messages) == "文件已发给你：RF_Temperature_RFFP_2026.pdf。"


def test_delivery_fallback_ignores_non_delivery_sent_results():
    from cyrene.agent.guidance import _delivery_fallback_text

    messages = [
        {"role": "user", "content": "告诉我一声"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "m1", "function": {"name": "send_message", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "m1", "content": json.dumps({"status": "sent"})},
    ]

    assert _delivery_fallback_text(messages) == ""


def test_streaming_wrapup_uses_quit_reply_before_done_fallback():
    from cyrene.agent.agent import _wrap_final_text_from_response

    wrap = _quit_call(json.dumps({"reply": "文件已经发给你了。"}))

    assert _wrap_final_text_from_response(wrap, []) == "文件已经发给你了。"


def test_streaming_wrapup_never_restores_dsml_from_quit_reply():
    from cyrene.agent.agent import _wrap_final_text_from_response

    wrap = _quit_call(json.dumps({
        "reply": (
            '<｜｜DSML｜｜tool_calls>'
            '<｜｜DSML｜｜invoke name="WebSearch"/>'
            '</｜｜DSML｜｜tool_calls>'
        ),
    }))

    assert _wrap_final_text_from_response(wrap, []) == "Done."


def test_terminal_reply_rejects_complete_and_partial_dsml_markup():
    from cyrene.agent.agent import _safe_terminal_reply_from_response

    complete = _quit_call(json.dumps({
        "reply": (
            '<｜｜DSML｜｜tool_calls>'
            '<｜｜DSML｜｜invoke name="WebSearch"/>'
            '</｜｜DSML｜｜tool_calls>'
        ),
    }))
    partial = {"content": "准备查询 <｜｜DSML｜｜tool_ca"}

    assert _safe_terminal_reply_from_response(complete, []) == ""
    assert _safe_terminal_reply_from_response(partial, []) == ""


async def test_streaming_wrapup_prompt_rejects_placeholder_after_delivery(monkeypatch):
    """The WebUI streaming quit path re-synthesizes the final answer; that call
    must carry the same no-placeholder rule as the normal final-answer path."""
    from cyrene.agent import guidance

    seen = {}

    async def fake_call_llm_stream(messages, max_tokens=32000, tools=None, **kwargs):
        seen["messages"] = messages
        seen["tools"] = tools
        return {"content": "Done."}

    monkeypatch.setattr(guidance, "_call_llm_stream", fake_call_llm_stream)

    tools = [{"type": "function", "function": {"name": "quit", "parameters": {"type": "object", "properties": {}}}}]
    response = await guidance._final_reply_with_tools(
        [
            {"role": "user", "content": "发我"},
            {"role": "tool", "tool_call_id": "sf1", "content": json.dumps({"status": "sent"})},
        ],
        tools,
    )

    assert response["content"] == "Done."
    assert seen["tools"] is tools
    final_instruction = seen["messages"][-1]["content"]
    assert "Do not reply with only 'Done'" in final_instruction
    assert "send_file" in final_instruction


async def test_quit_reply_is_persisted_as_assistant_content(monkeypatch):
    """A direct quit(reply=...) answer must be visible in the next LLM history."""
    from cyrene.agent import agent as agent_core
    from cyrene.call_llm import _sanitize_messages_for_llm

    saved_messages = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "q1",
                    "function": {
                        "name": "quit",
                        "arguments": json.dumps({"reply": "上一轮已经回答"}),
                    },
                }
            ],
        }

    async def fake_save(messages, **kwargs):
        saved_messages.append(messages)

    monkeypatch.setattr(agent_core, "_call_llm", fake_call_llm)
    monkeypatch.setattr(agent_core, "_save_session_messages", fake_save)
    monkeypatch.setattr(agent_core, "_append_session_message", AsyncMock())

    result = await agent_core._run_main_agent(
        "你在什么时候会找 entities",
        [{"role": "user", "content": "RecallConversation 之后你能看到什么"}],
        None,
        0,
        "db.sqlite3",
        persist_user_message=False,
    )

    assert result == "上一轮已经回答"
    assert saved_messages
    persisted_reply = saved_messages[-1][-1]
    assert persisted_reply["role"] == "assistant"
    assert persisted_reply["content"] == "上一轮已经回答"
    assert "tool_calls" not in persisted_reply

    next_turn_payload = _sanitize_messages_for_llm([
        {"role": "user", "content": "RecallConversation 之后你能看到什么"},
        persisted_reply,
        {"role": "user", "content": "继续说"},
    ])
    assert [message["role"] for message in next_turn_payload] == [
        "user", "assistant", "user",
    ]
    assert next_turn_payload[1]["content"] == "上一轮已经回答"
