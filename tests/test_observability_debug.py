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
