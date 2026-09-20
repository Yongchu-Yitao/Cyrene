"""Synthetic scaling probe; isolated prototype, no production monkeypatching.

Run from repo root: .venv/bin/python project-notes/experiments/performance-2026-09-20/timeline_probe.py
"""
import copy
import json
import platform
import statistics
import time
from pathlib import Path

from cyrene.workbench.chat.run_timeline import RunTimeline


def fixture(count):
    timeline = RunTimeline("probe")
    at = "2026-09-20T00:00:00+00:00"
    for i in range(count):
        record = timeline._new("message", at, str(i))
        record.update(content="x" * 2048, status="completed")
    timeline.reply_id = record["id"]
    record["status"] = "running"
    return timeline


def targeted_done(timeline, event):
    # ONLY this fixture's existing, explicitly identified reply completion.
    record = timeline.records[timeline.sources["message:" + event["messageId"]]]
    record.update(content=event["text"], status="completed", endedAt=event["timestamp"])
    return timeline._changed_patch([record])


def median_ms(call, rounds=50):
    samples = []
    for _ in range(rounds):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


results = []
for count in (10, 100, 1000, 5000):
    original = fixture(count)
    candidate = copy.deepcopy(original)
    event = {"type": "message.completed", "messageId": str(count - 1),
             "text": "final", "timestamp": "2026-09-20T00:00:01+00:00"}
    patches_equal = original.apply(event) == targeted_done(candidate, event)
    states_equal = original.__dict__ == candidate.__dict__
    assert patches_equal and states_equal
    # Alternate content to force a changed record on every measured completion.
    def call_base():
        event["text"] = "a" if event["text"] != "a" else "b"
        return original.apply(event)
    def call_candidate():
        event["text"] = "a" if event["text"] != "a" else "b"
        return targeted_done(candidate, event)
    results.append({"records": count, "baseline_done_ms": median_ms(call_base),
                    "prototype_done_ms": median_ms(call_candidate),
                    "fixture_patch_and_state_equal": patches_equal and states_equal})
report = {"python": platform.python_version(), "rounds": 50,
          "scope": "existing reply completion only; prototype is not production-ready", "results": results}
path = Path(__file__).with_name("timeline-probe.json")
path.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
