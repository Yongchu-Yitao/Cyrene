"""Focused tests for the ACP stdio execution layer (phase 1).

Covers the JSON-RPC protocol surface, the stdio transport (spawn, concurrent
request correlation, notifications, timeouts, bounded stderr, graceful close),
the ACP -> unified AgentEvent mapper (message/tool/permission/run lifecycle,
secret redaction), the installation-keyed process manager, the ACP driver /
connection SPI, and the ``run_external_agent_turn`` integration seam.

Most subprocess tests drive a real child process (the fake ACP server below)
through the same stdio path production uses; no shell is involved.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any

import pytest

from cyrene.agent_runtime import (
    ACP_STDIO_DRIVER,
    AcpProcessManager,
    AcpRuntimeService,
    AcpStdioDriver,
    AcpStdioTransport,
    AcpTransportError,
    EnvModelBinder,
    AgentRuntimeError,
    is_terminal_run_event,
    redact_secrets,
    run_external_agent_turn,
)
from cyrene.agent_runtime.acp_events import AcpEventMapper
from cyrene.agent_runtime.acp_protocol import (
    ACP_METHOD_INITIALIZE,
    ACP_METHOD_SESSION_NEW,
    ACP_METHOD_SESSION_PROMPT,
    ERROR_METHOD_NOT_FOUND,
    JsonRpcError,
    build_error,
    build_notification,
    build_request,
    build_response,
    error_from_frame,
    frame_id,
    frame_kind,
    parse_frame,
)
from cyrene.agent_runtime.acp_transport import is_valid_bare_command
from cyrene.agent_runtime.builtin import BUILTIN_INSTALLATION_ID
from cyrene.agent_runtime.runtime_service import AcpConnection, _fresh_session_prompt
from cyrene.agent_runtime.runtime_service import _materialize_acp_artifacts
from cyrene.agent_runtime.notices import (
    LeadingOperationalNoticeFilter,
    classify_operational_notice,
    split_leading_operational_notices,
)

# ---------------------------------------------------------------------------
# Fake ACP server (stdlib only) driven through real stdio.
# ---------------------------------------------------------------------------


def test_operational_transport_notice_is_split_from_assistant_reply() -> None:
    warning = "Warning: Falling back from WebSockets to HTTPS transport. request timed out"
    notices, visible = split_leading_operational_notices(
        warning + "\n\nI am Codex, your coding assistant."
    )

    assert visible == "I am Codex, your coding assistant."
    assert notices == [{
        "severity": "warning",
        "category": "transport_fallback",
        "message": warning,
        "source": "agent_transport",
        "terminal": False,
    }]


def test_operational_notice_filter_handles_split_stream_prefix_without_delaying_prose() -> None:
    normalizer = LeadingOperationalNoticeFilter()

    assert normalizer.feed("Warn") == ([], "")
    assert normalizer.feed("ing: Falling back from WebSockets") == ([], "")
    notices, visible = normalizer.feed(
        " to HTTPS transport. request timed out\n\nHello"
    )
    assert notices[0]["category"] == "transport_fallback"
    assert visible == "Hello"
    assert normalizer.feed(" world") == ([], " world")

    ordinary = LeadingOperationalNoticeFilter()
    assert ordinary.feed("What can I help with?") == ([], "What can I help with?")


def test_operational_notice_classifier_does_not_capture_normal_warning_prose() -> None:
    assert classify_operational_notice("Warning: this API deletes data.") is None

_FAKE_SERVER_SOURCE = r'''
import json
import os
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def notify(method, params):
    send({"jsonrpc": "2.0", "method": method, "params": params})


mode = os.environ.get("FAKE_ACP_MODE", "normal")
if mode == "stderr_flood":
    sys.stderr.write("x" * 200000)
    sys.stderr.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    frame = json.loads(line)
    req_id = frame.get("id")
    method = frame.get("method")
    params = frame.get("params") or {}
    if "id" not in frame:
        continue
    if method == "initialize":
        if mode in ("no_initialize", "no_cancel"):
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
            continue
        if mode == "slow_initialize":
            time.sleep(5)
        send({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": 1,
            "agentCapabilities": {
                "output": {"streaming": "supported"},
                "loadSession": mode in ("history_replay", "load_failure", "load_crash"),
            },
        }})
    elif method == "session/new":
        result = {
            "id": "ses_fake_1",
            "configOptions": [{
                "id": "model", "name": "Model", "category": "model", "type": "select",
                "currentValue": "model-a",
                "options": [{"value": "model-a", "name": "Model A"}, {"value": "model-b", "name": "Model B"}],
            }],
        }
        if mode == "large_frame":
            result["padding"] = "x" * 2000000
        send({"jsonrpc": "2.0", "id": req_id, "result": result})
    elif method == "session/set_config_option":
        send({"jsonrpc": "2.0", "id": req_id, "result": {
            "configOptions": [{
                "id": params.get("configId"), "name": "Model", "category": "model", "type": "select",
                "currentValue": params.get("value"),
                "options": [{"value": "model-a", "name": "Model A"}, {"value": "model-b", "name": "Model B"}],
            }],
        }})
    elif method == "session/load":
        if mode == "load_crash":
            sys.exit(17)
        if mode == "load_failure":
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": "Internal error: OpenCode service failure"}})
            continue
        if mode == "history_replay":
            notify("session/update", {
                "sessionId": params.get("sessionId", ""),
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "OLD REPLY"}},
            })
        send({"jsonrpc": "2.0", "id": req_id, "result": {"id": params.get("sessionId", "")}})
    elif method == "session/prompt":
        prompt = params.get("prompt", "")
        if isinstance(prompt, list):
            prompt_text = "".join(part.get("text", "") for part in prompt if isinstance(part, dict))
        elif isinstance(prompt, dict):
            prompt_text = prompt.get("text", "")
        else:
            prompt_text = str(prompt)
        if mode in ("official", "history_replay", "load_failure", "load_crash"):
            session_id = params.get("sessionId", "")
            notify("session/update", {
                "sessionId": session_id,
                "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "new " if mode == "history_replay" else "official done"}},
            })
            if mode == "history_replay":
                time.sleep(0.02)
                notify("session/update", {
                    "sessionId": session_id,
                    "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "reply"}},
                })
            notify("session/update", {
                "sessionId": session_id,
                "update": {"sessionUpdate": "usage_update", "used": 12, "size": 100},
            })
            send({"jsonrpc": "2.0", "id": req_id, "result": {"stopReason": "end_turn"}})
            continue
        send({"jsonrpc": "2.0", "id": req_id, "result": {"id": params.get("sessionId", "")}})
        if mode == "prompt_only":
            continue
        session_id = params.get("sessionId", "")
        notify("session/prompt_updated", {
            "sessionId": session_id,
            "prompt": {"id": "prompt_1", "status": "running", "message": "thinking"},
        })
        notify("tool/updated", {
            "sessionId": session_id,
            "toolCallId": "tool_1", "toolName": "bash",
            "toolInput": {"command": "echo hi"}, "toolStatus": "running",
        })
        notify("permission/requested", {
            "sessionId": session_id,
            "permissionRequest": {
                "id": "perm_1", "description": "Run command?",
                "options": [{"id": "allow_once", "label": "Allow once"}, {"id": "deny", "label": "Deny"}],
            },
        })
        notify("message/updated", {
            "sessionId": session_id,
            "message": {"id": "msg_1", "role": "assistant",
                        "content": [{"type": "text", "text": "done"}]},
        })
        notify("tool/updated", {
            "sessionId": session_id, "toolCallId": "tool_1", "toolName": "bash",
            "toolResult": {"exitCode": 0}, "toolStatus": "completed",
        })
        notify("session/prompt_updated", {
            "sessionId": session_id, "prompt": {"id": "prompt_1", "status": "completed"},
        })
    elif method == "session/cancel":
        if mode == "no_cancel":
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
        elif mode == "interrupt_only":
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
        else:
            send({"jsonrpc": "2.0", "id": req_id, "result": {"cancelled": True}})
    elif method == "session/interrupt":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"cancelled": True}})
    elif method == "permissions/response":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"received": params.get("response", {})}})
    else:
        send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
'''


@pytest.fixture
def fake_acp_bin(monkeypatch, tmp_path):
    """Write an executable fake ACP server named ``fake-acp`` on PATH."""
    script = tmp_path / "fake-acp"
    script.write_text("#!/usr/bin/env python3\n" + _FAKE_SERVER_SOURCE)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    return str(script)


def _installation(**overrides):
    record = {
        "installation_id": "agent_opencode_default",
        "agent_id": "opencode",
        "display_name": "OpenCode",
        "version": "1.0.0",
        "driver": "acp_stdio",
        "protocol_version": 1,
        "command": "fake-acp",
        "enabled": True,
        "install_state": "installed",
        "runtime_state": "pending_transport",
        "auth_state": "not_configured",
        "model_access": {"mode": "agent_managed"},
        "capabilities": {"output": {"streaming": "supported"}},
    }
    record.update(overrides)
    return record


def test_fresh_session_prompt_bridges_history_without_repeating_current_message():
    bridged = _fresh_session_prompt(
        {
            "messages": [
                {"role": "user", "content": "列出图片"},
                {"role": "assistant", "content": "第一张是 bg.png"},
                {"role": "user", "content": "第一张"},
            ],
        },
        "第一张",
    )

    assert '"content":"列出图片"' in bridged
    assert '"content":"第一张是 bg.png"' in bridged
    assert bridged.count("第一张") == 2  # one historical answer + current message
    assert bridged.endswith("Current user message:\n第一张")


def test_fresh_session_prompt_leaves_first_message_unchanged():
    assert _fresh_session_prompt(
        {"messages": [{"role": "user", "content": "hello"}]},
        "hello",
    ) == "hello"


async def _collect_notifications(transport: AcpStdioTransport, count: int, timeout: float = 5.0):
    out: list[dict[str, Any]] = []
    async with asyncio.timeout(timeout):
        async for frame in transport.notifications():
            out.append(frame)
            if len(out) >= count:
                break
    return out


async def _make_transport(bin_path: str, **kwargs):
    kwargs.setdefault("env", {"FAKE_ACP_MODE": os.environ.get("FAKE_ACP_MODE", "normal")})
    transport = AcpStdioTransport("fake-acp", **kwargs)
    await transport.start()
    return transport


# ---------------------------------------------------------------------------
# Protocol surface
# ---------------------------------------------------------------------------

def test_jsonrpc_builders_and_frame_classification():
    request = build_request(ACP_METHOD_INITIALIZE, {"protocolVersion": 1}, 7)
    assert request == {"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {"protocolVersion": 1}}
    assert frame_kind(request) == "request"
    assert frame_id(request) == 7
    assert frame_kind(build_notification("tool/updated")) == "notification"
    assert frame_kind(build_response({"ok": True}, 7)) == "response"
    error = build_error(ERROR_METHOD_NOT_FOUND, "nope", 7)
    assert frame_kind(error) == "error"
    assert error_from_frame(error).is_method_not_found
    assert parse_frame("   \n") is None
    with pytest.raises(ValueError):
        parse_frame("{not json}")
    with pytest.raises(JsonRpcError):
        raise error_from_frame(error)


def test_redact_secrets_recursive():
    payload = {
        "requestId": "perm_1",
        "options": [{"id": "allow_once", "label": "Allow"}],
        "toolInput": {"command": "echo hi", "apiKey": "sk-123", "nested": {"token": "t"}},
        "message": "Authorization: Bearer abc123",
    }
    cleaned = redact_secrets(payload)
    assert cleaned["requestId"] == "perm_1"
    assert cleaned["options"][0]["id"] == "allow_once"
    assert "apiKey" not in cleaned["toolInput"]
    assert "token" not in cleaned["toolInput"]["nested"]
    assert cleaned["message"] == "[redacted]"


@pytest.mark.asyncio
async def test_connection_captures_and_switches_agent_model(fake_acp_bin):
    from cyrene.agent_runtime.runtime_service import AcpConnection

    transport = await _make_transport(fake_acp_bin)
    connection = AcpConnection(
        installation={"installation_id": "agent_fake", "agent_id": "fake"},
        transport=transport,
        chat_id="chat_config",
        run_id="run_config",
    )
    try:
        opened = await connection.open_session({})
        assert opened["configOptions"][0]["category"] == "model"
        assert connection.config_options[0]["currentValue"] == "model-a"
        updated = await connection.set_config_option("model", "model-b")
        assert updated[0]["currentValue"] == "model-b"
    finally:
        await connection.close()


# ---------------------------------------------------------------------------
# ACP -> unified event mapping
# ---------------------------------------------------------------------------

def _frame(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def test_mapper_run_message_tool_permission_lifecycle():
    mapper = AcpEventMapper()
    ctx = dict(agent_id="opencode", installation_id="agent_opencode_default",
               chat_id="wbchat_1", run_id="run_1", session_id="ses_1")
    events: list[dict[str, Any]] = []
    events.extend(mapper.normalize(_frame("session/prompt_updated", {
        "sessionId": "ses_1", "prompt": {"id": "prompt_1", "status": "running", "message": "hi"},
    }), **ctx))
    events.extend(mapper.normalize(_frame("tool/updated", {
        "toolCallId": "tool_1", "toolName": "bash", "toolInput": {"command": "ls"}, "toolStatus": "running",
    }), **ctx))
    events.extend(mapper.normalize(_frame("permission/requested", {
        "permissionRequest": {
            "id": "perm_1", "description": "Run?",
            "options": [{"id": "allow_once", "label": "Allow once"}, {"id": "deny", "label": "Deny"}],
        },
    }), **ctx))
    events.extend(mapper.normalize(_frame("message/updated", {
        "message": {"id": "msg_1", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
    }), **ctx))
    events.extend(mapper.normalize(_frame("tool/updated", {
        "toolCallId": "tool_1", "toolName": "bash", "toolResult": {"exitCode": 0}, "toolStatus": "completed",
    }), **ctx))
    events.extend(mapper.normalize(_frame("session/prompt_updated", {
        "sessionId": "ses_1", "prompt": {"id": "prompt_1", "status": "completed"},
    }), **ctx))

    types = [event["type"] for event in events]
    assert types[0] == "run.started"
    assert "message.delta" in types
    assert "tool.started" in types
    assert "permission.requested" in types
    assert "message.completed" in types
    assert "tool.completed" in types
    assert types[-1] == "run.completed"
    assert is_terminal_run_event(types[-1])

    perm = next(event for event in events if event["type"] == "permission.requested")
    options = perm["payload"]["options"]
    assert [option["id"] for option in options] == ["allow_once", "deny"]
    assert options[0]["label"] == "Allow once"
    assert perm["extensions"]["acp"] == {}

    tool_completed = next(event for event in events if event["type"] == "tool.completed")
    assert tool_completed["payload"]["toolCallId"] == "tool_1"
    assert tool_completed["payload"]["failed"] is False


def test_mapper_partial_tool_output_does_not_become_false_failure():
    mapper = AcpEventMapper()
    ctx = dict(agent_id="opencode", installation_id="agent_opencode_default",
               chat_id="wbchat_1", run_id="run_1", session_id="ses_1")
    partial = mapper.normalize(_frame("session/update", {
        "sessionId": "ses_1",
        "update": {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tool_stream",
            "title": "ls ~/Desktop",
            "status": "in_progress",
            "content": [{"type": "content", "content": {"type": "text", "text": "bg.png\n"}}],
        },
    }), **ctx)
    completed = mapper.normalize(_frame("session/update", {
        "sessionId": "ses_1",
        "update": {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tool_stream",
            "title": "ls ~/Desktop",
            "status": "completed",
            "content": [{"type": "content", "content": {"type": "text", "text": "bg.png\n"}}],
        },
    }), **ctx)

    assert [event["type"] for event in partial] == ["tool.started", "tool.updated"]
    assert partial[-1]["payload"]["status"] == "running"
    assert [event["type"] for event in completed] == ["tool.completed"]
    assert completed[0]["payload"]["failed"] is False


def test_mapper_removes_inline_image_data_from_tool_event():
    mapper = AcpEventMapper()
    events = mapper.normalize(_frame("session/update", {
        "sessionId": "s",
        "update": {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "read-image",
            "title": "comparison.png",
            "status": "completed",
            "content": [{
                "type": "content",
                "content": {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": "very-large-base64",
                },
            }],
        },
    }), agent_id="a", installation_id="i", chat_id="c", run_id="r", session_id="s")

    serialized = json.dumps(events)
    assert "very-large-base64" not in serialized
    assert events[-1]["type"] == "tool.completed"


def test_materialize_acp_inline_image_for_cyrene_viewer(
    tmp_path, monkeypatch, real_pillow_modules
):
    from cyrene.runtime import attachments as attachment_service

    monkeypatch.setattr(attachment_service, "EXPORTS_DIR", tmp_path)
    # Some legacy route-test modules install a process-wide PIL shim during
    # pytest collection. This image-boundary test must exercise real decoding.
    monkeypatch.setattr(attachment_service, "Image", real_pillow_modules)
    # 1x1 transparent PNG.
    encoded = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
        "AScY42YAAAAASUVORK5CYII="
    )
    frame = _frame("session/update", {
        "sessionId": "s",
        "update": {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "read-image",
            "title": "/Users/example/Desktop/comparison.png",
            "status": "completed",
            "content": [{
                "type": "content",
                "content": {"type": "image", "mimeType": "image/png", "data": encoded},
            }],
        },
    })

    materialized = _materialize_acp_artifacts(frame)

    assert len(materialized) == 1
    assert materialized[0]["name"] == "comparison.png"
    assert materialized[0]["kind"] == "image"
    assert materialized[0]["url"].startswith("/api/chat/export/")
    assert (tmp_path / materialized[0]["id"]).read_bytes() == base64.b64decode(encoded)

    # OpenCode persists the same result as a file attachment with a data URL.
    opencode_frame = _frame("session/update", {
        "sessionId": "s",
        "update": {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "read-image",
            "title": "/Users/example/Desktop/comparison.png",
            "status": "completed",
            "content": [{
                "type": "content",
                "content": {
                    "type": "file",
                    "mime": "image/png",
                    "url": f"data:image/png;base64,{encoded}",
                },
            }],
        },
    })
    attachment_materialized = _materialize_acp_artifacts(opencode_frame)
    assert attachment_materialized[0]["id"] == materialized[0]["id"]

    # Standard resource links and artifact URIs use the same viewer path.
    local_image = tmp_path / "agent-resource.png"
    local_image.write_bytes(base64.b64decode(encoded))
    resource_frame = _frame("artifact/updated", {
        "artifact": {
            "id": "artifact-1",
            "title": "agent-resource.png",
            "kind": "image",
            "mimeType": "image/png",
            "uri": local_image.as_uri(),
        },
    })
    resource_materialized = _materialize_acp_artifacts(resource_frame)
    assert resource_materialized[0]["name"] == "agent-resource.png"
    assert resource_materialized[0]["url"].startswith("/api/chat/export/")
    assert (tmp_path / resource_materialized[0]["id"]).read_bytes() == base64.b64decode(encoded)

    remote_frame = _frame("artifact/updated", {
        "artifact": {
            "id": "artifact-remote",
            "title": "remote.png",
            "kind": "image",
            "uri": "https://cdn.example.test/assets/remote.png",
        },
    })
    remote_materialized = _materialize_acp_artifacts(remote_frame)
    assert remote_materialized[0]["kind"] == "image"
    assert remote_materialized[0]["url"] == "https://cdn.example.test/assets/remote.png"

    text_content = base64.b64encode(b"universal artifact\n").decode("ascii")
    text_frame = _frame("artifact/updated", {
        "artifact": {
            "id": "artifact-text",
            "title": "result.md",
            "kind": "file",
            "mimeType": "text/markdown",
            "data": text_content,
        },
    })
    text_materialized = _materialize_acp_artifacts(text_frame)
    assert text_materialized[0]["kind"] == "code"
    assert text_materialized[0]["content_type"] == "text/markdown"
    assert (tmp_path / text_materialized[0]["id"]).read_bytes() == b"universal artifact\n"


def test_mapper_dedupe_terminal_events_and_redacts():
    mapper = AcpEventMapper()
    ctx = dict(agent_id="opencode", installation_id="agent_opencode_default",
               chat_id="wbchat_1", run_id="run_1", session_id="ses_1")
    first = mapper.normalize(_frame("session/prompt_updated", {
        "prompt": {"id": "prompt_1", "status": "failed", "error": "boom"},
    }), **ctx)
    second = mapper.normalize(_frame("session/prompt_updated", {
        "prompt": {"id": "prompt_1", "status": "completed"},
    }), **ctx)
    run_events = [event for event in first + second if event["type"].startswith("run.")]
    assert [event["type"] for event in run_events] == ["run.started", "run.failed"]
    # Direct run/* variants also map, and only once.
    mapper2 = AcpEventMapper()
    extra = mapper2.normalize(_frame("run/completed", {"runId": "run_1"}), **ctx)
    assert extra[0]["type"] == "run.completed"
    assert mapper2.normalize(_frame("run/completed", {}), **ctx) == []


def test_mapper_tolerant_field_variants():
    mapper = AcpEventMapper()
    ctx = dict(agent_id="x", installation_id="i", chat_id="c", run_id="r", session_id="s")
    # delta delivered at params root with messageId
    events = mapper.normalize(_frame("message/updated", {
        "messageId": "m1", "delta": {"type": "text", "text": "tok"},
    }), **ctx)
    assert any(e["type"] == "message.delta" and e["payload"]["delta"] == "tok" for e in events)
    # permission request fields at root level
    events = mapper.normalize(_frame("permission/requested", {
        "requestId": "p1", "description": "D",
        "options": [{"id": "yes", "label": "Yes"}],
    }), **ctx)
    assert events[0]["payload"]["requestId"] == "p1"
    # usage + artifact
    events = mapper.normalize(_frame("usage/updated", {"usage": {"totalTokens": 10}}), **ctx)
    assert events[0]["payload"]["totalTokens"] == 10
    events = mapper.normalize(_frame("artifact/updated", {"artifact": {"id": "a1", "title": "t"}}), **ctx)
    assert events[0]["type"] == "artifact.created"
    events = mapper.normalize(_frame("artifact/updated", {"artifact": {"id": "a1", "title": "t2"}}), **ctx)
    assert events[0]["type"] == "artifact.updated"


def test_mapper_official_v1_session_updates_and_permission_request():
    mapper = AcpEventMapper()
    ctx = dict(agent_id="x", installation_id="i", chat_id="c", run_id="r", session_id="s")
    message = mapper.normalize(_frame("session/update", {
        "sessionId": "s",
        "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hello"}},
    }), **ctx)
    assert message[0]["type"] == "message.delta"
    assert message[0]["payload"]["delta"] == "hello"
    usage = mapper.normalize(_frame("session/update", {
        "sessionId": "s",
        "update": {
            "sessionUpdate": "usage_update", "used": 12, "size": 100,
            "segments": [{"key": "messages", "label": "Messages", "tokens": 8}],
        },
    }), **ctx)
    assert usage[0]["type"] == "usage.updated"
    assert usage[0]["payload"]["size"] == 100
    assert usage[0]["payload"]["segments"][0]["tokens"] == 8
    permission = mapper.permission_request({
        "jsonrpc": "2.0", "id": 23, "method": "session/request_permission",
        "params": {
            "sessionId": "s",
            "toolCall": {"toolCallId": "tool-1", "title": "Run command"},
            "options": [{"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"}],
        },
    }, **ctx)
    assert permission[0]["payload"]["requestId"] == "23"
    assert permission[0]["payload"]["options"] == [
        {"id": "allow_once", "label": "Allow once", "kind": "allow_once"}
    ]
    commands = mapper.normalize(_frame("session/update", {
        "sessionId": "s",
        "update": {
            "sessionUpdate": "available_commands_update",
            "availableCommands": [{"name": "review", "description": "Review changes"}],
        },
    }), **ctx)
    assert commands[0]["payload"]["commands"][0]["name"] == "review"
    config = mapper.normalize(_frame("session/update", {
        "sessionId": "s",
        "update": {
            "sessionUpdate": "config_option_update",
            "configOptions": [{"id": "model", "category": "model", "currentValue": "m1"}],
        },
    }), **ctx)
    assert config[0]["payload"]["configOptions"][0]["id"] == "model"
    plan = mapper.normalize(_frame("session/update", {
        "sessionId": "s",
        "update": {
            "sessionUpdate": "plan",
            "entries": [{"content": "Inspect", "status": "in_progress"}],
        },
    }), **ctx)
    assert plan[0]["payload"]["plan"]["status"] == "active"
    elicitation = mapper.elicitation_request({
        "jsonrpc": "2.0", "id": 24, "method": "elicitation/create",
        "params": {
            "message": "Choose output",
            "requestedSchema": {
                "type": "object",
                "required": ["format"],
                "properties": {"format": {"type": "string", "enum": ["md", "pdf"]}},
            },
        },
    }, **ctx)
    assert elicitation[0]["payload"]["schema"]["required"] == ["format"]


# ---------------------------------------------------------------------------
# Transport: subprocess lifecycle, correlation, bounds, timeouts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transport_concurrent_requests_and_notifications(fake_acp_bin):
    transport = await _make_transport(fake_acp_bin)
    try:
        init_result, session_result = await asyncio.gather(
            transport.request(ACP_METHOD_INITIALIZE, {"protocolVersion": 1}),
            transport.request(ACP_METHOD_SESSION_NEW, {}),
        )
        assert init_result["protocolVersion"] == 1
        assert session_result["id"] == "ses_fake_1"
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_transport_accepts_frame_above_asyncio_default_limit(fake_acp_bin, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_MODE", "large_frame")
    transport = await _make_transport(
        fake_acp_bin,
        env={"FAKE_ACP_MODE": "large_frame"},
    )
    try:
        result = await transport.request(ACP_METHOD_SESSION_NEW, {})
        assert len(result["padding"]) == 2000000
        assert transport.is_closed is False
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_transport_initialize_tolerant_fallback(fake_acp_bin, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_MODE", "no_initialize")
    transport = await _make_transport(fake_acp_bin)
    try:
        result = await transport.initialize()
        assert result["protocolVersion"] == 0  # fallback: no protocolVersion reported
        assert transport.negotiated_by_fallback is True
        assert transport.agent_capabilities == {}
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_transport_prompt_notifications_and_close(fake_acp_bin):
    transport = await _make_transport(fake_acp_bin)
    try:
        await transport.initialize()
        session_result = await transport.request(ACP_METHOD_SESSION_NEW, {})
        session_id = session_result["id"]
        await transport.request(ACP_METHOD_SESSION_PROMPT, {
            "sessionId": session_id, "prompt": {"text": "hello"},
        })
        frames = await _collect_notifications(transport, 6)
        methods = [frame["method"] for frame in frames]
        assert "session/prompt_updated" in methods
        assert "permission/requested" in methods
        assert "message/updated" in methods
    finally:
        await transport.close()
    assert transport.is_closed
    with pytest.raises(AcpTransportError):
        await transport.request(ACP_METHOD_SESSION_NEW, {})


@pytest.mark.asyncio
async def test_transport_close_reaps_process_after_protocol_eof(fake_acp_bin):
    transport = await _make_transport(fake_acp_bin)
    process = transport.process
    assert process is not None
    # Model an Agent whose protocol stream disappears while its process remains
    # alive. The transport is unusable, but close must still reap the child.
    transport._closed = True
    assert process.returncode is None

    await transport.close()

    assert process.returncode is not None


@pytest.mark.asyncio
async def test_transport_request_timeout(fake_acp_bin, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_MODE", "slow_initialize")
    transport = AcpStdioTransport(
        "fake-acp",
        initialize_timeout=0.3,
        request_timeout=0.3,
        shutdown_grace_seconds=0.5,
        kill_grace_seconds=0.5,
    )
    await transport.start()
    try:
        with pytest.raises(AcpTransportError) as excinfo:
            await transport.initialize()
        assert excinfo.value.kind == "agent_crashed"
        assert excinfo.value.detail["kind"] == "timeout"
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_transport_rejects_non_bare_command_and_missing_binary(fake_acp_bin):
    assert not is_valid_bare_command("../evil")
    assert not is_valid_bare_command("opencode --flag")
    assert not is_valid_bare_command("/abs/path")
    assert is_valid_bare_command("opencode")

    bad = AcpStdioTransport("../evil")
    with pytest.raises(AcpTransportError) as excinfo:
        await bad.start()
    assert excinfo.value.kind == "dependency_missing"

    missing = AcpStdioTransport("no-such-agent-binary-xyz", which_fn=lambda _cmd: None)
    with pytest.raises(AcpTransportError) as excinfo:
        await missing.start()
    assert excinfo.value.kind == "dependency_missing"


@pytest.mark.asyncio
async def test_transport_stderr_capture_bounded(fake_acp_bin, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_MODE", "stderr_flood")
    transport = AcpStdioTransport(
        "fake-acp",
        stderr_limit_bytes=4096,
        env={"FAKE_ACP_MODE": "stderr_flood"},
    )
    await transport.start()
    try:
        await transport.initialize()
        # Give the stderr reader a moment to drain before snapshotting.
        for _ in range(50):
            snapshot = transport.stderr_snapshot(max_chars=0)
            if snapshot["truncated"]:
                break
            await asyncio.sleep(0.02)
        snapshot = transport.stderr_snapshot(max_chars=0)
        assert snapshot["truncated"] is True
        assert snapshot["bytes"] <= 4096
    finally:
        await transport.close()


# ---------------------------------------------------------------------------
# Process manager
# ---------------------------------------------------------------------------

def test_process_manager_validation_kinds():
    manager = AcpProcessManager()
    with pytest.raises(AgentRuntimeError) as excinfo:
        manager.validate_installation(None)
    assert excinfo.value.kind == "dependency_missing"
    with pytest.raises(AgentRuntimeError) as excinfo:
        manager.validate_installation(_installation(driver="jsonrpc"))
    assert excinfo.value.kind == "protocol_mismatch"
    with pytest.raises(AgentRuntimeError) as excinfo:
        manager.validate_installation(_installation(enabled=False))
    assert excinfo.value.kind == "agent_disabled"
    with pytest.raises(AgentRuntimeError) as excinfo:
        manager.validate_installation(_installation(install_state="pending"))
    assert excinfo.value.kind == "dependency_missing"
    with pytest.raises(AgentRuntimeError) as excinfo:
        manager.validate_installation(_installation(command="rm -rf /"))
    assert excinfo.value.kind == "dependency_missing"
    with pytest.raises(AgentRuntimeError) as excinfo:
        manager.validate_installation(_installation(runtime_state="error"))
    assert excinfo.value.kind == "agent_crashed"


def test_process_manager_profile_args_ignore_manifest_args():
    manager = AcpProcessManager()
    record = _installation(manifest={
        "drivers": [{"kind": "acp_stdio", "command": "opencode", "args": ["--evil"]}],
    })
    assert manager.resolve_args(record) == ("acp",)
    assert manager.resolve_args(_installation(agent_id="unknown-agent")) == ()


def test_safe_proxy_environment_accepts_explicit_credential_free_proxy():
    from cyrene.agent_runtime import acp_transport

    resolved = acp_transport.safe_proxy_environment(
        {"HTTP_PROXY": "http://127.0.0.1:6578", "https_proxy": "http://127.0.0.1:6578"},
    )
    assert resolved["HTTP_PROXY"] == "http://127.0.0.1:6578"
    assert resolved["https_proxy"] == "http://127.0.0.1:6578"


def test_safe_proxy_environment_rejects_embedded_credentials():
    from cyrene.agent_runtime import acp_transport

    assert acp_transport.safe_proxy_environment(
        {"HTTPS_PROXY": "http://user:secret@proxy.example:8080"},
    ) == {}


def test_configured_agent_proxy_is_strictly_opt_in(monkeypatch):
    from cyrene.agent_runtime import process_manager
    from cyrene.runtime import config_store

    values = {"external_agent_proxy_enabled": False, "external_agent_proxy_port": 6578}
    monkeypatch.setattr(config_store, "get_setting", lambda key, default=None: values.get(key, default))
    assert process_manager.configured_agent_proxy_environment() == {}
    values["external_agent_proxy_enabled"] = True
    proxy = process_manager.configured_agent_proxy_environment()
    assert proxy["HTTPS_PROXY"] == "http://127.0.0.1:6578"
    assert proxy["all_proxy"] == "http://127.0.0.1:6578"
    assert proxy["NO_PROXY"] == "127.0.0.1,localhost,::1"


@pytest.mark.asyncio
async def test_process_manager_shares_and_releases_transport(fake_acp_bin):
    manager = AcpProcessManager()
    record = _installation()
    first = await manager.get_transport(record)
    second = await manager.get_transport(record)
    assert first is second
    assert manager.active_count() == 1
    assert manager.get("agent_opencode_default") is first
    await manager.release("agent_opencode_default")
    assert manager.active_count() == 0
    await manager.close_all()  # idempotent


@pytest.mark.asyncio
async def test_process_manager_restarts_for_changed_scoped_credentials(fake_acp_bin):
    manager = AcpProcessManager()
    record = _installation()
    first = await manager.get_transport(
        record,
        env={"OPENAI_API_KEY": "gateway-token-first"},
    )
    second = await manager.get_transport(
        record,
        env={"OPENAI_API_KEY": "gateway-token-second"},
    )

    assert second is not first
    assert first.is_closed
    assert not second.is_closed
    assert manager.active_count() == 1
    await manager.close_all()


# ---------------------------------------------------------------------------
# Driver / connection SPI + runtime service seam
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_driver_inspect_declarative(fake_acp_bin):
    driver = AcpStdioDriver(process_manager=AcpProcessManager())
    descriptor = await driver.inspect(_installation())
    assert descriptor.driver == ACP_STDIO_DRIVER
    assert descriptor.state == "ready"
    assert descriptor.default_model_access == "agent_managed"
    disabled = await driver.inspect(_installation(enabled=False))
    assert disabled.state == "disabled"
    with pytest.raises(AgentRuntimeError) as excinfo:
        await driver.inspect(_installation(driver="jsonrpc"))
    assert excinfo.value.kind == "protocol_mismatch"


@pytest.mark.asyncio
async def test_connection_permission_response_preserves_option_id(fake_acp_bin):
    manager = AcpProcessManager()
    record = _installation()
    transport = await manager.get_transport(record)
    conn = AcpConnection(installation=record, transport=transport, chat_id="c1", run_id="r1")
    try:
        await conn.open_session({})
        assert conn.session_id == "ses_fake_1"
        conn._pending_permission_request_ids["23"] = 23
        result = await conn.respond_permission("23", "allow_once")
        assert result["optionId"] == "allow_once"
        assert result["result"]["received"]["optionId"] == "allow_once"
        with pytest.raises(AgentRuntimeError) as excinfo:
            await conn.respond_permission("expired", "allow_once")
        assert excinfo.value.kind == "request_expired"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_connection_full_turn_events(fake_acp_bin):
    manager = AcpProcessManager()
    record = _installation()
    transport = await manager.get_transport(record)
    conn = AcpConnection(installation=record, transport=transport, chat_id="c1", run_id="r1")
    try:
        await conn.open_session({})
        await conn.prompt({"text": "hello"})
        events = []
        async for event in conn.events():
            events.append(event)
            if is_terminal_run_event(event["type"]):
                break
        types = [event["type"] for event in events]
        assert "run.started" in types
        assert "permission.requested" in types
        assert types[-1] == "run.completed"
        assert all(event["installationId"] == "agent_opencode_default" for event in events)
        perm = next(event for event in events if event["type"] == "permission.requested")
        assert perm["payload"]["options"][0]["id"] == "allow_once"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_connection_cancel_is_official_notification(fake_acp_bin):
    manager = AcpProcessManager()
    record = _installation()
    transport = await manager.get_transport(record)
    conn = AcpConnection(installation=record, transport=transport)
    try:
        await conn.open_session({})
        await conn.cancel("run_1")
        assert conn._cancel_started is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_connection_steer_unsupported(fake_acp_bin):
    manager = AcpProcessManager()
    transport = await manager.get_transport(_installation())
    conn = AcpConnection(installation=_installation(), transport=transport)
    try:
        with pytest.raises(AgentRuntimeError) as excinfo:
            await conn.steer({})
        assert excinfo.value.kind == "capability_missing"
    finally:
        await conn.close()


def test_validate_before_connect_model_gateway_boundary():
    service = AcpRuntimeService(process_manager=AcpProcessManager(), binder=EnvModelBinder())
    agent_managed = _installation()
    service.validate_before_connect(agent_managed)  # no gateway needed
    cyrene_managed = _installation(model_access={"mode": "cyrene_managed", "profileId": "primary"})
    with pytest.raises(AgentRuntimeError) as excinfo:
        service.validate_before_connect(cyrene_managed)
    assert excinfo.value.kind == "model_gateway_unavailable"


@pytest.mark.asyncio
async def test_run_external_agent_turn_end_to_end(fake_acp_bin, monkeypatch):
    install = _installation()
    monkeypatch.setattr(
        "cyrene.extensions.agent_runtime.get_agent_installation",
        lambda installation_id: install if installation_id == install["installation_id"] else None,
    )
    service = AcpRuntimeService(process_manager=AcpProcessManager())
    published: list[dict[str, Any]] = []

    async def publish(event):
        published.append(event)

    result = await run_external_agent_turn(
        chat={
            "id": "wbchat_1",
            "agent": {"installationId": "agent_opencode_default", "driver": "acp_stdio"},
            "modelAccess": {"mode": "agent_managed"},
            "agentConfigValues": {"model": "model-b"},
        },
        message="hello",
        publish=publish,
        run_id="run_1",
        runtime_service=service,
    )
    assert result["sessionId"] == "ses_fake_1"
    assert result["status"] == "completed"
    assert result["configOptions"][0]["currentValue"] == "model-b"
    types = [event["type"] for event in published]
    assert types[0] == "run.started"
    assert types[-1] == "run.completed"
    assert "permission.requested" in types
    assert service.process_manager.active_count() == 0  # transport released after close


@pytest.mark.asyncio
async def test_external_agent_receives_scoped_cyrene_model_gateway(fake_acp_bin, monkeypatch):
    from cyrene.agent_runtime import model_gateway

    install = _installation(model_access={"mode": "cyrene_managed", "profileId": "primary"})
    monkeypatch.setattr(
        "cyrene.extensions.agent_runtime.get_agent_installation",
        lambda installation_id: install if installation_id == install["installation_id"] else None,
    )
    monkeypatch.setattr(
        "cyrene.model_runtime.client.resolve_session_model_candidate",
        lambda session_id: {
            "id": "cyrene-primary",
            "provider": "openai_compatible",
            "model": "cyrene-model",
            "base_url": "https://models.example/v1",
            "api_key": "long-lived-secret",
        } if session_id == "wbchat_gateway" else None,
    )
    captured: dict[str, Any] = {}

    def capture_transport(command, args=(), **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return AcpStdioTransport(command, args, **kwargs)

    service = AcpRuntimeService(
        process_manager=AcpProcessManager(transport_factory=capture_transport),
        binder=EnvModelBinder(model_gateway.issue_model_gateway_binding),
    )
    await run_external_agent_turn(
        chat={
            "id": "wbchat_gateway",
            "agent": {"installationId": install["installation_id"], "driver": "acp_stdio"},
            "modelAccess": {"mode": "cyrene_managed", "profileId": "primary"},
        },
        message="hello",
        publish=lambda event: _append_event([], event),
        run_id="run_gateway",
        runtime_service=service,
    )
    env = captured["env"]
    assert env["OPENAI_BASE_URL"].endswith("/api/agent-model-gateway/v1")
    assert env["OPENAI_API_KEY"] != "long-lived-secret"
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["model"] == "cyrene-gateway/cyrene-model"
    assert config["provider"]["cyrene-gateway"]["options"]["baseURL"] == "{env:OPENAI_BASE_URL}"
    scope = model_gateway.authorize_model_gateway(f"Bearer {env['OPENAI_API_KEY']}")
    assert scope is not None
    assert scope["chatId"] == "wbchat_gateway"
    assert scope["installationId"] == install["installation_id"]
    model_gateway.revoke_model_gateway_scope(chat_id="wbchat_gateway")
    assert model_gateway.authorize_model_gateway(f"Bearer {env['OPENAI_API_KEY']}") is None


@pytest.mark.asyncio
async def test_run_external_agent_turn_official_v1_prompt_response_is_terminal(fake_acp_bin, monkeypatch):
    install = _installation()
    monkeypatch.setattr(
        "cyrene.extensions.agent_runtime.get_agent_installation",
        lambda installation_id: install if installation_id == install["installation_id"] else None,
    )
    def official_transport(command, args=(), **kwargs):
        env = dict(kwargs.pop("env", {}) or {})
        env["FAKE_ACP_MODE"] = "official"
        return AcpStdioTransport(command, args, env=env, **kwargs)

    service = AcpRuntimeService(
        process_manager=AcpProcessManager(transport_factory=official_transport)
    )
    published: list[dict[str, Any]] = []
    result = await run_external_agent_turn(
        chat={
            "id": "wbchat_official",
            "agent": {"installationId": install["installation_id"], "driver": "acp_stdio"},
            "modelAccess": {"mode": "agent_managed"},
        },
        message="hello",
        publish=lambda event: _append_event(published, event),
        run_id="run_official",
        runtime_service=service,
    )
    assert result["status"] == "completed"
    assert [event["type"] for event in published] == ["message.delta", "usage.updated", "run.completed"]
    assert published[-1]["payload"]["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_loaded_session_history_is_not_republished_and_new_turn_streams(fake_acp_bin, monkeypatch):
    install = _installation()
    monkeypatch.setattr(
        "cyrene.extensions.agent_runtime.get_agent_installation",
        lambda installation_id: install if installation_id == install["installation_id"] else None,
    )

    def replay_transport(command, args=(), **kwargs):
        env = dict(kwargs.pop("env", {}) or {})
        env["FAKE_ACP_MODE"] = "history_replay"
        return AcpStdioTransport(command, args, env=env, **kwargs)

    service = AcpRuntimeService(
        process_manager=AcpProcessManager(transport_factory=replay_transport)
    )
    published: list[dict[str, Any]] = []
    result = await run_external_agent_turn(
        chat={
            "id": "wbchat_replay",
            "agent": {"installationId": install["installation_id"], "driver": "acp_stdio"},
            "modelAccess": {"mode": "agent_managed"},
        },
        message="follow up",
        external_session_id="ses_existing",
        publish=lambda event: _append_event(published, event),
        run_id="run_replay",
        runtime_service=service,
    )

    deltas = [
        event["payload"]["delta"]
        for event in published
        if event["type"] == "message.delta"
    ]
    assert result["status"] == "completed"
    assert deltas == ["new ", "reply"]
    assert "OLD REPLY" not in "".join(deltas)


@pytest.mark.asyncio
async def test_loaded_session_service_failure_falls_back_to_new_session(fake_acp_bin, monkeypatch):
    install = _installation()
    monkeypatch.setattr(
        "cyrene.extensions.agent_runtime.get_agent_installation",
        lambda installation_id: install if installation_id == install["installation_id"] else None,
    )

    def failing_load_transport(command, args=(), **kwargs):
        env = dict(kwargs.pop("env", {}) or {})
        env["FAKE_ACP_MODE"] = "load_failure"
        return AcpStdioTransport(command, args, env=env, **kwargs)

    service = AcpRuntimeService(
        process_manager=AcpProcessManager(transport_factory=failing_load_transport)
    )
    published: list[dict[str, Any]] = []
    result = await run_external_agent_turn(
        chat={
            "id": "wbchat_load_failure",
            "agent": {"installationId": install["installation_id"], "driver": "acp_stdio"},
            "modelAccess": {"mode": "agent_managed"},
        },
        message="continue",
        external_session_id="ses_broken",
        publish=lambda event: _append_event(published, event),
        run_id="run_load_failure",
        runtime_service=service,
    )
    assert result["status"] == "completed"
    assert result["sessionId"] == "ses_fake_1"
    assert [event["payload"]["delta"] for event in published if event["type"] == "message.delta"] == ["official done"]


@pytest.mark.asyncio
async def test_loaded_session_process_crash_reconnects_before_new_session(fake_acp_bin, monkeypatch):
    install = _installation()
    monkeypatch.setattr(
        "cyrene.extensions.agent_runtime.get_agent_installation",
        lambda installation_id: install if installation_id == install["installation_id"] else None,
    )
    spawned = 0

    def crashing_load_transport(command, args=(), **kwargs):
        nonlocal spawned
        spawned += 1
        env = dict(kwargs.pop("env", {}) or {})
        env["FAKE_ACP_MODE"] = "load_crash"
        return AcpStdioTransport(command, args, env=env, **kwargs)

    service = AcpRuntimeService(
        process_manager=AcpProcessManager(transport_factory=crashing_load_transport)
    )
    published: list[dict[str, Any]] = []
    result = await run_external_agent_turn(
        chat={
            "id": "wbchat_load_crash",
            "agent": {"installationId": install["installation_id"], "driver": "acp_stdio"},
            "modelAccess": {"mode": "agent_managed"},
        },
        message="continue",
        external_session_id="ses_crashes_agent",
        publish=lambda event: _append_event(published, event),
        run_id="run_load_crash",
        runtime_service=service,
    )

    assert spawned == 2
    assert result["status"] == "completed"
    assert result["sessionId"] == "ses_fake_1"
    assert [event["payload"]["delta"] for event in published if event["type"] == "message.delta"] == ["official done"]


async def _append_event(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    events.append(event)


@pytest.mark.asyncio
async def test_run_external_agent_turn_rejects_builtin_and_missing_install():
    service = AcpRuntimeService(process_manager=AcpProcessManager())
    with pytest.raises(AgentRuntimeError) as excinfo:
        await run_external_agent_turn(
            chat={"id": "c", "agent": {"installationId": BUILTIN_INSTALLATION_ID}},
            message="hi",
            publish=lambda _event: asyncio.sleep(0),
            runtime_service=service,
        )
    assert excinfo.value.kind == "capability_missing"
    with pytest.raises(AgentRuntimeError) as excinfo:
        await run_external_agent_turn(
            chat={"id": "c", "agent": {"installationId": "agent_ghost_default"}},
            message="hi",
            publish=lambda _event: asyncio.sleep(0),
            runtime_service=service,
        )
    assert excinfo.value.kind == "dependency_missing"
