import sys
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _prompt_json(prompt: str, marker: str) -> dict:
    payload = prompt.split(marker, 1)[1].lstrip()
    value, _ = json.JSONDecoder().raw_decode(payload)
    return value


def test_behavior_media_route_rebases_restored_path_with_spaces(
    monkeypatch, tmp_path
):
    from cyrene import learning
    from route import learning as learning_routes

    data_dir = tmp_path / "Application Support" / "Cyrene" / "data"
    target = data_dir / "behavior-media" / "turn_1" / "capture.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    old_path = (
        "/Users/old/Library/Application Support/Cyrene/"
        "data/behavior-media/turn_1/capture.png"
    )

    async def list_tool_chains(_project="", _limit=500):
        return [{
            "chain": [{
                "tool": "desktop.use",
                "output_summary": f'{{"path":"{old_path}"}}',
            }],
        }]

    monkeypatch.setattr(learning_routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(learning, "list_tool_chains", list_tool_chains)
    app = FastAPI()
    router = APIRouter()
    learning_routes.register_learning_routes(router, None, "")
    app.include_router(router)

    response = TestClient(app).get(
        "/api/tool-chain-media", params={"path": old_path}
    )

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"


def test_installed_skill_path_rebases_after_portable_restore(
    monkeypatch, tmp_path
):
    from cyrene.learning import skills

    installed_root = tmp_path / "current" / "data" / "installed_skills"
    skill_dir = installed_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo Skill\ndescription: Restored skill\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills, "_SKILLS_DIR", installed_root)

    payload = skills.skill_payload_from_record({
        "id": "demo-skill",
        "enabled": True,
        "stored_path": (
            "/Users/old/Library/Application Support/Cyrene/"
            "data/installed_skills/demo-skill"
        ),
    })

    assert payload is not None
    assert payload["name"] == "Demo Skill"
    assert Path(payload["stored_path"]) == skill_dir


async def _fake_llm_json(prompt: str, *, caller: str = "behavior_learning"):
    if caller != "skill_learning_agent":
        return {}
    if "Create the short purpose label" in prompt:
        payload = _prompt_json(prompt, "Execution record:\n")
        request = re.sub(r"https?://\S+|(?:[A-Za-z]:\\\\|/)[^\s]+", "", str(payload.get("user_request") or ""))
        purpose = re.sub(r"[。！？!?，,；;：:\s]+", "", request)[:20] or "执行浏览器操作"
        return {"purpose": purpose}
    if "Assign one new completed workflow" in prompt:
        payload = _prompt_json(prompt, "Learning input:\n")
        incoming = str((payload.get("new_record") or {}).get("purpose") or "")
        for item in payload.get("existing_candidates") or []:
            if str(item.get("purpose") or "") == incoming:
                return {"decision": "existing", "candidate_id": item["candidate_id"], "reason": "same purpose"}
        return {"decision": "new", "candidate_id": "", "canonical_purpose": incoming, "reason": "new purpose"}
    if "Synthesize one reusable learned Skill" in prompt:
        return {"description": "执行已重复出现的工具流程。", "implementation": {"kind": "tool_chain"}}
    return {}


async def _init_behavior(tmp_path, monkeypatch):
    from cyrene.learning import engine as bl

    await bl.init(tmp_path, tmp_path)
    monkeypatch.setattr(bl, "_call_llm_json", _fake_llm_json)
    return bl


async def _record_code_fix_turn(bl, *, session_id: str, round_id: str, user_message: str):
    context = await bl.begin_turn(
        session_id=session_id,
        round_id=round_id,
        user_message=user_message,
        history=[],
        session_title="Behavior test session",
    )
    await bl.record_action("read_file", {"path": "src/app.py"}, "main_agent", round_id, 12, result="file content", success=True)
    await bl.record_action(
        "edit_file",
        {"path": "src/app.py", "old_string": "return raw", "new_string": "return exported"},
        "main_agent",
        round_id,
        20,
        result="patched",
        success=True,
    )
    await bl.record_action(
        "run_shell",
        {"command": "pytest -q tests/test_export.py"},
        "main_agent",
        round_id,
        80,
        result="1 passed",
        success=True,
    )
    await bl.complete_turn(
        turn_id=context["turn_id"],
        assistant_response="已修复并验证。",
        session_title="Behavior test session",
        round_title=round_id,
    )
    bl.clear_turn_context(context)
    return context["turn_id"]


async def _record_web_search_turn(bl, *, session_id: str, round_id: str, user_message: str):
    context = await bl.begin_turn(
        session_id=session_id,
        round_id=round_id,
        user_message=user_message,
        history=[],
        session_title="Behavior test session",
    )
    await bl.record_action(
        "search_web",
        {"query": "today weather Shanghai"},
        "main_agent",
        round_id,
        30,
        result="weather result",
        success=True,
    )
    await bl.complete_turn(
        turn_id=context["turn_id"],
        assistant_response="已查询天气。",
        session_title="Behavior test session",
        round_title=round_id,
    )
    bl.clear_turn_context(context)
    return context["turn_id"]


async def _record_repeated_read_turn(bl, *, round_id: str, user_message: str, suffix: str):
    context = await bl.begin_turn(
        session_id="session-repeated-read",
        round_id=round_id,
        user_message=user_message,
        history=[],
        session_title="Repeated read test",
    )
    await bl.record_action("read_file", {"path": f"src/{suffix}.py"}, "main_agent", round_id, 5, result="a", success=True)
    await bl.record_action("read_file", {"path": f"tests/test_{suffix}.py"}, "main_agent", round_id, 5, result="b", success=True)
    await bl.record_action("search_web", {"query": f"documentation {suffix}"}, "main_agent", round_id, 5, result="c", success=True)
    await bl.complete_turn(
        turn_id=context["turn_id"],
        assistant_response="done",
        session_title="Repeated read test",
        round_title=round_id,
    )
    bl.clear_turn_context(context)
    return context["turn_id"]


async def test_init_removes_legacy_learning_schema(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    db_path = tmp_path / "behavior-learning.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE learned_skills ADD COLUMN pattern_id TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE behavior_turns ADD COLUMN linked_skill_id TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE behavior_skill_candidates ADD COLUMN bucket_key TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX idx_behavior_skill_candidates_bucket ON behavior_skill_candidates(project_id, bucket_key)")
        for table in (
            "behavior_fingerprints",
            "behavior_patterns",
            "behavior_pattern_turns",
            "behavior_learning_agent_reviews",
            "behavior_vocabulary_labels",
            "behavior_vocabulary_aliases",
            "behavior_unknown_labels",
            "behavior_replay_tests",
        ):
            conn.execute(f"CREATE TABLE {table} (id TEXT)")

    await bl.init(tmp_path, tmp_path)

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        skill_columns = {row[1] for row in conn.execute("PRAGMA table_info(learned_skills)")}
        turn_columns = {row[1] for row in conn.execute("PRAGMA table_info(behavior_turns)")}
        chain_columns = {row[1] for row in conn.execute("PRAGMA table_info(behavior_turn_tool_chains)")}
        candidate_columns = {row[1] for row in conn.execute("PRAGMA table_info(behavior_skill_candidates)")}
        assignment_columns = {row[1] for row in conn.execute("PRAGMA table_info(behavior_skill_candidate_turns)")}
    assert not tables.intersection({
        "behavior_fingerprints",
        "behavior_patterns",
        "behavior_pattern_turns",
        "behavior_learning_agent_reviews",
        "behavior_vocabulary_labels",
        "behavior_vocabulary_aliases",
        "behavior_unknown_labels",
        "behavior_replay_tests",
    })
    assert "pattern_id" not in skill_columns
    assert "linked_skill_id" not in turn_columns
    assert "purpose" in chain_columns
    assert "purpose" in candidate_columns
    assert "bucket_key" not in candidate_columns
    assert "assignment_reason" in assignment_columns


async def test_behavior_learning_promotes_to_active_skill(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    for index in range(1, 6):
        await _record_code_fix_turn(
            bl,
            session_id="session-alpha",
            round_id=f"round-{index}",
            user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
        )

    stats = await bl.process_unprocessed_turns(force=True)
    skills = await bl.list_learned_skills()

    assert stats["processed_turns"] == 5
    assert stats["candidates_created"] == 1
    assert stats["candidates_awaiting_user"] == 1
    assert stats["candidates_auto_learned"] == 1
    candidates = await bl.list_skill_candidates()
    assert len(candidates) == 1
    assert candidates[0]["occurrence_count"] == 5
    assert candidates[0]["status"] == "auto_learned"
    assert len(skills) == 1
    assert skills[0]["status"] == "active"
    assert skills[0]["skill_type"] == "parameterized"
    assert skills[0]["steps"][0]["implementation_kind"] == "tool_call"
    assert skills[0]["script"]["format"] == "cyrene.parameterized-tool-script"
    assert skills[0]["script"]["steps"] == skills[0]["steps"]
    assert any(item["parameter_name"].startswith("param_path") for item in skills[0]["input_schema"])
    assert skills[0]["actual_usage_count"] == 0


async def test_second_occurrence_script_parameterizes_repeated_tool_calls(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    message = "读取实现和测试，再查询对应文档"
    await _record_repeated_read_turn(bl, round_id="repeat-1", user_message=message, suffix="alpha")
    await _record_repeated_read_turn(bl, round_id="repeat-2", user_message=message, suffix="beta")

    await bl.process_unprocessed_turns(force=True)
    candidate = (await bl.list_skill_candidates())[0]
    script = candidate["script"]

    assert candidate["status"] == "awaiting_user"
    assert script["format"] == "cyrene.parameterized-tool-script"
    assert script["steps"][0]["implementation_reference"]["tool_name"] == "read_file"
    repeated_items = script["steps"][0]["implementation_reference"]["args_template"]["_items"]
    assert len(repeated_items) == 2
    assert all("{{param_path" in item["path"] for item in repeated_items)
    assert any(item["required"] for item in script["parameters"])


async def test_defer_auto_learns_on_third_and_dismiss_blocks_auto_learning(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    defer_message = "重复流程等待第三次"
    for index in range(2):
        await _record_code_fix_turn(bl, session_id="defer", round_id=f"defer-{index}", user_message=defer_message)
    await bl.process_unprocessed_turns(force=True)
    deferred = (await bl.list_skill_candidates())[0]
    assert (await bl.decide_skill_candidate(deferred["id"], "defer"))["status"] == "waiting_third"
    await _record_code_fix_turn(bl, session_id="defer", round_id="defer-3", user_message=defer_message)
    await bl.process_unprocessed_turns(force=True)
    assert (await bl.list_skill_candidates())[0]["status"] == "auto_learned"
    assert len(await bl.list_learned_skills()) == 1

    dismiss_message = "这个重复流程不要学习"
    for index in range(2):
        await _record_repeated_read_turn(bl, round_id=f"dismiss-{index}", user_message=dismiss_message, suffix=f"dismiss{index}")
    await bl.process_unprocessed_turns(force=True)
    dismissed = next(item for item in await bl.list_skill_candidates() if item["status"] == "awaiting_user")
    assert (await bl.decide_skill_candidate(dismissed["id"], "dismiss"))["status"] == "dismissed"
    await _record_repeated_read_turn(bl, round_id="dismiss-3", user_message=dismiss_message, suffix="dismiss3")
    await bl.process_unprocessed_turns(force=True)
    dismissed = next(item for item in await bl.list_skill_candidates() if item["id"] == dismissed["id"])
    assert dismissed["status"] == "dismissed"
    assert len(await bl.list_learned_skills()) == 1


async def test_complex_workflow_learning_agent_generates_python_skill(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    async def reviewer(prompt: str, *, caller: str = "behavior_learning"):
        if "Synthesize one reusable learned Skill" in prompt:
            return {
                "description": "读取、修改并验证代码。",
                "implementation": {
                    "kind": "python",
                    "source": (
                        "import argparse\n"
                        "import json\n"
                        "parser = argparse.ArgumentParser()\n"
                        "parser.add_argument('--params-json', default='{}')\n"
                        "args = parser.parse_args()\n"
                        "params = json.loads(args.params_json)\n"
                        "print(json.dumps({'ok': True, 'params': params}, ensure_ascii=False))\n"
                    ),
                },
            }
        return await _fake_llm_json(prompt, caller=caller)

    monkeypatch.setattr(bl, "_call_llm_json", reviewer)
    message = "读取代码修复导出逻辑并运行测试"
    for index in range(3):
        await _record_code_fix_turn(bl, session_id="script", round_id=f"script-{index}", user_message=message)
    await bl.process_unprocessed_turns(force=True)
    skill = (await bl.list_learned_skills())[0]

    assert skill["skill_type"] == "python_script"
    assert skill["risk_level"] == "high"
    assert skill["steps"][0]["implementation_kind"] == "script"
    reference = skill["steps"][0]["implementation_reference"]
    assert reference["generated_by"] == "skill_learning_agent"
    assert reference["requires_runtime_approval"] is True
    assert Path(reference["script_path"]).read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")
    assert skill["script"]["implementation"]["source_sha256"] == reference["source_sha256"]
    assert len(skill["script"]["declarative_steps"]) == 3


async def test_learning_agent_shell_script_is_validated_persisted_and_hash_checked(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    implementation = bl._normalize_script_implementation(
        {
            "kind": "shell",
            "source": "printf '%s\\n' \"$CYRENE_SKILL_PARAMS\"",
        },
        allow_script=True,
    )
    original_steps = [_make_step("read_file"), _make_step("search_file_content")]

    steps, persisted = bl._persist_learning_agent_script(
        "shell-skill",
        implementation,
        original_steps,
        "输出参数",
    )
    output, ok, reason = await bl._execute_script_step(
        steps[0]["implementation_reference"],
        {"input": "value"},
    )

    assert persisted["kind"] == "shell_script"
    assert Path(persisted["script_path"]).read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert ok is True
    assert reason == ""
    assert json.loads(output) == {"input": "value"}

    Path(persisted["script_path"]).write_text("echo tampered\n", encoding="utf-8")
    _, ok, reason = await bl._execute_script_step(steps[0]["implementation_reference"], {})
    assert ok is False
    assert reason == "script_integrity_error"


async def test_learned_script_path_rebases_after_portable_restore(
    tmp_path, monkeypatch
):
    bl = await _init_behavior(tmp_path, monkeypatch)
    script = (
        tmp_path
        / "learned_skill_scripts"
        / "restored-skill"
        / "run.sh"
    )
    script.parent.mkdir(parents=True)
    source = "#!/bin/sh\nprintf restored\n"
    script.write_text(source, encoding="utf-8")

    output, ok, reason = await bl._execute_script_step(
        {
            "script_path": (
                "/Users/old/Library/Application Support/Cyrene/"
                "data/learned_skill_scripts/restored-skill/run.sh"
            ),
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "language": "shell",
        },
        {},
    )

    assert ok is True
    assert reason == ""
    assert output == "restored"


async def test_behavior_learning_sanitizes_legacy_scheduler_prompts(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    context = await bl.begin_turn(
        session_id="session-proactive",
        round_id="round-proactive-1",
        user_message="This is a scheduler-initiated proactive check-in.\nInternal guidance only.",
        history=[],
        session_title="Scheduled check-in",
    )
    await bl.record_action(
        "search_web", {"query": "weather"}, "main_agent", "round-proactive-1", 10,
        result="weather result", success=True,
    )
    await bl.complete_turn(
        turn_id=context["turn_id"],
        assistant_response="A user-facing update.",
        session_title="Scheduled check-in",
        round_title="proactive check-in",
    )
    bl.clear_turn_context(context)

    stats = await bl.process_unprocessed_turns(force=True)

    assert stats["processed_turns"] == 1
    assert await bl.list_skill_candidates() == []
    assert await bl.list_learned_skills() == []
    chain = (await bl.list_tool_chains())[0]
    assert chain["user_message"] == "Scheduled proactive check-in"
    assert chain["system_initiated"] is True


async def test_behavior_learning_preserves_screenshot_artifacts(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    screenshot = tmp_path / "capture.png"
    screenshot.write_bytes(b"png-content")
    context = await bl.begin_turn(
        session_id="session-screenshot",
        round_id="round-screenshot-1",
        user_message="截取当前网页截图",
        history=[],
        session_title="Screenshot test session",
    )
    await bl.record_action(
        "browser_screenshot", {}, "main_agent", "round-screenshot-1", 10,
        result=f"Screenshot taken.\nPath: {screenshot}", success=True,
    )
    await bl.complete_turn(
        turn_id=context["turn_id"],
        assistant_response="截图已保存。",
        session_title="Screenshot test session",
        round_title="网页截图",
    )
    bl.clear_turn_context(context)

    chain = (await bl.list_tool_chains())[0]
    stored_path = Path(chain["chain"][0]["output_summary"].split("Path: ", 1)[1])

    assert stored_path.exists()
    assert stored_path.parent.parent.name == "behavior-media"
    assert stored_path.read_bytes() == b"png-content"


async def test_behavior_learning_manual_edit_and_rollback(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    for index in range(1, 3):
        await _record_code_fix_turn(
            bl,
            session_id="session-beta",
            round_id=f"round-{index}",
            user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
        )

    await bl.process_unprocessed_turns(force=True)
    candidate = (await bl.list_skill_candidates())[0]
    decision = await bl.decide_skill_candidate(candidate["id"], "learn_now")
    assert decision["ok"] is True
    skills = await bl.list_learned_skills()
    skill = skills[0]
    updated = await bl.update_learned_skill(
        skill["id"],
        {"description": "manual edit description"},
        reason="manual test edit",
    )

    assert updated is not None
    assert updated["description"] == "manual edit description"
    assert updated["script"]["description"] == "manual edit description"
    assert updated["script"]["version"] == 2
    assert updated["version"] == 2

    rollback = await bl.rollback_learned_skill(skill["id"], 1)
    restored = await bl.get_learned_skill(skill["id"])

    assert rollback["ok"] is True
    assert restored is not None
    assert restored["version"] == 3
    assert restored["description"] != "manual edit description"
    assert restored["script"]["description"] == restored["description"]
    assert restored["script"]["version"] == 3


async def test_manual_turn_learning_creates_skill(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    turn_id = await _record_code_fix_turn(
        bl,
        session_id="session-manual-pattern",
        round_id="round-manual-pattern-1",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )

    result = await bl.learn_from_turn(turn_id)
    assert result["processed_turns"] == 1
    assert result["skills_created"] == 1
    learned = await bl.list_learned_skills()
    assert len(learned) == 1
    assert learned[0]["script"]["format"] == "cyrene.parameterized-tool-script"


async def test_learning_agent_does_not_auto_learn_from_one_turn(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    turn_id = await _record_code_fix_turn(
        bl,
        session_id="session-agent-learn",
        round_id="round-agent-learn-1",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )

    stats = await bl.process_unprocessed_turns(force=True)
    skills = await bl.list_learned_skills()

    assert stats["processed_turns"] == 1
    assert skills == []
    assert (await bl.list_skill_candidates())[0]["status"] == "observing"

    # An explicit user action may promote this single-turn workflow, while
    # background automatic learning must wait for repeated evidence.
    manual_stats = await bl.learn_from_turn(turn_id)
    skills = await bl.list_learned_skills()
    assert manual_stats["processed_turns"] == 1
    assert manual_stats["skills_created"] == 1
    assert len(skills) == 1
    assert skills[0]["skill_type"] == "parameterized"


async def test_second_occurrence_waits_and_third_auto_learns_once(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    await _record_code_fix_turn(
        bl,
        session_id="session-agent-duplicate",
        round_id="round-agent-duplicate-1",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )
    first_stats = await bl.process_unprocessed_turns(force=True)
    first_skills = await bl.list_learned_skills()
    assert first_stats["candidates_created"] == 1
    assert first_skills == []

    await _record_code_fix_turn(
        bl,
        session_id="session-agent-duplicate",
        round_id="round-agent-duplicate-2",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )
    second_stats = await bl.process_unprocessed_turns(force=True)
    second_skills = await bl.list_learned_skills()

    assert second_stats["candidates_awaiting_user"] == 1
    assert second_skills == []
    assert (await bl.list_skill_candidates())[0]["status"] == "awaiting_user"

    await _record_code_fix_turn(
        bl,
        session_id="session-agent-duplicate",
        round_id="round-agent-duplicate-3",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )
    third_stats = await bl.process_unprocessed_turns(force=True)
    third_skills = await bl.list_learned_skills()

    assert third_stats["candidates_auto_learned"] == 1
    assert len(third_skills) == 1
    assert (await bl.list_skill_candidates())[0]["status"] == "auto_learned"


async def test_browser_user_events_feed_learning_agent_and_are_queryable(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    prompts: list[str] = []

    async def reviewer(prompt: str, *, caller: str = "behavior_learning"):
        if caller == "skill_learning_agent":
            prompts.append(prompt)
        return await _fake_llm_json(prompt, caller=caller)

    monkeypatch.setattr(bl, "_call_llm_json", reviewer)
    context = await bl.begin_turn(
        session_id="session-browser-user",
        round_id="round-browser-user-1",
        user_message="用户在浏览器里完成筛选后继续",
        history=[],
        session_title="Browser user session",
    )
    await bl.record_browser_user_event(
        session_id="session-browser-user",
        round_id="round-browser-user-1",
        event_kind="click",
        payload={"x": 128, "y": 64, "button": "left"},
        browser_url="https://example.test/search",
        browser_title="Search",
        target={"tag": "button", "text": "Apply filters"},
    )
    await bl.record_browser_user_event(
        session_id="session-browser-user",
        round_id="round-browser-user-1",
        event_kind="input",
        payload={"value": "openai", "inputType": "insertText"},
        browser_url="https://example.test/search",
        browser_title="Search",
        target={"tag": "input", "name": "q"},
    )
    await bl.complete_turn(
        turn_id=context["turn_id"],
        assistant_response="已根据页面状态继续。",
        session_title="Browser user session",
        round_title="round-browser-user-1",
    )
    bl.clear_turn_context(context)

    await bl.process_unprocessed_turns(force=True)
    chains = await bl.list_tool_chains(limit=5)
    events = await bl.list_recent_browser_user_events(
        session_id="session-browser-user",
        round_id="round-browser-user-1",
        limit=10,
    )

    assert chains[0]["source"] == "user_browser"
    assert chains[0]["summary"]["browser_user_steps"] == 2
    assert [step["tool"] for step in chains[0]["chain"]] == ["browser.user.click", "browser.user.input"]
    assert chains[0]["chain"][0]["purpose"] == "activate button 'Apply filters'"
    assert chains[0]["chain"][0]["action_summary"] == "clicked button 'Apply filters'"
    assert chains[0]["chain"][1]["purpose"] == "provide browser input for input 'q'"
    assert chains[0]["chain"][1]["action_summary"] == "entered 'openai' into input 'q'"
    assert [event["tool"] for event in events] == ["browser.user.click", "browser.user.input"]
    assert events[0]["purpose"] == "activate button 'Apply filters'"
    assert events[1]["value_preview"] == "openai"
    assert chains[0]["purpose"] == "用户在浏览器里完成筛选后继续"
    assert len(prompts) == 2
    candidates = await bl.list_skill_candidates()
    assert len(candidates) == 1
    assert candidates[0]["purpose"] == chains[0]["purpose"]
    assert candidates[0]["status"] == "observing"


async def test_browser_user_text_without_semantic_target_is_redacted_before_storage(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    await bl.record_browser_user_event(
        session_id="session-browser-redaction",
        round_id="round-browser-redaction",
        event_kind="text",
        payload={"text": "secret-value-that-must-not-be-stored"},
        browser_url="https://example.test/login",
        browser_title="Login",
        target={},
    )

    events = await bl.list_recent_browser_user_events(
        session_id="session-browser-redaction",
        round_id="round-browser-redaction",
    )

    assert events[0]["payload"]["text"] == "[redacted-unattributed-text]"
    assert "secret-value" not in json.dumps(events, ensure_ascii=False)

async def test_single_tool_repetition_does_not_create_candidate_or_skill(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    for index in range(1, 4):
        await _record_web_search_turn(
            bl,
            session_id="session-weather-single-tool",
            round_id=f"weather-single-tool-{index}",
            user_message="帮我查一下上海今天的天气",
        )

    stats = await bl.process_unprocessed_turns(force=True)
    skills = await bl.list_learned_skills()

    assert stats["processed_turns"] == 3
    assert await bl.list_skill_candidates() == []
    assert skills == []


async def test_single_tool_cannot_be_auto_learned_even_if_agent_promotes_it(tmp_path, monkeypatch):
    """The model's promotion decision must not bypass the structural guard."""
    bl = await _init_behavior(tmp_path, monkeypatch)
    await _record_web_search_turn(
        bl,
        session_id="session-single-tool-promote",
        round_id="single-tool-promote-1",
        user_message="帮我查一下上海今天的天气",
    )

    stats = await bl.process_unprocessed_turns(force=True)

    assert stats["processed_turns"] == 1
    assert await bl.list_learned_skills() == []
    assert await bl.list_skill_candidates() == []


async def test_invalid_learning_agent_output_keeps_turn_pending_without_local_match_fallback(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    async def invalid(_prompt: str, *, caller: str = "behavior_learning"):
        return {}

    monkeypatch.setattr(bl, "_call_llm_json", invalid)
    turn_id = await _record_code_fix_turn(
        bl,
        session_id="invalid-learning-agent",
        round_id="invalid-learning-agent-1",
        user_message="修复导出逻辑并运行测试",
    )

    stats = await bl.process_unprocessed_turns(force=True)
    async with bl._conn() as conn:
        cursor = await conn.execute("SELECT processed_status FROM behavior_turns WHERE turn_id = ?", (turn_id,))
        row = await cursor.fetchone()

    assert stats["processed_turns"] == 0
    assert int(row["processed_status"]) == 0
    assert await bl.list_skill_candidates() == []


async def test_learning_agent_prompt_chain_redacts_credentials(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    prompt_chain = bl._purpose_chain_for_prompt([
        {
            "source": "agent",
            "tool": "run_shell",
            "args": {
                "api_key": "top-secret-key",
                "command": "curl -H 'Authorization: Bearer private-token' https://example.test?access_token=query-secret",
                "path": "src/app.py",
            },
            "success": True,
        }
    ])
    rendered = json.dumps(prompt_chain, ensure_ascii=False)

    assert "top-secret-key" not in rendered
    assert "private-token" not in rendered
    assert "query-secret" not in rendered
    assert "[redacted]" in rendered
    assert "src/app.py" in rendered


async def test_legacy_single_tool_skill_is_hidden_from_learning_surfaces(tmp_path, monkeypatch):
    """Old databases must not keep exposing skills created before the guard."""
    bl = await _init_behavior(tmp_path, monkeypatch)
    now = bl._now_iso()
    async with bl._conn() as conn:
        await conn.execute(
            """
            INSERT INTO learned_skills
            (skill_id, project_id, project_key, name, description, current_version, status,
             skill_type, risk_level, requires_llm, trigger_json, input_schema_json,
             parameter_extractor_json, steps_json, guards_json, fallback_policy_json,
             tests_json, editable_fields_json, created_from_json, run_statistics_json,
             created_at, updated_at)
            VALUES (?, 'global', 'global', ?, '', 1, 'active', 'draft', 'none', 0,
                    '{}', '[]', '{}', ?, '{}', '{}', '[]', '[]', '{}', '{}', ?, ?)
            """,
            (
                "legacy-single-tool",
                "旧的单工具技能",
                bl._json_dumps([_make_step("search_web")]),
                now,
                now,
            ),
        )
        await conn.commit()

    assert await bl.list_learned_skills() == []
    assert await bl.get_learned_skill("legacy-single-tool") is None
    assert await bl.build_learned_skill_block() == ""


async def test_purpose_assignment_is_project_scoped(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    def project_scope(session_id: str | None):
        sid = str(session_id or "")
        if sid.startswith("project-b"):
            return {"project_id": "project-b", "project_key": "project-b", "session_kind": "test"}
        return {"project_id": "project-a", "project_key": "project-a", "session_kind": "test"}

    monkeypatch.setattr(bl, "_project_scope_for_session", project_scope)

    message = "导出逻辑坏了，请读文件、改代码并跑测试"
    for index in range(2):
        await _record_code_fix_turn(
            bl,
            session_id=f"project-a-session-{index}",
            round_id=f"project-a-{index}",
            user_message=message,
        )
    await _record_code_fix_turn(
        bl,
        session_id="project-b-session-1",
        round_id="project-b-1",
        user_message=message,
    )

    await bl.process_unprocessed_turns(force=True, project_id="project-a")
    await bl.process_unprocessed_turns(force=True, project_id="project-b")
    project_a = await bl.list_skill_candidates("project-a")
    project_b = await bl.list_skill_candidates("project-b")

    assert len(project_a) == 1
    assert project_a[0]["occurrence_count"] == 2
    assert project_a[0]["status"] == "awaiting_user"
    assert len(project_b) == 1
    assert project_b[0]["occurrence_count"] == 1
    assert project_b[0]["status"] == "observing"


async def test_assignment_agent_receives_complete_purpose_catalog_in_one_call(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    assignments = []

    async def reviewer(prompt: str, *, caller: str = "behavior_learning"):
        if "Create the short purpose label" in prompt:
            payload = _prompt_json(prompt, "Execution record:\n")
            message = str(payload.get("user_request") or "")
            if message == "修复导出逻辑":
                return {"purpose": "修复导出逻辑"}
            if message == "整理导出文档":
                return {"purpose": "整理导出文档"}
            if message == "查询上海天气":
                return {"purpose": "查询天气"}
            return {"purpose": "修好导出并验证"}
        if "Assign one new completed workflow" in prompt:
            payload = _prompt_json(prompt, "Learning input:\n")
            assignments.append(payload)
            incoming = str((payload.get("new_record") or {}).get("purpose") or "")
            existing = payload.get("existing_candidates") or []
            if incoming == "修好导出并验证":
                target = next(item for item in existing if item["purpose"] == "修复导出逻辑")
                return {"decision": "existing", "candidate_id": target["candidate_id"], "reason": "same reusable goal"}
            return {"decision": "new", "candidate_id": "", "canonical_purpose": incoming, "reason": "new goal"}
        return await _fake_llm_json(prompt, caller=caller)

    monkeypatch.setattr(bl, "_call_llm_json", reviewer)
    await _record_web_search_turn(
        bl,
        session_id="catalog-session",
        round_id="catalog-weather",
        user_message="查询上海天气",
    )
    for index, message in enumerate(("修复导出逻辑", "整理导出文档", "把导出功能修好并测试"), 1):
        await _record_code_fix_turn(
            bl,
            session_id="catalog-session",
            round_id=f"catalog-{index}",
            user_message=message,
        )

    await bl.process_unprocessed_turns(force=True)

    assert len(assignments) == 3
    assert [item["purpose"] for item in assignments[2]["existing_candidates"]] == ["修复导出逻辑", "整理导出文档"]
    # A single-tool lookup is not reusable skill evidence and must not spend a
    # background LLM call merely to populate the historical purpose catalog.
    assert [item["purpose"] for item in assignments[2]["all_historical_purposes"]] == ["修复导出逻辑", "整理导出文档"]
    candidates = sorted(await bl.list_skill_candidates(), key=lambda item: item["purpose"])
    assert len(candidates) == 2
    assert next(item for item in candidates if item["purpose"] == "修复导出逻辑")["occurrence_count"] == 2


async def test_behavior_learning_patch_application(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    for index in range(1, 3):
        await _record_code_fix_turn(
            bl,
            session_id="session-gamma",
            round_id=f"round-{index}",
            user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
        )

    await bl.process_unprocessed_turns(force=True)
    candidate = (await bl.list_skill_candidates())[0]
    await bl.decide_skill_candidate(candidate["id"], "learn_now")
    skills = await bl.list_learned_skills()
    skill = skills[0]

    await bl._maybe_propose_patch(skill["id"], int(skill["version"]), "missing parameters: path")
    patches = await bl.list_learned_skill_patches(skill["id"])
    patch = patches[0]
    applied = await bl.apply_skill_patch(skill["id"], patch["patch_id"])
    refreshed = await bl.get_learned_skill(skill["id"])
    assert applied["ok"] is True
    assert refreshed is not None
    assert refreshed["fallback_policy"]["on_missing_args"] == "ask_user"
    result = await bl.list_learned_skill_patches(skill["id"])
    assert result[0]["status"] == "applied"


# ---------------------------------------------------------------------------
# Issue #46 regression: high-risk skill replay must not execute silently
# ---------------------------------------------------------------------------

def _make_skill_with_steps(steps: list[dict], *, risk_level: str = "none") -> dict:
    """Build a minimal skill dict for execution testing."""
    return {
        "skill_id": "test-skill-001",
        "name": "Test Skill",
        "description": "test",
        "version": 1,
        "status": "active",
        "skill_type": "deterministic",
        "risk_level": risk_level,
        "requires_llm": False,
        "trigger": {"positive_examples": []},
        "input_schema": [],
        "parameter_extractor": {"mode": "hybrid", "llm_fallback": False},
        "steps": steps,
        "guards": {"risk_level": risk_level, "required_context": [], "forbidden_conditions": [], "confidence_threshold": 0.75},
        "fallback_policy": {},
        "tests": [],
        "editable_fields": [],
        "created_from": {},
        "run_statistics": {},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _make_step(tool_name: str) -> dict:
    return {
        "enabled": True,
        "implementation_kind": "tool_call",
        "implementation_reference": {"tool_name": tool_name, "args_template": {}},
    }

async def test_parameterized_runner_applies_typed_defaults(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    from cyrene.tool_impl.skills import run_learned_skill as runner

    skill = _make_skill_with_steps([{
        "enabled": True,
        "implementation_kind": "tool_call",
        "implementation_reference": {
            "tool_name": "read_file",
            "args_template": {"path": "{{input_path}}", "limit": "{{line_limit}}"},
        },
    }])
    skill["input_schema"] = [
        {"parameter_name": "input_path", "type": "path", "required": False, "default_value": "src/app.py"},
        {"parameter_name": "line_limit", "type": "number", "required": False, "default_value": 20},
    ]
    monkeypatch.setattr(bl, "get_learned_skill_by_name", AsyncMock(return_value=skill))
    execute = AsyncMock(return_value="file content")
    monkeypatch.setattr(runner, "_execute_tool", execute)
    monkeypatch.setattr(bl, "record_manual_skill_run", AsyncMock())

    result = await runner._tool_run_learned_skill(
        {"name": skill["name"], "params": {}}, MagicMock(), 1, str(tmp_path / "db.sqlite"), None,
    )

    assert runner.json.loads(result)["ok"] is True
    called_args = execute.await_args.args[1]
    assert called_args == {"path": "src/app.py", "limit": 20}


def test_parameterized_runner_detects_unsafe_legacy_wrapper():
    from cyrene.tool_impl.skills import run_learned_skill as runner

    wrapper = {
        "enabled": True,
        "implementation_kind": "script",
        "implementation_reference": {"original_steps": [_make_step("Bash")]},
    }
    assert runner._has_unsafe_step([wrapper]) is True


async def test_skill_risk_level_inferred_on_creation(tmp_path, monkeypatch):
    """Skills containing high-risk tools must get risk_level='high' at creation time."""
    from cyrene.learning import engine as bl

    # Directly test the helper — no DB needed
    assert bl._infer_skill_risk_level([]) == "none"
    assert bl._infer_skill_risk_level([_make_step("read_file")]) == "none"
    assert bl._infer_skill_risk_level([_make_step("Bash")]) == "high"
    assert bl._infer_skill_risk_level([_make_step("Write")]) == "high"
    assert bl._infer_skill_risk_level([_make_step("schedule_task")]) == "high"
    assert bl._infer_skill_risk_level([_make_step("start_shell")]) == "high"
    assert bl._infer_skill_risk_level([_make_step("browser_navigate")]) == "none"
    assert bl._infer_skill_risk_level([_make_step("browser_click_ref")]) == "none"
    assert bl._infer_skill_risk_level([_make_step("browser_snapshot")]) == "none"
    assert bl._infer_skill_risk_level([_make_step("browser_type_ref")]) == "high"
    assert bl._infer_skill_risk_level([_make_step("browser_upload_files")]) == "high"
    # Mixed: one safe + one risky → high
    assert bl._infer_skill_risk_level([_make_step("read_file"), _make_step("Edit")]) == "high"
    # Disabled risky step should not count
    disabled_bash = {**_make_step("Bash"), "enabled": False}
    assert bl._infer_skill_risk_level([disabled_bash]) == "none"
    assert bl._has_skillworthy_steps([_make_step("read_file")]) is False
    assert bl._has_skillworthy_steps([_make_step("read_file"), _make_step("edit_file")]) is True
    assert bl._has_skillworthy_steps([_make_step("search_web"), _make_step("search_web")]) is False
    assert bl._has_skillworthy_steps([_make_step("ask_user")]) is False
    assert bl._has_auto_replay_blocked_step([_make_step("ask_user")]) is True
    assert bl._has_auto_replay_blocked_step([_make_step("browser.user.navigate")]) is True


async def test_browser_learned_skill_replays_and_skips_legacy_progress_step(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    from cyrene.tool_impl.skills import run_learned_skill as runner

    skill = _make_skill_with_steps([
        _make_step("send_message"),
        _make_step("browser_navigate"),
        _make_step("browser_click_ref"),
        _make_step("browser_snapshot"),
    ], risk_level="high")
    monkeypatch.setattr(bl, "get_learned_skill_by_name", AsyncMock(return_value=skill))
    execute = AsyncMock(return_value="ok")
    monkeypatch.setattr(runner, "_execute_tool", execute)
    monkeypatch.setattr(bl, "record_manual_skill_run", AsyncMock())

    result = await runner._tool_run_learned_skill(
        {"name": skill["name"], "params": {}}, MagicMock(), 1, str(tmp_path / "db.sqlite"), None,
    )

    payload = runner.json.loads(result)
    assert payload["ok"] is True
    assert [call.args[0] for call in execute.await_args_list] == [
        "browser_navigate",
        "browser_click_ref",
        "browser_snapshot",
    ]
