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
    assert stats["merged_patterns"] == 4
    assert len(patterns) == 1
    assert patterns[0]["description"] == "edit_resource / code_change / source_code_file / workspace_file"
    assert patterns[0]["prototype_fingerprint"]["action_sequence"][0]["subtype"] == "read_file"
    assert len(skills) == 1
    assert skills[0]["status"] == "active"
    assert skills[0]["skill_type"] == "parameterized"
    assert skills[0]["steps"][0]["implementation_kind"] == "script"
    assert Path(skills[0]["steps"][0]["implementation_reference"]["script_path"]).exists()
    assert len(skills[0]["steps"][0]["implementation_reference"]["original_steps"]) == 3
    assert skills[0]["actual_usage_count"] == 0
    assert skills[0]["shadow_validation_count"] == skills[0]["run_statistics"]["shadow_success"]
    # Shadow validation backfills every eligible historical turn before activation,
    # so the counter reflects total successful dry runs, not the promotion threshold.
    assert skills[0]["run_statistics"]["shadow_success"] == 4


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
    skills = await bl.list_learned_skills()
    skill = skills[0]
    updated = await bl.update_learned_skill(
        skill["id"],
        {"description": "manual edit description"},
        reason="manual test edit",
    )

    assert updated is not None
    assert updated["description"] == "manual edit description"
    assert updated["version"] == 2

    rollback = await bl.rollback_learned_skill(skill["id"], 1)
    restored = await bl.get_learned_skill(skill["id"])

    assert rollback["ok"] is True
    assert restored is not None
    assert restored["version"] == 3
    assert restored["description"] != "manual edit description"


async def test_manual_pattern_learning_creates_skill(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    await _record_code_fix_turn(
        bl,
        session_id="session-manual-pattern",
        round_id="round-manual-pattern-1",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )

    await bl.process_unprocessed_turns(force=True)
    patterns = await bl.list_patterns()
    learned = await bl.list_learned_skills()
    assert patterns
    assert len(learned) == 0

    result = await bl.learn_skill_from_pattern(patterns[0]["id"])
    assert result["ok"] is True
    assert result["created"] is True
    assert result["skill"] is not None
    assert result["skill"]["pattern_id"] == patterns[0]["id"]

    second = await bl.learn_skill_from_pattern(patterns[0]["id"])
    assert second["ok"] is True
    assert second["created"] is False
    assert second["skill_id"] == result["skill_id"]


async def test_learning_agent_learn_decision_creates_skill_immediately(tmp_path, monkeypatch):
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
    await _record_code_fix_turn(
        bl,
        session_id="session-agent-learn",
        round_id="round-agent-learn-1",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )

    stats = await bl.process_unprocessed_turns(force=True)
    skills = await bl.list_learned_skills()
    chains = await bl.list_tool_chains()

    assert stats["processed_turns"] == 1
    assert stats["learning_reviews"] == 1
    assert stats["agent_created_skills"] == 1
    assert len(skills) == 1
    assert skills[0]["skill_type"] == "parameterized"
    assert chains[0]["review"]["decision"] == "promote"
    assert chains[0]["review"]["proposed_skill"]["_decision"]["raw_decision"] == "parameterize"


async def test_learning_agent_duplicate_decision_does_not_create_second_skill(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    async def reviewer(prompt: str, *, caller: str = "behavior_learning"):
        if caller == "project_skill_learning_agent":
            if '"skills": []' not in prompt:
                return {
                    "decision": "duplicate",
                    "confidence": 0.95,
                    "rationale": "Existing skill already covers this workflow.",
                    "target_skill_id": "existing-skill-from-prompt",
                    "proposed_skill": {},
                }
            return {
                "decision": "parameterize",
                "confidence": 0.9,
                "rationale": "First occurrence should become a project-local skill.",
                "proposed_skill": {
                    "name": "修复并验证导出逻辑",
                    "description": "读取文件、修改导出逻辑并运行测试。",
                    "skill_type": "parameterized",
                },
            }
        return {}

    monkeypatch.setattr(bl, "_call_llm_json", reviewer)
    await _record_code_fix_turn(
        bl,
        session_id="session-agent-duplicate",
        round_id="round-agent-duplicate-1",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )
    first_stats = await bl.process_unprocessed_turns(force=True)
    first_skills = await bl.list_learned_skills()
    assert first_stats["agent_created_skills"] == 1
    assert len(first_skills) == 1

    await _record_code_fix_turn(
        bl,
        session_id="session-agent-duplicate",
        round_id="round-agent-duplicate-2",
        user_message="请检查 src/app.py 并修复导出逻辑，然后给我总结",
    )
    second_stats = await bl.process_unprocessed_turns(force=True)
    second_skills = await bl.list_learned_skills()
    chains = await bl.list_tool_chains()

    assert second_stats["learning_duplicates"] == 1
    assert second_stats["agent_created_skills"] == 0
    assert len(second_skills) == 1
    assert chains[0]["review"]["decision"] == "duplicate"


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

    assert stats["learning_reviews"] == 1
    assert chains[0]["source"] == "user_browser"
    assert chains[0]["summary"]["browser_user_steps"] == 2
    assert [step["tool"] for step in chains[0]["chain"]] == ["browser.user.click", "browser.user.input"]
    assert [event["tool"] for event in events] == ["browser.user.click", "browser.user.input"]
    assert prompts
    assert "browser.user.click" in prompts[-1]
    assert "https://example.test/search" in prompts[-1]


async def test_duplicate_skill_hard_veto_reuses_existing_cross_pattern(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)
    fp = await bl._heuristic_request_fingerprint(
        "帮我查一下上海今天的天气",
        action_sequence=[
            {
                "domain": "external_information_query",
                "type": "query_realtime_info",
                "subtype": "search_web",
                "raw_description": "search_web",
            }
        ],
    )
    now = bl._now_iso()
    pattern_ids = ["pattern-weather-a", "pattern-weather-b"]
    async with bl._conn() as conn:
        for pid in pattern_ids:
            await conn.execute(
                """
                INSERT INTO behavior_patterns
                (pattern_id, project_id, project_key, description, prototype_fingerprint, statistics_json,
                 skillability_json, status, linked_skill_list, created_at, updated_at)
                VALUES (?, 'global', 'global', 'weather lookup', ?, ?, ?, 'skill_candidate', '[]', ?, ?)
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

    for index in range(1, 4):
        await _record_web_search_turn(
            bl,
            session_id="session-weather-usage",
            round_id=f"weather-usage-{index}",
            user_message="帮我查一下上海今天的天气",
        )

    await bl.process_unprocessed_turns(force=True)
    skills = await bl.list_learned_skills()

    assert len(skills) == 1
    assert skills[0]["run_statistics"]["total_runs"] > 0
    assert skills[0]["shadow_validation_count"] > 0
    assert skills[0]["actual_usage_count"] == 0


async def test_similar_candidate_search_compares_purpose_and_tool_chain_within_project(tmp_path, monkeypatch):
    bl = await _init_behavior(tmp_path, monkeypatch)

    def project_scope(session_id: str | None):
        sid = str(session_id or "")
        if sid.startswith("project-b"):
            return {"project_id": "project-b", "project_key": "project-b", "session_kind": "test"}
        return {"project_id": "project-a", "project_key": "project-a", "session_kind": "test"}

    monkeypatch.setattr(bl, "_project_scope_for_session", project_scope)

    await _record_code_fix_turn(
        bl,
        session_id="project-a-session-seed",
        round_id="seed-code-fix",
        user_message="导出逻辑坏了，请读文件、改代码并跑测试",
    )
    await bl.process_unprocessed_turns(force=True, project_id="project-a")
    seed_pattern = (await bl.list_patterns(project_id="project-a"))[0]
    created = await bl.learn_skill_from_pattern(seed_pattern["id"], "project-a")
    assert created["ok"] is True

    await _record_web_search_turn(
        bl,
        session_id="project-a-session-weather",
        round_id="weather-search",
        user_message="帮我查一下上海今天的天气",
    )
    await bl.process_unprocessed_turns(force=True, project_id="project-a")
    weather_pattern = next(
        item for item in await bl.list_patterns(project_id="project-a")
        if item["id"] != seed_pattern["id"]
    )

    await _record_code_fix_turn(
        bl,
        session_id="project-b-session-seed",
        round_id="other-project-code-fix",
        user_message="导出逻辑坏了，请读文件、改代码并跑测试",
    )
    await bl.process_unprocessed_turns(force=True, project_id="project-b")
    other_project_pattern = (await bl.list_patterns(project_id="project-b"))[0]

    new_turn_id = await _record_code_fix_turn(
        bl,
        session_id="project-a-session-new",
        round_id="new-code-fix",
        user_message="帮我检查 src/app.py 的导出问题，修完后执行测试确认",
    )
    new_fp = await bl.build_turn_fingerprint(new_turn_id)
    similar = await bl._learning_similar_candidates(
        project_id="project-a",
        current_pattern_id="new-unmerged-candidate",
        fingerprint=new_fp,
        limit=10,
    )

    pattern_hits = {item["pattern_id"]: item for item in similar["patterns"]}
    skill_hits = {item["pattern_id"]: item for item in similar["skills"]}

    assert seed_pattern["id"] in pattern_hits
    assert pattern_hits[seed_pattern["id"]]["similarity"] >= 0.85
    assert pattern_hits[seed_pattern["id"]]["breakdown"]["action_sequence"] == 1.0
    assert created["skill_id"] in {item["skill_id"] for item in similar["skills"]}
    assert skill_hits[seed_pattern["id"]]["similarity"] >= 0.85

    assert weather_pattern["id"] not in pattern_hits
    assert other_project_pattern["id"] not in pattern_hits


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
    assert any(item["label_type"] == "intent_type" for item in vocabulary["unknown_labels"])


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
    assert bl._has_skillworthy_steps([_make_step("read_file")]) is True
    assert bl._has_skillworthy_steps([_make_step("ask_user")]) is False
    assert bl._has_auto_replay_blocked_step([_make_step("ask_user")]) is True
