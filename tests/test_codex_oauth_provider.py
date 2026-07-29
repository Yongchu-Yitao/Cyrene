from __future__ import annotations

import asyncio
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from cyrene.model_runtime.client import _normalized_candidate
from cyrene.model_runtime import client as model_client
from cyrene.model_runtime.codex_provider import (
    CODEX_BASE_URL,
    CODEX_AUTHENTICATION_EXPIRED,
    CODEX_MODEL_UNAVAILABLE,
    CODEX_PROVIDER,
    CODEX_QUOTA_EXHAUSTED,
    CodexAppServer,
    CodexAvailabilityError,
    CodexProtocolError,
    CodexTransportError,
    _codex_sdk_config,
    _disabled_host_skills_override,
    _normalize_provider_action,
    _normalized_effort,
    _provider_action_schema,
    _provider_action_tools,
    _provider_input,
    _provider_instructions,
    codex_availability_error,
    codex_error_should_cooldown,
)


def test_codex_sdk_uses_its_pinned_runtime_and_system_proxy() -> None:
    config = _codex_sdk_config()

    assert config.codex_bin is None
    assert config.cwd
    assert {
        "features.respect_system_proxy=true",
        "features.plugins=false",
        "features.apps=false",
        "features.shell_tool=false",
        "features.unified_exec=false",
        "features.browser_use=false",
        "features.computer_use=false",
        "features.image_generation=false",
        "features.multi_agent=false",
        "tools.web_search=false",
        "include_permissions_instructions=false",
        "include_apps_instructions=false",
        "include_collaboration_mode_instructions=false",
        "include_environment_context=false",
    }.issubset(set(config.config_overrides))
    assert any(
        item.startswith("model_instructions_file=")
        for item in config.config_overrides
    )
    assert _normalized_effort("LOW") == "low"
    assert _normalized_effort("MAX") == "max"


def test_codex_host_skills_are_disabled_by_folder_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    skill_dir = tmp_path / "skills" / "browser-control"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: browser-control\n---\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    override = _disabled_host_skills_override()

    assert override.startswith("skills.config=[")
    assert f'path="{(skill_dir / "SKILL.md").resolve()}"' in override
    assert "enabled=false" in override
    assert not (tmp_path / "config.toml").exists()


@pytest.mark.parametrize(
    ("error", "message", "expected_kind"),
    [
        (
            {"codexErrorInfo": "usageLimitExceeded"},
            "Usage limit reached",
            CODEX_QUOTA_EXHAUSTED,
        ),
        (
            {"codexErrorInfo": "unauthorized"},
            "Unauthorized",
            CODEX_AUTHENTICATION_EXPIRED,
        ),
        (
            {"codexErrorInfo": "badRequest"},
            "The model 'gpt-retired' does not exist",
            CODEX_MODEL_UNAVAILABLE,
        ),
        (
            {
                "codexErrorInfo": {
                    "httpConnectionFailed": {"httpStatusCode": 401}
                }
            },
            "Request failed",
            CODEX_AUTHENTICATION_EXPIRED,
        ),
    ],
)
def test_codex_availability_errors_are_classified(
    error: dict,
    message: str,
    expected_kind: str,
) -> None:
    classified = codex_availability_error(error, message=message)

    assert isinstance(classified, CodexAvailabilityError)
    assert classified.kind == expected_kind


@pytest.mark.parametrize("status", [403, 404])
def test_codex_does_not_guess_availability_from_ambiguous_http_status(
    status: int,
) -> None:
    classified = codex_availability_error(
        {
            "codexErrorInfo": {
                "httpConnectionFailed": {"httpStatusCode": status}
            }
        },
        message="Request failed",
    )

    assert classified is None


def test_codex_model_error_wins_over_403_auth_guess() -> None:
    classified = codex_availability_error(
        {
            "codexErrorInfo": {
                "httpConnectionFailed": {"httpStatusCode": 403}
            }
        },
        message="Selected model is not available for this account",
    )

    assert isinstance(classified, CodexAvailabilityError)
    assert classified.kind == CODEX_MODEL_UNAVAILABLE


def test_codex_cooldown_policy_keeps_recoverable_errors_hot() -> None:
    assert not codex_error_should_cooldown(
        CodexAvailabilityError(
            CODEX_AUTHENTICATION_EXPIRED,
            "login again",
        )
    )
    assert not codex_error_should_cooldown(
        CodexProtocolError("invalid action")
    )
    assert codex_error_should_cooldown(
        CodexAvailabilityError(CODEX_QUOTA_EXHAUSTED, "limit")
    )
    assert codex_error_should_cooldown(
        CodexAvailabilityError(CODEX_MODEL_UNAVAILABLE, "retired")
    )


def test_codex_candidate_never_inherits_api_credentials() -> None:
    candidate = _normalized_candidate(
        {
            "id": "codex-primary",
            "model": "gpt-5.6-sol",
            "provider": CODEX_PROVIDER,
            "reasoning_effort": "high",
            "api_key": "must-not-survive",
            "base_url": "https://example.invalid",
        },
        active_model="fallback",
        active_base_url="https://api.example/v1",
        active_api_key="secret",
    )

    assert candidate["provider"] == CODEX_PROVIDER
    assert candidate["base_url"] == CODEX_BASE_URL
    assert candidate["api_key"] == ""
    assert candidate["reasoning_effort"] == "high"
    assert candidate["vision_capable"] is None
    assert candidate["endpoints"] == [CODEX_BASE_URL]


def test_codex_candidate_is_never_resolved_as_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_client, "get_models", lambda: [
        {
            "id": "custom-primary",
            "model": "deepseek-chat",
            "provider": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test",
        },
        {
            "id": "codex-fallback",
            "model": "gpt-5.6-sol",
            "provider": CODEX_PROVIDER,
            "base_url": CODEX_BASE_URL,
        },
    ])

    candidates = model_client._resolve_llm_candidates()

    assert [candidate["provider"] for candidate in candidates] == [
        "openai_compatible"
    ]


@pytest.mark.asyncio
async def test_codex_quota_blocks_when_any_window_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def limits() -> dict:
        return {
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {"usedPercent": 100},
                    "secondary": {"usedPercent": 12},
                }
            }
        }

    monkeypatch.setattr(provider, "rate_limits_cached", limits)
    assert await provider.quota_available() is False


@pytest.mark.asyncio
async def test_codex_quota_check_failure_does_not_disable_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def limits() -> dict:
        raise RuntimeError("usage endpoint unavailable")

    monkeypatch.setattr(provider, "rate_limits_cached", limits)

    assert await provider.quota_available() is True


@pytest.mark.asyncio
async def test_codex_quota_check_failure_uses_stale_exhausted_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    provider._limits_cache = (
        0,
        {
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 100}}
            }
        },
    )

    async def limits() -> dict:
        raise RuntimeError("usage endpoint unavailable")

    monkeypatch.setattr(provider, "rate_limits", limits)

    assert await provider.quota_available() is False
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_codex_quota_returns_stale_value_before_background_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    provider._limits_cache = (
        time.monotonic() - 60,
        {
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 25}}
            }
        },
    )
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def refresh() -> dict:
        refresh_started.set()
        await release_refresh.wait()
        return {}

    monkeypatch.setattr(provider, "rate_limits", refresh)

    assert await provider.quota_available() is True
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    assert provider._limits_refresh_task is not None

    release_refresh.set()
    await provider._limits_refresh_task


@pytest.mark.asyncio
async def test_codex_snapshot_can_return_stale_limits_without_loading_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    cached_limits = {
        "rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 25}}
        }
    }
    provider._limits_cache = (time.monotonic(), cached_limits)

    async def account() -> dict:
        return {
            "account": {
                "type": "chatgpt",
                "planType": "prolite",
            }
        }

    async def models() -> list[dict]:
        raise AssertionError("quota snapshot should not load models")

    async def rate_limits() -> dict:
        raise AssertionError("fresh cache should be returned immediately")

    monkeypatch.setattr(provider, "account", account)
    monkeypatch.setattr(provider, "models", models)
    monkeypatch.setattr(provider, "rate_limits", rate_limits)

    snapshot = await provider.snapshot(
        include_models=False,
        stale_limits=True,
    )

    assert snapshot["account"]["planType"] == "prolite"
    assert snapshot["models"] == []
    assert snapshot["limits"] == cached_limits


@pytest.mark.asyncio
async def test_codex_completion_routes_streams_per_thread_and_runs_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    thread_counter = 0
    turns_started = 0
    both_turns_started = asyncio.Event()
    notification_queues: dict[str, asyncio.Queue] = {}

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            nonlocal thread_counter
            thread_counter += 1
            return {"thread": {"id": f"thread-{thread_counter}"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            nonlocal turns_started
            turn_id = f"turn-{thread_id}"
            notification_queues[turn_id] = asyncio.Queue()
            turns_started += 1
            if turns_started == 2:
                both_turns_started.set()
            await asyncio.wait_for(both_turns_started.wait(), timeout=1)
            queue = notification_queues[turn_id]
            queue.put_nowait(SimpleNamespace(
                method="item/agentMessage/delta",
                payload={"delta": f"reply:{thread_id}"},
            ))
            queue.put_nowait(SimpleNamespace(
                method="turn/completed",
                payload={"turn": {"id": turn_id, "status": "completed"}},
            ))
            return {"turn": {"id": turn_id}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            return await notification_queues[turn_id].get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            notification_queues.pop(turn_id, None)

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            return {}

    fake_client = FakeClient()

    async def ready_client() -> FakeClient:
        return fake_client

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    streams: list[list[dict]] = [[], []]

    async def run(index: int) -> dict:
        async def collect(event: dict) -> None:
            streams[index].append(event)

        return await provider.complete(
            messages=[{"role": "user", "content": f"request {index}"}],
            tools=None,
            model="gpt-5.6-sol",
            timeout=2,
            stream_callback=collect,
        )

    first, second = await asyncio.gather(run(0), run(1))

    assert {first["content"], second["content"]} == {
        "reply:thread-1",
        "reply:thread-2",
    }
    assert turns_started == 2
    for events in streams:
        assert events[0] == {"type": "reply_start"}
        assert set(events[1]) == {"type", "delta"}
        assert events[1]["type"] == "reply_delta"
        assert set(events[2]) == {"type", "response"}
        assert events[2]["type"] == "reply_done"
    assert notification_queues == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (
            {
                "message": "Your login session has expired",
                "codexErrorInfo": "unauthorized",
            },
            CODEX_AUTHENTICATION_EXPIRED,
        ),
        (
            {
                "message": "The model 'gpt-retired' does not exist",
                "codexErrorInfo": "badRequest",
            },
            CODEX_MODEL_UNAVAILABLE,
        ),
    ],
)
async def test_codex_completion_preserves_actionable_availability_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: dict,
    expected_kind: str,
) -> None:
    provider = CodexAppServer()
    queue: asyncio.Queue = asyncio.Queue()

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            return {"thread": {"id": "thread-1"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            queue.put_nowait(
                SimpleNamespace(
                    method="error",
                    payload={"error": error, "willRetry": False},
                )
            )
            return {"turn": {"id": "turn-1"}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            return await queue.get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            pass

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            return {}

    async def ready_client() -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(provider, "_ready_client", ready_client)

    with pytest.raises(CodexAvailabilityError) as exc_info:
        await provider.complete(
            messages=[{"role": "user", "content": "Say OK"}],
            tools=None,
            model="gpt-5.6-sol",
            timeout=2,
        )

    assert exc_info.value.kind == expected_kind


@pytest.mark.asyncio
async def test_codex_completion_forwards_reasoning_summary_and_low_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    queue: asyncio.Queue = asyncio.Queue()
    seen_turn_params: dict = {}

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            return {"thread": {"id": "thread-1"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            seen_turn_params.update(params)
            queue.put_nowait(SimpleNamespace(
                method="item/reasoning/summaryTextDelta",
                payload={"delta": "Checked the request."},
            ))
            queue.put_nowait(SimpleNamespace(
                method="item/agentMessage/delta",
                payload={"delta": "OK"},
            ))
            queue.put_nowait(SimpleNamespace(
                method="turn/completed",
                payload={
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [{"type": "agentMessage", "text": "OK"}],
                    }
                },
            ))
            return {"turn": {"id": "turn-1"}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            return await queue.get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            pass

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            return {}

    async def ready_client() -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    events: list[dict] = []

    async def collect_stream(event: dict) -> None:
        events.append(event)

    response = await provider.complete(
        messages=[{"role": "user", "content": "Say OK"}],
        tools=None,
        model="gpt-5.6-sol",
        reasoning_effort="LOW",
        timeout=2,
        stream_callback=collect_stream,
    )

    assert seen_turn_params["effort"] == "low"
    assert seen_turn_params["summary"] == "auto"
    assert response["reasoning_content"] == "Checked the request."
    assert [event["type"] for event in events] == [
        "reply_start",
        "reasoning_start",
        "reasoning_delta",
        "reply_delta",
        "reasoning_done",
        "reply_done",
    ]


@pytest.mark.asyncio
async def test_codex_completion_uses_structured_cyrene_actions_without_leaking_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    queue: asyncio.Queue = asyncio.Queue()
    seen_thread_params: dict = {}
    seen_turn_params: dict = {}
    action_text = (
        '{"content":"I already opened the browser.",'
        '"tool_calls":[{"name":"use_tools",'
        '"arguments_json":"{\\"task\\":\\"打开 B 站，用浏览器\\"}"}]}'
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "use_tools",
                "description": "Enter execution.",
                "parameters": {
                    "type": "object",
                    "properties": {"task": {"type": "string"}},
                    "required": ["task"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "quit",
                "description": "Finish.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "WebSearch",
                "description": "Search.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
    ]

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            seen_thread_params.update(params)
            return {"thread": {"id": "thread-1"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            seen_turn_params.update(params)
            queue.put_nowait(
                SimpleNamespace(
                    method="item/agentMessage/delta",
                    payload={"delta": action_text[:40]},
                )
            )
            queue.put_nowait(
                SimpleNamespace(
                    method="item/agentMessage/delta",
                    payload={"delta": action_text[40:]},
                )
            )
            queue.put_nowait(
                SimpleNamespace(
                    method="turn/completed",
                    payload={
                        "turn": {
                            "id": "turn-1",
                            "status": "completed",
                            "items": [
                                {"type": "agentMessage", "text": action_text}
                            ],
                        }
                    },
                )
            )
            return {"turn": {"id": "turn-1"}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            return await queue.get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            pass

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            return {}

    async def ready_client() -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    response = await provider.complete(
        messages=[{"role": "user", "content": "打开 B 站，用浏览器"}],
        tools=tools,
        model="gpt-5.3-codex-spark",
        phase="phase1",
        timeout=2,
        stream_callback=collect,
    )

    assert response["content"] == ""
    assert response["tool_calls"][0]["function"] == {
        "name": "use_tools",
        "arguments": '{"task": "打开 B 站，用浏览器"}',
    }
    assert events == []
    assert seen_turn_params["outputSchema"]["properties"]["tool_calls"][
        "minItems"
    ] == 1
    action_names = seen_turn_params["outputSchema"]["properties"]["tool_calls"][
        "items"
    ]["properties"]["name"]["enum"]
    assert action_names == ["use_tools", "quit"]
    assert "WebSearch" not in seen_thread_params["baseInstructions"]
    assert "DSML" not in seen_thread_params["baseInstructions"]
    assert "Do not invoke Codex built-in tools" in seen_thread_params[
        "baseInstructions"
    ]
    assert "Never invoke Codex-hosted tools" in seen_thread_params[
        "developerInstructions"
    ]
    assert seen_thread_params["cwd"]
    assert seen_thread_params["cwd"] != str(
        Path(__file__).resolve().parents[1]
    )
    assert "never read or follow them" in seen_thread_params[
        "developerInstructions"
    ]
    assert "Ignore Codex host skills" in seen_thread_params["baseInstructions"]


def test_codex_structured_action_preserves_only_terminal_content() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "quit",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    response = _normalize_provider_action(
        '{"content":"完成。","tool_calls":['
        '{"name":"quit","arguments_json":"{}"}]}',
        tools,
    )

    assert response["content"] == "完成。"
    assert response["tool_calls"][0]["function"]["name"] == "quit"


def test_codex_structured_action_rejects_invalid_arguments_json() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "use_tools",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    with pytest.raises(CodexProtocolError, match="invalid arguments"):
        _normalize_provider_action(
            '{"content":"","tool_calls":['
            '{"name":"use_tools","arguments_json":"not-json"}]}',
            tools,
        )


def test_codex_multi_tool_action_contract_allows_direct_completion() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_workspace",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_workspace",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    schema = _provider_action_schema(tools)
    response = _normalize_provider_action(
        '{"content":"{\\"status\\":\\"done\\"}","tool_calls":[]}',
        tools,
    )

    assert schema is not None
    assert schema["properties"]["tool_calls"]["minItems"] == 0
    assert response["content"] == '{"status":"done"}'
    assert response["tool_calls"] == []


def test_codex_phase1_action_contract_hides_execution_tools() -> None:
    tools = [
        {"type": "function", "function": {"name": "use_tools"}},
        {"type": "function", "function": {"name": "ask_user"}},
        {"type": "function", "function": {"name": "quit"}},
        {"type": "function", "function": {"name": "browser_tools"}},
    ]

    action_tools = _provider_action_tools(tools, phase="phase1")
    schema = _provider_action_schema(action_tools)

    assert [
        tool["function"]["name"] for tool in action_tools
    ] == ["use_tools", "ask_user", "quit"]
    assert schema is not None
    assert schema["properties"]["tool_calls"]["items"]["properties"]["name"][
        "enum"
    ] == ["use_tools", "ask_user", "quit"]


@pytest.mark.asyncio
async def test_codex_transport_retry_interrupts_and_falls_back_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    queue: asyncio.Queue = asyncio.Queue()
    interrupted: list[tuple[str, str]] = []

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            return {"thread": {"id": "thread-1"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            queue.put_nowait(SimpleNamespace(
                method="error",
                payload={
                    "threadId": thread_id,
                    "turnId": "turn-1",
                    "willRetry": True,
                    "error": {
                        "message": "stream disconnected",
                        "codexErrorInfo": {
                            "responseStreamDisconnected": {
                                "httpStatusCode": None,
                            }
                        },
                    },
                },
            ))
            return {"turn": {"id": "turn-1"}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            return await queue.get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            pass

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            interrupted.append((thread_id, turn_id))
            return {}

    fake_client = FakeClient()

    async def ready_client() -> FakeClient:
        return fake_client

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    transport_events: list[dict] = []

    async def collect_transport(event: dict) -> None:
        transport_events.append(event)

    with pytest.raises(CodexTransportError, match="stream disconnected"):
        await provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,
            model="gpt-5.6-sol",
            reasoning_effort="low",
            timeout=2,
            transport_callback=collect_transport,
        )

    assert interrupted == [("thread-1", "turn-1")]
    assert transport_events[-1]["status"] == "retrying"
    assert transport_events[-1]["error_kind"] == "responseStreamDisconnected"


@pytest.mark.asyncio
async def test_codex_without_an_upstream_signal_interrupts_before_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    never_notified = asyncio.Event()
    interrupted: list[tuple[str, str]] = []

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            return {"thread": {"id": "thread-1"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            return {"turn": {"id": "turn-1"}}

        async def next_turn_notification(self, turn_id: str) -> SimpleNamespace:
            await never_notified.wait()
            return SimpleNamespace(
                method="turn/completed",
                payload={"turn": {"id": turn_id, "status": "interrupted"}},
            )

        def unregister_turn_notifications(self, turn_id: str) -> None:
            pass

        async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            interrupted.append((thread_id, turn_id))
            never_notified.set()
            return {}

    async def ready_client() -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(provider, "_ready_client", ready_client)
    monkeypatch.setattr(
        "cyrene.model_runtime.codex_provider._first_signal_timeout",
        lambda _timeout: 0.01,
    )
    transport_events: list[dict] = []

    async def collect_transport(event: dict) -> None:
        transport_events.append(event)

    with pytest.raises(CodexTransportError, match="no upstream model signal"):
        await provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,
            model="gpt-5.6-sol",
            timeout=20,
            transport_callback=collect_transport,
        )

    assert interrupted == [("thread-1", "turn-1")]
    assert transport_events[-1]["status"] == "timed_out"


@pytest.mark.asyncio
async def test_codex_snapshot_survives_rate_limit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def account() -> dict:
        return {"account": {"type": "chatgpt", "email": "user@example.com"}}

    async def models() -> list[dict]:
        return [{"id": "gpt-5.6-sol"}]

    async def rate_limits() -> dict:
        raise RuntimeError("quota service unavailable")

    monkeypatch.setattr(provider, "account", account)
    monkeypatch.setattr(provider, "models", models)
    monkeypatch.setattr(provider, "rate_limits", rate_limits)

    snapshot = await provider.snapshot()

    assert snapshot["connected"] is True
    assert snapshot["models"] == [{"id": "gpt-5.6-sol"}]
    assert snapshot["limits"] == {}
    assert snapshot["errors"] == {"limits": "quota service unavailable"}


@pytest.mark.asyncio
async def test_codex_snapshot_can_skip_slow_rate_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def account() -> dict:
        return {"account": {"type": "chatgpt", "email": "user@example.com"}}

    async def models() -> list[dict]:
        return [{"id": "gpt-5.6-sol"}]

    async def rate_limits() -> dict:
        raise AssertionError("rate limits should not be requested")

    monkeypatch.setattr(provider, "account", account)
    monkeypatch.setattr(provider, "models", models)
    monkeypatch.setattr(provider, "rate_limits", rate_limits)

    snapshot = await provider.snapshot(include_limits=False)

    assert snapshot["connected"] is True
    assert snapshot["models"] == [{"id": "gpt-5.6-sol"}]
    assert snapshot["limits"] == {}
    assert "errors" not in snapshot


def test_codex_provider_replays_conversation_without_system_duplication() -> None:
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hello"},
    ]
    instructions = _provider_instructions(messages, None)
    replay = _provider_input(messages)

    assert "Be concise." in instructions
    assert '"role": "system"' not in replay
    assert '"role": "user"' in replay
