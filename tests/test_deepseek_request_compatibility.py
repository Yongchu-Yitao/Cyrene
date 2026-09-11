"""Regression coverage for DeepSeek thinking-mode request constraints."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json

import httpx
import pytest

from cyrene.core.context.projection import project_model_messages
from cyrene.core.plugin import PluginContext
from cyrene.core.plugin.core_impl.permission import (
    PERMISSION_DECIDE_TOOL,
    PERMISSION_DECIDE_TOOL_CHOICE,
)
from cyrene.plugins.builtin.cyrene_model._shared import _openai_payload
from cyrene.plugins.builtin.cyrene_model.deepseek import DEEPSEEK_PLUGIN, DEEPSEEK_PROVIDER
from cyrene.plugins.builtin.cyrene_model.deepseek_requests import restore_reasoning
from cyrene.plugins.builtin.cyrene_model.openai_compatible import OPENAI_COMPATIBLE_PROVIDER


def payload(messages, choice="auto", provider=DEEPSEEK_PROVIDER):
    return _openai_payload(
        {"messages": messages, "tools": [PERMISSION_DECIDE_TOOL],
         "tool_choice": choice, "reasoning_effort": "high"},
        provider, "deepseek-flash",
    )


@pytest.mark.parametrize("choice", [PERMISSION_DECIDE_TOOL_CHOICE, "required"])
def test_permission_keeps_forced_tool_and_disables_thinking(choice):
    result = payload([{"role": "user", "content": "Review this action"}], choice)
    assert result["tool_choice"] == choice
    assert result["tools"] == [PERMISSION_DECIDE_TOOL]
    assert result["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in result


@pytest.mark.parametrize("calls", [[], [{"id": "c1", "name": "decide", "arguments": {}}]])
def test_projected_reasoning_survives_next_deepseek_request(calls):
    values = [
        {"role": "user", "content": "Review", "run_id": "r1"},
        {"role": "assistant", "content": "Checking", "reasoning": "Original reasoning",
         "reasoning_details": [{"type": "reasoning.text", "text": "Original reasoning"}],
         "tool_calls": calls, "run_id": "r1", "model": "deepseek-flash"},
    ]
    original = deepcopy(values)
    nodes = [
        SimpleNamespace(id=str(i), value=value) for i, value in enumerate(values)
    ]
    messages = project_model_messages(nodes)
    context = PluginContext(tree=SimpleNamespace(get_path=lambda *_: nodes),
                            tree_id="current", node_id="leaf")
    result = payload(restore_reasoning(messages, context, "deepseek-flash"))
    assert result["messages"][1]["reasoning_content"] == "Original reasoning"
    assert result["reasoning_effort"] == "high"
    assert "thinking" not in result
    assert values == original


@pytest.mark.parametrize("case", ["ambiguous", "foreign", "transformed", "inactive_task"])
def test_recovery_does_not_borrow_unrelated_reasoning(case):
    value = {"role": "assistant", "content": "Answer", "reasoning": "Original",
             "model": "deepseek-flash"}
    nodes = [SimpleNamespace(value={"role": "system"}), SimpleNamespace(value=value)]
    content = "Answer"
    if case == "ambiguous":
        nodes.append(SimpleNamespace(value={**value, "reasoning": "Different"}))
    elif case == "foreign":
        value["model"] = "another-model"
    elif case == "transformed":
        content = "Summary of Answer"
    else:
        nodes[0].value["_task_contexts"] = {"active": "task-b"}
        value["task_context_id"] = "task-a"
    context = PluginContext(tree=SimpleNamespace(get_path=lambda *_: nodes),
                            tree_id="current", node_id="leaf")
    messages = [{"role": "assistant", "content": content}]
    restored = restore_reasoning(messages, context, "deepseek-flash")
    assert "reasoning_content" not in restored[0]
    assert payload(restored)["thinking"] == {"type": "disabled"}


def test_empty_original_reasoning_is_preserved_and_native_details_are_removed():
    result = payload([{"role": "assistant", "content": "Answer", "reasoning_content": "",
                       "reasoning_details": [{"type": "reasoning.text", "text": "Other"}]}])
    assert result["messages"][0]["reasoning_content"] == ""
    assert "reasoning_details" not in result["messages"][0]
    assert "thinking" not in result


@pytest.mark.parametrize("reasoning", [None, "absent"])
def test_legacy_or_foreign_history_without_reasoning_uses_nonthinking(reasoning):
    assistant = {"role": "assistant", "content": "Earlier answer"}
    if reasoning is None:
        assistant["reasoning_content"] = None
    messages = [{"role": "user", "content": "Review"}, assistant]
    original = deepcopy(messages)
    result = payload(messages)
    assert result["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in result
    assert messages == original


def test_first_turn_and_other_providers_keep_reasoning_configuration():
    result = payload([{"role": "user", "content": "Hello"}])
    assert result["reasoning_effort"] == "high"
    assert "thinking" not in result
    other = payload([{"role": "assistant", "content": "Hello"}],
                    PERMISSION_DECIDE_TOOL_CHOICE, OPENAI_COMPATIBLE_PROVIDER)
    assert other["reasoning_effort"] == "high"
    assert other["tool_choice"] == PERMISSION_DECIDE_TOOL_CHOICE
    assert "thinking" not in other


@pytest.mark.asyncio
async def test_real_plugin_sends_compatible_permission_and_followup_requests():
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            "id": "response-1", "model": "deepseek-flash",
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "decision-1", "type": "function", "function": {
                    "name": "decide", "arguments": '{"approve":true,"rationale":"Authorized"}',
                }}],
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    context = PluginContext(data={
        "http_transport": httpx.MockTransport(respond),
        "model_connection": {"base_url": "https://deepseek.test/v1", "api_key": "test"},
        "model_call_kind": "permission",
    })
    arguments = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "Review"}],
                 "tools": [PERMISSION_DECIDE_TOOL], "tool_choice": PERMISSION_DECIDE_TOOL_CHOICE,
                 "reasoning_effort": "high"}
    result = await DEEPSEEK_PLUGIN.handler(arguments, context)
    assert result["tool_calls"][0]["function"]["name"] == "decide"
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in requests[0]
    assert requests[0]["tool_choice"] == PERMISSION_DECIDE_TOOL_CHOICE

    nodes = [SimpleNamespace(value={"role": "assistant", "content": "Checking",
                                   "model": "deepseek-flash", "reasoning": "Exact original"})]
    context = replace(context, tree=SimpleNamespace(get_path=lambda *_: nodes),
                      tree_id="current", node_id="leaf",
                      data={**context.data, "model_call_kind": "agent"})
    await DEEPSEEK_PLUGIN.handler({**arguments, "tool_choice": "auto", "messages": [
        {"role": "user", "content": "Review"}, {"role": "assistant", "content": "Checking"},
    ]}, context)
    assert requests[1]["messages"][1]["reasoning_content"] == "Exact original"
    assert requests[1]["reasoning_effort"] == "high"
    assert "thinking" not in requests[1]
