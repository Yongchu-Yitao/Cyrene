from __future__ import annotations

import pytest

from cyrene.observability.terminal_performance_benchmark import (
    WORKLOADS,
    compare_with_baseline,
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
    assert report["fairness"]["qualityPreserved"] is True
    assert report["fairness"]["noisyRunningAtEcho"] is True
    assert report["fairness"]["webSocketResyncs"] == 0
    assert report["fairness"]["webSocketSequenceErrors"] == 0
    assert report["fairness"]["interactiveEchoLatencyMs"] > 0
    assert all(case["actualPtyBytes"] >= 64 * 1024 for case in report["cases"])
    assert all(case["screenBytesParsed"] == case["actualPtyBytes"] for case in report["cases"])
    assert all(
        0 < case["screenBatches"] <= case["screenUpdates"]
        for case in report["cases"]
    )
    assert all(case["scrollbackWriteAmplification"] < 1.1 for case in report["cases"])
    assert all(case["eventLoopDelayP95Ms"] >= 0 for case in report["cases"])
    assert all(case["rssPeakDeltaBytes"] >= 0 for case in report["cases"])
    assert all(case["processDiskWriteBytes"] >= 0 for case in report["cases"])
    assert all(case["replaySequenceErrors"] == 0 for case in report["cases"])
    assert all(case["replayDataMatches"] is True for case in report["cases"])
    assert all(case["sourceFramesValid"] is True for case in report["cases"])
    assert all(
        case["sourceBytesObserved"] == case["actualPtyBytes"]
        and case["sourceFramesObserved"] == case["sourceFramesExpected"]
        and case["sourceFrameSequenceErrors"] == 0
        for case in report["cases"]
    )
    assert all(case["workerQueueWaitMaxMs"] >= 0 for case in report["cases"])
    assert all(case["queryQueueWaitMaxMs"] >= 0 for case in report["cases"])
    assert all(
        case["webSocketLatencyP95Ms"] >= 0
        and case["webSocketBytes"] == case["actualPtyBytes"]
        and case["webSocketResyncs"] == 0
        and case["webSocketSequenceErrors"] == 0
        for case in report["cases"]
        if case["subscribed"]
    )
    markdown = render_markdown(report)
    assert "POSIX" in markdown or "posix_pty" in markdown or "conpty" in markdown
    assert "Scroll write amp" in markdown


def test_terminal_latency_regressions_require_matching_historical_baseline():
    case = {
        "workload": "ansi", "subscribed": True, "ptyBackend": "conpty",
        "eventLoopDelayP95Ms": 15.0, "webSocketLatencyP95Ms": 5.0,
    }
    comparison = compare_with_baseline(
        {"cases": [case]},
        {"cases": [{
            **case, "eventLoopDelayP95Ms": 10.0, "webSocketLatencyP95Ms": 5.0,
        }]},
        regression_threshold_percent=20,
    )

    assert comparison["matchedCases"] == 1
    assert comparison["passed"] is False
    assert comparison["regressions"][0]["metric"] == "eventLoopDelayP95Ms"
    assert compare_with_baseline({"cases": [case]}, {"cases": []})["passed"] is True

    fairness_comparison = compare_with_baseline(
        {
            "cases": [],
            "fairness": {
                "ptyBackend": "conpty", "interactiveEchoLatencyMs": 13.0,
            },
        },
        {
            "cases": [],
            "fairness": {
                "ptyBackend": "conpty", "interactiveEchoLatencyMs": 10.0,
            },
        },
        regression_threshold_percent=20,
    )
    assert fairness_comparison["passed"] is False
    assert fairness_comparison["regressions"][0]["metric"] == (
        "interactiveEchoLatencyMs"
    )
