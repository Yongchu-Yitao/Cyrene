from cyrene.workbench.store import read_document, write_document
from cyrene.workbench.task_context import (
    append_shared_outcome,
    build_main_context,
    build_subagent_context,
    build_volatile_context,
    resolve_task_scope,
)


def _project():
    return {
        "id": "project_1",
        "name": "客户增长项目",
        "description": "为客户增长项目完成后端任务编排。",
        "context": {"summary": "项目背景摘要"},
        "sessions": [
            {
                "id": "session_a",
                "projectId": "project_1",
                "kind": "task",
                "title": "实现共享上下文",
                "goal": "让同项目任务共享成果",
                "summary": {"text": "后端机制实现"},
                "constraints": ["保持普通聊天上下文不受影响"],
                "plan": [
                    {
                        "id": "step_1",
                        "title": "新增上下文模块",
                        "description": "集中渲染共享块",
                        "status": "pending",
                        "order": 1,
                        "dependsOn": [],
                    }
                ],
                "acceptanceCriteria": [{"id": "ac_1", "text": "主代理看到项目共享上下文"}],
            },
            {
                "id": "session_b",
                "projectId": "project_1",
                "kind": "task",
                "title": "兄弟任务",
                "goal": "读取最新共享成果",
                "plan": [],
                "acceptanceCriteria": [],
            },
        ],
    }


def test_main_context_orders_project_blocks_before_session_blocks():
    project = _project()
    session = project["sessions"][0]

    ctx = build_main_context(project, session)

    assert "## Workbench 项目共享上下文" in ctx
    assert "### 项目任务描述" in ctx
    assert "## 当前 session 任务" in ctx
    assert "## 当前 session 任务约束" in ctx
    assert "保持普通聊天上下文不受影响" in ctx
    assert "## 当前 session 计划" in ctx
    assert "## 当前计划的验收标准" in ctx
    assert ctx.index("### 项目任务描述") < ctx.index("## 当前 session 任务")
    assert ctx.index("## 当前 session 任务") < ctx.index("## 当前 session 任务约束")
    assert ctx.index("## 当前 session 任务约束") < ctx.index("## 当前 session 计划")
    assert ctx.index("## 当前 session 计划") < ctx.index("## 当前计划的验收标准")


def test_subagent_context_replaces_session_task_with_subtask_prompt():
    project = _project()
    session = project["sessions"][0]

    ctx = build_subagent_context(project, session, "只实现 append_shared_outcome")

    assert "## 当前 session 任务" in ctx
    assert "子任务：只实现 append_shared_outcome" in ctx
    assert "父 session：实现共享上下文" in ctx
    assert "目标：让同项目任务共享成果" not in ctx
    assert "保持普通聊天上下文不受影响" in ctx
    assert "## 当前 session 计划" in ctx
    assert "主代理看到项目共享上下文" in ctx


def test_subagent_outcome_append_is_visible_to_sibling_session(tmp_path):
    db_path = tmp_path / "workbench.db"
    export_path = tmp_path / "workbench_projects.json"
    payload = {
        "projects": [_project()],
        "activeProjectId": "project_1",
        "activeSessionId": "session_a",
    }
    write_document(db_path, "projects", payload, lambda: {"projects": []}, export_path=export_path)

    entry = append_shared_outcome(
        db_path=db_path,
        session_id="session_a",
        agent_id="worker_1",
        source="subagent",
        text="已完成上下文模块和写回逻辑。",
        export_path=export_path,
    )

    assert entry is not None
    latest = read_document(db_path, "projects", lambda: {"projects": []})
    project = latest["projects"][0]
    sibling = project["sessions"][1]
    volatile = build_volatile_context(project, sibling)

    assert "已完成上下文模块和写回逻辑" in volatile
    assert project["sharedContext"]["revision"] == 1


def test_shared_context_ignores_non_task_sessions(tmp_path):
    db_path = tmp_path / "workbench.db"
    export_path = tmp_path / "workbench_projects.json"
    project = _project()
    project["sessions"][0]["kind"] = "init"
    payload = {"projects": [project], "activeProjectId": "project_1", "activeSessionId": "session_a"}
    write_document(db_path, "projects", payload, lambda: {"projects": []}, export_path=export_path)

    entry = append_shared_outcome(
        db_path=db_path,
        session_id="session_a",
        agent_id="worker_1",
        source="subagent",
        text="不应写入。",
        export_path=export_path,
    )

    assert entry is None
    assert build_main_context(project, project["sessions"][0]) == ""


def test_shared_context_does_not_create_store_for_non_workbench_session(tmp_path):
    db_path = tmp_path / "plain_agent.db"
    legacy_path = tmp_path / "missing_workbench_projects.json"

    payload, project, session = resolve_task_scope(
        "ordinary_session",
        db_path=db_path,
        legacy_path=legacy_path,
    )
    entry = append_shared_outcome(
        db_path=db_path,
        legacy_path=legacy_path,
        export_path=legacy_path,
        session_id="ordinary_session",
        agent_id="worker_1",
        source="subagent",
        text="普通 agent 的结果不应创建 Workbench 文档。",
    )

    assert (payload, project, session) == (None, None, None)
    assert entry is None
    assert not db_path.exists()
    assert not legacy_path.exists()
