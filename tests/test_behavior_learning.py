import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def _fake_llm_json(_prompt: str, *, caller: str = "behavior_learning"):
    return {}


async def _init_behavior(tmp_path, monkeypatch):
    from cyrene import behavior_learning as bl

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
    patterns = await bl.list_patterns()
    skills = await bl.list_learned_skills()

    assert stats["processed_turns"] == 5
    assert stats["candidates_created"] == 1
    assert stats["candidates_awaiting_user"] == 1
    assert stats["candidates_auto_learned"] == 1
    assert patterns == []
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
    assert skills[0]["shadow_validation_count"] == 0


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


async def test_legacy_python_wrapper_is_migrated_to_declarative_script(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    message = "迁移旧技能脚本"
    for index in range(3):
        await _record_code_fix_turn(bl, session_id="migration", round_id=f"migration-{index}", user_message=message)
    await bl.process_unprocessed_turns(force=True)
    skill = (await bl.list_learned_skills())[0]
    original_steps = skill["steps"]
    wrapper = [{
        "enabled": True,
        "implementation_kind": "script",
        "implementation_reference": {
            "language": "python",
            "script_path": str(tmp_path / "legacy.py"),
            "original_steps": original_steps,
        },
    }]
    async with bl._conn() as conn:
        await conn.execute(
            "UPDATE learned_skills SET steps_json = ?, script_json = '{}' WHERE skill_id = ?",
            (bl._json_dumps(wrapper), skill["id"]),
        )
        await conn.commit()

    assert await bl._migrate_generated_skill_scripts() == 1
    migrated = await bl.get_learned_skill(skill["id"])
    assert migrated["steps"] == original_steps
    assert migrated["script"]["format"] == "cyrene.parameterized-tool-script"
    assert migrated["script"]["risk"]["requires_runtime_approval"] is True

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
    assert await bl.list_patterns() == []
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

    async def reviewer(prompt: str, *, caller: str = "behavior_learning"):
        if caller == "project_skill_learning_agent":
            return {
                "decision": "parameterize",
                "confidence": 0.91,
                "rationale": "Repeated project-local workflow.",
                "proposed_skill": {
                    "name": "修复并验证导出逻辑",
                    "description": "读取文件、修改导出逻辑并运行测试。",
                    "skill_type": "parameterized",
                },
            }
        return {}

    monkeypatch.setattr(bl, "_call_llm_json", reviewer)
    turn_id = await _record_code_fix_turn(
        bl,
        session_id="session-agent-learn",
        round_id="round-agent-learn-1",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )

    stats = await bl.process_unprocessed_turns(force=True)
    skills = await bl.list_learned_skills()
    chains = await bl.list_tool_chains()

    assert stats["processed_turns"] == 1
    assert stats["learning_reviews"] == 0
    assert stats["agent_created_skills"] == 0
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
    chains = await bl.list_tool_chains()

    assert third_stats["candidates_auto_learned"] == 1
    assert len(third_skills) == 1
    assert (await bl.list_skill_candidates())[0]["status"] == "auto_learned"


async def test_browser_user_events_feed_learning_agent_and_are_queryable(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    prompts: list[str] = []

    async def reviewer(prompt: str, *, caller: str = "behavior_learning"):
        if caller == "project_skill_learning_agent":
            prompts.append(prompt)
            return {
                "decision": "skip",
                "confidence": 0.78,
                "rationale": "Browser operation is visible but not reusable yet.",
                "proposed_skill": {},
            }
        return {}

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

    stats = await bl.process_unprocessed_turns(force=True)
    chains = await bl.list_tool_chains(limit=5)
    events = await bl.list_recent_browser_user_events(
        session_id="session-browser-user",
        round_id="round-browser-user-1",
        limit=10,
    )

    assert stats["learning_reviews"] == 0
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
    assert prompts == []
    assert await bl.list_skill_candidates() == []


async def test_duplicate_skill_hard_veto_reuses_existing_cross_pattern(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    fp = await bl._heuristic_request_fingerprint(
        "帮我查一下上海今天的天气并打开来源页面核对",
        action_sequence=[
            {
                "domain": "external_information_query",
                "type": "query_realtime_info",
                "subtype": "search_web",
                "raw_description": "search_web",
            },
            {
                "domain": "external_information_query",
                "type": "retrieve_external_knowledge",
                "subtype": "fetch_web_page",
                "raw_description": "fetch_web_page",
            },
        ],
    )
    now = bl._now_iso()
    pattern_ids = ["pattern-weather-a", "pattern-weather-b"]
    async with bl._conn() as conn:
        for index, pid in enumerate(pattern_ids, start=1):
            session_id = f"session-{pid}"
            turn_id = f"turn-{pid}"
            round_id = f"round-{pid}"
            await conn.execute(
                """
                INSERT INTO behavior_sessions
                (session_id, project_id, project_key, session_kind, created_at, updated_at, session_summary, metadata_json)
                VALUES (?, 'global', 'global', 'test', ?, ?, '', '{}')
                """,
                (session_id, now, now),
            )
            await conn.execute(
                """
                INSERT INTO behavior_turns
                (turn_id, session_id, project_id, project_key, session_kind, round_id, created_at, updated_at,
                 user_message, context_summary, agent_response, outcome_status, user_feedback, processed_status,
                 linked_skill_id, metadata_json)
                VALUES (?, ?, 'global', 'global', 'test', ?, ?, ?, ?, '', '已核对来源。', 'success', '', 1, '', '{}')
                """,
                (turn_id, session_id, round_id, now, now, f"帮我查一下上海今天的天气并打开来源页面核对 {index}"),
            )
            actions = [
                (
                    f"action-{pid}-1",
                    0,
                    "external_information_query",
                    "query_realtime_info",
                    "search_web",
                    {"query": "上海 今天 天气"},
                    "weather result",
                ),
                (
                    f"action-{pid}-2",
                    1,
                    "external_information_query",
                    "retrieve_external_knowledge",
                    "fetch_web_page",
                    {"url": "https://example.test/weather"},
                    "source page",
                ),
            ]
            for action_id, action_index, action_type, action_subtype, tool_name, args, output in actions:
                await conn.execute(
                    """
                    INSERT INTO behavior_actions
                    (action_id, turn_id, session_id, round_id, created_at, action_index, action_type, action_subtype,
                     tool_name, input_summary, output_summary, success, error_summary, requires_llm, risk_level, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '', 0, 'none', ?)
                    """,
                    (
                        action_id,
                        turn_id,
                        session_id,
                        round_id,
                        now,
                        action_index,
                        action_type,
                        action_subtype,
                        tool_name,
                        bl._json_dumps(args),
                        output,
                        bl._json_dumps({"raw_args": args, "action_domain": action_type}),
                    ),
                )
            await conn.execute(
                """
                INSERT INTO behavior_patterns
                (pattern_id, project_id, project_key, description, prototype_fingerprint, statistics_json,
                 skillability_json, status, linked_skill_list, created_at, updated_at)
                VALUES (?, 'global', 'global', 'weather lookup with source check', ?, ?, ?, 'skill_candidate', '[]', ?, ?)
                """,
                (
                    pid,
                    bl._json_dumps(fp),
                    bl._json_dumps({"effective_count": 2, "frequency": 2}),
                    bl._json_dumps({"draft": True}),
                    now,
                    now,
                ),
            )
            await conn.execute(
                """
                INSERT INTO behavior_pattern_turns
                (pattern_id, turn_id, similarity, created_at)
                VALUES (?, ?, 1.0, ?)
                """,
                (pid, turn_id, now),
            )
        await conn.commit()

    first = await bl.learn_skill_from_pattern(pattern_ids[0])
    second = await bl.learn_skill_from_pattern(pattern_ids[1])
    skills = await bl.list_learned_skills()

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["skill_id"] == first["skill_id"]
    assert second["created"] is False
    assert len(skills) == 1


async def test_list_learned_skills_separates_shadow_validation_from_actual_usage(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    for index in range(1, 5):
        await _record_code_fix_turn(
            bl,
            session_id="session-code-usage",
            round_id=f"code-usage-{index}",
            user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
        )

    await bl.process_unprocessed_turns(force=True)
    skills = await bl.list_learned_skills()

    assert len(skills) == 1
    assert skills[0]["run_statistics"]["total_runs"] == 0
    assert skills[0]["shadow_validation_count"] == 0
    assert skills[0]["actual_usage_count"] == 0


async def test_single_tool_repetition_records_pattern_but_does_not_create_skill(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    for index in range(1, 4):
        await _record_web_search_turn(
            bl,
            session_id="session-weather-single-tool",
            round_id=f"weather-single-tool-{index}",
            user_message="帮我查一下上海今天的天气",
        )

    stats = await bl.process_unprocessed_turns(force=True)
    patterns = await bl.list_patterns()
    skills = await bl.list_learned_skills()

    assert stats["processed_turns"] == 3
    assert patterns == []
    assert await bl.list_skill_candidates() == []
    assert skills == []
    assert stats["agent_created_skills"] == 0


async def test_single_tool_cannot_be_auto_learned_even_if_agent_promotes_it(tmp_path, monkeypatch):
    """The model's promotion decision must not bypass the structural guard."""
    bl = await _init_behavior(tmp_path, monkeypatch)

    async def reviewer(prompt: str, *, caller: str = "behavior_learning"):
        if caller == "project_skill_learning_agent":
            return {
                "decision": "promote",
                "confidence": 0.99,
                "rationale": "The model incorrectly promoted a one-tool turn.",
                "proposed_skill": {"skill_type": "workflow"},
            }
        return {}

    monkeypatch.setattr(bl, "_call_llm_json", reviewer)
    await _record_web_search_turn(
        bl,
        session_id="session-single-tool-promote",
        round_id="single-tool-promote-1",
        user_message="帮我查一下上海今天的天气",
    )

    stats = await bl.process_unprocessed_turns(force=True)

    assert stats["processed_turns"] == 1
    assert stats["agent_created_skills"] == 0
    assert await bl.list_learned_skills() == []
    assert await bl.list_skill_candidates() == []


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
             pattern_id, created_at, updated_at)
            VALUES (?, 'global', 'global', ?, '', 1, 'active', 'draft', 'none', 0,
                    '{}', '[]', '{}', ?, '{}', '{}', '[]', '[]', '{}', '{}', ?, ?, ?)
            """,
            (
                "legacy-single-tool",
                "旧的单工具技能",
                bl._json_dumps([_make_step("search_web")]),
                "legacy-pattern",
                now,
                now,
            ),
        )
        await conn.commit()

    assert await bl.list_learned_skills() == []
    assert await bl.get_learned_skill("legacy-single-tool") is None
    assert await bl.build_learned_skill_block() == ""


async def test_similar_candidate_search_compares_purpose_and_tool_chain_within_project(tmp_path, monkeypatch):
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


async def test_behavior_learning_patch_application_and_vocabulary_snapshot(tmp_path, monkeypatch):
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
    vocabulary = await bl.vocabulary_snapshot()

    assert applied["ok"] is True
    assert refreshed is not None
    assert refreshed["fallback_policy"]["on_missing_args"] == "ask_user"
    result = await bl.list_learned_skill_patches(skill["id"])
    assert result[0]["status"] == "applied"
    assert vocabulary["vocabulary_version"] == 1
    assert vocabulary["unknown_labels"] == []


# ---------------------------------------------------------------------------
# Issue #46 regression: high-risk skill replay must not execute silently
# ---------------------------------------------------------------------------

def _make_skill_with_steps(steps: list[dict], *, risk_level: str = "none") -> dict:
    """Build a minimal skill dict for replay testing."""
    return {
        "skill_id": "test-skill-001",
        "name": "Test Skill",
        "description": "test",
        "version": 1,
        "status": "active",
        "skill_type": "deterministic",
        "risk_level": risk_level,
        "requires_llm": False,
        "trigger": {"base_fingerprint": {}, "min_match_score": 0.75},
        "input_schema": [],
        "parameter_extractor": {"mode": "hybrid", "llm_fallback": False},
        "steps": steps,
        "guards": {"risk_level": risk_level, "required_context": [], "forbidden_conditions": [], "confidence_threshold": 0.75},
        "fallback_policy": {},
        "tests": [],
        "editable_fields": [],
        "created_from": {},
        "run_statistics": {},
        "pattern_id": "pat-001",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def _make_step(tool_name: str) -> dict:
    return {
        "enabled": True,
        "implementation_kind": "tool_call",
        "implementation_reference": {"tool_name": tool_name, "args_template": {}},
    }


async def test_high_risk_skill_blocked_from_replay(tmp_path, monkeypatch):
    """Skills with high-risk steps must not be auto-replayed (issue #46)."""
    bl = await _init_behavior(tmp_path, monkeypatch)

    # Patch match_active_skill to return a Bash-containing skill
    risky_skill = _make_skill_with_steps([_make_step("Bash")], risk_level="high")
    monkeypatch.setattr(
        bl,
        "match_active_skill",
        AsyncMock(return_value={
            "skill": risky_skill,
            "similarity": {"total": 0.92, "hard_fail": False},
        }),
    )
    # Patch extract_skill_parameters to return complete extraction
    monkeypatch.setattr(
        bl,
        "extract_skill_parameters",
        AsyncMock(return_value={"complete": True, "params": {}, "confidence": 0.92, "missing_required": []}),
    )

    context = await bl.begin_turn(
        session_id="session-risk",
        round_id="round-risk-1",
        user_message="run the build script",
        history=[],
    )

    result = await bl.try_route_and_execute_skill(
        user_message="run the build script",
        visible_user_entry={"role": "user", "content": "run the build script"},
        llm_user_entry={"role": "user", "content": "run the build script"},
        history=[],
        bot=MagicMock(),
        chat_id=1,
        db_path=str(tmp_path / "test.db"),
        effective_system="",
        client_request_id="req-1",
        round_id="round-risk-1",
        lang="en",
    )

    # Must fall back to agent — not execute silently
    assert result is None, "High-risk skill should not auto-execute (expected None fallback)"

    bl.clear_turn_context(context)


async def test_interactive_skill_blocked_from_replay(tmp_path, monkeypatch):
    """Skills that would pause for user input must fall back to the normal agent loop."""
    bl = await _init_behavior(tmp_path, monkeypatch)

    interactive_skill = _make_skill_with_steps([_make_step("ask_user")], risk_level="none")
    monkeypatch.setattr(
        bl,
        "match_active_skill",
        AsyncMock(return_value={
            "skill": interactive_skill,
            "similarity": {"total": 0.92, "hard_fail": False},
        }),
    )
    monkeypatch.setattr(
        bl,
        "extract_skill_parameters",
        AsyncMock(return_value={"complete": True, "params": {}, "confidence": 0.92, "missing_required": []}),
    )

    context = await bl.begin_turn(
        session_id="session-interactive",
        round_id="round-interactive-1",
        user_message="ask me a few setup questions",
        history=[],
    )

    result = await bl.try_route_and_execute_skill(
        user_message="ask me a few setup questions",
        visible_user_entry={"role": "user", "content": "ask me a few setup questions"},
        llm_user_entry={"role": "user", "content": "ask me a few setup questions"},
        history=[],
        bot=MagicMock(),
        chat_id=1,
        db_path=str(tmp_path / "test.db"),
        effective_system="",
        client_request_id="req-interactive",
        round_id="round-interactive-1",
        lang="en",
    )

    assert result is None, "Interactive skills should not auto-execute ask_user"

    bl.clear_turn_context(context)


async def test_safe_skill_still_executes(tmp_path, monkeypatch):
    """Skills with only safe (read-only) steps should still auto-execute."""
    bl = await _init_behavior(tmp_path, monkeypatch)

    # A read_file step is not in _HIGH_RISK_TOOLS
    safe_skill = _make_skill_with_steps([_make_step("read_file")], risk_level="none")
    monkeypatch.setattr(
        bl,
        "match_active_skill",
        AsyncMock(return_value={
            "skill": safe_skill,
            "similarity": {"total": 0.92, "hard_fail": False},
        }),
    )
    monkeypatch.setattr(
        bl,
        "extract_skill_parameters",
        AsyncMock(return_value={"complete": True, "params": {}, "confidence": 0.92, "missing_required": []}),
    )
    # Stub tool execution and LLM reply — patch the module that behavior_learning imports from
    import cyrene.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_execute_tool", AsyncMock(return_value="file content"))

    async def _fake_final_reply(_messages, **_kw):
        return "Done."

    import cyrene.agent.guidance as guidance
    monkeypatch.setattr(guidance, "_final_user_reply_from_history", _fake_final_reply)

    import cyrene.agent.message as msg_mod
    monkeypatch.setattr(msg_mod, "_apply_assistant_meta", lambda x: x)

    context = await bl.begin_turn(
        session_id="session-safe",
        round_id="round-safe-1",
        user_message="show me app.py",
        history=[],
    )

    result = await bl.try_route_and_execute_skill(
        user_message="show me app.py",
        visible_user_entry={"role": "user", "content": "show me app.py"},
        llm_user_entry={"role": "user", "content": "show me app.py"},
        history=[],
        bot=MagicMock(),
        chat_id=1,
        db_path=str(tmp_path / "test.db"),
        effective_system="",
        client_request_id="req-2",
        round_id="round-safe-1",
        lang="en",
    )

    # Safe skill should proceed (result is not None)
    assert result is not None, "Safe skill should auto-execute (expected non-None result)"
    assert result["final_text"] == "Done."

    bl.clear_turn_context(context)


async def test_parameterized_runner_applies_typed_defaults(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    from cyrene.tool_impl import run_learned_skill as runner

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
    from cyrene.tool_impl import run_learned_skill as runner

    wrapper = {
        "enabled": True,
        "implementation_kind": "script",
        "implementation_reference": {"original_steps": [_make_step("Bash")]},
    }
    assert runner._has_unsafe_step([wrapper]) is True


async def test_skill_risk_level_inferred_on_creation(tmp_path, monkeypatch):
    """Skills containing high-risk tools must get risk_level='high' at creation time."""
    from cyrene import behavior_learning as bl

    # Directly test the helper — no DB needed
    assert bl._infer_skill_risk_level([]) == "none"
    assert bl._infer_skill_risk_level([_make_step("read_file")]) == "none"
    assert bl._infer_skill_risk_level([_make_step("Bash")]) == "high"
    assert bl._infer_skill_risk_level([_make_step("Write")]) == "high"
    assert bl._infer_skill_risk_level([_make_step("schedule_task")]) == "high"
    assert bl._infer_skill_risk_level([_make_step("start_shell")]) == "high"
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
