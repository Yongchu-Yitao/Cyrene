from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


def test_parser_help_and_top_level_language_are_bilingual(monkeypatch):
    from cyrene import cli

    monkeypatch.setattr(cli, "_CLI_LANGUAGE", "en")

    english_help = cli.build_parser("en").format_help()
    chinese_help = cli.build_parser("zh").format_help()

    assert "System status" in english_help
    assert "CLI language" in english_help
    assert "系统状态" in chinese_help
    assert "CLI 语言" in chinese_help
    assert "用法：" in chinese_help

    top_level = cli.build_parser("en").parse_args([
        "--lang",
        "zh",
        "status",
    ])
    compatible_chat_form = cli.build_parser("en").parse_args([
        "chat",
        "--lang",
        "zh",
    ])
    assert top_level.lang == "zh"
    assert compatible_chat_form.lang == "zh"


def test_main_applies_top_level_language_to_non_chat_commands(monkeypatch):
    from cyrene import cli

    seen = []
    monkeypatch.setattr(cli.sys, "argv", ["cyrene", "--lang", "zh", "status"])
    monkeypatch.setattr(cli, "_discover_daemon_url", lambda: "")
    monkeypatch.setattr(cli, "cmd_status", lambda args: seen.append(args.lang))

    cli.main()

    assert seen == ["zh"]


def test_daemon_language_failure_uses_shared_localization_fallback(monkeypatch):
    from cyrene import cli

    def offline(*_args, **_kwargs):
        request = httpx.Request("GET", "http://cyrene.test/api/settings/config")
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(cli.httpx, "get", offline)
    monkeypatch.setattr(cli, "app_language", lambda explicit=None: "en")

    assert cli._daemon_language(None) == "en"


def test_json_command_payload_is_not_localized(monkeypatch, capsys):
    from cyrene import cli

    payload = {
        "model": "example-model",
        "base_url": "https://example.invalid",
        "workers": [{"id": "worker_1", "status": "running"}],
    }
    monkeypatch.setattr(cli, "_CLI_LANGUAGE", "zh")
    monkeypatch.setattr(cli, "_api_json", lambda _path: payload)

    cli.cmd_status(argparse.Namespace(json=True))

    assert json.loads(capsys.readouterr().out) == payload


@pytest.mark.asyncio
async def test_chat_transport_localizes_session_and_attachment_errors_to_chinese(
    tmp_path: Path,
):
    from cyrene.cli_chat import ChatClientError, ChatTransport

    client = httpx.AsyncClient(base_url="http://cyrene.test")
    async with ChatTransport(client=client, language="zh") as transport:
        assert transport.session_label == "新对话"
        with pytest.raises(ChatClientError, match="必须提供 Chat ID"):
            await transport.use_chat("")
        with pytest.raises(ChatClientError, match="未找到附件"):
            await transport.upload([tmp_path / "missing.txt"])
    await client.aclose()


@pytest.mark.asyncio
async def test_chat_transport_localizes_connection_error_to_chinese():
    from cyrene.cli_chat import ChatClientError, ChatTransport

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.AsyncClient(
        base_url="http://cyrene.test",
        transport=httpx.MockTransport(handler),
    )
    async with ChatTransport(client=client, language="zh") as transport:
        with pytest.raises(ChatClientError) as exc:
            await transport.health()
    await client.aclose()

    message = str(exc.value)
    assert "无法连接" in message
    assert "cyrene start" in message
    assert "Cannot connect" not in message


@pytest.mark.asyncio
async def test_chat_language_empty_and_daemon_failure_follow_shared_fallback(
    monkeypatch,
):
    from cyrene import cli_chat

    class EmptyTransport:
        async def get_setting(self, _path):
            return {"app_language": ""}

    class OfflineTransport:
        async def get_setting(self, _path):
            raise cli_chat.ChatClientError("offline")

    monkeypatch.setattr(cli_chat, "app_language", lambda explicit=None: "en")

    assert await cli_chat._resolve_chat_language(None, EmptyTransport()) == "en"
    assert await cli_chat._resolve_chat_language(None, OfflineTransport()) == "en"


@pytest.mark.asyncio
async def test_chat_json_preflight_error_uses_parsed_language(monkeypatch, capsys):
    from cyrene.cli_chat import run_chat

    stdin = io.StringIO("")
    monkeypatch.setattr("sys.stdin", stdin)
    args = SimpleNamespace(
        json=True,
        lang="zh",
        text="",
        list_chats=False,
        resume=False,
    )

    assert await run_chat(args) == 2
    error = capsys.readouterr().err
    assert "需要 TEXT" in error
    assert "requires TEXT" not in error
