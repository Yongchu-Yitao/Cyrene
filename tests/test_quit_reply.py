"""Verify that ``quit`` is only a terminal signal.

User-facing answers belong in normal assistant content. Quit arguments may carry
machine-readable completion metadata for subagents, but never answer text.
"""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _quit_call(arguments: str) -> dict:
    return {
        "tool_calls": [{
            "id": "quit-1",
            "function": {"name": "quit", "arguments": arguments},
        }]
    }


def test_terminal_reply_uses_assistant_content_and_ignores_quit_arguments():
    from cyrene.agent.agent import _safe_terminal_reply_from_response

    response = {
        "content": "最终答复",
        **_quit_call(json.dumps({"reply": "不应显示"})),
    }
    assert _safe_terminal_reply_from_response(response, []) == "最终答复"
    assert _safe_terminal_reply_from_response(
        _quit_call(json.dumps({"reply": "不应显示"})),
        [],
    ) == ""


def test_all_quit_tool_defs_exclude_reply_param():
    """Every phase-specific or fixed-wire quit schema excludes answer text."""
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
        assert "reply" not in props
        assert "required" not in _quit_def(defs)["function"]["parameters"]


def test_assistant_text_reads_normal_content_on_quit_turn():
    from cyrene.model_runtime.messages import _assistant_text

    response = {"content": "normal answer", **_quit_call("{}")}
    assert _assistant_text(response) == "normal answer"


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


def test_terminal_reply_rejects_dsml_and_legacy_tool_markup():
    from cyrene.agent.agent import _safe_terminal_reply_from_response

    complete = {
        "content": (
            '<｜｜DSML｜｜tool_calls>'
            '<｜｜DSML｜｜invoke name="WebSearch"/>'
            '</｜｜DSML｜｜tool_calls>'
        ),
        **_quit_call("{}"),
    }
    partial = {"content": "准备查询 <｜｜DSML｜｜tool_ca"}
    legacy = {
        "content": (
            "<tool_call><function=WebSearch>"
            "<parameter=query>Cyrene</parameter></function></tool_call>"
        ),
        **_quit_call("{}"),
    }

    assert _safe_terminal_reply_from_response(complete, []) == ""
    assert _safe_terminal_reply_from_response(partial, []) == ""
    assert _safe_terminal_reply_from_response(legacy, []) == ""


async def test_streaming_wrapup_prompt_rejects_placeholder_after_delivery(monkeypatch):
    """The WebUI streaming quit path re-synthesizes the final answer; that call
    must carry the same no-placeholder rule as the normal final-answer path."""
    from cyrene.agent import guidance, replies

    seen = {}

    async def fake_call_llm_stream(messages, max_tokens=32000, tools=None, **kwargs):
        seen["messages"] = messages
        seen["tools"] = tools
        return {"content": "Done."}

    monkeypatch.setattr(replies, "_call_llm_stream", fake_call_llm_stream)

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


async def test_phase1_plain_text_cannot_finish_without_quit(monkeypatch):
    from cyrene.agent import agent as agent_core

    responses = iter([
        {"content": "这是完整回答，但还没有完成信号。"},
        {
            "content": "这是带完成信号的正式回答。",
            **_quit_call("{}"),
        },
    ])
    model_messages = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        model_messages.append(messages)
        return next(responses)

    monkeypatch.setattr(agent_core, "_call_llm", fake_call_llm)
    monkeypatch.setattr(agent_core, "_save_session_messages", AsyncMock())
    monkeypatch.setattr(agent_core, "_append_session_message", AsyncMock())

    result = await agent_core._run_main_agent(
        "直接回答",
        [],
        None,
        0,
        "db.sqlite3",
        persist_user_message=False,
    )

    assert result == "这是带完成信号的正式回答。"
    assert len(model_messages) == 2
    assert "did not call a tool" in model_messages[1][-1]["content"]


async def test_execution_plain_text_requires_agent_to_continue_work(monkeypatch):
    from cyrene.agent import agent as agent_core

    def tool_call(call_id, name, arguments):
        return {
            "id": call_id,
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }

    responses = iter([
        {
            "content": "",
            "tool_calls": [tool_call("phase-1", "use_tools", {
                "execution_brief": "打开 B 站",
            })],
        },
        {
            "content": "",
            "tool_calls": [tool_call("describe", "browser_tools", {
                "operation": "describe",
                "capability_ids": ["browser.navigate"],
            })],
        },
        {"content": "正在打开浏览器访问 B 站。"},
        {
            "content": "",
            "tool_calls": [tool_call("invoke", "browser_tools", {
                "operation": "invoke",
                "capability_id": "browser.navigate",
                "arguments": {
                    "url": "https://www.bilibili.com",
                    "reason": "starting_page",
                },
            })],
        },
        {
            "content": "已打开 B 站。",
            **_quit_call("{}"),
        },
    ])
    model_messages = []
    executed = []
    saved_messages = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        model_messages.append(messages)
        return next(responses)

    async def fake_execute_tool(name, arguments, *_args):
        executed.append((name, arguments))
        return json.dumps({"status": "success"})

    async def fake_save(messages, **kwargs):
        saved_messages.append(messages)

    monkeypatch.setattr(agent_core, "_call_llm", fake_call_llm)
    monkeypatch.setattr(agent_core, "_execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent_core, "_save_session_messages", fake_save)
    monkeypatch.setattr(agent_core, "_append_session_message", AsyncMock())

    result = await agent_core._run_main_agent(
        "去浏览器打开B站。",
        [],
        None,
        0,
        "db.sqlite3",
        persist_user_message=False,
    )

    assert result == "已打开 B 站。"
    assert [arguments["operation"] for _name, arguments in executed] == [
        "describe",
        "invoke",
    ]
    assert "did not call a tool" in model_messages[3][-1]["content"]
    persisted_text = [
        message.get("content")
        for message in saved_messages[-1]
        if message.get("role") == "assistant"
    ]
    assert "正在打开浏览器访问 B 站。" not in persisted_text
    assert "已打开 B 站。" in persisted_text


async def test_quit_turn_persists_normal_assistant_content(monkeypatch):
    """A terminal answer remains visible in history without a dangling tool call."""
    from cyrene.agent import agent as agent_core
    from cyrene.call_llm import _sanitize_messages_for_llm

    saved_messages = []

    async def fake_call_llm(messages, tools=None, max_tokens=32000, **kwargs):
        return {
            "content": "上一轮已经回答",
            "tool_calls": [
                {
                    "id": "q1",
                    "function": {
                        "name": "quit",
                        "arguments": "{}",
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
