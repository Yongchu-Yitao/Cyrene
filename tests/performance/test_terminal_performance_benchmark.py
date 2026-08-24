from __future__ import annotations

import pytest

from cyrene.observability.terminal_performance_benchmark import (
    WORKLOADS,
    render_markdown,
    run_benchmark,
)


@pytest.mark.asyncio
async def test_real_terminal_benchmark_covers_workloads_and_subscription_modes():
    report = await run_benchmark(total_bytes=64 * 1024, output_limit=64 * 1024)

    assert {
        (case["workload"], case["subscribed"])
        for case in report["cases"]
    } == {
        (workload, subscribed)
        for workload in WORKLOADS
        for subscribed in (False, True)
    }
    assert report["qualityPreserved"] is True
    assert all(case["actualPtyBytes"] >= 64 * 1024 for case in report["cases"])
    assert all(case["screenBytesParsed"] == case["actualPtyBytes"] for case in report["cases"])
    assert all(case["scrollbackWriteAmplification"] < 1.1 for case in report["cases"])
    assert all(case["eventLoopDelayP95Ms"] >= 0 for case in report["cases"])
    assert all(case["rssPeakDeltaBytes"] >= 0 for case in report["cases"])
    assert all(case["processDiskWriteBytes"] >= 0 for case in report["cases"])
    assert all(
        case["webSocketLatencyP95Ms"] >= 0
        and case["webSocketBytes"] == case["actualPtyBytes"]
        and case["webSocketResyncs"] == 0
        for case in report["cases"]
        if case["subscribed"]
    )
    markdown = render_markdown(report)
    assert "POSIX" in markdown or "posix_pty" in markdown or "conpty" in markdown
    assert "Scroll write amp" in markdown
