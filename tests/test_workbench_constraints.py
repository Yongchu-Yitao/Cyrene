import asyncio


def test_constraint_extraction_uses_semantic_model_output(monkeypatch):
    from cyrene.workbench import runtime

    captured = {}

    async def fake_call_llm(messages, **kwargs):
        captured["prompt"] = messages[-1]["content"]
        captured["kwargs"] = kwargs
        return {
            "content": (
                '{"constraints":["不要修改认证协议",'
                '"仅支持 Web 端","不要修改认证协议"]}'
            )
        }

    monkeypatch.setattr(runtime, "_call_llm", fake_call_llm)

    result = asyncio.run(runtime._workbench_extract_constraints(
        "认证并不是必须重写的。请增加登录提示，不要修改认证协议；仅支持 Web 端。"
    ))

    assert result == ["不要修改认证协议", "仅支持 Web 端"]
    assert "不要因为文本出现" in captured["prompt"]
    assert captured["kwargs"]["secondary"] is True
    assert captured["kwargs"]["thinking"] == "disabled"


def test_constraint_extraction_does_not_guess_on_model_failure(monkeypatch):
    from cyrene.workbench import runtime

    async def failing_call_llm(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(runtime, "_call_llm", failing_call_llm)

    result = asyncio.run(runtime._workbench_extract_constraints("只修改 Web 端"))

    assert result == []


def test_constraint_extraction_rejects_non_list_model_output(monkeypatch):
    from cyrene.workbench import runtime

    async def fake_call_llm(*_args, **_kwargs):
        return {"content": '{"constraints":"不要修改后端"}'}

    monkeypatch.setattr(runtime, "_call_llm", fake_call_llm)

    assert asyncio.run(runtime._workbench_extract_constraints("不要修改后端")) == []


def test_plan_routing_uses_semantic_model_decision(monkeypatch):
    from cyrene.workbench import runtime

    async def fake_call_llm(messages, **_kwargs):
        prompt = messages[-1]["content"]
        assert "否定表达必须按语义理解" in prompt
        return {"content": (
            '{"workspaceRelationship":"related",'
            '"needsWorkspaceRefresh":true,"revisionMode":"revise"}'
        )}

    monkeypatch.setattr(runtime, "_call_llm", fake_call_llm)
    result = asyncio.run(runtime._workbench_classify_plan_routing(
        {"goal": "在 Cyrene 中实现旅行计划模板"},
        {"name": "Cyrene", "description": "桌面 Agent 应用"},
        feedback="不要整体重做，只核对当前实现后调整第三步",
        requested_operation="auto",
    ))

    assert result == {
        "workspaceRelationship": "related",
        "needsWorkspaceRefresh": True,
        "revisionMode": "revise",
    }


def test_plan_routing_failure_uses_conservative_defaults(monkeypatch):
    from cyrene.workbench import runtime

    async def failing_call_llm(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(runtime, "_call_llm", failing_call_llm)
    result = asyncio.run(runtime._workbench_classify_plan_routing(
        {"goal": "调整计划"}, {"name": "Cyrene"},
        feedback="换一种做法", requested_operation="auto",
    ))

    assert result["workspaceRelationship"] == "unclear"
    assert result["needsWorkspaceRefresh"] is False
    assert result["revisionMode"] == "revise"
