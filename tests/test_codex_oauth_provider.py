from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from cyrene.model.codex_provider import (
    CODEX_AUTHENTICATION_EXPIRED,
    CODEX_CLI_REQUIRED,
    CODEX_MODEL_UNAVAILABLE,
    CODEX_QUOTA_EXHAUSTED,
    CodexAppServer,
    CodexAvailabilityError,
    CodexProtocolError,
    CodexTransportError,
    _codex_image_sdk_config,
    _codex_sdk_config,
    _disabled_host_skills_override,
    _is_cli_protocol_mismatch,
    _normalize_provider_action,
    _normalized_effort,
    _provider_action_schema,
    _provider_action_tools,
    _provider_input,
    _provider_instructions,
    _provider_turn_input,
    _recover_with_pinned_cli,
    _require_cli,
    codex_availability_error,
    codex_error_should_cooldown,
    provider_request_cache_material,
)
from cyrene.model import codex_cli


def test_codex_sdk_uses_its_pinned_runtime_and_system_proxy(
    tmp_path,
) -> None:
    cli_path = tmp_path / "codex"
    config = _codex_sdk_config(cli_path)

    assert config.codex_bin == str(cli_path)
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


def test_codex_image_sdk_enables_only_image_generation(tmp_path) -> None:
    config = _codex_image_sdk_config(tmp_path / "codex")

    assert config.codex_bin == str(tmp_path / "codex")
    assert "features.image_generation=true" in config.config_overrides
    assert {
        "features.plugins=false",
        "features.apps=false",
        "features.shell_tool=false",
        "features.unified_exec=false",
        "features.browser_use=false",
        "features.computer_use=false",
        "features.multi_agent=false",
        "tools.view_image=false",
        "tools.web_search=false",
    }.issubset(set(config.config_overrides))


@pytest.mark.asyncio
async def test_codex_image_capability_uses_provider_capability_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    seen: dict = {}

    class FakeClient:
        async def request(
            self,
            method: str,
            params: dict,
            *,
            response_model,
        ):
            seen.update(
                method=method,
                params=params,
                response_model=response_model,
            )
            return SimpleNamespace(image_generation=True)

    async def ready_image_client():
        return FakeClient()

    monkeypatch.setattr(provider, "_ready_image_client", ready_image_client)

    assert await provider.image_generation_capability() is True
    assert seen["method"] == "modelProvider/capabilities/read"
    assert seen["params"] == {}


@pytest.mark.asyncio
async def test_codex_generate_image_collects_image_generation_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    queue: asyncio.Queue = asyncio.Queue()
    seen: dict = {}

    class FakeClient:
        async def thread_start(self, params: dict) -> dict:
            seen["thread"] = params
            return {"thread": {"id": "thread-image"}}

        async def turn_start(
            self,
            thread_id: str,
            input_items: list[dict],
            params: dict,
        ) -> dict:
            seen["turn_input"] = input_items
            seen["turn_params"] = params
            queue.put_nowait(
                SimpleNamespace(
                    method="item/completed",
                    payload={
                        "item": {
                            "type": "imageGeneration",
                            "status": "completed",
                            "result": "aW1hZ2U=",
                            "revisedPrompt": "A revised prompt",
                        }
                    },
                )
            )
            queue.put_nowait(
                SimpleNamespace(
                    method="turn/completed",
                    payload={
                        "turn": {
                            "id": "turn-image",
                            "status": "completed",
                            "items": [],
                        }
                    },
                )
            )
            return {"turn": {"id": "turn-image"}}

        async def next_turn_notification(self, _turn_id: str):
            return await queue.get()

        def unregister_turn_notifications(self, turn_id: str) -> None:
            seen["unregistered"] = turn_id

    async def ready_image_client():
        return FakeClient()

    monkeypatch.setattr(provider, "_ready_image_client", ready_image_client)

    result = await provider.generate_image(
        prompt="Draw an otter",
        model="gpt-5.6-sol",
        timeout=5,
    )

    assert result["type"] == "imageGeneration"
    assert result["result"] == "aW1hZ2U="
    assert seen["thread"]["ephemeral"] is True
    assert "Draw an otter" in seen["turn_input"][0]["text"]
    assert seen["unregistered"] == "turn-image"


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
    seen_turn_input: list[dict] = []

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
            seen_turn_input.extend(input_items)
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
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Say OK"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,aW1hZ2U=",
                    },
                },
            ],
        }],
        tools=None,
        model="gpt-5.6-sol",
        reasoning_effort="LOW",
        timeout=2,
        stream_callback=collect_stream,
    )

    assert seen_turn_params["effort"] == "low"
    assert seen_turn_params["summary"] == "auto"
    assert seen_turn_input[2] == {
        "type": "image",
        "url": "data:image/png;base64,aW1hZ2U=",
    }
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
    ] == 0
    action_names = seen_turn_params["outputSchema"]["properties"]["tool_calls"][
        "items"
    ]["properties"]["name"]["enum"]
    assert action_names == ["use_tools", "quit", "WebSearch"]
    assert "WebSearch" in seen_thread_params["baseInstructions"]
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

    assert response["content"] == ""
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


def test_codex_structured_action_repairs_unambiguous_schema_arguments() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "Plan",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "tasks": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["title", "tasks"],
                                "additionalProperties": False,
                            },
                        },
                        "max_steps": {"type": "integer", "minimum": 1},
                    },
                    "required": ["steps", "max_steps"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    malformed = {
        "steps": {
            "item": [
                {
                    "title": "Inspect",
                    "tasks": {"item": ["Read", "Verify"]},
                }
            ]
        },
        "max_steps": "2",
    }

    response = _normalize_provider_action(
        json.dumps(
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "Plan",
                        "arguments_json": json.dumps(malformed),
                    }
                ],
            }
        ),
        tools,
    )

    arguments = json.loads(
        response["tool_calls"][0]["function"]["arguments"]
    )
    assert arguments == {
        "steps": [
            {"title": "Inspect", "tasks": ["Read", "Verify"]}
        ],
        "max_steps": 2,
    }


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


def test_codex_phase1_and_phase2_keep_identical_action_schema() -> None:
    tools = [
        {"type": "function", "function": {"name": "use_tools"}},
        {"type": "function", "function": {"name": "ask_user"}},
        {"type": "function", "function": {"name": "quit"}},
        {"type": "function", "function": {"name": "browser_tools"}},
    ]

    action_tools = _provider_action_tools(tools, phase="phase1")
    schema = _provider_action_schema(action_tools)

    assert [tool["function"]["name"] for tool in action_tools] == [
        "use_tools",
        "ask_user",
        "quit",
        "browser_tools",
    ]
    assert schema is not None
    assert schema["properties"]["tool_calls"]["items"]["properties"]["name"][
        "enum"
    ] == ["use_tools", "ask_user", "quit", "browser_tools"]


def test_codex_final_provider_material_is_strictly_append_only_across_phases() -> None:
    tools = [
        {"type": "function", "function": {"name": "use_tools"}},
        {"type": "function", "function": {"name": "quit"}},
        {"type": "function", "function": {"name": "browser_tools"}},
    ]
    phase1_messages = [
        {"role": "system", "content": "stable system"},
        {"role": "user", "content": "inspect"},
        {"role": "user", "content": "decision rules"},
    ]
    phase2_messages = [
        *phase1_messages,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "use-1",
                "function": {"name": "use_tools", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "use-1", "content": "entered"},
    ]

    phase1 = provider_request_cache_material(
        messages=phase1_messages,
        tools=tools,
        model="gpt-5.3-codex",
        phase="phase1",
        reasoning_effort="high",
    )
    phase2 = provider_request_cache_material(
        messages=phase2_messages,
        tools=tools,
        model="gpt-5.3-codex",
        phase="phase2",
        reasoning_effort="high",
    )

    assert phase2["action_tools"] == phase1["action_tools"]
    assert phase2["action_schema"] == phase1["action_schema"]
    assert phase2["thread_params"]["baseInstructions"] == phase1["thread_params"][
        "baseInstructions"
    ]
    assert phase2["turn_input"][:len(phase1["turn_input"])] == phase1[
        "turn_input"
    ]
    assert phase2["message_units"][:len(phase1["message_units"])] == phase1[
        "message_units"
    ]


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
        "cyrene.model.codex_provider._first_signal_timeout",
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


def test_codex_provider_converts_openai_image_content_to_turn_input() -> None:
    data_url = "data:image/png;base64,aW1hZ2U="
    messages = [
        {"role": "system", "content": "Inspect images carefully."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is shown?"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    turn_input = _provider_turn_input(messages)

    assert turn_input[2] == {"type": "image", "url": data_url}
    assert data_url not in "".join(
        item.get("text", "") for item in turn_input if item["type"] == "text"
    )
    assert "[Image 1 is attached to this turn.]" in turn_input[1]["text"]


def test_codex_cli_missing_surfaces_actionable_availability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> Path:
        raise codex_cli.CodexCliMissingError("Codex CLI runtime is not downloaded")

    monkeypatch.setattr(codex_cli, "ensure_cli", missing)

    with pytest.raises(CodexAvailabilityError) as exc_info:
        _require_cli()

    assert exc_info.value.kind == CODEX_CLI_REQUIRED
    assert "not downloaded" in str(exc_info.value)


@pytest.mark.asyncio
async def test_codex_snapshot_reports_cli_required_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def missing_account() -> dict:
        raise CodexAvailabilityError(
            CODEX_CLI_REQUIRED, "Codex CLI runtime is not downloaded"
        )

    monkeypatch.setattr(provider, "account", missing_account)
    monkeypatch.setattr(
        codex_cli, "status", lambda: {"installed": False, "version": ""}
    )

    snapshot = await provider.snapshot()

    assert snapshot["available"] is False
    assert snapshot["connected"] is False
    assert snapshot["cli"] == {"installed": False, "version": ""}
    assert "Codex CLI" in snapshot["error"]


@pytest.mark.asyncio
async def test_codex_snapshot_reports_broken_installed_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def failing_account() -> dict:
        raise RuntimeError("codex app-server failed to start")

    monkeypatch.setattr(provider, "account", failing_account)
    provider._client_start_error = "codex app-server failed to start"
    monkeypatch.setattr(codex_cli, "status", lambda: {"installed": True, "version": "0.200.0"})

    snapshot = await provider.snapshot()

    assert snapshot["available"] is False
    assert snapshot["connected"] is False
    assert snapshot["cli"] == {
        "installed": True,
        "broken": True,
        "version": "0.200.0",
        "error": "codex app-server failed to start",
    }
    assert snapshot["error"] == "codex app-server failed to start"


@pytest.mark.asyncio
async def test_codex_snapshot_does_not_mark_cli_broken_on_upstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    async def failing_account() -> dict:
        raise RuntimeError("quota service unavailable")

    monkeypatch.setattr(provider, "account", failing_account)
    provider._client_start_error = None

    with pytest.raises(RuntimeError, match="quota service unavailable"):
        await provider.snapshot()


def test_codex_cli_protocol_mismatch_detection() -> None:
    assert _is_cli_protocol_mismatch(RuntimeError("SDK/CLI protocol mismatch"))
    assert _is_cli_protocol_mismatch(RuntimeError("unknown method: v2/foo"))
    assert _is_cli_protocol_mismatch(
        RuntimeError("app-server version incompatible with SDK")
    )
    assert _is_cli_protocol_mismatch(
        RuntimeError("app-server protocol version 3 is not supported")
    )
    assert not _is_cli_protocol_mismatch(
        RuntimeError("stream disconnected")
    )
    assert not _is_cli_protocol_mismatch(
        TimeoutError("Codex request timed out")
    )
    assert not _is_cli_protocol_mismatch(
        OSError("no route to host")
    )
    # Transport-layer wording shares "protocol version" with genuine clash
    # messages and must never trigger the pinned-runtime fallback.
    assert not _is_cli_protocol_mismatch(
        OSError("tlsv1 alert protocol version")
    )
    assert not _is_cli_protocol_mismatch(
        RuntimeError("SSL: certificate verify failed")
    )
    assert not _is_cli_protocol_mismatch(
        RuntimeError("proxy connection refused")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installed", "download_error", "expected", "expected_downloads"),
    [
        # Already on the pinned runtime: nothing to fix by re-downloading.
        ("0.144.4", None, False, []),
        # Newer CLI + protocol clash: the pinned runtime is downloaded.
        ("0.200.0", None, True, ["0.144.4"]),
        # A failed fallback download keeps the original error, never masks it.
        ("0.200.0", OSError("disk full"), False, ["0.144.4"]),
    ],
)
async def test_codex_recover_with_pinned_cli(
    monkeypatch: pytest.MonkeyPatch,
    installed: str,
    download_error: Exception | None,
    expected: bool,
    expected_downloads: list[str],
) -> None:
    downloaded: list[str | None] = []

    async def download_and_wait(version: str | None) -> Path:
        downloaded.append(version)
        if download_error is not None:
            raise download_error
        return Path("/tmp/codex")

    monkeypatch.setattr(codex_cli, "sdk_pinned_version", lambda: "0.144.4")
    monkeypatch.setattr(codex_cli, "installed_version", lambda: installed)
    monkeypatch.setattr(codex_cli, "download_and_wait", download_and_wait)

    assert (
        await _recover_with_pinned_cli(
            RuntimeError("app-server protocol mismatch")
        )
        is expected
    )
    assert downloaded == expected_downloads


@pytest.mark.asyncio
async def test_codex_start_client_requires_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()

    def missing() -> Path:
        raise codex_cli.CodexCliMissingError("Codex CLI runtime is not downloaded")

    monkeypatch.setattr(codex_cli, "ensure_cli", missing)

    with pytest.raises(CodexAvailabilityError) as exc_info:
        await provider._start_client(_codex_sdk_config)

    assert exc_info.value.kind == CODEX_CLI_REQUIRED


@pytest.mark.asyncio
async def test_codex_start_client_retries_once_with_pinned_cli_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CodexAppServer()
    spawns: list[tuple[str, int]] = []

    class FailingThenWorkingClient:
        def __init__(self, config):
            self.config = config

        async def start(self) -> None:
            spawns.append((str(self.config.codex_bin), len(spawns)))

        async def initialize(self) -> SimpleNamespace:
            if len(spawns) == 1:
                raise RuntimeError("app-server protocol mismatch")
            return SimpleNamespace(user_agent="pinned")

        async def close(self) -> None:
            pass

    monkeypatch.setattr(
        "cyrene.model.codex_provider.AsyncCodexClient",
        FailingThenWorkingClient,
    )
    monkeypatch.setattr(
        codex_cli, "ensure_cli", lambda: Path("/cache/codex_cli/versions/latest")
    )
    monkeypatch.setattr(
        codex_cli, "installed_cli_path", lambda: Path("/cache/codex_cli/versions/0.144.4")
    )
    monkeypatch.setattr(
        codex_cli, "installed_version", lambda: "0.144.4"
    )
    monkeypatch.setattr(codex_cli, "sdk_pinned_version", lambda: "0.144.4")

    async def recover(error: BaseException) -> bool:
        assert _is_cli_protocol_mismatch(error)
        return True

    monkeypatch.setattr(
        "cyrene.model.codex_provider._recover_with_pinned_cli",
        recover,
    )

    client = await provider._start_client(_codex_sdk_config)

    assert len(spawns) == 2
    assert str(client.config.codex_bin).endswith("0.144.4")
