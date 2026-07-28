from __future__ import annotations

import argparse
import io
import inspect
import json

import httpx
import pytest


def test_ndjson_decoder_handles_split_and_batched_chunks():
    from cyrene.cli_chat import NdjsonDecoder

    decoder = NdjsonDecoder()

    assert decoder.feed(b'{"type":"reply_del') == []
    assert decoder.feed(
        b'ta","delta":"hello"}\n\n{"type":"reply_done","response":"hello"}\n'
    ) == [
        {"type": "reply_delta", "delta": "hello"},
        {"type": "reply_done", "response": "hello"},
    ]
    assert decoder.finish() == []


def test_ndjson_decoder_accepts_final_line_without_newline():
    from cyrene.cli_chat import NdjsonDecoder

    decoder = NdjsonDecoder()
    assert decoder.feed(b'{"type":"reply_done","response":"done"}') == []
    assert decoder.finish() == [{"type": "reply_done", "response": "done"}]


def test_auth_error_explains_how_to_connect():
    from cyrene.cli_chat import ChatTransport

    request = httpx.Request("GET", "http://cyrene.test/api/status")
    response = httpx.Response(401, request=request, json={"detail": "bad token"})

    error = ChatTransport._http_error(response, response.content)

    assert "CYRENE_AUTH_TOKEN" in str(error)


def test_interactive_command_surface_matches_cli_design():
    from cyrene import cli_chat

    assert "/resume" in cli_chat._COMMANDS
    assert "/deep-research" in cli_chat._COMMANDS
    assert "/context" in cli_chat._COMMANDS
    assert "/config" in cli_chat._COMMANDS
    assert "/project" not in cli_chat._COMMANDS
    assert "/chats" not in cli_chat._COMMANDS
    assert "/use" not in cli_chat._COMMANDS
    assert "/clear" not in cli_chat._COMMANDS


def test_header_uses_compact_agent_title():
    from rich.console import Console
    from cyrene.cli_chat import RichRenderer

    stream = io.StringIO()
    renderer = RichRenderer(color=False)
    renderer.console = Console(file=stream, no_color=True, highlight=False)

    renderer.header("chat_1", "default")

    output = stream.getvalue()
    assert "CYRENE  Agent" in output
    assert "交互式 Agent" not in output
    assert "Ctrl+C 退出" in output


def test_selection_menu_binds_arrow_keys_and_input_has_bottom_rule():
    from cyrene.cli_chat import InteractiveChat

    source = inspect.getsource(InteractiveChat._choose_with_arrows)
    assert '@bindings.add("up")' in source
    assert '@bindings.add("down")' in source
    assert '@bindings.add("enter")' in source
    assert InteractiveChat._input_bottom_rule().startswith("─")


def test_interactive_color_palette_has_distinct_semantic_styles():
    from cyrene.cli_chat import ChatOptions, InteractiveChat, JsonRenderer

    app = InteractiveChat(
        object(),
        JsonRenderer(stream=io.StringIO()),
        ChatOptions(color=True),
    )
    rules = app._terminal_style().style_rules
    styles = dict(rules)

    assert "ansibrightcyan" in styles["prompt"]
    assert "ansibrightblack" in styles["bottom-toolbar"]
    assert "noreverse" in styles["bottom-toolbar"]
    assert "bg:default" in styles["bottom-toolbar"]
    assert "reverse" in styles["selection-current"]


@pytest.mark.asyncio
async def test_rich_renderer_shows_thinking_and_total_elapsed_time(monkeypatch):
    from rich.console import Console
    from cyrene.cli_chat import RichRenderer

    stream = io.StringIO()
    renderer = RichRenderer(color=False)
    renderer.console = Console(file=stream, no_color=True, highlight=False)
    monkeypatch.setattr(renderer, "_start_status", lambda: None)
    now = {"value": 100.0}
    monkeypatch.setattr("cyrene.cli_chat.time.monotonic", lambda: now["value"])

    renderer._turn_started_at = 100.0
    renderer._reasoning_started_at = 100.0
    now["value"] = 101.0
    await renderer.handle({"type": "reasoning_start"})
    await renderer.handle({"type": "reasoning_delta", "delta": "先检查上下文"})
    now["value"] = 103.0
    await renderer.handle({
        "type": "reasoning_done",
        "response": "先检查上下文",
    })
    await renderer.handle({"type": "reply_start"})
    await renderer.handle({"type": "reply_done", "response": "完成"})
    now["value"] = 105.0
    await renderer.end_turn(_success=True)

    output = stream.getvalue()
    assert "思考了 3s（Ctrl+O 展开）" in output
    assert "完成，用时 5s" in output
    assert "先检查上下文" not in output

    renderer.toggle_reasoning()
    assert "思考详情" in stream.getvalue()
    assert "先检查上下文" in stream.getvalue()


def test_elapsed_time_format_matches_compact_cli_style():
    from cyrene.cli_chat import RichRenderer

    assert RichRenderer._format_elapsed(2.9) == "2s"
    assert RichRenderer._format_elapsed(64.2) == "1m 04s"
    assert RichRenderer._format_elapsed(3661) == "1h 01m 01s"


def test_activity_symbol_changes_randomly_without_repeating(monkeypatch):
    from cyrene.cli_chat import RichRenderer

    renderer = RichRenderer(color=False)
    monkeypatch.setattr("cyrene.cli_chat.random.choice", lambda values: values[-1])

    first = renderer._next_activity_symbol()
    renderer._activity_symbol = first
    second = renderer._next_activity_symbol()

    assert set(renderer._ACTIVITY_SYMBOLS) == {"✶", "✸", "✹", "✺", "✷", "◌"}
    assert first in renderer._ACTIVITY_SYMBOLS
    assert second in renderer._ACTIVITY_SYMBOLS
    assert second != first


def test_ctrl_c_requires_second_press(monkeypatch):
    from cyrene.cli_chat import ChatOptions, InteractiveChat, JsonRenderer

    app = InteractiveChat(object(), JsonRenderer(stream=io.StringIO()), ChatOptions())
    moments = iter([10.0, 11.0, 20.0])
    monkeypatch.setattr("cyrene.cli_chat.time.monotonic", lambda: next(moments))

    assert app._arm_ctrl_c_exit() is False
    assert app._arm_ctrl_c_exit() is True
    assert app._arm_ctrl_c_exit() is False


def test_cli_parser_exposes_interactive_chat_options():
    from cyrene.cli import build_parser

    args = build_parser().parse_args([
        "chat",
        "--mode",
        "plan",
        "--no-color",
        "inspect this repository",
    ])

    assert args.command == "chat"
    assert args.mode == "plan"
    assert args.no_color is True
    assert args.text == "inspect this repository"


def test_cli_parser_exposes_run_resume():
    from cyrene.cli import build_parser

    args = build_parser().parse_args([
        "chat", "--chat", "chat_1", "--resume", "--cursor", "7", "--json",
    ])

    assert args.chat_id == "chat_1"
    assert args.resume is True
    assert args.cursor == 7
    assert args.json is True


@pytest.mark.asyncio
async def test_transport_creates_default_workbench_chat_and_streams_events():
    from cyrene.cli_chat import ChatTransport

    requests: list[tuple[str, str, dict | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = None
        if request.content:
            payload = json.loads(request.content)
        requests.append((request.method, request.url.path, payload))
        if request.url.path == "/api/workbench/quick-chat/targets":
            return httpx.Response(
                200,
                json={"defaultProject": {"id": "project_1"}, "targets": []},
            )
        if request.url.path == "/api/workbench/chats":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "chat": {
                        "id": "chat_cli",
                        "projectId": "project_1",
                        "title": "新对话",
                    },
                },
            )
        if request.url.path == "/api/workbench/chats/chat_cli/messages":
            body = "\n".join([
                json.dumps({"type": "ack", "runId": "run_1", "_seq": 1}),
                json.dumps({
                    "type": "tool_call_started",
                    "tool_call_id": "tool_1",
                    "tool": "search_files",
                    "_seq": 2,
                    "runId": "run_1",
                }),
                json.dumps({
                    "type": "reply_delta",
                    "delta": "hello",
                    "_seq": 3,
                    "runId": "run_1",
                }),
                json.dumps({
                    "type": "reply_done",
                    "response": "hello",
                    "_seq": 4,
                    "runId": "run_1",
                }),
            ]) + "\n"
            return httpx.Response(
                200,
                content=body.encode(),
                headers={"content-type": "application/x-ndjson"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="http://cyrene.test",
        transport=httpx.MockTransport(handler),
    )
    events = []

    async def collect(event):
        events.append(event)

    async with ChatTransport(client=client) as transport:
        result = await transport.send(
            "hello",
            mode="default",
            lang="zh",
            attachments=[],
            on_event=collect,
        )

    await client.aclose()
    assert result.response == "hello"
    assert result.run_id == "run_1"
    assert result.cursor == 4
    assert [event["type"] for event in events] == [
        "ack",
        "tool_call_started",
        "reply_delta",
        "reply_done",
    ]
    create_request = next(
        item for item in requests
        if item[0] == "POST" and item[1] == "/api/workbench/chats"
    )
    assert create_request[2] == {
        "project": "project_1",
        "title": "",
    }
    message_request = next(
        item for item in requests
        if item[1] == "/api/workbench/chats/chat_cli/messages"
    )
    assert message_request[2] == {
        "message": "hello",
        "stream": True,
        "mode": "default",
        "lang": "zh",
        "attachments": [],
        "command": "",
    }


@pytest.mark.asyncio
async def test_transport_workbench_answer_converts_json_reply_to_stream_events():
    from cyrene.cli_chat import ChatTransport

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/workbench/chats/chat_cli/answer"
        assert json.loads(request.content) == {
            "question_id": "question_1",
            "answer": "允许一次",
            "mode": "default",
            "stream": True,
        }
        return httpx.Response(
            200,
            content=(
                '{"type":"reply_start"}\n'
                '{"type":"reply_delta","delta":"继续完成"}\n'
                '{"type":"reply_done","response":"继续完成"}\n'
            ).encode(),
            headers={"content-type": "application/x-ndjson"},
        )

    client = httpx.AsyncClient(
        base_url="http://cyrene.test",
        transport=httpx.MockTransport(handler),
    )
    events = []

    async def collect(event):
        events.append(event)

    async with ChatTransport(client=client, chat_id="chat_cli") as transport:
        result = await transport.answer(
            {"id": "question_1"},
            "允许一次",
            mode="default",
            on_event=collect,
        )

    await client.aclose()
    assert result.response == "继续完成"
    assert [event["type"] for event in events] == [
        "reply_start",
        "reply_delta",
        "reply_done",
    ]


@pytest.mark.asyncio
async def test_transport_resumes_existing_workbench_run():
    from cyrene.cli_chat import ChatTransport

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/workbench/chats/chat_cli/run-stream"
        assert request.url.params["cursor"] == "7"
        return httpx.Response(
            200,
            content=(
                '{"type":"reply_delta","delta":"continued","_seq":8}\n'
                '{"type":"reply_done","response":"continued","_seq":9}\n'
            ).encode(),
            headers={"content-type": "application/x-ndjson"},
        )

    client = httpx.AsyncClient(
        base_url="http://cyrene.test",
        transport=httpx.MockTransport(handler),
    )
    events = []

    async def collect(event):
        events.append(event)

    async with ChatTransport(client=client, chat_id="chat_cli") as transport:
        result = await transport.resume(on_event=collect, cursor=7)

    await client.aclose()
    assert result.response == "continued"
    assert result.cursor == 9
    assert [event["type"] for event in events] == ["reply_delta", "reply_done"]


@pytest.mark.asyncio
async def test_new_chat_auto_selects_only_project_without_title(monkeypatch):
    from cyrene.cli_chat import ChatOptions, InteractiveChat, JsonRenderer

    class Transport:
        async def list_projects(self):
            return [{"id": "project_1", "name": "Alpha"}]

        async def new_chat(self, *, project_id="", title=""):
            assert project_id == "project_1"
            assert title == ""
            return {"id": "chat_1", "title": "New chat"}

    app = InteractiveChat(Transport(), JsonRenderer(stream=io.StringIO()), ChatOptions())
    monkeypatch.setattr(
        app,
        "_prompt_text",
        lambda _message: (_ for _ in ()).throw(
            AssertionError("one project must not prompt")
        ),
    )

    await app._new_chat()


@pytest.mark.asyncio
async def test_context_matches_workbench_grouped_composition_and_indents_messages():
    from rich.console import Console
    from cyrene.cli_chat import ChatOptions, InteractiveChat, RichRenderer

    class Transport:
        legacy = False
        chat_id = "chat_1"

        async def context(self):
            return {
                "model": "deepseek-v4-flash",
                "ctxUsed": 224,
                "ctxLimit": 1_000_000,
                "ratio": 0.000224,
                "messageCount": 2,
            }

        async def context_blocks(self):
            return {
                "messageTokens": 224,
                "layers": [
                    {
                        "id": "system_prefix",
                        "totalTokens": 6_800,
                        "blocks": [
                            {
                                "id": "main.system.base",
                                "type": "system",
                                "tokens_est": 6_300,
                            },
                            {
                                "id": "memory.context",
                                "type": "memory",
                                "tokens_est": 500,
                            },
                        ],
                    },
                    {
                        "id": "ephemeral",
                        "totalTokens": 297,
                        "blocks": [
                            {
                                "id": "ephemeral.run",
                                "type": "ephemeral",
                                "tokens_est": 297,
                            },
                        ],
                    },
                    {
                        "id": "messages",
                        "totalTokens": 224,
                        "blocks": [
                            {
                                "id": "segment.user",
                                "type": "user",
                                "tokens_est": 9,
                            },
                            {
                                "id": "segment.assistant",
                                "type": "assistant",
                                "tokens_est": 215,
                            },
                        ],
                    },
                ],
            }

    stream = io.StringIO()
    renderer = RichRenderer(color=False)
    renderer.console = Console(
        file=stream,
        no_color=True,
        highlight=False,
        width=100,
    )
    app = InteractiveChat(Transport(), renderer, ChatOptions())

    await app._show_context()

    output = stream.getvalue()
    assert "对话上下文" in output
    assert "224 tokens" in output
    assert "系统前缀" in output
    assert "基础指令" in output and "6.3k" in output
    assert "临时注入" in output
    assert "对话消息" in output
    assert "  ■ 用户" in output
    assert "  ■ 助手" in output
    assert "deepseek-v4-flash · 224 / 1,000,000" in output


@pytest.mark.asyncio
async def test_resume_menu_selects_session_and_preserves_project_name(monkeypatch):
    from cyrene.cli_chat import ChatOptions, InteractiveChat, JsonRenderer

    used = []

    class Transport:
        async def list_chat_targets(self):
            return [
                {"chatId": "chat_1", "title": "One", "projectName": "Alpha"},
                {"chatId": "chat_2", "title": "Two", "projectName": "Beta"},
            ]

        async def use_chat(self, chat_id):
            used.append(chat_id)
            return {"id": chat_id, "title": "Two"}

    app = InteractiveChat(Transport(), JsonRenderer(stream=io.StringIO()), ChatOptions())

    async def choose_second(_message):
        return "2"

    monkeypatch.setattr(app, "_prompt_text", choose_second)

    await app._resume_chat()

    assert used == ["chat_2"]


@pytest.mark.asyncio
async def test_deep_reflect_and_research_use_backend_commands(monkeypatch):
    from cyrene.cli_chat import ChatOptions, InteractiveChat, JsonRenderer

    calls = []
    app = InteractiveChat(object(), JsonRenderer(stream=io.StringIO()), ChatOptions())

    async def run_turn(text, *, allow_prompt, command=""):
        calls.append((text, allow_prompt, command))
        return True

    monkeypatch.setattr(app, "_run_turn", run_turn)

    await app._command("/deep-reflect")
    await app._command('/deep-research "topic"')

    assert calls == [
        ("/deep-reflect", True, "deep-reflect"),
        ("topic", True, "deep-research"),
    ]


@pytest.mark.asyncio
async def test_runtime_event_writer_only_receives_public_run_events(monkeypatch):
    from cyrene.agent.context import bind_run_context, publish_runtime_event
    from cyrene.observability import debug

    persisted = []
    streamed = []

    async def fake_publish(event, *args, **kwargs):
        persisted.append(dict(event))

    async def collect(event):
        streamed.append(dict(event))

    monkeypatch.setattr(debug, "publish_event", fake_publish)
    binding = bind_run_context(
        session_id="chat_cli",
        round_id="round_1",
        runtime_event_writer=collect,
    )
    try:
        await publish_runtime_event({
            "type": "tool_call_started",
            "tool_call_id": "tool_1",
            "tool": "search_files",
        })
        await publish_runtime_event({
            "type": "llm_call",
            "messages": [{"role": "system", "content": "hidden"}],
        })
    finally:
        binding.reset()

    assert len(persisted) == 2
    assert streamed == [{
        "type": "tool_call_started",
        "tool_call_id": "tool_1",
        "tool": "search_files",
        "round_id": "round_1",
        "session_id": "chat_cli",
    }]


def test_cmd_chat_runs_async_entrypoint(monkeypatch):
    from cyrene import cli

    seen = []

    async def fake_run_chat(args):
        seen.append(args)
        return 0

    monkeypatch.setattr(
        "cyrene.cli_chat.run_chat",
        fake_run_chat,
    )
    args = argparse.Namespace()

    with pytest.raises(SystemExit) as exc:
        cli.cmd_chat(args)

    assert exc.value.code == 0
    assert seen == [args]


def test_bare_cyrene_starts_daemon_then_enters_chat(monkeypatch):
    from cyrene import cli

    calls = []
    monkeypatch.setattr(cli.sys, "argv", ["cyrene"])

    def start(args, quiet=False):
        calls.append(("start", args.command, quiet))
        cli.DAEMON_TOKEN = "desktop-secret"
        return "http://127.0.0.1:4243"

    monkeypatch.setattr(cli, "cmd_start", start)
    monkeypatch.setattr(
        cli,
        "cmd_chat",
        lambda args: calls.append(
            ("chat", args.command, args.url, args.auth_token)
        ),
    )

    cli.main()

    assert calls == [
        ("start", "chat", True),
        ("chat", "chat", "http://127.0.0.1:4243", "desktop-secret"),
    ]
