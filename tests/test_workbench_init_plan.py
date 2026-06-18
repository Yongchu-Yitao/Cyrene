import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_workbench_init_task_plan_normalizes_llm_payload():
    from webui.routes import _workbench_coerce_init_task_plan

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
    from webui.routes import _workbench_create_sessions_from_init_plan

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


def test_workbench_plan_revision_preserves_existing_steps_when_feedback_is_supplemental():
    from webui.routes import _workbench_new_plan_step, _workbench_reconcile_revised_plan

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


def test_workbench_plan_revision_allows_explicit_replacement():
    from webui.routes import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [_workbench_new_plan_step("旧计划", "", 1, "task_1")]
    generated = [_workbench_new_plan_step("新计划", "", 1, "task_1")]

    merged = _workbench_reconcile_revised_plan(existing, generated, "重新规划，替换原计划")

    assert [step["title"] for step in merged] == ["新计划"]


def test_workbench_agent_selected_replacement_does_not_append_old_plan():
    from webui.routes import _workbench_new_plan_step, _workbench_reconcile_revised_plan

    existing = [_workbench_new_plan_step("旧计划", "", 1, "task_1")]
    generated = [_workbench_new_plan_step("完全不同的新计划", "", 1, "task_1")]

    merged = _workbench_reconcile_revised_plan(
        existing, generated, "换一种做法", operation="replace"
    )

    assert [step["title"] for step in merged] == ["完全不同的新计划"]


def test_workbench_revision_preserves_matching_step_identity_and_progress():
    from webui.routes import _workbench_new_plan_step, _workbench_reconcile_revised_plan

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
    from webui.routes import _workbench_new_plan_step, _workbench_reconcile_revised_plan

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
    from webui.routes import _workbench_coerce_acceptance_criteria

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


async def test_workbench_plan_agent_returns_fresh_acceptance_criteria(monkeypatch, tmp_path):
    from webui import routes

    async def fake_agent(*args, **kwargs):
        return {
            "steps": [
                {"title": "实现登录接口", "description": "修改认证模块"},
                {"title": "验证登录流程", "description": "运行认证测试"},
            ],
            "acceptanceCriteria": [
                "有效账号可以登录并获得会话",
                "认证测试全部通过",
            ],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成账号登录功能",
        "constraints": [],
        "plan": [],
        "acceptanceCriteria": [{"id": "old", "text": "旧标准", "status": "passed"}],
    }
    project = {"workspacePath": str(tmp_path)}

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(session, project)

    assert from_llm is True
    assert operation == "create"
    assert [step["title"] for step in steps] == ["实现登录接口", "验证登录流程"]
    assert [item["text"] for item in acceptance] == [
        "有效账号可以登录并获得会话",
        "认证测试全部通过",
    ]
    assert all(item["status"] == "pending" for item in acceptance)


async def test_workbench_acceptance_agent_uses_current_plan(monkeypatch, tmp_path):
    from webui import routes

    captured = {}

    async def fake_agent(workspace_root, prompt, **kwargs):
        captured["prompt"] = prompt
        return {"acceptanceCriteria": ["认证模块测试通过"]}

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成账号登录功能",
        "constraints": ["仅支持邮箱登录"],
        "plan": [{"title": "实现认证模块", "description": "修改 auth.py"}],
    }
    project = {"workspacePath": str(tmp_path)}

    acceptance, from_llm = await routes._workbench_generate_acceptance_criteria(session, project)

    assert from_llm is True
    assert acceptance[0]["text"] == "认证模块测试通过"
    assert "完成账号登录功能" in captured["prompt"]
    assert "实现认证模块" in captured["prompt"]
    assert "仅支持邮箱登录" in captured["prompt"]


async def test_workbench_failed_plan_revision_preserves_existing_acceptance(monkeypatch):
    from webui import routes

    async def failed_agent(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", failed_agent)
    old_criteria = [{
        "id": "accept_existing",
        "text": "人工确认的验收标准",
        "status": "passed",
        "evidence": "tests passed",
    }]
    session = {
        "id": "task_1",
        "title": "实现登录",
        "goal": "完成账号登录功能",
        "constraints": [],
        "plan": [{"id": "step_1", "title": "实现登录", "status": "pending"}],
        "acceptanceCriteria": old_criteria,
    }

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session, {"workspacePath": ""}, feedback="补充登录测试"
    )

    assert from_llm is False
    assert operation == "revise"
    assert steps == session["plan"]
    assert acceptance == old_criteria


async def test_workbench_reconciled_plan_does_not_launch_second_acceptance_agent(monkeypatch):
    from webui import routes

    prompts = []

    async def fake_agent(workspace_root, prompt, **kwargs):
        prompts.append(prompt)
        return {
            "revisionMode": "revise",
            "steps": [{"title": "新增测试", "description": "补充测试"}],
            "acceptanceCriteria": ["新增测试通过"],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "实现功能",
        "goal": "完成核心功能",
        "constraints": [],
        "plan": [
            {"id": "step_1", "title": "实现功能", "description": "完成实现", "status": "pending"},
            {"id": "step_2", "title": "构建产物", "description": "生成包", "status": "pending"},
        ],
        "acceptanceCriteria": [],
    }

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session, {"workspacePath": ""}, feedback="补充测试"
    )

    assert from_llm is True
    assert operation == "revise"
    assert [step["title"] for step in steps] == ["实现功能", "构建产物", "新增测试"]
    assert [item["text"] for item in acceptance] == ["新增测试通过"]
    assert len(prompts) == 1


async def test_workbench_plan_agent_can_choose_full_replacement(monkeypatch):
    from webui import routes

    async def fake_agent(*args, **kwargs):
        return {
            "revisionMode": "replace",
            "steps": [
                {"sourceStepId": None, "title": "设计全新方案", "description": "重新设计"},
                {"sourceStepId": None, "title": "实现全新方案", "description": "重新实现"},
            ],
            "acceptanceCriteria": ["全新方案可运行"],
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "旧任务",
        "goal": "完成任务",
        "constraints": [],
        "plan": [
            {"id": "step_old", "title": "旧步骤", "description": "", "status": "completed"},
        ],
        "acceptanceCriteria": [],
    }

    steps, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session,
        {"workspacePath": ""},
        feedback="请生成完全不同的计划",
    )

    assert from_llm is True
    assert operation == "replace"
    assert [step["title"] for step in steps] == ["设计全新方案", "实现全新方案"]
    assert all(step["id"] != "step_old" for step in steps)
    assert [item["text"] for item in acceptance] == ["全新方案可运行"]


async def test_workbench_auto_start_acceptance_uses_derived_goal(monkeypatch):
    from webui import routes

    calls = 0

    async def fake_agent(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "goal": "修复认证模块的登录回归",
                "title": "修复登录回归",
                "steps": [{"title": "修复认证逻辑", "description": "修改认证模块"}],
            }
        return None

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_1",
        "title": "新任务",
        "goal": "",
        "constraints": [],
        "plan": [],
        "acceptanceCriteria": [],
    }

    _, acceptance, from_llm, operation = await routes._workbench_generate_plan_steps(
        session, {"workspacePath": ""}, auto_start=True
    )

    assert from_llm is True
    assert operation == "create"
    assert session["goal"] == "修复认证模块的登录回归"
    assert any("修复认证模块的登录回归" in item["text"] for item in acceptance)


async def test_workbench_verifier_uses_clean_agent_context(monkeypatch, tmp_path):
    from webui import routes

    captured = {}

    async def fake_agent(workspace_root, prompt, **kwargs):
        captured["workspace_root"] = workspace_root
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "results": [{"id": "accept_1", "passed": True, "evidence": "测试通过"}],
            "recommend_reflection": False,
            "reason": "全部通过",
        }

    monkeypatch.setattr(routes, "_workbench_run_explore_agent", fake_agent)
    session = {
        "id": "task_session_1",
        "title": "实现登录",
        "goal": "完成账号登录",
        "acceptanceCriteria": [
            {"id": "accept_1", "text": "登录测试通过", "status": "pending"}
        ],
    }

    verdict = await routes._workbench_verify_acceptance(
        session, {"workspacePath": str(tmp_path)}
    )

    assert verdict["results"][0]["passed"] is True
    assert captured["kwargs"]["session_id"] == "task_session_1"
    assert captured["kwargs"]["clean_context"] is True
    assert "上下文完全独立" in captured["prompt"]
    assert "不得依赖任务执行 Agent 的对话" in captured["prompt"]


async def test_workbench_clean_explore_agent_clears_inherited_session(monkeypatch):
    from cyrene.agent.state import _current_session_id
    from webui import routes

    seen_session_ids = []

    async def fake_llm(*args, **kwargs):
        seen_session_ids.append(_current_session_id.get())
        return {"content": '{"ok": true}', "tool_calls": []}

    monkeypatch.setattr(routes, "_call_llm", fake_llm)
    outer_token = _current_session_id.set("dirty_execution_session")
    try:
        result = await routes._workbench_run_explore_agent(
            None,
            "return json",
            session_id="task_session_1",
            clean_context=True,
        )
        assert result == {"ok": True}
        assert seen_session_ids == [""]
        assert _current_session_id.get() == "dirty_execution_session"
    finally:
        _current_session_id.reset(outer_token)


def test_workbench_file_changes_from_write_and_edit_events(tmp_path):
    from webui.routes import _workbench_file_changes_from_tool_event

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
    from webui.routes import _workbench_file_changes_from_tool_event

    changes = _workbench_file_changes_from_tool_event(
        {"tool": "custom_write", "args": {}, "result": f"Wrote {tmp_path / 'out.txt'}"},
        tmp_path,
    )

    assert changes[0]["path"] == "out.txt"
    assert changes[0]["status"] == "created/updated"


def test_workbench_git_status_delta_and_step_related_files():
    from webui.routes import _workbench_apply_step_file_changes, _workbench_git_status_delta

    changes = _workbench_git_status_delta({"old.py": " M"}, {"old.py": " M", "new.py": "??", "app.py": " M"})
    assert [(item["path"], item["status"]) for item in changes] == [("new.py", "created"), ("app.py", "modified")]

    session = {"plan": [{"id": "s1", "relatedFiles": [{"path": "old.py", "status": "modified"}]}]}
    _workbench_apply_step_file_changes(session, "s1", changes)
    assert [item["path"] for item in session["plan"][0]["relatedFiles"]] == ["old.py", "new.py", "app.py"]


import pytest


@pytest.mark.asyncio
async def test_workbench_git_diff_for_tracked_and_untracked_files(tmp_path):
    from webui.routes import _workbench_git_diff_for_path

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


@pytest.mark.asyncio
async def test_workbench_git_diff_rejects_paths_outside_workspace(tmp_path):
    from webui.routes import _workbench_git_diff_for_path

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError):
        await _workbench_git_diff_for_path(tmp_path, outside)


@pytest.mark.asyncio
async def test_workbench_init_task_plan_reports_llm_success(monkeypatch):
    from webui import routes as R

    async def fake_call_llm(messages, tools=None, max_tokens=None, secondary=False, thinking="auto"):
        return {"content": '{"tasks": [{"title": "拆解需求", "goal": "明确范围", "priority": "high"}]}'}

    monkeypatch.setattr(R, "_call_llm", fake_call_llm)
    plan, from_llm = await R._workbench_generate_init_task_plan(
        {"id": "p1", "name": "Demo", "template": "blank"}, {"answers": {}},
    )
    assert from_llm is True
    assert plan[0]["title"] == "拆解需求"


@pytest.mark.asyncio
async def test_workbench_init_task_plan_reports_fallback_on_failure(monkeypatch):
    from webui import routes as R

    async def failing_call_llm(messages, tools=None, max_tokens=None, secondary=False, thinking="auto"):
        raise RuntimeError("model down")

    monkeypatch.setattr(R, "_call_llm", failing_call_llm)
    plan, from_llm = await R._workbench_generate_init_task_plan(
        {"id": "p1", "name": "Demo", "template": "blank"},
        {"answers": {"goal": "做一个 CLI 工具"}},
    )
    assert from_llm is False
    assert plan, "fallback plan must not be empty"


def test_workbench_promote_file_artifacts_promotes_and_dedups():
    from webui.routes import _workbench_promote_file_artifacts

    session = {"artifacts": [{"id": "a1", "type": "task_brief", "name": "task-brief.md", "status": "draft"}]}
    changes = [
        {"path": "cyrene/Cyrene_v1.py", "status": "modified"},
        {"path": "cyrene/train.py", "status": "created/updated"},
        {"path": "scan_channels.py", "status": "created"},
        {"path": "old.py", "status": "deleted"},            # not a deliverable
        {"path": "cyrene/Cyrene_v1.py", "status": "modified"},  # duplicate
    ]
    added = _workbench_promote_file_artifacts(session, changes, "2026-06-14T00:00:00Z")

    assert added == 3
    file_arts = [a for a in session["artifacts"] if a["type"] == "file_change"]
    by_name = {a["name"]: a for a in file_arts}
    assert set(by_name) == {"Cyrene_v1.py", "train.py", "scan_channels.py"}
    assert by_name["Cyrene_v1.py"]["status"] == "modified"
    assert by_name["train.py"]["status"] == "created"
    assert by_name["train.py"]["path"] == "cyrene/train.py"
    # task brief is preserved, deletions are not promoted
    assert any(a["type"] == "task_brief" for a in session["artifacts"])
    assert "old.py" not in {a.get("path") for a in file_arts}

    # idempotent: re-running adds nothing
    assert _workbench_promote_file_artifacts(session, changes, "2026-06-14T01:00:00Z") == 0


def test_workbench_backfill_file_artifacts_from_runs_and_steps():
    from webui.routes import _workbench_backfill_file_artifacts

    session = {
        "artifacts": [{"id": "a1", "type": "task_brief", "name": "task-brief.md", "status": "draft"}],
        "runs": [{"fileChanges": [
            {"path": "cyrene/train.py", "status": "created/updated"},
            {"path": "old.py", "status": "deleted"},
        ]}],
        "plan": [{"relatedFiles": [
            {"path": "cyrene/Cyrene_v1.py", "status": "modified"},
            {"path": "cyrene/train.py", "status": "modified"},  # also in a run -> merged
        ]}],
    }
    added = _workbench_backfill_file_artifacts(session, "2026-06-14T00:00:00Z")

    assert added == 2  # train.py + Cyrene_v1.py, dedup across run/step
    names = {a["name"] for a in session["artifacts"] if a["type"] == "file_change"}
    assert names == {"train.py", "Cyrene_v1.py"}
    # idempotent
    assert _workbench_backfill_file_artifacts(session, "2026-06-14T00:00:00Z") == 0
