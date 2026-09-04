from __future__ import annotations

import asyncio
import json
import stat

from cyrene.model.protocol_trace import create_model_protocol_trace


def test_model_protocol_trace_is_disabled_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CYRENE_MODEL_PROTOCOL_TRACE", raising=False)

    assert create_model_protocol_trace(
        tmp_path / "agent-state",
        session_id="wbchat_disabled",
    ) is None


def test_model_protocol_trace_requires_opt_in_and_writes_owner_only_jsonl(
    monkeypatch,
    tmp_path,
) -> None:
    trace_directory = tmp_path / "raw-model-trace"
    monkeypatch.setenv("CYRENE_MODEL_PROTOCOL_TRACE", str(trace_directory))
    writer = create_model_protocol_trace(
        tmp_path / "agent-state",
        session_id="wbchat_trace",
    )

    assert writer is not None
    asyncio.run(writer({
        "type": "response_line",
        "sequence": 1,
        "line": 'data: {"private":"developer explicitly opted in"}',
    }))

    records = [
        json.loads(line)
        for line in writer.path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["session_id"] == "wbchat_trace"
    assert records[0]["type"] == "response_line"
    assert "developer explicitly opted in" in records[0]["line"]
    assert stat.S_IMODE(writer.path.stat().st_mode) == 0o600


def test_model_protocol_trace_stops_at_configured_size_limit(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CYRENE_MODEL_PROTOCOL_TRACE", "1")
    monkeypatch.setenv("CYRENE_MODEL_PROTOCOL_TRACE_MAX_BYTES", "1024")
    writer = create_model_protocol_trace(
        tmp_path / "agent-state",
        session_id="wbchat_bounded",
    )

    assert writer is not None
    asyncio.run(writer({"type": "response_line", "line": "x" * 800}))
    first_size = writer.path.stat().st_size
    asyncio.run(writer({"type": "response_line", "line": "y" * 800}))

    assert writer.path.stat().st_size == first_size
    assert first_size <= writer.max_bytes
