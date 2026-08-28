import sys
import os
import asyncio
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))



def test_workbench_init_task_plan_normalizes_llm_payload():
    from cyrene.workbench.task_initialization_runtime import _workbench_coerce_init_task_plan

    fallback = [{"title": "fallback", "goal": "fallback", "priority": "medium"}]
    plan = _workbench_coerce_init_task_plan(
        {
            "tasks": [
                {
                    "title": "  明确 MVP 范围  ",
                    "goal": "确定首版目标",
                    "priority": "urgent",
                    "constraints": ["  只做 Web 端  ", ""],
                    "acceptanceCriteria": ["范围已确认", ""],
                },
                {"description": "补齐登录注册流程"},
                {"title": ""},
            ]
        },
        fallback,
    )

    assert len(plan) == 2
    assert plan[0]["title"] == "明确 MVP 范围"
    assert plan[0]["priority"] == "medium"
    assert plan[0]["constraints"] == ["只做 Web 端"]
    assert plan[0]["acceptanceCriteria"] == ["范围已确认"]
    assert plan[1]["title"] == "补齐登录注册流程"


def test_workbench_init_tool_creates_task_sessions_from_major_plan():
    from cyrene.workbench.task_initialization_runtime import _workbench_create_sessions_from_init_plan

    project = {"id": "project_1", "sessions": [{"id": "init_1", "kind": "init"}]}
    created = _workbench_create_sessions_from_init_plan(
        project,
        [
            {
                "title": "明确范围",
                "goal": "整理需求边界",
                "priority": "high",
                "constraints": ["范围限制：不做移动端"],
                "acceptanceCriteria": ["需求边界已确认"],
            },
            {"title": "实现核心功能", "goal": "交付 MVP", "priority": "medium"},
        ],
        "2026-06-11T00:00:00+00:00",
    )

    assert [session["title"] for session in created] == ["明确范围", "实现核心功能"]
    assert project["sessions"][0]["title"] == "明确范围"
    assert project["sessions"][1]["title"] == "实现核心功能"
    assert project["sessions"][2]["id"] == "init_1"
    assert created[0]["kind"] == "task"
    assert created[0]["priority"] == "high"
    assert created[0]["constraints"] == ["范围限制：不做移动端"]
    assert created[0]["acceptanceCriteria"][0]["text"] == "需求边界已确认"
    assert created[0]["events"][0]["type"] == "CreatedFromInitPlan"


def test_workbench_follow_up_seed_uses_current_task_state():
    from cyrene.workbench.planning_runtime import _workbench_follow_up_seed

    seed = _workbench_follow_up_seed({
        "title": "修复登录流程",
        "goal": "让用户可以稳定登录",
        "status": "failed",
        "priority": "high",
        "summary": {"text": "接口已完成，浏览器回归仍失败"},
        "constraints": ["不要修改认证协议"],
        "plan": [
            {"title": "实现登录接口", "status": "completed"},
            {"title": "修复浏览器回归", "status": "failed"},
        ],
        "acceptanceCriteria": [
            {"text": "接口测试通过", "status": "passed"},
            {"text": "浏览器登录成功", "status": "failed"},
        ],
        "reflection": {"packet": {"next_step": "检查登录页事件处理"}},
    })

    assert seed["title"] == "修复登录流程 · Follow-up"
    assert seed["priority"] == "high"
    assert seed["constraints"] == ["不要修改认证协议"]
    assert "Current source-task status: failed" in seed["goal"]
    assert "Unresolved steps: 修复浏览器回归" in seed["goal"]
    assert "Unmet acceptance criteria: 浏览器登录成功" in seed["goal"]
    assert "Next step suggested by reflection: 检查登录页事件处理" in seed["goal"]
    assert seed["unresolvedAcceptance"] == ["浏览器登录成功"]


def test_workbench_follow_up_seed_keeps_explicit_request_with_source_context():
    from cyrene.workbench.planning_runtime import _workbench_follow_up_seed

    seed = _workbench_follow_up_seed(
        {
            "title": "整理发布说明",
            "goal": "输出版本发布说明",
            "status": "completed",
            "constraints": [],
        },
        requested_title="补充英文版本",
        requested_goal="另外制作一份英文发布说明",
    )

    assert seed["title"] == "补充英文版本"
    assert "Follow-up request: 另外制作一份英文发布说明" in seed["goal"]
    assert "Source task goal: 输出版本发布说明" in seed["goal"]
    assert "Current source-task status: completed" in seed["goal"]


def test_workbench_plan_revision_preserves_existing_steps_when_feedback_is_supplemental():
    from cyrene.workbench.planning_runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [
        _workbench_new_plan_step("读取项目上下文", "理解当前实现", 1, "task_1"),
        _workbench_new_plan_step("执行验证", "运行相关检查", 2, "task_1"),
    ]
    generated = [
        _workbench_new_plan_step("使用 torch 环境执行验证", "通过 conda run -n torch 运行检查", 1, "task_1"),
    ]

    merged = _workbench_reconcile_revised_plan(existing, generated, "你可以用 conda 环境 torch")

    assert [step["title"] for step in merged] == ["读取项目上下文", "执行验证", "使用 torch 环境执行验证"]
    assert merged[0]["id"] == existing[0]["id"]
    assert merged[2]["status"] == "pending"


def test_workbench_plan_graph_rejects_cycles_missing_dependencies_and_invalid_order():
    from cyrene.workbench.planning_runtime import _workbench_validate_plan_graph

    valid, _, code = _workbench_validate_plan_graph([
        {"id": "a", "title": "A", "dependsOn": []},
        {"id": "b", "title": "B", "dependsOn": ["a"]},
    ])
    assert valid is True
    assert code == ""

    valid, _, code = _workbench_validate_plan_graph([
        {"id": "a", "title": "A", "dependsOn": ["missing"]},
    ])
    assert valid is False
    assert code == "missing_dependency"

    valid, _, code = _workbench_validate_plan_graph([
        {"id": "b", "title": "B", "dependsOn": ["a"]},
        {"id": "a", "title": "A", "dependsOn": []},
    ])
    assert valid is False
    assert code == "dependency_order"

    valid, _, code = _workbench_validate_plan_graph([
        {"id": "a", "title": "A", "dependsOn": ["b"]},
        {"id": "b", "title": "B", "dependsOn": ["a"]},
    ], require_dependency_order=False)
    assert valid is False
    assert code == "dependency_cycle"


def test_workbench_plan_coercion_resolves_dependency_indexes():
    from cyrene.workbench.planning_runtime import _workbench_coerce_plan_steps

    steps = _workbench_coerce_plan_steps(
        {
            "steps": [
                {"title": "读取上下文", "dependsOnStepIndexes": []},
                {"title": "实现功能", "dependsOnStepIndexes": [1]},
                {"title": "运行测试", "dependsOnStepIndexes": [1, 2, 9]},
            ]
        },
        {"id": "task_1"},
    )

    assert steps[0]["dependsOn"] == []
    assert steps[1]["dependsOn"] == [steps[0]["id"]]
    assert steps[2]["dependsOn"] == [steps[0]["id"], steps[1]["id"]]


def test_workbench_plan_revision_drops_only_invalid_dependency_edges():
    from cyrene.workbench.planning_runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [
        _workbench_new_plan_step("A", "", 1, "task_1"),
        _workbench_new_plan_step("B", "", 2, "task_1"),
        _workbench_new_plan_step("C", "", 3, "task_1"),
    ]
    existing[1]["dependsOn"] = [existing[0]["id"]]
    existing[2]["dependsOn"] = [existing[1]["id"]]
    generated = [
        _workbench_new_plan_step("B", "move first", 1, "task_1"),
        _workbench_new_plan_step("A", "move second", 2, "task_1"),
        _workbench_new_plan_step("C", "keep last", 3, "task_1"),
    ]
    generated[0]["sourceStepId"] = existing[1]["id"]
    generated[1]["sourceStepId"] = existing[0]["id"]
    generated[2]["sourceStepId"] = existing[2]["id"]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "把 B 移到 A 前面", operation="revise"
    )

    assert [step["title"] for step in merged] == ["B", "A", "C"]
    assert merged[0]["dependsOn"] == []
    assert merged[2]["dependsOn"] == [existing[1]["id"]]


def test_workbench_plan_revision_allows_explicit_replacement():
    from cyrene.workbench.planning_runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [_workbench_new_plan_step("旧计划", "", 1, "task_1")]
    generated = [_workbench_new_plan_step("新计划", "", 1, "task_1")]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "重新规划，替换原计划", operation="replace"
    )

    assert [step["title"] for step in merged] == ["新计划"]


def test_workbench_agent_selected_replacement_does_not_append_old_plan():
    from cyrene.workbench.planning_runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [_workbench_new_plan_step("旧计划", "", 1, "task_1")]
    generated = [_workbench_new_plan_step("完全不同的新计划", "", 1, "task_1")]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "换一种做法", operation="replace"
    )

    assert [step["title"] for step in merged] == ["完全不同的新计划"]


def test_workbench_revision_preserves_matching_step_identity_and_progress():
    from cyrene.workbench.planning_runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [
        _workbench_new_plan_step("实现接口", "旧描述", 1, "task_1"),
        _workbench_new_plan_step("运行测试", "旧描述", 2, "task_1"),
    ]
    existing[0]["status"] = "completed"
    existing[0]["progressEvents"] = [{"body": "done"}]
    generated = [
        _workbench_new_plan_step("实现接口", "更新描述", 1, "task_1"),
        _workbench_new_plan_step("运行测试", "更新描述", 2, "task_1"),
        _workbench_new_plan_step("发布版本", "新增步骤", 3, "task_1"),
    ]
    generated[0]["sourceStepId"] = existing[0]["id"]
    generated[1]["sourceStepId"] = existing[1]["id"]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "增加发布步骤", operation="revise"
    )

    assert merged[0]["id"] == existing[0]["id"]
    assert merged[0]["status"] == "completed"
    assert merged[0]["progressEvents"] == [{"body": "done"}]
    assert merged[0]["description"] == "更新描述"
    assert merged[2]["title"] == "发布版本"


def test_workbench_repeated_partial_revisions_cannot_grow_plan_unbounded():
    from cyrene.workbench.planning_runtime import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [
        _workbench_new_plan_step(f"旧步骤 {index}", "", index, "task_1")
        for index in range(1, 13)
    ]
    generated = [_workbench_new_plan_step("额外步骤", "", 1, "task_1")]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "再补充一步", operation="revise"
    )

    assert len(merged) == 12


def test_workbench_acceptance_normalizes_agent_payload_and_resets_status():
    from cyrene.workbench.planning_runtime import _workbench_coerce_acceptance_criteria

    criteria = _workbench_coerce_acceptance_criteria(
        {
            "acceptanceCriteria": [
                "登录接口返回有效会话",
                {"text": "登录失败时显示明确错误"},
                "",
            ]
        },
        [],
    )

    assert [item["text"] for item in criteria] == [
        "登录接口返回有效会话",
        "登录失败时显示明确错误",
    ]
    assert all(item["status"] == "pending" for item in criteria)


def test_workbench_json_parser_skips_stray_braces_before_valid_object():
    from cyrene.workbench.task_initialization_runtime import _workbench_parse_json_object

    parsed = _workbench_parse_json_object(
        '说明里有一个无效片段 {not json}，最终结果是 '
        '{"results": [{"id": "a1", "passed": true}], "reason": "ok"} 后续文字'
    )

    assert parsed["results"][0]["id"] == "a1"
    assert parsed["reason"] == "ok"


def test_workbench_json_parser_does_not_accept_nested_object_from_malformed_outer_json():
    from cyrene.workbench.task_initialization_runtime import _workbench_parse_json_object

    parsed = _workbench_parse_json_object(
        '{"results": [{"id": "a1", "passed": true}], "reason": "ok",}'
    )

    assert parsed is None


def test_workbench_file_changes_from_write_and_edit_events(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_file_changes_from_tool_event

    write_changes = _workbench_file_changes_from_tool_event(
        {"tool": "Write", "args": {"path": str(tmp_path / "notes.md")}, "result": ""},
        tmp_path,
    )
    edit_changes = _workbench_file_changes_from_tool_event(
        {"tool": "Edit", "args": {"path": str(tmp_path / "src/app.py")}, "result": ""},
        tmp_path,
    )

    assert write_changes[0]["path"] == "notes.md"
    assert write_changes[0]["status"] == "created/updated"
    assert edit_changes[0]["path"] == "src/app.py"
    assert edit_changes[0]["status"] == "modified"


def test_workbench_file_changes_parse_tool_result_fallback(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_file_changes_from_tool_event

    changes = _workbench_file_changes_from_tool_event(
        {"tool": "custom_write", "args": {}, "result": f"Wrote {tmp_path / 'out.txt'}"},
        tmp_path,
    )

    assert changes[0]["path"] == "out.txt"
    assert changes[0]["status"] == "created/updated"


def test_workbench_file_changes_reject_paths_outside_workspace(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_file_changes_from_tool_event

    outside = tmp_path.parent / "outside.md"
    absolute = _workbench_file_changes_from_tool_event(
        {"tool": "Write", "args": {"path": str(outside)}, "result": ""},
        tmp_path,
    )
    traversal = _workbench_file_changes_from_tool_event(
        {"tool": "Write", "args": {"path": "../outside.md"}, "result": ""},
        tmp_path,
    )

    assert absolute == []
    assert traversal == []


def test_workbench_file_changes_reject_cyrene_managed_run_state(tmp_path):
    from cyrene.workbench.artifact_runtime import (
        _workbench_file_changes_from_tool_event,
        _workbench_git_status_delta,
        _workbench_workspace_file_snapshot,
        _workbench_workspace_text_snapshot,
    )

    cyrene_root = tmp_path / ".cyrene"
    (cyrene_root / "conversations").mkdir(parents=True)
    (cyrene_root / "conversations" / "wbchat_1.md").write_text("chat\n", encoding="utf-8")
    (cyrene_root / "plan").mkdir()
    (cyrene_root / "plan" / "plan_1.md").write_text("plan\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    assert _workbench_file_changes_from_tool_event(
        {"tool": "Write", "args": {"path": ".cyrene/conversations/wbchat_1.md"}, "result": ""},
        tmp_path,
    ) == []
    assert _workbench_git_status_delta(
        {},
        {".cyrene/conversations/wbchat_1.md": "??", ".cyrene/plan/plan_1.md": "??", "app.py": "??"},
        tmp_path,
    )[0]["path"] == "app.py"
    # The snapshot walks skip dot directories, so .cyrene is invisible.
    assert set(_workbench_workspace_file_snapshot(tmp_path)) == {"app.py"}
    assert set(_workbench_workspace_text_snapshot(tmp_path)) == {"app.py"}


def test_workbench_git_status_snapshot_is_scoped_to_nested_workspace(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_git_status_snapshot

    repo = tmp_path / "repo"
    workspace = repo / "workspace"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "outside.txt").write_text("before\n", encoding="utf-8")
    (workspace / "inside.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "outside.txt", "workspace/inside.txt"], cwd=repo, check=True, capture_output=True)
    (repo / "outside.txt").write_text("after\n", encoding="utf-8")
    (workspace / "inside.txt").write_text("after\n", encoding="utf-8")

    assert _workbench_git_status_snapshot(workspace) == {"inside.txt": "AM"}


def test_workbench_git_status_delta_and_step_related_files():
    from cyrene.workbench.artifact_runtime import _workbench_apply_step_file_changes, _workbench_git_status_delta

    changes = _workbench_git_status_delta({"old.py": " M"}, {"old.py": " M", "new.py": "??", "app.py": " M"})
    assert [(item["path"], item["status"]) for item in changes] == [("new.py", "created"), ("app.py", "modified")]

    session = {"plan": [{"id": "s1", "relatedFiles": [{"path": "old.py", "status": "modified"}]}]}
    _workbench_apply_step_file_changes(session, "s1", changes)
    assert [item["path"] for item in session["plan"][0]["relatedFiles"]] == ["old.py", "new.py", "app.py"]


def test_workbench_workspace_snapshot_detects_named_shell_output(tmp_path):
    from cyrene.workbench.artifact_runtime import (
        _workbench_workspace_file_snapshot,
        _workbench_workspace_snapshot_delta,
        _workbench_workspace_text_snapshot,
    )

    before = _workbench_workspace_file_snapshot(tmp_path)
    before_text = _workbench_workspace_text_snapshot(tmp_path)
    output = tmp_path / "exports" / "report.pdf"
    output.parent.mkdir()
    output.write_bytes(b"%PDF-1.7\n")
    scratch = tmp_path / "scratch.tmp"
    scratch.write_text("temporary", encoding="utf-8")
    after = _workbench_workspace_file_snapshot(tmp_path)
    after_text = _workbench_workspace_text_snapshot(tmp_path)

    changes = _workbench_workspace_snapshot_delta(
        before,
        after,
        "已生成 exports/report.pdf，可直接交付。",
        before_text=before_text,
        after_text=after_text,
    )
    by_path = {item["path"]: item for item in changes}
    assert by_path["exports/report.pdf"]["status"] == "produced"
    assert by_path["exports/report.pdf"]["source"] == "workspace_output"
    assert by_path["scratch.tmp"]["status"] == "created"
    assert by_path["scratch.tmp"]["source"] == "workspace"
    assert "+temporary" in by_path["scratch.tmp"]["diff"]


def test_workbench_workspace_snapshot_delta_records_text_diffs_without_git(tmp_path):
    from cyrene.workbench.artifact_runtime import (
        _workbench_merge_file_changes,
        _workbench_recorded_diff_for_path,
        _workbench_workspace_file_snapshot,
        _workbench_workspace_snapshot_delta,
        _workbench_workspace_text_snapshot,
    )

    existing = tmp_path / "notes.md"
    existing.write_text("old line\nkeep\n", encoding="utf-8")
    removed = tmp_path / "old.txt"
    removed.write_text("remove me\n", encoding="utf-8")
    before = _workbench_workspace_file_snapshot(tmp_path)
    before_text = _workbench_workspace_text_snapshot(tmp_path)

    existing.write_text("new line\nkeep\n", encoding="utf-8")
    created = tmp_path / "created.md"
    created.write_text("# Created\n", encoding="utf-8")
    removed.unlink()
    after = _workbench_workspace_file_snapshot(tmp_path)
    after_text = _workbench_workspace_text_snapshot(tmp_path)

    changes = _workbench_workspace_snapshot_delta(
        before,
        after,
        "",
        before_text=before_text,
        after_text=after_text,
    )
    by_path = {item["path"]: item for item in changes}
    assert by_path["notes.md"]["status"] == "modified"
    assert "-old line" in by_path["notes.md"]["diff"]
    assert "+new line" in by_path["notes.md"]["diff"]
    assert by_path["created.md"]["status"] == "created"
    assert "--- /dev/null" in by_path["created.md"]["diff"]
    assert "+# Created" in by_path["created.md"]["diff"]
    assert by_path["old.txt"]["status"] == "deleted"
    assert "-remove me" in by_path["old.txt"]["diff"]
    assert "+++ /dev/null" in by_path["old.txt"]["diff"]

    merged = _workbench_merge_file_changes([
        {"path": "notes.md", "status": "modified", "source": "Edit"},
        by_path["notes.md"],
    ])
    assert merged[0]["source"] == "Edit"
    assert "+new line" in merged[0]["diff"]

    recorded = _workbench_recorded_diff_for_path({"runs": [{"fileChanges": merged}]}, "notes.md", tmp_path)
    assert recorded is not None
    assert recorded["source"] == "workspace_snapshot"
    assert "+new line" in recorded["diff"]


def test_workbench_recorded_diff_blocks_misleading_current_snapshot_fallback(tmp_path):
    from cyrene.workbench.artifact_runtime import (
        _workbench_git_diff_for_path,
        _workbench_recorded_diff_for_path,
        _workbench_workspace_file_snapshot,
        _workbench_workspace_snapshot_delta,
        _workbench_workspace_text_snapshot,
    )

    target = tmp_path / "same.md"
    target.write_text("same content\n", encoding="utf-8")
    before = _workbench_workspace_file_snapshot(tmp_path)
    before_text = _workbench_workspace_text_snapshot(tmp_path)
    stat = target.stat()
    os.utime(target, (stat.st_atime + 10, stat.st_mtime + 10))
    after = _workbench_workspace_file_snapshot(tmp_path)
    after_text = _workbench_workspace_text_snapshot(tmp_path)

    changes = _workbench_workspace_snapshot_delta(
        before,
        after,
        "",
        before_text=before_text,
        after_text=after_text,
    )
    assert changes[0]["path"] == "same.md"
    assert changes[0]["diffUnavailableReason"] == "no_text_difference"
    assert "diff" not in changes[0]

    recorded = _workbench_recorded_diff_for_path({"runs": [{"fileChanges": changes}]}, "same.md", tmp_path)
    assert recorded == {
        "path": "same.md",
        "diff": "",
        "has_changes": False,
        "source": "no_text_difference",
        "reason": "no_text_difference",
    }

    fallback = asyncio.run(_workbench_git_diff_for_path(tmp_path, "same.md"))
    assert fallback["source"] == "snapshot"
    assert "+same content" in fallback["diff"]


import pytest


@pytest.mark.asyncio
async def test_workbench_git_diff_for_tracked_and_untracked_files(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_git_diff_for_path

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    tracked = tmp_path / "app.py"
    tracked.write_text("print('old')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True)
    tracked.write_text("print('new')\n", encoding="utf-8")

    tracked_diff = await _workbench_git_diff_for_path(tmp_path, "app.py")
    assert tracked_diff["path"] == "app.py"
    assert "-print('old')" in tracked_diff["diff"]
    assert "+print('new')" in tracked_diff["diff"]

    untracked = tmp_path / "notes.md"
    untracked.write_text("# Notes\n", encoding="utf-8")
    untracked_diff = await _workbench_git_diff_for_path(tmp_path, "notes.md")
    assert untracked_diff["path"] == "notes.md"
    assert "--- /dev/null" in untracked_diff["diff"]
    assert "+++ b/notes.md" in untracked_diff["diff"]
    assert "+# Notes" in untracked_diff["diff"]

    staged = tmp_path / "staged.md"
    staged.write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged.md"], cwd=tmp_path, check=True, capture_output=True)
    staged_diff = await _workbench_git_diff_for_path(tmp_path, "staged.md")
    assert staged_diff["source"] == "git"
    assert "+++ b/staged.md" in staged_diff["diff"]
    assert "+staged" in staged_diff["diff"]


@pytest.mark.asyncio
async def test_workbench_file_diff_falls_back_to_current_text_snapshot(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_git_diff_for_path

    report = tmp_path / "report.tex"
    report.write_text("\\section{Result}\n", encoding="utf-8")

    no_git_diff = await _workbench_git_diff_for_path(tmp_path, "report.tex")
    assert no_git_diff["source"] == "snapshot"
    assert no_git_diff["has_changes"] is True
    assert "--- /dev/null" in no_git_diff["diff"]
    assert "+++ b/report.tex" in no_git_diff["diff"]
    assert "+\\section{Result}" in no_git_diff["diff"]

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "report.tex"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    clean_diff = await _workbench_git_diff_for_path(tmp_path, "report.tex")
    assert clean_diff["source"] == "snapshot"
    assert "+\\section{Result}" in clean_diff["diff"]


@pytest.mark.asyncio
async def test_workbench_git_diff_rejects_paths_outside_workspace(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_git_diff_for_path

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        await _workbench_git_diff_for_path(tmp_path, outside)


def test_workbench_generation_error_redacts_credentials():
    from cyrene.workbench.task_initialization_runtime import _workbench_generation_error

    error = _workbench_generation_error(
        RuntimeError("Bearer secret-token sk-abcdefghijkl api_key=private-value")
    )

    assert "secret-token" not in error.message
    assert "abcdefghijkl" not in error.message
    assert "private-value" not in error.message
    assert error.message == "The model request failed unexpectedly."


def test_workbench_promote_file_artifacts_promotes_and_dedups():
    from cyrene.workbench.artifact_runtime import _workbench_promote_file_artifacts

    session = {"artifacts": [
        {"id": "a1", "type": "task_brief", "name": "task-brief.md", "status": "draft"},
        {"id": "a2", "type": "knowledge_document", "name": "archived.md", "status": "indexed"},
    ]}
    changes = [
        {"path": "report.md", "status": "produced", "source": "workspace_output"},
        {"path": "existing.py", "status": "modified", "source": "Edit"},
        {"path": "inferred.txt", "status": "created", "source": "git"},
        {"path": "export.pdf", "status": "produced", "source": "send_file"},
        {"path": "shell.pdf", "status": "produced", "source": "workspace_output"},
        {"path": "report.md", "status": "created/updated", "source": "Write"},
    ]
    added = _workbench_promote_file_artifacts(session, changes, "2026-06-14T00:00:00Z")

    assert added == 3
    file_arts = [a for a in session["artifacts"] if a["type"] == "file_change"]
    by_name = {a["name"]: a for a in file_arts}
    assert set(by_name) == {"report.md", "export.pdf", "shell.pdf"}
    assert by_name["report.md"]["status"] == "ready"
    assert by_name["export.pdf"]["status"] == "ready"
    assert by_name["shell.pdf"]["status"] == "ready"
    assert by_name["report.md"]["source"] == "workspace_output"
    assert all(a["type"] == "file_change" for a in session["artifacts"])

    # idempotent: re-running adds nothing
    assert _workbench_promote_file_artifacts(session, changes, "2026-06-14T01:00:00Z") == 0


def test_workbench_promote_file_artifacts_pins_attachment_copy(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_promote_file_artifacts

    (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
    session = {"artifacts": []}
    changes = [{
        "path": "report.md",
        "status": "produced",
        "source": "workspace_output",
        "diff": "--- /dev/null\n+++ b/report.md\n@@ -0,0 +1 @@\n+# Report\n",
        "diffSource": "workspace_snapshot",
        # send_file's tool event pins the durable webui_exports copy. The
        # public attachment payload carries an id (the exported filename) but
        # no path — mirror the real build_public_attachment_payload shape.
        "attachment": {
            "id": "report_f1a2b3c4d5.md",
            "name": "report.md",
            "content_type": "text/markdown",
            "size": 11,
            "kind": "file",
            "url": "/api/chat/export/report_f1a2b3c4d5.md",
        },
    }]

    assert _workbench_promote_file_artifacts(session, changes, "2026-06-14T00:00:00Z") == 1
    artifact = session["artifacts"][0]
    # No deliverables/ dir anymore: the artifact keeps the Agent-verified
    # relative path and pins the durable webui_exports copy for download.
    assert artifact["path"] == "report.md"
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# Report\n"
    assert artifact["attachment"]["id"] == "report_f1a2b3c4d5.md"
    assert artifact["attachment"].get("path") is None
    # Diff headers keep the source path since no copy is made.
    assert "+++ b/report.md" in artifact["diff"]
    assert "deliverables" not in artifact["diff"]


def test_workbench_promote_file_artifacts_attachment_flows_from_tool_event():
    """send_file's real tool-event result pins the attachment on the change."""
    from cyrene.workbench.artifact_runtime import _workbench_file_changes_from_tool_event

    changes = _workbench_file_changes_from_tool_event(
        {
            "tool": "send_file",
            "args": {"path": "report.md"},
            "result": '{"status": "sent", "attachment": {"id": "report_abc123.md", "name": "report.md", "url": "/api/chat/export/report_abc123.md"}}',
        },
        None,
    )

    assert len(changes) == 1
    assert changes[0]["path"] == "report.md"
    assert changes[0]["status"] == "produced"
    assert changes[0]["attachment"]["id"] == "report_abc123.md"


def test_workbench_promote_file_artifacts_no_attachment_keeps_workspace_path(tmp_path):
    from cyrene.workbench.artifact_runtime import _workbench_promote_file_artifacts

    (tmp_path / "report.md").write_text("# Report\n", encoding="utf-8")
    session = {"artifacts": []}
    changes = [{
        "path": "report.md",
        "status": "produced",
        "source": "workspace_output",
        "diff": "--- /dev/null\n+++ b/report.md\n@@ -0,0 +1 @@\n+# Report\n",
        "diffSource": "workspace_snapshot",
    }]

    assert _workbench_promote_file_artifacts(session, changes, "2026-06-14T00:00:00Z") == 1
    artifact = session["artifacts"][0]
    assert artifact["path"] == "report.md"
    assert "attachment" not in artifact


def test_workbench_final_artifact_file_changes_use_declared_artifacts_only():
    from cyrene.workbench.artifact_runtime import _workbench_final_artifact_file_changes

    session = {
        "runs": [{"fileChanges": [
            {"path": "analysis.py", "status": "created", "source": "Write"},
            {"path": "report.tex", "status": "created", "source": "Write"},
        ]}],
        "artifacts": [{
            "id": "artifact_pdf",
            "type": "file_change",
            "name": "report.pdf",
            "path": "deliverables/report.pdf",
            "status": "ready",
            "source": "send_file",
        }],
    }

    changes = _workbench_final_artifact_file_changes(session)

    assert changes == [{
        "path": "deliverables/report.pdf",
        "status": "produced",
        "source": "send_file",
    }]


def test_workbench_prunes_non_file_and_duplicate_artifacts():
    from cyrene.workbench.artifact_runtime import _workbench_prune_non_file_artifacts

    session = {"artifacts": [
        {"id": "brief", "type": "task_brief", "name": "task-brief.md"},
        {"id": "file-1", "type": "file_change", "name": "report.md", "path": "out/report.md"},
        {"id": "file-2", "type": "file_change", "name": "report.md", "path": "out/report.md"},
        {"id": "test", "type": "file_change", "name": "test_render.md", "path": "test_render.md"},
        {"id": "knowledge", "type": "knowledge_document", "name": "archived.md"},
    ]}

    assert _workbench_prune_non_file_artifacts(session) is True
    assert session["artifacts"] == [
        {"id": "file-1", "type": "file_change", "name": "report.md", "path": "out/report.md"},
        {"id": "test", "type": "file_change", "name": "test_render.md", "path": "test_render.md"},
    ]
