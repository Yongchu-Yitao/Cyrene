from __future__ import annotations


async def test_telemetry_flush_preserves_events_appended_during_database_write(
    monkeypatch,
) -> None:
    from cyrene.observability import debug
    from cyrene.runtime import database

    first = {"type": "tool_call", "timestamp": "t1", "tool": "Read"}
    appended_during_write = {
        "type": "tool_call",
        "timestamp": "t2",
        "tool": "Write",
    }
    debug._telemetry_pending.clear()
    debug._telemetry_pending.append(first)

    async def record_batch(*_args, **_kwargs):
        debug._telemetry_pending.append(appended_during_write)

    monkeypatch.setattr(database, "record_usage_stats_batch", record_batch)
    try:
        await debug._flush_telemetry_batch()

        assert list(debug._telemetry_pending) == [appended_during_write]
    finally:
        debug._telemetry_pending.clear()


async def test_plugin_llm_event_updates_both_usage_projections(monkeypatch) -> None:
    from cyrene.observability import debug
    from cyrene.runtime import database

    captured = {}

    async def record_batch(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(database, "record_usage_stats_batch", record_batch)
    debug._telemetry_pending.clear()
    debug._telemetry_pending.extend([
        {
            "type": "llm_call",
            "timestamp": "2026-08-28T12:00:00+00:00",
            "status": "completed",
            "model": "example-model",
            "session_id": "chat-1",
            "round_id": "run-1",
            "caller": "main_agent",
            "duration_ms": 1250,
            "usage": {"input_tokens": 30, "output_tokens": 12},
            "usage_observation": {
                "prompt_tokens": 30,
                "completion_tokens": 12,
                "total_tokens": 42,
                "cached_prompt_tokens": 7,
                "cache_miss_tokens": 23,
            },
        },
        {
            "type": "llm_call",
            "timestamp": "2026-08-28T12:00:01+00:00",
            "status": "failed",
            "model": "failed-model",
            "usage": {},
        },
    ])
    try:
        await debug._flush_telemetry_batch()
    finally:
        debug._telemetry_pending.clear()

    assert captured["runtime_events"] == [
        (
            "2026-08-28T12:00:00+00:00",
            {
                "prompt_tokens": 30,
                "completion_tokens": 12,
                "total_tokens": 42,
                "prompt_cache_hit_tokens": 7,
                "prompt_cache_miss_tokens": 23,
            },
        )
    ]
    assert captured["model_events"][0][1] == "example-model"
    assert captured["token_events"] == [{
        "created_at": "2026-08-28T12:00:00+00:00",
        "model": "example-model",
        "round_id": "run-1",
        "session_id": "chat-1",
        "caller": "main_agent",
        "prompt_tokens": 30,
        "completion_tokens": 12,
        "total_tokens": 42,
        "cache_hit_tokens": 7,
        "cache_miss_tokens": 23,
        "duration_ms": 1250,
    }]
