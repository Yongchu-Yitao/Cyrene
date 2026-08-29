from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_code_plugin_validation_messages_follow_context_language(
    tmp_path,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import git, indexer

    context = PluginContext(
        workspace=tmp_path,
        data={"language": "zh"},
        services={"code_index_db": tmp_path / "index.db"},
    )

    commit = json.loads(await git._tool_git_commit({}, context))
    symbol = json.loads(await indexer._tool_search_symbol({}, context))
    missing_path = json.loads(await indexer._tool_index_codebase(
        {"path": "missing"},
        context,
    ))

    assert commit["error"] == "必须提供提交信息。"
    assert symbol["error"] == "必须提供 name。"
    assert missing_path["error_code"] == "path_not_found"
    assert missing_path["error"] == "未找到路径：missing"


@pytest.mark.asyncio
async def test_code_review_suggestions_are_localized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import analysis

    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    context = PluginContext(workspace=tmp_path, data={"language": "zh"})

    async def fake_lint(_path, _context):
        return [{"code": "F401"}]

    async def fake_format(_path, _context, *, check_only=False):
        assert check_only is True
        return {"changed": True, "diff": "technical diff"}

    monkeypatch.setattr(analysis, "_run_ruff_check", fake_lint)
    monkeypatch.setattr(analysis, "_run_ruff_format", fake_format)
    monkeypatch.setattr(analysis, "analyze_structure", lambda _path, _context: {
        "long_functions": [{"name": "oversized"}],
    })

    result = json.loads(await analysis._tool_code_review(
        {"path": "sample.py"},
        context,
    ))

    assert result["suggestions"] == [
        "发现 1 个代码检查问题。",
        "代码需要格式化（ruff format）。",
        "过长函数（超过 50 行）：oversized",
    ]
    assert result["format_diff"] == "technical diff"


@pytest.mark.asyncio
async def test_git_unexpected_exception_is_not_returned_raw(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import git

    async def fail_to_start(*_args, **_kwargs):
        raise RuntimeError("private interpreter detail")

    monkeypatch.setattr(git.asyncio, "create_subprocess_exec", fail_to_start)
    context = PluginContext(workspace=tmp_path, data={"language": "zh"})

    result = json.loads(await git._tool_git_status({}, context))

    assert result["error"] == "无法完成 Git 命令。"
    assert "private interpreter detail" not in result["error"]


@pytest.mark.asyncio
async def test_terminal_empty_and_ambiguous_messages_are_localized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_code import list_shells
    from cyrene.plugins.builtin.cyrene_code.services import CyreneTerminalService

    service = CyreneTerminalService()

    async def no_shells(*_args, **_kwargs):
        return []

    monkeypatch.setattr(service, "list_owned", no_shells)
    monkeypatch.setattr(service, "list_visible", no_shells)
    context = PluginContext(
        data={"language": "zh"},
        services={"terminals": service},
    )

    assert await list_shells._tool_list_shells({}, context) == (
        "当前会话未绑定终端，当前分屏中也没有可见终端。"
    )

    async def multiple_terminals(*_args, **_kwargs):
        return {
            "ok": False,
            "error": "multiple_terminals_visible",
            "terminals": [
                {"terminalId": "term_a", "title": "API"},
                {"terminalId": "term_b", "title": "Worker"},
            ],
        }

    monkeypatch.setattr(
        "cyrene.plugins.builtin.cyrene_code.services._surface_request",
        multiple_terminals,
    )
    with pytest.raises(ValueError, match="请提供终端名称"):
        await service._current_terminal_id("surface-1", context)
